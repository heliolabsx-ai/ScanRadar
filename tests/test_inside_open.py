# tests/test_inside_open.py: the Inside open page's data, assembled from hand-built rows (no DB, no Redis).
import datetime as dt
import pandas as pd
import pytest
import alerts
import inside_open as io
from conftest import ROOT
from core import rules

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
DAY = dt.date(2026, 11, 27)
SETTINGS = {"capital": 500000, "max_loss": 2000, "day_loss": 6000, "max_trades": 5, "position_cap": 0.6,
            "product": "intraday", "flat_by": "15:20"}


def _scans() -> dict:
    keep = alerts.SCAN_DIR
    alerts.SCAN_DIR = str(ROOT / "scans")
    try:
        return {s["name"]: s for s, _ in alerts.scan_files()}
    finally:
        alerts.SCAN_DIR = keep


SCANS = _scans()


def T(hhmm, day=DAY):
    h, m = hhmm.split(":")[:2]
    return dt.datetime(day.year, day.month, day.day, int(h), int(m), tzinfo=IST)


def alert(id, sym, side, price, stop, target, rank=1, of=1, star=False, strength=0.3, market="long", breadth=60.0,
          source="replay", group="Inside open"):
    return {"id": id, "ts": T("09:20").isoformat(), "scan": f"Inside open {side}", "symbol": sym, "price": price, "tf": "1m",
            "source": source, "side": side, "stop": stop, "target": target, "outcome": None, "story": None,
            "digest": {"group": group, "strength": strength, "rank": rank, "of": of, "star": star, "breadth": breadth, "market": market}}


def trade(id, sym, side, entry, stop, target, outcome=None, odds=None, shares=10):
    return {"id": id, "day": DAY.isoformat(), "ts": T("09:20").isoformat(), "ref": f"alert:{id}", "symbol": sym, "side": side,
            "source": "alert", "entry": entry, "stop": stop, "target": target, "target2": None,
            "rr": round(abs(target - entry) / abs(entry - stop), 2), "odds": odds,
            "size": {"shares": shares, "risk_inr": round(shares * abs(entry - stop), 2), "position_inr": round(shares * entry, 2),
                     "limited_by": "max_loss", "settings": dict(SETTINGS)},
            "gate": "PASS", "gate_reason": None, "outcome": outcome, "paper": None, "label": f"Inside open {side}"}


def settled(hit, px, hhmm):
    return {"r15": None, "r30": None, "r60": None, "hit": hit, "exit_px": px, "pnl_inr": 0.0, "settled_at": T(hhmm).isoformat()}


# 1-minute bars as the loader hands them over: (minute since 09:15, open, high, low, close, volume)
AAA = [(0, 2508, 2512, 2505, 2510, 100), (1, 2510, 2514, 2509, 2512, 100), (2, 2512, 2516, 2511, 2515, 100),
       (3, 2515, 2519, 2514, 2518, 100), (4, 2518, 2521, 2517, 2520, 100), (5, 2520, 2524, 2519, 2523, 100),
       (6, 2523, 2590, 2522, 2588, 100), (7, 2588, 2601, 2586, 2600, 100), (8, 2600, 2604, 2598, 2602, 100)]
BBB = [(0, 2998, 3005, 2994, 2996, 50), (1, 2996, 2998, 2992, 2994, 50), (2, 2994, 2996, 2990, 2992, 50),
       (3, 2992, 2994, 2989, 2991, 50), (4, 2991, 2992, 2988, 2990, 50), (5, 2990, 2993, 2987, 2989, 50),
       (6, 2989, 2992, 2986, 2988, 50), (7, 2988, 2990, 2985, 2987, 50), (8, 2987, 2994, 2986, 2993, 50),
       (9, 2993, 3006, 2992, 3004, 50), (10, 3004, 3008, 3001, 3003, 50)]
CCC = [(m, 4000 + m, 4003 + m, 3998 + m, 4001 + m, 10) for m in range(0, 12)]
REFS = {"AAA": {"prev_close": 2500.0, "prev_high": 2600.0, "prev_low": 2400.0, "sector": "IT"},
        "BBB": {"prev_close": 3000.0, "prev_high": 3100.0, "prev_low": 2900.0, "sector": "BANKS"},
        "CCC": {"prev_close": 3990.0, "prev_high": 4100.0, "prev_low": 3900.0, "sector": "OTHERS"}}
