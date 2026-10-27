import json, time, pathlib
import pandas as pd, pytest
from core.backtest import fires, fires_naive, max_offset, outcome
from conftest import synthetic_day

ROOT = pathlib.Path(__file__).resolve().parents[1]
BOUNCE = json.load(open(ROOT / "scans/bounce_failure_short.json"))
VWAP_LONG = json.load(open(ROOT / "scans/vwap_reclaim_long.json"))
ALWAYS_1M = {"name": "always", "timeframe": "1m", "match": "all",
             "rules": [{"lhs": {"off": 0, "field": "close"}, "op": ">", "rhs": {"number": 0}}]}


def ctx_for(d, scan=None):
    """prev close just below the open for long scans, just above for short ones, so both can fire."""
    c0 = float(d.close.iloc[0])
    k = 1.005 if scan is BOUNCE else 0.995
    return {"prev_day_close": c0 * k, "prev_day_high": c0 * 1.03, "prev_day_low": c0 * 0.97}


@pytest.mark.parametrize("scan", [BOUNCE, VWAP_LONG], ids=["bounce", "vwap"])
@pytest.mark.parametrize("seed", range(6))
def test_fires_equals_naive(scan, seed):
    d = synthetic_day(seed=seed, base_price=1200.0)   # > 1000 so the bounce scan's price filter can pass
    ctx = ctx_for(d, scan)
    got, ref = fires(scan, d, ctx, cooldown_min=0), fires_naive(scan, d, ctx, cooldown_min=0)
    assert got == ref


@pytest.mark.parametrize("scan", [BOUNCE, VWAP_LONG], ids=["bounce", "vwap"])
def test_naive_actually_fires_sometimes(scan):
    # guards the equivalence test against vacuous [] == []
    days = [synthetic_day(seed=s, base_price=1200.0) for s in range(6)]
    assert sum(len(fires_naive(scan, d, ctx_for(d, scan), 0)) for d in days) > 0


def test_cooldown_suppresses_refires():
    out = fires(ALWAYS_1M, synthetic_day(), {}, cooldown_min=30)
    assert len(out) == 13 and (out[1] - out[0]) == pd.Timedelta(minutes=30)


def test_off0_is_stub_not_completed_bar():
    scan = {"name": "x", "timeframe": "1m", "match": "all",
            "rules": [{"lhs": {"off": 0, "field": "high"}, "op": ">", "rhs": {"off": 0, "field": "open"}}]}
    assert fires(scan, synthetic_day(), {}, 0) == []


def test_max_offset():
    assert max_offset(BOUNCE) == 3 and max_offset(ALWAYS_1M) == 0


def test_fires_perf_budget():
    d = synthetic_day(base_price=1200.0)
    t0 = time.perf_counter()
    fires(BOUNCE, d, ctx_for(d, BOUNCE), 30)
    assert time.perf_counter() - t0 < 0.25


def test_outcome_long_target_first_and_returns():
    d = synthetic_day()
    at = d.ts.iloc[100]
    entry = float(d.open.iloc[100])
    later = d[d.ts > at]
    o = outcome(d, at, "long", entry, stop=entry * 0.5, target=float(later.high.max()))
    assert o["hit"] == "target"
    assert abs(o["r30"] - (float(d.close.iloc[130]) / entry - 1) * 100) < 1e-9


def test_outcome_short_sign_and_none_near_close():
    d = synthetic_day()
    at = d.ts.iloc[370]
    entry = float(d.open.iloc[370])
    o = outcome(d, at, "short", entry, stop=entry * 2, target=entry * 0.5)
    assert o["hit"] == "none" and o["r15"] is None and o["r30"] is None and o["r60"] is None


def test_outcome_short_return_sign_is_favourable_when_price_falls():
    d = synthetic_day()
    d["close"] = 100.0
    d.loc[d.index >= 115, "close"] = 99.0
    at = d.ts.iloc[100]
    o = outcome(d, at, "short", 100.0, stop=1000.0, target=0.0)
    assert o["r15"] > 0


def test_time_to_target_min():
    from core.backtest import time_to_target_min
    d = synthetic_day()
    d["high"] = d.close + 0.01
    at = d.ts.iloc[100]
    d.loc[d.index == 112, "high"] = 10_000.0                       # target touched 12 minutes later
    assert time_to_target_min(d, at, "long", 9_000.0) == 12
    assert time_to_target_min(d, at, "long", 20_000.0) is None
    d.loc[d.index == 105, "low"] = 0.5                              # short: low <= target
    assert time_to_target_min(d, at, "short", 1.0) == 5


def test_grade_pattern_untriggered_is_none_and_triggered_has_tt():
    from core.backtest import grade_pattern
    d = synthetic_day()
    assert grade_pattern(d, {"triggered_at": None}) is None
    at = d.ts.iloc[100]; entry = float(d.close.iloc[100])
    g = grade_pattern(d, {"triggered_at": at, "side": "long", "entry": entry, "stop": entry * 0.5, "target": float(d.high.iloc[101:].max())})
    assert g["hit"] == "target" and isinstance(g["tt_min"], int) and g["tt_min"] >= 1


def test_fast_replay_sees_enough_bars_for_a_moving_average(day):
    from core.backtest import fires, fires_naive, max_offset
    scan = {"timeframe": "5m", "match": "all",
            "rules": [{"lhs": {"off": 1, "field": "close"}, "op": "<=", "rhs": {"off": 1, "field": "close", "sma": 13}},
                      {"lhs": {"off": 2, "field": "close"}, "op": ">=", "rhs": {"off": 1, "field": "close"}}]}
    assert max_offset(scan) == 13
    ctx = {"prev_day_close": 100, "prev_day_high": 101, "prev_day_low": 99}
    got, ref = fires(scan, day, ctx, cooldown_min=0), fires_naive(scan, day, ctx, cooldown_min=0)
    assert got == ref and len(got) > 0
    ema = {"timeframe": "5m", "rules": [{"lhs": {"off": 0, "field": "close"}, "op": ">", "rhs": {"off": 0, "field": "close", "ema": 5}}]}
    assert fires(ema, day, ctx, cooldown_min=0) == fires_naive(ema, day, ctx, cooldown_min=0)


@pytest.mark.parametrize("seed", range(4))
def test_fast_replay_sees_the_whole_day_for_a_day_series(seed):
    """`day_low` needs every bar of the day, not just the rule's offsets: the fast path must match the naive one."""
    at_low = {"name": "at the low", "timeframe": "5m", "match": "all",
              "rules": [{"lhs": {"series": "day_low"}, "op": "==", "rhs": {"off": 0, "field": "low"}},
                        {"lhs": {"off": 0, "field": "volume"}, "op": ">", "rhs": {"off": 1, "field": "volume", "sma": 5, "mul": 1.2}}]}
    assert max_offset(at_low) == 10_000
    d = synthetic_day(seed=seed, base_price=1200.0)
    got, ref = fires(at_low, d, {}, cooldown_min=0), fires_naive(at_low, d, {}, cooldown_min=0)
    assert got == ref


def test_day_series_scan_fires_sometimes():
    at_low = {"name": "at the low", "timeframe": "5m", "match": "all",
              "rules": [{"lhs": {"series": "day_low"}, "op": "==", "rhs": {"off": 0, "field": "low"}}]}
    assert sum(len(fires_naive(at_low, synthetic_day(seed=s, base_price=1200.0), {}, 0)) for s in range(4)) > 0
