import pytest

from triarb.engine import best_plan, fmt_step, fmt_tick, plan_buy, plan_sell, quick_rate, simulate
from triarb.models import BUY, SELL, SymbolInfo
from triarb.paths import build_index, build_paths


def test_fmt_step_rounds_down_and_strips_zeros():
    assert fmt_step(0.30000000000000004, "0.1") == "0.3"
    assert fmt_step(0.29999, "0.1") == "0.2"
    assert fmt_step(12.0, "1") == "12"
    assert fmt_step(0.00049, "0.00001") == "0.00049"
    assert fmt_tick(25.3519, "0.0001") == "25.3519"
    assert fmt_tick(0.0499, "0.00001") == "0.0499"


def test_paths_both_directions(symbols, paths):
    assert sorted(p.name for p in paths) == ["USDT → BTC → ETH → USDT", "USDT → ETH → BTC → USDT"]
    fwd = next(p for p in paths if p.name == "USDT → BTC → ETH → USDT")
    assert [leg.side for leg in fwd.legs] == [BUY, BUY, SELL]
    index = build_index(paths)
    assert all(len(index[s]) == 2 for s in symbols)


def test_paths_respect_asset_whitelist(symbols):
    assert build_paths(symbols, ["USDT"], {"USDT", "BTC"}, 3, lambda s: 0.001) == []


def test_quick_rate(forward, books):
    expected = (1 / 50000) / 0.05 * 2600 * 0.999**3
    assert quick_rate(forward, books) == pytest.approx(expected)


def test_quick_rate_missing_book(forward, books):
    del books["ETHBTC"]
    assert quick_rate(forward, books) is None


def test_simulate_profitable_triangle(forward, books):
    plan = simulate(forward, 100.0, books)
    assert [lp.qty for lp in plan.legs] == pytest.approx([0.002, 0.0399, 0.0398])
    assert plan.spent == pytest.approx(100.0)
    assert plan.received == pytest.approx(0.0398 * 2600 * 0.999)
    assert plan.pct == pytest.approx(3.37652, abs=1e-5)


def test_simulate_below_min_notional(forward, books):
    assert simulate(forward, 2.0, books) is None  # 2 USDT < minNotional de 5


def test_plan_buy_walks_depth():
    sym = SymbolInfo("XY", "X", "Y", step=0.01, step_str="0.01")
    qty, cost, limit = plan_buy([(100.0, 1.0), (101.0, 1.0), (102.0, 5.0)], 250.0, sym)
    assert qty == pytest.approx(2.45)
    assert cost == pytest.approx(100 + 101 + 0.45 * 102)
    assert limit == 102.0
    assert qty * limit <= 250.0  # la orden LIMIT bloquea qty × precio límite


def test_plan_sell_walks_depth():
    sym = SymbolInfo("XY", "X", "Y", step=0.1, step_str="0.1")
    qty, revenue, limit = plan_sell([(100.0, 1.0), (99.0, 1.0)], 1.5, sym)
    assert (qty, limit) == (pytest.approx(1.5), 99.0)
    assert revenue == pytest.approx(149.5)


def test_best_plan_respects_top_of_book_capacity(forward, books):
    books["ETHUSDT"].bids = [(2600.0, 0.01)]  # solo hay 26 USDT de liquidez en el último tramo
    plan = best_plan(forward, books, 10.0, 100.0, 6)
    assert plan is not None and plan.profit > 0
    assert plan.legs[2].qty <= 0.01
    assert plan.spent < 27


def test_best_plan_unprofitable_reverse(paths, books):
    reverse = next(p for p in paths if p.name == "USDT → ETH → BTC → USDT")
    plan = best_plan(reverse, books, 10.0, 100.0, 6)
    assert plan is not None and plan.profit < 0
