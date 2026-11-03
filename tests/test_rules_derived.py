"""Derived candle fields (range, body, upper_wick, lower_wick) and the pin-bar scan translations."""
import json
import pandas as pd
from core.rules import evaluate, _resolve, validate_scan, trade_levels


def bars(rows):
    """rows = [(open, high, low, close), ...] as 15-minute bars from 09:15."""
    o, h, l, c = zip(*rows)
    return pd.DataFrame({"ts": pd.date_range("2026-11-03 09:15", periods=len(rows), freq="15min", tz="Asia/Kolkata"),
                         "open": o, "high": h, "low": l, "close": c, "volume": 1, "oi": 1, "vwap": c})


def test_derived_fields_of_the_offset_bar():
    b = bars([(100, 110, 95, 104), (104, 112, 100, 101)])
    assert _resolve({"off": 0, "field": "range"}, b, {}) == 12.0
    assert _resolve({"off": 0, "field": "body"}, b, {}) == 3.0            # |101 - 104|
    assert _resolve({"off": 0, "field": "upper_wick"}, b, {}) == 8.0      # 112 - max(104, 101)
    assert _resolve({"off": 0, "field": "lower_wick"}, b, {}) == 1.0      # min(104, 101) - 100
    assert _resolve({"off": 1, "field": "body"}, b, {}) == 4.0
    assert _resolve({"off": 0, "field": "range", "sma": 2}, b, {}) == 13.5


def test_unknown_field_fails_closed():
    scan = {"rules": [{"lhs": {"off": 0, "field": "wick"}, "op": ">", "rhs": {"number": 0}}]}
    assert evaluate(scan, bars([(1, 2, 0, 1.5)]), {}) is False


def _scan(path):
    with open(path) as f:
        s = json.load(f)
    validate_scan(s)
    return s


def test_pin_day_high_fires_on_a_green_or_red_bar_with_no_lower_wick_at_the_day_high():
    s = _scan("scans/pin_day_high_short.json")
    green = bars([(100, 101, 99, 100.5), (100.5, 110, 100.5, 102)])      # open = low, body 1.5, range 9.5
    red = bars([(100, 101, 99, 100.5), (102, 110, 100.5, 100.5)])        # close = low, body 1.5
    assert evaluate(s, green, {}) is True
    assert evaluate(s, red, {}) is True
    assert trade_levels(s, green, {}, entry=102)["stop"] == 110


def test_pin_day_high_needs_the_day_high_a_small_body_and_no_lower_wick():
    s = _scan("scans/pin_day_high_short.json")
    assert evaluate(s, bars([(100, 115, 99, 100.5), (100.5, 110, 100.5, 102)]), {}) is False   # an earlier bar is the day high
    assert evaluate(s, bars([(100, 101, 99, 100.5), (100.5, 110, 100.5, 108)]), {}) is False   # body 7.5 of range 9.5
    assert evaluate(s, bars([(100, 101, 99, 100.5), (101, 110, 100.5, 102)]), {}) is False     # lower wick 0.5
    assert evaluate(s, bars([(100, 101, 99, 100.5), (100.5, 110, 100.5, 100.5)]), {}) is False  # doji: Chartink's green/red both fail


def test_pin_day_low_is_the_mirror():
    s = _scan("scans/pin_day_low_long.json")
    green = bars([(100, 101, 99, 100.5), (97, 99, 90, 99)])              # close = high, body 2, range 9
    red = bars([(100, 101, 99, 100.5), (99, 99, 90, 97.5)])              # open = high, body 1.5
    assert evaluate(s, green, {}) is True
    assert evaluate(s, red, {}) is True
    assert evaluate(s, bars([(100, 101, 85, 100.5), (97, 99, 90, 99)]), {}) is False   # not the day low
    assert evaluate(s, bars([(100, 101, 99, 100.5), (97, 99.5, 90, 99)]), {}) is False  # upper wick 0.5
    assert trade_levels(s, green, {}, entry=99)["stop"] == 90
