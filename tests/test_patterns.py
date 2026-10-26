import time
import pandas as pd, pytest
from core.patterns import DEFAULTS, detector_hash, bars5m, swings, detect_day, as_of
from conftest import shaped_day, synthetic_day, IST

# the synthetic shapes are compact (lows 4 bars apart); test the mechanics with the looser geometry
P = {**DEFAULTS, "min_sep": 4, "min_retrace": 0.0035, "min_extension": 0.0, "breakout_vol": 0}   # mechanics with the volume gate off

# 5m closes in % vs base. L1 at bar 3 (-2.0), neckline bar 5 (-0.9), L2 at bar 7 (-1.98),
# L2 confirmed at bar 9, trigger bar 10 (-0.7 > -0.9), then up to the target.
BOTTOM = [0.0, -0.6, -1.3, -2.0, -1.5, -0.9, -1.4, -1.98, -1.6, -1.2, -0.7, -0.3, 0.1, 0.6, 0.9]
TOP = [-x for x in BOTTOM]
FLAT_OI = [1_000_000] * len(BOTTOM)
FALLING_OI = [1_000_000] * 4 + [990_000] * 3 + [975_000] * 8          # lower at L2 than at L1
VOL = [5000] * 10 + [16000] + [5000] * 4                              # 3× on the trigger bar


def at_bar(i):   # close minute of 5m bar i
    return pd.Timestamp("2026-10-16 09:15", tz=IST) + pd.Timedelta(minutes=5 * i + 5)


def one(pats, kind):
    xs = [p for p in pats if p["kind"] == kind]
    assert len(xs) == 1, [p["kind"] for p in pats]
    return xs[0]


def test_bars5m_complete_only_and_close_at(day):
    b = bars5m(day.iloc[:33], prev_close=100.0)          # 33 minutes → 6 complete bars, the 7th partial
    assert len(b) == 6 and b.close_at.iloc[0] == at_bar(0) and "avg_vol" in b and "day_pct" in b


def test_swings_confirmed_two_bars_later():
    d, pc = shaped_day(BOTTOM)
    s = swings(bars5m(d, pc), k=2)
    lows = [(i, c) for i, kind, c in s if kind == "low"]
    assert (3, 5) in lows and (7, 9) in lows
    assert (5, 7) in [(i, c) for i, kind, c in s if kind == "high"]


def test_double_bottom_detected_with_levels():
    d, pc = shaped_day(BOTTOM, oi=FALLING_OI, vol=VOL)
    p = one(detect_day(d, pc, P), "double_bottom")
    b = bars5m(d, pc)
    assert p["side"] == "long"
    p1, p2 = p["points"]
    assert p1["idx"] == 3 and p2["idx"] == 7
    assert abs(p2["price"] - p1["price"]) / p1["price"] <= DEFAULTS["tol"]
    assert p["neckline"] == pytest.approx(float(b.high.iloc[4:7].max()))
    assert p["stop"] == pytest.approx(min(p1["price"], p2["price"]))
    assert p["target"] == pytest.approx(p["neckline"] + 1.5 * (p["neckline"] - p["stop"])) and p["facts"]["target_basis"] == "1.5x"   # no pivots given
    assert p["formed_at"] == at_bar(9)
    assert p["triggered_at"] == at_bar(10) and p["entry"] == pytest.approx(float(b.close.iloc[10]))
    assert p["failed_at"] is None and p["abandoned_at"] is None


def test_lower_low_is_not_a_double_bottom():
    shape = list(BOTTOM); shape[7] = -3.0
    assert not [p for p in detect_day(*shaped_day(shape), P) if p["kind"] == "double_bottom" and p["points"][0]["idx"] == 3]


def test_triple_extends_and_supersedes():
    shape = BOTTOM[:10] + [-1.3, -2.01, -1.5, -1.0, -0.6, -0.2, 0.3, 0.8]   # third low at bar 11, trigger at 14
    pats = detect_day(*shaped_day(shape), P)
    dbl = one(pats, "double_bottom"); tri = one(pats, "triple_bottom")
    assert [q["idx"] for q in tri["points"]] == [3, 7, 11]
    assert tri["formed_at"] == at_bar(13) and dbl["superseded_at"] == at_bar(13)
    assert dbl["triggered_at"] is None
    assert tri["triggered_at"] == at_bar(14)


def test_top_mirrors():
    d, pc = shaped_day(TOP)
    p = one(detect_day(d, pc, P), "double_top")
    b = bars5m(d, pc)
    assert p["side"] == "short"
    assert p["neckline"] == pytest.approx(float(b.low.iloc[4:7].min()))
    assert p["stop"] == pytest.approx(max(q["price"] for q in p["points"]))
    assert p["target"] == pytest.approx(p["neckline"] - 1.5 * (p["stop"] - p["neckline"]))
    assert p["triggered_at"] == at_bar(10)


