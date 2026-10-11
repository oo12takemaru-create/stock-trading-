# -*- coding: utf-8 -*-
"""正本（daily_metrics）を日付つきのファイルに保存する／そこから戻す。

    python precompute/snapshot_material.py save    --env-file ../ruletrade-app/.env.local
    python precompute/snapshot_material.py restore --file daily_metrics_2026-10-11.csv.gz \\
                                                   --env-file ../ruletrade-app/.env.local --yes

■ なぜ要るか（2026-10-11）
過去の材料を固定した（Fable 判断・本人承認）。固定した材料は、
**壊れたときに戻せるよう日付つきで保存しておく**決まり。

2026-10-02 には月次の取り直しで yfinance が10銘柄の価格を壊して返し
（東京海上の 2018-09-20 の終値が 1,350円ではなく 0.6円など）、
それが正本を上書きして公開数字が4ポイント良く見えた。
**提供元は黙って壊れた値を返すことがある**ので、正しいと確かめた材料を手元に持つ。

■ 保存先について
出したファイルは **非公開の** stock-trading-private の Release に置く。
stock-trading- は公開リポジトリで、10年分の価格を丸ごと置くと
データの再配布になるため、そこには置かない。

■ ここは黙って進まない
・読んだ行数は、先に数えた件数と一致しなければ止める
  （PostgREST は limit を大きく書いても黙って1000行しか返さない）
・戻すときは、ファイルの行数と銘柄数を確かめてから消す。--yes が無ければ消さない
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import hashlib
import io
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import supabase_io  # noqa: E402
from universe import ACTIVE_TICKERS  # noqa: E402

TABLE = "daily_metrics"
PAGE = 1000


def log(msg):
    print("[%s] %s" % (dt.datetime.now().strftime("%H:%M:%S"), msg), flush=True)


def _get(endpoint, key, what, attempts=4):
    import time
    import urllib.error
    last = None
    for i in range(attempts):
        try:
            _, body = supabase_io._request("GET", endpoint, key)
            return json.loads(body.decode("utf-8"))
        except (urllib.error.URLError, OSError) as e:
            last = e
            time.sleep(2.0 * (i + 1))
    raise SystemExit("%s を読めませんでした（%d回試行）: %s" % (what, attempts, last))


def read_all():
    """daily_metrics を全列・全行読む。銘柄ごとに読み、件数と突き合わせる。"""
    total = supabase_io.count_rows(TABLE)
    if total == 0:
        raise SystemExit("daily_metrics に1行もありません。")
    url_base, key = supabase_io.credentials()

    def one(ticker):
        out, off = [], 0
        while True:
            ep = ("%s/rest/v1/%s?select=*&ticker=eq.%s&order=date.asc&limit=%d&offset=%d"
                  % (url_base, TABLE, ticker, PAGE, off))
            part = _get(ep, key, ticker)
            out.extend(part)
            if len(part) < PAGE:
                return out
            off += PAGE

    rows = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        for part in ex.map(one, ACTIVE_TICKERS):
            rows.extend(part)
    # ★数が合わなければ止める★ 一覧に無い銘柄の行が残っていても、ここで分かる
    if len(rows) != total:
        raise SystemExit(
            "daily_metrics は %s 行ありますが、一覧の %d 銘柄ぶんを読んだら %s 行でした。"
            % (f"{total:,}", len(ACTIVE_TICKERS), f"{len(rows):,}"))
    return rows


def save(args):
    log("daily_metrics を全列・全行読みます")
    rows = read_all()
    cols = sorted({k for r in rows for k in r})
    # 列の並びを固定する（毎回同じ並びで書く＝差分を比べられる）
    first = ["ticker", "date"]
    cols = first + [c for c in cols if c not in first]

    day = dt.date.today().isoformat()
    out = args.out or "daily_metrics_%s.csv.gz" % day
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, lineterminator="\n")
    w.writeheader()
    for r in sorted(rows, key=lambda r: (r["ticker"], r["date"])):
        w.writerow(r)
    data = buf.getvalue().encode("utf-8")
    # mtime を固定して、同じ中身なら同じバイト列になるようにする
    with open(out, "wb") as f:
        with gzip.GzipFile(fileobj=f, mode="wb", mtime=0) as gz:
            gz.write(data)

    tickers = len({r["ticker"] for r in rows})
    dates = sorted({r["date"] for r in rows})
    sha = hashlib.sha256(open(out, "rb").read()).hexdigest()
    log("書き出し: %s（%.1f MB）" % (out, os.path.getsize(out) / 1e6))
    log("  行 %s / 銘柄 %d / 列 %d / 期間 %s 〜 %s"
        % (f"{len(rows):,}", tickers, len(cols), dates[0], dates[-1]))
    log("  sha256 %s" % sha)
    return 0


def restore(args):
    if not args.file:
        raise SystemExit("--file で戻すファイルを指定してください。")
    with gzip.open(args.file, "rt", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    tickers = {r["ticker"] for r in rows}
    log("ファイル: %s 行 / 銘柄 %d" % (f"{len(rows):,}", len(tickers)))
    # ★消す前に中身を確かめる★ 空や欠けたファイルで正本を消さない
    if set(ACTIVE_TICKERS) - tickers:
        raise SystemExit("ファイルに無い銘柄があります: %s"
                         % ", ".join(sorted(set(ACTIVE_TICKERS) - tickers)[:10]))
    if len(rows) < 200 * len(ACTIVE_TICKERS):
        raise SystemExit("行数が少なすぎます（%d）。壊れたファイルの可能性があります。" % len(rows))
    if not args.yes:
        raise SystemExit("正本を消して戻します。よければ --yes を付けて実行してください。")

    # CSV は全部文字列なので、空は None に、数値は数値に戻す
    def clean(r):
        out = {}
        for k, v in r.items():
            if v == "":
                out[k] = None
            elif k in ("ticker", "date", "universe_version"):
                out[k] = v
            elif v in ("True", "False", "true", "false"):
                out[k] = v.lower() == "true"
            else:
                try:
                    out[k] = float(v)
                except ValueError:
                    out[k] = v
        return out

    recs = [clean(r) for r in rows]
    supabase_io.truncate_metrics()
    supabase_io.drop_secondary_indexes(log=log)
    supabase_io.upsert(TABLE, recs, "date,ticker", log=log)   # build_metrics.py と同じ衝突キー
    supabase_io.rebuild_secondary_indexes(log=log)
    n = supabase_io.count_rows(TABLE)
    if n != len(recs):
        raise SystemExit("戻した後の行数 %s がファイルの %s と合いません。" % (n, len(recs)))
    log("戻しました: %s 行" % f"{n:,}")
    return 0


def main():
    p = argparse.ArgumentParser(description="正本の保存と復元")
    p.add_argument("action", choices=["save", "restore"])
    p.add_argument("--env-file", default="")
    p.add_argument("--out", default="")
    p.add_argument("--file", default="")
    p.add_argument("--yes", action="store_true")
    args = p.parse_args()
    if args.env_file:
        supabase_io.load_env_file(args.env_file)
    return save(args) if args.action == "save" else restore(args)


if __name__ == "__main__":
    sys.exit(main())
