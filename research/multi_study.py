"""Estudio multiplataforma con las fotos de collect_multi.

1. Arbitraje ENTRE exchanges: el mismo par (p. ej. SOL/USDT) más barato en uno y más caro en
   otro. Modelo "con inventario": tienes USDT en el exchange barato y la moneda en el caro, compras
   y vendes a la vez (sin transferir), y reequilibras de vez en cuando.
2. Arbitraje triangular DENTRO de cada exchange, con su comisión.

Uso: python -m research.multi_study
"""

from __future__ import annotations

import bisect
import json
import math
import statistics
from array import array
from collections import Counter, defaultdict
from pathlib import Path

from rich.console import Console
from rich.table import Table

DATA = Path(__file__).parent / "data"
STABLE = {"USDT", "USDC", "FDUSD", "TUSD", "USDP", "DAI", "USD1", "USDE", "PYUSD", "RLUSD", "USD"}

# Comisión taker de cliente normal, con el descuento por token propio cuando existe (fracción)
TAKER = {"binance": 0.00075, "okx": 0.001, "bybit": 0.001, "kucoin": 0.0008, "gate": 0.001,
         "mexc": 0.0005, "bitget": 0.0008, "htx": 0.002, "kraken": 0.004}
MAX_GAP = 0.05  # diferencias de más del 5 % suelen ser tokens distintos con el mismo nombre o depósitos cerrados
MIN_USD = 10.0
ALIGN_MS = 2500


def is_test(asset: str) -> bool:
    return "TEST" in asset


async def _transfer_status() -> dict[str, dict[str, bool]]:
    """{exchange: {activo: True si depósitos Y retiros están abiertos}} de los exchanges que lo publican sin claves."""
    import aiohttp

    out: dict[str, dict[str, bool]] = {}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
        async def get(url):
            async with s.get(url) as r:
                return await r.json(content_type=None)

        try:
            d = await get("https://api.gateio.ws/api/v4/spot/currencies")
            st: dict[str, bool] = {}
            for x in d:
                cur = x["currency"].split("_")[0].upper()
                ok = not (x.get("delisted") or x.get("withdraw_disabled") or x.get("deposit_disabled"))
                st[cur] = st.get(cur, False) or ok
            out["gate"] = st
        except Exception:
            pass
        try:
            d = await get("https://api.kucoin.com/api/v3/currencies")
            out["kucoin"] = {x["currency"].upper(): any(c.get("isDepositEnabled") and c.get("isWithdrawEnabled")
                                                        for c in (x.get("chains") or [])) for x in d["data"]}
        except Exception:
            pass
        try:
            d = await get("https://api.bitget.com/api/v2/spot/public/coins")
            out["bitget"] = {x["coin"].upper(): any(c.get("withdrawable") == "true" and c.get("rechargeable") == "true"
                                                    for c in (x.get("chains") or [])) for x in d["data"]}
        except Exception:
            pass
        try:
            d = await get("https://api.huobi.pro/v2/reference/currencies")
            out["htx"] = {x["currency"].upper(): any(c.get("depositStatus") == "allowed" and c.get("withdrawStatus") == "allowed"
                                                     for c in (x.get("chains") or [])) for x in d["data"]}
        except Exception:
            pass
        try:
            d = await get("https://api.kraken.com/0/public/Assets")
            out["kraken"] = {(x.get("altname") or k).upper().replace("XBT", "BTC").replace("XDG", "DOGE"):
                             x.get("status", "enabled") == "enabled" for k, x in d["result"].items()}
        except Exception:
            pass
    return out


def transfer_status() -> dict[str, dict[str, bool]]:
    import asyncio

    cache = DATA / "transfer_status.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    st = asyncio.run(_transfer_status())
    cache.write_text(json.dumps(st), encoding="utf-8")
    return st


def load():
    meta = json.loads((DATA / "multi_meta.json").read_text(encoding="utf-8"))
    venues = {}
    for name, syms in meta.items():
        path = DATA / f"multi_{name}.bin"
        if not path.exists():
            continue
        width = 2 + 4 * len(syms)
        raw = array("d")
        n = path.stat().st_size // (8 * width)
        with open(path, "rb") as fh:
            raw.fromfile(fh, n * width)
        rows = [raw[i * width : (i + 1) * width] for i in range(n)]
        venues[name] = {"syms": syms, "rows": rows, "times": [r[0] for r in rows]}
    return venues


