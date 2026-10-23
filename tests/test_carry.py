import pytest

from triarb.carry import Book, Candidate, CarryConfig, annual_rate, backtest, decide

DAY = 86_400_000
PAXG = Candidate("PAXG", "PAXGUSDT", "PAXGUSDT")
Q = {"spot_bid": 3999.0, "spot_ask": 4000.0, "perp_bid": 4004.0, "perp_ask": 4005.0, "mark": 4004.5}


def test_annual_rate():
    rows = [(i * DAY // 3, 0.0001) for i in range(30)]  # 0,01 % cada 8 h durante 10 días
    assert annual_rate(rows, 0, 10 * DAY) == pytest.approx(0.0001 * 30 / 10 * 365)
    assert annual_rate(rows[:2], 0, DAY) is None  # muy pocos datos


def test_decide_enters_switches_and_exits():
    assert decide({"PAXG": 0.08, "XAUT": 0.06}, None, 0.04, 0.0, 0.03) == "PAXG"
    assert decide({"PAXG": 0.03}, None, 0.04, 0.0, 0.03) is None  # no llega al mínimo
    # no cambia por poca diferencia (cambiar cuesta comisiones)
    assert decide({"PAXG": 0.06, "XAUT": 0.08}, "PAXG", 0.04, 0.0, 0.03) == "PAXG"
    assert decide({"PAXG": 0.04, "XAUT": 0.08}, "PAXG", 0.04, 0.0, 0.03) == "XAUT"
    # sale si el funding se vuelve negativo y nada supera el mínimo
    assert decide({"PAXG": -0.01, "XAUT": 0.02}, "PAXG", 0.04, 0.0, 0.03) is None


def test_book_open_funding_close():
    b = Book(cash=500.0, initial=500.0)
    p = b.open(PAXG, Q, leverage=2, utilization=1.0, fees=(0.001, 0.0005), now_ms=0)
    assert p.qty * Q["spot_ask"] == pytest.approx(500 / 1.5)
    assert b.equity(Q) == pytest.approx(500 - b.fees_paid + p.qty * (Q["spot_bid"] - Q["spot_ask"])
                                        + p.qty * (Q["perp_bid"] - Q["mark"]), rel=1e-9)
    got = b.credit_funding(0.0001, Q["mark"])
    assert got == pytest.approx(p.qty * Q["mark"] * 0.0001)
    b.close(Q, fees=(0.001, 0.0005))
    assert b.position is None
    # sin moverse el precio, se pierde el diferencial y las comisiones y se gana el funding
    assert b.cash == pytest.approx(500 - b.fees_paid + got + p.qty * (Q["spot_bid"] - Q["spot_ask"])
                                   + p.qty * (Q["perp_bid"] - Q["perp_ask"]))


def test_book_survives_json_roundtrip():
    b = Book(cash=500.0, initial=500.0)
    b.open(PAXG, Q, 2, 0.95, (0.001, 0.0005), 0)
    again = Book.from_json(b.to_json())
    assert again.position.qty == b.position.qty and again.cash == b.cash


def test_liquidation_distance_at_2x():
    b = Book(cash=500.0)
    b.open(PAXG, Q, 2, 1.0, (0, 0), 0)
    assert 0.45 < b.liquidation_move(Q) < 0.52  # a 2x hace falta una subida de ~+49 %


def test_backtest_collects_steady_funding():
    hist = {"PAXG": [(i * DAY // 3, 0.0001) for i in range(3 * 60)]}  # 60 días a ~11 %/año
    r = backtest(hist, CarryConfig(minimum=0.04, window_days=3))
    assert r["switches"] == 1 and r["held"]["PAXG"] > 150
    # 11 %/año × (0,95/1,5) ≈ 6,9 %, menos entrada+salida (0,25 % del nocional) anualizada sobre 57 días ≈ 1 %
    assert 0.055 < r["annual"] < 0.065
