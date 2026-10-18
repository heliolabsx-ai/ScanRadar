import pandas as pd
from core.rules import evaluate, _resolve


def bars(closes):
    return pd.DataFrame({"ts": pd.date_range("2026-10-19 09:15", periods=len(closes), freq="5min", tz="Asia/Kolkata"),
                         "open": closes, "high": closes, "low": closes, "close": closes, "volume": 1, "oi": 1, "vwap": closes})


def test_sma_is_over_the_bars_ending_at_the_offset():
    b = bars([1, 2, 3, 4, 5, 6])
    assert _resolve({"off": 0, "field": "close", "sma": 3}, b, {}) == 5.0          # 4,5,6
    assert _resolve({"off": 1, "field": "close", "sma": 3}, b, {}) == 4.0          # 3,4,5
    assert _resolve({"off": 0, "field": "close", "sma": 6}, b, {}) == 3.5


def test_too_few_bars_for_the_average_fails_closed():
    scan = {"rules": [{"lhs": {"off": 0, "field": "close"}, "op": ">", "rhs": {"off": 0, "field": "close", "sma": 13}}]}
    assert evaluate(scan, bars([1] * 12), {}) is False
    assert evaluate(scan, bars([1] * 12 + [5]), {}) is True


def test_ema_matches_pandas_recursive_definition():
    b = bars([10, 11, 12, 13, 14])
    e = _resolve({"off": 0, "field": "close", "ema": 3}, b, {})
    assert abs(e - b.close.ewm(span=3, adjust=False).mean().iloc[-1]) < 1e-9


def test_decline_below_sma13_scan_translation():
    import json
    scan = json.load(open("scans/decline_below_sma13_short.json"))
    closes = [2100] * 13 + [2090, 2080, 2070, 2065]      # three falling closes, last closed bar under the 13-SMA, price > 2000
    assert evaluate(scan, bars(closes), {}) is True
    closes_rising = [2100] * 13 + [2090, 2095, 2098, 2099]
    assert evaluate(scan, bars(closes_rising), {}) is False