def test_failed_within_3_bars_and_not_after():
    fail_shape = BOTTOM[:11] + [-1.1, -1.3, -1.4, -1.5]        # back below the neckline the bar after trigger
    p = one(detect_day(*shaped_day(fail_shape), P), "double_bottom")
    assert p["triggered_at"] == at_bar(10) and p["failed_at"] == at_bar(11)
    late_shape = BOTTOM[:11] + [-0.6, -0.5, -0.4, -0.3, -1.2]  # back below only on the 5th bar after trigger
    p = one(detect_day(*shaped_day(late_shape), P), "double_bottom")
    assert p["failed_at"] is None


def test_abandoned_before_trigger():
    shape = BOTTOM[:10] + [-2.6, -2.7, -2.8]
    d, pc = shaped_day(shape)
    p = one(detect_day(d, pc, P), "double_bottom")
    assert p["abandoned_at"] == at_bar(10) and p["triggered_at"] is None
    assert as_of([p], at_bar(11), {"sector_pct": 0, "bars": bars5m(d, pc)}) == []
    assert len(as_of([p], at_bar(9), {"sector_pct": 0, "bars": bars5m(d, pc)})) == 1


def test_detect_with_gaps_uses_bar_positions():
    shape = [0.0, -0.6, -1.3, -2.0, -1.7, -1.5, -1.2, -0.9, -1.3, -1.6, -1.99, -1.6, -1.2, -0.7, -0.2, 0.3]
    d, pc = shaped_day(shape, drop_bars=(5, 8))
    p = one(detect_day(d, pc, P), "double_bottom")
    assert [q["idx"] for q in p["points"]] == [3, 8]                    # positions after the drops
    assert p["points"][1]["ts"] == pd.Timestamp("2026-10-16 10:05", tz=IST)   # original bar 10's clock time


def test_forming_at_close_is_kept():
    d, pc = shaped_day(BOTTOM[:10])                                      # day ends right after L2 confirms
    p = one(detect_day(d, pc, P), "double_bottom")
    assert p["triggered_at"] is None
    rows = as_of([p], d.ts.iloc[-1] + pd.Timedelta(minutes=1), {"sector_pct": 0, "bars": bars5m(d, pc)})
    assert rows and rows[0]["state"] == "forming"


def test_as_of_state_transitions():
    fail_shape = BOTTOM[:11] + [-1.1, -1.3, -1.4, -1.5]
    d, pc = shaped_day(fail_shape)
    p = one(detect_day(d, pc, P), "double_bottom"); ctx = {"sector_pct": 0, "bars": bars5m(d, pc)}
    assert as_of([p], at_bar(8), ctx) == []
    assert as_of([p], at_bar(9), ctx)[0]["state"] == "forming"
    assert as_of([p], at_bar(10), ctx)[0]["state"] == "triggered"
    assert as_of([p], at_bar(11), ctx)[0]["state"] == "failed"


def key(r):
    return (r["kind"], tuple(str(q["ts"]) for q in r["points"]), round(r["neckline"], 6), r["state"], r["score"],
            None if r["entry"] is None else round(r["entry"], 6), round(r["stop"], 6), round(r["target"], 6), str(r["failed_at"]))


@pytest.mark.parametrize("seed", range(5))
def test_as_of_never_uses_future(seed):
    import math
    d = synthetic_day(seed=seed, base_price=500.0)
    d["close"] = d.close * (1 + 0.01 * pd.Series(range(len(d))).mul(0.09).apply(math.sin))
    d["high"] = d[["open", "close"]].max(axis=1) + 0.05; d["low"] = d[["open", "close"]].min(axis=1) - 0.05
    rng = __import__("numpy").random.default_rng(seed)
    d = d.drop(index=rng.choice(d.index[5:-5], size=8, replace=False)).reset_index(drop=True)   # thin names skip minutes
    pc = float(d.open.iloc[0])
    full = detect_day(d, pc, P)
    for m in range(20, 360, 5):
        at = pd.Timestamp("2026-10-16 09:15", tz=IST) + pd.Timedelta(minutes=m)
        cut = d[d.ts < at]
        a = sorted(key(r) for r in as_of(full, at, {"sector_pct": 0, "bars": bars5m(d, pc)}))
        b = sorted(key(r) for r in as_of(detect_day(cut, pc, P, at=at), at, {"sector_pct": 0, "bars": bars5m(cut, pc, at=at)}))
        assert a == b, m


