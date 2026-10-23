import pytest

from conftest import FEE
from triarb.engine import simulate
from triarb.executor import Executor
from triarb.models import BUY
from triarb.pricing import UsdPricer
from triarb.risk import Risk
from triarb.venues import PaperVenue


class MovingMarket(PaperVenue):
    """Simula que, justo antes de la orden número `at`, otro bot se lleva la liquidez de ETHBTC."""

    def __init__(self, *args, at: int, **kwargs):
        super().__init__(*args, **kwargs)
        self.at = at
        self.calls = 0

    async def fok(self, leg, qty, limit):
        self.calls += 1
        if self.calls == self.at:
            self.books["ETHBTC"].asks = [(0.06, 10.0)]
        return await super().fok(leg, qty, limit)


def make_executor(venue, symbols, books, strategy="lineal", on_fail="deshacer"):
    return Executor(venue, Risk(0, 0, 0), UsdPricer(books), symbols, strategy, on_fail, 0)


async def test_paper_fok_fills_and_charges_fee(forward, books):
    venue = PaperVenue(books, 0, {"USDT": 1000}, lambda s: FEE)
    leg = forward.legs[0]
    fill = await venue.fok(leg, 0.002, 50000.0)
    assert fill.ok and fill.quote == pytest.approx(100.0)
    assert venue.balances["BTC"] == pytest.approx(0.002 * (1 - FEE))
    assert venue.balances["USDT"] == pytest.approx(900.0)


async def test_paper_fok_expires_when_price_moves(forward, books):
    venue = PaperVenue(books, 0, {"USDT": 1000}, lambda s: FEE)
    books["BTCUSDT"].asks = [(50010.0, 1.0)]
    fill = await venue.fok(forward.legs[0], 0.002, 50000.0)
    assert not fill.ok and fill.status == "EXPIRED"
    assert venue.balances == {"USDT": 1000}


async def test_paper_fok_rejects_without_balance(forward, books):
    venue = PaperVenue(books, 0, {"USDT": 50}, lambda s: FEE)
    fill = await venue.fok(forward.legs[0], 0.002, 50000.0)
    assert fill.status == "REJECTED"


async def test_linear_success(symbols, books, forward):
    venue = PaperVenue(books, 0, {"USDT": 1000}, lambda s: FEE)
    ex = make_executor(venue, symbols, books)
    plan = simulate(forward, 100.0, books)
    res = await ex.execute(plan)
    assert res.success and res.failed_leg is None
    assert res.deltas["USDT"] == pytest.approx(plan.profit, rel=1e-6)
    assert res.pnl_usd > 3
    assert not ex.busy


async def test_linear_failure_unwinds_to_start(symbols, books, forward):
    venue = MovingMarket(books, 0, {"USDT": 1000}, lambda s: FEE, at=2)
    ex = make_executor(venue, symbols, books)
    res = await ex.execute(simulate(forward, 100.0, books))
    assert not res.success and res.failed_leg == 1
    assert len(res.unwind) == 1 and res.unwind[0].symbol == "BTCUSDT"
    assert venue.balances["BTC"] < 0.00001  # solo polvo
    assert -1 < res.pnl_usd < 0  # se pierde el diferencial y las comisiones, no más


async def test_linear_failure_hold_keeps_asset(symbols, books, forward):
    venue = MovingMarket(books, 0, {"USDT": 1000}, lambda s: FEE, at=2)
    ex = make_executor(venue, symbols, books, on_fail="mantener")
    res = await ex.execute(simulate(forward, 100.0, books))
    assert res.unwind == []
    assert venue.balances["BTC"] == pytest.approx(0.001998)


async def test_parallel_failure_reverts_filled_legs(symbols, books, forward):
    venue = MovingMarket(books, 0, {}, lambda s: FEE, at=1, unlimited=True)
    ex = make_executor(venue, symbols, books, strategy="paralela")
    res = await ex.execute(simulate(forward, 100.0, books))
    assert not res.success
    assert [f.ok for f in res.fills] == [True, False, True]
    assert {(f.symbol, f.side) for f in res.unwind} == {("BTCUSDT", "SELL"), ("ETHUSDT", BUY)}


async def test_ready_checks_cooldown_and_balance(symbols, books, forward):
    venue = PaperVenue(books, 0, {"USDT": 50}, lambda s: FEE)
    ex = Executor(venue, Risk(0, 0, 0), UsdPricer(books), symbols, "lineal", "deshacer", 10_000)
    plan = simulate(forward, 100.0, books)
    assert not ex.ready(plan, 0.0)  # 50 USDT no alcanzan para 100
    venue.balances["USDT"] = 1000
    assert ex.ready(plan, 0.0)
    ex.last_attempt[forward.id] = 0.0
    assert not ex.ready(plan, 5.0)  # enfriamiento de 10 s


def test_risk_limits():
    r = Risk(max_executions=2, per_minute=0, max_daily_loss_usd=5)
    assert r.allows()
    r.record(-1, False)
    assert r.allows()
    r.record(-4.5, False)
    assert not r.allows() and "pérdida diaria" in r.stopped_reason

    capped = Risk(max_executions=1, per_minute=0, max_daily_loss_usd=0)
    capped.record(0.1, True)
    assert not capped.allows() and "límite" in capped.status()