def quote(row, i):
    j = 2 + 4 * i
    return row[j], row[j + 1], row[j + 2], row[j + 3]


def usd_table(venues, k_ref) -> dict[str, float]:
    """Precio en USD de cada activo con la fila más cercana de Binance (o del primero que lo tenga)."""
    usd = {s: 1.0 for s in STABLE}
    for name in ("binance", "okx", "bybit", "kucoin", "bitget", "gate", "mexc"):
        v = venues.get(name)
        if not v:
            continue
        row = v["rows"][min(k_ref, len(v["rows"]) - 1)]
        for i, (sym, base, q) in enumerate(v["syms"]):
            if base in usd or q not in STABLE:
                continue
            bid, _, ask, _ = quote(row, i)
            if bid > 0 and ask > 0:
                usd[base] = (bid + ask) / 2
    return usd


def cross_exchange(venues, console) -> dict:
    ref = max(venues, key=lambda n: len(venues[n]["rows"]))
    ref_times = venues[ref]["times"]
    pairs = defaultdict(list)
    for name, v in venues.items():
        for i, (sym, base, q) in enumerate(v["syms"]):
            if not (is_test(base) or is_test(q)):
                pairs[(base, q)].append((name, i))
    pairs = {p: lst for p, lst in pairs.items() if len({n for n, _ in lst}) >= 2}
    console.print(f"[bold]Entre exchanges:[/] {len(pairs)} pares que cotizan en 2 o más plataformas · "
                  f"{len(ref_times)} rondas de ~3 s")
    usd_cache = {}
    hits = []  # (ronda, par, comprar_en, vender_en, neto, capacidad USD)
    gross_over = Counter()
    for k, t in enumerate(ref_times):
        rows = {}
        for name, v in venues.items():
            j = bisect.bisect_left(v["times"], t)
            cands = [x for x in (j - 1, j) if 0 <= x < len(v["times"])]
            if not cands:
                continue
            best = min(cands, key=lambda x: abs(v["times"][x] - t))
            if abs(v["times"][best] - t) <= ALIGN_MS:
                rows[name] = v["rows"][best]
        if k % 50 == 0:
            usd = usd_table(venues, k)
        for (base, q), lst in pairs.items():
            best_sell = best_buy = None
            for name, i in lst:
                row = rows.get(name)
                if row is None:
                    continue
                bid, bq, ask, aq = quote(row, i)
                fee = TAKER[name]
                if bid > 0 and (best_sell is None or bid * (1 - fee) > best_sell[0]):
                    best_sell = (bid * (1 - fee), name, bid, bq)
                if ask > 0 and (best_buy is None or ask * (1 + fee) < best_buy[0]):
                    best_buy = (ask * (1 + fee), name, ask, aq)
            if not best_sell or not best_buy or best_sell[1] == best_buy[1]:
                continue
            net = best_sell[0] / best_buy[0] - 1
            if net > MAX_GAP:
                gross_over[(base, q)] += 1
                continue
            if net <= 0:
                continue
            qv = usd.get(q)
            sell_q, buy_q = best_sell[3], best_buy[3]
            # Si un exchange no publica la cantidad (Gate), la capacidad es desconocida
            cap = min(best_sell[2] * sell_q, best_buy[2] * buy_q) * qv if qv and sell_q > 0 and buy_q > 0 else math.nan
            hits.append((k, (base, q), best_buy[1], best_sell[1], net, cap))

    hours = (ref_times[-1] - ref_times[0]) / 3_600_000
    status = transfer_status()

    def transfer(base, buy, sell):
        known = [status[v].get(base) for v in (buy, sell) if v in status and base in status[v]]
        if any(x is False for x in known):
            return "cerrado"
        return "abierto" if len(known) == 2 else ("parcial" if known else "desconocido")

    valid = [h for h in hits if h[5] >= MIN_USD]
    unknown_cap = [h for h in hits if not h[5] >= MIN_USD and math.isnan(h[5])]
    eps = episodes(valid)
    best_of = [(max(ep, key=lambda h: h[4] * h[5]), len(ep)) for ep in eps]
    console.print(f"Oportunidades netas (después de las dos comisiones), ≥{MIN_USD:.0f} $ al mejor precio, "
                  f"diferencia ≤{MAX_GAP * 100:.0f} %: [bold]{len(eps)} episodios[/] en {hours * 60:.0f} min "
                  f"(+{len(episodes(unknown_cap))} con Gate, sin tamaño publicado)")
    if gross_over:
        console.print(f"Descartados por diferencia > {MAX_GAP * 100:.0f} %: {len(gross_over)} pares (p. ej. "
                      + ", ".join(f"{b}/{q}" for (b, q), _ in gross_over.most_common(6)) + ")")

    classes = {"fugaz (1 ronda, ≤3 s)": lambda n: n == 1, "media (2–9 rondas, 3–30 s)": lambda n: 2 <= n <= 9,
               "persistente (≥10 rondas, >30 s)": lambda n: n >= 10}
    t = Table(title="Arbitraje entre exchanges: por duración y estado de depósitos/retiros")
    for col in ("Duración", "Episodios", "Teórico $/h", "Transfer. cerrada", "Abierta (confirmado)",
                "Sin datos", "Neto mediano", "Capacidad mediana $"):
        t.add_column(col, justify="left" if col == "Duración" else "right")
    summary = {}
    for label, cond in classes.items():
        sel = [(h, n) for h, n in best_of if cond(n)]
        if not sel:
            t.add_row(label, "0", "—", "—", "—", "—", "—", "—")
            continue
        st = Counter(transfer(h[1][0], h[2], h[3]) for h, _ in sel)
        prof = sum(h[4] * h[5] for h, _ in sel) / hours
        summary[label] = {"episodes": len(sel), "per_hour": prof, **st}
        t.add_row(label, str(len(sel)), f"{prof:.2f}", str(st["cerrado"]), str(st["abierto"]),
                  str(st["desconocido"] + st["parcial"]), f"{statistics.median(h[4] for h, _ in sel) * 100:.3f} %",
                  f"{statistics.median(h[5] for h, _ in sel):.0f}")
    console.print(t)

    # Lo más prometedor: duración media, transferencias no cerradas
    good = [(h, n) for h, n in best_of if 2 <= n <= 9 and transfer(h[1][0], h[2], h[3]) != "cerrado"]
    flow = defaultdict(Counter)
    for h, _ in [(h, n) for h, n in best_of if transfer(h[1][0], h[2], h[3]) != "cerrado"]:
        a, b = sorted((h[2], h[3]))
        flow[(h[1], a, b)][(h[2], h[3])] += 1
    t = Table(title="Mejores pares 'capturables' (3–30 s, transferencias no cerradas)")
    for col in ("Par", "Comprar → vender", "Episodios", "Teórico $/h", "Neto medio", "Capacidad med. $", "Ida/vuelta"):
        t.add_column(col, justify="left" if col in ("Par", "Comprar → vender") else "right")
    by = defaultdict(list)
    for h, n in good:
        by[(h[1], h[2], h[3])].append(h)
    for (pair, buy, sell), hs in sorted(by.items(), key=lambda kv: -sum(h[4] * h[5] for h in kv[1]))[:15]:
        a, b = sorted((buy, sell))
        fl = flow[(pair, a, b)]
        t.add_row(f"{pair[0]}/{pair[1]}", f"{buy} → {sell}", str(len(hs)), f"{sum(h[4] * h[5] for h in hs) / hours:.3f}",
                  f"{statistics.mean(h[4] for h in hs) * 100:.3f} %", f"{statistics.median(h[5] for h in hs):.0f}",
                  f"{fl[(buy, sell)]}/{fl[(sell, buy)]}")
    console.print(t)
    combos = Counter()
    for h, n in good:
        combos[(h[2], h[3])] += h[4] * h[5]
    console.print("Combinaciones más rentables (capturables): " + " · ".join(
        f"{a}→{b} {v / hours:.2f} $/h" for (a, b), v in combos.most_common(8)))
    return {"hours": hours, "classes": summary,
            "capturable_per_hour": sum(h[4] * h[5] for h, _ in good) / hours, "capturable_episodes": len(good)}


