"""Digest scans (core.rules, mirrored in engine/rules.py): a scan whose alerts go out as one summary message.
The strength of a fire, ranks and stars within one scan, the market's lean at the fire minute, the message text."""
import datetime as dt
import pandas as pd
import pytest
from core import rules

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
NOW = dt.datetime(2026, 11, 27, 9, 20, 2, tzinfo=IST)


def scan(side="long", **over) -> dict:
    sc = {"name": f"Inside open {side}", "side": side, "timeframe": "1m", "match": "all",
          "rules": [{"lhs": {"off": 0, "field": "close"}, "op": ">", "rhs": {"number": 2000}}],
          "alert": {"telegram": "digest", "cooldown_min": 400},
          "digest": {"group": "Inside open", "title": "LONG · target PDH" if side == "long" else "SHORT · target PDL", "stars": 3,
                     "strength": {"lhs": {"off": 0, "field": "close"}, "rhs": {"off": 0, "field": "vwap"}},
                     "market": {"label": "of stocks above VWAP", "lean": 55,
                                "universe": [{"lhs": {"off": 0, "field": "close"}, "op": ">", "rhs": {"number": 2000}}],
                                "up": [{"lhs": {"off": 0, "field": "close"}, "op": ">", "rhs": {"off": 0, "field": "vwap"}}]}}}
    sc.update(over)
    return sc


def bars(close: float, vwap: float) -> pd.DataFrame:
    return pd.DataFrame({"open": [close], "high": [close], "low": [close], "close": [close], "volume": [10], "oi": [0], "vwap": [vwap]})


def test_digest_block_only_for_a_digest_scan():
    assert rules.digest(scan())["group"] == "Inside open"
    assert rules.digest(scan(alert={"telegram": True})) is None          # the block alone does not make a digest
    assert rules.digest({"name": "x", "rules": []}) is None


def test_strength_is_the_percent_distance_between_the_two_sides():
    assert rules.strength(scan(), bars(2010.0, 2000.0), {}) == pytest.approx(0.5)
    assert rules.strength(scan(), bars(1990.0, 2000.0), {}) == pytest.approx(0.5)       # absolute: a short reads the same way
    assert rules.strength(scan(), bars(2010.0, 0.0), {}) is None                        # nothing to compare with
    sc = scan(); del sc["digest"]["strength"]
    assert rules.strength(sc, bars(2010.0, 2000.0), {}) is None


def test_market_vote_counts_only_the_universe():
    assert rules.market_vote(scan(), bars(2010.0, 2000.0), {}) is True
    assert rules.market_vote(scan(), bars(2010.0, 2020.0), {}) is False
    assert rules.market_vote(scan(), bars(1500.0, 1400.0), {}) is None                  # under ₹2,000: no vote
    sc = scan(); del sc["digest"]["market"]
    assert rules.market_vote(sc, bars(2010.0, 2000.0), {}) is None


def test_market_leans_only_past_the_threshold():
    assert rules.market([True] * 6 + [False] * 4) == {"breadth": 60.0, "n": 10, "side": "long"}
    assert rules.market([True] * 4 + [False] * 6)["side"] == "short"
    assert rules.market([True] * 5 + [False] * 5)["side"] == "mixed"
    assert rules.market([True] * 11 + [False] * 9, lean=55)["side"] == "long"          # exactly 55 % counts
    assert rules.market([True] * 6 + [False] * 4, lean=65)["side"] == "mixed"
    assert rules.market([True, None, False, None])["n"] == 2                           # a symbol outside the universe has no vote
    assert rules.market([]) is None and rules.market([None]) is None


def test_apply_digest_ranks_by_strength_and_stars_the_top():
    fires = [{"symbol": s, "strength": x, "payload": {}} for s, x in
             [("AAA", 0.10), ("BBB", 0.40), ("CCC", None), ("DDD", 0.25), ("EEE", 0.25), ("FFF", 0.05)]]
    rules.apply_digest(scan(), fires, {"breadth": 61.2, "n": 60, "side": "long"})
    d = {f["symbol"]: f["payload"]["digest"] for f in fires}
    assert [d[s]["rank"] for s in ("BBB", "DDD", "EEE", "AAA", "FFF", "CCC")] == [1, 2, 3, 4, 5, 6]     # ties by symbol, unknown last
    assert [s for s in d if d[s]["star"]] == ["BBB", "DDD", "EEE"]
    assert d["AAA"] == {"group": "Inside open", "strength": 0.1, "rank": 4, "of": 6, "star": False, "breadth": 61.2, "market": "long"}
    assert d["CCC"]["strength"] is None and d["CCC"]["star"] is False
    one = [{"symbol": "ZZZ", "strength": 0.3, "payload": {}}]
    rules.apply_digest(scan(), one, None)                                               # no market read: the fields stay empty
    assert one[0]["payload"]["digest"] == {"group": "Inside open", "strength": 0.3, "rank": 1, "of": 1, "star": True, "breadth": None, "market": None}


