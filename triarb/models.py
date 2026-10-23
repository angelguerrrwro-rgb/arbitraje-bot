"""Estructuras de datos compartidas."""

from __future__ import annotations

from dataclasses import dataclass, field

BUY = "BUY"
SELL = "SELL"


@dataclass(slots=True, eq=False)
class SymbolInfo:
    """Un par de Binance con los filtros que necesitan las órdenes."""

    symbol: str
    base: str
    quote: str
    step: float = 0.0
    step_str: str = "0"
    min_qty: float = 0.0
    market_step: float = 0.0
    market_step_str: str = "0"
    market_min_qty: float = 0.0
    tick_str: str = "0"
    min_notional: float = 0.0
    quote_precision: int = 8
    quote_market_allowed: bool = True


@dataclass(slots=True, eq=False)
class Leg:
    """Un tramo de la ruta: comprar (gastar quote) o vender (gastar base) en un par."""

    symbol: SymbolInfo
    side: str
    fee: float  # fracción, p. ej. 0.001 = 0,1 %

    @property
    def asset_in(self) -> str:
        return self.symbol.quote if self.side == BUY else self.symbol.base

    @property
    def asset_out(self) -> str:
        return self.symbol.base if self.side == BUY else self.symbol.quote


@dataclass(slots=True, eq=False)
class Path:
    """Ciclo que empieza y termina en el mismo activo (p. ej. USDT → BTC → ETH → USDT)."""

    id: int
    start: str
    legs: tuple[Leg, ...]
    fee_mult: float
    name: str
    symbols: tuple[str, ...]


@dataclass(slots=True, eq=False)
class Book:
    """Libro de órdenes local. bids de mayor a menor, asks de menor a mayor."""

    bids: list[tuple[float, float]]
    asks: list[tuple[float, float]]
    ts: float  # time.monotonic() de la última actualización


@dataclass(slots=True, eq=False)
class LegPlan:
    leg: Leg
    qty: float  # cantidad en el activo base del par
    limit: float  # precio límite (el peor nivel del libro que se toca)
    amount_in: float  # cuánto se gasta del activo de entrada
    amount_out: float  # cuánto se recibe del activo de salida, ya sin comisión


@dataclass(slots=True, eq=False)
class Plan:
    path: Path
    legs: list[LegPlan]
    spent: float  # activo inicial gastado
    received: float  # activo inicial recibido al cerrar el ciclo
    profit: float  # received - spent, en el activo inicial
    pct: float
    fill_ratio: float  # 1.0 si cada tramo pudo convertir todo lo que recibió
    profit_usd: float | None = None
    age_ms: float = 0.0


@dataclass(slots=True, eq=False)
class Fill:
    """Resultado de una orden (real o simulada)."""

    ok: bool
    status: str
    symbol: str
    side: str
    qty: float = 0.0  # base ejecutado
    quote: float = 0.0  # quote ejecutado
    commissions: dict[str, float] = field(default_factory=dict)
    error: str = ""

    def deltas(self, sym: SymbolInfo) -> dict[str, float]:
        d: dict[str, float] = {}
        sign = 1.0 if self.side == BUY else -1.0
        d[sym.base] = d.get(sym.base, 0.0) + sign * self.qty
        d[sym.quote] = d.get(sym.quote, 0.0) - sign * self.quote
        for asset, amount in self.commissions.items():
            d[asset] = d.get(asset, 0.0) - amount
        return d

    def received_net(self, sym: SymbolInfo) -> float:
        if self.side == BUY:
            return self.qty - self.commissions.get(sym.base, 0.0)
        return self.quote - self.commissions.get(sym.quote, 0.0)
