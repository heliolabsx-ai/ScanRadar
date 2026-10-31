import datetime as dt, pytest, pandas as pd
from core.rules import in_hours, min_rr
from core.backtest import fires
from conftest import shaped_day

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
W = {"name": "w", "hours": {"from": "09:20", "to": "11:00"}}

def test_in_hours_inclusive_from_exclusive_to():
    assert in_hours(W, dt.datetime(2026, 10, 30, 9, 20, tzinfo=IST))
    assert in_hours(W, dt.datetime(2026, 10, 30, 10, 59, tzinfo=IST))
    assert not in_hours(W, dt.datetime(2026, 10, 30, 11, 0, tzinfo=IST))
    assert in_hours({"name": "n"}, dt.datetime(2026, 10, 30, 15, 0, tzinfo=IST))
    assert in_hours(W, pd.Timestamp("2026-10-30 10:30", tz="Asia/Kolkata"))

def test_hours_rejects_malformed():
    for bad in ({"from": "11:00", "to": "09:20"}, {"from": "9am", "to": "11:00"}, {"to": "11:00"}):
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
