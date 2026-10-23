"""Micro-operaciones en pares de stablecoins (una sola plataforma, sin triangular).

Idea: un par como U/USDT oscila unos pocos ticks alrededor de 1. Se deja una orden de compra
N ticks por debajo del centro y, cuando se llena, una de venta N ticks por encima. Si el par no
paga comisión (promoción "U" de Binance), cada ida y vuelta deja 2N ticks limpios.

Simulación conservadora con velas de 1 minuto de los últimos `dias`:
- El centro es la mediana de las 24 h ANTERIORES (no se mira el futuro).
- Una orden solo cuenta como llenada si el precio pasó POR DEBAJO de la compra (o por encima de la
  venta): estar en la cola al mismo precio no basta.
- Una sola ida y vuelta abierta a la vez, con `--tamano` dólares.

Uso: python -m research.stable_grid --dias 7 --tamano 250
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import time

import aiohttp
from rich.console import Console
from rich.table import Table

PAIRS = {"UUSDT": 0.0, "UUSDC": 0.0, "USDCUSDT": 0.00075, "FDUSDUSDT": 0.00075}  # comisión supuesta (maker)


async def klines(s, symbol, days):
    out, start = [], int((time.time() - days * 86400) * 1000)
    while True:
        async with s.get("https://api.binance.com/api/v3/klines",
                         params={"symbol": symbol, "interval": "1m", "startTime": start, "limit": 1000}) as r:
            if r.status != 200:
                return out
            b = await r.json()
        out += [(k[0], float(k[2]), float(k[3]), float(k[5]) * float(k[4])) for k in b]  # (t, high, low, volumen quote)
        if len(b) < 1000:
            return out
        start = b[-1][0] + 60_000


async def ticks(s, symbols):
    async with s.get("https://api.binance.com/api/v3/exchangeInfo", params={"symbols": str(symbols).replace("'", '"').replace(" ", "")}) as r:
        d = await r.json()
    return {x["symbol"]: float(next(f["tickSize"] for f in x["filters"] if f["filterType"] == "PRICE_FILTER"))
            for x in d.get("symbols", [])}


def simulate(k, tick, n_ticks, fee, size):
    """Devuelve (idas y vueltas completadas, beneficio $)."""
    day = 1440
    trips, pnl, holding, buy_px = 0, 0.0, False, 0.0
    for i in range(day, len(k)):
        if i % 60 == 0 or i == day:
            center = statistics.median((h + l) / 2 for _, h, l, _ in k[i - day : i])
            center = round(center / tick) * tick
            buy_level, sell_level = center - n_ticks * tick, center + n_ticks * tick
        _, high, low, _ = k[i]
        if not holding and low < buy_level:
            holding, buy_px = True, buy_level
        elif holding and high > sell_level:
            holding = False
            trips += 1
            pnl += size * ((sell_level / buy_px - 1) - 2 * fee)
    return trips, pnl


async def main(days: int, size: float) -> None:
    console = Console(width=150)
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
        tk = await ticks(s, list(PAIRS))
        data = {sym: await klines(s, sym, days + 1) for sym in PAIRS}
    t = Table(title=f"Micro-operaciones en stablecoins · {days} días de velas de 1 min · {size:.0f} $ por operación")
    for col in ("Par", "Tick", "Rango típico/día", "Volumen/día $", "Comisión supuesta", "Ticks a cada lado",
                "Idas y vueltas/día", "Beneficio/día", "Beneficio/mes"):
        t.add_column(col, justify="left" if col == "Par" else "right")
    for sym, fee in PAIRS.items():
        k = data[sym]
        if len(k) < 2 * 1440 or sym not in tk:
            t.add_row(sym, "—", "sin datos", "", "", "", "", "", "")
            continue
        tick = tk[sym]
        ndays = (len(k) - 1440) / 1440
        ranges = [max(h for _, h, _, _ in k[d : d + 1440]) - min(l for _, _, l, _ in k[d : d + 1440])
                  for d in range(0, len(k) - 1440, 1440)]
        vol = sum(v for *_, v in k[1440:]) / ndays
        for n in (1, 2, 3):
            trips, pnl = simulate(k, tick, n, fee, size)
            t.add_row(sym, f"{tick:g}", f"{statistics.median(ranges) / tick:.0f} ticks", f"{vol:,.0f}",
                      f"{fee * 100:.3f} %", str(n), f"{trips / ndays:.1f}", f"{pnl / ndays:+.3f} $", f"{pnl / ndays * 30:+.2f} $")
    console.print(t)
    console.print("Comisión supuesta: 0 % en los pares U (promoción de Binance, verifícala en tu cuenta); "
                  "0,075 % maker con BNB en el resto.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dias", type=int, default=7)
    ap.add_argument("--tamano", type=float, default=250)
    a = ap.parse_args()
    asyncio.run(main(a.dias, a.tamano))
