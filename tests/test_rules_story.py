import pytest
from core.rules import story, resample
from conftest import shaped_day
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
