# -*- coding: utf-8 -*-
"""過去1年分の日足を「遡って取った分（origin=backfill）」として1回だけ書き出す（2026-11-18）

当日記録（daily_bars.py → data/bars/）とは混ぜない。置き場を分け、各行に origin=backfill を入れる。
  data/bars_backfill/YYYY-MM.csv.gz   列: date,code,open,high,low,close,adj_close,volume,origin

■ 当日記録との違い（読む側の注意）
  - 取った日（実行日）の時点で Yahoo が見せている値。その後の分割は遡って直っている（＝その日の夕方に見えた値ではない）。
  - 上場廃止・統合で今いない銘柄は入らない（生き残りの偏り）。
■ 取得
  movers_daily.py と同じユニバース・同じ取り方（200銘柄ずつ・間に2秒・1年分）＋ 1306.T・^N225。約15分。
  手元に1年分のキャッシュは無い（movers のキャッシュは直近30本だけ）ので、この1回だけ取る。
  workflow_dispatch（bars-backfill.yml）で手動実行する。毎日は走らない。
■ 1回だけ
  data/bars_backfill/ に既に csv.gz があれば何もしない（--force で上書き）。
  実行日当日の行は入れない（当日分は daily_bars.py の担当）。

使い方:
  python -X utf8 bars_backfill.py [--out <dir>] [--force] [--limit N（試験用・先頭N銘柄だけ）]
"""
import argparse
import gzip
import io
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import pandas as pd

import movers_daily as md
from daily_bars import COLS, JST, MARKET, row_of

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "bars_backfill"


def log(*a):
    print(*a, flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    out = Path(a.out)
    if not a.force and out.exists() and any(out.glob("*.csv.gz")):
        log(f"{out} にもう書いてある。1回だけの約束なので何もしない（やり直すなら --force）")
        return 0

    t0 = time.time()
    uni = md.load_universe()
    if a.limit:
        uni = uni[:a.limit]
    tickers = [u["t"] for u in uni]
    log(f"ユニバース: {len(tickers)}銘柄")
    data = md.fetch_all(tickers)
    log(f"取得: {len(data)}/{len(tickers)}銘柄  {time.time() - t0:.0f}秒")

    import yfinance as yf
    try:
        df = yf.download(list(MARKET), period="1y", interval="1d", progress=False,
                         auto_adjust=False, group_by="ticker", threads=False)
        for t in MARKET:
            sub = (df[t] if isinstance(df.columns, pd.MultiIndex) else df).dropna(subset=["Close"])
            if len(sub):
                data[t] = sub
    except Exception as e:
        log(f"::warning::1306.T / ^N225 が取れない: {e}")

    today = datetime.now(JST).date()
    code_of = {u["t"]: u["c"] for u in uni}
    code_of.update(MARKET)
    by_month = defaultdict(list)
    n = 0
    for t, sub in data.items():
        code = code_of.get(t)
        if not code:
            continue
        sub = sub.dropna(subset=["Close"])
        for ix, r in sub.iterrows():
            d = ix.date() if hasattr(ix, "date") else ix
            if d >= today:
                continue
            by_month[f"{d:%Y-%m}"].append(row_of(code, d.isoformat(), r) + ["backfill"])
            n += 1

    out.mkdir(parents=True, exist_ok=True)
    total = 0
    for m, rows in sorted(by_month.items()):
        rows.sort(key=lambda r: (r[0], r[1]))
        buf = io.StringIO()
        buf.write(",".join(COLS + ("origin",)) + "\n")
        for r in rows:
            buf.write(",".join(r) + "\n")
        p = out / f"{m}.csv.gz"
        with open(p, "wb") as f:
            with gzip.GzipFile(filename="", mode="wb", fileobj=f, mtime=0, compresslevel=9) as gz:
                gz.write(buf.getvalue().encode("utf-8"))
        total += p.stat().st_size
        log(f"  {p.name}: {len(rows)}行  {p.stat().st_size / 1024:.0f}KB")
    log(f"backfill: {n}行・{len(by_month)}ファイル・計 {total / 1024 / 1024:.1f}MB  所要 {time.time() - t0:.0f}秒")
    return 0


if __name__ == "__main__":
    sys.exit(main())