ALERTS = [alert(3, "CCC", "long", 4005.0, 3998.0, 4100.0, rank=2, of=2, strength=0.1),
          alert(2, "BBB", "short", 2990.0, 3005.0, 2900.0, rank=1, of=1, star=True, strength=0.2),
          alert(1, "AAA", "long", 2520.0, 2505.0, 2600.0, rank=1, of=2, star=True, strength=0.23)]
TRADES = [trade(1, "AAA", "long", 2520.0, 2505.0, 2600.0, outcome=settled("target", 2600.0, "09:23")),
          trade(2, "BBB", "short", 2990.0, 3005.0, 2900.0, outcome=settled("stop", 3005.0, "09:25")),
          trade(3, "CCC", "long", 4005.0, 3998.0, 4100.0, outcome=settled("none", 4012.0, "15:20"))]
BARS = {"AAA": AAA, "BBB": BBB, "CCC": CCC}


def run(**kw):
    args = dict(alerts=ALERTS, trade_rows=TRADES, bars=BARS, refs=REFS, funnel={"universe": 9, "others": [{"sym": "ZZZ", "why": "opened above PDH"}],
                "breadth": 55.5, "market": "long"}, settings=SETTINGS, scans=SCANS, deliveries={}, live=False, final=True)
    args.update(kw)
    return io.assemble(DAY, **args)


def by_sym(body):
    return {r["sym"]: r for r in body["rows"]}


def test_rows_carry_levels_first_candle_result_and_the_minute_bars():
    b = run()
    assert [r["sym"] for r in b["rows"]] == ["AAA", "CCC", "BBB"]                 # longs first, then shorts, by name
    a = by_sym(b)["AAA"]
    assert (a["ref"], a["side"], a["sector"], a["origin"]) == ("alert:1", "long", "IT", "replay")
    assert (a["pc"], a["ph"], a["pl"]) == (2500.0, 2600.0, 2400.0)
    assert (a["o5"], a["h5"], a["l5"], a["c5"]) == (2508.0, 2521.0, 2505.0, 2520.0)
    assert a["vwap5"] == pytest.approx(2514.2, abs=0.01)
    assert (a["entry"], a["stop"], a["target"], a["rr"]) == (2520.0, 2505.0, 2600.0, 5.33)
    assert (a["hit"], a["x"], a["xp"], a["gross"]) == ("target", "09:23", 2600.0, 3.17)
    assert (a["rank"], a["of"], a["star"], a["astr"], a["mkt"]) == (1, 2, True, 0.23, "with")
    assert a["size"] == {"shares": 10, "risk_inr": 150.0, "position_inr": 25200.0, "limited_by": "max_loss"}
    assert a["m"] == list(range(9)) and a["c"][:3] == [2510.0, 2512.0, 2515.0] and a["v"][0] == 100
    assert len(a["o"]) == len(a["h"]) == len(a["l"]) == len(a["c"]) == len(a["v"]) == 9
    s = by_sym(b)["BBB"]
    assert (s["hit"], s["x"], s["xp"], s["gross"], s["mkt"]) == ("stop", "09:25", 3005.0, -0.5, "against")
    c = by_sym(b)["CCC"]
    assert (c["hit"], c["x"], c["xp"], c["gross"], c["star"]) == ("none", "15:20", 4012.0, 0.17, False)


def test_body_header_fields():
    b = run()
    assert (b["day"], b["live"], b["final"], b["flat_by"], b["close"], b["fire"]) == ("2026-11-27", False, True, "15:20", 365, 5)
    assert b["last"] == 11                                                        # the last stored minute among the rows' bars
    assert (b["universe"], b["others"]) == (9, [{"sym": "ZZZ", "why": "opened above PDH"}])
    assert (b["breadth"], b["market"]) == (60.0, "long")                          # what the alarm stored wins over the funnel's reading
    assert b["origin"] == "replay" and b["settings"]["max_loss"] == 2000
    live = run(alerts=[dict(a, source="live") for a in ALERTS], live=True, final=False)
    assert (live["origin"], live["live"], live["final"]) == ("live", True, False)


