import json, datetime as dt
import pandas as pd
from core.screens import evaluate, weekly, monthly, lookback_days


def daily(n=80, start="2026-07-01", base=100.0, trend=-0.3):
    days = pd.bdate_range(start, periods=n)
    rows = []
    px = base
    for i, d in enumerate(days):
        o = px; c = px + trend; h = max(o, c) + 2.0; l = min(o, c) - 2.0
        rows.append({"open": o, "high": h, "low": l, "close": c, "volume": 50_000})
        px = c
    return pd.DataFrame(rows, index=[d.date() for d in days])


def test_s0101_passes_a_narrow_red_day_in_a_downtrend_and_fails_otherwise():
    screen = json.load(open("screens/S0101.json"))
    d = daily()
    today = d.index[-1] + dt.timedelta(days=3)                               # a Monday-ish forming day
    d.loc[today] = {"open": d.close.iloc[-1], "high": d.close.iloc[-1] + 0.5, "low": d.close.iloc[-1] - 1.0, "close": d.close.iloc[-1] - 0.8, "volume": 70_000}
    d = d.sort_index()
    assert evaluate(screen, d) is True                                        # NR7, red, below yesterday, SMA20<40<60, vol 1.4x
    wide = d.copy(); wide.loc[today, "high"] = d.close.iloc[-2] + 10          # today's range wider than yesterday's
    assert evaluate(screen, wide) is False
    green = d.copy(); green.loc[today, "close"] = green.loc[today, "open"] + 0.1
    assert evaluate(screen, green) is False
    quiet = d.copy(); quiet.loc[today, "volume"] = 50_000                     # not 1.25x yesterday
    assert evaluate(screen, quiet) is False
    assert evaluate(screen, d.iloc[-30:]) is False                            # not enough history for SMA60: fail closed


def test_weekly_and_monthly_forming_bars():
    d = daily(n=30, start="2026-09-01", trend=0.5)
    w = weekly(d); m = monthly(d)
    assert w.close.iloc[-1] == d.close.iloc[-1] and w.open.iloc[-1] == d.open[d.index >= (w.index[-1] - pd.Timedelta(days=6)).date()].iloc[0]
    assert m.open.iloc[-1] == d.open[[x.month == d.index[-1].month for x in d.index]].iloc[0]
    assert "range" in w and "range" in m


def test_s0101_is_an_end_of_day_screen():
    screen = json.load(open("screens/S0101.json"))
    assert screen.get("basis") == "eod"
    from screens import basis
    assert basis(screen) == "eod" and basis({"basis": "intraday"}) == "intraday" and basis({}) == "eod"


def test_lookback_covers_sma_and_months():
    screen = json.load(open("screens/S0101.json"))
    assert lookback_days(screen) >= 61
    assert lookback_days({"rules": [{"lhs": {"m": 2, "field": "close"}, "op": ">", "rhs": {"number": 1}}]}) >= 3 * 23


def test_prorate_scales_a_full_day_value_by_the_session_fraction():
    d = daily(n=5)
    d.loc[d.index[-1], "volume"] = 20_000                       # today so far: 20k; yesterday full day 50k
    rule = {"rules": [{"lhs": {"d": 0, "field": "volume"}, "op": ">", "rhs": {"d": 1, "field": "volume", "mul": 1.25, "prorate": True}}]}
    assert evaluate(rule, d, elapsed=1.0) is False              # 20k vs 62.5k at the close
    assert evaluate(rule, d, elapsed=0.25) is True              # 20k vs 15.6k a quarter of the way in
    plain = {"rules": [{"lhs": {"d": 0, "field": "volume"}, "op": ">", "rhs": {"d": 1, "field": "volume", "mul": 1.25}}]}
    assert evaluate(plain, d, elapsed=0.25) is False            # without prorate the fraction is ignored
    live = json.load(open("screens/S0101-live.json"))
    assert any(r["rhs"].get("prorate") for r in live["rules"]) and live["basis"] == "intraday"


def test_scan_fired_symbols_cut_at_the_as_of_minute():
    from api.screens import fired_symbols
    IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
    T = lambda h, m: dt.datetime(2026, 10, 23, h, m, tzinfo=IST)   # noqa: E731
    rows = [(T(11, 9), "INDIGO"), (T(11, 9), "INDIGO"), (T(14, 53), "KAYNES"), (T(14, 54), "DIXON")]
    assert fired_symbols(rows, T(11, 9)) == ["INDIGO"]              # the fire minute itself counts
    assert fired_symbols(rows, T(11, 8)) == []
    assert fired_symbols(rows, T(14, 53)) == ["INDIGO", "KAYNES"]
    assert fired_symbols(rows, T(15, 30)) == ["DIXON", "INDIGO", "KAYNES"]
