"""Fiat y oro/plata tokenizados entre exchanges + la pregunta de las micro-operaciones de 1 $.

Usa las fotos de collect_multi. A diferencia de multi_study, aquí un mismo mercado puede estar al
revés en cada exchange (EUR/USDT en Binance, USDT/EUR en Kraken), así que todo se pasa a una
orientación común antes de comparar.

Al final: con un capital de `--capital` dólares (la mitad en cada exchange), ¿cuántas veces por hora
hubo una oportunidad que dejara ≥ 1 $ neto? Incluye TODO (cripto, fiat y metales).

Uso: python -m research.assets_study --capital 500
"""

from __future__ import annotations

import argparse
import bisect
import math
import statistics
from collections import Counter, defaultdict

from rich.console import Console
from rich.table import Table

from research.multi_study import ALIGN_MS, MAX_GAP, MIN_USD, STABLE, TAKER, episodes, is_test, load, quote, transfer_status

FIAT = {"EUR", "GBP", "JPY", "TRY", "BRL", "ARS", "MXN", "AUD", "CAD", "CHF", "PLN", "ZAR", "UAH", "IDR", "NGN",
        "COP", "CZK", "RON", "KRW", "USD", "EURC", "EURI"}
METAL = {"PAXG", "XAUT", "XAUM", "KAG", "KAU"}


def usd_values(venues, k) -> dict[str, float]:
    """Precio en USD de cualquier activo: X/estable directo o estable/X invertido (fiat)."""
    usd = {s: 1.0 for s in STABLE}
    for name, v in venues.items():
        row = v["rows"][min(k, len(v["rows"]) - 1)]
        for i, (sym, b, q) in enumerate(v["syms"]):
            bid, _, ask, _ = quote(row, i)
            if not (bid > 0 and ask > 0):
                continue
            mid = (bid + ask) / 2
            if q in STABLE and b not in usd:
                usd[b] = mid
            elif b in STABLE and q not in usd:
                usd[q] = 1 / mid
    return usd


def canonical(venues):
    """{(A, B) ordenado: [(exchange, índice, invertido)]} solo para mercados con fiat o metales."""
    markets = defaultdict(list)
    for name, v in venues.items():
        for i, (sym, b, q) in enumerate(v["syms"]):
            if is_test(b) or is_test(q):
                continue
            key = tuple(sorted((b, q)))
            markets[key].append((name, i, key != (b, q)))
    return markets


def oriented(row, i, inverted):
    """bid/ask del mercado en orientación común; cantidades en unidades de la base común."""
    bid, bq, ask, aq = quote(row, i)
    if not inverted:
        return bid, bq, ask, aq
    if not (bid > 0 and ask > 0):
        return math.nan, math.nan, math.nan, math.nan
    return 1 / ask, aq * ask, 1 / bid, bq * bid


def scan(venues, markets, select):
    ref = max(venues, key=lambda n: len(venues[n]["rows"]))
    times = venues[ref]["times"]
    hits = []
    for k, t in enumerate(times):
        rows = {}
        for name, v in venues.items():
            j = min(max(bisect.bisect_left(v["times"], t), 0), len(v["times"]) - 1)
            if abs(v["times"][j] - t) <= ALIGN_MS:
                rows[name] = v["rows"][j]
        if k % 50 == 0:
            usd = usd_values(venues, k)
        for key, lst in markets.items():
            if not select(key) or len({n for n, _, _ in lst}) < 2:
                continue
            best_sell = best_buy = None
            for name, i, inv in lst:
                row = rows.get(name)
                if row is None:
                    continue
                bid, bq, ask, aq = oriented(row, i, inv)
                fee = TAKER[name]
                if bid > 0 and (best_sell is None or bid * (1 - fee) > best_sell[0]):
                    best_sell = (bid * (1 - fee), name, bid, bq)
                if ask > 0 and (best_buy is None or ask * (1 + fee) < best_buy[0]):
                    best_buy = (ask * (1 + fee), name, ask, aq)
            if not best_sell or not best_buy or best_sell[1] == best_buy[1]:
                continue
            net = best_sell[0] / best_buy[0] - 1
            if not 0 < net <= MAX_GAP:
                continue
            qv = usd.get(key[1])
            cap = (min(best_sell[2] * best_sell[3], best_buy[2] * best_buy[3]) * qv
                   if qv and best_sell[3] > 0 and best_buy[3] > 0 else math.nan)
            if cap >= MIN_USD:
                hits.append((k, key, best_buy[1], best_sell[1], net, cap))
    hours = (times[-1] - times[0]) / 3_600_000
    return hits, hours