def test_market_is_mixed_or_unknown_without_a_lean():
    b = run(alerts=[alert(1, "AAA", "long", 2520.0, 2505.0, 2600.0, market="mixed", breadth=50.0)])
    assert b["rows"][0]["mkt"] == "" and (b["breadth"], b["market"]) == (50.0, "mixed")
    none = run(alerts=[alert(1, "AAA", "long", 2520.0, 2505.0, 2600.0, market=None, breadth=None)])
    assert none["rows"][0]["mkt"] == "" and (none["breadth"], none["market"]) == (55.5, "long")       # falls back to the funnel


def test_only_the_groups_alerts_with_levels_and_candles_become_rows():
    other = alert(9, "AAA", "short", 2520.0, 2530.0, 2400.0, group="Something else")
    plain = dict(alert(8, "BBB", "short", 2990.0, 3005.0, 2900.0), digest=None)
    no_levels = alert(7, "CCC", "long", 4005.0, None, None)
    no_bars = alert(6, "DDD", "long", 2520.0, 2505.0, 2600.0)
    b = run(alerts=[other, plain, no_levels, no_bars, ALERTS[2]], trade_rows=[TRADES[0]])
    assert [r["ref"] for r in b["rows"]] == ["alert:1"]


def test_an_open_row_on_a_live_day_and_a_missing_trade_row():
    open_rows = [trade(1, "AAA", "long", 2520.0, 2505.0, 2600.0), TRADES[1], TRADES[2]]
    a = by_sym(run(trade_rows=open_rows, live=True, final=False))["AAA"]
    assert (a["hit"], a["x"], a["xp"], a["gross"]) == (None, None, None, None)     # the trade loop has not settled it yet
    # no trade row at all: graded here on the same first-touch rule, from the bar after the alarm minute
    b = run(trade_rows=[])
    a, s, c = by_sym(b)["AAA"], by_sym(b)["BBB"], by_sym(b)["CCC"]
    assert (a["hit"], a["x"], a["xp"]) == ("target", "09:23", 2600.0)
    assert (s["hit"], s["x"], s["xp"]) == ("stop", "09:25", 3005.0)
    assert (c["hit"], c["x"], c["xp"]) == ("none", "15:20", 4012.0)                # flat at the last close before flat-by
    assert a["size"]["shares"] == 119 and a["size"]["limited_by"] == "position_cap"  # 60 % of 5,00,000 at 2,520 a share
    live = by_sym(run(trade_rows=[], live=True, final=False))
    assert live["CCC"]["hit"] is None and live["AAA"]["hit"] == "target"


def test_odds_view_is_the_cards_shape():
    odds = {"mc": {"target_pct": 27.3, "stop_pct": 70.0, "none_pct": 2.7, "sessions": 60, "paths": 4000},
            "past": {"last": [[f"2026-11-{d:02d}", "stop" if d % 2 else "target", 15] for d in range(1, 15)]},
            "verdict": "bad", "sentence": "…", "rr": 1.9, "flat_by": "15:20"}
    v = io.odds_view(odds)
    assert (v["t"], v["s"], v["n"], v["verdict"], v["sessions"]) == (27.3, 70.0, 2.7, "bad", 60)
    assert len(v["last"]) == 12 and v["last"][-1] == ["11-14", "target"] and v["last"][0] == ["11-03", "stop"]
    assert io.odds_view(None) is None and io.odds_view({"verdict": "bad"}) is None


def test_messages_are_the_texts_the_phone_gets():
    b = run(deliveries={"alert:2": {"sent_at": "2026-11-27T09:20:03+05:30", "ok": True, "error": None, "silent": True},
                        "followup:alert:1:exit": {"sent_at": "2026-11-27T09:23:21+05:30", "ok": False, "error": "timeout", "silent": False}})
    ms = b["messages"]
    assert [(m["kind"], m["at"], m["m"]) for m in ms] == [("summary", "09:20", 5), ("near", "09:22", 7), ("target", "09:23", 8),
                                                         ("stop", "09:25", 10), ("result", "15:20", 365)]
    s = ms[0]
    assert s["quiet"] is True and s["delivery"] == {"ok": True, "at": "09:20:03", "error": None}
    assert s["text"].splitlines() == [
        "<b>Inside open · 09:20</b>",
        "market: 60 % of stocks above VWAP · with the longs",
        "<b>LONG · target PDH</b> (2)",
        "★ AAA 2,520.00 · stop 2,505.00 · tgt 2,600.00",
        "+ 1 more: CCC",
        "<b>SHORT · target PDL</b> (1)",
        "★ BBB 2,990.00 · stop 3,005.00 · tgt 2,900.00",
        "09:20  ·  scanradar.tech"]
    assert ms[1]["text"].splitlines()[:2] == ["<b>AAA</b> is close to its target", "now 2,588.00 · PDH 2,600.00 · Inside open long"]
    assert ms[1]["quiet"] is False and ms[1]["delivery"] is None
    assert ms[2]["text"].splitlines() == ["<b>TARGET</b> · <b>AAA</b>", "PDH 2,600.00 reached at 09:23 · +3.17 %", "Inside open long  ·  scanradar.tech"]
    assert ms[2]["delivery"] == {"ok": False, "at": "09:23:21", "error": "timeout"}
    assert ms[3]["text"].splitlines()[:2] == ["<b>STOP</b> · <b>BBB</b>", "3,005.00 hit at 09:25 · −0.50 %"]
    r = ms[4]
    assert r["quiet"] is True and r["text"].splitlines()[:2] == ["<b>Inside open · day result</b>", "3 alarms · 1 target · 1 stopped · 1 flat at 15:20"]
    assert "★ strongest 2: 1 target · 1 stopped" in r["text"]


