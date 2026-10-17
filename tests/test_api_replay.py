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
