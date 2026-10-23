"""Señales tipo "XAUUSD BUY 4047 · TP 4053/4055/4057 · SL 4040" contra datos reales de oro.

La posición se divide en 3 partes iguales: cada una se cierra en su TP; lo que quede se cierra en el
SL o a las 24 h. Las distancias se toman de la señal en dólares y se escalan con el precio
(6/8/10 $ de objetivo y 7 $ de stop a 4047).

Entradas que se comparan:
- al azar (comprando): la referencia para saber si una señal aporta algo;
- al azar en dirección (compra o venta);
- "retroceso en tendencia": comprar si el precio está sobre su media de 24 h y cae 0,3 % desde el
  máximo de la última hora (el tipo de regla que hay detrás de muchas señales).

Costes por operación (1 onza): broker MT5 con spread de 0,30 $; Binance perpetuo maker+taker
(0,07 %); Binance taker+taker (0,10 %). Si en una vela se pudo tocar TP y SL, se asume el SL.

Uso: python -m research.signal_backtest --dias 365
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import time
from pathlib import Path

import aiohttp
from rich.console import Console
from rich.table import Table

DATA = Path(__file__).parent / "data"
REF = 4047.0
TPS = (6.0, 8.0, 10.0)
SL = 7.0
COSTS = {"MT5 (spread 0,30 $)": ("abs", 0.30), "Binance maker+taker (0,07 %)": ("pct", 0.0007),
         "Binance taker+taker (0,10 %)": ("pct", 0.0010)}


async def download(symbol: str, days: int):
    cache = DATA / f"fklines_{symbol}_5m_{days}d.json"
    if cache.exists() and time.time() - cache.stat().st_mtime < 6 * 3600:
        return [tuple(x) for x in json.loads(cache.read_text())]
    out, start = [], int((time.time() - days * 86400) * 1000)
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
        while True:
            async with s.get("https://fapi.binance.com/fapi/v1/klines",
                             params={"symbol": symbol, "interval": "5m", "startTime": start, "limit": 1500}) as r:
                b = await r.json()
            if not isinstance(b, list) or not b:
                break
            out += [(k[0], float(k[1]), float(k[2]), float(k[3]), float(k[4])) for k in b]
            if len(b) < 1500:
                break
            start = b[-1][0] + 300_000
            await asyncio.sleep(0.05)
    DATA.mkdir(exist_ok=True)
    cache.write_text(json.dumps(out))
    return out


def trade(k, i, side, scale):
    """Resultado en $ por onza (sin costes) de una señal abierta al cierre de la vela i."""
    entry = k[i][4]
    tps = [entry + side * tp * scale for tp in TPS]
    sl = entry - side * SL * scale
    parts, pnl = [True, True, True], 0.0
    for j in range(i + 1, min(i + 289, len(k))):
        _, o, h, l, c = k[j]
        stop_hit = (l <= sl) if side > 0 else (h >= sl)
        if stop_hit:  # pesimista: el stop antes que cualquier TP de la misma vela
            pnl += sum(parts) * (sl - entry) * side / 3
            return pnl, j
        for n, tp in enumerate(tps):
            if parts[n] and ((h >= tp) if side > 0 else (l <= tp)):
                parts[n] = False
                pnl += (tp - entry) * side / 3
        if not any(parts):
            return pnl, j
    j = min(i + 288, len(k) - 1)
    pnl += sum(parts) * (k[j][4] - entry) * side / 3
    return pnl, j


def run(k, signal, seed=7):
    rng = random.Random(seed)
    results, i = [], 288
    while i < len(k) - 1:
        side = signal(k, i, rng)
        if side:
            scale = k[i][4] / REF
            pnl, end = trade(k, i, side, scale)
            results.append((pnl, k[i][4]))
            i = end + 1
        else:
            i += 1
    return results


def random_long(k, i, rng):
    return 1 if rng.random() < 1 / 48 else 0  # ~1 señal cada 4 h


def random_side(k, i, rng):
    return rng.choice((1, -1)) if rng.random() < 1 / 48 else 0


def pullback(k, i, rng):
    closes = [x[4] for x in k[i - 288 : i]]
    mean24 = sum(closes) / len(closes)
    high1h = max(x[2] for x in k[i - 12 : i])
    c = k[i][4]
    return 1 if c > mean24 and c <= high1h * 0.997 else 0


def main(days: int) -> None:
    console = Console(width=170)
    k = asyncio.run(download("XAUUSDT", days))
    span = (k[-1][0] - k[0][0]) / 86_400_000
    console.print(f"[bold]Oro (XAUUSDT perpetuo de Binance)[/]: {len(k):,} velas de 5 min, {span:.0f} días · "
                  f"{k[0][4]:,.0f} → {k[-1][4]:,.0f} ({(k[-1][4] / k[0][4] - 1) * 100:+.1f} %) · "
                  f"señal: TP +{'/+'.join(f'{t:g}' for t in TPS)} $, SL −{SL:g} $ (a {REF:g})")
    t = Table(title="Señales con 3 objetivos y stop, 1 onza por señal (≈ 0,01 lote en MT5)")
    for col in ("Entrada", "Señales", "Acierto (alguna ganancia)", "Media sin costes"):
        t.add_column(col, justify="left" if col == "Entrada" else "right")
    for name in COSTS:
        t.add_column(f"Media con {name}", justify="right")
    t.add_column("Total con MT5", justify="right")
    for label, sig in (("Comprar al azar", random_long), ("Dirección al azar", random_side),
                       ("Retroceso en tendencia alcista", pullback)):
        res = run(k, sig)
        if not res:
            continue
        gross = [p for p, _ in res]
        row = [label, str(len(res)), f"{sum(p > 0 for p in gross) / len(gross) * 100:.0f} %",
               f"{sum(gross) / len(gross):+.2f} $"]
        for kind, cost in COSTS.values():
            net = [p - (cost if kind == "abs" else cost * px) for p, px in res]
            row.append(f"{sum(net) / len(net):+.2f} $")
        mt5 = sum(p - 0.30 for p, _ in res)
        row.append(f"{mt5:+.0f} $")
        t.add_row(*row)
    console.print(t)
    console.print("Media por señal y por onza. Con 500 $ en MT5, 1 onza por señal arriesga ~7 $ (1,4 % del capital) por operación.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dias", type=int, default=365)
    main(ap.parse_args().dias)
