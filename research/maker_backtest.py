"""Backtest de la estrategia maker en pares cruzados, con operaciones reales de Binance.

Idea: en vez de competir en velocidad cazando huecos (pierde siempre contra los más rápidos),
dejar una orden pasiva en un par poco líquido (p. ej. comprar X con Q) a un precio que, si se
llena, permite cerrar el ciclo con ganancia en dos pares líquidos: X → Z → Q como taker.

Simulación (pesimista a propósito):
- Con cada foto (≈1 s) se recalcula la orden: 1 tick (≈0,01 %) mejor que el mejor precio del
  par, solo si a ese precio el ciclo deja al menos `objetivo` tras comisiones. Si no, no se cotiza.
- La orden se llena si en su ventana (de la foto k a la k+1, más la latencia) hubo una operación
  agresora en ese lado a un precio igual o peor que el nuestro (aggTrades reales).
- La cobertura se hace con los precios de la primera foto posterior a la ejecución + latencia.
  En un servidor real serían ~0,1 s, aquí hasta ~1 s: el resultado incluye de sobra el riesgo
  de que te ejecuten justo cuando el precio se mueve en tu contra (selección adversa).
- Comisiones: maker 0,075 % (0 % en los pares FDUSD con promoción), taker 0,075 % (con BNB).

Uso: python -m research.maker_backtest
"""

from __future__ import annotations

import asyncio
import bisect
import json
from collections import defaultdict
from pathlib import Path

import aiohttp
from rich.console import Console
from rich.table import Table

from research.triangle_study import DATA, load, usd_prices
from triarb.binance import LIVE_REST, BinanceRest

ZERO_MAKER = {"BTCFDUSD", "BNBFDUSD", "DOGEFDUSD", "ETHFDUSD", "LINKFDUSD", "SOLFDUSD", "XRPFDUSD"}
TAKER = 0.00075
MAKER = 0.00075
IMPROVE = 0.0001
QUOTE_USD = 50.0
LATENCY_MS = 200
HEDGE_LATENCY_MS = 300
VOL_MIN = 20_000  # USD/24 h del par donde se cotiza
HEDGE_VOL_MIN = 500_000  # USD/24 h de los pares de cobertura
TARGETS = (0.0005, 0.001, 0.002)


def build(meta, rows):
    syms = meta["symbols"]
    H = meta["header"]
    index = {s["symbol"]: i for i, s in enumerate(syms)}
    usd_last = usd_prices(rows[-1], syms, index, H)
    vol = {s["symbol"]: meta["quote_volume_24h"].get(s["symbol"], 0) * usd_last.get(s["quote"], 0) for s in syms}
    pair = {}
    for i, s in enumerate(syms):
        pair[(s["quote"], s["base"])] = (i, True)
        pair[(s["base"], s["quote"])] = (i, False)
    nbrs = defaultdict(set)
    for a, b in pair:
        nbrs[a].add(b)
    # (índice del par maker, lado, [rutas de cobertura: (tramo1, tramo2)])
    cands = []
    for i, s in enumerate(syms):
        if vol[s["symbol"]] < VOL_MIN:
            continue
        x, q = s["base"], s["quote"]
        bid_hedges, ask_hedges = [], []
        for z in nbrs[x] & nbrs[q]:
            l1, l2 = pair[(x, z)], pair[(z, q)]  # cotizo compra: Q→X (maker), X→Z, Z→Q
            if vol[syms[l1[0]]["symbol"]] >= HEDGE_VOL_MIN and vol[syms[l2[0]]["symbol"]] >= HEDGE_VOL_MIN:
                bid_hedges.append((l1, l2))
            l1, l2 = pair[(q, z)], pair[(z, x)]  # cotizo venta: X→Q (maker), Q→Z, Z→X
            if vol[syms[l1[0]]["symbol"]] >= HEDGE_VOL_MIN and vol[syms[l2[0]]["symbol"]] >= HEDGE_VOL_MIN:
                ask_hedges.append((l1, l2))
        if bid_hedges:
            cands.append((i, "BUY", bid_hedges))
        if ask_hedges:
            cands.append((i, "SELL", ask_hedges))
    return syms, H, index, vol, cands


def leg_rate(row, H, leg):
    i, buy = leg
    j = H + 4 * i
    p = row[j + 2] if buy else row[j]
    return (1.0 / p if buy else p) if p > 0 else 0.0


