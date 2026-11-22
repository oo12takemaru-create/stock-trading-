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


def prev_closes(out, trade_date):
    """trade_date より前の、いちばん新しい data/bars の終値 {code: close}（無ければ {}）"""
    import csv
    files = sorted(p for p in Path(out).glob("*/*.csv.gz") if p.name[:10] < trade_date.isoformat())
    if not files:
        return {}, None
    p = files[-1]
    out_ = {}
    with gzip.open(p, "rt", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                out_[r["code"]] = float(r["close"])
            except (TypeError, ValueError):
                pass
    return out_, p.name[:10]


def guard_rows(rows, out, trade_date, splits=None):
    """株価の汚れの守り（2026-11-21・px_sanity）。当日記録は「その日に見えた値」をそのまま残すので、
    書かないのは壊れているとはっきり言える行だけ: 終値が 0以下・1億円超、または前の記録の日の終値から
    10倍・1/10（px_sanity.BIG_JUMP。高い方が50円以上）に飛び、その前後3日に分割・併合の記録（docs/splits.json）が無いもの。
    2.5倍の線（px_sanity の規則2）は使わない（値幅制限の拡大が続くと、200円前後の株は本当に2.5倍を超えることがある。
    その並びの直しは、読む側の ledger2_panel・similar_weekly が px_sanity で行う）。戻り値 (書く行, 書かない [(code, 理由)])"""
    import px_sanity as S
    from datetime import date as _d
    prev, prev_d = prev_closes(out, trade_date)
    if splits is None:
        try:
            import json
            sj = json.loads((ROOT / "docs" / "splits.json").read_text(encoding="utf-8"))
        except Exception:
            sj = {}
        splits = {}
        for c, lst in (sj.get("by_code") or {}).items():
            for x in lst or []:
                if x.get("ex") and x.get("ratio"):
                    splits.setdefault(c, []).append((x["ex"], float(x["ratio"])))
        for key in ("upcoming", "recent"):
            for x in sj.get(key) or []:
                if x.get("code") and x.get("ex") and x.get("ratio"):
                    splits.setdefault(str(x["code"]), []).append((x["ex"], float(x["ratio"])))
    keep, skipped = [], []
    for r in rows:
        code = r[1]
        try:
            c = float(r[5])
        except (TypeError, ValueError):
            keep.append(r)                          # 終値が欠けた行は今までどおり（読む側が捨てる）
            continue
        if not (0 < c <= S.MAX_PRICE):
            skipped.append((code, f"終値 {c:,.1f}"))
            continue
        p = prev.get(code)
        if p and p > 0:
            ratio = c / p
            if max(p, c) >= S.JUMP_MIN_PRICE and (ratio >= S.BIG_JUMP or ratio <= 1 / S.BIG_JUMP):
                near = [rr for ex, rr in splits.get(code, [])
                        if abs((_d.fromisoformat(ex) - trade_date).days) <= S.SPLIT_NEAR and abs(ratio * rr - 1) <= S.SPLIT_TOL]
                if not near:
                    skipped.append((code, f"{prev_d} {p:,.1f}→{c:,.1f}（{ratio:.3g}倍）"))
                    continue
        keep.append(r)
    return keep, skipped


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
    mk = [] if a.no_market else fetch_market(trade_date)
    rows, skipped = guard_rows(rows + mk, out, trade_date)
    n_stock = sum(1 for r in rows if r[1] not in MARKET.values())
    mk = [r for r in rows if r[1] in MARKET.values()]
    rows.sort(key=lambda r: r[1])
    if skipped:
        log(f"  株価の検査で書かなかった行 {len(skipped)}: {skipped[:10]}")

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
