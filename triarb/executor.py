"""Ejecución de una oportunidad.

- Órdenes LIMIT + FOK (harjus): o se llenan enteras al precio calculado o caducan sin coste.
  Nunca órdenes a mercado para entrar (lo que hacen bmino, Hephyrius y Cherecho), porque
  con 150-300 ms de latencia el precio ya se ha movido.
- "lineal" (bmino): un tramo tras otro, recalculando la cantidad del siguiente con lo que
  realmente se recibió. Solo necesitas saldo del activo inicial.
- "paralela" (bmino, harjus): los tramos a la vez. Más rápida, pero necesitas saldo de los
  tres activos y si un tramo falla te quedas descompensado.
- Si un tramo falla, "deshacer" (t0mm4rx/Cherecho) vuelve al activo inicial a mercado;
  "mantener" se queda con lo que tenga.
- Un solo ciclo a la vez, con enfriamiento por ruta y límites de riesgo (bmino, harjus).
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from .engine import floor_step
from .models import BUY, SELL, Fill, Plan, SymbolInfo
from .risk import Risk

log = logging.getLogger("triarb.ejecucion")

HALT_STATUSES = ("DESCONOCIDO", "LIMITE")


@dataclass(slots=True, eq=False)
class ExecResult:
    plan: Plan
    success: bool
    fills: list[Fill]
    unwind: list[Fill] = field(default_factory=list)
    deltas: dict[str, float] = field(default_factory=dict)
    pnl_usd: float = 0.0
    duration_ms: float = 0.0
    failed_leg: int | None = None
    note: str = ""


def _merge(into: dict[str, float], deltas: dict[str, float]) -> None:
    for asset, amount in deltas.items():
        into[asset] = into.get(asset, 0.0) + amount


class Executor:
    def __init__(
        self,
        venue,
        risk: Risk,
        pricer,
        symbols: dict[str, SymbolInfo],
        strategy: str,
        on_fail: str,
        cooldown_ms: float,
        on_result=None,
    ):
        self.venue = venue
        self.risk = risk
        self.pricer = pricer
        self.symbols = symbols
        self.strategy = strategy
        self.on_fail = on_fail
        self.cooldown_s = cooldown_ms / 1000.0
        self.on_result = on_result
        self.busy = False
        self.last_attempt: dict[int, float] = {}
        self.results: list[ExecResult] = []
        self._tasks: set[asyncio.Task] = set()

    def needs(self, plan: Plan) -> dict[str, float]:
        """Saldo que hace falta en cada activo para lanzar el plan."""
        legs = plan.legs if self.strategy == "paralela" else plan.legs[:1]
        need: dict[str, float] = {}
        for lp in legs:
            amount = lp.qty * lp.limit if lp.leg.side == BUY else lp.qty
            need[lp.leg.asset_in] = need.get(lp.leg.asset_in, 0.0) + amount
        return need

    def ready(self, plan: Plan, now: float) -> bool:
        if self.busy or not self.risk.allows():
            return False
        last = self.last_attempt.get(plan.path.id)
        if last is not None and now - last < self.cooldown_s:
            return False
        return all(self.venue.free(a) >= amt for a, amt in self.needs(plan).items())

    def submit(self, plan: Plan) -> None:
        self.busy = True
        self.last_attempt[plan.path.id] = time.monotonic()
        task = asyncio.create_task(self.execute(plan))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def wait_idle(self, timeout: float) -> None:
        if self._tasks:
            await asyncio.wait(self._tasks, timeout=timeout)

    async def execute(self, plan: Plan) -> ExecResult:
        self.busy = True
        t0 = time.monotonic()
        result = ExecResult(plan, False, [])
        try:
            if self.strategy == "paralela":
                await self._parallel(plan, result)
            else:
                await self._linear(plan, result)
        except Exception as e:
            log.exception("Error inesperado ejecutando %s", plan.path.name)
            result.note = f"error inesperado: {e!r}"
            self.risk.stop("error inesperado durante una ejecución; revisa los saldos en Binance")
        finally:
            result.duration_ms = (time.monotonic() - t0) * 1000
            result.pnl_usd = self.pricer.value(result.deltas)
            for f in result.fills + result.unwind:
                if f.status in HALT_STATUSES:
                    self.risk.stop(f"orden {f.symbol} en estado {f.status}: {f.error}")
            self.risk.record(result.pnl_usd, result.success)
            self.results.append(result)
            del self.results[:-50]
            try:
                await self.venue.refresh()
            except Exception as e:
                log.warning("No se pudieron refrescar los saldos: %s", e)
            self.busy = False
            if self.on_result:
                self.on_result(result)
        return result

    async def _linear(self, plan: Plan, res: ExecResult) -> None:
        amount = 0.0
        for k, lp in enumerate(plan.legs):
            leg, sym = lp.leg, lp.leg.symbol
            if k == 0:
                qty = lp.qty
            else:
                # Recalcular con lo recibido de verdad (bmino recalculateTradeLeg)
                qty = floor_step(amount / lp.limit if leg.side == BUY else amount, sym.step)
                if qty <= 0 or qty < sym.min_qty or qty * lp.limit < sym.min_notional:
                    res.fills.append(Fill(False, "OMITIDA", sym.symbol, leg.side, error="cantidad bajo el mínimo del par"))
                    res.failed_leg = k
                    break
            fill = await self.venue.fok(leg, qty, lp.limit)
            res.fills.append(fill)
            _merge(res.deltas, fill.deltas(sym))
            if not fill.ok:
                res.failed_leg = k
                break
            amount = fill.received_net(sym)
        else:
            res.success = True
            return

        if res.failed_leg and self.on_fail == "deshacer" and not self.risk.stopped_reason:
            held = plan.legs[res.failed_leg].leg.asset_in
            fill = await self._convert(held, plan.path.start, amount)
            if fill is None:
                res.note = f"no se pudo deshacer: te quedas con {amount:.8g} {held}"
            else:
                res.unwind.append(fill)
                _merge(res.deltas, fill.deltas(self.symbols[fill.symbol]))
                res.note = f"deshecho: {held} → {plan.path.start} ({fill.status})"

    async def _parallel(self, plan: Plan, res: ExecResult) -> None:
        fills = await asyncio.gather(*(self.venue.fok(lp.leg, lp.qty, lp.limit) for lp in plan.legs))
        res.fills.extend(fills)
        for lp, fill in zip(plan.legs, fills):
            _merge(res.deltas, fill.deltas(lp.leg.symbol))
        failed = [k for k, f in enumerate(fills) if not f.ok]
        if not failed:
            res.success = True
            return
        res.failed_leg = failed[0]
        if self.on_fail != "deshacer" or self.risk.stopped_reason:
            return
        # Revertir cada tramo que sí se llenó, en el mismo par
        for lp, fill in zip(plan.legs, fills):
            if not fill.ok:
                continue
            sym = lp.leg.symbol
            got = fill.received_net(sym)
            if lp.leg.side == BUY:
                qty = floor_step(got, sym.market_step)
                if qty <= 0 or qty < sym.market_min_qty:
                    continue
                undo = await self.venue.market(sym, SELL, qty=qty)
            else:
                undo = await self.venue.market(sym, BUY, quote_qty=got)
            res.unwind.append(undo)
            _merge(res.deltas, undo.deltas(sym))
        res.note = f"revertidos {len(res.unwind)} tramos"

    async def _convert(self, asset: str, target: str, amount: float) -> Fill | None:
        """Convierte `amount` de `asset` a `target` a mercado por el par directo."""
        sym = self.symbols.get(asset + target)
        if sym is not None:
            qty = floor_step(amount, sym.market_step)
            if qty <= 0 or qty < sym.market_min_qty:
                return None
            return await self.venue.market(sym, SELL, qty=qty)
        sym = self.symbols.get(target + asset)
        if sym is not None and sym.quote_market_allowed:
            return await self.venue.market(sym, BUY, quote_qty=amount)
        return None
