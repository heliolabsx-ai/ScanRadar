import sys, pathlib, datetime as dt
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
from backtest_patterns import summarise, UPSERT_SQL, row_to_record

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def row(kind, hit, r30, tt, score=3, failed=False, triggered=True):
    return {"kind": kind, "side": "long" if "bottom" in kind else "short", "score": score,
            "triggered_at": dt.datetime(2026, 10, 16, 10, 0, tzinfo=IST) if triggered else None,
            "failed_at": dt.datetime(2026, 10, 16, 10, 10, tzinfo=IST) if failed else None,
            "outcome": ({"hit": hit, "r30": r30, "r60": r30, "tt_min": tt} if triggered else None)}


def test_summarise_per_kind_with_median_and_score_split():
    rows = [row("double_bottom", "target", 1.0, 10, score=4), row("double_bottom", "stop", -1.0, None, score=2),
            row("double_bottom", "target", 0.5, 30, score=3, failed=True), row("double_bottom", None, None, None, triggered=False),
            row("double_top", "none", 0.2, None, score=1)]
    s = summarise(rows)
    db = s["double_bottom"]
    assert db["triggers"] == 3 and db["forming_only"] == 1
    assert db["hit_rate"] == 2 / 3 * 100 and db["fail_rate"] == 1 / 3 * 100
    assert db["avg_r30"] == (1.0 - 1.0 + 0.5) / 3 and db["median_tt_min"] == 20
    assert db["by_score"][">=3"]["triggers"] == 2 and db["by_score"]["<3"]["triggers"] == 1
    assert s["double_top"]["hit_rate"] is None and s["double_top"]["triggers"] == 1


def test_upsert_sql_is_keyed_on_the_unique_key_and_updates_lifecycle():
    assert "ON CONFLICT (day, symbol, kind, formed_at) DO UPDATE SET" in UPSERT_SQL


def test_row_to_record_serialises_timestamps_and_points():
    import pandas as pd
    p = {"kind": "double_bottom", "side": "long", "formed_at": pd.Timestamp("2026-10-16 10:00", tz="Asia/Kolkata"),
         "triggered_at": None, "failed_at": None, "abandoned_at": None, "superseded_at": None, "neckline": 1.0,
         "entry": None, "stop": 0.9, "target": 1.1, "points": [{"idx": 3, "ts": pd.Timestamp("2026-10-16 09:30", tz="Asia/Kolkata"), "price": 0.9, "oi": 1.0}],
         "checks": [True] * 6, "score": 5, "outcome": None}
    rec = row_to_record(dt.date(2026, 10, 16), "X", p, "replay", "abc123")
    assert rec[:4] == (dt.date(2026, 10, 16), "X", "double_bottom", "5m")
    assert '"ts": "2026-10-16T09:30:00+05:30"' in rec[14]       # points JSON
    assert rec[-2:] == ("replay", "abc123")