def quotes(rows, H, cand, target, maker_fee):
    """Para cada foto: (precio de la orden, cobertura elegida) o None."""
    i, side, hedges = cand
    out = []
    for row in rows:
        j = H + 4 * i
        bid, ask = row[j], row[j + 2]
        if not (bid > 0 and ask > 0):
            out.append(None)
            continue
        best = None
        for h in hedges:
            r = leg_rate(row, H, h[0]) * leg_rate(row, H, h[1]) * (1 - TAKER) ** 2 * (1 - maker_fee)
            if r <= 0:
                continue
            if side == "BUY":  # pago p por X: multiplicador r/p ≥ 1+target
                limit = r / (1 + target)
                if best is None or limit > best[0]:
                    best = (limit, h)
            else:  # recibo p por X: multiplicador p·r ≥ 1+target
                limit = (1 + target) / r
                if best is None or limit < best[0]:
                    best = (limit, h)
        if best is None:
            out.append(None)
            continue
        limit, h = best
        if side == "BUY":
            price = bid * (1 + IMPROVE)
            ok = price < ask and price <= limit
        else:
            price = ask * (1 - IMPROVE)
            ok = price > bid and price >= limit
        out.append((price, h) if ok else None)
    return out


async def fetch_trades(symbols: list[str], start: int, end: int) -> dict[str, list]:
    trades: dict[str, list] = {}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
        rest = BinanceRest(s, LIVE_REST)
        for sym in symbols:
            seen, rows, since = set(), [], start
            while True:
                batch = await rest.request("GET", "/api/v3/aggTrades",
                                           {"symbol": sym, "startTime": since, "endTime": end, "limit": 1000})
                new = [b for b in batch if b["a"] not in seen]
                for b in new:
                    seen.add(b["a"])
                    rows.append((b["T"], float(b["p"]), float(b["q"]), b["m"]))
                if len(batch) < 1000 or not new:
                    break
                since = batch[-1]["T"]
                await asyncio.sleep(0.05)
            trades[sym] = sorted(rows)
            await asyncio.sleep(0.05)
    return trades


def simulate(rows, H, syms, usd_rows, cand, qts, trades, times, hedge_after=True):
    """hedge_after=True: cobertura con la primera foto posterior a la ejecución + latencia (pesimista).
    hedge_after=False: con la última foto anterior a la ejecución, lo mejor que lograría un servidor
    muy rápido (optimista). La realidad en un VPS en Tokio quedaría entre los dos."""
    i, side, _ = cand
    sym = syms[i]
    fills = []
    tlist = trades.get(sym["symbol"], [])
    ttimes = [t[0] for t in tlist]
    for k in range(len(rows) - 1):
        qt = qts[k]
        if qt is None:
            continue
        price, hedge = qt
        w0, w1 = times[k] + LATENCY_MS, times[k + 1] + LATENCY_MS
        a = bisect.bisect_left(ttimes, w0)
        b = bisect.bisect_left(ttimes, w1)
        usd_q = usd_rows[k].get(sym["quote"])
        if not usd_q:
            continue
        need = QUOTE_USD / usd_q / price  # cantidad de X de nuestra orden
        filled, t_fill = 0.0, None
        for t, p, q, buyer_maker in tlist[a:b]:
            aggressive_sell = buyer_maker
            if side == "BUY" and aggressive_sell and p <= price or side == "SELL" and not aggressive_sell and p >= price:
                take = min(q, need - filled)
                filled += take
                t_fill = t_fill or t
                if filled >= need:
                    break
        if not filled:
            continue
        if hedge_after:
            j = bisect.bisect_left(times, t_fill + HEDGE_LATENCY_MS)
        else:
            j = max(k, bisect.bisect_right(times, t_fill) - 1)
        if j >= len(rows):
            continue
        row = rows[j]
        r = leg_rate(row, H, hedge[0]) * leg_rate(row, H, hedge[1]) * (1 - TAKER) ** 2
        maker_fee = 0.0 if sym["symbol"] in ZERO_MAKER else MAKER
        if side == "BUY":  # gasté filled·price Q, recibo X·(1-fee) y lo convierto a Q
            spent_q = filled * price
            got_q = filled * (1 - maker_fee) * r
            pnl_usd = (got_q - spent_q) * usd_q
        else:  # vendí filled X por Q, recompro X con ese Q
            got_x = filled * price * (1 - maker_fee) * r
            usd_x = usd_rows[j].get(sym["base"]) or 0.0
            pnl_usd = (got_x - filled) * usd_x
        fills.append((t_fill, filled * price * usd_q, pnl_usd))
        # tras ejecutarse, la orden no se repone hasta la foto siguiente
    return fills


