# -*- coding: utf-8 -*-
"""規定値ポートフォリオの10年カーブを docs/equity_curve.json に書き出す。

■ 何のためか（2026-09-21 Fable・意匠設計書「データを絵にする」）
静的サイト（ruletrade.jp）が10年のエクイティカーブを描くための1枚。
portfolio_stats.json と **同じ portfolio_results 行・同じ asof** から作る。

  出力先: docs/equity_curve.json
          （https://oo12takemaru-create.github.io/stock-trading-/equity_curve.json）

■ ここは計算しない（案A）
曲線そのものは portfolio_run.py が result["equity_curve"] に保存する。
このスクリプトは**読んで形を整えるだけ**。
理由は単純で、正本と別に計算すると、同じ画面に出る「最大DD −27.1%」と
曲線の谷がずれても誰も気づけないから。summary と曲線は同じ final・
同じ歩き方（portfolio_engine.monthly_equity_curve）から出る。

■ 出さないもの
円建ての値（残高・損益額）は入れない。口座規模が逆算できるため。
export_portfolio_json.py の FORBIDDEN_KEYS をそのまま借りて機械で止める。

    python precompute/export_equity_curve.py --env-file ../ruletrade-app/.env.local
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import supabase_io  # noqa: E402
# ★写さずに借りる★ 同じ決まりが2か所にあると、片方だけ直る日が来る
from export_portfolio_json import (  # noqa: E402
    FORBIDDEN_KEYS, RESULT_ID, _date_only, log, r,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_PATH = os.path.join(REPO_ROOT, "docs", "equity_curve.json")
STATS_PATH = os.path.join(REPO_ROOT, "docs", "portfolio_stats.json")

SCHEMA_VERSION = 1

# 丸めの差ぶんだけ許す。曲線は小数2桁・summary は1桁で持っているため。
CUM_TOLERANCE = 0.06


def build(row: dict) -> dict:
    result = row["result"]
    s = result["summary"]
    period = result["period"]
    curve = result.get("equity_curve")

    if not curve:
        raise SystemExit(
            "result に equity_curve がありません。\n"
            "  portfolio_run.py に月次カーブを足したあと、"
            "build_portfolio.py を流し直してください（古い行には入っていません）。"
        )

    points = [
        {
            "month": p["month"],
            "cum_return_pct": r(p["cum_return_pct"], 2),
            "dd_pct": r(p["dd_pct"], 1),
            "dd_min_pct": r(p["dd_min_pct"], 1),
            "trades": int(p["trades"]),
        }
        for p in curve
    ]

    max_dd = s.get("max_dd")
    return {
        "schema": SCHEMA_VERSION,
        "asof": _date_only(row["computed_at"]),
        "version": (result.get("basis") or {}).get("rule_version") or "v2.8.0",
        "period_start": _date_only(period["from"]),
        "period_end": _date_only(period["to"]),

        # 曲線の起点。0% から始まる（円は持たない）
        "base_pct": 0,
        "points": points,

        # ★画面の数字と突き合わせるための控え★
        # サイトは曲線とこの値を同時に出す。食い違ったまま配らないよう、
        # 下の validate() が書き出し前に一致を確かめる。
        "totals": {
            "trades": s.get("trades"),
            "win_rate": r(s.get("win_rate"), 1),
            "pf": r(s.get("pf"), 2),
            "max_dd_pct": r(-abs(float(max_dd)), 1) if max_dd is not None else None,
            "cum_return_pct": r(s.get("total_return"), 1),
            "cagr_pct": r(s.get("cagr"), 1),
        },
    }


def validate(d, stats=None) -> list:
    """公開前の自己点検。1件でも引っかかったら書き出さない。"""
    errs = []

    for k in ("schema", "asof", "version", "period_start", "period_end",
              "points", "totals"):
        if d.get(k) is None:
            errs.append("必須キーが無い/None: %s" % k)
    if errs:
        return errs

    pts = d["points"]
    tot = d["totals"]

    if len(pts) < 12:
        errs.append("点が %d 個しかありません（10年のカーブとして少なすぎます）" % len(pts))

    # ★月が飛んでいないこと★ 抜けると横軸が詰まって形そのものが変わる
    months = [p["month"] for p in pts]
    if len(set(months)) != len(months):
        errs.append("同じ月が2回出ています")
    for a, b in zip(months, months[1:]):
        ya, ma = int(a[:4]), int(a[5:7])
        expect = "%04d-%02d" % (ya + 1, 1) if ma == 12 else "%04d-%02d" % (ya, ma + 1)
        if b != expect:
            errs.append("月が飛んでいます: %s の次が %s（%s のはず）" % (a, b, expect))
            break

    # ★曲線の谷と、公開している最大DDが一致すること★
    #   ここがこの検査の中心。実際より浅い谷を描いて配るのがいちばん悪い形で、
    #   2026-09-21 にサイトで起きたのがまさにその型（−27.1 を −24.2 と配った）。
    if tot.get("max_dd_pct") is not None:
        deepest = min(p["dd_min_pct"] for p in pts)
        if abs(deepest - tot["max_dd_pct"]) > 0.05:
            errs.append(
                "曲線の谷 %.1f%% が 最大DD %.1f%% と一致しません"
                % (deepest, tot["max_dd_pct"])
            )

    # 曲線の終点が累積リターンと一致すること
    if tot.get("cum_return_pct") is not None and pts:
        if abs(pts[-1]["cum_return_pct"] - tot["cum_return_pct"]) > CUM_TOLERANCE:
            errs.append(
                "曲線の終点 %.2f%% が 累積リターン %.1f%% と一致しません"
                % (pts[-1]["cum_return_pct"], tot["cum_return_pct"])
            )

    # 月ごとの決済件数の合計が、公開しているトレード数と一致すること
    if isinstance(tot.get("trades"), int):
        n = sum(p["trades"] for p in pts)
        if n != tot["trades"]:
            errs.append("月ごとの件数の合計 %d が トレード数 %d と合いません"
                        % (n, tot["trades"]))

    # ドローダウンは 0 以下。月末のDDが、その月の最深より深いことはない
    for p in pts:
        if p["dd_pct"] > 0 or p["dd_min_pct"] > 0:
            errs.append("%s のドローダウンが正の値です" % p["month"])
            break
        if p["dd_min_pct"] > p["dd_pct"] + 0.05:
            errs.append("%s の月内最深DDが月末DDより浅いです" % p["month"])
            break

    # ★円が漏れていないこと★ export_portfolio_json.py と同じ決まりで見る
    def scan(node, path=""):
        if isinstance(node, dict):
            for k, v in node.items():
                if k in FORBIDDEN_KEYS:
                    errs.append("公開してはいけないキーが混じっている: %s%s" % (path, k))
                scan(v, "%s%s." % (path, k))
        elif isinstance(node, list):
            for i, v in enumerate(node):
                scan(v, "%s[%d]." % (path, i))

    scan(d)

    for k in ("asof", "period_start", "period_end"):
        try:
            dt.date.fromisoformat(d.get(k, ""))
        except (ValueError, TypeError):
            errs.append("%s が YYYY-MM-DD でない: %r" % (k, d.get(k)))

    # ★portfolio_stats.json と同じ行から出ていること★
    #   サイトは2枚を同じ画面に出す。asof や合計が食い違う2枚を配らない。
    if stats:
        if stats.get("asof") != d.get("asof"):
            errs.append("portfolio_stats.json の asof %s と違います（%s）"
                        % (stats.get("asof"), d.get("asof")))
        for key, mine in (("trades", "trades"), ("max_dd_pct", "max_dd_pct"),
                          ("cum_return_pct", "cum_return_pct"), ("pf", "pf")):
            a, b = stats.get(key), tot.get(mine)
            if a is not None and b is not None and a != b:
                errs.append("portfolio_stats.json の %s=%s と違います（%s）" % (key, a, b))

    return errs


def load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return None


def _write_summary(errs):
    """GitHub Actions のジョブ要約に出す（ログを開かなくても気づけるように）。"""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write("## エクイティカーブの更新を止めました\n\n")
            f.write("前回の `docs/equity_curve.json` をそのまま残しています。\n\n")
            for e in errs:
                f.write("- %s\n" % e)
    except OSError:
        pass


def main():
    p = argparse.ArgumentParser(description="10年カーブを docs/equity_curve.json に出す")
    p.add_argument("--env-file", default="")
    p.add_argument("--out", default=OUT_PATH)
    p.add_argument("--stats", default=STATS_PATH,
                   help="突き合わせる portfolio_stats.json（無ければ突き合わせない）")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--from-json", default="",
                   help="Supabase の代わりに portfolio_results 行の JSON から作る（検証用）")
    args = p.parse_args()

    if args.from_json:
        with open(args.from_json, encoding="utf-8") as f:
            rows = [json.load(f)]
    else:
        if args.env_file:
            supabase_io.load_env_file(args.env_file)
        url, key = supabase_io.credentials()
        import urllib.request
        req = urllib.request.Request(
            "%s/rest/v1/portfolio_results?select=computed_at,data_through,params,result&id=eq.%s"
            % (url, RESULT_ID),
            headers={"apikey": key, "Authorization": "Bearer " + key})
        with urllib.request.urlopen(req, timeout=120) as r_:
            rows = json.loads(r_.read())
        if not rows:
            log("portfolio_results に %s がありません。先に build_portfolio.py を流してください。"
                % RESULT_ID)
            return 1

    payload = build(rows[0])
    errs = validate(payload, load_json(args.stats))

    if errs:
        log("::error::カーブの検証に失敗したので書き出しません（前回のJSONを残します）")
        for e in errs:
            log("  - " + e)
        _write_summary(errs)
        return 1

    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    pts = payload["points"]
    log("%s 〜 %s の %d か月 / 終点 %.1f%% / 谷 %.1f%% / asof %s"
        % (pts[0]["month"], pts[-1]["month"], len(pts),
           pts[-1]["cum_return_pct"],
           min(x["dd_min_pct"] for x in pts), payload["asof"]))
    if args.dry_run:
        log("--dry-run のため書き出しません")
        print(text)
        return 0

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    log("書き出し: %s (%.1f KB)" % (args.out, os.path.getsize(args.out) / 1024))
    return 0


if __name__ == "__main__":
    sys.exit(main())
