import datetime as dt
from tradingday import is_trading_day


def test_weekdays_trade_weekends_do_not():
    assert is_trading_day(dt.date(2026, 10, 19)) is True     # Monday
    assert is_trading_day(dt.date(2026, 10, 23)) is True     # Friday
    assert is_trading_day(dt.date(2026, 10, 17)) is False    # Saturday
    assert is_trading_day(dt.date(2026, 10, 18)) is False    # Sunday


def test_listed_holidays_do_not_trade():
    assert is_trading_day(dt.date(2026, 10, 20), holidays={dt.date(2026, 10, 20)}) is False
    assert is_trading_day(dt.date(2026, 10, 21), holidays={dt.date(2026, 10, 20)}) is True
