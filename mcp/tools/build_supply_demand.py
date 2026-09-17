# -*- coding: utf-8 -*-
"""需給イベント（指数の入替）の検証結果を JSON にする（書籍29）。

■ 何に使うか
「TOPIX の入替で何が起きるか」「指数から外れる銘柄はいつ売られるか」に、
**JPX・日経の公表資料をもとにした実測**で答えるための元データ。
`docs/supply_demand_events.json` → MCP の `get_event_reaction`。

■ この検証でいちばん大事な点
売りも買いも**同じ形**をしている。

    TOPIX 除外 … 実施日の直前3営業日で -1.57%、実施後20日は +0.84%（p=0.55）＝無風
    TOPIX 採用 … 指定日の前20営業日で +10.4%、組入れ日の直後5日で -2.50%

**イベントを通過する前に動き、通過したら終わる。**
連想27種の「初動型」、決算後ドリフトの「+5日で消える」と同じ形で、
3つの検証が別々のデータから同じことを言っている。

■ 自社株買いは入れていない（起動文の質問のうち1つに答えられない）
起動文 §2 は「自社株買いで株は上がるか」も挙げているが、
`00_マスター検定結果.csv` に自社株買いの行は無い。
自社株買いのファイルは `buyback_*.csv`(5,523行・3,658行・5,681行)で、
いずれも **銘柄コード・企業名つきの明細**。起動文 §6 の
「明細は絶対に入れない」に当たるため開かない。
明細から集計を作るのは「検証をやり直す」ことであり、実務の範囲を超える。
→ `not_included` に理由を書いて返す。**黙って落とさない。**

    python mcp/tools/build_supply_demand.py
"""
from __future__ import annotations

import csv
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from legal_check import assert_clean  # noqa: E402

SRC = os.path.join(r"D:\マイドキュメント\Claude\Projects\株式投資開発",
                   "需給イベント検証", "results", "00_マスター検定結果.csv")
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "docs", "supply_demand_events.json")

# カテゴリ列 → どのイベントの話か
EVENTS = [
    {
        "id": "topix_exclusion",
        "name": "TOPIX からの除外（段階的ウエイト低減）",
        "aliases": ["topix", "除外", "低減", "ウエイト", "指数除外",
                    "topix exclusion", "index deletion", "リバランス"],
        "pressure": "売り",
        "match": lambda c: c.startswith("売り"),
        "what_happened":
            "2022年10月の公表にもとづき、10回に分けて指数のウエイトが下げられた。"
            "その各回で何が起きたかを測っている",
    },
    {
        "id": "topix_addition",
        "name": "TOPIX への新規採用（旧・東証1部への指定替え）",
        "aliases": ["topix", "採用", "組入れ", "指定替え", "昇格",
                    "topix addition", "index inclusion"],
        "pressure": "買い",
        "match": lambda c: c == "買い(TOPIX採用)",
        "what_happened":
            "指定日（採用が決まった日）と組入れ日（実際に指数へ入る日）の"
            "それぞれを起点に、204銘柄の値動きを測っている",
    },
    {
        "id": "nikkei225_addition",
        "name": "日経225 への採用",
        "aliases": ["日経", "日経平均", "225", "nikkei", "採用", "入替"],
        "pressure": "買い",
        "match": lambda c: c == "買い(日経225採用)",
        "what_happened":
            "入替の発表日と実施日を起点に測っている。"
            "該当する回数が少なく、いずれも統計的に有意ではない",
    },
]


def f(v, nd=4):
    s = str(v).strip()
    if not s or s == "nan":
        return None
    try:
        return round(float(s), nd)
    except ValueError:
        return None


