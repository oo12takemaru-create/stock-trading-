# -*- coding: utf-8 -*-
"""規定値ポートフォリオの成績を docs/portfolio_stats.json に書き出す。

■ 何のためか（引継ぎ.md §19・起動文 2-4c・2026-09-06 Fable）
静的サイト（ruletrade.jp）が公開数字を読むための1枚。
**会員アプリの /dashboard 右カラムと同じ値になること**が要件なので、
どちらも `portfolio_results` 表の同じ行を出どころにする。
静的サイト側の読み込みは改装セッションが担当する。

  出力先: stock-trading- の docs/portfolio_stats.json
          （GitHub Pages で https://oo12takemaru-create.github.io/stock-trading-/portfolio_stats.json）

■ 「一つの定義・一つの数字」を守るために
サイトに出す数字をこのファイル以外から作らないこと。
値を変えたいときは `portfolio_engine.py` を直して再計算し、ここを出し直す。

    python precompute/export_portfolio_json.py --env-file ../ruletrade-app/.env.local
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import supabase_io  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_PATH = os.path.join(REPO_ROOT, "docs", "portfolio_stats.json")
RESULT_ID = "preset_v2_8_0"

DISCLAIMER = (
    "過去の検証結果であり、将来の成績を約束するものではありません。"
    "売買手数料・スリッページ・税金は考慮していません。"
    "規定値は書籍の検証条件であり、推奨ではありません。"
)


def log(msg):
    print("[%s] %s" % (dt.datetime.now().strftime("%H:%M:%S"), msg), flush=True)


def build(row: dict) -> dict:
    result = row["result"]
    s = result["summary"]
    basis = result["basis"]
    period = result["period"]

    return {
        # いつ時点の数字か
        "asof": row["computed_at"],
        "data_through": row["data_through"],
        "period": {"from": period["from"], "to": period["to"]},
        "universe_size": basis.get("tickers_used"),
        "universe_note": basis.get("universe"),

        # 成績
        "trades": s.get("trades"),
        "wins": s.get("wins"),
        "losses": s.get("losses"),
        "win_rate": s.get("win_rate"),
        "pf": s.get("pf"),
        "avg_return": s.get("avg_return"),
        "median_return": s.get("median_return"),
        "avg_win": s.get("avg_win"),
        "avg_loss": s.get("avg_loss"),
        "max_dd": s.get("max_dd"),
        "max_losing_streak": s.get("max_losing_streak"),
        "avg_hold_days": s.get("avg_hold_days"),
        "total_return": s.get("total_return"),
        "cagr": s.get("cagr"),

        # 出口の内訳（何で手仕舞ったか）
        "exit_reasons": result.get("exit_reasons", {}),

        # 戦略別・年別（サイトが使いたければ使う）
        "by_rule": result.get("by_rule", []),
        "yearly": result.get("yearly", []),

        # 「検証の基準」枠に出す定義（13_公開数字の検証基準.md §6-3）
        "definition": {
            "regime": basis.get("regime"),
            "price": basis.get("price"),
            "stop": basis.get("stop"),
            "position": basis.get("position"),
            "cost": basis.get("cost"),
            "rule_version": "v2.8.0",
            "rules": "BNF逆張り / モメンタム(20日高値ブレイク) / ミネルヴィニ・テンプレート の3本合算",
        },
        "disclaimer": DISCLAIMER,
    }


def main():
    p = argparse.ArgumentParser(description="公開数字を docs/portfolio_stats.json に出す")
    p.add_argument("--env-file", default="")
    p.add_argument("--out", default=OUT_PATH)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    if args.env_file:
        supabase_io.load_env_file(args.env_file)

    url, key = supabase_io.credentials()
    import urllib.request
    req = urllib.request.Request(
        "%s/rest/v1/portfolio_results?select=computed_at,data_through,params,result&id=eq.%s"
        % (url, RESULT_ID),
        headers={"apikey": key, "Authorization": "Bearer " + key})
    with urllib.request.urlopen(req, timeout=120) as r:
        rows = json.loads(r.read())
    if not rows:
        log("portfolio_results に %s がありません。先に build_portfolio.py を流してください。"
            % RESULT_ID)
        return 1

    payload = build(rows[0])
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"

    log("トレード %s / 勝率 %s%% / PF %s / 最大DD %s%% / 最大連敗 %s"
        % (payload["trades"], payload["win_rate"], payload["pf"],
           payload["max_dd"], payload["max_losing_streak"]))
    if args.dry_run:
        log("--dry-run のため書き出しません")
        print(text)
        return 0

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(text)
    log("書き出し: %s (%.1f KB)" % (args.out, os.path.getsize(args.out) / 1024))
    return 0


if __name__ == "__main__":
    sys.exit(main())
