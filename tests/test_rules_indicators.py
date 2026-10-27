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


def test_mul_scales_any_side():
    b = bars([1, 2, 3, 4, 5, 6])
    assert _resolve({"off": 0, "field": "close", "mul": 3}, b, {}) == 18.0
    assert _resolve({"off": 1, "field": "close", "sma": 3, "mul": 2}, b, {}) == 8.0
    assert _resolve({"number": 2, "mul": 1.5}, b, {}) == 3.0


def test_day_low_and_high_come_from_the_bars_seen_so_far():
    b = bars([5, 3, 4, 7, 6])
    assert _resolve({"series": "day_low"}, b, {}) == 3.0
    assert _resolve({"series": "day_high"}, b, {}) == 7.0
    assert _resolve({"series": "prev_day_close"}, b, {"prev_day_close": 9}) == 9.0     # ctx series untouched


def test_b0101_scan_translation():
    import json
    scan = json.load(open("scans/B0101.json"))
    n = 21
    o = [2100.0] * n; c = [2100.0] * n; h = [2101.0] * n; lo = [2099.0] * n; v = [100] * n
    # bar -1: red bar; bar 0: green bar closing above bar -1's high, at the day's low, on 4x the average volume
    o[-2], c[-2], h[-2], lo[-2], v[-2] = 2095.0, 2090.0, 2096.0, 2089.0, 150
    o[-1], c[-1], h[-1], lo[-1], v[-1] = 2088.0, 2097.0, 2098.0, 2087.0, 450
    df = pd.DataFrame({"ts": pd.date_range("2026-10-27 09:15", periods=n, freq="5min", tz="Asia/Kolkata"),
                       "open": o, "high": h, "low": lo, "close": c, "volume": v, "oi": 1, "vwap": c})
    assert evaluate(scan, df, {}) is True
    df2 = df.copy(); df2.loc[3, "low"] = 2080.0           # the day's low was printed earlier → not at the low
    assert evaluate(scan, df2, {}) is False
    df3 = df.copy(); df3.loc[n - 1, "volume"] = 250       # under 3x the 20-bar average volume
    assert evaluate(scan, df3, {}) is False
    df4 = df.copy(); df4.loc[n - 1, "close"] = 1990.0     # under the price floor
    assert evaluate(scan, df4, {}) is False
