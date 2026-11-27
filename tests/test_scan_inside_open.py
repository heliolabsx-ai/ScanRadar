"""scans/Inside_open_long.json and Inside_open_short.json on hand-built days: price above 2,000, the first five
minutes inside yesterday's range, and at 09:20 the price above (long) or below (short) the 1-minute VWAP.
One fire a day, at 09:20, on exactly one side; stop = the first five minutes' other end, target = PDH / PDL."""
import pandas as pd
import alerts
from conftest import ROOT, shaped_day
from core import rules
from core.backtest import fires, fires_naive
from core.asof import view_at

BASE = 2500.0
CTX = {"prev_day_high": BASE * 1.01, "prev_day_low": BASE * 0.99, "prev_day_close": BASE}
UP = [0.2, 0.3, 0.35, 0.3] + [0.3] * 20          # 5-minute closes in % of BASE: a first candle that rises
DOWN = [-0.2, -0.3, -0.35, -0.3] + [-0.3] * 20


def _scans() -> dict:
    keep = alerts.SCAN_DIR
    alerts.SCAN_DIR = str(ROOT / "scans")
    try:
        return {s["name"]: s for s, _ in alerts.scan_files()}
    finally:
        alerts.SCAN_DIR = keep


SCANS = _scans()
LONG, SHORT = SCANS["Inside open long"], SCANS["Inside open short"]


def _fires(scan, rel, base=BASE, ctx=CTX):
    df, _ = shaped_day(rel, base=base)
    return df, [t.strftime("%H:%M") for t in fires(scan, df, ctx, 400)]


def test_a_rising_first_candle_fires_long_once_at_0920_and_never_short():
    df, out = _fires(LONG, UP)
    assert out == ["09:20"]
    assert _fires(SHORT, UP)[1] == []
    assert [t.strftime("%H:%M") for t in fires_naive(LONG, df, CTX, 400)] == ["09:20"]      # the fast replay agrees with the definition


def test_a_falling_first_candle_fires_short_once_at_0920_and_never_long():
    assert _fires(SHORT, DOWN)[1] == ["09:20"]
    assert _fires(LONG, DOWN)[1] == []


def test_levels_are_the_first_five_minutes_other_end_and_yesterdays_extreme():
    for scan, rel in ((LONG, UP), (SHORT, DOWN)):
        df, _ = shaped_day(rel, base=BASE)
        at = df.ts.iloc[5]                                               # 09:20
        bars = rules.resample(view_at(df, at), "1m"); bars.attrs["session_start"] = 0
        entry = float(bars.close.iloc[-1])
        lv = rules.trade_levels(scan, bars, CTX, entry=entry)
        first = df.iloc[:5]
        assert lv["basis"] == "scan"
        if scan is LONG:
            assert lv["stop"] == min(first.low.min(), entry) and lv["target"] == CTX["prev_day_high"]
        else:
            assert lv["stop"] == max(first.high.max(), entry) and lv["target"] == CTX["prev_day_low"]
        assert rules.strength(scan, bars, CTX) > 0 and rules.market_vote(scan, bars, CTX) is (scan is LONG)


def test_no_fire_under_2000():
    ctx = {"prev_day_high": 1515.0, "prev_day_low": 1485.0, "prev_day_close": 1500.0}
    assert _fires(LONG, UP, base=1500.0, ctx=ctx)[1] == [] and _fires(SHORT, DOWN, base=1500.0, ctx=ctx)[1] == []


def test_no_fire_when_the_first_five_minutes_left_yesterdays_range():
    above = dict(CTX, prev_day_high=BASE * 1.001)                        # the first candle trades through PDH
    below = dict(CTX, prev_day_low=BASE * 0.999)
    assert _fires(LONG, UP, ctx=above)[1] == [] and _fires(SHORT, UP, ctx=above)[1] == []
    assert _fires(SHORT, DOWN, ctx=below)[1] == [] and _fires(LONG, DOWN, ctx=below)[1] == []


def test_only_the_0920_minute_can_fire():
    df, _ = shaped_day(UP, base=BASE)
    late = df.copy(); late["ts"] = late.ts + pd.Timedelta(minutes=5)     # the same shape, the day starting at 09:20
    assert fires(LONG, late, CTX, 400) == []
    flat_then_up = [0.0, 0.3, 0.5, 0.6] + [0.6] * 20                      # nothing to read at 09:20; the move comes later
    assert _fires(LONG, flat_then_up)[1] in ([], ["09:20"])              # whatever 09:20 says, no later minute fires
    assert all(t == "09:20" for t in _fires(LONG, flat_then_up)[1] + _fires(SHORT, flat_then_up)[1])


def test_no_fire_without_reference_levels():
    assert _fires(LONG, UP, ctx={"prev_day_high": 0, "prev_day_low": 0, "prev_day_close": 0})[1] == []
    assert _fires(SHORT, DOWN, ctx={"prev_day_high": 0, "prev_day_low": 0, "prev_day_close": 0})[1] == []


def test_the_live_engine_reads_the_same_state_when_the_new_minute_has_not_ticked_yet():
    df, _ = shaped_day(UP, base=BASE)
    bars = rules.resample(df.iloc[:5], "1m"); bars.attrs["session_start"] = 0       # 09:15..09:19, the last one still "forming"
    assert rules.evaluate(LONG, bars, CTX) and not rules.evaluate(SHORT, bars, CTX)


def test_the_two_files_are_one_group_with_one_market_rule():
    for sc in (LONG, SHORT):
        rules.validate_scan(sc)
        assert rules.digest(sc)["group"] == "Inside open" and sc["desk"] == "rows" and rules.min_rr(sc, 1.5) == 0
        assert sc["hours"] == {"from": "09:20", "to": "09:21"} and sc["timeframe"] == "1m"
    assert LONG["digest"]["market"] == SHORT["digest"]["market"] and LONG["digest"]["strength"] == SHORT["digest"]["strength"]
    assert LONG["digest"]["stars"] == SHORT["digest"]["stars"] == 3
