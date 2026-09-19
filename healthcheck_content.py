# -*- coding: utf-8 -*-
"""公開JSONの「中身」を点検する（鮮度とは別・data-healthcheck.yml から呼ぶ）

■ なぜ要るか
  FREDが2026-08-15から取れなくなっていたのに、gauge.json は毎日更新され続けた。
  鮮度だけを見る healthcheck は「今日も更新されている」ので何も言わず、
  30日の前回値フォールバックが切れて未判定3つになった9/12以降も黙っていた。
  「毎日更新されているが中身は失敗」を捕まえる仕組みが無かった。

■ 1日では騒がない
  データ元が一時的に落ちるのは日常。**2営業日続いたとき**だけ問題として扱う。
  前日の状態は git の履歴から読む（状態ファイルを持たず、後から検証もできる）。

■ 判定は「明らかに壊れている」ときだけ
  件数のしきい値は平常値のおよそ半分に置く。少し減っただけで鳴る監視は、
  やがて誰も見なくなる。
"""
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))
HERE = Path(__file__).parent


def bizdays_between(d1, d2):
    """d1→d2 の営業日数（土日のみ除く。祝日は見ない＝安全側に厳しめ）"""
    n, cur = 0, d1
    while cur < d2:
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            n += 1
    return n


def prev_bizday(d):
    cur = d - timedelta(days=1)
    while cur.weekday() >= 5:
        cur -= timedelta(days=1)
    return cur


# ─── 個別の判定 ────────────────────────────────────────────
# 返り値: None なら正常、文字列なら「何が壊れているか」
def ck_gauge_unknown(d, ctx):
    n = d.get("unknown")
    if n is None:
        n = sum(1 for g in d.get("gauges", []) if g.get("on") is None)
    if n >= 2:
        return f"取得失敗が{n}個（5つ中）"
    return None


def ck_gauge_stale(d, ctx):
    # 前回値で代用している＝取得自体は失敗している。未判定になる前に気づくための早期警報。
    n = sum(1 for g in d.get("gauges", []) if g.get("stale"))
    if n >= 1:
        keys = "・".join(g.get("label") or g.get("key") for g in d.get("gauges", []) if g.get("stale"))
        return f"前回値で代用中が{n}個（{keys}）＝取得は失敗している"
    return None


def ck_crash_tradedate(d, ctx):
    """取得に失敗すれば crash_fetch.py は異常終了するが、価格が古いまま返ってきた場合は
    「更新はされたのに中身は一昨日」になる。ここはその取りこぼしを見る。

    ★基準はカレンダーではなくヒートマップの取引日★
      土日しか知らない日数計算で「何営業日前か」を測ると、連休のたびに誤検知する。
      同じ日の相場を見ている別のJSONと突き合わせれば、祝日を知らなくても判定できる。
    """
    td = d.get("trade_date")
    if not td:
        return "trade_date が無い"
    ref = (ctx.at("docs/heatmap.json") or {}).get("trade_date")
    if not ref:
        return None                     # 比べる相手がいなければ判定しない
    if td < ref:
        return f"trade_date が {td}（ヒートマップは {ref} を見ている＝古い相場を表示している）"
    return None


def ck_score3(d, ctx):
    if d.get("inputs_ok") is False:
        return "inputs_ok が false（入力が揃わないまま採点している）"
    return None


def ck_stale_flag(d, ctx):
    if d.get("stale") is True:
        return f"stale が true（中身は {d.get('asof') or d.get('target_date')} 時点）"
    return None


def ck_ai(d, ctx):
    lt = d.get("latest") or {}
    if not (lt.get("headline") or "").strip():
        return "本文（headline）が空"
    return None


def count_check(path, floor, what):
    def f(d, ctx):
        n = len(d.get(path, []))
        if n < floor:
            return f"{what}が{n}件（平常時の半分未満・しきい値{floor}）"
        return None
    return f


def ck_board(d, ctx):
    want = {"radar.json", "gauge.json", "crash.json", "karauri.json", "kessan.json",
            "shinyo.json", "investor_flow.json", "cot.json", "ai_analysis.json", "heatmap_agg"}
    miss = want - set((d.get("files") or {}).keys())
    if miss:
        return f"トップの要約に {'・'.join(sorted(miss))} が入っていない（元データを読めなかった）"
    return None