def main() -> None:
    console = Console(width=150)
    meta, rows = load()
    syms, H, index, vol, cands = build(meta, rows)
    times = [row[0] for row in rows]
    usd_rows = [usd_prices(r, syms, index, H) for r in rows]
    hours = (times[-1] - times[0]) / 3_600_000
    console.print(f"{len(rows)} fotos ({hours * 60:.1f} min) · {len(cands)} candidatos (par × lado) con volumen suficiente")

    # Primero las cotizaciones, para descargar operaciones solo de los pares donde llegamos a cotizar
    all_quotes = {}
    needed = set()
    for target in TARGETS:
        for c in cands:
            maker_fee = 0.0 if syms[c[0]]["symbol"] in ZERO_MAKER else MAKER
            q = quotes(rows, H, c, target, maker_fee)
            all_quotes[(target, c[0], c[1])] = q
            if any(q):
                needed.add(syms[c[0]]["symbol"])
    console.print(f"Pares donde alguna vez se podría cotizar con margen: {len(needed)} → descargando sus operaciones…")
    cache = DATA / "aggtrades.json"
    trades = json.loads(cache.read_text()) if cache.exists() else {}
    missing = sorted(needed - set(trades))
    if missing:
        trades.update(asyncio.run(fetch_trades(missing, int(times[0]), int(times[-1]) + 2000)))
        cache.write_text(json.dumps(trades))

    summary = {}
    for target in TARGETS:
        per = []
        for c in cands:
            q = all_quotes[(target, c[0], c[1])]
            if not any(q):
                continue
            fills = simulate(rows, H, syms, usd_rows, c, q, trades, times)
            active = sum(1 for x in q if x) / len(q)
            per.append((syms[c[0]]["symbol"], c[1], active, fills))
        total = sum(f[2] for _, _, _, fs in per for f in fs)
        n_fills = sum(len(fs) for *_, fs in per)
        wins = sum(1 for *_, fs in per for f in fs if f[2] > 0)
        volume = sum(f[1] for *_, fs in per for f in fs)
        fast = [simulate(rows, H, syms, usd_rows, c, all_quotes[(target, c[0], c[1])], trades, times, False)
                for c in cands if any(all_quotes[(target, c[0], c[1])])]
        fast_pnl = sum(f[2] for fs in fast for f in fs)
        fast_wins = sum(1 for fs in fast for f in fs if f[2] > 0)
        summary[target] = {"fills": n_fills, "pnl": total, "per_hour": total / hours, "wins": wins, "volume": volume,
                           "fast_pnl": fast_pnl, "fast_per_hour": fast_pnl / hours, "fast_wins": fast_wins}
        console.print(f"[bold]Objetivo {target * 100:.2f} %[/]: cobertura lenta (foto siguiente) {total:+.3f} $ "
                      f"({total / hours:+.3f} $/h, {wins}/{n_fills} ganadoras) · cobertura instantánea (foto anterior) "
                      f"{fast_pnl:+.3f} $ ({fast_pnl / hours:+.3f} $/h, {fast_wins}/{n_fills} ganadoras)")
        t = Table(title=f"Objetivo de margen {target * 100:.2f} % · orden de {QUOTE_USD:.0f} $ · "
                        f"{n_fills} ejecuciones · PnL {total:+.3f} $ ({total / hours:+.3f} $/h) · "
                        f"aciertos {wins}/{n_fills}")
        for col in ("Par", "Lado", "% tiempo cotizando", "Ejecuciones", "Volumen $", "PnL $", "PnL medio por ejecución"):
            t.add_column(col, justify="left" if col in ("Par", "Lado") else "right")
        per.sort(key=lambda x: -abs(sum(f[2] for f in x[3])))
        for sym, side, active, fs in per[:12]:
            pnl = sum(f[2] for f in fs)
            t.add_row(sym, "compra" if side == "BUY" else "venta", f"{active * 100:.0f} %", str(len(fs)),
                      f"{sum(f[1] for f in fs):.0f}", f"{pnl:+.4f}", f"{pnl / len(fs):+.4f}" if fs else "—")
        console.print(t)
    (DATA / "maker_summary.json").write_text(json.dumps({str(k): v for k, v in summary.items()}, indent=1),
                                             encoding="utf-8")


if __name__ == "__main__":
    main()
