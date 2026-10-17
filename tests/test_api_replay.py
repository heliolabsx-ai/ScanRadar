import datetime as dt
import pandas as pd, pytest
from replay import parse_replay_args, snapshot_from_frames, ReplayError
from history import stability
from conftest import synthetic_day, IST


def test_parse_replay_args():
    d, at = parse_replay_args("2026-10-16", "10:00")
    assert d == dt.date(2026, 10, 16) and at.hour == 10 and at.minute == 0 and at.tzinfo is not None
    for bad in [("2026-10-16", "09:00"), ("2026-10-16", "15:31"), ("2026-10-18", "10:00"), ("nope", "10:00"), ("2026-10-16", "1000")]:
        with pytest.raises(ReplayError) as e:
            parse_replay_args(*bad)
        assert e.value.status_code == 400


def test_snapshot_from_frames_uses_stub():
    d = synthetic_day()
    at = pd.Timestamp("2026-10-16 10:00", tz=IST)
    ref = {"prev_close": float(d.open.iloc[0]) * 0.99, "prev_high": float(d.open.iloc[0]) * 1.02,
           "prev_low": float(d.open.iloc[0]) * 0.98, "prev_oi": 990_000, "avg_vol_1m": 2500, "sector": "IT"}
    snap = snapshot_from_frames({"X": d}, {"X": ref}, at)
    row = (snap["bulls"] + snap["bears"])[0]
    assert snap["mode"] == "replay" and row["price"] == float(d[d.ts == at].open.iloc[0])


def test_snapshot_from_frames_none_when_empty():
    assert snapshot_from_frames({}, {}, pd.Timestamp("2026-10-16 10:00", tz=IST)) is None


def MINUTES(a, b):
    t = dt.datetime.strptime(a, "%H:%M")
    end = dt.datetime.strptime(b, "%H:%M")
    out = []
    while t <= end:
        out.append(t.strftime("%H:%M"))
        t += dt.timedelta(minutes=1)
    return out


def test_stability_unbroken_run_and_rank_30m():
    tl = {m: {"X": (1 if ("10:0" in m or m >= "10:10") else 7, 1, m >= "10:10")} for m in MINUTES("09:15", "10:40")}
    assert stability(tl, "10:40", "X") == ("10:10", 1)
    assert stability(tl, "09:30", "X") == (None, None)      # not picked, and fewer than 30 minutes of history


def test_stability_symbol_absent():
    tl = {"10:00": {"Y": (3, 2, True)}}
    assert stability(tl, "10:00", "X") == (None, None)


def test_cache_ttl_policy():
    from replay import cache_ttl, IST as _IST
    day = dt.date(2026, 10, 19)
    at = dt.datetime(2026, 10, 19, 10, 0, tzinfo=_IST)
    assert cache_ttl(dt.date(2026, 10, 16), at.replace(day=16), now=dt.datetime(2026, 10, 19, 11, 0, tzinfo=_IST)) == 7 * 86400   # past day
    assert cache_ttl(day, at, now=dt.datetime(2026, 10, 19, 10, 0, 30, tzinfo=_IST)) is None      # the 10:00 bar is still forming
    assert cache_ttl(day, at, now=dt.datetime(2026, 10, 19, 10, 2, 0, tzinfo=_IST)) == 300        # flushed, but the day is live
    assert cache_ttl(day, at, now=dt.datetime(2026, 10, 19, 15, 40, 0, tzinfo=_IST)) == 7 * 86400  # settled


def test_future_at_is_rejected_for_today():
    from replay import assert_not_future, ReplayError, IST as _IST
    at = dt.datetime(2026, 10, 19, 14, 0, tzinfo=_IST)
    with pytest.raises(ReplayError) as e:
        assert_not_future(at, now=dt.datetime(2026, 10, 19, 10, 0, tzinfo=_IST))
    assert e.value.status_code == 400
    assert_not_future(at, now=dt.datetime(2026, 10, 19, 14, 0, 5, tzinfo=_IST)) is None
