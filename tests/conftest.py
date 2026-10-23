import time

import pytest

from triarb.models import Book, SymbolInfo
from triarb.paths import build_paths

FEE = 0.001


def make_symbols() -> dict[str, SymbolInfo]:
    return {
        "BTCUSDT": SymbolInfo("BTCUSDT", "BTC", "USDT", 0.00001, "0.00001", 0.00001, 0.00001, "0.00001", 0.0, "0.01", 5.0),
        "ETHBTC": SymbolInfo("ETHBTC", "ETH", "BTC", 0.0001, "0.0001", 0.0001, 0.0001, "0.0001", 0.0, "0.00001", 0.0001),
        "ETHUSDT": SymbolInfo("ETHUSDT", "ETH", "USDT", 0.0001, "0.0001", 0.0001, 0.0001, "0.0001", 0.0, "0.01", 5.0),
    }


def make_books() -> dict[str, Book]:
    now = time.monotonic()
    return {
        "BTCUSDT": Book([(49990.0, 1.0)], [(50000.0, 1.0)], now),
        "ETHBTC": Book([(0.0499, 10.0)], [(0.05, 10.0)], now),
        "ETHUSDT": Book([(2600.0, 10.0)], [(2601.0, 10.0)], now),
    }


@pytest.fixture
def symbols():
    return make_symbols()


@pytest.fixture
def books():
    return make_books()


@pytest.fixture
def paths(symbols):
    return build_paths(symbols, ["USDT"], None, 3, lambda s: FEE)


@pytest.fixture
def forward(paths):
    """USDT → BTC → ETH → USDT (rentable con los libros de prueba)."""
    return next(p for p in paths if p.symbols == ("BTCUSDT", "ETHBTC", "ETHUSDT"))
