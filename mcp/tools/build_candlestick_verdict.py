# -*- coding: utf-8 -*-
"""酒田五法12本の検証結果を MCP が返せる JSON にする（書籍32）。

■ 何に使うか
「三尊天井が出たら売りか」「赤三兵は買いか」に、**検証した結果**で答えるための
元データ。`docs/candlestick_verdict.json` → MCP の `get_candlestick_verdict`。

■ この検証のいちばん大事な点
**12本すべてが不採用**だった。採用ゼロ本である。
中でも三空叩き込みは PF 3.04・勝率66.7% と最も見栄えのする数字だが、
「発見期と確認期に分けると符号が反転する」ため落ちている。
**PF が高くても採用できない理由**は他のどこにも書いていない。これを返すのが
このツールの値打ちで、「効きます」と言うためのものではない。

元 JSON 自身が `"policy": "不採用も必ず開示する"` と宣言している。それに従う。

■ verdict は2値（Fable 判断 2026-09-17）
  not_adopted      … 12本すべて。採用基準を満たさなかった
  filter_candidate … 逆三尊のみ併記。トレードルールとしては不採用だが別枠で保持

`adopted` は使わない。元データに1本も無いため。

■ 出すもの・出さないもの（法務の線）
  出す   … 形の名前・方向の分類・統計（取引回数・勝率・PF・平均超過・p値・
           負け年・感応度・5基準のどれで落ちたか）・不採用理由・期間・出所
  出さない … 銘柄名・銘柄コード・個別銘柄の値動き・価格系列
           **行動の指示**（「買え」「買うな」「売れ」の類）

★逆三尊の不採用理由だけは差し替える★
元の理由には「『この形が出た直後は買わない』という回避フィルターの候補」
という**検証者の設計メモ**が入っている。これは社内の判断材料であって、
外に出すと行動の指示に読める。事実（不採用・別枠で保持）までに留める。
差し替えたことは `verify_candlestick_verdict.py` が明示的に確かめる。

    python mcp/tools/build_candlestick_verdict.py
"""
from __future__ import annotations

import io
import json
import os
import sys

SRC = os.path.join(
    r"C:\Users\kawamura takeshi\書籍販売",
    "32_本間宗久_酒田五法", "検証", "ruletrade_sakata_rules.json")
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "docs", "candlestick_verdict.json")

# 形の別名。エージェントは日本語でも英語でも聞いてくる
ALIASES = {
    "sakata_sanzan": ["三山", "さんざん", "triple top", "sanzan"],
    "sakata_sanzon": ["三尊", "三尊天井", "ヘッドアンドショルダー",
                      "head and shoulders", "head-and-shoulders top", "sanzon"],
    "sakata_sankawa_triple_bottom": ["三川", "トリプルボトム", "三尊底",
                                     "triple bottom", "sankawa"],
    "sakata_gyaku_sanzon": ["逆三尊", "ぎゃくさんぞん", "逆ヘッドアンドショルダー",
                            "inverse head and shoulders",
                            "inverted head and shoulders", "gyaku sanzon"],
    "sakata_morning_star": ["明けの明星", "あけのみょうじょう", "三川明けの明星",
                            "morning star"],
    "sakata_evening_star": ["宵の明星", "よいのみょうじょう", "三川宵の明星",
                            "evening star"],
    "sakata_sanku_tatakikomi": ["三空叩き込み", "三空", "さんくうたたきこみ",
                                "three gaps down", "sanku"],
    "sakata_sanku_fumiage": ["三空踏み上げ", "三空", "さんくうふみあげ",
                             "three gaps up", "sanku"],
    "sakata_aka_sanpei": ["赤三兵", "あかさんぺい", "three white soldiers"],
    "sakata_kuro_sanpei": ["黒三兵", "三羽烏", "さんばがらす", "くろさんぺい",
                           "three black crows"],
    "sakata_age_sanpo": ["上げ三法", "あげさんぽう", "rising three methods"],
    "sakata_sage_sanpo": ["下げ三法", "さげさんぽう", "falling three methods"],
}

# ★ここだけ元の文を使わない★（上の docstring を参照）
REASON_OVERRIDE = {
    "sakata_gyaku_sanzon":
        "トレードルールとしては不採用。通説と正反対の成績で、"
        "定義21通り・3時代・時価総額4区分・流動性2水準・ユニバース2基盤の"
        "すべてで符号が変わらなかったため、別枠で保持している。",
}

DIRECTION_LABEL = {"buy": "買い方向の形として検証", "sell": "売り方向の形として検証"}


def pct(x, nd=2):
    """比率（0.4886）を %（48.86）に。"""
    return None if x is None else round(x * 100, nd)


def failed(checks):
    """5基準のうち落ちたものの名前。なぜ不採用かはここに出る。"""
    return [k for k, v in checks.items() if not v]


def stat_block(s):
    return {
        "hold_days": s["hold_days"],
        "trades": s["trades"],
        "win_rate_pct": pct(s["win_rate"]),
        "pf": s["pf"],
        "mean_excess_pct": s["mean_excess_pct"],
        "p_value": s["p_value"],
        "losing_years": s["losing_years"],
        "years": s["years"],
        # 「規定値を少しずらしても符号が変わらなかった回数 / 試した回数」
        "sensitivity_same_sign": s["sensitivity_same_sign"],
        "checks": s["checks"],
        "checks_passed": s["passed"],
        "checks_failed": failed(s["checks"]),
    }