def test_no_day_result_before_flat_by_and_no_near_note_once_settled():
    assert [m["kind"] for m in run(live=True, final=False)["messages"]] == ["summary", "near", "target", "stop"]
    # the bar that carries the price past 80 % also touches the target: the row settles that minute, so only the exit goes out
    fast = [x for x in AAA if x[0] != 6][:6] + [(6, 2523, 2601, 2522, 2590, 100)]
    b = run(bars=dict(BARS, AAA=fast), trade_rows=[trade(1, "AAA", "long", 2520.0, 2505.0, 2600.0, outcome=settled("target", 2600.0, "09:22")),
                                                  TRADES[1], TRADES[2]])
    assert [(m["kind"], m["at"]) for m in b["messages"] if "AAA" in m["text"] and m["kind"] != "summary"][:1] == [("target", "09:22")]
    assert "near" not in [m["kind"] for m in b["messages"]]
    # a bar from before the alarm never counts as "now"
    early = [(4, 2518, 2595, 2517, 2594, 100) if x[0] == 4 else x for x in AAA]
    kinds = [(m["kind"], m["at"]) for m in run(bars=dict(BARS, AAA=early))["messages"]]
    assert ("near", "09:20") not in kinds and ("near", "09:21") not in kinds and ("near", "09:22") in kinds
    assert run(alerts=[])["messages"] == []


# ---------- the funnel: who was in the universe at 09:20, and why the others were left out ----------
def frame(rows, day=DAY):
    """rows: (open, high, low, close) for 09:15, 09:16, ... one per minute."""
    t0 = pd.Timestamp(f"{day} 09:15", tz="Asia/Kolkata")
    return pd.DataFrame([{"ts": t0 + pd.Timedelta(minutes=i), "open": o, "high": h, "low": l, "close": c, "volume": 100, "oi": 1000, "delta": 0}
                         for i, (o, h, l, c) in enumerate(rows)])


def rising(base, step=2.0, n=6):
    return [(base + i * step, base + i * step + step + 1, base + i * step - 1, base + (i + 1) * step) for i in range(n)]


def test_funnel_counts_the_universe_and_says_why_a_stock_was_left_out():
    first = {"IN1": frame(rising(2500)), "UP1": frame(rising(2500)), "GAP": frame(rising(2500)), "DN1": frame(rising(2500)),
             "GDN": frame(rising(2500)), "LOW": frame(rising(1500)), "QUIET": frame(rising(2500)), "NOREF": frame(rising(2500)),
             "THIN": frame(rising(2500, n=2))}
    ref = lambda ph, pl, pc=2500.0: {"prev_close": pc, "prev_high": ph, "prev_low": pl, "sector": "X"}
    refs = {"IN1": ref(2600, 2400), "UP1": ref(2508, 2400), "GAP": ref(2450, 2300), "DN1": ref(2600, 2499.5), "GDN": ref(2700, 2550),
            "LOW": ref(1600, 1400, 1500.0), "QUIET": ref(2600, 2400), "THIN": ref(2600, 2400)}
    f = io.funnel(first, refs, SCANS["Inside open long"], pd.Timestamp(f"{DAY} 09:20", tz="Asia/Kolkata"), {"IN1"})
    assert f["universe"] == 6
    assert f["others"] == [{"sym": "DN1", "why": "first candle crossed PDL"}, {"sym": "GAP", "why": "opened above PDH"},
                           {"sym": "GDN", "why": "opened below PDL"}, {"sym": "QUIET", "why": "inside, no alarm"},
                           {"sym": "UP1", "why": "first candle crossed PDH"}]
    assert (f["breadth"], f["market"]) == (100.0, "long")
    # a stock that fired counts in the universe even if the stored bars would leave it out; no scan file, no reading
    assert io.funnel(first, refs, SCANS["Inside open long"], pd.Timestamp(f"{DAY} 09:20", tz="Asia/Kolkata"), {"IN1", "LOW"})["universe"] == 7
    assert io.funnel(first, refs, None, pd.Timestamp(f"{DAY} 09:20", tz="Asia/Kolkata"), {"IN1"}) == {"universe": None, "others": [], "breadth": None, "market": None}