def episodes(hits):
    by_key = defaultdict(list)
    for h in hits:
        by_key[(h[1], h[2], h[3])].append(h)
    out = []
    for items in by_key.values():
        items.sort()
        ep = [items[0]]
        for h in items[1:]:
            if h[0] == ep[-1][0] + 1:
                ep.append(h)
            else:
                out.append(ep)
                ep = [h]
        out.append(ep)
    return out


def triangular(venues, console) -> dict:
    t = Table(title="Arbitraje triangular dentro de cada exchange (comisión con descuento de su token; "
                    "solo rutas con ≥10 $ en cada tramo)")
    for col in ("Exchange", "Comisión", "Triángulos", "Mejor bruto (mediana)", "Mejor bruto (máx.)",
                "Episodios rentables ≥10 $", "Beneficio teórico $/h"):
        t.add_column(col, justify="left" if col == "Exchange" else "right")
    out = {}
    for name, v in sorted(venues.items()):
        syms, rows = v["syms"], v["rows"]
        fee = TAKER[name]
        pair = {}
        for i, (sym, base, q) in enumerate(syms):
            if is_test(base) or is_test(q):
                continue
            pair[(q, base)] = (i, True)
            pair[(base, q)] = (i, False)
        nbrs = defaultdict(set)
        for a, b in pair:
            nbrs[a].add(b)
        routes = []
        for a in sorted(nbrs):
            for b in nbrs[a]:
                if b <= a:
                    continue
                for c in nbrs[b]:
                    if c <= b or (c, a) not in pair:
                        continue
                    legs = (pair[(a, b)], pair[(b, c)], pair[(c, a)])
                    routes.append(((a, b, c), legs))
                    routes.append(((a, c, b), tuple((i, not buy) for i, buy in reversed(legs))))
        mult = (1 - fee) ** 3
        best_gross, hits = [], []
        for k, row in enumerate(rows):
            usd = usd_table(venues, k) if k % 50 == 0 else usd
            best = -1.0
            for r, (assets, legs) in enumerate(routes):
                rate, cap = 1.0, math.inf
                for i, buy in legs:
                    bid, bq, ask, aq = quote(row, i)
                    p, qty = (ask, aq) if buy else (bid, bq)
                    if not p > 0:
                        rate = 0.0
                        break
                    rate = rate / p if buy else rate * p
                    qv = usd.get(syms[i][2])
                    cap = min(cap, p * qty * qv if qv and qty > 0 else 0.0)
                if rate <= 0 or not cap >= MIN_USD:
                    continue  # solo cuentan rutas con liquidez real en los tres tramos
                best = max(best, rate - 1)
                net = rate * mult - 1
                if net > 0 and net < MAX_GAP and cap >= MIN_USD:
                    hits.append((k, r, net, cap))
            best_gross.append(best)
        hours = max((v["times"][-1] - v["times"][0]) / 3_600_000, 1e-9)
        eps = episodes([(k, r, 0, 0, net, cap) for k, r, net, cap in hits])
        profit = sum(max(h[4] * h[5] for h in ep) for ep in eps)
        bg = sorted(x for x in best_gross if x > -1) or [float("nan")]
        out[name] = {"routes": len(routes), "episodes": len(eps), "per_hour": profit / hours}
        t.add_row(name, f"{fee * 100:.3f} %", str(len(routes) // 2), f"{statistics.median(bg) * 100:.3f} %",
                  f"{bg[-1] * 100:.3f} %", str(len(eps)), f"{profit / hours:.3f}")
    console.print(t)
    return out


def main() -> None:
    console = Console(width=160)
    venues = load()
    console.print(", ".join(f"{n}: {len(v['rows'])} fotos" for n, v in venues.items()))
    res = {"cross": cross_exchange(venues, console), "triangular": triangular(venues, console)}
    (DATA / "multi_summary.json").write_text(json.dumps(res, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