def main():
    if not os.path.exists(SRC):
        print("元データが見つかりません: %s" % SRC, file=sys.stderr)
        return 1
    rows = list(csv.DictReader(io.open(SRC, encoding="utf-8-sig")))
    k = list(rows[0].keys())
    CAT, EV, WIN, CAR, N, P, NOTE = k[0], k[1], k[2], k[3], k[4], k[5], k[6]

    events = []
    used = 0
    for spec in EVENTS:
        ms = []
        for r in rows:
            if not spec["match"](str(r[CAT]).strip()):
                continue
            used += 1
            p = f(r[P])
            m = {
                "category": r[CAT],
                "target": r[EV],
                "window": r[WIN],
                "mean_car_pct": f(r[CAR], 2),
                "samples": r[N],
                "p_value": p,
                "significant": p is not None and p < 0.05,
                "note": r[NOTE],
            }
            # ★「p=0」と読ませない★
            # 元CSVで 0.0 に丸められている行がある。厳密なゼロではないので
            # 値は素通ししたうえで意味を添える（ジンクス50本と同じ扱い）。
            if p == 0:
                m["p_note"] = ("元データで 0.0 と記録されている。厳密なゼロではなく、"
                               "0.0001 を下回る値")
            ms.append(m)
        events.append({
            "event_id": spec["id"],
            "name": spec["name"],
            "aliases": spec["aliases"],
            "pressure": spec["pressure"],
            "what_happened": spec["what_happened"],
            "measurements": ms,
        })

    if used != len(rows):
        print("分類できなかった行があります: %d / %d" % (used, len(rows)),
              file=sys.stderr)
        return 1

    def pick(eid, target_part, window_part):
        ms = next(e for e in events if e["event_id"] == eid)["measurements"]
        return next(m for m in ms
                    if target_part in m["target"] and window_part in m["window"])

    ex3 = pick("topix_exclusion", "低減10回", "CAR(-3,-1)")
    ex20 = pick("topix_exclusion", "低減10回", "CAR(0,+20)")
    ad20 = pick("topix_addition", "指定日", "CAR(-20,-1)")
    ad5 = pick("topix_addition", "組入れ日", "CAR(+1,+5)")

    doc = {
        "schema": "supply_demand_events/1",
        "source_book": "需給イベントの検証（29）",
        "source_file": "需給イベント検証/results/00_マスター検定結果.csv",
        "data_source": "JPX・日経の公表資料（推計リストは使っていない）",
        "method": {
            "car": "対TOPIX の累積超過収益。配当調整済みの株価を使用",
            "windows":
                "CAR(-20,-1) は起点日の前20営業日、CAR(-3,-1) は直前3営業日、"
                "CAR(0,0) は当日、CAR(0,+20) は当日から20営業日",
            "significance":
                "除外側は「回」を1観測とした検定（同じ回の銘柄は独立ではないため）。"
                "採用側は日付単位",
            "controls":
                "四半期末の季節性・境界での差の差（DiD）・売買代金の推移でも確かめている",
        },
        "headline": (
            f"指数の入替では、イベントを通過する前に値が動く。"
            f"TOPIX 除外は実施日の直前3営業日で {ex3['mean_car_pct']:+.2f}%"
            f"（p={ex3['p_value']:.4f}）動き、実施後20営業日は"
            f" {ex20['mean_car_pct']:+.2f}%（p={ex20['p_value']:.2f}）で動いていない。"),
        # ★3つの検証が同じ形を示している★
        "key_finding": (
            f"買い側も同じ形だった。TOPIX 新規採用は指定日の前20営業日で"
            f" {ad20['mean_car_pct']:+.1f}%（勝率73.5%）動く一方、"
            f"組入れ日の直後5営業日は {ad5['mean_car_pct']:+.2f}%"
            f"（p={ad5['p_value']:.4f}）で逆を向いている。"
            f"指数に入るための買いが終わった時点が、値動きの区切りになっている。"),
        "events": events,
        "how_to_read": [
            "CAR は対TOPIX の超過収益。市場全体の動きは引いてある",
            "除外側の「10回」は実施の回数で、銘柄数ではない。同じ回の銘柄は独立ではないため回を1観測として検定している",
            "「実施後は無風」は「上がる」という意味ではない。統計的に動きが確認できなかったということ",
            "日経225 の採用は該当回数が少なく、いずれも有意ではない。参考値として載せている",
        ],
        "not_included":
            "銘柄名・銘柄コードは含まない。"
            "自社株買いの検証は含まない（元データに集計表が無く、"
            "銘柄コードと企業名を伴う明細しか存在しないため）。",
        "disclaimer":
            "過去の指数入替への当てはめであり、将来の値動きを示すものではありません。"
            "特定の銘柄や売買を勧めるものでもありません。"
            "指数の算出ルールは変更されることがあり、過去と同じ動きになるとは限りません。",
    }

    assert_clean(doc)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with io.open(OUT, "w", encoding="utf-8") as fp:
        json.dump(doc, fp, ensure_ascii=False, indent=1)

    size = os.path.getsize(OUT)
    print("→ %s" % OUT)
    for e in events:
        print("   %-22s %d 件" % (e["name"][:20], len(e["measurements"])))
    print("   全 %d 行 / %s バイト（上限の %.0f%%）"
          % (len(rows), format(size, ","), size / 51200 * 100))
    return 0


if __name__ == "__main__":
    sys.exit(main())
