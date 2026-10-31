import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
from backtest_odds import calibration, fmt_note


def test_calibration_buckets_and_verdicts():
    rows = [{"odds": {"mc": {"target_pct": 35.0}, "verdict": "coin"}, "outcome": {"hit": "target"}},
            {"odds": {"mc": {"target_pct": 38.0}, "verdict": "coin"}, "outcome": {"hit": "stop"}},
            {"odds": {"mc": {"target_pct": 80.0}, "verdict": "edge"}, "outcome": {"hit": "none"}},
            {"odds": None, "outcome": {"hit": "target"}},
            {"odds": {"mc": {"target_pct": 85.0}, "verdict": "edge"}, "outcome": None}]                 # ungraded
    c = calibration(rows)
    d = {b["bucket"]: b for b in c["deciles"]}
    assert d["30-40"]["n"] == 2 and d["30-40"]["predicted"] == 36.5 and d["30-40"]["realised"] == 50.0
    assert d["80-90"]["n"] == 2 and d["80-90"]["realised"] is None                                    # undecided rows stay out of realised
    assert c["by_verdict"]["coin"]["n"] == 2 and c["by_verdict"]["edge"] == {"n": 1, "target_pct": 0.0, "stop_pct": 0.0, "none_pct": 100.0}
    assert c["ungraded"] == 1 and c["no_odds"] == 1
    note = fmt_note(c, 42, 5)
    assert "| 30-40 | 2 | 36.5 | 50.0 |" in note and "1 ungraded, left out of the verdict table" in note
