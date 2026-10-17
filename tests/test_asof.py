import pandas as pd
from core.asof import view_at
from conftest import synthetic_day, IST


def T(hhmm: str) -> pd.Timestamp:
    return pd.Timestamp(f"2026-10-16 {hhmm}", tz=IST)


def test_view_at_complete_bars_strictly_before_at(day):
    at = T("10:00")
    v = view_at(day, at)
    assert (v.ts.iloc[:-1] < at).all() and v.ts.iloc[-1] == at


def test_view_at_stub_is_flat_at_open_with_prev_oi(day):
    at = T("10:00")
    bar = day[day.ts == at].iloc[0]
    prev = day[day.ts < at].iloc[-1]
    stub = view_at(day, at).iloc[-1]
    assert stub.open == stub.high == stub.low == stub.close == bar.open
    assert stub.volume == 0 and stub.delta == 0 and stub.oi == prev.oi


def test_view_at_no_bar_at_minute():
    d = synthetic_day(with_gap_at=["10:00"])
    at = T("10:00")
    v = view_at(d, at)
    assert len(v) == (d.ts < at).sum() and v.ts.iloc[-1] < at


def test_view_at_first_minute_stub_oi_is_bars_own(day):
    v = view_at(day, T("09:15"))
    assert len(v) == 1 and v.iloc[0].oi == day.iloc[0].oi


def test_view_at_does_not_mutate_input(day):
    before = day.copy()
    view_at(day, T("11:00"))
    pd.testing.assert_frame_equal(before, day)
