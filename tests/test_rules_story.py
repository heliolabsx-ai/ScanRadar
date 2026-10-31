import json
import pytest
from core.rules import story, resample, join_sessions
from core.backtest import fires, prev_bars
from core.asof import view_at
from conftest import ROOT, shaped_day
import pandas as pd


def test_story_finds_the_break_and_the_run():
    df, _ = shaped_day([0, 0.1, 0.4, 0.9, 0.6, -0.2], base=100.0)     # PDH 100.2: bar 2 (09:25) closes above, run high in bar 3, bar 5 closes back below
    bars = resample(df, "5m"); bars.attrs["session_start"] = 0
    sc = {"name": "PDH fade", "side": "short", "story": {"line": "prev_day_high", "template": "x"}}
    s = story(sc, bars, {"prev_day_high": 100.2})
    assert s["line"] == 100.2 and s["break_at"] == "09:30" and s["run_px"] == pytest.approx(float(bars.high.iloc[2:5].max())) and s["run_pct"] > 0


def test_story_none_without_a_break_or_block():
    df, _ = shaped_day([0, -0.1, -0.2], base=100.0)
    bars = resample(df, "5m"); bars.attrs["session_start"] = 0
    assert story({"name": "n", "side": "short"}, bars, {"prev_day_high": 100.2}) is None
    assert story({"name": "n", "side": "short", "story": {"line": "prev_day_high", "template": "x"}}, bars, {"prev_day_high": 100.2}) is None


def test_story_long_side_runs_below_the_line():
    df, _ = shaped_day([0, -0.1, -0.4, -0.9, -0.6, 0.2], base=100.0)    # PDL 99.8: bar 2 (09:25) closes below, run low in bar 3, bar 5 is the forming bar
    bars = resample(df, "5m"); bars.attrs["session_start"] = 0
    sc = {"name": "PDL fade", "side": "long", "story": {"line": "prev_day_low", "template": "x"}}
    s = story(sc, bars, {"prev_day_low": 99.8})
    assert s["break_at"] == "09:30" and s["run_px"] == pytest.approx(float(bars.low.iloc[2:5].min())) and s["run_pct"] < 0


def test_story_ignores_yesterdays_bars_on_a_continuous_frame():
    prev, _ = shaped_day([0.5] * 10, base=100.0, day="2026-10-15")      # yesterday closed above PDH 100.2 all session
    today, _ = shaped_day([-0.1, -0.2, -0.1], base=100.0)              # today stays below
    bars = join_sessions(resample(prev, "5m"), resample(today, "5m"))
    assert bars.attrs["session_start"] > 0
    sc = {"name": "PDH fade", "side": "short", "story": {"line": "prev_day_high", "template": "x"}}
    assert story(sc, bars, {"prev_day_high": 100.2}) is None


def test_break_scan_story_breaks_at_the_fire_minute():
    sc = json.loads((ROOT / "scans" / "PDH_break.json").read_text())
    prev, _ = shaped_day([0] * 75, base=100.0, day="2026-10-15")       # yesterday closed at 100, under PDH 100.2
    df, _ = shaped_day([0, 0.5, 0.6] + [0.6] * 10, base=100.0)          # bar 1 (09:20) closes above: the break, fired at 09:25
    ctx = {"prev_day_high": 100.2, "prev_day_low": 99.0, "prev_day_close": 100.0}
    out = fires(sc, df, ctx, 400, prev1m=prev)
    assert [t.strftime("%H:%M") for t in out] == ["09:25"]
    bars = join_sessions(prev_bars(sc, prev), resample(view_at(df, out[0]), sc.get("timeframe", "5m")))
    s = story(sc, bars, ctx)
    assert s["break_at"] == out[0].strftime("%H:%M")
