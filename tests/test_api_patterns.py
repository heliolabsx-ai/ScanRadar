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
    assert forming["checks"][5] is False and forming["entry"] is None and forming["rr"] == (102.0 - 100.0) / (100.0 - 98.0)


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
