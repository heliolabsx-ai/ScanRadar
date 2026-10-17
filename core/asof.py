"""The one as-of definition shared by live, replay and backtest.

"As of HH:MM" is the view the live engine has when the HH:MM-1 bar closes:
complete bars are those with ts < at; the forming (off 0) bar is a stub of the
bar that starts at `at`, holding only its open (what ~1 s of ticks would show).
"""
import pandas as pd


def view_at(df1m: pd.DataFrame, at: pd.Timestamp) -> pd.DataFrame:
    """Complete bars with ts < at, plus a flat stub for the bar at `at` when it exists.

    df1m: one symbol, one day, ascending, columns ts/open/high/low/close/volume/oi/delta.
    Never mutates the input.
    """
    n = int(df1m.ts.searchsorted(at, side="left"))
    done = df1m.iloc[:n]
    if n < len(df1m) and df1m.ts.iloc[n] == at:
        bar = df1m.iloc[n]
        prev_oi = done.oi.iloc[-1] if n else bar.oi
        stub = pd.DataFrame([{"ts": bar.ts, "open": bar.open, "high": bar.open, "low": bar.open, "close": bar.open,
                              "volume": 0, "oi": prev_oi, "delta": 0}])
        return pd.concat([done, stub], ignore_index=True)
    return done.reset_index(drop=True)
