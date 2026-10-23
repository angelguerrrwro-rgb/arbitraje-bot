"""Estudio del arbitraje triangular sobre las fotos grabadas con collect_books.

Responde con datos:
1. ¿Cuánta ventaja bruta hay de verdad y con qué comisión sale rentable?
2. ¿Cuántos USD caben al mejor precio (capacidad) y cuánto se ganaría como máximo por hora?
3. ¿Cuánto dura cada oportunidad? (fotos consecutivas en las que sigue viva)
4. ¿Qué pares la forman? (las ventajas grandes suelen estar en pares raros y sin liquidez)
5. Variante maker: ¿hay hueco para poner una orden pasiva en un par cruzado y cubrir las otras
   dos patas como taker cuando se llene?

Uso: python -m research.triangle_study
"""

from __future__ import annotations

import json
import math
import statistics
from array import array
from collections import Counter, defaultdict
from pathlib import Path

from rich.console import Console
from rich.table import Table

DATA = Path(__file__).parent / "data"
STABLE = {"USDT", "USDC", "FDUSD", "TUSD", "USDP", "DAI", "U", "USD1", "USDE"}
ZERO_FEE_PROMO = {"UUSDT", "UUSDC"}  # promoción "U" sin comisión (verificar en tu cuenta con /api/v3/account/commission)
FEE_SCENARIOS = {  # comisión taker por tramo (fracción) según el par
    "VIP0 (0,1 %)": lambda s: 0.001,
    "VIP0 + BNB (0,075 %)": lambda s: 0.00075,
    "BNB + promos U a 0 %": lambda s: 0.0 if s in ZERO_FEE_PROMO else 0.00075,
    "VIP3 aprox. (0,042 %)": lambda s: 0.00042,
    "VIP9 aprox. (0,023 %)": lambda s: 0.00023,
    "Sin comisión": lambda s: 0.0,
}
MIN_USD = 10.0  # por debajo de esto no supera el minNotional típico
MAKER_FEE = 0.00075
TAKER_FEE = 0.00075


def load():
    meta = json.loads((DATA / "meta.json").read_text(encoding="utf-8"))
    syms = meta["symbols"]
    width = meta["header"] + 4 * len(syms)
    raw = array("d")
    path = DATA / "books.bin"
    n_rows = path.stat().st_size // (8 * width)
    with open(path, "rb") as fh:
        raw.fromfile(fh, n_rows * width)
    rows = [raw[i * width : (i + 1) * width] for i in range(n_rows)]
    return meta, rows


def triangles(syms: list[dict]) -> list[tuple]:
    """Triángulos {A, B, C} una sola vez (A el menor, B < C), en el sentido A→B→C→A; el otro
    sentido lo añade quien llama. Cada tramo: (índice del par, True si es compra)."""
    pair = {}
    for i, s in enumerate(syms):
        pair[(s["quote"], s["base"])] = (i, True)  # gastar quote → recibir base: comprar
        pair[(s["base"], s["quote"])] = (i, False)  # gastar base → recibir quote: vender
    nbrs = defaultdict(set)
    for a, b in pair:
        nbrs[a].add(b)
    out = []
    for a in sorted(nbrs):
        for b in nbrs[a]:
            if b <= a:
                continue
            for c in nbrs[b]:
                if c <= b or (c, a) not in pair:
                    continue
                out.append(((a, b, c), (pair[(a, b)], pair[(b, c)], pair[(c, a)])))
    return out


