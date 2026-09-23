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
import re
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
    # 10年カーブ。portfolio_stats.json と**必ず同じコミットで出る**ので、
    # 判定の基準もそちらと同じでよい（2026-09-23）。
    # data-healthcheck の日次レポート側には置かない。あちらは土日しか見ず、
    # 祝日を知らないため休場明けに必ず「古い」と誤報する。
    "equity_curve": {
        "path": "docs/equity_curve.json",
        "keys": ["asof"],
        "label": "10年カーブ",
    },
    "signals_log": {
        # 日次シグナルは CSV。最終行の scan_date を見る
        "path": "signals_log.csv",
        "csv_column": "scan_date",
        "label": "日次シグナル",
    },
}


# ★祝日表は cron-worker/src/holidays.js が唯一の正本★（2026-09-23）
#   Python 側に同じ日付を写すと、片方だけ直る日が必ず来る。
#   JS の表をそのまま読む（"YYYY-MM-DD" の羅列なので素直に拾える）。
_HOLIDAYS_JS = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "cron-worker", "src", "holidays.js")

#: 対応年ごとに最低これだけ休場日があるはず（日本の祝日は年16日以上＋年末年始）
MIN_HOLIDAYS_PER_YEAR = 10


def _load_holidays():
    """(休場日の集合, 表が対応している年の集合) を返す。"""
    try:
        with open(_HOLIDAYS_JS, encoding="utf-8") as f:
            src = f.read()
    except OSError as e:
        raise SystemExit("休場日表を読めません: %s (%s)" % (_HOLIDAYS_JS, e))

    days = set(re.findall(r'"(\d{4}-\d{2}-\d{2})"', src))
    m = re.search(r"KNOWN_YEARS\s*=\s*\[([^\]]*)\]", src)
    years = {int(y) for y in re.findall(r"\d{4}", m.group(1))} if m else set()

    # ★0件を成功と見なさない★
    #   読み方が壊れると、祝日を知らないまま「古い」と言い続けることになる。
    if not days or not years:
        raise SystemExit(
            "休場日表から日付(%d件)・対応年(%d件)を取れませんでした。\n"
            "  cron-worker/src/holidays.js の書き方が変わった可能性があります。"
            % (len(days), len(years))
        )

    # ★「0件でない」だけでは足りない★（2026-09-23）
    #   表の書き方が少し変わって**大半が拾えなくなっても**、1件でも残れば
    #   上の検査は通ってしまう。実際に手元で踏んだ（翌年の元日だけ残り通過）。
    #   日本の祝日は年16日以上あり、年末年始の休場も足される。
    #   対応年それぞれに最低10件は無いとおかしい。
    for y in sorted(years):
        n = sum(1 for d in days if d.startswith("%d-" % y))
        if n < MIN_HOLIDAYS_PER_YEAR:
            raise SystemExit(
                "休場日表の %d 年が %d 件しかありません（最低 %d 件のはず）。\n"
                "  読み方が壊れているか、表が書きかけです。\n"
                "  祝日を知らないまま判定すると、休場日に誤って『古い』と出ます。"
                % (y, n, MIN_HOLIDAYS_PER_YEAR)
            )
    return days, years


_HOLIDAYS, _KNOWN_YEARS = _load_holidays()


def is_trading_day(d):
    """東証が開いている日か。表の範囲外の年は土日だけで判定する。"""
    if d.weekday() >= 5:
        return False
    if d.year not in _KNOWN_YEARS:
        # 表に無い年は祝日を知らない。止めずに「開いている」側へ倒すが、
        # 気づけるよう警告を出す（cron-worker の Worker と同じ振る舞い）。
        print("::warning::休場日表に %d 年がありません。祝日でも営業日として判定します。"
              % d.year, file=sys.stderr)
        return True
    return d.isoformat() not in _HOLIDAYS


def prev_business_day(d):
    """1営業日戻る（土日と休場日を飛ばす）。"""
    for _ in range(30):
        d -= timedelta(days=1)
        if is_trading_day(d):
            return d
    raise SystemExit("30日さかのぼっても営業日が見つかりません。休場日表を疑ってください。")


def latest_settled_business_day(now=None):
    """いま時点で終値が確定しているはずの直近営業日。

    ★祝日を見ること★（2026-09-23）
      以前は土日と15時だけで判定していたため、祝日が平日に来ると
      「その日の終値があるはず」と誤判定した。2026-09-22（国民の休日）と
      09-23（秋分の日）に、この検査が実際に failure を出している。
      **赤が常態化すると、本当に止まった日に気づけなくなる。**
    """
    now = now or datetime.now(JST)
    d = now.date()
    if now.hour < 15 or not is_trading_day(d):
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
