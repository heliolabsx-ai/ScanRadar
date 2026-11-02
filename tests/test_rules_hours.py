import datetime as dt, pytest, pandas as pd
from core.rules import in_hours, min_rr
from core.backtest import fires, fires_naive
from conftest import shaped_day, synthetic_day

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
W = {"name": "w", "hours": {"from": "09:20", "to": "11:00"}}

def test_in_hours_inclusive_from_exclusive_to():
    assert in_hours(W, dt.datetime(2026, 10, 30, 9, 20, tzinfo=IST))
    assert in_hours(W, dt.datetime(2026, 10, 30, 10, 59, tzinfo=IST))
    assert not in_hours(W, dt.datetime(2026, 10, 30, 11, 0, tzinfo=IST))
    assert in_hours({"name": "n"}, dt.datetime(2026, 10, 30, 15, 0, tzinfo=IST))
    assert in_hours(W, pd.Timestamp("2026-10-30 10:30", tz="Asia/Kolkata"))

def test_hours_rejects_malformed():
    for bad in ({"from": "11:00", "to": "09:20"}, {"from": "9am", "to": "11:00"}, {"from": "9:20", "to": "11:00"}, {"to": "11:00"}):
        with pytest.raises(ValueError, match="w"):
            in_hours({"name": "w", "hours": bad}, dt.datetime(2026, 10, 30, 10, 0, tzinfo=IST))

def test_min_rr_default_and_zero():
    assert min_rr({"name": "a"}, 1.5) == 1.5
    assert min_rr({"name": "a", "min_rr": 0}, 1.5) == 0.0

def test_fires_respects_the_window():
    df, _ = shaped_day([0] * 75)                                   # flat day: a rule that is always true
    scan = {"name": "always", "timeframe": "5m", "rules": [{"lhs": {"number": 1}, "op": ">", "rhs": {"number": 0}}], "hours": {"from": "10:00", "to": "10:10"}}
    out = fires(scan, df, {}, cooldown_min=1)
    assert [t.strftime("%H:%M") for t in out][:3] == ["10:00", "10:01", "10:02"] and all("10:00" <= t.strftime("%H:%M") < "10:10" for t in out)


def test_windowed_fires_equal_unwindowed_filtered_to_the_window():
    # forming-bar volume depends on folding every minute of the bar, so a fold skipped with the minute would change fires
    d = synthetic_day(seed=3)
    rule = [{"lhs": {"off": 0, "field": "volume"}, "op": ">", "rhs": {"number": 6000}}]
    plain = {"name": "p", "timeframe": "5m", "rules": rule}
    windowed = {**plain, "hours": {"from": "10:07", "to": "10:32"}}
    ref = [t for t in fires(plain, d, {}, cooldown_min=1) if "10:07" <= t.strftime("%H:%M") < "10:32"]
    got = fires(windowed, d, {}, cooldown_min=1)
    assert got == ref and len(got) > 0
    assert len(fires(plain, d, {}, cooldown_min=1)) > len(got)
    assert fires_naive(windowed, d, {}, cooldown_min=1) == got


def test_validate_scan_accepts_good_and_rejects_bad():
    from core.rules import validate_scan
    validate_scan({"name": "ok"})
    validate_scan({"name": "ok", "hours": {"from": "09:20", "to": "11:00"}, "min_rr": 0})
    validate_scan({"name": "ok", "min_rr": 1.5})
    for bad in ({"name": "w", "hours": {"from": "11:00", "to": "09:20"}},
                {"name": "w", "min_rr": "x"}, {"name": "w", "min_rr": -1}, {"name": "w", "min_rr": True}, {"name": "w", "min_rr": None}):
        with pytest.raises(ValueError, match="w"):
            validate_scan(bad)
    with pytest.raises(ValueError):
        validate_scan([{"name": "w"}])


def test_resampler_resamples_each_symbol_once_per_timeframe(monkeypatch):
    import core.rules as rules
    d = synthetic_day(seed=3)
    calls = []
    real = rules.resample
    monkeypatch.setattr(rules, "resample", lambda df, tf: calls.append(tf) or real(df, tf))
    get = rules.resampler({"A": d, "B": d})
    for _ in range(8):                                   # eight scans
        for s in ("A", "B"):
            a5 = get(s, "5m"); get(s, "15m")
    assert sorted(calls) == ["15m", "15m", "5m", "5m"]
    pd.testing.assert_frame_equal(a5, real(d, "5m"))     # same bars as resampling directly
