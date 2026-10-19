"""Detector v4: volume ticks, breakout-volume gate, retest label, pivot targets."""
import pandas as pd
from core.patterns import DEFAULTS, detect_day, as_of, bars5m, target_for, pivots, state_at, summarise, _retest_source
from conftest import shaped_day, IST
from test_patterns import P as P0, BOTTOM, TOP, FALLING_OI, VOL, at_bar, one
P = {**P0, "breakout_vol": 1.5}   # the gate on, as in DEFAULTS

CTX = lambda d, pc: {"sector_pct": 0.5, "bars": bars5m(d, pc)}   # noqa: E731


def test_exhaustion_tick_when_the_second_low_trades_less():
    vol = [5000] * 10 + [16000] + [5000] * 4
    vol[1:6] = [9000] * 5                                   # bars around L1 (bar 3, k=2) heavier than around L2 (bar 7)
    d, pc = shaped_day(BOTTOM, oi=FALLING_OI, vol=vol)
    p = one(detect_day(d, pc, P), "double_bottom")
    r = as_of([p], at_bar(10), CTX(d, pc))[0]
    assert p["facts"]["vol_p1"] > p["facts"]["vol_p2"] and r["checks"][5] is True
    f = as_of([p], at_bar(9), CTX(d, pc))[0]
    assert f["checks"][5] is True                            # known at formation, like the other forming facts


def test_breakout_volume_gate_keeps_a_quiet_close_forming():
    d, pc = shaped_day(BOTTOM, oi=FALLING_OI, vol=[5000] * len(BOTTOM))     # trigger bar no heavier than the pattern
    p = one(detect_day(d, pc, P), "double_bottom")
    assert p["triggered_at"] is None and p["facts"]["weak_closes"] >= 1
    assert as_of([p], at_bar(10), CTX(d, pc))[0]["state"] == "forming"
    q = one(detect_day(d, pc, {**P, "breakout_vol": 0}), "double_bottom")      # gate off: the old behaviour
    assert q["triggered_at"] == at_bar(10) and q["facts"]["vol_ratio_pat_trigger"] == 1.0
    r = as_of([q], at_bar(10), CTX(d, pc))[0]
    assert r["checks"][6] is False and r["state"] == "triggered"


def test_breakout_volume_and_delta_ticks_on_a_strong_trigger():
    d, pc = shaped_day(BOTTOM, oi=FALLING_OI, vol=VOL)
    trig_minutes = (d.ts >= at_bar(9)) & (d.ts < at_bar(10))
    d.loc[trig_minutes, "delta"] = 900                      # buyers dominated the confirming bar
    p = one(detect_day(d, pc, P), "double_bottom")
    assert p["triggered_at"] == at_bar(10) and p["facts"]["vol_ratio_pat_trigger"] > 3 and p["facts"]["delta_trigger"] == 4500
    r = as_of([p], at_bar(10), CTX(d, pc))[0]
    assert r["checks"][6] is True and r["checks"][7] is True
    d2, pc2 = shaped_day(TOP, vol=VOL)
    d2.loc[(d2.ts >= at_bar(9)) & (d2.ts < at_bar(10)), "delta"] = 900           # buying on a top breakdown: wrong way
    t = one(detect_day(d2, pc2, P), "double_top")
    assert as_of([t], at_bar(10), CTX(d2, pc2))[0]["checks"][7] is False


def test_pivot_target_varies_rr_and_falls_back_to_1_5x():
    ref = {"prev_high": 104.0, "prev_low": 96.0, "prev_close": 100.0}
    pv = pivots(ref)
    assert pv["P"] == 100.0 and pv["R1"] == 104.0 and pv["S1"] == 96.0
    assert target_for("long", 101.0, 1.0, ref, DEFAULTS) == (104.0, "R1")          # nearest pivot at least a height away
    assert target_for("long", 103.5, 1.0, ref, DEFAULTS) == (108.0, "R2")          # R1 too close: next one
    assert target_for("long", 101.0, 1.0, None, DEFAULTS) == (102.5, "1.5x")      # no refs
    assert target_for("short", 99.0, 1.0, ref, DEFAULTS) == (96.0, "S1")
    d, pc = shaped_day(BOTTOM, oi=FALLING_OI, vol=VOL)
    p = one(detect_day(d, pc, P, ref={"prev_high": 101.0, "prev_low": 97.0, "prev_close": 100.0}), "double_bottom")
    assert p["facts"]["target_basis"] in ("P", "R1", "R2", "1.5x")
    rr = as_of([p], at_bar(9), CTX(d, pc))[0]["rr"]
    assert rr != 1.0 and rr >= 1.0


def test_retest_label_for_a_shape_on_a_failed_patterns_levels():
    failed = {"side": "long", "stop": 98.0, "neckline": 100.0, "failed_idx": 20, "formed_at": pd.Timestamp("2026-10-16 10:00", tz=IST)}
    assert _retest_source([failed], "long", 98.1, 100.2, 25, DEFAULTS) is failed
    assert _retest_source([failed], "long", 98.1, 100.2, 33, DEFAULTS) is None          # 13 bars later: a fresh setup
    assert _retest_source([failed], "long", 96.0, 100.2, 25, DEFAULTS) is None          # different stop
    p = {"formed_at": pd.Timestamp("2026-10-16 11:00", tz=IST), "abandoned_at": None, "superseded_at": None, "failed_at": None,
         "triggered_at": None, "facts": {"retest_until": "2026-10-16T12:00:00+05:30"}}
    assert state_at(p, pd.Timestamp("2026-10-16 11:30", tz=IST)) == "retest"
    assert state_at(p, pd.Timestamp("2026-10-16 12:00", tz=IST)) == "forming"
    p["triggered_at"] = pd.Timestamp("2026-10-16 11:40", tz=IST)
    assert state_at(p, pd.Timestamp("2026-10-16 11:45", tz=IST)) == "triggered"        # a trigger during the retest still counts


def test_summarise_splits_by_breakout_volume():
    mk = lambda v, hit: {"kind": "double_bottom", "score": 6, "triggered_at": "t", "failed_at": None, "outcome": {"hit": hit}, "facts": {"vol_ratio_pat_trigger": v}}  # noqa: E731
    s = summarise([mk(2.0, "target"), mk(2.5, "target"), mk(1.0, "stop"), mk(None, "target")])["double_bottom"]
    assert s["by_volume"]["with"]["triggers"] == 2 and s["by_volume"]["with"]["hit_rate"] == 100.0
    assert s["by_volume"]["without"]["triggers"] == 1 and s["by_volume"]["without"]["hit_rate"] == 0.0
    assert s["by_volume"]["unknown"] == 1 and s["by_score"][">=5"]["triggers"] == 4
