import pandas as pd
from conftest import shaped_day
from core.gate import ledger, gate, settle
from core.sizing import DEFAULTS

def row(sym, ts, risk, shares=100, logged=True, gate_="PASS", outcome=None):
    return {"symbol": sym, "ts": f"2026-10-28T{ts}:00+05:30", "side": "long", "entry": 100.0, "stop": 99.0, "target": 102.0,
            "size": {"shares": shares, "risk_inr": risk, "position_inr": shares * 100.0, "limited_by": "max_loss"},
            "gate": gate_, "gate_reason": "", "paper": {"logged_at": "x"} if logged else None, "outcome": outcome}

def test_day_risk_fills_up_and_blocks():            # the 28 Oct sequence from the mockup: three logged rows leave ₹34 of room
    rows = [row("AMBER", "10:55", 1992.0), row("KFINTECH", "11:05", 1980.0), row("DABUR", "10:30", 1994.0)]
    st = ledger(rows, DEFAULTS)
    assert st["committed"] == 5966.0 and st["taken"] == 3 and st["room_inr"] == 34.0 and st["gate_state"] == "CLOSED"
    assert gate(row("LT", "13:25", 1990.0, logged=False), st, DEFAULTS) == ("BLOCK", "₹5,966 of ₹6,000 day risk committed")

def test_count_cap_same_symbol_and_zero_shares():
    rows = [row(s, t, 500.0) for s, t in (("A", "09:30"), ("B", "09:35"), ("C", "09:40"), ("D", "09:45"), ("E", "09:50"))]
    assert gate(row("F", "10:00", 500.0, logged=False), ledger(rows, DEFAULTS), DEFAULTS) == ("BLOCK", "5 trades today")
    st = ledger([row("LT", "10:55", 500.0)], DEFAULTS)
    assert gate(row("LT", "11:30", 500.0, logged=False), st, DEFAULTS) == ("BLOCK", "already in LT since 10:55")
    z = row("X", "11:00", 0.0, shares=0, logged=False); z["size"]["limited_by"] = "zero_distance"
    assert gate(z, st, DEFAULTS) == ("BLOCK", "stop equals entry")
    assert gate(row("Y", "11:00", 500.0, logged=False), st, DEFAULTS) == ("PASS", "")

def test_settled_rows_move_to_realised_and_losses_count_against_room():
    won = row("A", "09:30", 1000.0, outcome={"hit": "target", "pnl_inr": 2000.0})
    lost = row("B", "09:40", 1000.0, outcome={"hit": "stop", "pnl_inr": -1000.0})
    blocked_won = row("C", "09:50", 1000.0, logged=False, gate_="BLOCK", outcome={"hit": "target", "pnl_inr": 1500.0})
    st = ledger([won, lost, blocked_won, row("D", "10:00", 1000.0)], DEFAULTS)
    assert st["committed"] == 1000.0 and st["realised"] == 1000.0 and st["realised_losses"] == 1000.0
    assert st["blocked_total"] == 1 and st["blocked_won"] == 1 and st["taken"] == 3 and st["open"] == {"D": "10:00"}

def test_logged_block_row_counts():
    st = ledger([row("A", "09:30", 1000.0, gate_="BLOCK")], DEFAULTS)
    assert st["taken"] == 1 and st["committed"] == 1000.0

def test_settle_target_stop_open_and_flat_by():
    df, _ = shaped_day([0, 0.5, 1.0, 1.5, 2.5, 2.0] + [2.0] * 69, day="2026-10-28")          # 75 bars to 15:29
    r = row("X", "09:20", 100.0, shares=10); r["entry"] = 100.0; r["stop"] = 99.0; r["target"] = 102.0
    s = settle(r, df, "15:20", final=False)
    assert s["hit"] == "target" and s["exit_px"] == 102.0 and s["pnl_inr"] == 20.0 and s["settled_at"].endswith("+05:30")
    r["target"] = 110.0
    assert settle(r, df[df.ts < pd.Timestamp("2026-10-28 12:00", tz="Asia/Kolkata")], "15:20", final=False) is None
    s = settle(r, df, "15:20", final=False)                      # the data reaches 15:19: flat at the last close before 15:20
    assert s["hit"] == "none" and s["settled_at"][11:16] == "15:20" and s["pnl_inr"] == round(10 * (s["exit_px"] - 100.0), 2)
    assert settle(r, df[df.ts < pd.Timestamp("2026-10-28 15:14", tz="Asia/Kolkata")], "15:20", final=True)["hit"] == "none"
    late = dict(r, ts="2026-10-28T15:25:00+05:30")
    assert settle(late, df, "15:20", final=False)["hit"] == "none"   # a trigger after flat_by settles at once
