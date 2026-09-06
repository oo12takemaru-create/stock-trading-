# -*- coding: utf-8 -*-
"""yfinance からの株価取得とローカルキャッシュ。

何度も検証で回すので、生の株価は cache/ に Parquet で保存し、
同じ期間の再実行ではダウンロードしない。

キャッシュの場所:
  cache/prices/adj/<TICKER>.parquet   auto_adjust=True  （既存エンジンと同じ取得方法）
  cache/prices/raw/<TICKER>.parquet   auto_adjust=False （日次スキャナと同じ取得方法）
  cache/global/<adj|raw>/<TICKER>.parquet

再取得したいときは --refresh を付けるか、cache/ を消す。
"""
from __future__ import annotations

import os
import time
import warnings

import pandas as pd

warnings.filterwarnings("ignore")

import yfinance as yf  # noqa: E402

from config import CACHE_DIR  # noqa: E402

OHLCV = ["Open", "High", "Low", "Close", "Volume"]


def _cache_path(ticker, auto_adjust, kind="prices"):
    sub = "adj" if auto_adjust else "raw"
    d = os.path.join(CACHE_DIR, kind, sub)
    os.makedirs(d, exist_ok=True)
    safe = ticker.replace("^", "_IDX_").replace("/", "_")
    return os.path.join(d, safe + ".parquet")


def _normalize(df):
    """列を OHLCV に揃え、index を tz なしの日付に正規化する。"""
    if df is None or len(df) == 0:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df = df.copy()
        df.columns = df.columns.get_level_values(0)
    missing = [c for c in OHLCV if c not in df.columns]
    if missing:
        return None
    df = df[OHLCV].copy()
    idx = pd.to_datetime(df.index)
    try:
        idx = idx.tz_localize(None)
    except (TypeError, AttributeError):
        try:
            idx = idx.tz_convert(None)
        except (TypeError, AttributeError):
            pass
    df.index = pd.DatetimeIndex(idx).normalize()
    df = df[~df.index.duplicated(keep="last")].sort_index()
    df = df.dropna(subset=["Close"])
    return df if len(df) else None


def _covers(df, start, end):
    """キャッシュが要求期間をカバーしているか。
    末尾は「最新営業日の少し前まであればよい」ので7日の余裕を見る。"""
    if df is None or len(df) == 0:
        return False
    start = pd.Timestamp(start)
    end = pd.Timestamp(end)
    return df.index.min() <= start + pd.Timedelta(days=10) and \
        df.index.max() >= end - pd.Timedelta(days=7)


def load_cached(ticker, auto_adjust, kind="prices"):
    p = _cache_path(ticker, auto_adjust, kind)
    if not os.path.exists(p):
        return None
    try:
        return _normalize(pd.read_parquet(p))
    except Exception:
        return None


def save_cache(ticker, df, auto_adjust, kind="prices"):
    if df is None or len(df) == 0:
        return
    df.to_parquet(_cache_path(ticker, auto_adjust, kind), index=True)


def download_batch(tickers, start, end, auto_adjust, retries=2, pause=1.0):
    """複数銘柄をまとめて取得し、{ticker: DataFrame} を返す（失敗分は入らない）。"""
    result = {}
    for attempt in range(retries + 1):
        remaining = [t for t in tickers if t not in result]
        if not remaining:
            break
        try:
            raw = yf.download(remaining, start=start, end=end, progress=False,
                              auto_adjust=auto_adjust, group_by="ticker",
                              threads=True)
        except Exception:
            raw = None
        if raw is None or len(raw) == 0:
            time.sleep(pause * (attempt + 1))
            continue
        if len(remaining) == 1:
            df = _normalize(raw)
            if df is not None:
                result[remaining[0]] = df
        else:
            lv0 = set(raw.columns.get_level_values(0))
            for t in remaining:
                if t not in lv0:
                    continue
                df = _normalize(raw[t])
                if df is not None:
                    result[t] = df
        if len([t for t in tickers if t not in result]) == 0:
            break
        time.sleep(pause * (attempt + 1))
    return result


def get_prices(tickers, start, end, auto_adjust=True, kind="prices",
               refresh=False, batch_size=25, log=print):
    """キャッシュを使いつつ株価を取得する。{ticker: DataFrame} を返す。"""
    out = {}
    need = []
    for t in tickers:
        if not refresh:
            c = load_cached(t, auto_adjust, kind)
            if _covers(c, start, end):
                out[t] = c.loc[(c.index >= pd.Timestamp(start)) & (c.index <= pd.Timestamp(end))]
                continue
        need.append(t)

    log("  キャッシュ命中 %d 件 / 要ダウンロード %d 件" % (len(out), len(need)))
    failed = []
    for i in range(0, len(need), batch_size):
        chunk = need[i:i + batch_size]
        got = download_batch(chunk, start, end, auto_adjust)
        for t in chunk:
            if t in got:
                save_cache(t, got[t], auto_adjust, kind)
                out[t] = got[t]
            else:
                failed.append(t)
        log("  取得 %d/%d 件（失敗累計 %d）" % (min(i + batch_size, len(need)), len(need), len(failed)))
    if failed:
        log("  ★取得失敗（スキップ）: %s" % ", ".join(failed))
    return out, failed
