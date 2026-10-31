import pandas as pd
from conftest import synthetic_day, shaped_day
from core import timemachine as tm
from core.backtest import outcome

def hist_of(n=20, **kw):
    return tm.history({f"2026-09-{d:02d}": synthetic_day(seed=d, day=f"2026-09-{d:02d}", **kw) for d in range(1, n + 1)})

def test_trigger_bar_and_bars5():
    assert tm.trigger_bar(pd.Timestamp("2026-10-28 10:20", tz="Asia/Kolkata")) == "10:15"
    assert tm.trigger_bar(pd.Timestamp("2026-10-28 10:20:05", tz="Asia/Kolkata")) == "10:15"
    assert tm.trigger_bar(pd.Timestamp("2026-10-28 09:20", tz="Asia/Kolkata")) == "09:15"
    b = tm.bars5(synthetic_day())
    assert list(b)[:2] == ["09:15", "09:20"] and len(b) == 75 and len(b["09:15"]) == 4

def test_seeded_and_deterministic():
    h = hist_of()
    a = tm.monte_carlo(h, "10:15", "long", 100.0, 99.0, 102.0)
    b = tm.monte_carlo(h, "10:15", "long", 100.0, 99.0, 102.0)
    assert a == b and a["paths"] == 4000 and a["sessions"] == 20 and a["bars_left"] == 60
    assert abs(a["target_pct"] + a["stop_pct"] + a["none_pct"] - 100) < 0.2

def test_no_future_leak():
    h = hist_of()
    wild = {"2026-10-28": synthetic_day(seed=99, day="2026-10-28", base_price=100.0)}
    wild["2026-10-28"].loc[:, ["high"]] = 1000.0                  # a leak would push target_pct to ~100
    plan = dict(symbol="X", day="2026-10-28", at="10:15", side="long", entry=100.0, stop=99.0, target=102.0)
    assert tm.replay(plan, h) == tm.replay(plan, {**h, **tm.history(wild)})

def test_both_touched_is_stop_and_target_inside_first_bar():
    h = hist_of()
    tight = tm.monte_carlo(h, "10:15", "long", 100.0, 99.999, 100.001)    # both inside almost every bar → stop
    assert tight["stop_pct"] > 97.0
    easy = tm.monte_carlo(h, "10:15", "long", 100.0, 90.0, 100.001)      # target inside the first bar's range, stop far away
    assert easy["target_pct"] > 98.0

def test_after_flat_by_is_flat_and_empty_history_is_none():
    h = hist_of()
    late = tm.monte_carlo(h, "15:15", "long", 100.0, 99.0, 102.0)
    assert late == {**late, "bars_left": 0, "none_pct": 100.0, "target_pct": 0.0, "stop_pct": 0.0}
    assert tm.verdict(late, 2.0)[0] == "flat"
    assert tm.replay(dict(symbol="X", day="2026-08-01", at="10:15", side="long", entry=100, stop=99, target=102), h) is None

def test_verdict_words_and_sentences():
    assert tm.verdict({"none_pct": 75.0, "target_pct": 10.0, "stop_pct": 15.0, "avg_r": 0.1}, 1.0)[0] == "flat"
    w, s = tm.verdict({"none_pct": 19.1, "target_pct": 39.4, "stop_pct": 41.5, "avg_r": -0.05}, 0.84)
    assert w == "coin" and s == "Close to a coin flip: T1 first 39.4 %, stop first 41.5 %, average -0.05 R."
    w, s = tm.verdict({"none_pct": 66.1, "target_pct": 29.8, "stop_pct": 4.1, "avg_r": 0.52}, 0.7)
    assert w == "edge" and s == "Odds favour T1: 29.8 % against 4.1 %, average 0.52 R."
    assert tm.verdict({"none_pct": 10.0, "target_pct": 20.0, "stop_pct": 70.0, "avg_r": -0.6}, 1.0) == ("bad", "Odds favour the stop: 70.0 % against 20.0 %.")
    assert "will" not in s.lower()

def test_past_sessions_agree_with_backtest_outcome():
    df, _ = shaped_day([0, 0.2, 0.4, 0.3, 1.0, 1.8, 2.5, 2.2], day="2026-09-05")   # one clean session: long from the 09:30 bar reaches +2 %
    h = tm.history({"2026-09-05": df})
    at = "09:30"; e = h["2026-09-05"][at][3]
    ps = tm.past_sessions(h, at, "long", e, e * 0.995, e * 1.02)
    assert ps["target"] == 1 and ps["stop"] == 0 and ps["last"][0][0] == "2026-09-05" and ps["last"][0][1] == "target"
    o = outcome(df, pd.Timestamp("2026-09-05 09:34", tz="Asia/Kolkata"), "long", e, e * 0.995, e * 1.02)
    assert o["hit"] == "target"
