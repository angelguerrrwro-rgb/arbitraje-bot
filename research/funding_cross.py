"""Arbitraje de funding ENTRE plataformas (Binance, Bybit, OKX, Hyperliquid).

Estrategia: corto en el perpetuo de la plataforma que paga más funding y largo en la que paga
menos, del mismo tamaño. El precio se compensa y cobras la diferencia. No depende de la velocidad.

Datos públicos de los últimos `dias` (OKX solo guarda ~3 meses). Todo se agrupa en tramos de 8 h
(Hyperliquid paga cada hora, el resto cada 8 h o 4 h).

Uso: python -m research.funding_cross --dias 90
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import time
from collections import defaultdict
from pathlib import Path

import aiohttp
from rich.console import Console
from rich.table import Table

OUT = Path(__file__).parent / "data"
BUCKET = 8 * 3600 * 1000
COINS = ["BTC", "ETH", "SOL", "XRP", "DOGE", "SUI", "ADA", "AVAX", "LINK", "LTC", "BNB", "NEAR", "APT", "ARB",
         "OP", "WLD", "TIA", "INJ", "AAVE", "UNI", "ENA", "HYPE", "TAO", "FIL", "DOT", "ONDO", "PEPE", "WIF"]
TAKER = {"binance": 0.0005, "bybit": 0.00055, "okx": 0.0005, "hyperliquid": 0.00045}
ALIAS = {"PEPE": {"binance": "1000PEPE", "bybit": "1000PEPE", "hyperliquid": "kPEPE", "okx": "PEPE"}}


def sym(coin: str, venue: str) -> str:
    return ALIAS.get(coin, {}).get(venue, coin)


async def binance(s, coin, start):
    out, since = [], start
    while True:
        async with s.get("https://fapi.binance.com/fapi/v1/fundingRate",
                         params={"symbol": sym(coin, "binance") + "USDT", "startTime": since, "limit": 1000}) as r:
            if r.status != 200:
                return out
            b = await r.json()
        out += [(x["fundingTime"], float(x["fundingRate"])) for x in b]
        if len(b) < 1000:
            return out
        since = b[-1]["fundingTime"] + 1


async def bybit(s, coin, start):
    out, end = [], int(time.time() * 1000)
    while end > start:
        async with s.get("https://api.bybit.com/v5/market/funding/history",
                         params={"category": "linear", "symbol": sym(coin, "bybit") + "USDT", "startTime": start,
                                 "endTime": end, "limit": 200}) as r:
            d = await r.json(content_type=None)
        rows = (d.get("result") or {}).get("list") or []
        if not rows:
            break
        out += [(int(x["fundingRateTimestamp"]), float(x["fundingRate"])) for x in rows]
        end = min(int(x["fundingRateTimestamp"]) for x in rows) - 1
        if len(rows) < 200:
            break
    return out


async def okx(s, coin, start):
    out, after = [], None
    while True:
        params = {"instId": f"{sym(coin, 'okx')}-USDT-SWAP", "limit": 100}
        if after:
            params["after"] = after
        async with s.get("https://www.okx.com/api/v5/public/funding-rate-history", params=params) as r:
            d = await r.json(content_type=None)
        rows = d.get("data") or []
        if not rows:
            break
        out += [(int(x["fundingTime"]), float(x["realizedRate"] or x["fundingRate"])) for x in rows]
        after = min(int(x["fundingTime"]) for x in rows)
        if after < start or len(rows) < 100:
            break
        await asyncio.sleep(0.15)
    return [x for x in out if x[0] >= start]


async def hyperliquid(s, coin, start):
    out, since = [], start
    while True:
        async with s.post("https://api.hyperliquid.xyz/info",
                          json={"type": "fundingHistory", "coin": sym(coin, "hyperliquid"), "startTime": since}) as r:
            rows = await r.json(content_type=None)
        if not isinstance(rows, list) or not rows:
            break
        out += [(int(x["time"]), float(x["fundingRate"])) for x in rows]
        if len(rows) < 500:
            break
        since = int(rows[-1]["time"]) + 1
        await asyncio.sleep(0.2)
    return out


FETCH = {"binance": binance, "bybit": bybit, "okx": okx, "hyperliquid": hyperliquid}


async def fetch(days: int, coins: list[str] = COINS, out_name: str = "funding_cross.json") -> dict:
    start = int((time.time() - days * 86400) * 1000)
    data: dict = defaultdict(dict)
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
        for coin in coins:
            for venue, fn in FETCH.items():
                try:
                    rows = await fn(s, coin, start)
                except Exception as e:
                    print(f"{coin} {venue}: error {e!r}", flush=True)
                    rows = []
                if rows:
                    data[coin][venue] = rows
            print(f"{coin}: " + ", ".join(f"{v} {len(r)}" for v, r in data[coin].items()), flush=True)
    payload = {"days": days, "fetched": time.strftime("%Y-%m-%d %H:%M"), "data": data}
    OUT.mkdir(exist_ok=True)
    (OUT / out_name).write_text(json.dumps(payload), encoding="utf-8")
    return payload


def buckets(rows) -> dict[int, float]:
    b: dict[int, float] = defaultdict(float)
    for t, r in rows:
        b[t // BUCKET] += r
    return b


def strategy(diff: list[float], enter: float, leave: float, cost: float, span: int = 3) -> tuple[float, int]:
    """diff[t] = funding(A) - funding(B) en el tramo t. Posición +1: corto A / largo B (cobra diff).
    Decide con la media de los `span` tramos anteriores; paga `cost` (fracción del nocional) por
    abrir o cerrar las dos patas."""
    pos, pnl, trades = 0, 0.0, 0
    for t in range(span, len(diff)):
        avg = sum(diff[t - span : t]) / span
        want = pos
        if pos == 0 and abs(avg) > enter:
            want = 1 if avg > 0 else -1
        elif pos != 0 and pos * avg < leave:
            want = 0
        if want != pos:
            pnl -= cost * (abs(want - pos))  # cerrar y/o abrir
            trades += 1
            pos = want
        pnl += pos * diff[t]
    pnl -= cost * abs(pos)
    return pnl, trades


def analyze(payload: dict) -> None:
    console = Console(width=160)
    days = payload["days"]
    year = 365 / days
    rows = []
    for coin, venues in payload["data"].items():
        b = {v: buckets(r) for v, r in venues.items()}
        for a, c in itertools.combinations(sorted(b), 2):
            common = sorted(set(b[a]) & set(b[c]))
            if len(common) < 30:
                continue
            diff = [b[a][t] - b[c][t] for t in common]
            mean = sum(diff) / len(diff)
            cost = TAKER[a] + TAKER[c]  # abrir o cerrar las dos patas como taker
            best = max(((strategy(diff, e, 0.0, cost)[0], e) for e in (0.00005, 0.0001, 0.0002, 0.0003)),
                       key=lambda x: x[0])
            span_days = len(common) / 3
            rows.append({
                "coin": coin, "a": a, "c": c, "n": len(common),
                "mean_apr": mean * 3 * 365 * 100,
                "same_sign": sum(1 for d in diff if d * mean > 0) / len(diff) * 100,
                "static": (abs(sum(diff)) - 2 * cost) / span_days * 365 * 100,  # con retrospectiva: cota superior
                "strat": best[0] / span_days * 365 * 100, "enter": best[1],
                # capital = nocional (las dos patas a 2x): el % sobre nocional es el % sobre capital
            })
    rows.sort(key=lambda r: -r["strat"])
    t = Table(title=f"Funding entre plataformas · {days} días · rentabilidad anual sobre capital (dos patas a 2x)")
    for col in ("Moneda", "A − B", "Tramos", "Diferencia media/año", "% tramos mismo signo",
                "Siempre en el lado bueno*", "Estrategia con umbral", "Umbral"):
        t.add_column(col, justify="left" if col in ("Moneda", "A − B") else "right")
    for r in rows[:25]:
        t.add_row(r["coin"], f"{r['a']} − {r['c']}", str(r["n"]), f"{r['mean_apr']:+.1f} %", f"{r['same_sign']:.0f}",
                  f"{r['static']:+.1f} %", f"{r['strat']:+.1f} %", f"{r['enter'] * 100:.3f} %")
    console.print(t)
    console.print("* Sabiendo de antemano qué lado paga más: cota superior, no alcanzable.")
    pos = [r for r in rows if r["strat"] > 0]
    console.print(f"Combinaciones con estrategia positiva: {len(pos)}/{len(rows)} · media de las 10 mejores: "
                  f"{sum(r['strat'] for r in rows[:10]) / max(1, min(10, len(rows))):+.1f} %/año")
    (OUT / "funding_cross_summary.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dias", type=int, default=90)
    ap.add_argument("--sin-descargar", action="store_true")
    ap.add_argument("--monedas", nargs="+", help="p. ej. XAU XAG PAXG XAUT XPT (oro, plata, platino)")
    a = ap.parse_args()
    name = "funding_cross.json" if not a.monedas else f"funding_cross_{'_'.join(a.monedas)}.json"
    if a.sin_descargar:
        payload = json.loads((OUT / name).read_text(encoding="utf-8"))
    else:
        payload = asyncio.run(fetch(a.dias, a.monedas or COINS, name))
    analyze(payload)
