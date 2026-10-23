"""Cálculo de rentabilidad de una ruta.

Dos pasos, de más barato a más caro:
1. quick_rate: producto de los mejores precios con comisiones (cota superior, 3 multiplicaciones).
   La inmensa mayoría de rutas se descartan aquí.
2. best_plan: recorre los niveles del libro (idea de bmino) con redondeo a stepSize y
   minNotional (idea de harjus), prueba varios tamaños y se queda con el de mayor beneficio
   absoluto. Lo que no se puede convertir por falta de profundidad o por redondeo vale 0,
   así que el resultado es conservador.
"""

from __future__ import annotations

import math
from decimal import ROUND_FLOOR, ROUND_HALF_EVEN, Decimal

from .models import BUY, Book, LegPlan, Path, Plan, SymbolInfo

_EPS = Decimal("1e-9")


def floor_step(x: float, step: float) -> float:
    if step <= 0:
        return x
    return math.floor(x / step + 1e-9) * step


def fmt_step(x: float, step_str: str) -> str:
    """Cantidad como texto, redondeada hacia abajo al múltiplo de step (para la API)."""
    d = Decimal(repr(x))
    step = Decimal(step_str)
    if step > 0:
        d = (d / step + _EPS).to_integral_value(rounding=ROUND_FLOOR) * step
    return format(d.normalize(), "f")


def fmt_tick(x: float, tick_str: str) -> str:
    """Precio como texto, redondeado al tick más cercano."""
    d = Decimal(repr(x))
    tick = Decimal(tick_str)
    if tick > 0:
        d = (d / tick).to_integral_value(rounding=ROUND_HALF_EVEN) * tick
    return format(d.normalize(), "f")


def quick_rate(path: Path, books: dict[str, Book]) -> float | None:
    """Multiplicador del ciclo usando solo el mejor precio. >1 significa beneficio."""
    rate = path.fee_mult
    for leg in path.legs:
        book = books.get(leg.symbol.symbol)
        if book is None:
            return None
        if leg.side == BUY:
            rate /= book.asks[0][0]
        else:
            rate *= book.bids[0][0]
    return rate


def plan_buy(asks: list[tuple[float, float]], budget: float, sym: SymbolInfo):
    """Compra con `budget` de quote. Devuelve (cantidad, coste, precio límite) o None.

    Una orden LIMIT de compra bloquea cantidad × precio límite, así que la cantidad
    tiene que caber en el presupuesto al peor precio que se toque.
    """
    best_q = 0.0
    best_i = -1
    cum = 0.0
    for i, (price, qty) in enumerate(asks):
        cum += qty
        afford = budget / price
        if cum >= afford:
            if afford > best_q:
                best_q, best_i = afford, i
            break
        if cum > best_q:
            best_q, best_i = cum, i
    if best_i < 0:
        return None
    limit = asks[best_i][0]
    qty = floor_step(best_q, sym.step)
    if qty <= 0 or qty < sym.min_qty or qty * limit < sym.min_notional:
        return None
    cost = 0.0
    rem = qty
    for price, level_qty in asks[: best_i + 1]:
        take = level_qty if level_qty < rem else rem
        cost += take * price
        rem -= take
        if rem <= 0:
            break
    return qty, cost, limit


def plan_sell(bids: list[tuple[float, float]], amount: float, sym: SymbolInfo):
    """Vende `amount` de base. Devuelve (cantidad, ingreso, precio límite) o None."""
    total = 0.0
    for _, q in bids:
        total += q
    qty = floor_step(min(amount, total), sym.step)
    if qty <= 0 or qty < sym.min_qty:
        return None
    revenue = 0.0
    rem = qty
    limit = bids[0][0]
    for price, level_qty in bids:
        take = level_qty if level_qty < rem else rem
        revenue += take * price
        rem -= take
        limit = price
        if rem <= 0:
            break
    if qty * limit < sym.min_notional:
        return None
    return qty, revenue, limit


def simulate(path: Path, amount: float, books: dict[str, Book]) -> Plan | None:
    """Ejecuta la ruta sobre el libro local con `amount` del activo inicial."""
    amt = amount
    legs: list[LegPlan] = []
    spent = 0.0
    fill = 1.0
    for k, leg in enumerate(path.legs):
        book = books.get(leg.symbol.symbol)
        if book is None or amt <= 0:
            return None
        if leg.side == BUY:
            r = plan_buy(book.asks, amt, leg.symbol)
            if r is None:
                return None
            qty, cost, limit = r
            used, out = cost, qty * (1.0 - leg.fee)
        else:
            r = plan_sell(book.bids, amt, leg.symbol)
            if r is None:
                return None
            qty, revenue, limit = r
            used, out = qty, revenue * (1.0 - leg.fee)
        if k == 0:
            spent = used
        fill *= used / amt
        legs.append(LegPlan(leg, qty, limit, used, out))
        amt = out
    if spent <= 0:
        return None
    profit = amt - spent
    return Plan(path, legs, spent, amt, profit, profit / spent * 100.0, fill)


def sizes_between(lo: float, hi: float, n: int) -> list[float]:
    if n <= 1 or hi <= lo:
        return [hi]
    ratio = (hi / lo) ** (1.0 / (n - 1))
    return [lo * ratio**i for i in range(n)]


def best_plan(path: Path, books: dict[str, Book], lo: float, hi: float, n: int = 6) -> Plan | None:
    """Prueba tamaños entre lo y hi (más la capacidad real del libro) y devuelve el mejor."""
    if hi < lo or hi <= 0:
        return None
    best = simulate(path, hi, books)
    sizes = sizes_between(lo, hi, n)[:-1]
    if best is not None and best.fill_ratio < 0.995:
        cap = hi * best.fill_ratio * 0.995
        if lo <= cap < hi:
            sizes.append(cap)
    for size in sizes:
        plan = simulate(path, size, books)
        if plan is not None and (best is None or plan.profit > best.profit):
            best = plan
    return best
