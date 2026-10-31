import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
from backtest_odds import calibration, fmt_note


def test_calibration_buckets_and_verdicts():
    rows = [{"odds": {"mc": {"target_pct": 35.0}, "verdict": "coin"}, "outcome": {"hit": "target"}},
            {"odds": {"mc": {"target_pct": 38.0}, "verdict": "coin"}, "outcome": {"hit": "stop"}},
            {"odds": {"mc": {"target_pct": 80.0}, "verdict": "edge"}, "outcome": {"hit": "none"}},
            {"odds": None, "outcome": {"hit": "target"}}]
    c = calibration(rows)
    d = {b["bucket"]: b for b in c["deciles"]}
    assert d["30-40"]["n"] == 2 and d["30-40"]["predicted"] == 36.5 and d["30-40"]["realised"] == 50.0
    assert c["by_verdict"]["coin"]["n"] == 2 and c["by_verdict"]["edge"]["none_pct"] == 100.0
    assert "| 30-40 | 2 | 36.5 | 50.0 |" in fmt_note(c, 42, 4)
