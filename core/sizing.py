"""Trade settings and share sizing: how many cash shares a trigger may carry.

Pure functions, no I/O. Size is whole shares, bounded by the max loss at the stop
and by a share of capital; there are no lots here."""
import math

DEFAULTS = {"capital": 500000, "max_loss": 2000, "day_loss": 6000, "max_trades": 5,
            "position_cap": 0.6, "product": "intraday", "flat_by": "15:20"}

PRODUCTS = ("intraday", "delivery")
FLAT_BY_MIN, FLAT_BY_MAX = (9, 20), (15, 30)


def _number(field: str, v) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0:
        raise ValueError(f"{field} must be a positive number, got {v!r}")
    return v


def _flat_by(v) -> str:
    try:
        h, m = v.split(":")
        t = (int(h), int(m))
        ok = len(h) == 2 and len(m) == 2 and 0 <= t[1] < 60
    except (AttributeError, ValueError):
        ok = False
    if not ok or not FLAT_BY_MIN <= t <= FLAT_BY_MAX:
        raise ValueError("flat_by must be HH:MM between 09:20 and 15:30, got %r" % (v,))
    return v


def validate(settings: dict) -> dict:
    """Full settings dict: DEFAULTS filled in under `settings`, unknown keys dropped."""
    out = {k: settings.get(k, d) for k, d in DEFAULTS.items()}
    for k in ("capital", "max_loss", "day_loss", "max_trades", "position_cap"):
        _number(k, out[k])
    if out["day_loss"] < out["max_loss"]:
        raise ValueError(f"day_loss ({out['day_loss']}) must be at least max_loss ({out['max_loss']})")
    if isinstance(out["max_trades"], float) and not out["max_trades"].is_integer():
        raise ValueError(f"max_trades must be a positive integer, got {out['max_trades']!r}")
    if not out["position_cap"] <= 1:
        raise ValueError(f"position_cap must be in (0, 1], got {out['position_cap']!r}")
    if out["product"] not in PRODUCTS:
        raise ValueError(f"product must be one of {', '.join(PRODUCTS)}, got {out['product']!r}")
    out["flat_by"] = _flat_by(out["flat_by"])
    return out


def shares(entry: float, stop: float, settings: dict, side: str = "long") -> dict:
    """Whole shares for a trade: min(max_loss / distance, capital x position_cap / entry)."""
    def result(n: int, limit: str, dist: float = 0.0) -> dict:
        return {"shares": n, "risk_inr": round(n * dist, 2), "position_inr": round(n * entry, 2), "limited_by": limit}

    if settings["product"] == "delivery" and side == "short":
        return result(0, "delivery_no_short")
    dist = round(abs(entry - stop), 4)
    if dist == 0:
        return result(0, "zero_distance")
    by_loss = math.floor(settings["max_loss"] / dist)
    by_cap = math.floor(settings["capital"] * settings["position_cap"] / entry)
    if by_cap < by_loss:
        return result(by_cap, "position_cap", dist)
    return result(by_loss, "max_loss", dist)
