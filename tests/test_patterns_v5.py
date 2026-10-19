"""Detector v5: head & shoulders, flags, rectangles, triangles, opening-range breaks, confidence."""
import pandas as pd
from core.patterns import DEFAULTS, detect_day, as_of, bars5m, confidence, LABELS, CONTINUATION, checks_for
from conftest import shaped_day
from test_patterns import P as P0, at_bar, one

P = {**P0, "breakout_vol": 1.5}
CTX = lambda d, pc: {"sector_pct": 0.5, "bars": bars5m(d, pc)}   # noqa: E731


def vol_spike(n, at, base=5000, spike=16000):
    v = [base] * n; v[at] = spike; return v


def test_head_and_shoulders_top():
    seq = [0, 0.5, 0.8, 1.1, 0.8, 0.4, 0.8, 1.2, 1.7, 1.2, 0.8, 0.4, 0.7, 0.9, 1.1, 0.9, 0.6, 0.1, -0.3, -0.6]
    d, pc = shaped_day(seq, vol=vol_spike(len(seq), 17))
    p = one(detect_day(d, pc, P), "head_shoulders")
    assert [q["idx"] for q in p["points"]] == [3, 8, 14] and p["side"] == "short"
    assert p["formed_at"] == at_bar(16) and p["stop"] == p["points"][1]["price"]
    assert p["triggered_at"] == at_bar(17) and p["facts"]["head_idx"] == 8
    r = as_of([p], at_bar(17), CTX(d, pc))[0]
    assert r["state"] == "triggered" and r["checks"][6] is True and r["rr"] >= 1.0


def test_inverse_head_and_shoulders_mirrors():
    seq = [0, -0.5, -0.8, -1.1, -0.8, -0.4, -0.8, -1.2, -1.7, -1.2, -0.8, -0.4, -0.7, -0.9, -1.1, -0.9, -0.6, -0.1, 0.3, 0.6]
    d, pc = shaped_day(seq, vol=vol_spike(len(seq), 17))
    p = one(detect_day(d, pc, P), "inv_head_shoulders")
    assert p["side"] == "long" and p["triggered_at"] == at_bar(17) and p["target"] > p["neckline"] > p["stop"]


def test_bull_flag_forms_after_a_pole_and_breaks_on_volume():
    seq = [0, 0, 0, 0.2, 0.5, 0.9, 1.3, 1.6, 1.5, 1.4, 1.45, 1.8, 2.0, 2.2]
    d, pc = shaped_day(seq, vol=vol_spike(len(seq), 11))
    p = one(detect_day(d, pc, P), "bull_flag")
    assert p["facts"]["pole_idx"] == 7 and p["formed_at"] == at_bar(10) and p["facts"]["continuation"] is True
    assert p["triggered_at"] == at_bar(11) and p["facts"]["height"] > 0.015 * 100 * 0.9   # the pole, not the flag
    r = as_of([p], at_bar(11), CTX(d, pc))[0]
    assert r["rr"] >= 2.0 and r["checks"][0] is True                  # continuation: day up >= 1% counts as extended
    quiet, pc2 = shaped_day(seq)                                        # no volume on the break: stays forming, then expires
    q = one(detect_day(quiet, pc2, P), "bull_flag")
    assert q["triggered_at"] is None and q["facts"]["weak_closes"] >= 1


def test_bear_flag_mirrors():
    seq = [0, 0, 0, -0.2, -0.5, -0.9, -1.3, -1.6, -1.5, -1.4, -1.45, -1.8, -2.0]
    d, pc = shaped_day(seq, vol=vol_spike(len(seq), 11))
    p = one(detect_day(d, pc, P), "bear_flag")
    assert p["side"] == "short" and p["triggered_at"] == at_bar(11)