def report(console, title, hits, hours, capital, status):
    eps = episodes(hits)
    best = [(max(ep, key=lambda h: h[4] * h[5]), len(ep)) for ep in eps]
    per_trade = capital / 2

    def closed(h):
        base = h[1][0] if h[1][0] not in STABLE else h[1][1]
        return any(status.get(v, {}).get(base) is False for v in (h[2], h[3]))

    t = Table(title=title)
    for col in ("Mercado", "Comprar → vender", "Episodios", "Neto medio", "Capacidad med. $",
                f"Ganancia/op. con {per_trade:.0f} $", "Duración media (rondas)", "Ida/vuelta"):
        t.add_column(col, justify="left" if col in ("Mercado", "Comprar → vender") else "right")
    by = defaultdict(list)
    flow = defaultdict(Counter)
    for h, n in best:
        by[(h[1], h[2], h[3])].append((h, n))
        a, b = sorted((h[2], h[3]))
        flow[(h[1], a, b)][(h[2], h[3])] += 1
    for (key, buy, sell), items in sorted(by.items(), key=lambda kv: -sum(h[4] * min(h[5], per_trade) for h, _ in kv[1]))[:12]:
        a, b = sorted((buy, sell))
        fl = flow[(key, a, b)]
        t.add_row(f"{key[0]}/{key[1]}", f"{buy} → {sell}", str(len(items)), f"{statistics.mean(h[4] for h, _ in items) * 100:.3f} %",
                  f"{statistics.median(h[5] for h, _ in items):.0f}",
                  f"{statistics.mean(h[4] * min(h[5], per_trade) for h, _ in items):.3f} $",
                  f"{statistics.mean(n for _, n in items):.1f}", f"{fl[(buy, sell)]}/{fl[(sell, buy)]}")
    console.print(t)
    return best, closed


def main(capital: float) -> None:
    console = Console(width=170)
    venues = load()
    status = transfer_status()
    markets = canonical(venues)

    def has(groups):
        return lambda key: any(a in groups for a in key) and not all(a in STABLE for a in key)

    for label, groups in (("ORO Y PLATA TOKENIZADOS", METAL), ("FIAT", FIAT)):
        hits, hours = scan(venues, markets, has(groups))
        console.print(f"\n[bold]{label}[/]: {len(episodes(hits))} episodios rentables en {hours * 60:.0f} min "
                      f"(después de las dos comisiones, ≥{MIN_USD:.0f} $)")
        report(console, f"{label}: mejores combinaciones", hits, hours, capital, status)

    # Pregunta de las micro-operaciones: todo el mercado (cripto + fiat + metales)
    hits, hours = scan(venues, markets, lambda key: not all(a in STABLE for a in key))
    eps = episodes(hits)
    per_trade = capital / 2
    rows = []
    for ep in eps:
        h = max(ep, key=lambda x: x[4] * min(x[5], per_trade))
        base = h[1][0] if h[1][0] not in STABLE else h[1][1]
        closed = any(status.get(v, {}).get(base) is False for v in (h[2], h[3]))
        rows.append((h[4] * min(h[5], per_trade), len(ep), closed))
    t = Table(title=f"Micro-operaciones con {capital:.0f} $ de capital ({per_trade:.0f} $ por operación), todo el mercado")
    for col in ("Filtro", "Oportunidades/hora", "Ganancia/hora si capturas todas", "Equivalente/día"):
        t.add_column(col, justify="left" if col == "Filtro" else "right")
    filters = [
        ("≥ 1 $ netos, cualquier duración", lambda p, n, c: p >= 1),
        ("≥ 1 $ y dura ≥ 2 rondas (3–6 s+)", lambda p, n, c: p >= 1 and n >= 2),
        ("≥ 1 $, ≥ 2 rondas y transferencias no cerradas", lambda p, n, c: p >= 1 and n >= 2 and not c),
        ("≥ 0,10 $, ≥ 2 rondas, transferencias no cerradas", lambda p, n, c: p >= 0.1 and n >= 2 and not c),
    ]
    for label, cond in filters:
        sel = [r for r in rows if cond(*r)]
        t.add_row(label, f"{len(sel) / hours:.0f}", f"{sum(r[0] for r in sel) / hours:.2f} $",
                  f"{sum(r[0] for r in sel) / hours * 24:.0f} $")
    console.print(t)
    console.print("Nota: 'si capturas todas' es un techo. En la práctica compites con otros bots, muchas de estas "
                  "diferencias van siempre en el mismo sentido (hay que trasladar fondos y pagar retiros) y algunas "
                  "son tokens distintos con el mismo nombre.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--capital", type=float, default=500)
    main(ap.parse_args().capital)