def test_bars5m_clock_rule_with_at():
    d = synthetic_day(with_gap_at=["10:04"])                     # the 10:00 bar's last minute is missing
    at = pd.Timestamp("2026-10-16 10:05", tz=IST)
    cut = d[d.ts < at]
    assert bars5m(cut, 100.0).ts.iloc[-1] < pd.Timestamp("2026-10-16 10:00", tz=IST)     # data rule: not complete yet
    b = bars5m(cut, 100.0, at=at)
    assert b.ts.iloc[-1] == pd.Timestamp("2026-10-16 10:00", tz=IST) and b.close_at.iloc[-1] == at   # clock rule: complete at 10:05


def test_forming_checks_frozen_at_formation():
    d, pc = shaped_day(BOTTOM[:10] + [-1.3, -1.2, -1.1], oi=FALLING_OI + [975_000] * 3, vol=VOL + [5000] * 3)
    p = one(detect_day(d, pc, P), "double_bottom")
    ctx = {"sector_pct": 0.5, "bars": bars5m(d, pc)}
    first = as_of([p], p["formed_at"], ctx)[0]["checks"][:5]
    later = as_of([p], p["formed_at"] + pd.Timedelta(minutes=15), ctx)[0]["checks"][:5]
    assert first == later


def test_score_without_oi():
    d, pc = shaped_day(BOTTOM, oi=[0] * len(BOTTOM), vol=VOL)
    p = one(detect_day(d, pc, P), "double_bottom")
    r = as_of([p], at_bar(10), {"sector_pct": 0.5, "bars": bars5m(d, pc)})[0]
    assert r["checks"][1] is False and r["score"] <= 8


def test_score_checks_order_and_count():
    d, pc = shaped_day(BOTTOM, oi=FALLING_OI, vol=VOL)
    p = one(detect_day(d, pc, P), "double_bottom")
    r = as_of([p], at_bar(10), {"sector_pct": 0.5, "bars": bars5m(d, pc)})[0]
    # equal volume around both lows: no exhaustion; synthetic delta is 0: no delta tick
    assert r["checks"] == [True, True, True, True, True, False, True, False, False, True] and r["score"] == 6 and r["rr"] > 0   # no breadth given: tape tick false
    f = as_of([p], at_bar(9), {"sector_pct": 0.5, "bars": bars5m(d, pc)})[0]
    assert f["state"] == "forming" and f["checks"][9] is False and f["checks"][6] is False
    rb = as_of([p], at_bar(10), {"sector_pct": 0.5, "breadth": 0.6})[0]
    assert rb["checks"][8] is True and rb["score"] == 7


def test_detector_hash_changes_with_params():
    assert detector_hash() != detector_hash({**DEFAULTS, "tol": 0.005}) and DEFAULTS["min_sep"] == 6 and DEFAULTS["min_extension"] == 1.0 and len(detector_hash()) == 12


def test_perf_30_symbol_days():
    days = [synthetic_day(seed=s, base_price=800.0) for s in range(30)]
    t0 = time.perf_counter()
    for d in days:
        detect_day(d, float(d.open.iloc[0]))
    assert time.perf_counter() - t0 < 0.3


def test_min_extension_filters_shallow_days():
    d, pc = shaped_day(BOTTOM, oi=FALLING_OI, vol=VOL)              # day move at L2 is about -2%
    assert one(detect_day(d, pc, {**P, "min_extension": 1.0}), "double_bottom")
    assert not [p for p in detect_day(d, pc, {**P, "min_extension": 3.0}) if p["kind"] == "double_bottom"]
    assert "min_extension" in DEFAULTS


def test_successive_second_lows_supersede_instead_of_duplicating():
    # L1 at bar 3; L2 at bar 7 slightly above L1; a later low at bar 13 slightly below L1 (within TOL of L1,
    # not of L2). The (L1, L2) double must be superseded by (L1, L3); only one live double remains.
    shape = [0.0, -0.6, -1.3, -2.0, -1.5, -0.9, -1.4, -1.7, -1.3, -1.0, -1.4, -1.7, -1.9, -2.3, -1.8, -1.4, -1.0, -0.7, -0.3]
    shape[7] = -1.7            # L2 = L1 + 0.3%
    shape[13] = -2.3           # L3 = L1 - 0.3%  (0.6% below L2 → not a triple)
    pats = [p for p in detect_day(*shaped_day(shape), P) if p["side"] == "long"]
    live = [p for p in pats if p["superseded_at"] is None]
    assert len(live) == 1 and [q["idx"] for q in live[0]["points"]] == [3, 13]
    old = [p for p in pats if p["superseded_at"] is not None]
    assert len(old) == 1 and [q["idx"] for q in old[0]["points"]] == [3, 7] and old[0]["superseded_at"] == live[0]["formed_at"]