# ---------- the day chips and the record under the page ----------
def drow(side, hit, gross, star=False, mkt=""):
    return {"side": side, "hit": hit, "gross": gross, "star": star, "mkt": mkt}


def test_index_chips_newest_first_and_the_record_over_settled_sessions():
    days = [{"day": "2026-11-25", "live": False, "breadth": 60.0, "market": "long",
             "rows": [drow("long", "target", 1.0, True, "with"), drow("long", "stop", -0.5, False, "with"),
                      drow("short", "stop", -0.6, True, "against"), drow("short", "none", 0.3, False, "against")]},
            {"day": "2026-11-26", "live": False, "breadth": 50.0, "market": "mixed",
             "rows": [drow("long", "target", 2.0, True), drow("short", "target", 1.0, True)]},
            {"day": "2026-11-27", "live": True, "breadth": 40.0, "market": "short",
             "rows": [drow("short", None, None, True, "with"), drow("long", "stop", -0.4, False, "against")]}]
    out = io.index(days)
    assert [c["day"] for c in out["days"]] == ["2026-11-27", "2026-11-26", "2026-11-25"]
    c0, c2 = out["days"][0], out["days"][2]
    assert (c0["label"], c0["live"], c0["n"], c0["long"], c0["target"], c0["stop"], c0["net"]) == ("Fri 27 Nov", True, 2, 1, 0, 1, None)
    assert (c2["label"], c2["n"], c2["long"], c2["target"], c2["stop"], c2["net"], c2["breadth"], c2["market"]) == \
        ("Wed 25 Nov", 4, 2, 1, 2, -0.05, 60.0, "long")                             # (1.0 − 0.5 − 0.6 + 0.3) / 4 − 0.10
    rec = out["rec"]
    assert (rec["sessions"], rec["from"], rec["to"], rec["min"], rec["max"]) == (2, "25 Nov", "26 Nov", 2, 4)
    assert rec["gross"] == 0.53                                                     # 3.2 over 6 trades, before costs
    assert rec["combos"]["all"] == {"label": "every stock that passes", "per_day": 3, "n": 6, "target": 50, "stop": 33, "net": 0.43}
    assert rec["combos"]["mkt"] == {"label": "with the market only", "per_day": 1, "n": 2, "target": 50, "stop": 50, "net": 0.15}
    assert rec["combos"]["star"] == {"label": "strongest 3 each side", "per_day": 2, "n": 4, "target": 75, "stop": 25, "net": 0.75}
    assert rec["combos"]["both"] == {"label": "strongest 3, with the market", "per_day": 0.5, "n": 1, "target": 100, "stop": 0, "net": 0.9}
    assert rec["long"] == {"n": 3, "target": 67, "stop": 33, "net": 0.73} and rec["short"] == {"n": 3, "target": 33, "stop": 33, "net": 0.13}


def test_index_limits_the_chips_and_has_no_record_without_a_settled_session():
    many = [{"day": f"2026-10-{d:02d}", "live": False, "breadth": None, "market": None, "rows": [drow("long", "target", 1.0)]} for d in range(1, 21)]
    out = io.index(many)
    assert len(out["days"]) == io.CHIPS and out["days"][0]["day"] == "2026-10-20" and out["rec"]["sessions"] == 20
    live_only = io.index([{"day": "2026-11-27", "live": True, "breadth": None, "market": None, "rows": []}])
    assert live_only["rec"] is None and live_only["days"][0]["n"] == 0
    assert io.index([]) == {"days": [], "rec": None}
