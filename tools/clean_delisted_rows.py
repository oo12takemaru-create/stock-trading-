# -*- coding: utf-8 -*-
"""正本（Supabase の daily_metrics）から、上場廃止した銘柄の「最終売買日より後の行」を消す道具。

■ なぜ要るか（2026-10-24・Fable 判断）
  上場廃止した銘柄（例: 7240.T・9508.T。2026-10-01 の株式移転で 9/29 に廃止、最終売買日 9/28）の
  最終売買日より後の行が、前計算の修正前に正本へ入っている。
    ・提供元が埋めた行（売買の無い日を前日の終値で埋めた「四本値同じ」の行。day_change に
      配当落ちぶんの見かけの値動きが出ている）
    ・旧コードに流れた後継の新会社の値（新コードは別の銘柄として扱う）
  会員が新しい日を含めて回すと、この行で約定・決済が起きうる。
  前計算はもう書かない（pipeline.run_pipeline が最終売買日で閉じる）ので、残っている行を消す。

■ 何を消すか
  docs/delisted.json（最終売買日は precompute/delisting.py の1か所）にあって、正本に入っている銘柄
  （universe.ACTIVE_TICKERS）の、
  **最終売買日より後の日付の行だけ**。最終売買日以前の行（検証期間を含む）には触らない。

■ 使い方（本人が実行する。CI からは呼ばない）
    python tools/clean_delisted_rows.py --dry   --env-file ../ruletrade-app/.env.local
        … 消す行の一覧と件数を出すだけ（読み取りのみ）
    python tools/clean_delisted_rows.py --apply --expect N --env-file ../ruletrade-app/.env.local
        … N は --dry が出した件数。食い違えば何もしないで止まる
          1) 消す前に控え（gz）を保存して読み戻す
          2) DELETE を1行ずつ。1行消えたことを返り値で確かめ、読み戻して無いことを確かめる
          3) 最後にもう一度数えて 0 行であることを確かめる
    python tools/clean_delisted_rows.py --rollback 控え.json.gz --expect N --env-file ...
        … 控えから1行ずつ戻す（既にある行は、同じ値なら飛ばし、違えば止まる）。読み戻して確かめる
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import math
import os
import sys
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "precompute"))

import delisting  # noqa: E402
import supabase_io  # noqa: E402
from universe import ACTIVE_TICKERS  # noqa: E402

TABLE = "daily_metrics"
#: 1銘柄で最終売買日より後に残っている行がこれ以上なら、何かがおかしいので止める
MAX_ROWS_PER_TICKER = 200
DEFAULT_BACKUP_DIR = os.path.join(os.path.expanduser("~"), "stock-trading-db-backups")


def log(msg):
    print(msg, flush=True)


def _get(query):
    url, key = supabase_io.credentials()
    _, body = supabase_io._request("GET", "%s/rest/v1/%s?%s" % (url, TABLE, query), key)
    return json.loads(body.decode("utf-8"))


def _key_query(ticker, date):
    return "ticker=eq.%s&date=eq.%s" % (urllib.parse.quote(ticker), date)


def targets():
    """{ticker: 最終売買日} … 正本に入っている銘柄（ACTIVE_TICKERS）のうち、今日より前に履歴が終わった銘柄"""
    today = dt.date.today()
    return {t: d for t, d in delisting.last_trading_days(list(ACTIVE_TICKERS)).items()
            if d.date() < today}


def rows_after_last_day(ends):
    """[(ticker, 最終売買日, row), ...] 最終売買日より後の行を全部（日付順）"""
    out = []
    for t, last in sorted(ends.items()):
        q = ("select=*&ticker=eq.%s&date=gt.%s&order=date.asc&limit=%d"
             % (urllib.parse.quote(t), last.date().isoformat(), MAX_ROWS_PER_TICKER + 1))
        rows = _get(q)
        if len(rows) > MAX_ROWS_PER_TICKER:
            raise SystemExit("%s の最終売買日より後の行が %d 行を超えています。表か正本を疑ってください。"
                             "何もしないで止めます。" % (t, MAX_ROWS_PER_TICKER))
        out += [(t, last, r) for r in rows]
    return out


def _flat(r):
    """四本値が同じ（売買の無い日を埋めた形）か"""
    vals = [r.get(c) for c in ("open", "high", "low", "close")]
    return None not in vals and max(vals) == min(vals)


def show(found, ends):
    log("対象の銘柄（docs/delisted.json にあり、正本に入っている・最終売買日が今日より前）: %d" % len(ends))
    for t, d in sorted(ends.items()):
        stored = _get("select=date,close&ticker=eq.%s&date=eq.%s" % (urllib.parse.quote(t), d.date()))
        log("  %s 最終売買日 %s（正本のその日の行: %s）"
            % (t, d.date(), "あり" if stored else "★無い"))
    log("")
    log("消す行（最終売買日より後）:")
    log("  %-8s %-10s %10s %10s %10s %10s %9s %8s  %s"
        % ("ticker", "date", "open", "high", "low", "close", "day_chg", "vol_r20", "形"))
    for t, _, r in found:
        log("  %-8s %-10s %10s %10s %10s %10s %9s %8s  %s"
            % (t, r["date"], r.get("open"), r.get("high"), r.get("low"), r.get("close"),
               r.get("day_change"), r.get("vol_ratio_20"),
               "四本値同じ（埋めた行）" if _flat(r) else "値動きあり（旧コードに流れた値）"))
    by = {}
    for t, _, _ in found:
        by[t] = by.get(t, 0) + 1
    log("")
    log("件数: %d 行（%s）" % (len(found), "、".join("%s=%d" % kv for kv in sorted(by.items())) or "なし"))


def write_backup(found, ends, backup_dir):
    os.makedirs(backup_dir, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(backup_dir, "daily_metrics_delisted_rows_%s.json.gz" % stamp)
    doc = {"created": dt.datetime.now().isoformat(timespec="seconds"), "table": TABLE,
           "last_trade": {t: d.date().isoformat() for t, d in ends.items()},
           "rows": [r for _, _, r in found]}
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False)
    with gzip.open(path, "rt", encoding="utf-8") as f:     # 読み戻して確かめる
        back = json.load(f)
    assert len(back["rows"]) == len(found), "控えの行数が合いません"
    assert back["rows"] == doc["rows"], "控えの中身が書いたものと違います"
    return path


def _same(a, b):
    """正本の1行どうしが同じか（real 列の丸めは相対 1e-6 まで許す）"""
    if set(a) != set(b):
        return False
    for k in a:
        x, y = a[k], b[k]
        if isinstance(x, (int, float)) and isinstance(y, (int, float)) \
                and not isinstance(x, bool) and not isinstance(y, bool):
            if not math.isclose(float(x), float(y), rel_tol=1e-6, abs_tol=1e-9):
                return False
        elif x != y:
            return False
    return True


def apply(found, ends, expect, backup_dir):
    if expect is None or expect != len(found):
        raise SystemExit("★消す行は %d 行です。--expect %s と合わないので、何もしないで止めます。"
                         "--dry で一覧を見てから --expect に件数を渡してください。"
                         % (len(found), expect))
    if not found:
        log("消す行はありません。何もしません。")
        return 0
    path = write_backup(found, ends, backup_dir)
    log("控えを保存して読み戻しました: %s（%d 行）" % (path, len(found)))

    url, key = supabase_io.credentials()
    deleted = 0
    for t, _, r in found:
        q = _key_query(t, r["date"])
        _, body = supabase_io._request("DELETE", "%s/rest/v1/%s?%s" % (url, TABLE, q), key,
                                       None, {"Prefer": "return=representation"})
        gone = json.loads(body.decode("utf-8") or "[]")
        if len(gone) != 1:
            raise SystemExit("★%s %s を消したら %d 行が返りました（1行のはず）。ここで止めます。"
                             "控え: %s" % (t, r["date"], len(gone), path))
        if _get("select=date&" + q):
            raise SystemExit("★%s %s を消したのに、読み戻すとまだあります。ここで止めます。控え: %s"
                             % (t, r["date"], path))
        deleted += 1
        log("  消した: %s %s" % (t, r["date"]))
    left = rows_after_last_day(ends)
    assert deleted == expect, "消した行数 %d が --expect %d と違います" % (deleted, expect)
    if left:
        raise SystemExit("★消したあとも最終売買日より後の行が %d 行あります（途中で書かれた？）。控え: %s"
                         % (len(left), path))
    log("完了: %d 行を消しました。最終売買日より後の行は 0 行です。控え: %s" % (deleted, path))
    log("戻すとき: python tools/clean_delisted_rows.py --rollback %s --expect %d" % (path, deleted))
    return 0


def rollback(path, expect, dry):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        doc = json.load(f)
    rows = doc.get("rows") or []
    assert doc.get("table") == TABLE, "控えの表が %s ではありません" % TABLE
    log("控え %s: %d 行（作成 %s）" % (path, len(rows), doc.get("created")))
    todo = []
    for r in rows:
        q = _key_query(r["ticker"], r["date"])
        now = _get("select=*&" + q)
        if now and _same(now[0], r):
            log("  既にある（同じ値）ので飛ばす: %s %s" % (r["ticker"], r["date"]))
            continue
        if now:
            raise SystemExit("★%s %s は正本に別の値で入っています。上書きしないで止めます。"
                             % (r["ticker"], r["date"]))
        todo.append(r)
    log("戻す行: %d" % len(todo))
    if dry:
        for r in todo:
            log("  %s %s close=%s" % (r["ticker"], r["date"], r.get("close")))
        return 0
    if expect is None or expect != len(todo):
        raise SystemExit("★戻す行は %d 行です。--expect %s と合わないので、何もしないで止めます。"
                         % (len(todo), expect))
    url, key = supabase_io.credentials()
    for r in todo:
        _, body = supabase_io._request("POST", "%s/rest/v1/%s" % (url, TABLE), key, [r],
                                       {"Prefer": "return=representation"})
        got = json.loads(body.decode("utf-8") or "[]")
        if len(got) != 1:
            raise SystemExit("★%s %s を戻したら %d 行が返りました（1行のはず）。ここで止めます。"
                             % (r["ticker"], r["date"], len(got)))
        back = _get("select=*&" + _key_query(r["ticker"], r["date"]))
        if len(back) != 1 or not _same(back[0], r):
            raise SystemExit("★%s %s を戻したあと、読み戻した値が控えと違います。ここで止めます。"
                             % (r["ticker"], r["date"]))
        log("  戻した: %s %s" % (r["ticker"], r["date"]))
    log("完了: %d 行を戻しました。" % len(todo))
    return 0


def parse_args():
    p = argparse.ArgumentParser(description="上場廃止した銘柄の、最終売買日より後の行を正本から消す")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry", action="store_true", help="消す行の一覧と件数を出すだけ（読み取りのみ）")
    g.add_argument("--apply", action="store_true", help="控えを保存してから1行ずつ消す")
    g.add_argument("--rollback", metavar="BACKUP", help="控え（.json.gz）から1行ずつ戻す")
    p.add_argument("--expect", type=int, help="--apply / --rollback で扱う行数（--dry の件数）")
    p.add_argument("--preview", action="store_true", help="--rollback で、戻す行を出すだけ")
    p.add_argument("--backup-dir", default=DEFAULT_BACKUP_DIR, help="控えの置き場所")
    p.add_argument("--env-file", default="")
    return p.parse_args()


def main():
    args = parse_args()
    if args.env_file:
        supabase_io.load_env_file(args.env_file)
    if args.rollback:
        return rollback(args.rollback, args.expect, args.preview)
    ends = targets()
    found = rows_after_last_day(ends)
    show(found, ends)
    if args.dry:
        log("\n--dry のため何も消していません。消すときは --apply --expect %d" % len(found))
        return 0
    return apply(found, ends, args.expect, args.backup_dir)


if __name__ == "__main__":
    sys.exit(main())