CHECKS = [
    ("docs/gauge.json",        "傾斜計（取得失敗）",     ck_gauge_unknown),
    ("docs/gauge.json",        "傾斜計（前回値で代用）", ck_gauge_stale),
    ("docs/crash.json",        "着火判定",               ck_crash_tradedate),
    ("docs/score3.json",       "3軸スコア",              ck_score3),
    ("docs/free_scanner.json", "無料スキャナー",         ck_stale_flag),
    ("docs/market_jiai.json",  "地合い",                 ck_stale_flag),
    ("docs/ai_analysis.json",  "AI朝刊",                 ck_ai),
    ("docs/board.json",        "トップの要約",           ck_board),
    ("docs/heatmap.json",      "ヒートマップ",           count_check("items", 200, "銘柄")),
    ("docs/karauri.json",      "空売り残高",             count_check("stocks", 700, "銘柄")),
    ("docs/tenbagger.json",    "十倍株スキャナー",       count_check("items", 10, "銘柄")),
    ("docs/investor_flow.json", "投資部門別",            count_check("items", 4, "主体")),
]


class Ctx:
    """「いつ時点の docs/ を見るか」をまとめたもの。

    今日ぶんは作業ツリーを、前営業日ぶんは git の履歴を読む。判定関数は
    どちらを渡されても同じコードで動く（着火判定のようにJSONをまたぐ判定があるため）。
    """

    def __init__(self, ref, cutoff=None):
        self.ref = ref
        self.cutoff = cutoff        # None なら「今」＝作業ツリー
        self._cache = {}

    def at(self, path):
        if path not in self._cache:
            try:
                self._cache[path] = (self._from_git(path) if self.cutoff
                                     else json.loads((HERE / path).read_text(encoding="utf-8")))
            except Exception:
                self._cache[path] = None
        return self._cache[path]

    def _from_git(self, path):
        sha = subprocess.run(
            ["git", "rev-list", "-1", f"--before={self.cutoff.isoformat()}", "HEAD", "--", path],
            cwd=HERE, capture_output=True).stdout.decode().strip()
        if not sha:
            return None
        blob = subprocess.run(["git", "show", f"{sha}:{path}"], cwd=HERE, capture_output=True).stdout
        return json.loads(blob.decode("utf-8"))


def main():
    now = datetime.now(JST)
    ref = (now - timedelta(hours=7)).date()          # 鮮度チェックと同じ基準日
    prev = prev_bizday(ref)
    today_ctx = Ctx(ref)
    prev_ctx = Ctx(prev, datetime.combine(prev, datetime.max.time(), JST))

    rows, problems = [], []
    for path, label, fn in CHECKS:
        d = today_ctx.at(path)
        if d is None:
            # 読めない・存在しない。鮮度チェック側でも「読込失敗」として出る
            rows.append(f"| {label} | ❌ | 読み込めない |")
            problems.append(f"**{label}**: {path} を読み込めない")
            continue
        try:
            today = fn(d, today_ctx)
        except Exception as e:
            today = f"判定できない（{e}）"
        if today is None:
            rows.append(f"| {label} | ✅ | - |")
            continue

        # 今日おかしい。前営業日も同じだったかを git から見る（1日で騒がないため）
        old = prev_ctx.at(path)
        if old is None:
            rows.append(f"| {label} | ⚠️ 今日だけ（前営業日の版が無い） | {today} |")
            continue
        try:
            yday = fn(old, prev_ctx)
        except Exception:
            yday = None
        if yday is not None:
            problems.append(f"**{label}**: {today}（前営業日も同じ状態＝2営業日連続）")
            rows.append(f"| {label} | ❌ 2営業日連続 | {today} |")
        else:
            rows.append(f"| {label} | ⚠️ 今日だけ（様子見） | {today} |")

    print(f"\n## 中身の点検（{now:%Y-%m-%d %H:%M} JST・前営業日={prev}）\n")
    print("更新はされているのに中身が失敗していないかを見る。**2営業日続いたときだけ**問題として扱う。\n")
    print("| データ | 状態 | 中身 |")
    print("|---|---|---|")
    print("\n".join(rows))
    if problems:
        print("\n### ⚠️ 要対応（中身）\n")
        for p in problems:
            print(f"- {p}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
