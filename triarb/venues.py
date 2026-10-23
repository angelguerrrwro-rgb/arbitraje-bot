"""Dónde se ejecutan las órdenes: simulación en papel o Binance (testnet o real).

Las dos exponen la misma interfaz, así el ejecutor es idéntico en simulación y en real.

La simulación tiene en cuenta la latencia: tu orden tarda RTT/2 en llegar a Binance y lo que
pase allí en ese instante lo verás en local con el retraso de los datos encima. Así que una
orden enviada en t se casa contra el libro que verás en t + RTT/2 + retraso (como mínimo t +
RTT, que es lo que tarda la respuesta). Si en ese momento ya no queda liquidez a tu precio, la
orden FOK caduca, igual que en Binance. Sin esto la simulación miente: harjus marcaba 60 000 $
de beneficio en testnet y 0,13 $ en real.
"""

from __future__ import annotations

import asyncio
import math
from typing import Callable

from .binance import BinanceError, BinanceRest
from .engine import fmt_step, fmt_tick
from .models import BUY, SELL, Book, Fill, Leg, SymbolInfo


def walk(levels: list[tuple[float, float]], qty: float) -> tuple[float, float]:
    """Recorre niveles hasta cubrir qty. Devuelve (cantidad cubierta, importe en quote)."""
    filled = quote = 0.0
    for price, level_qty in levels:
        take = min(level_qty, qty - filled)
        filled += take
        quote += take * price
        if filled >= qty:
            break
    return filled, quote


def walk_quote(levels: list[tuple[float, float]], quote_amount: float) -> tuple[float, float]:
    """Compra gastando quote_amount. Devuelve (base comprado, quote gastado)."""
    base = spent = 0.0
    for price, level_qty in levels:
        take = min(level_qty, (quote_amount - spent) / price)
        base += take
        spent += take * price
        if spent >= quote_amount * (1 - 1e-12):
            break
    return base, spent


class PaperVenue:
    name = "papel"

    def __init__(
        self,
        books: dict[str, Book],
        delay_s: float | Callable[[], float],
        balances: dict[str, float],
        fee_for: Callable[[str], float],
        slippage_pct: float = 0.05,
        unlimited: bool = False,
    ):
        self.books = books
        self.delay_s = delay_s
        self.balances = dict(balances)
        self.fee_for = fee_for
        self.slippage = slippage_pct / 100.0
        # En paralela hace falta inventario de todos los activos: los no configurados se dan por ilimitados
        self.unlimited = unlimited
        self._configured = set(balances)

    def free(self, asset: str) -> float:
        if self.unlimited and asset not in self._configured:
            return math.inf
        return self.balances.get(asset, 0.0)

    async def refresh(self) -> None:
        return None

    async def _wait(self) -> None:
        await asyncio.sleep(self.delay_s() if callable(self.delay_s) else self.delay_s)

    def _apply(self, sym: SymbolInfo, side: str, qty: float, quote: float) -> Fill:
        fee = self.fee_for(sym.symbol)
        if side == BUY:
            commissions = {sym.base: qty * fee}
        else:
            commissions = {sym.quote: quote * fee}
        fill = Fill(True, "FILLED", sym.symbol, side, qty, quote, commissions)
        for asset, delta in fill.deltas(sym).items():
            self.balances[asset] = self.balances.get(asset, 0.0) + delta
        return fill

    async def fok(self, leg: Leg, qty: float, limit: float) -> Fill:
        await self._wait()
        sym = leg.symbol
        need = qty * limit if leg.side == BUY else qty
        if self.free(leg.asset_in) < need * (1 - 1e-9):
            return Fill(False, "REJECTED", sym.symbol, leg.side, error="saldo insuficiente")
        book = self.books.get(sym.symbol)
        if book is None:
            return Fill(False, "EXPIRED", sym.symbol, leg.side, error="sin datos del par")
        if leg.side == BUY:
            levels = [(p, q) for p, q in book.asks if p <= limit]
        else:
            levels = [(p, q) for p, q in book.bids if p >= limit]
        filled, quote = walk(levels, qty)
        if filled < qty * (1 - 1e-9):
            return Fill(False, "EXPIRED", sym.symbol, leg.side, error="ya no hay liquidez a ese precio")
        return self._apply(sym, leg.side, qty, quote)

    async def market(self, sym: SymbolInfo, side: str, qty: float | None = None, quote_qty: float | None = None) -> Fill:
        await self._wait()
        book = self.books.get(sym.symbol)
        if book is None:
            return Fill(False, "EXPIRED", sym.symbol, side, error="sin datos del par")
        if side == SELL:
            filled, quote = walk(book.bids, qty)
            if filled < qty:  # lo que no se ve en el libro, a peor precio
                quote += (qty - filled) * book.bids[-1][0] * (1 - self.slippage)
            return self._apply(sym, side, qty, quote)
        base, spent = walk_quote(book.asks, quote_qty)
        if spent < quote_qty:
            base += (quote_qty - spent) / (book.asks[-1][0] * (1 + self.slippage))
        return self._apply(sym, side, base, quote_qty)


def _parse_order(r: dict, sym: SymbolInfo, side: str) -> Fill:
    commissions: dict[str, float] = {}
    for f in r.get("fills", []):
        asset = f.get("commissionAsset", "")
        commissions[asset] = commissions.get(asset, 0.0) + float(f.get("commission", 0))
    status = r.get("status", "")
    return Fill(
        ok=status == "FILLED",
        status=status,
        symbol=sym.symbol,
        side=side,
        qty=float(r.get("executedQty", 0)),
        quote=float(r.get("cummulativeQuoteQty", 0)),
        commissions=commissions,
    )


class LiveVenue:
    """Órdenes reales en Binance (testnet o producción)."""

    def __init__(self, rest: BinanceRest, name: str):
        self.rest = rest
        self.name = name
        self.balances: dict[str, float] = {}

    def free(self, asset: str) -> float:
        return self.balances.get(asset, 0.0)

    async def refresh(self) -> None:
        self.balances = await self.rest.balances()

    async def _send(self, sym: SymbolInfo, side: str, **params) -> Fill:
        try:
            r = await self.rest.new_order(symbol=sym.symbol, side=side, **params)
        except BinanceError as e:
            # 429/418: Binance nos está limitando o baneando la IP; hay que parar
            status = "LIMITE" if e.status in (418, 429) else "ERROR"
            return Fill(False, status, sym.symbol, side, error=str(e))
        except (asyncio.TimeoutError, OSError) as e:
            # No sabemos si la orden llegó: el ejecutor lo trata como estado desconocido y se detiene
            return Fill(False, "DESCONOCIDO", sym.symbol, side, error=f"sin respuesta: {e!r}")
        return _parse_order(r, sym, side)

    async def fok(self, leg: Leg, qty: float, limit: float) -> Fill:
        sym = leg.symbol
        return await self._send(
            sym,
            leg.side,
            type="LIMIT",
            timeInForce="FOK",
            quantity=fmt_step(qty, sym.step_str),
            price=fmt_tick(limit, sym.tick_str),
        )

    async def market(self, sym: SymbolInfo, side: str, qty: float | None = None, quote_qty: float | None = None) -> Fill:
        if qty is not None:
            return await self._send(sym, side, type="MARKET", quantity=fmt_step(qty, sym.market_step_str))
        step = "1" if sym.quote_precision <= 0 else "0." + "0" * (sym.quote_precision - 1) + "1"
        return await self._send(sym, side, type="MARKET", quoteOrderQty=fmt_step(quote_qty, step))
