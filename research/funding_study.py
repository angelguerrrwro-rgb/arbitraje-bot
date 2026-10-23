"""Estudio de "cash and carry" con el funding rate de Binance Futures (datos públicos, sin claves).

La estrategia: comprar X en spot y abrir un corto del mismo tamaño en el perpetuo X/USDT.
El precio se compensa (posición neutral) y cobras el funding cuando es positivo (los largos pagan
a los cortos). No depende de la velocidad: el funding se paga cada 8 h (o 4 h / 1 h).

Uso: python -m research.funding_study --top 40 --dias 365
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections import defaultdict
from pathlib import Path

import aiohttp
from rich.console import Console
from rich.table import Table

from triarb.binance import LIVE_REST, BinanceRest, parse_symbols

FAPI = "https://fapi.binance.com"
OUT = Path(__file__).parent / "data"
BUCKET_MS = 8 * 3600 * 1000

# Comisiones (fracción). Spot VIP0 0,1 % (0,075 % con BNB); futuros USDⓈ-M VIP0 maker 0,02 %, taker 0,05 %.
SPOT_TAKER = 0.00075
FUT_TAKER = 0.0005
LEVERAGE = 2  # margen del corto = nocional / 2 → capital por unidad de nocional = 1 + 1/2
CAPITAL_PER_NOTIONAL = 1 + 1 / LEVERAGE
ROUND_TRIP = 2 * (SPOT_TAKER + FUT_TAKER)  # entrar y salir, las dos patas, como taker


async def fetch(days: int, top: int) -> dict:
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
        async def get(path, **params):
            async with s.get(FAPI + path, params=params) as r:
                r.raise_for_status()
                return await r.json()

        spot = parse_symbols(await BinanceRest(s, LIVE_REST).exchange_info())
        info = await get("/fapi/v1/exchangeInfo")
        perps = {
            x["symbol"]: x["baseAsset"] for x in info["symbols"]
            if x["contractType"] == "PERPETUAL" and x["quoteAsset"] == "USDT" and x["status"] == "TRADING"
            and x["baseAsset"] + "USDT" in spot
        }
        vols = {t["symbol"]: float(t["quoteVolume"]) for t in await get("/fapi/v1/ticker/24hr") if t["symbol"] in perps}
        chosen = sorted(vols, key=vols.get, reverse=True)[:top]
        current = {p["symbol"]: float(p["lastFundingRate"]) for p in await get("/fapi/v1/premiumIndex")}
        start = int((time.time() - days * 86400) * 1000)
        history: dict[str, list] = {}
        for sym in chosen:
            rows, since = [], start
            while True:
                batch = await get("/fapi/v1/fundingRate", symbol=sym, startTime=since, limit=1000)
                rows += [(r["fundingTime"], float(r["fundingRate"])) for r in batch]
                if len(batch) < 1000:
                    break
                since = batch[-1]["fundingTime"] + 1
                await asyncio.sleep(0.3)
            history[sym] = rows
            await asyncio.sleep(0.3)
        data = {"fetched": time.strftime("%Y-%m-%d %H:%M"), "days": days, "volume_24h": vols,
                "current": {k: current.get(k) for k in chosen}, "history": history}
        OUT.mkdir(exist_ok=True)
        (OUT / "funding.json").write_text(json.dumps(data), encoding="utf-8")
        return data


def buckets(rows: list) -> dict[int, float]:
    """Suma el funding de cada tramo de 8 h (algunos pares pagan cada 4 h o cada hora)."""
    out: dict[int, float] = defaultdict(float)
    for t, r in rows:
        out[t // BUCKET_MS] += r
    return out


def always_on(rates: list[float]) -> float:
    """Rentabilidad sobre capital de estar siempre dentro, con una entrada y una salida."""
    return (sum(rates) - ROUND_TRIP) / CAPITAL_PER_NOTIONAL


def threshold(rates: list[float], enter: float, leave: float, span: int = 3) -> tuple[float, int]:
    """Entrar cuando la media de los últimos `span` pagos supera `enter`, salir bajo `leave`.
    Solo usa información pasada: decide antes de cada pago con los pagos ya ocurridos."""
    pnl, inside, trades = 0.0, False, 0
    for i in range(span, len(rates)):
        avg = sum(rates[i - span : i]) / span
        if not inside and avg > enter:
            inside, trades = True, trades + 1
            pnl -= ROUND_TRIP / 2
        elif inside and avg < leave:
            inside = False
            pnl -= ROUND_TRIP / 2
        if inside:
            pnl += rates[i]
    if inside:
        pnl -= ROUND_TRIP / 2
    return pnl / CAPITAL_PER_NOTIONAL, trades


def rotation(series: dict[str, dict[int, float]], k: int, span: int = 3) -> tuple[float, int, list[float]]:
    """Cartera de los k pares con mejor funding reciente, repartida a partes iguales.
    Solo cambia un par si sale del top 2k (para no comerse las comisiones)."""
    times = sorted(set().union(*series.values()))
    held: set[str] = set()
    pnl, switches, curve = 0.0, 0, []
    for i, t in enumerate(times):
        if i >= span:
            score = {s: sum(series[s].get(times[j], 0.0) for j in range(i - span, i)) / span for s in series}
            ranked = sorted(score, key=score.get, reverse=True)
            keep = {s for s in held if s in ranked[: 2 * k] and score[s] > 0}
            for s in ranked:
                if len(keep) >= k or score[s] <= 0:
                    break
                keep.add(s)
            changes = len(held ^ keep)
            switches += changes
            pnl -= changes * (ROUND_TRIP / 2) / k
            held = keep
        pnl += sum(series[s].get(t, 0.0) for s in held) / k
        curve.append(pnl / CAPITAL_PER_NOTIONAL)
    pnl -= len(held) * (ROUND_TRIP / 2) / k
    return pnl / CAPITAL_PER_NOTIONAL, switches, curve


def max_drawdown(curve: list[float]) -> float:
    peak, dd = float("-inf"), 0.0
    for v in curve:
        peak = max(peak, v)
        dd = min(dd, v - peak)
    return dd


def analyze(data: dict) -> dict:
    console = Console()
    days = data["days"]
    series = {s: buckets(rows) for s, rows in data["history"].items() if rows}
    year = 365 / days
    results = []
    for sym, b in series.items():
        times = sorted(b)
        rates = [b[t] for t in times]
        last30 = rates[-90:]
        results.append({
            "symbol": sym,
            "vol_m": data["volume_24h"].get(sym, 0) / 1e6,
            "apr_gross": sum(rates) * year * 100,
            "apr_30d": sum(last30) * 365 / 30 * 100,
            "pct_pos": sum(r > 0 for r in rates) / len(rates) * 100,
            "always_on": always_on(rates) * year * 100,
            "thr": threshold(rates, 0.0001, 0.0)[0] * year * 100,
            "now_apr": (data["current"].get(sym) or 0) * 3 * 365 * 100,
        })
    results.sort(key=lambda r: -r["vol_m"])

    t = Table(title=f"Funding de los {len(results)} perpetuos más negociados ({days} días, rentabilidades anuales sobre capital)")
    for col in ("Par", "Vol. 24h M$", "Funding bruto/año", "Últ. 30 d/año", "% pagos >0", "Siempre dentro",
                "Con umbral", "Ahora/año"):
        t.add_column(col, justify="left" if col == "Par" else "right")
    for r in results:
        t.add_row(r["symbol"], f"{r['vol_m']:.0f}", f"{r['apr_gross']:.1f} %", f"{r['apr_30d']:.1f} %",
                  f"{r['pct_pos']:.0f}", f"{r['always_on']:.1f} %", f"{r['thr']:.1f} %", f"{r['now_apr']:.1f} %")
    console.print(t)

    rot = {}
    for k in (1, 3, 5):
        pnl, switches, curve = rotation(series, k)
        rot[k] = {"apr": pnl * year * 100, "switches": switches, "max_dd": max_drawdown(curve) * 100,
                  "last30": (curve[-1] - curve[-91]) * 365 / 30 * 100 if len(curve) > 91 else None}
        console.print(f"Rotación top-{k}: {rot[k]['apr']:.1f} %/año sobre capital · {switches} cambios · "
                      f"peor caída {rot[k]['max_dd']:.2f} % · últimos 30 d {rot[k]['last30']:.1f} %/año")
    summary = {"per_symbol": results, "rotation": rot, "round_trip": ROUND_TRIP,
               "capital_per_notional": CAPITAL_PER_NOTIONAL}
    (OUT / "funding_summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    return summary


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument("--dias", type=int, default=365)
    ap.add_argument("--sin-descargar", action="store_true", help="reutilizar research/data/funding.json")
    a = ap.parse_args()
    if a.sin_descargar:
        payload = json.loads((OUT / "funding.json").read_text(encoding="utf-8"))
    else:
        payload = asyncio.run(fetch(a.dias, a.top))
    analyze(payload)
