import datetime as dt
import pytest
from core.ranking import rank, TOP_N
from conftest import synthetic_day

NOW = dt.datetime(2026, 10, 16, 10, 30, tzinfo=dt.timezone(dt.timedelta(hours=5, minutes=30)))


@pytest.fixture
def three_symbol_frames():
    return {s: synthetic_day(seed=i, base_price=p) for i, (s, p) in enumerate([("A", 100.0), ("B", 2000.0), ("C", 550.0)], start=1)}


@pytest.fixture
def refs(three_symbol_frames):
    out = {}
    for i, (s, f) in enumerate(three_symbol_frames.items()):
        pc = float(f.open.iloc[0]) * (0.99 if i % 2 else 1.01)
        out[s] = {"prev_close": pc, "prev_high": pc * 1.02, "prev_low": pc * 0.98, "prev_oi": 990_000,
                  "avg_vol_1m": 2500, "sector": "IT" if s != "C" else "BANK"}
    return out


def test_rank_matches_engine_formulas(three_symbol_frames, refs):
    snap = rank(three_symbol_frames, refs, ts=NOW)
    rows = snap["bulls"] + snap["bears"]
    assert [r["rank"] for r in sorted(rows, key=lambda r: -r["score"])] == [1, 2, 3]
    top = max(rows, key=lambda r: r["score"])
    assert top["pctile"] == 100 and top["conv"] == 99
    assert all(r["picked"] == (r["side_rank"] <= TOP_N and r["rr"] >= 1.5) for r in rows)
    assert snap["n_bull"] + snap["n_bear"] == 3 and snap["mode"] == "live" and snap["ts"] == NOW.isoformat()
    assert set(snap["sectors"]) == {"IT", "BANK"}


def test_rank_skips_symbol_without_ref(three_symbol_frames, refs):
    refs = {k: v for k, v in refs.items() if k != "B"}
    snap = rank(three_symbol_frames, refs, ts=NOW)
    assert {r["symbol"] for r in snap["bulls"] + snap["bears"]} == {"A", "C"}


def test_rank_delta_available_false_when_all_zero(three_symbol_frames, refs):
    assert rank(three_symbol_frames, refs, ts=NOW)["delta_available"] is True
    for f in three_symbol_frames.values():
        f["delta"] = 0
    assert rank(three_symbol_frames, refs, ts=NOW, mode="replay")["delta_available"] is False


def test_rank_returns_none_without_rows():
    assert rank({}, {}, ts=NOW) is None
