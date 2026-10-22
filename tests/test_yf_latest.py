# -*- coding: utf-8 -*-
"""yf_latest.fill_latest のテスト（ネットにはつながない）

yfinance 1.7 から history_metadata の regularMarketTime が int ではなく
pandas.Timestamp で返るようになり、int(ts) で落ちて score3 の終値が全滅した
（2026-10-22・10-23 の AI朝刊が欠番）。偽の yfinance で両方の形を試す。
"""
import sys
import types
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from yf_latest import fill_latest   # noqa: E402

NG = 0


def eq(got, want, label):
    global NG
    if got != want:
        NG += 1
        print(f"  NG {label}\n     期待={want}\n     実際={got}")


def fake_yf(ts, end):
    class Ticker:
        def __init__(self, t):
            self.history_metadata = None

        def history(self, **kw):
            self.history_metadata = {
                "regularMarketPrice": 64999.0, "regularMarketTime": ts,
                "exchangeTimezoneName": "Asia/Tokyo",
                "currentTradingPeriod": {"regular": {"end": end}}}
            return pd.DataFrame()
    sys.modules["yfinance"] = types.SimpleNamespace(Ticker=Ticker)


def run():
    c = pd.Series([64000.0, 64500.0, np.nan],
                  index=pd.to_datetime(["2026-10-20", "2026-10-21", "2026-10-22"]))
    r = fill_latest("^N225", c, verbose=False)
    return f"{r.index[-1]:%Y-%m-%d}", float(r.iloc[-1])


TS = pd.Timestamp("2026-10-22 06:00", tz="UTC")     # 15:00 JST（大引け後）
END = pd.Timestamp("2026-10-22 06:30", tz="UTC")

fake_yf(TS, END)                                       # yfinance 1.7 の形
eq(run(), ("2026-10-22", 64999.0), "Timestamp で返るとき最新日を埋める")

fake_yf(int(TS.timestamp()), int(END.timestamp()))     # 以前の形
eq(run(), ("2026-10-22", 64999.0), "int で返るとき最新日を埋める")

fake_yf(TS.tz_localize(None), END.tz_localize(None))   # tz なし Timestamp は UTC とみなす
eq(run(), ("2026-10-22", 64999.0), "tz なし Timestamp")

fake_yf(object(), None)                                # 想定外の型でも終値ごと失わない
eq(run(), ("2026-10-21", 64500.0), "想定外の型なら空欄を捨てるだけ")

print(f"{'NG ' + str(NG) + '件' if NG else '全て一致 ✓'}")
sys.exit(1 if NG else 0)
