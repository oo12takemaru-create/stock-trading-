# -*- coding: utf-8 -*-
"""予兆レーダーの遡及用: 全銘柄の日足 過去5年を yfinance から1回まとめて取る（2026-11-18）

■ 何に使うか
  ledger2_backfill.py（検証台帳 段階3）が、当日記録と同じ検出関数で過去の出来事を作るための株価。
  出力はリポジトリに入れない（.cache/px5y/。手動 workflow では actions/cache で途中経過を持ち越す）。
  リポジトリに残すのは、そこから作った出来事・答え・統計だけ。

■ 値の意味（読む側の注意。meta.json にも書く）
  - 取った日（実行日）の時点で Yahoo が見せている値。auto_adjust=False（配当は未調整）だが、
    分割は Yahoo が遡って直している＝「実行日時点の分割調整済み」。その日に見えていた値ではない。
  - 上場廃止・統合で今いない銘柄は入らない（生き残りの偏り）。
  - 分割の記録（Stock Splits）も一緒に取り、splits.json と合わせて「その日に見えていた値」へ戻すのに使う。

■ 束ねて回数を抑える・途中で止まっても続きから
  100銘柄ずつ1回の download。取り終えた銘柄は done.txt に1行ずつ足し、次に走ったときは残りだけ取る。
  1チャンク＝1ファイル（chunk_NNNN.csv.gz と chunk_NNNN.splits.json）。

使い方:
  python -X utf8 yochou_px5y.py [--out .cache/px5y] [--limit N（試験用）] [--period 5y]
"""
import argparse
import gzip
import io
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
OUT = ROOT / ".cache" / "px5y"
CHUNK = 100
WAIT = 2.0
COLS = ("date", "code", "open", "high", "low", "close", "adj_close", "volume")


def log(*a):
    print(*a, flush=True)


def fetch_chunk(tickers, period):
    import yfinance as yf
    df = yf.download(tickers, period=period, interval="1d", progress=False, auto_adjust=False,
                     actions=True, group_by="ticker", threads=True)
    out = {}
    if df is None or df.empty:
        return out
    for t in tickers:
        try:
            sub = df[t] if isinstance(df.columns, pd.MultiIndex) else df
            sub = sub.dropna(subset=["Close"])
            if len(sub):
                out[t] = sub
        except Exception:
            continue
    return out


def write_chunk(out, seq, got, code_of, today):
    from daily_bars import row_of
    rows, splits = [], {}
    for t, sub in got.items():
        code = code_of[t]
        for ix, r in sub.iterrows():
            d = ix.date() if hasattr(ix, "date") else ix
            if d >= today:
                continue                      # 実行日当日の足は途中のことがあるので入れない
            rows.append(row_of(code, d.isoformat(), r))
        if "Stock Splits" in sub.columns:
            s = sub["Stock Splits"]
            ev = [(ix.date().isoformat(), float(v)) for ix, v in s.items() if v and v == v and float(v) > 0]
            if ev:
                splits[code] = [{"ex": d, "ratio": v} for d, v in ev]
    rows.sort(key=lambda r: (r[0], r[1]))
    buf = io.StringIO()
    buf.write(",".join(COLS) + "\n")
    for r in rows:
        buf.write(",".join(r) + "\n")
    p = out / f"chunk_{seq:04d}.csv.gz"
    with open(p, "wb") as f:
        with gzip.GzipFile(filename="", mode="wb", fileobj=f, mtime=0, compresslevel=6) as gz:
            gz.write(buf.getvalue().encode("utf-8"))
    (out / f"chunk_{seq:04d}.splits.json").write_text(json.dumps(splits, ensure_ascii=False), encoding="utf-8")
    return len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--period", default="5y")
    ap.add_argument("--max-minutes", type=float, default=0, help="この分数を超えたら区切りの良いところで止める（続きは次回）")
    a = ap.parse_args()
    import movers_daily as md
    from daily_bars import JST, MARKET

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    uni = md.load_universe()
    if a.limit:
        uni = uni[:a.limit]
    code_of = {u["t"]: u["c"] for u in uni}
    code_of.update(MARKET)
    tickers = list(MARKET) + [u["t"] for u in uni]
    done_p = out / "done.txt"
    done = set(done_p.read_text(encoding="utf-8").split()) if done_p.exists() else set()
    todo = [t for t in tickers if t not in done]
    seq = len(list(out.glob("chunk_*.csv.gz")))
    log(f"ユニバース {len(tickers)}銘柄 / 取得済み {len(done & set(tickers))} / 残り {len(todo)}")
    today = datetime.now(JST).date()
    meta_p = out / "meta.json"
    meta = json.loads(meta_p.read_text(encoding="utf-8")) if meta_p.exists() else {}
    meta.setdefault("started", datetime.now(JST).isoformat(timespec="seconds"))
    meta.update({"period": a.period, "source": "yfinance download(auto_adjust=False, actions=True)",
                 "adjusted_asof": meta.get("adjusted_asof") or today.isoformat(),
                 "note": "実行日時点で Yahoo が分割を遡って直した値（配当は未調整）。上場廃止・統合で今いない銘柄は入らない。"})
    failed = []
    for i in range(0, len(todo), CHUNK):
        if a.max_minutes and (time.time() - t0) / 60 > a.max_minutes:
            log(f"{a.max_minutes}分を超えたので止める（続きは次回）")
            break
        ch = todo[i:i + CHUNK]
        try:
            got = fetch_chunk(ch, a.period)
        except Exception as e:
            log(f"  チャンク失敗: {e}")
            got = {}
        if not got:
            failed += ch
            time.sleep(WAIT * 5)
            continue
        n = write_chunk(out, seq, got, code_of, today)
        meta.setdefault("chunks", {})[f"chunk_{seq:04d}"] = today.isoformat()   # その束を取った日＝分割調整の基準日
        meta_p.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        seq += 1
        miss = [t for t in ch if t not in got]
        failed += miss
        with open(done_p, "a", encoding="utf-8") as f:
            for t in got:
                f.write(t + "\n")
        log(f"  {i + len(ch)}/{len(todo)}: {len(got)}/{len(ch)}銘柄・{n}行  {time.time() - t0:.0f}秒")
        time.sleep(WAIT)
    if failed:
        log(f"取れなかった {len(failed)}銘柄（次回もう一度だけ試す。上場廃止・売買なしは取れないまま）")
        tried_p = out / "failed_once.txt"
        before = set(tried_p.read_text(encoding="utf-8").split()) if tried_p.exists() else set()
        # 2回目も取れないものは done に入れて、もう試さない
        give_up = [t for t in failed if t in before]
        with open(done_p, "a", encoding="utf-8") as f:
            for t in give_up:
                f.write(t + "\n")
        tried_p.write_text("\n".join(sorted(before | set(failed))), encoding="utf-8")
    left = [t for t in tickers if t not in set(done_p.read_text(encoding="utf-8").split())] if done_p.exists() else tickers
    meta.update({"n_tickers": len(tickers), "left": len(left), "updated": datetime.now(JST).isoformat(timespec="seconds")})
    meta_p.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"完了: 残り {len(left)}銘柄・所要 {time.time() - t0:.0f}秒")
    return 0


if __name__ == "__main__":
    sys.exit(main())
