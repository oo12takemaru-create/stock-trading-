# -*- coding: utf-8 -*-
"""東証の営業日の表（検証と日次の両方が、ここだけを見る）。

■ なぜ要るか（2026-10-17・Fable）
固定した材料（daily_metrics）に、東証の休場日の行が 22 日分入っていた
（2017 年に6日・2018 年に16日。yfinance がその期間だけ休場日の行を返していた）。
中身は 始値＝高値＝安値＝終値＝前の営業日の終値・出来高0 で、エンジンはそれを
「翌営業日」として扱うので、休場日の行で約定・決済が起きていた
（2018-12-24 の振替休日に BNF が9件、12/21 の終値で約定していた）。

■ 表の出どころ
推測（「出来高0・値が動かない日」など）で決めない。公式の取引所カレンダーを使う。
  exchange_calendars の XTKS（東京証券取引所）。版は requirements.txt で固定する
  （表が版で変わると、同じ材料でも答えが変わるため）。
  2020-10-01（システム障害で終日売買停止）も休場として入っている。

■ 使い方
  drop_non_sessions(df)        … 営業日でない日の行を落とす（落とした日を返す）
  missing_sessions(dates)      … 営業日なのに無い日を返す（検出して知らせるだけ・埋めない）
"""
from __future__ import annotations

from functools import lru_cache

import pandas as pd

#: 取引所カレンダーの名前（exchange_calendars）
CALENDAR = "XTKS"
#: 表の始まり。材料の取り始め（MATERIAL_START − WARMUP_DAYS）より前にしておく
TABLE_START = "2014-01-01"


@lru_cache(maxsize=1)
def _calendar():
    import exchange_calendars as xc
    return xc.get_calendar(CALENDAR, start=TABLE_START)


def sessions(start, end) -> pd.DatetimeIndex:
    """start〜end の営業日（tz なし・0時）。表の範囲を越える部分は含まない。"""
    cal = _calendar()
    s = max(pd.Timestamp(start), pd.Timestamp(cal.first_session).tz_localize(None))
    e = min(pd.Timestamp(end), pd.Timestamp(cal.last_session).tz_localize(None))
    if s > e:
        return pd.DatetimeIndex([])
    idx = pd.DatetimeIndex(cal.sessions_in_range(s, e))
    return idx.tz_localize(None) if idx.tz is not None else idx


def table_last_day() -> pd.Timestamp:
    return pd.Timestamp(_calendar().last_session).tz_localize(None)


def non_sessions(dates) -> pd.DatetimeIndex:
    """dates のうち、営業日でない日。

    ★表の範囲より後の日は判定しない（落とさない）★
      exchange_calendars の表は先の1年ほどまでしか無い。範囲外を「営業日でない」と
      扱うと、新しい日が丸ごと消える。範囲外は呼び出し側で warn_beyond_table が言う。
    """
    d = pd.DatetimeIndex(pd.to_datetime(dates)).normalize().unique()
    if len(d) == 0:
        return d
    inside = d[d <= table_last_day()]
    if len(inside) == 0:
        return inside
    return inside.difference(sessions(inside.min(), inside.max()))


def drop_non_sessions(df: pd.DataFrame, date_col: str | None = "date"):
    """営業日でない日の行を落とした (df, 落とした日) を返す。date_col=None なら index を見る。"""
    dates = df.index if date_col is None else df[date_col]
    bad = non_sessions(dates)
    if len(bad) == 0:
        return df, bad
    key = pd.DatetimeIndex(pd.to_datetime(dates)).normalize()
    return df[~key.isin(bad)], bad


def missing_sessions(dates, start=None, end=None) -> pd.DatetimeIndex:
    """start〜end（既定は dates の最初〜最後）の営業日のうち、dates に無い日。"""
    d = pd.DatetimeIndex(pd.to_datetime(dates)).normalize().unique()
    if len(d) == 0:
        return d
    s = pd.Timestamp(start) if start is not None else d.min()
    e = pd.Timestamp(end) if end is not None else d.max()
    if s > e:
        return pd.DatetimeIndex([])
    return sessions(s, e).difference(d)


def warn_beyond_table(dates, log=print):
    d = pd.DatetimeIndex(pd.to_datetime(dates))
    if len(d) and d.max() > table_last_day():
        log("  ★営業日の表（%s・exchange_calendars）は %s までです。それより後の日は判定していません。"
            "exchange_calendars を新しくしてください" % (CALENDAR, table_last_day().date()))
