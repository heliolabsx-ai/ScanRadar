"""Detector v6: T1 measured-move target, last-trigger cutoff, breadth check."""
import pandas as pd
from core.patterns import detect_day, as_of, bars5m, checks_for, DEFAULTS, summarise
from core.backtest import grade_pattern
from conftest import shaped_day
from test_patterns import P as P0, BOTTOM, FALLING_OI, VOL, at_bar, one

P = {**P0, "breakout_vol": 0}


def test_t1_is_the_measured_move_and_is_graded_separately():
    d, pc = shaped_day(BOTTOM, oi=FALLING_OI, vol=VOL)
    p = one(detect_day(d, pc, P), "double_bottom")
    h = p["neckline"] - p["stop"]
    assert abs(p["facts"]["target1"] - (p["neckline"] + h)) < 1e-9 and p["target"] > p["facts"]["target1"]
    o = grade_pattern(d, p)
    assert o["hit_t1"] in ("target", "stop", "none") and "tt1_min" in o
    s = summarise([{"kind": "double_bottom", "score": 6, "triggered_at": "t", "failed_at": None, "outcome": {"hit": "none", "hit_t1": "target"}, "facts": {}}])
    assert s["double_bottom"]["hit_rate_t1"] == 100.0 and s["double_bottom"]["hit_rate"] is None


def test_no_new_triggers_after_the_cutoff():
    late = [0.0] * 55 + BOTTOM           # the same shape, but its second low confirms around 14:20 and the break comes after 14:00
    d, pc = shaped_day(late, vol=[5000] * 55 + VOL)
    pats = [p for p in detect_day(d, pc, P) if p["kind"] == "double_bottom"]
    assert pats and all(p["triggered_at"] is None and p["abandoned_at"] is not None for p in pats)
    early = detect_day(d, pc, {**P, "last_trigger": "15:30"})
    assert any(p["triggered_at"] is not None for p in early if p["kind"] == "double_bottom")


def test_breadth_check_direction():
    long_ok = {"breadth_trigger": 0.5}; long_bad = {"breadth_trigger": 0.3}
    assert checks_for(long_ok, "long", "triggered")[8] is True and checks_for(long_bad, "long", "triggered")[8] is False
    assert checks_for({"breadth_trigger": 0.3}, "short", "triggered")[8] is True and checks_for({"breadth_trigger": 0.7}, "short", "triggered")[8] is False
    assert checks_for({"breadth_formed": 0.5}, "long", "forming")[8] is True and checks_for({}, "long", "forming")[8] is False
