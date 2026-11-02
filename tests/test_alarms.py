# tests/test_alarms.py: the Alarms board assembled from hand-built rows (no DB, no Redis).
import datetime as dt
import alarms

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
DAY = dt.date(2026, 11, 2)
BOARD = ["PDH break", "PDL break", "PDH fade", "PDL fade", "B0101"]
SIDES = {"PDH break": "short", "PDL break": "long", "PDH fade": "short", "PDL fade": "long", "B0101": "long",
         "bounce_failure_short": "short"}


def T(hhmm, day=DAY):
    h, m = hhmm.split(":")
    return dt.datetime(day.year, day.month, day.day, int(h), int(m), tzinfo=IST)


def iso(hhmm):
    return T(hhmm).isoformat()


def alert(id, scan, sym, hhmm, price=100.0, side="short", **kw):
    return {"id": id, "ts": iso(hhmm), "scan": scan, "symbol": sym, "price": price, "tf": "5m", "source": "live",
            "side": side, "stop": None, "target": None, "outcome": None, "story": None, **kw}


def trade(ref, sym, hhmm, source="alert", label="PDH fade", **kw):
    return {"id": 1, "day": DAY.isoformat(), "ts": iso(hhmm), "ref": ref, "symbol": sym, "side": "short", "source": source,
            "entry": 100.0, "stop": 102.0, "target": 97.0, "target2": None, "rr": 1.5, "odds": None,
            "size": {"shares": 120, "risk_inr": 240.0}, "gate": "PASS", "gate_reason": None, "outcome": None, "paper": None,
            "label": label, **kw}


def run(**kw):
    args = dict(alerts=[], trade_rows=[], desk_calls=[], deliveries={}, board_scans=BOARD, scan_sides=SIDES, records={},
                breadth=None, index=None, telegram_on=False, live=False, pending=False, patterns_total=0)
    args.update(kw)
    at = args.pop("at", T("15:30"))
    return alarms.assemble(DAY, at, **args)


def test_columns_order_and_names():
    cols = alarms.columns(BOARD, SIDES)
    assert [c["key"] for c in cols] == ["pdh_brk", "pdl_brk", "pat", "pdh_fade", "pdl_fade", "scan:B0101"]
    by = {c["key"]: c for c in cols}
    assert (by["pdh_brk"]["name"], by["pdh_brk"]["sub"], by["pdh_brk"]["side"], by["pdh_brk"]["scan"]) == \
        ("PDH breakout", "crossed above yesterday's high", "short", "PDH break")
    assert (by["pdl_brk"]["name"], by["pdl_brk"]["sub"], by["pdl_brk"]["side"]) == ("PDL breakdown", "crossed below yesterday's low", "long")
    assert (by["pat"]["name"], by["pat"]["sub"], by["pat"]["side"], by["pat"]["scan"]) == ("Patterns", "trade-grade triggers", None, None)
    assert (by["pdh_fade"]["name"], by["pdh_fade"]["sub"], by["pdh_fade"]["side"]) == ("PDH fade", "short · first 5m close back below", "short")
    assert (by["pdl_fade"]["name"], by["pdl_fade"]["sub"], by["pdl_fade"]["side"]) == ("PDL fade", "long · first 5m close back above", "long")
    assert (by["scan:B0101"]["name"], by["scan:B0101"]["sub"], by["scan:B0101"]["side"], by["scan:B0101"]["scan"]) == \
        ("B0101", "rule scan", "long", "B0101")
    # a board scan without a scan file is skipped
    assert "scan:Ghost" not in [c["key"] for c in alarms.columns(BOARD + ["Ghost"], SIDES)]


def test_assemble_empty_day():
    b = run()
    assert [c["key"] for c in b["columns"]] == ["pdh_brk", "pdl_brk", "pat", "pdh_fade", "pdl_fade", "scan:B0101"]
    assert all(c["record"] == {"text": "no record yet"} for c in b["columns"])
    assert b["rows"] == [] and b["breadth"] is None and b["index"] is None
    assert b["folded"] == {"alerts": 0, "scans": 0, "patterns": 0}
    assert b["day"] == "2026-11-02" and b["at"] == "15:30" and b["telegram"] == "not set up" and b["pending"] is False


def test_assemble_assigns_columns_and_cuts_at():
    alerts = [alert(1, "PDH break", "AAA", "09:20"), alert(2, "PDH fade", "AAA", "09:35"),
              alert(3, "bounce_failure_short", "BBB", "09:40")]
    pat = trade("pattern:CCC|double_top|09:40", "CCC", "09:40", source="pattern", label="Double top")
    b = run(alerts=alerts, trade_rows=[pat], at=T("09:36"), patterns_total=1, telegram_on=True)
    assert [(r["col"], r["symbol"]) for r in b["rows"]] == [("pdh_brk", "AAA"), ("pdh_fade", "AAA")]
    assert b["folded"] == {"alerts": 0, "scans": 0, "patterns": 1}      # the 09:40 alert is after `at`; the pattern trigger is counted only in patterns_total
    assert b["telegram"] == "on"
    b = run(alerts=alerts, trade_rows=[pat], at=T("09:40"), patterns_total=3)
    assert [r["col"] for r in b["rows"]] == ["pdh_brk", "pdh_fade", "pat"]
    assert b["folded"] == {"alerts": 1, "scans": 1, "patterns": 2}
    prow = next(r for r in b["rows"] if r["col"] == "pat")
    assert (prow["setup"], prow["price"], prow["source"], prow["ts"], prow["shares"]) == ("Double top", 100.0, "pattern", iso("09:40"), 120)


