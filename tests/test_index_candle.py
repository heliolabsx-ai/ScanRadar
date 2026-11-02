import datetime as dt
from candles import Candle, IndexState, split_ticks, INDEX_TOKEN, INDEX_SYMBOL

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def at(hh, mm, ss=0):
    return dt.datetime(2026, 11, 3, hh, mm, ss, tzinfo=IST)


def test_constants():
    assert INDEX_TOKEN == 256265 and INDEX_SYMBOL == "NIFTY50"


def test_split_ticks_separates_the_index():
    ticks = [{"instrument_token": 1, "last_price": 10.0},
             {"instrument_token": 256265, "last_price": 24000.0},
             {"instrument_token": 2, "last_price": 11.0}]
    stock, idx = split_ticks(ticks)
    assert [t["instrument_token"] for t in stock] == [1, 2]
    assert [t["instrument_token"] for t in idx] == [256265]
    assert split_ticks([]) == ([], [])


def test_index_state_builds_ohlc_per_minute():
    s = IndexState()
    for p, t in [(100, at(9, 20, 10)), (101, at(9, 20, 20)), (99, at(9, 20, 30)), (102, at(9, 21, 5))]:
        s.on_tick({"last_price": p}, t)
    assert s.pop_closed() == [Candle(ts=at(9, 20), open=100, high=101, low=99, close=99, volume=0, oi=0, delta=0)]
    assert s.cur.ts == at(9, 21) and s.cur.open == 102 and s.last_price == 102
    assert s.pop_closed() == []


def test_index_state_ignores_out_of_session():
    s = IndexState()
    s.on_tick({"last_price": 100.0}, at(9, 10))
    assert s.cur is None and s.last_price == 100.0
    s.on_tick({"last_price": 105.0}, at(15, 31))
    assert s.cur is None and s.last_price == 105.0 and s.pop_closed() == []


def test_session_boundaries():
    s = IndexState()
    s.on_tick({"last_price": 1.0}, at(9, 15))
    assert s.cur is not None
    s.on_tick({"last_price": 2.0}, at(15, 30))
    assert s.cur.close == 1.0 and s.last_price == 2.0


def test_finalize_moves_the_forming_candle():
    s = IndexState()
    s.on_tick({"last_price": 100.0}, at(15, 29, 30))
    s.finalize()
    assert s.cur is None
    assert [c.ts for c in s.pop_closed()] == [at(15, 29)]


def test_last_tick_at_and_malformed_tick():
    s = IndexState()
    s.on_tick({"last_price": 100.0}, at(9, 20, 10))
    s.on_tick({}, at(9, 20, 40))                      # no last_price: ignored, not a crash
    s.on_tick({"last_price": None}, at(9, 20, 50))
    assert s.last_price == 100.0 and s.last_tick_at == at(9, 20, 10)
    assert s.cur.close == 100.0
