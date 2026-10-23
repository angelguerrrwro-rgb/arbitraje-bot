"""`python -m triarb analizar`: estadísticas de lo registrado en logs/."""

from __future__ import annotations

import csv
import math
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from rich import box
from rich.console import Console
from rich.table import Table

BUCKETS = [(0, 10), (10, 50), (50, 100), (100, 250), (250, 1000), (1000, math.inf)]


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def analyze(folder: str) -> None:
    console = Console()
    base = Path(folder)
    opps = _read(base / "oportunidades.csv")
    if not opps:
        console.print(f"No hay oportunidades registradas en {base.resolve()}")
    else:
        first = datetime.fromisoformat(opps[0]["inicio"])
        last = datetime.fromisoformat(opps[-1]["inicio"])
        hours = max((last - first).total_seconds() / 3600, 1 / 60)
        total_usd = sum(float(r["pico_usd"]) for r in opps)
        console.print(f"[bold]{len(opps)} oportunidades[/] entre {first:%Y-%m-%d %H:%M} y {last:%Y-%m-%d %H:%M} "
                      f"({len(opps) / hours:.1f}/hora) · potencial {total_usd:.4f} USD ({total_usd / hours:.4f} USD/hora)")

        t = Table(title="¿Cuánto duran?", box=box.SIMPLE_HEAVY)
        t.add_column("Duración")
        t.add_column("Cantidad", justify="right")
        t.add_column("%", justify="right")
        t.add_column("Potencial USD", justify="right")
        for lo, hi in BUCKETS:
            rows = [r for r in opps if lo <= float(r["duracion_ms"]) < hi]
            label = f"≥ {lo} ms" if hi == math.inf else f"{lo}–{hi} ms"
            t.add_row(label, str(len(rows)), f"{len(rows) / len(opps) * 100:.1f}",
                      f"{sum(float(r['pico_usd']) for r in rows):.4f}")
        console.print(t)

        by_route: dict[str, list[float]] = defaultdict(list)
        for r in opps:
            by_route[r["ruta"]].append(float(r["pico_usd"]))
        t = Table(title="Rutas con más potencial", box=box.SIMPLE_HEAVY)
        t.add_column("Ruta")
        t.add_column("Veces", justify="right")
        t.add_column("Potencial USD", justify="right")
        for route, values in sorted(by_route.items(), key=lambda kv: -sum(kv[1]))[:10]:
            t.add_row(route, str(len(values)), f"{sum(values):.4f}")
        console.print(t)

    execs = _read(base / "ejecuciones.csv")
    if execs:
        by_mode: dict[str, list[dict]] = defaultdict(list)
        for r in execs:
            by_mode[r["modo"]].append(r)
        t = Table(title="Ejecuciones", box=box.SIMPLE_HEAVY)
        for col in ("Modo", "Intentos", "Completas", "Esperado USD", "PnL real USD"):
            t.add_column(col, justify="right" if col != "Modo" else "left")
        for mode, rows in by_mode.items():
            ok = sum(1 for r in rows if r["resultado"] == "completa")
            t.add_row(mode, str(len(rows)), str(ok), f"{sum(float(r['esperado_usd']) for r in rows):.4f}",
                      f"{sum(float(r['pnl_usd']) for r in rows):+.4f}")
        console.print(t)
