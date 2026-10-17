import datetime as dt
import pytest
from backfill import merge_oi, fetch_window, REBUILD_DAILY_REF_SQL

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def T(day: str, hhmm: str) -> dt.datetime:
    h, m = hhmm.split(":")
    y, mo, d = (int(x) for x in day.split("-"))
    return dt.datetime(y, mo, d, int(h), int(m), tzinfo=IST)


def bar(ts, **kw):
    b = {"date": ts, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 5}
    b.update(kw)
    return b


def test_merge_oi_forward_fills_within_day_only():
    eq = [bar(T("2026-10-15", "09:15")), bar(T("2026-10-15", "09:16")), bar(T("2026-10-15", "09:17")),
          bar(T("2026-10-16", "09:15"))]
    fut = [bar(T("2026-10-15", "09:16"), oi=100)]
    rows = merge_oi(eq, fut)
    assert [r[0] for r in rows] == [b["date"] for b in eq]
    assert [r[6] for r in rows] == [0, 100, 100, 0]
    assert all(r[7] == 0 for r in rows)


def test_merge_oi_row_layout():
    rows = merge_oi([bar(T("2026-10-15", "09:15"), open=2, high=3, low=1, close=2.5, volume=7)], [])
    assert rows == [(T("2026-10-15", "09:15"), 2, 3, 1, 2.5, 7, 0, 0)]


def test_backfill_window_excludes_open_session():
    assert fetch_window(T("2026-10-19", "11:00"), 60)[1] == dt.date(2026, 10, 18)   # Monday, market open
    assert fetch_window(T("2026-10-19", "16:00"), 60)[1] == dt.date(2026, 10, 19)   # after 15:35
    assert fetch_window(T("2026-10-18", "11:00"), 60)[1] == dt.date(2026, 10, 18)   # Sunday
    frm, to = fetch_window(T("2026-10-19", "16:00"), 60)
    assert (to - frm).days == 60


def test_rebuild_daily_ref_sql_mentions_write_once_window():
    assert "ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING" in REBUILD_DAILY_REF_SQL
    assert "ON CONFLICT (day, symbol) DO UPDATE" in REBUILD_DAILY_REF_SQL
    assert "prev_close IS NOT NULL" in REBUILD_DAILY_REF_SQL


def test_universe_upsert_sql_keys_on_symbol():
    from backfill import UPSERT_INSTRUMENTS
    assert "ON CONFLICT(symbol) DO UPDATE" in UPSERT_INSTRUMENTS and "lot_size=EXCLUDED.lot_size" in UPSERT_INSTRUMENTS
