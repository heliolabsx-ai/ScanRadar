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
    # as of 09:35 (before the 09:35:04 send) the row has not rung yet
    b = run(alerts=[alert(7, "PDH fade", "AAA", "09:35")], trade_rows=[t], deliveries=dl, at=T("09:35"))
    assert b["rows"][0]["delivery"] is None


def test_null_price_and_confidence_do_not_break_the_board():
    t = trade("alert:5", "AAA", "09:35", entry=None, stop=None, target=None, rr=None)
    desk = [{"ref": "alert:5", "ts": iso("09:36"), "symbol": "AAA", "output": {"call": "wait", "confidence": None}}]
    b = run(alerts=[alert(5, "PDH fade", "AAA", "09:35", price=None)], trade_rows=[t], desk_calls=desk)
    assert [e["text"] for e in b["rows"][0]["timeline"]] == ["PDH fade · SELL at —", "desk WAIT"]
    assert b["rows"][0]["rr"] is None


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


def test_origin_live_or_replay():
    alerts = [alert(1, "PDH fade", "AAA", "09:35", source="replay"), alert(2, "B0101", "BBB", "09:40", side="long")]
    pats = [trade("pattern:CCC|double_top|09:40", "CCC", "09:45", source="pattern", label="Double top"),
            trade("pattern:DDD|orb_long|09:30", "DDD", "09:30", source="pattern", label="Opening range break up")]
    b = run(alerts=alerts, trade_rows=pats, pattern_origins={"pattern:CCC|double_top|09:40": "replay"})
    origin = {r["ref"]: r["origin"] for r in b["rows"]}
    assert origin == {"alert:1": "replay", "alert:2": "live", "pattern:CCC|double_top|09:40": "replay", "pattern:DDD|orb_long|09:30": "live"}
    assert all(r["origin"] == "live" for r in run(alerts=alerts[1:], trade_rows=pats)["rows"])     # no map: patterns default live


def test_index_fresh_age_rule():
    now = T("10:00")
    assert alarms.index_fresh((now - dt.timedelta(seconds=30)).isoformat(), now)
    assert alarms.index_fresh((now - dt.timedelta(seconds=180)).isoformat(), now)
    assert not alarms.index_fresh((now - dt.timedelta(seconds=181)).isoformat(), now)
    assert not alarms.index_fresh(None, now) and not alarms.index_fresh("garbage", now)
    assert alarms.index_fresh("2026-11-03T09:59:00", now)          # a naive ts reads as IST


# ---------- the Inside open columns: stars, strength and the market badge ----------
IO_BOARD = BOARD + ["Inside open long", "Inside open short"]
IO_SIDES = {**SIDES, "Inside open long": "long", "Inside open short": "short"}


def _dg(rank, of, star, strength, market="short", breadth=39.4):
    return {"group": "Inside open", "strength": strength, "rank": rank, "of": of, "star": star, "breadth": breadth, "market": market}


def test_inside_open_columns_have_their_own_names_and_come_last():
    cols = alarms.columns(IO_BOARD, IO_SIDES)
    assert [c["key"] for c in cols][-3:] == ["scan:B0101", "io_long", "io_short"]
    by = {c["key"]: c for c in cols}
    assert (by["io_long"]["name"], by["io_long"]["sub"], by["io_long"]["side"], by["io_long"]["scan"]) == \
        ("Inside open · long", "above VWAP · target PDH", "long", "Inside open long")
    assert (by["io_short"]["name"], by["io_short"]["sub"], by["io_short"]["side"]) == ("Inside open · short", "below VWAP · target PDL", "short")
    assert "Inside open long" in alarms.DEFAULT_SCANS and "Inside open short" in alarms.DEFAULT_SCANS


def test_digest_rows_carry_star_rank_strength_and_their_side_of_the_market():
    alerts = [alert(1, "Inside open long", "AAA", "09:20", side="long", stop=98.0, target=103.0, digest=_dg(2, 16, True, 0.31)),
              alert(2, "Inside open long", "BBB", "09:20", side="long", stop=98.0, target=103.0, digest=_dg(9, 16, False, 0.08)),
              alert(3, "Inside open short", "CCC", "09:20", side="short", stop=102.0, target=97.0, digest=_dg(1, 21, True, 0.55)),
              alert(4, "PDH fade", "DDD", "10:05", stop=102.0, target=97.0)]
    b = run(alerts=alerts, board_scans=IO_BOARD, scan_sides=IO_SIDES)
    rows = {r["symbol"]: r for r in b["rows"]}
    assert (rows["AAA"]["col"], rows["AAA"]["star"], rows["AAA"]["rank"], rows["AAA"]["of"], rows["AAA"]["strength"], rows["AAA"]["market"]) == \
        ("io_long", True, 2, 16, 0.31, "against")
    assert (rows["BBB"]["star"], rows["BBB"]["rank"]) == (False, 9)
    assert (rows["CCC"]["col"], rows["CCC"]["star"], rows["CCC"]["market"]) == ("io_short", True, "with")
    assert (rows["DDD"]["star"], rows["DDD"]["rank"], rows["DDD"]["market"]) == (False, None, None)        # an ordinary row
    cols = {c["key"]: c for c in b["columns"]}
    assert cols["io_long"]["badge"] == {"cls": "against", "text": "against the market", "breadth": 39.4}
    assert cols["io_short"]["badge"] == {"cls": "with", "text": "with the market", "breadth": 39.4}
    assert cols["pdh_fade"]["badge"] is None and cols["pat"]["badge"] is None
    assert not any(k.startswith("_") for r in b["rows"] for k in r)                    # the helper key never reaches the page


def test_market_badge_reads_mixed_and_is_absent_before_the_fire():
    alerts = [alert(1, "Inside open long", "AAA", "09:20", side="long", stop=98.0, target=103.0, digest=_dg(1, 1, True, 0.31, market="mixed", breadth=48.0))]
    b = run(alerts=alerts, board_scans=IO_BOARD, scan_sides=IO_SIDES)
    assert {c["key"]: c for c in b["columns"]}["io_long"]["badge"] == {"cls": "mixed", "text": "market mixed", "breadth": 48.0}
    assert b["rows"][0]["market"] == "mixed"
    early = run(alerts=alerts, board_scans=IO_BOARD, scan_sides=IO_SIDES, at=T("09:19"))
    assert early["rows"] == [] and all(c["badge"] is None for c in early["columns"])
    no_read = run(alerts=[alert(1, "Inside open long", "AAA", "09:20", side="long", digest=_dg(1, 1, True, 0.3, market=None, breadth=None))],
                  board_scans=IO_BOARD, scan_sides=IO_SIDES)
    assert no_read["rows"][0]["market"] is None and {c["key"]: c for c in no_read["columns"]}["io_long"]["badge"] is None
