import json
import time

from triarb.market import MarketData


def make(source="libro"):
    return MarketData(None, "wss://x", ["BTCUSDT", "ETHBTC"], source, 5, clock_offset_ms=0.0)


def test_book_ticker_updates_book_and_marks_dirty():
    m = make()
    m._on_text(json.dumps({"stream": "btcusdt@bookTicker",
                           "data": {"u": 1, "s": "BTCUSDT", "b": "50000.1", "B": "0.5", "a": "50000.2", "A": "1.5"}}))
    assert m.books["BTCUSDT"].bids == [(50000.1, 0.5)] and m.books["BTCUSDT"].asks == [(50000.2, 1.5)]
    assert m.take_dirty() == {"BTCUSDT"} and m.msg_count == 1


def test_empty_side_drops_book():
    m = make()
    m._on_text(json.dumps({"stream": "btcusdt@bookTicker", "data": {"s": "BTCUSDT", "b": "1", "B": "1", "a": "2", "A": "1"}}))
    m._on_text(json.dumps({"stream": "btcusdt@bookTicker", "data": {"s": "BTCUSDT", "b": "0", "B": "0", "a": "2", "A": "1"}}))
    assert "BTCUSDT" not in m.books


def test_depth_stream_uses_stream_name():
    m = make("profundidad")
    m._stream("ETHBTC")
    m._on_text(json.dumps({"stream": "ethbtc@depth5@100ms",
                           "data": {"lastUpdateId": 1, "bids": [["0.05", "2"], ["0.049", "3"]], "asks": [["0.051", "1"]]}}))
    assert m.books["ETHBTC"].bids == [(0.05, 2.0), (0.049, 3.0)]


def test_trade_stream_measures_lag_without_touching_books():
    m = make()
    event_ms = time.time() * 1000 - 250
    m._on_text(json.dumps({"stream": "btcusdt@trade", "data": {"e": "trade", "E": event_ms, "s": "BTCUSDT"}}))
    assert 240 < m.lag_ms < 400
    assert m.msg_count == 0 and not m.books


def test_subscription_ack_is_ignored():
    m = make()
    m._on_text('{"result":null,"id":1}')
    assert m.msg_count == 0
