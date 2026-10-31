"""The engine's Telegram text for a rule alert (core.rules.alert_text, mirrored in engine/rules.py)."""
import datetime as dt
import json
import pytest
from conftest import ROOT
from core.rules import alert_text

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def scan(name: str) -> dict:
    return json.loads((ROOT / "scans" / f"{name}.json").read_text())


def test_fade_text_has_the_side_word_and_the_entry_line():
    st = {"line": 866.45, "break_at": "15:00", "run_px": 873.90, "run_pct": 0.9}
    lv = {"stop": 873.90, "target": 851.15, "basis": "scan"}
    t = alert_text(scan("PDH_fade"), "MAXHEALTH", 864.20, lv, st, dt.datetime(2026, 10, 30, 15, 10, tzinfo=IST))
    assert t.splitlines() == ["<b>PDH fade · SELL</b>",
                              "MAXHEALTH broke above PDH 866.45 at 15:00, ran to 873.90 (+0.9 %), first 5m close back below at 15:10.",
                              "entry ₹864.20 · stop ₹873.90 (run high) · target ₹851.15 (prev close) · R:R 1:1.3",
                              "15:10  ·  scanradar.tech"]
    st = {"line": 1500.0, "break_at": "09:30", "run_px": 1480.0, "run_pct": -1.3}
    t = alert_text(scan("PDL_fade"), "M&M", 1502.0, {"stop": 1480.0, "target": 1530.0, "basis": "scan+rr"}, st,
                   dt.datetime(2026, 10, 30, 9, 45, tzinfo=IST))
    assert t.startswith("<b>PDL fade · BUY</b>\nM&amp;M broke below PDL 1,500.00")
    assert "stop ₹1,480.00 (run low) · target ₹1,530.00 · R:R 1:1.3" in t      # fallback target: not labelled prev close


def test_break_note_has_no_side_word_and_no_levels():
    st = {"line": 866.45, "break_at": "15:05", "run_px": 870.0, "run_pct": 0.4}
    t = alert_text(scan("PDH_break"), "GVT&D", 870.0, None, st, dt.datetime(2026, 10, 30, 15, 5, tzinfo=IST))
    assert t == "<b>PDH break</b>\nGVT&amp;D above PDH 866.45 at 15:05 · watching for the fade\n15:05  ·  scanradar.tech"
    assert "SELL" not in t and "BUY" not in t
    t = alert_text(scan("PDL_break"), "INFY", 1400.0, None, dict(st, line=1410.0), dt.datetime(2026, 10, 30, 9, 30, tzinfo=IST))
    assert t.splitlines()[0] == "<b>PDL break</b>"


def test_scan_without_a_story_keeps_the_plain_text_and_a_bad_template_raises():
    lv = {"stop": 2010.0, "target": 2060.0, "basis": "scan"}
    t = alert_text(scan("B0101"), "M&M", 2030.0, lv, None, dt.datetime(2026, 10, 30, 10, 0, tzinfo=IST))
    assert t == "<b>B0101</b>\nM&amp;M @ ₹2,030.00  (5m)\nstop ₹2,010.00 · target ₹2,060.00 · R:R 1:1.5\n10:00  ·  scanradar.tech"
    bad = dict(scan("PDH_fade"), story={"line": "prev_day_high", "template": "{nope}"})
    with pytest.raises(KeyError):
        alert_text(bad, "X", 100.0, None, {"line": 1.0, "break_at": "09:20", "run_px": 1.0, "run_pct": 0.0}, dt.datetime(2026, 10, 30, 10, 0, tzinfo=IST))