def test_rectangle_gives_both_sides_and_dedupes():
    seq = [0.5, 0.8, 0.9, 1.0, 0.7, 0.3, 0.0, 0.3, 0.7, 1.0, 0.7, 0.3, 0.0, 0.3, 0.7, 1.0, 0.7, 0.4, 0.2, 1.4, 1.7]
    d, pc = shaped_day(seq, vol=vol_spike(len(seq), 19))
    pats = detect_day(d, pc, P)
    longs = [p for p in pats if p["kind"] == "range_break_long"]; shorts = [p for p in pats if p["kind"] == "range_break_short"]
    assert len(longs) == 1 and len(shorts) == 1                        # the third swing high does not spawn a second box
    lg = longs[0]
    assert lg["formed_at"] == at_bar(14) and lg["stop"] < lg["neckline"] and lg["triggered_at"] == at_bar(19)
    assert shorts[0]["abandoned_at"] is not None                       # the breakout killed the breakdown case


def test_ascending_triangle():
    seq = [0.5, 0.8, 0.9, 1.0, 0.7, 0.3, 0.0, 0.3, 0.7, 1.0, 0.8, 0.7, 0.6, 0.7, 0.85, 1.0, 0.9, 0.8, 0.7, 1.4, 1.7]
    d, pc = shaped_day(seq, vol=vol_spike(len(seq), 19))
    pats = detect_day(d, pc, P)
    t = one(pats, "asc_triangle")
    assert t["side"] == "long" and t["formed_at"] == at_bar(14) and t["triggered_at"] == at_bar(19)
    assert not [p for p in pats if p["kind"].startswith("range_break")]


def test_opening_range_break_is_created_already_triggered():
    seq = [0.0, 0.3, -0.2, 0.6, 0.9, 1.1, 1.3, 1.5]
    d, pc = shaped_day(seq, vol=vol_spike(len(seq), 3))
    p = one(detect_day(d, pc, P), "orb_long")
    assert p["formed_at"] == p["triggered_at"] == at_bar(3) and p["facts"]["or_high"] > p["facts"]["or_low"]
    assert p["neckline"] == p["facts"]["or_high"] and p["stop"] < p["neckline"]
    quiet, pc2 = shaped_day(seq)
    assert not [q for q in detect_day(quiet, pc2, P) if q["kind"] == "orb_long"]           # no volume: no event
    late = [0.0, 0.3, -0.2] + [0.0] * 20 + [0.6, 0.9]                                     # break after 10:30: ignored
    d3, pc3 = shaped_day(late, vol=vol_spike(len(late), 23))
    assert not [q for q in detect_day(d3, pc3, P) if q["kind"] == "orb_long"]


def test_continuation_checks_flip_extension_and_oi():
    f = {"continuation": True, "day_pct_p2": 1.4, "oi_p1": 100, "oi_p2": 110}
    assert checks_for(f, "long", "forming")[:2] == [True, True]
    assert checks_for({**f, "continuation": False}, "long", "forming")[:2] == [False, False]


def test_confidence_from_stats_buckets():
    st = {"double_bottom": {"hit_rate": 54.2, "decided": 200, "by_score": {">=5": {"hit_rate": 65.0, "decided": 120}, "<5": {"hit_rate": 43.0, "decided": 80}}}}
    assert confidence(st, "double_bottom", 6, "triggered") == {"hit_rate": 65.0, "n": 120, "basis": "score >=5"}
    assert confidence(st, "double_bottom", 2, "failed")["hit_rate"] == 43.0
    assert confidence(st, "double_bottom", 3, "forming") == {"hit_rate": 54.2, "n": 200, "basis": "all triggers"}
    assert confidence(st, "bull_flag", 6, "triggered") is None and confidence({}, "x", 1, "forming") is None


def test_every_kind_has_a_label():
    for k in ("double_bottom", "triple_top", "head_shoulders", "inv_head_shoulders", "bull_flag", "bear_flag",
              "range_break_long", "range_break_short", "asc_triangle", "desc_triangle", "orb_long", "orb_short"):
        assert k in LABELS
    assert "bull_flag" in CONTINUATION and "double_bottom" not in CONTINUATION