def test_alert_without_trade_row():
    b = run(alerts=[alert(7, "PDH fade", "AAA", "09:35", stop=102.0, target=97.0)])
    r = b["rows"][0]
    assert r["odds"] is None and r["past"] == [] and r["shares"] is None and r["gate"] is None and r["outcome"] is None
    assert r["desk"] is None and r["delivery"] is None and r["source"] == "alert" and r["ref"] == "alert:7"
    assert r["stop"] == 102.0 and r["rr"] == 1.5


def test_join_trade_desk_delivery():
    past = [[f"2026-10-{d:02d}", "target" if d % 2 else "stop", 30] for d in range(1, 15)]
    t = trade("alert:7", "AAA", "09:35", odds={"past": {"last": past}})
    desk = [{"ref": "alert:7", "ts": iso("09:36"), "symbol": "AAA", "output": {"call": "take", "confidence": 72}}]
    dl = {"alert:7": {"sent_at": "2026-11-02T09:35:04+05:30", "ok": True, "error": None, "silent": False}}
    b = run(alerts=[alert(7, "PDH fade", "AAA", "09:35")], trade_rows=[t], desk_calls=desk, deliveries=dl)
    r = b["rows"][0]
    assert r["past"] == past[-12:] and len(r["past"]) == 12
    assert (r["shares"], r["risk_inr"], r["gate"], r["rr"]) == (120, 240.0, "PASS", 1.5)
    assert r["desk"] == {"call": "take", "confidence": 72, "at": "09:36"}
    assert r["delivery"] == dl["alert:7"]


def test_timeline_order_for_one_symbol():
    story = {"line": 100.0, "break_at": "09:20", "run_px": 101.5, "run_pct": 1.5}
    alerts = [alert(1, "PDH break", "AAA", "09:30", story=story), alert(2, "PDH fade", "AAA", "09:35", price=99.5)]
    t = trade("alert:2", "AAA", "09:35", outcome={"hit": "target", "r60": 1.0, "exit_px": 97.0, "settled_at": iso("10:12"), "pnl_inr": 300})
    desk = [{"ref": "alert:2", "ts": iso("09:36"), "symbol": "AAA", "output": {"call": "take", "confidence": 72}}]
    b = run(alerts=alerts, trade_rows=[t], desk_calls=desk)
    want = [{"at": "09:20", "kind": "break", "text": "above PDH 100.00 at 09:20 · ran to 101.50 (+1.5 %)"},
            {"at": "09:35", "kind": "alarm", "text": "PDH fade · SELL at 99.50"},
            {"at": "09:36", "kind": "desk", "text": "desk TAKE 72"},
            {"at": "10:12", "kind": "exit", "text": "target at 97.00"}]
    assert len(b["rows"]) == 2 and all(r["timeline"] == want for r in b["rows"])
    assert b["rows"][1]["outcome"]["exit_px"] == 97.0
    b = run(alerts=alerts, trade_rows=[t], desk_calls=desk, at=T("09:50"))
    assert [e["kind"] for e in b["rows"][0]["timeline"]] == ["break", "alarm", "desk"]
    assert b["rows"][1]["outcome"] is None                     # settled after `at`: not shown yet


def test_break_text_without_story_and_pdl_long():
    b = run(alerts=[alert(1, "PDH break", "AAA", "09:30"), alert(2, "PDL break", "BBB", "09:31", side="long")])
    assert b["rows"][0]["timeline"][0]["text"] == "above PDH" and b["rows"][1]["timeline"][0]["text"] == "below PDL"


def test_break_record_and_texts():
    rows = []
    for i in range(10):
        day = dt.date(2026, 11, 2 if i < 5 else 3)
        rows.append({"ts": T("09:20", day), "scan": "PDH break", "symbol": f"S{i}"})
    for i in range(3):
        rows.append({"ts": T("09:40", dt.date(2026, 11, 2 if i < 5 else 3)), "scan": "PDH fade", "symbol": f"S{i}"})
    rows.append({"ts": T("09:40"), "scan": "PDH fade", "symbol": "OTHER"})        # a fade with no break does not count
    rec = alarms.break_record(rows, "PDH break", "PDH fade", 20)
    assert rec["per_day"] == 5.0 and rec["fade_pct"] == 30.0 and rec["n"] == 10
    assert alarms.break_record([], "PDH break", "PDH fade", 20) is None
    assert alarms.record_text("pdh_brk", {"per_day": 137.0, "fade_pct": 31.0, "n": 2740}) == "137/day · 31 % faded"
    assert alarms.record_text("pdh_fade", {"n": 520, "hit": 54.0, "avg_r": 0.31}) == "54 % hit · +0.3 % @60m · n 520"
    assert alarms.record_text("pat", {"n": 1420, "hit": 62.0}) == "62 % T1 · n 1,420"
    assert alarms.record_text("pdh_fade", {"n": 19, "hit": 54.0, "avg_r": 0.3}) == "no record yet"
    assert alarms.record_text("pdh_fade", None) == "no record yet"


def test_record_lands_on_column():
    b = run(records={"pdh_fade": {"n": 520, "hit": 54.0, "avg_r": 0.3, "text": "54 % hit · +0.3 % @60m · n 520"}})
    assert next(c for c in b["columns"] if c["key"] == "pdh_fade")["record"]["text"] == "54 % hit · +0.3 % @60m · n 520"