def build_pattern(r):
    s = r["stats"]
    is_filter = r.get("status") == "candidate"
    p = {
        "pattern_id": r["rule_id"],
        "name": r["name"],
        "aliases": ALIASES.get(r["rule_id"], []),
        "direction": r["direction"],
        "direction_note": DIRECTION_LABEL.get(r["direction"], ""),
        # ★12本すべてここは not_adopted★
        "verdict": "not_adopted",
        "filter_candidate": is_filter,
        "universe": r["universe"],
        "period": r["period"],
        "entry": r["entry"],
        "exit": r["exit"],
        "params_tested": r["params"],
        "primary": stat_block(s),
        "by_horizon": [stat_block(r["stats_by_horizon"][k])
                       for k in sorted(r["stats_by_horizon"], key=int)],
        "regime": {
            "above_75ma": r["regime"]["above_75ma"],
            "below_75ma": r["regime"]["below_75ma"],
            "note": "75日移動平均より上／下で分けた場合の平均超過収益",
        },
        "rejection_reason": REASON_OVERRIDE.get(r["rule_id"], r["rejection_reason"]),
    }
    if is_filter:
        p["filter_note"] = (
            "トレードルールとしては不採用だが、すべての切り口で符号が変わらなかった"
            "ため別枠で保持している。掲載する場合は改めて検証してから。")
    notes = r.get("notes") or {}
    if notes.get("size_excess_pct_reference"):
        # ★注記ごと返す★ 数字だけ抜くと別基盤の値が採用判定に見える
        p["size_reference"] = {
            "excess_pct": notes["size_excess_pct_reference"],
            "note": notes["size_note"],
        }
    return p


def main():
    if not os.path.exists(SRC):
        print("元JSONが見つかりません: %s" % SRC, file=sys.stderr)
        return 1
    src = json.load(io.open(SRC, encoding="utf-8"))
    rules = src["rules"]

    n_filter = sum(1 for r in rules if r.get("status") == "candidate")
    doc = {
        "schema": "candlestick_verdict/1",
        "source_book": "酒田五法の全検証（32）",
        "source_file": "32_本間宗久_酒田五法/検証/ruletrade_sakata_rules.json",
        "source_generated": src["generated"],
        "universe": "東証プライム1,550銘柄",
        "period": "2016-01〜2026-08",
        "method": {
            "entry": "シグナル成立日の翌営業日始値",
            "exit": "保有日数の経過後の終値（5・10・20営業日で検証）",
            "excess": "同じ期間の市場全体を引いた超過収益",
            "primary": "5・10・20営業日のうち、基準を最も多く通った窓を primary とした",
        },
        "criteria": {
            "min_trades": src["criteria"]["min_trades"],
            "min_pf": src["criteria"]["min_pf"],
            "max_losing_year_ratio": src["criteria"]["max_losing_year_ratio"],
            "sensitivity": src["criteria"]["sensitivity"],
            "regime": src["criteria"]["regime"],
            "note": "5つすべてを満たしたものだけを採用とした",
        },
        "policy": src["policy"],
        # ★このツールの結論。最初に読ませる★
        "headline": (
            "酒田五法12本すべてを東証プライムで検証したが、"
            "採用基準を満たしたものは1本も無かった。"),
        "summary": {
            "patterns_tested": len(rules),
            "adopted": 0,
            "not_adopted": len(rules),
            "filter_candidates": n_filter,
        },
        # ★PF が高くても採用できない理由。他のどこにも書いていない★
        "key_finding": (
            "PF の高さだけでは採用できない。三空叩き込みは PF 3.04 と"
            "12本の中で最も高い数字が出たが、期間を発見期（前半6年）と"
            "確認期（後半4年）に分けると符号が反転したため不採用とした。"
            "全期間の平均だけが良く見える型は、過去への当てはめで生まれる。"),
        "verdict_values": {
            "not_adopted": "採用基準を満たさなかった。12本すべてがこれに当たる",
            "filter_candidate":
                "トレードルールとしては不採用だが、別枠で保持しているもの",
        },
        "not_included":
            "銘柄名・銘柄コード・個別銘柄の値動きは含まない。"
            "返すのは形ごとの統計と、採用しなかった理由だけ。",
        "patterns": [build_pattern(r) for r in rules],
        "disclaimer":
            "過去の値動きへの当てはめであり、将来の値動きを示すものではありません。"
            "特定の銘柄や売買を勧めるものでもありません。"
            "ここに載っている形は、いずれも著者の採用基準を満たしていません。",
    }

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with io.open(OUT, "w", encoding="utf-8") as fp:
        json.dump(doc, fp, ensure_ascii=False, indent=1)

    size = os.path.getsize(OUT)
    print("→ %s" % OUT)
    print("   形 %d 本 / %s バイト（1ツール50KB の上限に対して %.0f%%）"
          % (len(doc["patterns"]), f"{size:,}", size / 51200 * 100))
    print("   採用 0 本 / 不採用 %d 本 / 別枠 %d 本"
          % (doc["summary"]["not_adopted"], n_filter))
    return 0


if __name__ == "__main__":
    sys.exit(main())