def usd_prices(row, syms, index, header) -> dict[str, float]:
    def mid(sym):
        i = index.get(sym)
        if i is None:
            return None
        j = header + 4 * i
        bid, ask = row[j], row[j + 2]
        if math.isnan(bid) or bid <= 0 or ask <= 0:
            return None
        return (bid + ask) / 2

    usd = {s: 1.0 for s in STABLE}
    assets = {s["base"] for s in syms} | {s["quote"] for s in syms}
    for a in assets:
        if a in usd:
            continue
        for q in ("USDT", "USDC", "FDUSD"):
            p = mid(a + q)
            if p:
                usd[a] = p
                break
        else:
            for q in ("USDT", "USDC"):
                p = mid(q + a)
                if p:
                    usd[a] = 1 / p
                    break
    for a in assets:  # segunda pasada vía BTC/ETH/BNB
        if a not in usd:
            for bridge in ("BTC", "ETH", "BNB"):
                p = mid(a + bridge)
                if p and bridge in usd:
                    usd[a] = p * usd[bridge]
                    break
    return usd


def main() -> None:
    console = Console(width=150)
    meta, rows = load()
    syms = meta["symbols"]
    H = meta["header"]
    index = {s["symbol"]: i for i, s in enumerate(syms)}
    last_usd = usd_prices(rows[-1], syms, index, H)
    quote_of = {s["symbol"]: s["quote"] for s in syms}
    vol = {s: v * last_usd.get(quote_of[s], 0.0) for s, v in meta["quote_volume_24h"].items() if s in quote_of}
    tris = triangles(syms)
    n = len(rows)
    span_s = (rows[-1][0] - rows[0][0]) / 1000
    console.print(f"[bold]{n} fotos[/] en {span_s / 60:.1f} min (una cada {span_s / max(n - 1, 1):.2f} s) · "
                  f"{len(syms)} pares · {len(tris)} triángulos ({2 * len(tris)} rutas contando los dos sentidos)")

    # Cada triángulo en sus dos sentidos
    routes = []
    for assets, legs in tris:
        routes.append((assets, legs))
        a, b, c = assets
        rev = tuple((i, not buy) for i, buy in reversed(legs))
        routes.append(((a, c, b), rev))
    # Multiplicador de comisiones de cada ruta en cada escenario (solo depende de sus pares)
    fee_mult = {
        name: [math.prod(1 - fn(syms[i]["symbol"]) for i, _ in legs) for _, legs in routes]
        for name, fn in FEE_SCENARIOS.items()
    }

    best_gross = []  # mejor ventaja bruta por foto
    hits = {name: [] for name in FEE_SCENARIOS}  # (foto, ruta, ventaja neta, capacidad USD)
    usd_cache = []
    for k, row in enumerate(rows):
        usd = usd_prices(row, syms, index, H)
        usd_cache.append(usd)
        best = -1.0
        for r, (assets, legs) in enumerate(routes):
            rate = 1.0
            ok = True
            for i, buy in legs:
                j = H + 4 * i
                p = row[j + 2] if buy else row[j]
                if not (p > 0):
                    ok = False
                    break
                rate = rate / p if buy else rate * p
            if not ok:
                continue
            gross = rate - 1
            if gross > best:
                best = gross
            if gross <= 0:
                continue
            # capacidad: USD al mejor precio en cada tramo, el mínimo
            cap = math.inf
            for i, buy in legs:
                j = H + 4 * i
                price, qty = (row[j + 2], row[j + 3]) if buy else (row[j], row[j + 1])
                qv = usd.get(syms[i]["quote"])
                cap = min(cap, price * qty * qv if qv else 0.0)
            for name in FEE_SCENARIOS:
                net = rate * fee_mult[name][r] - 1
                if net > 0:
                    hits[name].append((k, r, net, cap))
        best_gross.append(best)

    # 1. Ventaja bruta
    bg = sorted(best_gross)
    pct = lambda q: bg[min(len(bg) - 1, int(q * len(bg)))] * 100
    console.print(f"\n[bold]1. Mejor ventaja BRUTA de todo el mercado en cada foto[/] (antes de comisiones): "
                  f"mediana {pct(0.5):.3f} % · p90 {pct(0.9):.3f} % · p99 {pct(0.99):.3f} % · máx {bg[-1] * 100:.3f} %")
    console.print(f"   Umbral a superar: 0,300 % con VIP0 · 0,225 % con BNB · ~0,126 % VIP3 · ~0,069 % VIP9")

    # 2. Rentabilidad por escenario de comisión
    t = Table(title="2. Oportunidades rentables por nivel de comisión (taker, al mejor precio)")
    for col in ("Comisión", "Fotos con alguna", "Rutas distintas", "Con ≥10 $ de capacidad",
                "Beneficio máx. teórico/hora", "Mediana capacidad $"):
        t.add_column(col, justify="left" if col == "Comisión" else "right")
    summary = {}
    for name, hs in hits.items():
        snaps = {h[0] for h in hs}
        big = [h for h in hs if h[3] >= MIN_USD]
        # Cada oportunidad se cobra una sola vez aunque dure varias fotos: se cuenta su mejor momento
        episodes = count_episodes(big)
        profit = sum(net * cap for (k, r, net, cap) in first_of_episode(big))
        per_hour = profit / (span_s / 3600)
        caps = sorted(h[3] for h in big)
        summary[name] = {"snaps": len(snaps), "routes": len({h[1] for h in hs}), "episodes": episodes,
                         "per_hour": per_hour}
        t.add_row(name, f"{len(snaps)} ({len(snaps) / n * 100:.1f} %)", str(len({h[1] for h in hs})),
                  f"{episodes} episodios", f"{per_hour:.3f} $", f"{statistics.median(caps):.0f}" if caps else "—")
    console.print(t)

    # 3. Duración y 4. composición, con la mejor comisión realista (BNB + promociones)
    name = "BNB + promos U a 0 %"
    big = [h for h in hits[name] if h[3] >= MIN_USD]
    durations = episode_lengths(big)
    if durations:
        c = Counter(durations)
        console.print(f"\n[bold]3. Duración con {name}[/] (fotos consecutivas ≈ segundos): " +
                      " · ".join(f"{d} foto{'s' if d > 1 else ''}: {c[d]}" for d in sorted(c)[:8]))
    else:
        console.print(f"\n[bold]3. Duración con {name}[/]: ninguna oportunidad con ≥{MIN_USD:.0f} $ de capacidad")
    sym_count = Counter()
    for k, r, net, cap in first_of_episode(big):
        for i, _ in routes[r][1]:
            sym_count[syms[i]["symbol"]] += 1
    if sym_count:
        t = Table(title=f"4. Pares que más aparecen en oportunidades rentables ({name})")
        for col in ("Par", "Veces", "Volumen 24 h $"):
            t.add_column(col, justify="left" if col == "Par" else "right")
        for s, cnt in sym_count.most_common(15):
            t.add_row(s, str(cnt), f"{vol.get(s, 0):,.0f}")
        console.print(t)
    top = sorted(first_of_episode(big), key=lambda h: -h[2] * h[3])[:10]
    if top:
        t = Table(title=f"Mejores episodios ({name})")
        for col in ("Ruta", "Neto %", "Capacidad $", "Beneficio $", "Dura (fotos)"):
            t.add_column(col, justify="left" if col == "Ruta" else "right")
        lengths = episode_length_map(big)
        for k, r, net, cap in top:
            t.add_row(" → ".join(routes[r][0] + (routes[r][0][0],)), f"{net * 100:.3f}", f"{cap:.0f}",
                      f"{net * cap:.4f}", str(lengths.get((k, r), 1)))
        console.print(t)

    # 5. Variante maker en el tramo cruzado
    maker = maker_room(rows, routes, syms, H, usd_cache, vol)
    t = Table(title="5. Hueco para orden maker en un par (las otras dos patas como taker con BNB)")
    for col in ("Par maker", "% fotos con hueco", "Margen medio", "Volumen 24 h $", "Ruta"):
        t.add_column(col, justify="left" if col in ("Par maker", "Ruta") else "right")
    for row_ in maker[:15]:
        t.add_row(*row_)
    console.print(t)

    out = {"snapshots": n, "minutes": span_s / 60, "triangles": len(tris), "best_gross_pct": {
        "p50": pct(0.5), "p90": pct(0.9), "p99": pct(0.99), "max": bg[-1] * 100}, "scenarios": summary}
    (DATA / "triangle_summary.json").write_text(json.dumps(out, indent=1), encoding="utf-8")


