# -*- coding: utf-8 -*-
"""yfinance からの株価取得とローカルキャッシュ。

何度も検証で回すので、生の株価は cache/ に Parquet で保存し、
同じ期間の再実行ではダウンロードしない。

キャッシュの場所:
  cache/prices/adj/<TICKER>.csv   auto_adjust=True  （既存エンジンと同じ取得方法）
  cache/prices/raw/<TICKER>.csv   auto_adjust=False （日次スキャナと同じ取得方法）
  cache/global/<adj|raw>/<TICKER>.csv

★形式は CSV。Parquet だと pyarrow が要るが、GitHub Actions の requirements.txt には
  入っていない（既存18本のワークフローが使っていないので足したくない）。
  1銘柄10年で 200KB 程度なので CSV で困らない。（2026-09-06 に実際に踏んだ）

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

# yfinance はタイムゾーン情報を SQLite に貯める。既定の場所は共有なので、
# 同じマシンで別のワークフローと重なると OperationalError('database is locked')
# になり、その銘柄だけ取得に失敗する。
# 2026-09-06 にこれで ^GSPC が落ち、相場環境の判定が丸ごと変わった（§19-2）。
# 実行ごとに独立した場所を使わせて、そもそも競合させない。
try:
    _TZ_DIR = os.path.join(CACHE_DIR, "yf_tz")
    os.makedirs(_TZ_DIR, exist_ok=True)
    yf.set_tz_cache_location(_TZ_DIR)
except Exception:  # 古い yfinance にこの関数が無くても止めない
    pass

OHLCV = ["Open", "High", "Low", "Close", "Volume"]


def _cache_path(ticker, auto_adjust, kind="prices"):
    sub = "adj" if auto_adjust else "raw"
    d = os.path.join(CACHE_DIR, kind, sub)
    os.makedirs(d, exist_ok=True)
    safe = ticker.replace("^", "_IDX_").replace("/", "_")
    return os.path.join(d, safe + ".csv")


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


#: 1日でこれより大きく動いたら分割の取りこぼしを疑う（0.6倍以下 / 1.7倍以上）
SPLIT_SUSPECT_LOW, SPLIT_SUSPECT_HIGH = 0.6, 1.7
#: 記録された分割の日と、値が動いた日のずれの許容（日本は権利落ちが効力発生の前日）
SPLIT_DAY_TOLERANCE = 4
#: 値動きの倍率が 1/分割比 からどれだけずれてよいか
SPLIT_RATIO_TOLERANCE = 0.15


def fix_unadjusted_splits(df, ticker, log=print, splits=None):
    """★分割の記録があるのに、調整済みの値に反映されていない急変を直す★（2026-10-11）

    yfinance は分割を記録していても、調整済みの値（auto_adjust=True）に
    反映しないことがある。2026-10-11 に見つけた例:
        8227.T（しまむら） 分割 1:3 の記録は 2026-02-19
        調整済みの終値 2026-02-17 10,806円 → 02-18 3,634円（0.336倍）
    株価が3分の1になったように見え、検証期間の中で移動平均を歪めていた。
    日本は権利落ちが効力発生の前日なので、値が動くのは記録の前日になる。

    直し方: 値が動いた日より前の四本値を 分割比 で割り、出来高を 分割比 倍する。
    直すのは「記録された分割の近くで、倍率が 1/分割比 に合う急変」だけ。
    記録の無い急変は直さない（本物の暴落かもしれないので、勝手に消さない）。

    splits: 試験用に差し込める {日付: 分割比}。None なら yfinance に聞く。
    """
    if df is None or len(df) < 2:
        return df
    r = df["Close"] / df["Close"].shift(1)
    sus = r[(r <= SPLIT_SUSPECT_LOW) | (r >= SPLIT_SUSPECT_HIGH)].dropna()
    if sus.empty:
        return df

    if splits is None:
        try:
            raw = yf.Ticker(ticker).splits
            splits = {pd.Timestamp(d).tz_localize(None).normalize(): float(k)
                      for d, k in raw.items() if float(k) > 0}
        except Exception as e:      # 聞けなければ直さない（勝手に推測しない）
            log("  ★%s: 分割の記録を取れませんでした（%s）。急変は直さずに残します" % (ticker, e))
            return df

    df = df.copy()
    for day, ratio in sus.items():
        for sday, k in splits.items():
            if abs((sday - day).days) > SPLIT_DAY_TOLERANCE:
                continue
            want = 1.0 / k                      # 1:k の分割なら値は 1/k になる
            if abs(ratio / want - 1) > SPLIT_RATIO_TOLERANCE:
                continue
            before = df.index < day
            for c in ("Open", "High", "Low", "Close"):
                df.loc[before, c] = df.loc[before, c] / k
            df.loc[before, "Volume"] = df.loc[before, "Volume"] * k
            log("  ★%s: %s の %.3f倍 は分割（1:%g・記録 %s）の取りこぼし。"
                "それより前の値を直しました" % (ticker, day.date(), ratio, k, sday.date()))
            break
    return df


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
        return _normalize(pd.read_csv(p, index_col=0, parse_dates=True))
    except Exception:
        return None


def save_cache(ticker, df, auto_adjust, kind="prices"):
    if df is None or len(df) == 0:
        return
    df.to_csv(_cache_path(ticker, auto_adjust, kind), index=True)


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


def refetch_one(ticker, start, end, auto_adjust, attempts=4, pause=3.0, log=print):
    """1銘柄だけを、間隔を空けて単独で取り直す。

    `database is locked` のような一過性の失敗はまとめ取りの再試行では抜けにくい。
    threads を使わず1本ずつ・待ち時間を伸ばして粘る。
    取れたら DataFrame、ダメなら None。
    """
    for i in range(attempts):
        time.sleep(pause * (i + 1))
        try:
            raw = yf.download(ticker, start=start, end=end, progress=False,
                              auto_adjust=auto_adjust, threads=False)
        except Exception as e:
            log("    再取得 %d/%d 失敗: %s" % (i + 1, attempts, e))
            continue
        if raw is None or len(raw) == 0:
            log("    再取得 %d/%d: 空の応答" % (i + 1, attempts))
            continue
        df = _normalize(raw)
        if df is not None and len(df) > 0:
            log("    再取得 %d/%d 成功（%d 行）" % (i + 1, attempts, len(df)))
            return df
    return None


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
