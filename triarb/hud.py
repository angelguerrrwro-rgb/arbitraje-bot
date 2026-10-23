"""Pantalla en vivo con las mejores rutas (el HUD de bmino, con rich)."""

from __future__ import annotations

import heapq
import time

from rich import box
from rich.console import Group
from rich.table import Table
from rich.text import Text

MODE_LABEL = {"escanear": "ESCANEAR", "papel": "PAPEL (simulación)", "testnet": "TESTNET", "real": "REAL ⚠ dinero real"}


def _uptime(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def _pct(value: float) -> Text:
    style = "bold green" if value > 0 else ("yellow" if value > -0.1 else "red")
    return Text(f"{value:+.4f} %", style=style)


def render(app) -> Group:
    cfg = app.cfg
    head = Text()
    head.append(f"{MODE_LABEL.get(cfg.modo, cfg.modo)}", style="bold magenta" if cfg.modo == "real" else "bold cyan")
    lag = app.market.lag_ms
    head.append(
        f"  ·  {cfg.ejecucion}  ·  ping {app.rtt_ms:.0f} ms  ·  datos con {f'{lag:.0f}' if lag is not None else '?'} ms de retraso"
        f"  ·  {len(app.market.books)}/{len(app.market.symbols)} pares"
        f"  ·  {len(app.paths)} rutas  ·  {app.msgs_per_s:.0f} msg/s  ·  CPU cálculo {app.busy_pct:.0f} %"
        f"  ·  {_uptime(time.monotonic() - app.started)}\n"
    )
    j = app.journal
    stats = j.duration_stats()
    head.append(f"Oportunidades: {j.closed + len(j.open)}  ·  potencial {j.potential_usd:.4f} USD")
    if stats:
        head.append(f"  ·  duración mediana {stats[0]:.0f} ms (p90 {stats[1]:.0f} ms)")
    if app.executor:
        r = app.risk
        head.append(
            f"\nEjecuciones: {r.successes} completas / {r.executions} intentos  ·  PnL {r.pnl_total_usd:+.4f} USD"
            f"  ·  {r.status()}"
        )
        if app.blocked_by_lag:
            head.append(f"  ·  frenadas por retraso de datos: {app.blocked_by_lag}", style="yellow")

    table = Table(box=box.SIMPLE_HEAVY, expand=False, title_justify="left")
    table.add_column("Ruta", no_wrap=True)
    table.add_column("Neto al mejor precio", justify="right")
    table.add_column("Beneficio", justify="right")
    table.add_column("Tamaño", justify="right")
    table.add_column("Edad", justify="right")
    best = heapq.nlargest(cfg.pantalla_filas, ((r, i) for i, r in enumerate(app.rates) if r is not None))
    for rate, pid in best:
        path = app.paths[pid]
        plan = app.plans.get(pid)
        if plan is not None:
            usd = f"{plan.profit_usd:+.4f} $" if plan.profit_usd is not None else f"{plan.profit:+.8g} {path.start}"
            size = f"{plan.spent:.6g} {path.start}"
            age = f"{plan.age_ms:.0f} ms"
        else:
            usd = size = age = "—"
        table.add_row(path.name, _pct((rate - 1) * 100), usd, size, age)

    parts = [head, table]
    if app.executor and app.executor.results:
        tail = Text("Últimas ejecuciones:\n", style="bold")
        for res in app.executor.results[-3:]:
            state = "completa" if res.success else f"fallida en tramo {res.failed_leg + 1 if res.failed_leg is not None else '?'}"
            tail.append(
                f"  {res.plan.path.name}  esperado {res.plan.pct:+.4f} %  →  {state}, PnL {res.pnl_usd:+.4f} USD"
                f" en {res.duration_ms:.0f} ms {res.note}\n",
                style="green" if res.success else "red",
            )
        parts.append(tail)
    return Group(*parts)
