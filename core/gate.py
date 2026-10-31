"""The day gate and the ledger: what the owner's day rules allow, and what the day has cost.

Pure functions, no I/O. Rows are JSON-safe `trade_rows` dicts; a row counts in the ledger
only when it was logged as a paper trade (`paper` not None), whatever its gate says."""
import pandas as pd

from core.backtest import outcome

IST = "Asia/Kolkata"

_NO_SHARES = {"zero_distance": "stop equals entry", "delivery_no_short": "delivery: no shorts",
              "position_cap": "position cap leaves no shares", "max_loss": "max loss leaves no shares"}


def ledger(rows: list[dict], settings: dict) -> dict:
    """The day so far from the rows cut at the as-of minute."""
    committed = realised = losses = 0.0
    taken = blocked_total = blocked_won = 0
    open_: dict[str, str] = {}
    for r in rows:
        out = r.get("outcome")
        if r.get("gate") == "BLOCK" and r.get("paper") is None:   # a BLOCK he overrode and logged is a taken trade, not a saved one
            blocked_total += 1
            if out and out.get("hit") == "target":
                blocked_won += 1
        if r.get("paper") is None:
            continue
        taken += 1
        if out is None:
            committed += r["size"]["risk_inr"]
            open_[r["symbol"]] = r["ts"][11:16]
        else:
            pnl = out["pnl_inr"]
            realised += pnl
            if pnl < 0:
                losses -= pnl
    day_loss = settings["day_loss"]
    closed = taken >= settings["max_trades"] or committed + losses + settings["max_loss"] > day_loss
    return {"committed": committed, "realised": realised, "realised_losses": losses, "taken": taken,
            "open": open_, "blocked_total": blocked_total, "blocked_won": blocked_won,
            "gate_state": "CLOSED" if closed else "OPEN", "room_inr": max(day_loss - committed - losses, 0)}


def gate(row: dict, state: dict, settings: dict) -> tuple[str, str]:
    """PASS or BLOCK for a new trigger, with the first rule that stops it."""
    if state["taken"] >= settings["max_trades"]:
        return "BLOCK", f"{state['taken']} trades today"
    used = state["committed"] + state["realised_losses"]
    if used + row["size"]["risk_inr"] > settings["day_loss"]:
        return "BLOCK", f"₹{used:,.0f} of ₹{settings['day_loss']:,.0f} day risk committed"
    if row["size"]["shares"] == 0:
        return "BLOCK", _NO_SHARES[row["size"]["limited_by"]]
    if row["symbol"] in state["open"]:
        return "BLOCK", f"already in {row['symbol']} since {state['open'][row['symbol']]}"
    return "PASS", ""


def settle(row: dict, df1m: pd.DataFrame, flat_by: str, final: bool) -> dict | None:
    """Grade a trade on 1-minute bars up to `flat_by`; None while it is still open."""
    at = pd.Timestamp(row["ts"]).tz_convert(IST)
    cut = pd.Timestamp(f"{row['ts'][:10]} {flat_by}", tz=IST)
    side, entry, shares = row["side"], float(row["entry"]), row["size"]["shares"]
    stop, target = float(row["stop"]), float(row["target"])
    sign = 1 if side == "long" else -1

    def done(o: dict, exit_px: float, settled: pd.Timestamp) -> dict:
        pnl = round(shares * sign * (exit_px - entry), 2) if shares else 0.0
        return {**o, "exit_px": exit_px, "pnl_inr": pnl, "settled_at": settled.isoformat()}

    if at >= cut:
        return done({"r15": None, "r30": None, "r60": None, "hit": "none"}, entry, at)
    cutdata = df1m[df1m.ts < cut]
    o = outcome(cutdata, at, side, entry, stop, target)
    after = cutdata[cutdata.ts > at]
    if o["hit"] != "none":
        level = target if o["hit"] == "target" else stop
        # the level is touched by the high (long target, short stop) or by the low (long stop, short target)
        touched = after.high >= level if (o["hit"] == "target") == (side == "long") else after.low <= level
        return done(o, level, after.ts[touched].iloc[0] + pd.Timedelta(minutes=1))   # the touching bar's close
    reached = not cutdata.empty and cutdata.ts.iloc[-1] >= cut - pd.Timedelta(minutes=1)
    if not (final or reached):
        return None
    return done(o, float(after.close.iloc[-1]) if not after.empty else entry, cut)
