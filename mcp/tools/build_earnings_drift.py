# -*- coding: utf-8 -*-
"""決算後ドリフト（PEAD）の検証結果を JSON にする（書籍31）。

■ 何に使うか
「決算をまたぐのは得か」「好決算で飛んだ株を翌日買えば取れるか」に、
**26,483件の決算イベント**で答えるための元データ。
`docs/earnings_drift.json` → MCP の `get_event_reaction`（決算で引いたとき）。

■ この検証でいちばん大事な点
決算後ドリフトは**実在するが5日で消える**。しかも分位で割ると、

    分位5（最も好反応）を翌日買って+5日  →  +0.04%（p=0.91）
    分位1（最も悪反応）は+5日で          →  -0.85%（p=0.002）

**好反応を買っても統計的にゼロと区別できない。** ロングショートで +1.38% が
出るのは悪材料側が売られ続けた分で、「好決算を買う」では取れない。

★README の結論文はそのまま取り込まない★
「儲けの源泉は買いではなく売りだった」という一文があるが、行動の含意が強く、
エージェントに「売れば取れる」と要約されうる。**同じ内容を数字で返す**。
（酒田の逆三尊で「買わない」を落としたのと同じ線）

■ 明細は絶対に開かない
`events_raw.csv` は J-Quants 由来の銘柄レベル明細。起動文 §6 の指定どおり
**集計表4つだけ**を読む。開いた瞬間に「決算 → 買う銘柄」を返す道具になる。

    python mcp/tools/build_earnings_drift.py
"""
from __future__ import annotations

import csv
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from legal_check import assert_clean  # noqa: E402

BASE = os.path.join(r"D:\マイドキュメント\Claude\Projects\株式投資開発",
                    "PEAD検証", "results")
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "docs", "earnings_drift.json")

# ★この4つ以外は開かない★（events_raw.csv は銘柄レベルの明細）
FILES = {
    "quantile": "01_分位別ドリフト.csv",
    "long_short": "02_ロングショート.csv",
    "robustness": "05_頑健性_CAR5.csv",
    "backtest": "07_バックテスト.csv",
}

HORIZONS = [5, 10, 20, 40, 60]

QUANTILE_LABEL = {
    "1": "分位1（決算への反応が最も悪かった20%）",
    "2": "分位2",
    "3": "分位3（中間）",
    "4": "分位4",
    "5": "分位5（決算への反応が最も良かった20%）",
}


def f(v, nd=4):
    s = str(v).strip()
    if not s or s == "nan":
        return None
    try:
        return round(float(s), nd)
    except ValueError:
        return None


def pct(v, nd=2):
    x = f(v, 10)
    return None if x is None else round(x * 100, nd)


def rows(key):
    return list(csv.DictReader(io.open(os.path.join(BASE, FILES[key]),
                                       encoding="utf-8-sig")))


