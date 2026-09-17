# -*- coding: utf-8 -*-
"""連想27種の検証結果を MCP が返せる JSON にする（書籍36）。

■ 何に使うか
「地震が起きたら何が上がるか」「利上げで何が上がるか」に、
**過去に何が起きたか**で答えるための元データ。
`docs/event_reaction.json` → MCP の `get_event_reaction`。

■ 出すもの・出さないもの（起動文 §4-1・法務の線）
  出す   … 出来事の分類・判定（持続型/初動型/不発/逆行）・窓別の統計
           （超過収益・勝率・p値・標本数）・期間・出所の書籍名
  出さない … **銘柄バスケット**（`baskets.csv` の 499行・code と name を持つ）
           個別銘柄の値動き・価格系列

★ここが最重要★
`baskets.csv` は**読み込まない**。同じフォルダにあるが、あれを混ぜた瞬間に
「出来事 → 買う銘柄」を返す道具になり、法務の線を越える。

■ 数字は手で書かない
`summary_main.csv` から機械的に変換し、`verify_event_reaction.py` で
元CSVと突き合わせる（§28 の test:materials と同じ発想）。

    python mcp/tools/build_event_reaction.py
"""
from __future__ import annotations

import csv
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from legal_check import assert_clean  # noqa: E402

SRC = os.path.join(
    r"C:\Users\kawamura takeshi\書籍販売",
    "36_株の連想大全", "検証", "out", "summary_main.csv")
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "docs", "event_reaction.json")

# 出来事のあと何営業日後か。CSV の列名の接尾辞と対応する
HORIZONS = [
    ("t0", 0, "当日"),
    ("t1", 1, "翌日"),
    ("t5", 5, "5営業日後"),
    ("t21", 21, "1か月後"),
    ("t63", 63, "3か月後"),
]

# 検索しやすいよう、分類コードに日本語と英語の別名を持たせる。
# エージェントは「地震」「earthquake」のどちらでも聞いてくる。
ALIASES = {
    "A1": ["日銀", "利上げ", "引き締め", "boj", "rate hike"],
    "A2": ["日銀", "利下げ", "緩和", "boj", "rate cut", "easing"],
    "A3": ["fomc", "利上げ", "fed", "rate hike"],
    "A4": ["fomc", "利下げ", "fed", "rate cut"],
    "A5": ["円高", "為替", "yen", "strong yen"],
    "A6": ["円安", "為替", "yen", "weak yen"],
    "A7": ["原油", "oil", "crude"],
    "A8": ["原油", "oil", "crude"],
    "A9": ["金利", "米金利", "treasury", "yield"],
    "A10": ["vix", "恐怖指数", "volatility"],
    "B1": ["地震", "earthquake", "震災", "災害"],
    "B2": ["台風", "豪雨", "typhoon", "flood", "災害"],
    "B4": ["猛暑", "気温", "heat", "heatwave"],
    "B6": ["大雪", "snow", "災害"],
    "B7": ["噴火", "volcano", "eruption", "災害"],
    "C1": ["戦争", "軍事", "中東", "war", "conflict"],
    "C2": ["北朝鮮", "ミサイル", "north korea", "missile"],
    "C4": ["台湾", "taiwan", "海峡"],
    "D1": ["首相", "総裁選", "政治", "prime minister", "election"],
    "D2": ["衆院選", "選挙", "政治", "election"],
    "E1": ["感染症", "パンデミック", "pandemic", "covid", "疫病"],
    "E2": ["五輪", "万博", "olympic", "expo"],
    "F1": ["米国株", "s&p", "米株急落", "us stocks"],
    "F2": ["中国株", "上海", "china"],
    "F3": ["大統領選", "米大統領", "us election"],
    "G1": ["関税", "貿易", "tariff", "trade"],
    "H1": ["金融ショック", "銀行", "banking", "financial crisis"],
}

# 判定の意味。エージェントが誤読しないよう言葉で添える
JUDGE_NOTE = {
    "初動型": "反応は当日から数日で終わる。遅れて入ると取れない",
    "持続型": "反応が数週間から数か月続いた",
    "不発": "指数を上回る動きは確認できなかった",
    "逆行": "想定と逆の方向に動いた",
    "市場全体のみ": "特定の分類ではなく市場全体が動いた",
    # ★注記が無いと空文字を返してしまう★ 元CSVの judge1 は6種類ある
    "参考(n<5)": "標本が5件未満。統計として扱えないため参考値として載せる",
}


