"""Diferencia momentánea spot–perpetuo en Binance (una sola plataforma, sin triangular).

Cuando el perpetuo se pone más caro que el spot más allá de lo que cuestan las comisiones:
comprar spot + vender perpetuo (posición neutral al precio). Cuando vuelven a juntarse: vender
spot + recomprar perpetuo. Se gana lo que se cerró la diferencia.

- Se ejecuta con los precios de la foto SIGUIENTE a la señal (≈1,5 s después): no se usa
  información que el bot no tendría.
- 4 comisiones: spot 0,075 % ×2 (BNB) + perpetuo 0,05 % ×2 = 0,25 % por ida y vuelta.
- Una posición a la vez por par, `--tamano` dólares; solo pares con ≥ `--volumen-min` $/día en el perpetuo.

Uso: python -m research.basis_study --tamano 250
"""

from __future__ import annotations

import argparse
import json
import math
from array import array
from pathlib import Path

from rich.console import Console
from rich.table import Table

DATA = Path(__file__).parent / "data"
COST = 2 * 0.00075 + 2 * 0.0005


def load():
    meta = json.loads((DATA / "basis_meta.json").read_text(encoding="utf-8"))
    syms = meta["symbols"]
    width = 1 + 4 * len(syms)
    raw = array("d")
    path = DATA / "basis.bin"
    n = path.stat().st_size // (8 * width)
    with open(path, "rb") as fh:
        raw.fromfile(fh, n * width)
    rows = [raw[i * width : (i + 1) * width] for i in range(n)]
    return meta, syms, rows


def run(rows, i, enter, leave, size):
    trades, pnl, pos, wins = 0, 0.0, None, 0
    for k in range(len(rows) - 1):
        r, nxt = rows[k], rows[k + 1]
        sb, sa, pb, pa = r[1 + 4 * i : 5 + 4 * i]
        if not (sb > 0 and sa > 0 and pb > 0 and pa > 0):
            continue
        if pos is None and pb / sa - 1 > enter:
            nsb, nsa, npb, npa = nxt[1 + 4 * i : 5 + 4 * i]
            if nsa > 0 and npb > 0:
                pos = (nsa, npb)  # se ejecuta en la foto siguiente
        elif pos is not None and pa / sb - 1 < leave:
            nsb, nsa, npb, npa = nxt[1 + 4 * i : 5 + 4 * i]
            if nsb > 0 and npa > 0:
                spot_in, perp_in = pos
                ret = (nsb / spot_in - 1) + (perp_in / npa - 1) - COST
                pnl += ret * size
                trades += 1
                wins += ret > 0
                pos = None
    return trades, pnl, wins, pos is not None


def main(size: float, min_vol: float) -> None:
    console = Console(width=150)
    meta, syms, rows = load()
    vol = meta["perp_volume_24h"]
    hours = (rows[-1][0] - rows[0][0]) / 3_600_000
    liquid = [i for i, s in enumerate(syms) if vol.get(s, 0) >= min_vol]
    console.print(f"{len(rows)} fotos en {hours * 60:.0f} min · {len(syms)} pares con spot y perpetuo · "
                  f"{len(liquid)} con ≥{min_vol / 1e6:.0f} M$/día en el perpetuo")

    # ¿Con qué frecuencia el perpetuo se separa lo suficiente?
    t = Table(title="Prima del perpetuo sobre el spot (vender perp / comprar spot), pares líquidos")
    for col in ("Umbral de prima", "% de fotos con algún par por encima", "Pares distintos"):
        t.add_column(col, justify="right")
    for thr in (0.0025, 0.004, 0.006, 0.01):
        snaps, pairs = 0, set()
        for r in rows:
            hit = False
            for i in liquid:
                sb, sa, pb, pa = r[1 + 4 * i : 5 + 4 * i]
                if sa > 0 and pb > 0 and pb / sa - 1 > thr:
                    hit = True
                    pairs.add(syms[i])
            snaps += hit
        t.add_row(f"{thr * 100:.2f} %", f"{snaps / len(rows) * 100:.1f} %", str(len(pairs)))
    console.print(t)

    t = Table(title=f"Estrategia (comisiones {COST * 100:.2f} % por ida y vuelta, {size:.0f} $ por operación)")
    for col in ("Entrar si prima >", "Salir si prima <", "Operaciones", "Ganadoras", "Beneficio", "Beneficio/día (extrap.)",
                "Abiertas al final", "Mejores pares"):
        t.add_column(col, justify="left" if col == "Mejores pares" else "right")
    for enter in (0.003, 0.004, 0.006, 0.01):
        for leave in (0.0, 0.0005):
            total, n, w, still, per = 0.0, 0, 0, 0, []
            for i in liquid:
                trades, pnl, wins, open_ = run(rows, i, enter, leave, size)
                total += pnl
                n += trades
                w += wins
                still += open_
                if trades:
                    per.append((pnl, syms[i]))
            per.sort(reverse=True)
            t.add_row(f"{enter * 100:.2f} %", f"{leave * 100:.2f} %", str(n), f"{w}/{n}", f"{total:+.2f} $",
                      f"{total / hours * 24:+.2f} $", str(still), ", ".join(f"{s} {p:+.2f}" for p, s in per[:3]))
    console.print(t)
    console.print("'Abiertas al final': posiciones que no llegaron a cerrarse en la muestra (pueden tardar horas o días).")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--tamano", type=float, default=250)
    ap.add_argument("--volumen-min", type=float, default=5_000_000)
    a = ap.parse_args()
    main(a.tamano, a.volumen_min)
