import copy

import pytest

from core.sizing import shares, validate, DEFAULTS


def test_defaults_values():
    assert DEFAULTS == {"capital": 500000, "max_loss": 2000, "day_loss": 6000, "max_trades": 5,
                        "position_cap": 0.6, "product": "intraday", "flat_by": "15:20"}


def test_shares_by_max_loss():
    s = shares(854.6, 845.9, DEFAULTS)
    assert s == {"shares": 229, "risk_inr": round(229 * 8.7, 2), "position_inr": round(229 * 854.6, 2), "limited_by": "max_loss"}


def test_shares_capped_by_position():
    s = shares(6104.0, 6100.0, DEFAULTS, side="short")          # 500 by max loss, 49 by 60 % of ₹5,00,000
    assert s["shares"] == 49 and s["limited_by"] == "position_cap"


def test_zero_distance_and_delivery_short():
    assert shares(100.0, 100.0, DEFAULTS)["limited_by"] == "zero_distance"
    assert shares(100.0, 100.0, DEFAULTS)["shares"] == 0
    d = shares(100.0, 101.0, {**DEFAULTS, "product": "delivery"}, side="short")
    assert d == {"shares": 0, "risk_inr": 0.0, "position_inr": 0.0, "limited_by": "delivery_no_short"}


def test_tie_is_reported_as_max_loss():
    # 2000 / 10 = 200 by loss; 300000 / 1500 = 200 by position
    s = shares(1500.0, 1490.0, DEFAULTS)
    assert s["shares"] == 200 and s["limited_by"] == "max_loss"


def test_short_distance_is_absolute():
    s = shares(100.0, 102.0, DEFAULTS, side="short")
    assert s["shares"] == 1000 and s["risk_inr"] == 2000.0


def test_validate_fills_defaults_and_rejects_bad_values():
    assert validate({"max_loss": 1500}) == {**DEFAULTS, "max_loss": 1500}
    for bad in ({"capital": -1}, {"day_loss": 1000, "max_loss": 2000}, {"product": "cash"}, {"flat_by": "3pm"}, {"flat_by": "16:00"}, {"max_trades": 0}):
        try:
            validate(bad); assert False, bad
        except ValueError as e:
            assert next(iter(bad)) in str(e)


def test_validate_edges_and_no_mutation():
    before = copy.deepcopy(DEFAULTS)
    assert validate({}) == DEFAULTS and validate({}) is not DEFAULTS
    assert validate({"flat_by": "09:20", "unknown": 1}) == {**DEFAULTS, "flat_by": "09:20"}
    assert validate({"flat_by": "15:30"})["flat_by"] == "15:30"
    assert validate({"position_cap": 1})["position_cap"] == 1
    assert validate({"day_loss": 2000})["day_loss"] == 2000
    assert DEFAULTS == before
    for bad, field in (({"flat_by": "09:19"}, "flat_by"), ({"flat_by": "15:31"}, "flat_by"), ({"max_trades": 2.5}, "max_trades"),
                       ({"position_cap": 1.5}, "position_cap"), ({"position_cap": 0}, "position_cap"),
                       ({"max_loss": "abc"}, "max_loss"), ({"capital": True}, "capital")):
        with pytest.raises(ValueError, match=field):
            validate(bad)