def f(v, nd=None):
    """CSV の文字列を数値に。空は None。"""
    s = str(v).strip()
    if not s:
        return None
    try:
        x = float(s)
    except ValueError:
        return None
    return round(x, nd) if nd is not None else x


def pct(v, nd=2):
    """比率（0.0156）を % （1.56）に。"""
    x = f(v)
    return None if x is None else round(x * 100, nd)


def build_row(r):
    windows = []
    for tag, days, label in HORIZONS:
        excess = pct(r.get("sp1_%s" % tag))
        if excess is None:
            continue
        windows.append({
            "days": days,
            "label": label,
            # sp1 = 主分類の超過収益。bench は市場全体
            "excess_pct": excess,
            "win_rate_pct": pct(r.get("sp1_win_%s" % tag), 1),
            "p_value": f(r.get("sp1_p_%s" % tag), 4),
            "significant": str(r.get("sp1_sig_%s" % tag, "")).strip() == "1",
            "market_pct": pct(r.get("bench_%s" % tag)),
        })

    j1 = (r.get("judge1") or "").strip()
    j2 = (r.get("judge2") or "").strip()
    return {
        "event_type": r["event_type"],
        "name": r["name"],
        "aliases": ALIASES.get(r["event_type"], []),
        "samples": int(f(r["n"]) or 0),
        "samples_all": int(f(r["n_all"]) or 0),
        "period": r["period"],
        "verdict": j1,
        "verdict_note": JUDGE_NOTE.get(j1, ""),
        "verdict_secondary": j2 or None,
        "peak_day": f(r.get("peak_day"), 1),
        "half_life_day": f(r.get("half_day"), 1),
        "windows": windows,
    }


def main():
    if not os.path.exists(SRC):
        print("元CSVが見つかりません: %s" % SRC, file=sys.stderr)
        return 1
    rows = list(csv.DictReader(io.open(SRC, encoding="utf-8-sig")))

    doc = {
        "schema": "event_reaction/1",
        "source_book": "株の連想大全（36）",
        "source_file": "36_株の連想大全/検証/out/summary_main.csv",
        "universe": "東証プライム（分類ごとのバスケット）",
        "method": {
            "excess": "分類の平均リターンから市場全体（TOPIX）を引いた超過収益",
            "entry": "出来事の判明日の翌営業日始値",
            "windows": "当日・翌日・5営業日後・1か月後・3か月後",
            "significant": "p < 0.10 を有意として扱う（標本数が少ない出来事があるため）",
            "peak_day":
                "累積の超過収益が最も大きくなった営業日（出来事の翌営業日を0日目として"
                "63日目まで探した）。窓ごとの有意性とは別の指標で、"
                "peak_day が先でも判定が「初動型」になることはある",
            "half_day":
                "累積の超過収益がピークの半分まで戻った営業日。63日以内に戻らなければ null",
        },
        # ★この検証でいちばん大事な知見（起動文 §4-1 で必ず入れると指定）
        "key_finding":
            "「初動型」は前日引けから翌朝の寄り付きで反応が終わる。"
            "ニュースを見てから買うのでは間に合わないものが多い。",
        "not_included":
            "銘柄名・銘柄コード・個別銘柄の値動きは含まない。返すのは分類と統計だけ。",
        "events": [build_row(r) for r in rows],
        "disclaimer":
            "過去の出来事への当てはめであり、将来の値動きを示すものではありません。"
            "特定の銘柄や売買を勧めるものでもありません。標本数の少ない出来事が"
            "含まれます（n<5 は参考値）。",
    }

    # ★書き出す前に止める★ 伏せ字は最後の砦であって、そこに頼らない
    assert_clean(doc)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with io.open(OUT, "w", encoding="utf-8") as fp:
        json.dump(doc, fp, ensure_ascii=False, indent=1)

    size = os.path.getsize(OUT)
    print("→ %s" % OUT)
    print("   出来事 %d 件 / %s バイト（1ツール50KB の上限に対して %.0f%%）"
          % (len(doc["events"]), f"{size:,}", size / 51200 * 100))
    n_sig = sum(1 for e in doc["events"] if any(w["significant"] for w in e["windows"]))
    print("   どこかの窓で有意だったもの: %d 件" % n_sig)
    return 0


if __name__ == "__main__":
    sys.exit(main())
