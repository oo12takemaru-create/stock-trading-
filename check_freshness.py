# -*- coding: utf-8 -*-
"""公開JSONの鮮度を1か所で判定する。

■ 何のためか（指示書 §14-2 / 引継ぎ.md §19「発見 2026-09-09」・Fable）
GitHub Actions の schedule は**起動しないことがある**。2026-09-05(金)は
free-scanner が丸ごと起動せず、9/7ぶんも5時間半遅れて 9/8 00:59 に走ったため、
公開JSONの対象日が4日間そのまま残った。失敗記録が出ないので気づけない。

対策は2つで、どちらもこのスクリプトを使う。
  (b) 本命 cron の60〜90分後に予備 cron を置き、
      **当日ぶんが既にあるならスキップ**する（冪等ガード）
  (c) 翌朝 7:00 JST の健全性チェックで、前営業日ぶんが無ければ**失敗**させる

■ 使い方
    python check_freshness.py --target free_scanner            # 鮮度を表示するだけ
    python check_freshness.py --target free_scanner --quiet    # 終了コードだけ
      終了コード 0 = 最新（予備実行は要らない）
      終了コード 1 = 古い（予備実行すべき／健全性チェックなら失敗）

    # ワークフローからはこう使う（新鮮ならステップを飛ばす）
    if python check_freshness.py --target free_scanner --quiet; then
      echo "当日ぶんは取得済み。スキップします"; exit 0
    fi

■ 「新鮮」の定義
`asof`（そのJSONが見ている最新営業日）が **前営業日以上**なら新鮮。
当日の終値が固まるのは 15:00 JST 以降なので、それより前・土日は前営業日までしか
確定していない。**祝日は考慮していない**（土日だけ飛ばす）ため、祝日明けは
「古い」と出ることがある。黙って古いデータを配るよりは安全側なので、この粗さで運用する。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))

# 対象ごとに「どのファイルの・どのキーを見るか」。
# キーは上から順に探し、最初に見つかったものを asof として使う。
TARGETS = {
    "free_scanner": {
        "path": "docs/free_scanner.json",
        # asof は 2026-09-09 に足したキー。それ以前に生成された JSON には無いので
        # target_date（仕様として1営業日遅れる）で代用する。その場合は
        # default_lag=1 のぶん甘く見ないと、正常なのに「古い」と出てしまう。
        "keys": ["asof", "target_date"],
        "lag_when_key": {"target_date": 1},
        "label": "無料版スキャナー",
    },
    "market_jiai": {
        "path": "docs/market_jiai.json",
        # market_jiai は遅延なし（最新終値で判定）なので target_date でも遅れ0
        "keys": ["asof", "target_date"],
        "label": "地合い専用JSON",
    },
    "radar": {
        "path": "docs/radar.json",
        # radar.json は日付キーを持たず updated（生成時刻）しかない
        "keys": ["asof", "target_date", "updated"],
        "label": "株レーダー地合い",
    },
    "portfolio_stats": {
        "path": "docs/portfolio_stats.json",
        "keys": ["asof"],
        "label": "公開数字",
    },
    "signals_log": {
        # 日次シグナルは CSV。最終行の scan_date を見る
        "path": "signals_log.csv",
        "csv_column": "scan_date",
        "label": "日次シグナル",
    },
}


def prev_business_day(d):
    """土日を飛ばして1営業日戻る（祝日は考慮しない）。"""
    d -= timedelta(days=1)
    while d.weekday() >= 5:          # 土(5)・日(6)
        d -= timedelta(days=1)
    return d


def latest_settled_business_day(now=None):
    """いま時点で終値が確定しているはずの直近営業日。"""
    now = now or datetime.now(JST)
    d = now.date()
    if now.hour < 15 or d.weekday() >= 5:
        d = prev_business_day(d)
    return d


def _date_of(value):
    """'2026-09-08' でも '2026-09-08T23:42:45+09:00' でも日付にする。"""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(JST).date()
    except ValueError:
        try:
            return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
        except ValueError:
            return None


def read_asof(spec):
    """対象の asof（見ている最新営業日）を返す。読めなければ None。"""
    path = spec["path"]
    if not os.path.exists(path):
        return None, "ファイルがありません: %s" % path

    if "csv_column" in spec:
        import csv
        last = None
        with open(path, encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                v = row.get(spec["csv_column"])
                if v:
                    last = v
        return _date_of(last), None

    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (ValueError, OSError) as e:
        return None, "読めません: %s" % e

    for k in spec["keys"]:
        if data.get(k):
            # どのキーで代用したかによって、許容する遅れが変わる
            lag = spec.get("lag_when_key", {}).get(k, 0)
            return _date_of(data[k]), None if lag == 0 else "__lag__%d" % lag
    return None, "日付のキーが見つかりません（探した: %s）" % ", ".join(spec["keys"])


def main():
    ap = argparse.ArgumentParser(description="公開JSONの鮮度を判定する")
    ap.add_argument("--target", required=True, choices=sorted(TARGETS),
                    help="どの出力を見るか")
    ap.add_argument("--quiet", action="store_true", help="終了コードだけ返す")
    ap.add_argument("--allow-lag-days", type=int, default=0,
                    help="この営業日数までの遅れは新鮮とみなす（既定0）")
    args = ap.parse_args()

    spec = TARGETS[args.target]
    asof, err = read_asof(spec)
    expected = latest_settled_business_day()

    # 代用キーを使ったぶんの遅れ（例: asof が無く target_date で見た場合の1営業日）
    key_lag = 0
    if err and err.startswith("__lag__"):
        key_lag = int(err[len("__lag__"):])
        err = None

    limit = expected
    for _ in range(max(args.allow_lag_days, 0) + key_lag):
        limit = prev_business_day(limit)

    fresh = asof is not None and asof >= limit
    if not args.quiet:
        print("対象      : %s (%s)" % (spec["label"], spec["path"]))
        print("asof      : %s" % (asof or "取得できず"))
        print("期待       : %s 以降" % limit)
        print("判定       : %s" % ("最新" if fresh else "古い"))
        if err:
            print("メモ       : %s" % err)
        if not fresh and asof:
            print("※ 実行が飛んだか、大きく遅れています。")
    return 0 if fresh else 1


if __name__ == "__main__":
    sys.exit(main())
