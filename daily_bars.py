# -*- coding: utf-8 -*-
"""全銘柄の当日の日足を 1日1ファイルで残す（検証台帳 v1 段階1の前準備・2026-11-18）

■ なぜ
  movers_daily.py は毎日 全銘柄×1年分の日足を Yahoo から取っているが、保存しているのは
  終値まわりの指標（prices.json・最新1日だけ）で、始値・高値・安値は残っていなかった。
  当日時点の記録は始めた日からしか溜まらないので、取ったものをそのまま1行ずつ残す。

■ 取得は増やさない
  movers_daily.py が書いた .cache/ohlc_recent.pkl（同じジョブ内・直近30本）を読むだけ。
  例外は市場全体の物差し2本（1306.T＝TOPIX の代わり・^N225＝日経平均。ユニバースに入っていない）で、
  これだけ yfinance を1回（2銘柄・1か月分）呼ぶ。取れなくても銘柄の分は書く。

■ 出力
  data/bars/YYYY/YYYY-MM-DD.csv.gz   列: date,code,open,high,low,close,adj_close,volume
  - docs/ の外（GitHub Pages には出さない）。
  - 1日1ファイル・一度書いたら二度と書き換えない（同じ日をもう一度走らせても何もしない＝重複しない・時点を守る）。
  - 休場日は書かない（Yahoo の最終行の日付＝取引日。その日のファイルがあれば終わる）。
  - その日の足が無い銘柄（取得失敗・売買停止）は行を作らない。件数は標準出力に出す。
  - gzip のヘッダ時刻は 0 に固定（同じ中身なら同じバイト列）。

■ 値の意味
  open/high/low/close/volume は yfinance auto_adjust=False の値（Yahoo は分割があると過去の値を
  遡って直すので、ここに残るのは「その日の夕方に見えていた値」）。adj_close は配当・分割調整後の終値。

使い方:
  python -X utf8 daily_bars.py                     # ジョブの中（movers_daily.py の直後）
  python -X utf8 daily_bars.py --out <dir> --cache <pkl>   # 試験用（本番の場所に書かない）
"""
import argparse
import gzip
import io
import math
import pickle
import sys
from collections import Counter
from datetime import datetime, time as dtime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "bars"
CACHE = ROOT / ".cache" / "ohlc_recent.pkl"
MARKET = {"1306.T": "1306", "^N225": "^N225"}   # TOPIX の代わり（jukyu_events）・日経平均（ledger_score）
CLOSE_JST = dtime(15, 45)                        # 東証の大引け 15:30 ＋余裕。これより前は当日の足が途中
COLS = ("date", "code", "open", "high", "low", "close", "adj_close", "volume")


def log(*a):
    print(*a, flush=True)


def num(x, nd=4):
    """数字を短い文字列に（2850.0 → 2850 / 1234.56789 → 1234.5679）。欠けは空欄"""
    try:
        f = float(x)
    except (TypeError, ValueError):
        return ""
    if math.isnan(f) or math.isinf(f):
        return ""
    s = f"{f:.{nd}f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def row_of(code, d, r):
    def g(k):
        return r[k] if k in r.index else None
    vol = g("Volume")
    try:
        vol = "" if vol is None or math.isnan(float(vol)) else str(int(round(float(vol))))
    except (TypeError, ValueError):
        vol = ""
    # 四本値は小数2桁（東証の呼値は最小0.1円・指数は2桁。Yahoo の float32 の端数 59007.398438 を落とす）。
    # 調整後終値は配当調整で端数が出るので4桁
    return [d, code, num(g("Open"), 2), num(g("High"), 2), num(g("Low"), 2), num(g("Close"), 2),
            num(g("Adj Close"), 4), vol]


def bar_on(sub, d):
    """sub（日付インデックスの DataFrame）から d の行を1本。無ければ None"""
    for i in range(len(sub) - 1, -1, -1):
        ix = sub.index[i]
        dd = ix.date() if hasattr(ix, "date") else ix
        if dd == d:
            r = sub.iloc[i]
            if "Close" in r.index and r["Close"] == r["Close"]:   # NaN でない
                return r
            return None
        if dd < d:
            return None
    return None


