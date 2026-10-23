"""Límites de seguridad: tope de ejecuciones (bmino), ritmo máximo y pérdida diaria."""

from __future__ import annotations

import time
from collections import deque
from datetime import date


class Risk:
    def __init__(self, max_executions: int, per_minute: int, max_daily_loss_usd: float):
        self.max_executions = max_executions
        self.per_minute = per_minute
        self.max_daily_loss_usd = max_daily_loss_usd
        self.executions = 0
        self.successes = 0
        self.pnl_total_usd = 0.0
        self.pnl_today_usd = 0.0
        self._day = date.today()
        self._recent: deque[float] = deque()
        self.stopped_reason = ""

    def allows(self) -> bool:
        if self.stopped_reason:
            return False
        if self.max_executions and self.executions >= self.max_executions:
            return False
        if self._day != date.today():
            self._day = date.today()
            self.pnl_today_usd = 0.0
        now = time.monotonic()
        while self._recent and now - self._recent[0] > 60:
            self._recent.popleft()
        return not (self.per_minute and len(self._recent) >= self.per_minute)

    def record(self, pnl_usd: float, success: bool) -> None:
        self.executions += 1
        self.successes += int(success)
        self._recent.append(time.monotonic())
        self.pnl_total_usd += pnl_usd
        self.pnl_today_usd += pnl_usd
        if self.max_daily_loss_usd and self.pnl_today_usd <= -self.max_daily_loss_usd:
            self.stop(f"pérdida diaria de {self.pnl_today_usd:.2f} USD supera el límite")

    def stop(self, reason: str) -> None:
        if not self.stopped_reason:
            self.stopped_reason = reason

    def status(self) -> str:
        if self.stopped_reason:
            return f"DETENIDO: {self.stopped_reason}"
        if self.max_executions and self.executions >= self.max_executions:
            return f"límite de {self.max_executions} ejecuciones alcanzado"
        return "activo"
