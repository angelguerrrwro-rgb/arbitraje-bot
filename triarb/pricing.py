"""Valor en USD de cualquier activo, para comparar beneficios de rutas distintas (idea de harjus)."""

from __future__ import annotations

from .models import Book

STABLES = {"USDT", "USDC", "FDUSD", "TUSD", "USDP", "DAI", "BUSD", "USD1", "USDE"}
_DIRECT = ("USDT", "USDC", "FDUSD")
_BRIDGES = ("BTC", "ETH", "BNB")


class UsdPricer:
    def __init__(self, books: dict[str, Book], snapshot: dict[str, float] | None = None):
        self.books = books
        self.snapshot = snapshot or {}

    def _price(self, symbol: str) -> float | None:
        book = self.books.get(symbol)
        if book is not None:
            return (book.bids[0][0] + book.asks[0][0]) / 2
        return self.snapshot.get(symbol)

    def usd(self, asset: str) -> float | None:
        if asset in STABLES:
            return 1.0
        for q in _DIRECT:
            p = self._price(asset + q)
            if p:
                return p
        for q in _DIRECT:
            p = self._price(q + asset)  # p. ej. USDTTRY → 1 TRY = 1/p USD
            if p:
                return 1.0 / p
        for bridge in _BRIDGES:
            if bridge == asset:
                continue
            p = self._price(asset + bridge)
            if p:
                u = self.usd(bridge)
                if u:
                    return p * u
        return None

    def value(self, deltas: dict[str, float]) -> float:
        total = 0.0
        for asset, amount in deltas.items():
            if amount:
                total += amount * (self.usd(asset) or 0.0)
        return total