def test_digest_text_lists_the_starred_with_levels_and_the_rest_by_name():
    row = lambda s, rank, star, px=2000.0: {"symbol": s, "price": px, "stop": px - 10, "target": px + 25, "rank": rank, "star": star}
    longs = {"side": "long", "title": "LONG · target PDH", "rows": [row("BBB", 2, True), row("AAA", 1, True, 20674.0), row("M&M", 3, False), row("CCC", 4, False)]}
    shorts = {"side": "short", "title": "SHORT · target PDL", "rows": [{"symbol": "ZZZ", "price": 3478.7, "stop": 3493.0, "target": 3457.0, "rank": 1, "star": True}]}
    t = rules.digest_text("Inside open", NOW, {"breadth": 39.4, "n": 66, "side": "short"}, "of stocks above VWAP", [longs, shorts])
    assert t.splitlines() == [
        "<b>Inside open · 09:20</b>",
        "market: 39 % of stocks above VWAP · with the shorts",
        "<b>SHORT · target PDL</b> (1)",                                               # the side with the market goes first
        "★ ZZZ 3,478.70 · stop 3,493.00 · tgt 3,457.00",
        "<b>LONG · target PDH</b> (4)",
        "★ AAA 20,674.00 · stop 20,664.00 · tgt 20,699.00",
        "★ BBB 2,000.00 · stop 1,990.00 · tgt 2,025.00",
        "+ 2 more: M&amp;M, CCC",
        "09:20  ·  scanradar.tech"]
    mixed = rules.digest_text("Inside open", NOW, {"breadth": 48.0, "n": 66, "side": "mixed"}, "of stocks above VWAP", [longs, shorts])
    assert mixed.splitlines()[1:3] == ["market: 48 % of stocks above VWAP · mixed", "<b>LONG · target PDH</b> (4)"]
    plain = rules.digest_text("Inside open", NOW, None, None, [{"side": "long", "title": "LONG", "rows": [{"symbol": "AAA", "price": 2000.0, "stop": None, "target": None, "rank": None, "star": False}]}])
    assert plain.splitlines() == ["<b>Inside open · 09:20</b>", "<b>LONG</b> (1)", "AAA", "09:20  ·  scanradar.tech"]


def test_digest_text_keeps_a_long_list_inside_one_message():
    rows = [{"symbol": f"SYM{i:03d}", "price": 2000.0, "stop": 1990.0, "target": 2020.0, "rank": i + 1, "star": i < 3} for i in range(120)]
    t = rules.digest_text("Inside open", NOW, None, None, [{"side": "long", "title": "LONG", "rows": rows}])
    assert len(t) < 4096 and "+ 117 more: " in t and t.count("SYM") == 3 + rules.DIGEST_NAMES and "…" in t


def test_validate_scan_checks_the_digest_block():
    rules.validate_scan(scan())
    sc = scan(); del sc["digest"]
    with pytest.raises(ValueError, match="digest"):
        rules.validate_scan(sc)
    sc = scan(); sc["digest"]["group"] = ""
    with pytest.raises(ValueError, match="group"):
        rules.validate_scan(sc)
    sc = scan(); sc["digest"]["stars"] = -1
    with pytest.raises(ValueError, match="stars"):
        rules.validate_scan(sc)
    sc = scan(); sc["digest"]["market"]["lean"] = 40
    with pytest.raises(ValueError, match="lean"):
        rules.validate_scan(sc)
    sc = scan(); sc["digest"]["market"]["up"] = "close > vwap"
    with pytest.raises(ValueError, match="market"):
        rules.validate_scan(sc)
    rules.validate_scan({"name": "plain", "rules": [], "alert": {"telegram": "silent"}})    # other scans are untouched
