import datetime as dt
import pandas as pd
from patterns import rows_at, today_rows_from_frames, pattern_state
from conftest import synthetic_day

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def T(hhmm):
    h, m = hhmm.split(":")
    return dt.datetime(2026, 10, 16, int(h), int(m), tzinfo=IST)


def stored(**kw):
    base = {"id": 1, "day": dt.date(2026, 10, 16), "symbol": "X", "kind": "double_bottom", "tf": "5m", "side": "long",
            "formed_at": T("10:00"), "triggered_at": T("10:10"), "failed_at": T("10:20"), "abandoned_at": None,
            "superseded_at": None, "neckline": 100.0, "entry": 100.5, "stop": 98.0, "target": 102.0,
            "points": [{"idx": 3, "ts": "2026-10-16T09:30:00+05:30", "price": 98.0, "oi": 1.0}],
            "checks": [True, False, True, True, True, True], "score": 4, "outcome": {"hit": "stop"}, "source": "replay"}
    base.update(kw)
    return base


def test_pattern_state_and_rows_at_states():
    r = stored()
    assert rows_at([r], T("09:59")) == []
    assert pattern_state(r, T("10:00")) == "forming" and rows_at([r], T("10:00"))[0]["state"] == "forming"
    assert pattern_state(r, T("10:10")) == "triggered"
    assert pattern_state(r, T("10:20")) == "failed"
    row = rows_at([r], T("10:30"))[0]
    assert row["state"] == "failed" and row["age_min"] == 20 and row["rr"] == (102.0 - 100.5) / (100.5 - 98.0)
    forming = rows_at([r], T("10:05"))[0]
    assert forming["checks"][9] is False and len(forming["checks"]) == 10 and forming["entry"] is None and forming["rr"] == (102.0 - 100.0) / (100.0 - 98.0)


def test_rows_at_hides_superseded_and_abandoned_after_minute():
    sup = stored(superseded_at=T("10:30"), triggered_at=None, failed_at=None)
    assert rows_at([sup], T("10:29")) and rows_at([sup], T("10:30")) == []
    ab = stored(abandoned_at=T("10:05"), triggered_at=None, failed_at=None)
    assert rows_at([ab], T("10:04")) and rows_at([ab], T("10:05")) == []


def test_today_rows_empty_before_first_bar():
    d = synthetic_day().iloc[:3]
    refs = {"X": {"prev_close": 100.0, "sector": "IT"}}
    assert today_rows_from_frames({"X": d}, refs, pd.Timestamp("2026-10-16 09:18", tz="Asia/Kolkata")) == []


def test_today_rows_from_frames_returns_rows_with_symbol():
    import math
    d = synthetic_day(seed=3, base_price=500.0)
    d["close"] = d.close * (1 + 0.01 * pd.Series(range(len(d))).mul(0.09).apply(math.sin))   # 14-bar waves → repeated lows
    d["high"] = d[["open", "close"]].max(axis=1) + 0.05; d["low"] = d[["open", "close"]].min(axis=1) - 0.05
    refs = {"X": {"prev_close": float(d.open.iloc[0]) * 1.03, "sector": "IT"}}
    rows = today_rows_from_frames({"X": d}, refs, d.ts.iloc[-1] + pd.Timedelta(minutes=1),
                                  params={"tol": 0.004, "min_sep": 4, "max_sep": 36, "min_retrace": 0.0035, "k": 2, "fail_bars": 3, "min_extension": 0.0})
    assert rows and all(r["symbol"] == "X" and r["state"] in ("forming", "triggered", "failed") for r in rows)


def test_upsert_sql_updates_lifecycle_on_conflict():
    from patterns import UPSERT_SQL
    assert "ON CONFLICT (day, symbol, kind, formed_at) DO UPDATE SET" in UPSERT_SQL
    for col in ("triggered_at", "entry", "failed_at", "abandoned_at", "superseded_at", "checks", "score", "facts"):
        assert f"{col}=EXCLUDED.{col}" in UPSERT_SQL
    assert "outcome=COALESCE(EXCLUDED.outcome, patterns.outcome)" in UPSERT_SQL


def test_rows_at_forming_checks_come_from_facts_not_trigger_time():
    from patterns import rows_at
    facts = {"day_pct_p2": -1.5, "oi_p1": 100, "oi_p2": 90, "vol_ratio_p2": 1.0, "vol_ratio_trigger": 3.0,
             "vwap_ok_formed": False, "vwap_ok_trigger": True, "sector_formed": -0.2, "sector_trigger": 0.4}
    facts.update(vol_p1=1000, vol_p2=800, vol_ratio_pat_trigger=2.0, delta_trigger=50, breadth_formed=0.3, breadth_trigger=0.6)
    r = stored(facts=facts, checks=[True] * 10, score=9)
    forming = rows_at([r], T("10:05"))[0]
    assert forming["checks"] == [True, True, False, False, False, True, False, False, False, False] and forming["score"] == 3
    trig = rows_at([r], T("10:10"))[0]
    assert trig["checks"] == [True] * 10 and trig["score"] == 9
    old = stored(facts=None, checks=[True, False, True, True, True, True], score=4)       # a v3 row without facts
    assert len(rows_at([old], T("10:10"))[0]["checks"]) == 10


def test_json_rows_use_iso_timestamps_in_both_paths():
    from patterns import to_json_rows
    d = synthetic_day()
    refs = {"X": {"prev_close": float(d.open.iloc[0]) * 1.03, "sector": "IT"}}
    import math
    d["close"] = d.close * (1 + 0.01 * pd.Series(range(len(d))).mul(0.09).apply(math.sin))
    d["high"] = d[["open", "close"]].max(axis=1) + 0.05; d["low"] = d[["open", "close"]].min(axis=1) - 0.05
    rows = today_rows_from_frames({"X": d}, refs, d.ts.iloc[-1] + pd.Timedelta(minutes=1),
                                  params={"tol": 0.004, "min_sep": 4, "max_sep": 36, "min_retrace": 0.0035, "k": 2, "fail_bars": 3, "min_extension": 0.0})
    js = to_json_rows(rows)
    assert js and "T" in js[0]["formed_at"] and "T" in js[0]["points"][0]["ts"] and "+05:30" in js[0]["formed_at"]


def test_today_rows_are_graded_as_of_at():
    import math
    d = synthetic_day(seed=3, base_price=500.0)
    d["close"] = d.close * (1 + 0.01 * pd.Series(range(len(d))).mul(0.09).apply(math.sin))
    d["high"] = d[["open", "close"]].max(axis=1) + 0.05; d["low"] = d[["open", "close"]].min(axis=1) - 0.05
    refs = {"X": {"prev_close": float(d.open.iloc[0]) * 1.03, "sector": "IT"}}
    params = {"tol": 0.004, "min_sep": 4, "max_sep": 36, "min_retrace": 0.0035, "k": 2, "fail_bars": 3, "min_extension": 0.0, "breakout_vol": 0}
    rows = today_rows_from_frames({"X": d}, refs, d.ts.iloc[-1] + pd.Timedelta(minutes=1), params=params)
    trig = [r for r in rows if r["state"] != "forming"]
    assert trig and all("outcome" in r and r["outcome"] is not None for r in trig)