def main():
    if not os.path.isdir(BASE):
        print("元データが見つかりません: %s" % BASE, file=sys.stderr)
        return 1

    # --- 分位別
    quantiles = []
    for r in rows("quantile"):
        k = list(r.keys())
        q = str(r[k[0]]).strip()
        quantiles.append({
            "quantile": int(q),
            "label": QUANTILE_LABEL.get(q, q),
            "cases": int(f(r["件数"], 0)),
            "initial_move_pct": pct(r["初動r0"]),
            "windows": [{
                "days": h,
                "car_pct": pct(r["CAR%d" % h]),
                "t": f(r["t%d" % h], 2),
                "p_value": f(r["p%d" % h]),
                "significant": (f(r["p%d" % h]) or 1) < 0.05,
            } for h in HORIZONS],
        })

    # --- ロングショート
    long_short = []
    for r in rows("long_short"):
        k = list(r.keys())
        long_short.append({
            "window": r[k[0]],
            "spread_pct": pct(r["スプレッド"]),
            "t": f(r["t"], 2),
            "p_value": f(r["p"]),
            "significant": (f(r["p"]) or 1) < 0.05,
            "announcement_days": int(f(r["発表日数"], 0)),
            "win_rate_pct": pct(r["勝率"], 1),
        })

    # --- 頑健性（+1〜+5日のスプレッドを区分ごとに）
    robustness = []
    for r in rows("robustness"):
        k = list(r.keys())
        robustness.append({
            "segment": r[k[0]],
            "cases": int(f(r["件数"], 0)),
            "spread_pct": pct(r["スプレッド"]),
            "t": f(r["t"], 2),
            "p_value": f(r["p"]),
            "significant": (f(r["p"]) or 1) < 0.05,
            "win_rate_pct": pct(r["勝率"], 1),
        })

    # --- ルール別のバックテスト
    backtest = []
    for r in rows("backtest"):
        k = list(r.keys())
        backtest.append({
            "rule": r[k[0]],
            "cases": int(f(r["件数"], 0)),
            "before_cost_pct": pct(r["コスト前"]),
            "after_cost_pct": pct(r["コスト後"]),
            "win_rate_pct": pct(r["勝率"], 1),
            "t": f(r["t"], 2),
            "p_value": f(r["p"]),
            "significant": (f(r["p"]) or 1) < 0.05,
        })

    q5 = next(q for q in quantiles if q["quantile"] == 5)
    q1 = next(q for q in quantiles if q["quantile"] == 1)
    ls5 = next(x for x in long_short if x["window"].startswith("+1〜+5"))
    w5_of = lambda q: next(w for w in q["windows"] if w["days"] == 5)

    doc = {
        "schema": "earnings_drift/1",
        "source_book": "決算後ドリフトの検証（31）",
        "source_files": list(FILES.values()),
        "data_source": "J-Quants API V2（決算短信と日足）",
        "universe": "内国普通株3,756銘柄（ETF・REIT・TOKYO PRO MARKET を除く）",
        "period": "2024-03-28〜2026-06-02（531営業日）",
        "method": {
            "event": "決算短信の発表",
            "surprise": "発表への初動（発表直後の値動き）で5つの分位に分ける",
            "car": "発表の翌営業日を起点とした累積超過収益（市場全体を引いたもの）",
            "filter": "発表前20日の平均売買代金が1億円以上",
            "events": "有効26,483件 → 流動性で絞って10,888件",
        },
        "headline": (
            # ★% 書式は使わない★ 文中の「20%」が書式指定子と解釈されて落ちる
            f"決算後のドリフトは実在するが、+5日で終わる。"
            f"上位20%と下位20%の差は翌日〜+5日で +{ls5['spread_pct']:.2f}%"
            f"（p={ls5['p_value']:.4f}）、+20日まで延ばすと統計的に消える。"),
        # ★好反応を買っても取れない★ 数字で示す（README の結論文は取り込まない）
        "key_finding": (
            f"差が出たのは「好反応を買った」からではない。"
            f"最も反応の良かった分位5を翌営業日から+5日持っても"
            f" {w5_of(q5)['car_pct']:+.2f}%（p={w5_of(q5)['p_value']:.2f}）で、"
            f"統計的にゼロと区別できない。"
            f"一方で最も反応の悪かった分位1は同じ期間に"
            f" {w5_of(q1)['car_pct']:+.2f}%（p={w5_of(q1)['p_value']:.4f}）動いている。"
            f"上位と下位の差は、下位側が動き続けた分で説明される。"),
        "by_quantile": quantiles,
        "long_short": long_short,
        "robustness": robustness,
        "rule_backtest": backtest,
        "how_to_read": [
            "CAR は発表の翌営業日を起点にした累積超過収益。発表当日の値動き（初動）は含まない",
            "スプレッドは上位20%と下位20%の差。片側だけを取った場合の数字ではない",
            "p値は発表日単位で計算している（同じ日の銘柄は独立ではないため）",
            "ルール別の数字はコスト後（往復0.2%を引いたもの）も併記している",
        ],
        "not_included":
            "銘柄名・銘柄コード・個別の決算内容は含まない。"
            "J-Quants 由来の銘柄レベル明細は生成側でも開いていない（集計表4つのみを使用）。",
        "disclaimer":
            "過去の決算への当てはめであり、将来の値動きを示すものではありません。"
            "特定の銘柄や売買を勧めるものでもありません。"
            "検証期間は約2年と短く、制度変更や相場環境で結果は変わりえます。",
    }

    assert_clean(doc)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with io.open(OUT, "w", encoding="utf-8") as fp:
        json.dump(doc, fp, ensure_ascii=False, indent=1)

    size = os.path.getsize(OUT)
    print("→ %s" % OUT)
    print("   分位 %d / 期間 %d / 区分 %d / ルール %d / %s バイト（上限の %.0f%%）"
          % (len(quantiles), len(long_short), len(robustness), len(backtest),
             format(size, ","), size / 51200 * 100))
    print(f"   分位5の+5日: {w5_of(q5)['car_pct']:+.2f}%"
          f"（p={w5_of(q5)['p_value']:.2f}） / 分位1: {w5_of(q1)['car_pct']:+.2f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
