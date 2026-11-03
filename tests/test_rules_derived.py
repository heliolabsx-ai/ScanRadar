"""Derived candle fields (range, body, upper_wick, lower_wick) and the pin-bar scan translations."""
import json
import pandas as pd
from core.rules import evaluate, _resolve, validate_scan, trade_levels


def bars(rows, forming=None):
    """rows = [(open, high, low, close), ...] as 15-minute bars from 09:15; the parked pin scans read the last closed bar
    (off 1), so the scan tests append a flat forming bar (inside the pin bar's range, above the price floor) after it."""
    if forming is not None:
        rows = list(rows) + [forming]
    o, h, l, c = zip(*rows)
    return pd.DataFrame({"ts": pd.date_range("2026-11-03 09:15", periods=len(rows), freq="15min", tz="Asia/Kolkata"),
                         "open": o, "high": h, "low": l, "close": c, "volume": 1, "oi": 1, "vwap": c})


FH = (1050, 1050, 1050, 1050)   # the forming bar the parked short scan sees at off 0: above the 1000 floor, inside the pin bar's range
FL = (1950, 1950, 1950, 1950)   # the same for the long scan's bars around 1900-2010


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
    s = _scan("scans/parked/pin_day_high_short.json")
    green = bars([(1000, 1010, 990, 1005), (1005, 1100, 1005, 1020)], FH)   # open = low, body 15, range 95
    red = bars([(1000, 1010, 990, 1005), (1020, 1100, 1005, 1005)], FH)     # close = low, body 15
    assert evaluate(s, green, {}) is True
    assert evaluate(s, red, {}) is True
    assert trade_levels(s, green, {}, entry=1050)["stop"] == 1100


def test_pin_day_high_needs_the_day_high_a_small_body_and_no_lower_wick():
    s = _scan("scans/parked/pin_day_high_short.json")
    assert evaluate(s, bars([(1000, 1150, 990, 1005), (1005, 1100, 1005, 1020)], FH), {}) is False   # an earlier bar is the day high
    assert evaluate(s, bars([(1000, 1010, 990, 1005), (1005, 1100, 1005, 1080)], FH), {}) is False   # body 75 of range 95
    assert evaluate(s, bars([(1000, 1010, 990, 1005), (1010, 1100, 1005, 1020)], FH), {}) is False   # lower wick 5
    assert evaluate(s, bars([(1000, 1010, 990, 1005), (1005, 1100, 1005, 1005)], FH), {}) is False   # doji: Chartink's green/red both fail
    assert evaluate(s, bars([(1000, 1010, 990, 1005), (1005, 1100, 1005, 1020)], (900, 900, 900, 900)), {}) is False  # price floor 1000


def test_pin_day_low_is_the_mirror():
    s = _scan("scans/parked/pin_day_low_long.json")
    green = bars([(2000, 2010, 1990, 2005), (1970, 1990, 1900, 1990)], FL)   # close = high, body 20, range 90
    red = bars([(2000, 2010, 1990, 2005), (1990, 1990, 1900, 1975)], FL)     # open = high, body 15
    assert evaluate(s, green, {}) is True
    assert evaluate(s, red, {}) is True
    assert evaluate(s, bars([(2000, 2010, 1850, 2005), (1970, 1990, 1900, 1990)], FL), {}) is False   # not the day low
    assert evaluate(s, bars([(2000, 2010, 1990, 2005), (1970, 1995, 1900, 1990)], FL), {}) is False   # upper wick 5
    assert trade_levels(s, green, {}, entry=1950)["stop"] == 1900
