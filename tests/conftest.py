"""Shared fixtures: a deterministic synthetic trading day in the candles_1m layout."""
import sys, pathlib
import numpy as np, pandas as pd, pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "ingest", ROOT / "api"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

IST = "Asia/Kolkata"
DAY = "2026-10-16"


def synthetic_day(seed: int = 1, start: str = "09:15", minutes: int = 375, with_gap_at: list[str] | None = None,
                  day: str = DAY, base_price: float = 100.0) -> pd.DataFrame:
    """Random-walk 1-minute candles: columns ts, open, high, low, close, volume, oi, delta (ascending, IST)."""
    rng = np.random.default_rng(seed)
    ts = pd.date_range(f"{day} {start}", periods=minutes, freq="1min", tz=IST)
    ret = rng.normal(0, 0.0008, minutes)
    close = base_price * np.cumprod(1 + ret)
    open_ = np.concatenate([[base_price], close[:-1]])
    spread = np.abs(rng.normal(0, 0.0005, minutes)) * close
    high = np.maximum(open_, close) + spread
    low = np.minimum(open_, close) - spread
    volume = rng.integers(500, 5000, minutes)
    oi = 1_000_000 + np.cumsum(rng.integers(-2000, 2500, minutes))
    delta = (np.sign(close - open_) * volume * rng.uniform(0.5, 0.8, minutes)).astype(int)
    df = pd.DataFrame({"ts": ts, "open": open_, "high": high, "low": low, "close": close,
                       "volume": volume.astype(int), "oi": oi.astype(int), "delta": delta})
    if with_gap_at:
        gaps = {pd.Timestamp(f"{day} {g}", tz=IST) for g in with_gap_at}
        df = df[~df.ts.isin(gaps)].reset_index(drop=True)
    return df


@pytest.fixture
def day() -> pd.DataFrame:
    return synthetic_day()


def shaped_day(rel_pcts, base: float = 100.0, prev_close: float | None = None, oi=None, vol=None,
               noise: float = 0.0004, seed: int = 7, day: str = DAY, drop_bars=()):
    """A 1-minute day whose 5-minute closes follow `rel_pcts` (percent vs `base`), with
    small intrabar noise. `oi`/`vol` are per-5m-bar lists (default flat). `drop_bars`
    removes whole 5m bars (by index) to simulate gaps. Returns (df1m, prev_close)."""
    rng = np.random.default_rng(seed)
    n = len(rel_pcts)
    oi = list(oi) if oi is not None else [1_000_000] * n
    vol = list(vol) if vol is not None else [5000] * n
    rows = []
    prev = base
    t0 = pd.Timestamp(f"{day} 09:15", tz=IST)
    for b, rp in enumerate(rel_pcts):
        if b in drop_bars:
            prev = base * (1 + rp / 100)
            continue
        target = base * (1 + rp / 100)
        start = prev + (target - prev) * 0.2          # open a little into the move so the bar's extreme is its own
        for m in range(5):
            c = start + (target - start) * (m + 1) / 5
            o = start if m == 0 else rows[-1]["close"]
            jitter = abs(rng.normal(0, noise)) * base
            rows.append({"ts": t0 + pd.Timedelta(minutes=b * 5 + m), "open": o, "high": max(o, c) + jitter,
                         "low": min(o, c) - jitter, "close": c, "volume": int(vol[b] / 5), "oi": int(oi[b]), "delta": 0})
        prev = target
    return pd.DataFrame(rows), (prev_close if prev_close is not None else base)
