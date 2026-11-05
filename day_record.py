# -*- coding: utf-8 -*-
"""AI朝刊の「今日の記録」（AIを使わない・機械で作る1行・2026-11-06）

日経平均・ドル円・米10年金利・金・WTI原油・VIX・S&P500 の日足（立会いが終わった足だけ）から、
今朝いちばん目立つ「記録」を1行だけ作る。該当が無い日は None（出さない）。

優先順位:
  1. 年初来高値／年初来安値の更新（終値）
  2. N日連続の上昇／下落（3日以上。過去1年で最長なら添える）
  3. N営業日ぶりの高値／安値（10営業日以上）
同じ順位の中では、N の大きいもの → 下の並び順（日経平均が先）。
"""
import sys
from datetime import datetime, time, timezone

import pandas as pd

NY, TK, LN = "America/New_York", "Asia/Tokyo", "Europe/London"
# (ticker, 呼び名, (足の日付の現地時刻で立会いが終わる時刻, そのタイムゾーン))。yf_latest の足は日付だけ（tz なし）
SERIES = [
    ("^N225", "日経平均", (time(15, 30), TK)),
    ("JPY=X", "ドル円", (time(23, 0), LN)),
    ("^TNX", "米10年金利", (time(16, 0), NY)),
    ("GC=F", "金", (time(17, 0), NY)),
    ("CL=F", "WTI原油", (time(17, 0), NY)),
    ("^VIX", "VIX", (time(16, 15), NY)),
    ("^GSPC", "S&P500", (time(16, 0), NY)),
]
MIN_STREAK = 3
MIN_SINCE = 10


def closed_only(c, close, now=None):
    """立会いが終わっていない最新の足（先物・為替の途中の値）を落とす"""
    if c is None or not len(c):
        return c
    close_t, tz = close
    now = pd.Timestamp(now or datetime.now(timezone.utc))
    last = c.index[-1]
    end = pd.Timestamp(datetime.combine(last.date(), close_t)).tz_localize(tz)
    if now.tz_convert(tz) < end:
        return c.iloc[:-1]
    return c


def _streak(vals, i):
    """vals[i] で終わる連続の上昇(+n)／下落(-n)の日数"""
    n, sign = 0, 0
    while i - n >= 1:
        d = vals[i - n] - vals[i - n - 1]
        s = 1 if d > 0 else -1 if d < 0 else 0
        if s == 0 or (sign and s != sign):
            break
        sign = s
        n += 1
    return n * sign


def record_of(c, name):
    """1つの系列の記録。戻り値: (順位, 大きさ, 文) または None"""
    c = c.dropna()
    if len(c) < 30:
        return None
    vals = [float(x) for x in c.values]
    dates = [x.date() for x in c.index]
    v, d = vals[-1], dates[-1]
    # 1. 年初来高値・安値（その年の2本目以降の足）
    ytd = [x for x, dd in zip(vals[:-1], dates[:-1]) if dd.year == d.year]
    if len(ytd) >= 5:
        if v > max(ytd):
            return (1, 0, f"{name}は年初来高値を更新")
        if v < min(ytd):
            return (1, 0, f"{name}は年初来安値を更新")
    # 2. N日連続
    s = _streak(vals, len(vals) - 1)
    if abs(s) >= MIN_STREAK:
        one_year = [i for i, dd in enumerate(dates) if (d - dd).days <= 365]
        past = [abs(_streak(vals, i)) for i in one_year[:-1] if i >= 1
                and (i + 1 >= len(vals) or (vals[i + 1] - vals[i]) * (vals[i] - vals[i - 1]) <= 0)]
        best = max(past) if past else 0
        tail = "（過去1年で最長）" if abs(s) > best else "（過去1年で最長タイ）" if abs(s) == best else ""
        return (2, abs(s), f"{name}は{abs(s)}日連続で{'上昇' if s > 0 else '下落'}{tail}")
    # 3. N営業日ぶりの高値・安値
    for kind, worse in (("高値", lambda x: x >= v), ("安値", lambda x: x <= v)):
        k = 0
        for x in reversed(vals[:-1]):
            if worse(x):
                break
            k += 1
        if k >= MIN_SINCE and k < len(vals) - 1:
            return (3, k, f"{name}は{k}営業日ぶりの{kind}")
    return None


def today_record(fetch=None, now=None, verbose=True):
    """今朝の1行。戻り値: {"text", "t", "rank", "date"} または None。取れなくても朝刊は止めない"""
    if fetch is None:
        from yf_latest import history_closes

        def fetch(t):
            return history_closes(t, verbose=False, period="2y")
    cands = []
    for k, (t, name, close_t) in enumerate(SERIES):
        try:
            c = closed_only(fetch(t), close_t, now)
            r = record_of(c, name) if c is not None else None
        except Exception as e:
            if verbose:
                print(f"  今日の記録 {t}: 取れず {e}", file=sys.stderr)
            continue
        if r:
            cands.append((r[0], -r[1], k, r[2], t, c.index[-1].date().isoformat()))
    if not cands:
        return None
    cands.sort()
    rank, _m, _k, text, t, d = cands[0]
    return {"text": text, "t": t, "rank": rank, "date": d}


if __name__ == "__main__":
    print(today_record())
