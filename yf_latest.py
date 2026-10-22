# -*- coding: utf-8 -*-
"""yfinance の日足の「最新の終値の取りこぼし」を防ぐ（2026-10-21）

■ 起きていたこと
yfinance の日足は、いちばん新しい取引日の足が「終値が空欄（NaN）」のまま返ってくることがある。
Yahoo 側が日足を確定させるまで時間がかかるためで、朝5〜6時台（AI朝刊の準備）や土曜の朝に多い。
これまでのコードは `["Close"].dropna()` で空欄の足を捨てていたので、
**前日ではなく2営業日前の終値が「直近の日経」になっていた**。

  10/14〜10/16・10/20・10/21 の朝刊が毎日1日古い終値を使い、
  10/16 の朝刊は「先物は現物比 −2.9% の安寄り示唆」（正しくは約 +2.1% の上振れ）と書いた。
  月曜だけ正しかったのは、金曜の足が週末のうちに確定していたから。

■ 直し方
空欄になった最新日の終値を、取引所の確定値（history_metadata の regularMarketPrice と
regularMarketTime）で埋める。**その日の立会いが終わっているときだけ**埋める（取引時間中の
途中の値で埋めない）。取れなかったときは、これまでどおり空欄を捨てるだけ（悪くはならない）。
"""
import sys

import pandas as pd


def _flat(c):
    """単一ティッカーでも列が MultiIndex になることがあるので Series にそろえる"""
    if hasattr(c, "columns"):
        c = c.iloc[:, 0]
    idx = pd.to_datetime(c.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    c = pd.Series(pd.to_numeric(c.values, errors="coerce"), index=idx.normalize())
    return c[~c.index.duplicated(keep="last")].sort_index()


def _epoch(v):
    """regularMarketTime などを UNIX 秒にそろえる。yfinance 1.7 から int ではなく
    pandas.Timestamp で返るようになり、int(ts) が落ちて全ティッカーの終値が取れなくなった
    （2026-10-22・10-23 の AI朝刊が欠番）。取れなければ None"""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return int(v)
    try:
        t = pd.Timestamp(v)
    except Exception:
        return None
    if t.tzinfo is None:
        t = t.tz_localize("UTC")
    return int(t.timestamp())


def fill_latest(ticker, c, verbose=True, until=None):
    """終値の Series（NaN を含んでよい）を受け取り、最新の取引日の終値が空欄なら
    取引所の確定値で埋めて、空欄を捨てて返す。
    until（この日より前だけ欲しいとき）を渡すと、その日以降の値では埋めない
    埋める処理で何が起きても、これまでどおり空欄を捨てるだけに戻す（終値ごと失わない）"""
    c = _flat(c)
    try:
        return _fill(ticker, c, verbose, until)
    except Exception as e:
        print(f"  {ticker}: 最新日の補完をあきらめた（{e}）", file=sys.stderr)
        return c.dropna()


def _fill(ticker, c, verbose, until):
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        t.history(period="5d", interval="1d", auto_adjust=False)
        md = t.history_metadata or {}
        price = md.get("regularMarketPrice")
        ts = md.get("regularMarketTime")
        tzname = md.get("exchangeTimezoneName") or "UTC"
        end = ((md.get("currentTradingPeriod") or {}).get("regular") or {}).get("end")
    except Exception:
        return c.dropna()
    ts, end = _epoch(ts), _epoch(end)
    if price is None or ts is None:
        return c.dropna()

    when = pd.Timestamp(ts, unit="s", tz="UTC").tz_convert(tzname)
    day = pd.Timestamp(when.date())
    today = pd.Timestamp.now(tz=tzname).date()
    # その日の立会いが終わっているときだけ（取引時間中の途中の値で埋めない）
    closed = day.date() < today or (end is not None and ts >= end)
    if not closed:
        return c.dropna()
    if until is not None and day >= pd.Timestamp(until):
        return c.dropna()     # 欲しい範囲の外（過去の日を取りにきたとき）

    valid = c.dropna()
    if len(valid) and valid.index[-1] >= day:
        return valid                      # 最新日の終値はもう入っている
    c = c.copy()
    c.loc[day] = float(price)
    out = c.sort_index().dropna()
    if verbose:
        prev = valid.index[-1].date() if len(valid) else None
        print(f"  {ticker}: {day.date()} の終値が空欄だったので確定値 {float(price):,.2f} で埋めた"
              f"（埋める前の最新は {prev}）", file=sys.stderr)
    return out


def closes(ticker, period="10d", verbose=True):
    """yf.download の終値に fill_latest をかけたもの（日付昇順・空欄なし）。取れなければ None"""
    try:
        import yfinance as yf
        df = yf.download(ticker, period=period, progress=False, auto_adjust=False, threads=False)
    except Exception as e:
        print(f"  {ticker}: 取得失敗 {e}", file=sys.stderr)
        return None
    if df is None or len(df) == 0:
        return None
    c = fill_latest(ticker, df["Close"], verbose=verbose)
    return c if len(c) else None


def history_closes(ticker, verbose=True, **kw):
    """yf.Ticker(t).history(**kw) の終値に fill_latest をかけたもの。取れなければ空の Series"""
    try:
        import yfinance as yf
        h = yf.Ticker(ticker).history(auto_adjust=False, **kw)
    except Exception as e:
        print(f"  {ticker}: 取得失敗 {e}", file=sys.stderr)
        return pd.Series(dtype=float)
    if h is None or len(h) == 0:
        return pd.Series(dtype=float)
    end = kw.get("end")
    c = fill_latest(ticker, h["Close"], verbose=verbose, until=end)
    if end is not None and len(c):
        c = c[c.index < pd.Timestamp(end)]
    return c
