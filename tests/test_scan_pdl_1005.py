"""scans/PDL_1005_short.json on hand-built days: the 09:45 close holds yesterday's low and sits under VWAP,
the 10:00 close and the 10:05 close are both below the low, and the alarm fires once, at 10:05."""
import pandas as pd
import alerts
from conftest import ROOT, shaped_day
from core.backtest import fires
from core.rules import resample, trade_levels, validate_scan

NAME = "PDL 10:05 short"
CTX = {"prev_day_high": 1012.0, "prev_day_low": 995.0, "prev_day_close": 1002.0}       # PDL = base - 0.5 %
#       09:15 ..................... 09:40 | 09:45  09:50  09:55 | 10:00 | after
DRIFT = [-0.05, -0.1, -0.15, -0.2, -0.25, -0.3, -0.4, -0.45, -0.7, -0.8] + [-0.9] * 12


def _scan() -> dict:
    keep = alerts.SCAN_DIR
    alerts.SCAN_DIR = str(ROOT / "scans")
    try:
        return {s["name"]: s for s, _ in alerts.scan_files()}[NAME]
    finally:
        alerts.SCAN_DIR = keep


SCAN = _scan()


def _at(rel):
    df, _ = shaped_day(rel, base=1000.0)
    return df, [t.strftime("%H:%M") for t in fires(SCAN, df, CTX, 400)]


def test_fires_once_at_1005_with_a_fixed_stop_and_target():
    df, out = _at(DRIFT)
    assert out == ["10:05"]
    seen = df[df.ts.dt.strftime("%H:%M") < "10:05"]
    bars = resample(seen, "5m"); bars.attrs["session_start"] = 0
    entry = float(seen.close.iloc[-1])
    lv = trade_levels(SCAN, bars, CTX, entry=entry)
    assert lv["basis"] == "scan"
    assert abs(lv["stop"] - entry * 1.015) < 1e-6 and abs(lv["target"] - entry * 0.97) < 1e-6


def test_no_fire_when_the_1000_candle_closes_back_above_the_low():
    rel = list(DRIFT); rel[9] = -0.4
    assert _at(rel)[1] == []


def test_no_fire_when_the_1000_close_is_not_below_the_low():
    rel = list(DRIFT); rel[8] = -0.45                       # 3rd 15-minute candle still above PDL; the 10:05 close alone is not enough
    assert _at(rel)[1] == []


def test_no_fire_when_the_0945_close_was_already_below_the_low():
    rel = list(DRIFT); rel[5] = -0.6
    assert _at(rel)[1] == []


def test_no_fire_when_the_0945_close_is_above_vwap():
    rel = [0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.0, -0.3, -0.7, -0.8] + [-0.9] * 12      # rising into 09:45, then the fall
    assert _at(rel)[1] == []


def test_the_same_shape_later_in_the_day_does_not_fire():
    df, _ = shaped_day(DRIFT, base=1000.0)
    late = df.copy(); late["ts"] = late.ts + pd.Timedelta(minutes=30)
    assert fires(SCAN, late, CTX, 400) == []


def test_no_fire_without_reference_levels():
    df, _ = shaped_day(DRIFT, base=1000.0)
    assert fires(SCAN, df, {"prev_day_high": 0, "prev_day_low": 0, "prev_day_close": 0}, 400) == []


def test_the_file_is_a_loud_desk_scan_one_per_stock_per_day():
    validate_scan(SCAN)
    assert SCAN["side"] == "short" and SCAN["timeframe"] == "5m" and "continuous" not in SCAN
    assert SCAN["hours"] == {"from": "10:05", "to": "10:10"}
    assert SCAN["alert"] == {"telegram": True, "cooldown_min": 400} and SCAN["desk"] is True
    assert SCAN["trade"] == {"stop": {"pct": 1.5}, "target": {"pct": 3.0}}
