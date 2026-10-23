"""¿Funciona "comprar a 5.000 y vender a 5.050" en BTC? Backtest con velas reales de Binance.

No es arbitraje: es especulación (si compras y el precio sigue bajando, pierdes o te quedas atrapado).
Estrategias, todas en spot sin apalancamiento, con comisión maker/taker del 0,075 % (BNB) por lado:

1. Rejilla: con el precio a P, compra un tramo cuando cae un `paso` desde el último máximo; si sigue
   cayendo, compra otro tramo un `paso` más abajo (hasta N tramos). Cada tramo se vende con +`objetivo`.
2. Comprar la caída: si el precio cae `caida` desde el máximo de 24 h, compra con todo; sale con
   +`objetivo`, con un stop de −`stop` o a las 72 h.
3. Referencias: comprar y mantener; entrar al azar con el mismo objetivo/stop.

Reglas para no engañarse:
- Si dentro de una misma vela se pudo tocar el objetivo y el stop, se asume el stop.
- Un tramo comprado en una vela no se puede vender en esa misma vela.
- Las posiciones abiertas al final se valoran al último precio (pérdida latente incluida).

Uso: python -m research.directional_backtest --simbolo BTCUSDT --dias 730
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
FEE = 0.00075
STEP_MS = 5 * 60_000


async def download(symbol: str, days: int) -> list[tuple[int, float, float, float, float]]:
    cache = DATA / f"klines_{symbol}_5m_{days}d.json"
    if cache.exists() and time.time() - cache.stat().st_mtime < 6 * 3600:
        return [tuple(x) for x in json.loads(cache.read_text())]
    out, start = [], int((time.time() - days * 86400) * 1000)
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
        while True:
            async with s.get("https://api.binance.com/api/v3/klines",
                             params={"symbol": symbol, "interval": "5m", "startTime": start, "limit": 1000}) as r:
                b = await r.json()
            out += [(k[0], float(k[1]), float(k[2]), float(k[3]), float(k[4])) for k in b]  # t, o, h, l, c
            if len(b) < 1000:
                break
            start = b[-1][0] + STEP_MS
            await asyncio.sleep(0.05)
    DATA.mkdir(exist_ok=True)
    cache.write_text(json.dumps(out))
    return out


def max_drawdown(curve: list[float]) -> float:
    peak, dd = curve[0], 0.0
    for v in curve:
        peak = max(peak, v)
        dd = min(dd, v / peak - 1)
    return dd


def grid(k, capital, step, target, slots):
    """Devuelve métricas de la rejilla."""
    lot = capital / slots
    cash, lots, trips, curve = capital, [], 0, []  # lots: (precio compra, cantidad, vela de compra)
    anchor = k[0][4]
    worst_hold = 0
    for i, (_, o, h, l, c) in enumerate(k):
        # ventas (solo tramos comprados en velas anteriores)
        keep = []
        for buy, qty, at in lots:
            tp = buy * (1 + target)
            if at < i and h >= tp:
                cash += qty * tp * (1 - FEE)
                trips += 1
            else:
                keep.append((buy, qty, at))
        lots = keep
        if not lots:
            anchor = max(anchor, h)
        # compras
        ref = min(b for b, _, _ in lots) if lots else anchor
        nxt = ref * (1 - step)
        while len(lots) < slots and l <= nxt and cash >= lot:
            qty = lot * (1 - FEE) / nxt
            cash -= lot
            lots.append((nxt, qty, i))
            nxt *= 1 - step
        if lots:
            worst_hold = max(worst_hold, i - min(at for _, _, at in lots))
        curve.append(cash + sum(q for _, q, _ in lots) * c)
    final = curve[-1]
    stuck = sum(q * k[-1][4] for _, q, _ in lots) - lot * len(lots)
    return {"final": final, "trips": trips, "dd": max_drawdown(curve), "open": len(lots), "stuck": stuck,
            "worst_days": worst_hold * 5 / 1440}


def dip(k, capital, drop, target, stop, rand=False, seed=1):
    """Comprar tras una caída desde el máximo de 24 h (o al azar), salir con objetivo, stop o a las 72 h."""
    rng = random.Random(seed)
    win = 288  # velas de 5 min en 24 h
    timeout = 3 * win
    cash, pos, trades, wins, curve = capital, None, 0, 0, []
    highs = []
    for i, (_, o, h, l, c) in enumerate(k):
        highs.append(h)
        if pos:
            buy, qty, at = pos
            hit_stop = stop and l <= buy * (1 - stop)
            hit_tp = h >= buy * (1 + target) and at < i
            if hit_stop or hit_tp or i - at >= timeout:
                px = buy * (1 - stop) if hit_stop else (buy * (1 + target) if hit_tp else c)
                cash = qty * px * (1 - FEE)
                trades += 1
                wins += px > buy * (1 + 2 * FEE)
                pos = None
        elif i >= win:
            signal = rng.random() < 1 / win if rand else c <= max(highs[i - win : i]) * (1 - drop)
            if signal:
                pos = (c, cash * (1 - FEE) / c, i)
                cash = 0.0
        curve.append(cash + (pos[1] * c if pos else 0.0))
    return {"final": curve[-1], "trades": trades, "wins": wins, "dd": max_drawdown(curve), "open": pos is not None}


def main(symbol: str, days: int, capital: float) -> None:
    console = Console(width=160)
    k = asyncio.run(download(symbol, days))
    span = (k[-1][0] - k[0][0]) / 86_400_000
    half = len(k) // 2
    periods = {"todo": k, "1.ª mitad": k[:half], "2.ª mitad": k[half:]}
    first, last = k[0][4], k[-1][4]
    console.print(f"[bold]{symbol}[/]: {len(k):,} velas de 5 min, {span:.0f} días · precio {first:,.0f} → {last:,.0f} "
                  f"({(last / first - 1) * 100:+.1f} %) · capital {capital:.0f} $ · comisión {FEE * 100:.3f} % por lado")

    def fmt(final, per_k):
        days_ = (per_k[-1][0] - per_k[0][0]) / 86_400_000
        return f"{(final / capital - 1) * 100:+.1f} %", f"{(final - capital) / days_:+.2f} $"

    t = Table(title="Comparación por periodo (rentabilidad total · ganancia media por día con tu capital)")
    t.add_column("Estrategia")
    for p in periods:
        t.add_column(f"{p}: total", justify="right")
        t.add_column(f"{p}: $/día", justify="right")
    t.add_column("Peor caída (todo)", justify="right")
    t.add_column("Detalle (todo)")

    def add(label, fn):
        cells, detail, dd = [], "", ""
        for name, per in periods.items():
            r = fn(per)
            cells += list(fmt(r["final"], per))
            if name == "todo":
                dd = f"{r['dd'] * 100:.1f} %"
                if "trips" in r:
                    detail = (f"{r['trips']} ventas · {r['open']} tramos atrapados ({r['stuck']:+.0f} $) · "
                              f"tramo más largo {r['worst_days']:.0f} d")
                else:
                    detail = f"{r['trades']} operaciones · {r['wins']} ganadoras" + (" · abierta al final" if r["open"] else "")
        t.add_row(label, *cells, dd, detail)

    add("Comprar y mantener", lambda per: {"final": capital * per[-1][4] / per[0][4] * (1 - FEE) ** 2,
                                          "dd": max_drawdown([x[4] for x in per]), "trades": 1, "wins": 0, "open": True})
    for step, target, slots in ((0.01, 0.01, 10), (0.02, 0.01, 10), (0.02, 0.02, 10), (0.03, 0.02, 5), (0.05, 0.03, 5)):
        add(f"Rejilla: compra cada −{step * 100:.0f} %, vende +{target * 100:.0f} %, {slots} tramos",
            lambda per, s=step, tg=target, n=slots: grid(per, capital, s, tg, n))
    for drop, target, stop in ((0.02, 0.01, 0.0), (0.02, 0.01, 0.03), (0.04, 0.02, 0.0), (0.04, 0.02, 0.05)):
        stop_txt = f"stop −{stop * 100:.0f} %" if stop else "sin stop"
        add(f"Caída −{drop * 100:.0f} % en 24 h → +{target * 100:.0f} %, {stop_txt}, máx. 72 h",
            lambda per, d=drop, tg=target, st=stop: dip(per, capital, d, tg, st))
    add("Al azar → +1 %, stop −1 %, máx. 72 h (referencia)",
        lambda per: dip(per, capital, 0, 0.01, 0.01, rand=True))
    console.print(t)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--simbolo", default="BTCUSDT")
    ap.add_argument("--dias", type=int, default=730)
    ap.add_argument("--capital", type=float, default=500)
    a = ap.parse_args()
    main(a.simbolo, a.dias, a.capital)
