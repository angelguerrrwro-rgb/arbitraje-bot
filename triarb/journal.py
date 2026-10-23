"""Registro en CSV de oportunidades y ejecuciones (la recogida de datos de Roibal, mejorada).

Una oportunidad no se apunta en cada actualización sino como un "episodio": desde que la
ruta se vuelve rentable hasta que deja de serlo. Así sabemos cuánto dura cada una, que es
lo que decide si con tu latencia se puede capturar o no.
"""

from __future__ import annotations

import csv
import json
import statistics
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path as FsPath

from .models import Path, Plan

OPP_HEADER = ["inicio", "ruta", "activo", "duracion_ms", "pico_pct", "pico_usd", "tamano", "actualizaciones", "ejecutada"]
EXEC_HEADER = [
    "fecha", "modo", "estrategia", "ruta", "esperado_pct", "esperado_usd", "resultado",
    "tramo_fallido", "pnl_usd", "deltas", "duracion_ms", "detalle",
]


@dataclass(slots=True, eq=False)
class Episode:
    path: Path
    started_wall: str
    started: float
    peak_pct: float
    peak_usd: float
    size: float
    updates: int = 0
    executed: bool = False


class _CsvFile:
    def __init__(self, path: FsPath, header: list[str]):
        new = not path.exists() or path.stat().st_size == 0
        self._fh = open(path, "a", newline="", encoding="utf-8")
        self._w = csv.writer(self._fh)
        if new:
            self._w.writerow(header)
            self._fh.flush()

    def write(self, row: list) -> None:
        self._w.writerow(row)
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()


class Journal:
    def __init__(self, folder: str, record_opportunities: bool, mode: str, strategy: str):
        self.dir = FsPath(folder)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.mode = mode
        self.strategy = strategy
        self.record = record_opportunities
        self._opps = _CsvFile(self.dir / "oportunidades.csv", OPP_HEADER) if record_opportunities else None
        self._execs = _CsvFile(self.dir / "ejecuciones.csv", EXEC_HEADER)
        self.open: dict[int, Episode] = {}
        self.closed = 0
        self.potential_usd = 0.0
        self.durations: deque[float] = deque(maxlen=100_000)

    def update(self, path: Path, plan: Plan | None, now: float) -> None:
        ep = self.open.get(path.id)
        if plan is None:
            if ep is not None:
                self._close(ep, now)
            return
        usd = plan.profit_usd or 0.0
        if ep is None:
            ep = Episode(path, datetime.now().isoformat(timespec="milliseconds"), now, plan.pct, usd, plan.spent)
            self.open[path.id] = ep
        ep.updates += 1
        if usd > ep.peak_usd or (usd == ep.peak_usd and plan.pct > ep.peak_pct):
            ep.peak_pct, ep.peak_usd, ep.size = plan.pct, usd, plan.spent

    def mark_executed(self, path_id: int) -> None:
        ep = self.open.get(path_id)
        if ep is not None:
            ep.executed = True

    def _close(self, ep: Episode, now: float) -> None:
        del self.open[ep.path.id]
        duration = (now - ep.started) * 1000
        self.closed += 1
        self.potential_usd += ep.peak_usd
        self.durations.append(duration)
        if self._opps:
            self._opps.write([
                ep.started_wall, ep.path.name, ep.path.start, f"{duration:.1f}", f"{ep.peak_pct:.4f}",
                f"{ep.peak_usd:.6f}", f"{ep.size:.8g}", ep.updates, int(ep.executed),
            ])

    def close_all(self) -> None:
        now = time.monotonic()
        for ep in list(self.open.values()):
            self._close(ep, now)

    def execution(self, res) -> None:
        plan = res.plan
        detail = "; ".join(
            f"{f.symbol} {f.side} {f.status}" + (f" ({f.error})" if f.error else "") for f in res.fills + res.unwind
        )
        if res.note:
            detail += f" | {res.note}"
        self._execs.write([
            datetime.now().isoformat(timespec="milliseconds"), self.mode, self.strategy, plan.path.name,
            f"{plan.pct:.4f}", f"{plan.profit_usd or 0:.6f}", "completa" if res.success else "fallida",
            "" if res.failed_leg is None else res.failed_leg + 1, f"{res.pnl_usd:.6f}",
            json.dumps({k: round(v, 10) for k, v in res.deltas.items() if v}), f"{res.duration_ms:.0f}", detail,
        ])

    def duration_stats(self) -> tuple[float, float] | None:
        if not self.durations:
            return None
        data = sorted(self.durations)
        p90 = data[min(len(data) - 1, int(len(data) * 0.9))]
        return statistics.median(data), p90

    def close(self) -> None:
        if self._opps:
            self._opps.close()
        self._execs.close()