def fetch_market(trade_date):
    """1306.T と ^N225 だけ取る（1回）。失敗したら空"""
    try:
        import pandas as pd
        import yfinance as yf
        df = yf.download(list(MARKET), period="1mo", interval="1d", progress=False,
                         auto_adjust=False, group_by="ticker", threads=False)
    except Exception as e:
        log(f"::warning::1306.T / ^N225 の取得に失敗（銘柄の分は書く）: {e}")
        return []
    rows = []
    for t, code in MARKET.items():
        try:
            sub = df[t] if isinstance(df.columns, pd.MultiIndex) else df
            sub = sub.dropna(subset=["Close"])
            r = bar_on(sub, trade_date)
            if r is None:
                log(f"  {t}: {trade_date} の足が無い（欠けたまま）")
                continue
            rows.append(row_of(code, trade_date.isoformat(), r))
        except Exception as e:
            log(f"  {t}: 読めない（欠けたまま）: {e}")
    return rows


def write_gz(path, rows):
    buf = io.StringIO()
    buf.write(",".join(COLS) + "\n")
    for r in rows:
        buf.write(",".join(r) + "\n")
    raw = buf.getvalue().encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as f:
        with gzip.GzipFile(filename="", mode="wb", fileobj=f, mtime=0, compresslevel=9) as gz:
            gz.write(raw)
    tmp.replace(path)
    return len(raw), path.stat().st_size


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default=str(CACHE))
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--no-market", action="store_true", help="1306.T / ^N225 を取りに行かない（試験用）")
    a = ap.parse_args()
    out = Path(a.out)

    try:
        with open(a.cache, "rb") as f:
            c = pickle.load(f)
    except Exception as e:
        log(f"::warning::日足のキャッシュが読めない（今日の分は残せない）: {e}")
        return 0
    uni, data = c.get("uni") or [], c.get("data") or {}
    if not data:
        log("::warning::キャッシュに日足が無い。書かずに終了")
        return 0

    # 取引日 = 最終行の日付の最頻値（movers_daily.py と同じ決め方）
    last = Counter()
    for sub in data.values():
        if len(sub):
            ix = sub.index[-1]
            last[ix.date() if hasattr(ix, "date") else ix] += 1
    trade_date = last.most_common(1)[0][0]
    now = datetime.now(JST)
    log(f"取引日: {trade_date}（キャッシュ {c.get('saved', '?')}）")

    if trade_date > now.date():
        log(f"::warning::取引日 {trade_date} が今日より先。書かずに終了")
        return 0
    if trade_date == now.date() and now.time() < CLOSE_JST:
        log(f"大引け前（{now:%H:%M} JST）なので当日の足は途中。書かずに終了")
        return 0

    path = out / f"{trade_date:%Y}" / f"{trade_date.isoformat()}.csv.gz"
    if path.exists():
        log(f"{path.name} はもうある。書き換えない（休場日・同じ日の2回目の実行）")
        return 0

    rows, miss = [], 0
    seen = set()
    for u in uni:
        t, code = u.get("t"), u.get("c")
        if not t or not code or code in seen:
            continue
        seen.add(code)
        sub = data.get(t)
        r = bar_on(sub, trade_date) if sub is not None else None
        if r is None:
            miss += 1
            continue
        rows.append(row_of(code, trade_date.isoformat(), r))
    n_stock = len(rows)
    mk = [] if a.no_market else fetch_market(trade_date)
    rows += mk
    rows.sort(key=lambda r: r[1])

    if n_stock < len(seen) * 0.5:
        log(f"::warning::当日の足がある銘柄が半分に届かない（{n_stock}/{len(seen)}）。取得の障害とみなし書かない")
        return 0

    raw, gz = write_gz(path, rows)
    log(f"日足を保存: {path.relative_to(out.parent) if out.parent in path.parents else path}"
        f"  銘柄 {n_stock}/{len(seen)}（欠け {miss}）＋市場 {len(mk)}/{len(MARKET)}"
        f"  {raw / 1024:.0f}KB → gzip {gz / 1024:.0f}KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