def _episodes(hs):
    """Agrupa (foto, ruta) consecutivos de la misma ruta en episodios."""
    by_route = defaultdict(list)
    for h in hs:
        by_route[h[1]].append(h)
    for r, items in by_route.items():
        items.sort()
        ep = [items[0]]
        for h in items[1:]:
            if h[0] == ep[-1][0] + 1:
                ep.append(h)
            else:
                yield ep
                ep = [h]
        yield ep


def count_episodes(hs) -> int:
    return sum(1 for _ in _episodes(hs))


def first_of_episode(hs):
    return [max(ep, key=lambda h: h[2] * h[3]) for ep in _episodes(hs)]


def episode_lengths(hs) -> list[int]:
    return [len(ep) for ep in _episodes(hs)]


def episode_length_map(hs) -> dict:
    out = {}
    for ep in _episodes(hs):
        best = max(ep, key=lambda h: h[2] * h[3])
        out[(best[0], best[1])] = len(ep)
    return out


def maker_room(rows, routes, syms, H, usd_cache, vol):
    """Para cada ruta y cada tramo como maker: si pongo mi orden 1 tick mejor que el mejor precio
    actual del tramo maker (aproximado con un 0,01 % de mejora) y se llena, y cubro los otros dos
    tramos como taker al precio actual, ¿gano después de comisiones? Mide en qué % de fotos hay
    hueco y con qué margen. No estima la probabilidad de que la orden se llene."""
    stats = defaultdict(lambda: [0, 0.0, None])  # (par, ruta) -> [fotos con hueco, suma margen, ruta]
    improve = 0.0001
    n = len(rows)
    for k, row in enumerate(rows):
        if k % 5:  # muestreo para no tardar demasiado
            continue
        for assets, legs in routes:
            prices = []
            ok = True
            for i, buy in legs:
                j = H + 4 * i
                bid, ask = row[j], row[j + 2]
                if not (bid > 0 and ask > 0):
                    ok = False
                    break
                prices.append((bid, ask))
            if not ok:
                continue
            for m in range(3):
                rate = 1.0
                for x, (i, buy) in enumerate(legs):
                    bid, ask = prices[x]
                    if x == m:
                        # orden pasiva: mejoro el mejor precio si cabe en el spread, si no me pongo a la par
                        if buy:
                            p = bid * (1 + improve)
                            p = p if p < ask else bid
                        else:
                            p = ask * (1 - improve)
                            p = p if p > bid else ask
                        rate = rate / p if buy else rate * p
                        rate *= 1 - MAKER_FEE
                    else:
                        rate = rate / ask if buy else rate * bid
                        rate *= 1 - TAKER_FEE
                if rate > 1:
                    key = (syms[legs[m][0]]["symbol"], assets)
                    st = stats[key]
                    st[0] += 1
                    st[1] += rate - 1
    sampled = (n + 4) // 5
    out = []
    for (sym, assets), (cnt, margin, _) in stats.items():
        if vol.get(sym, 0) >= 20_000:  # con menos volumen la orden casi nunca se llenaría
            out.append((cnt, sym, assets, margin / cnt))
    out.sort(key=lambda x: (-x[0], -x[3]))
    rows_ = []
    seen = set()
    for cnt, sym, assets, m in out:
        if sym in seen:
            continue
        seen.add(sym)
        rows_.append((sym, f"{cnt / sampled * 100:.0f} %", f"{m * 100:.3f} %", f"{vol.get(sym, 0):,.0f}",
                      " → ".join(assets + (assets[0],))))
    return rows_


if __name__ == "__main__":
    main()
