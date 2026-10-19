import json, datetime as dt
import pandas as pd
from core.rules import trade_levels, resample
from core.asof import view_at
from conftest import synthetic_day


def bars(closes, highs=None, lows=None):
    highs = highs or [c + 1 for c in closes]; lows = lows or [c - 1 for c in closes]
    return pd.DataFrame({"ts": pd.date_range("2026-10-19 09:15", periods=len(closes), freq="5min", tz="Asia/Kolkata"),
                         "open": closes, "high": highs, "low": lows, "close": closes, "volume": 1, "oi": 1, "vwap": closes})


def test_short_stop_at_bar2_high_and_rr_target():
    scan = {"side": "short", "trade": {"stop": {"off": 2, "field": "high"}, "target": {"rr": 2.0}}}
    b = bars([100, 101, 100, 99], highs=[101, 103, 101, 99.5])          # bar-2 (off 2) high = 103
    lv = trade_levels(scan, b, {}, entry=99.0)
    assert lv == {"stop": 103.0, "target": 99.0 - 2 * 4.0, "basis": "scan"}


def test_long_stop_level_and_pct_target():
    scan = {"side": "long", "trade": {"stop": {"off": 1, "field": "low"}, "target": {"pct": 1.0}}}
    b = bars([100, 101, 102], lows=[99, 100.5, 101.5])
    lv = trade_levels(scan, b, {}, entry=102.0)
    assert lv["stop"] == 100.5 and abs(lv["target"] - 103.02) < 1e-9 and lv["basis"] == "scan"


def test_wrong_side_stop_returns_none_and_bad_target_falls_back():
    b = bars([100, 101, 102], highs=[101, 102, 103])
    assert trade_levels({"side": "short", "trade": {"stop": {"off": 2, "field": "high"}}}, b, {}, entry=105.0) is None   # stop below a short entry
    lv = trade_levels({"side": "short", "trade": {"stop": {"pct": 1.0}, "target": {"series": "prev_day_high"}}}, b, {"prev_day_high": 110}, entry=100.0)
    assert lv["stop"] == 101.0 and lv["target"] == 98.5 and lv["basis"] == "scan+rr"         # target above a short entry: 1.5x risk instead
    assert trade_levels({"side": "short", "rules": []}, b, {}, 100.0) is None                 # no trade block
    assert trade_levels({"side": "long", "trade": {"stop": {"series": "nope"}}}, b, {}, 100.0) is None


def test_live_scans_carry_trade_blocks():
    import glob
    for f in glob.glob("scans/*.json"):
        sc = json.load(open(f))
        assert "trade" in sc and "stop" in sc["trade"], f


def test_replay_payload_uses_the_scans_levels():
    from alerts import _trade_payload
    d = synthetic_day(seed=5)
    ref = {"prev_close": 100.0, "prev_high": 101.0, "prev_low": 99.0, "sector": "IT"}
    ts = d.ts.iloc[40]
    scan = {"side": "long", "timeframe": "5m", "trade": {"stop": {"pct": 0.5}, "target": {"rr": 2.0}}}
    price = float(view_at(d, ts).close.iloc[-1])
    p = _trade_payload("X", d, ref, ts, "long", price, scan)
    assert p["levels"] == "scan" and abs(p["stop"] - price * 0.995) < 1e-9 and abs(p["target"] - (price + 2 * (price - p["stop"]))) < 1e-9
    assert p["outcome"]["hit"] in ("target", "stop", "none")
    q = _trade_payload("X", d, ref, ts, "long", price, {"side": "long"})
    assert q["levels"] == "pivot"
