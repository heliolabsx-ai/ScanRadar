import datetime as dt
import pandas as pd
from watch import watch_row, rank_fields
from conftest import synthetic_day


def test_watch_row_has_analytics_rank_and_spark():
    d = synthetic_day(seed=2)
    ref = {"prev_close": 100.0, "prev_high": 101.5, "prev_low": 98.5, "prev_oi": 1_000_000, "avg_vol_1m": 2000, "sector": "IT"}
    snap = {"bulls": [{"symbol": "X", "rank": 7, "pctile": 96.7, "conv": 97, "score": 61.2, "side_rank": 3, "picked": True}], "bears": []}
    r = watch_row(d, ref, "X", snap, 500)
    assert r["symbol"] == "X" and r["rank"] == 7 and r["picked"] is True and r["lot_size"] == 500
    assert len(r["spark"]) == 60 and r["spark"][-1] == round(float(d.close.iloc[-1]), 2) and r["n_min"] == len(d)
    assert "vwap" in r and "oi_label" in r and r["prev_high"] == 101.5 and r["spark_from"] == "14:30"
    assert rank_fields(snap, "Y") == {} and watch_row(d, None, "X", snap, 500) is None and watch_row(d.iloc[0:0], ref, "X", snap, 500) is None
