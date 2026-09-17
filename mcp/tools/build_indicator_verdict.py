# -*- coding: utf-8 -*-
"""テクニカル指標26種の検証結果を MCP が返せる JSON にする（書籍21）。

■ 何に使うか
「RSI は日本株で使えるか」「MACD はどうか」に、**教科書どおりに売買したら
どうなったか**で答えるための元データ。
`docs/indicator_verdict.json` → MCP の `get_indicator_verdict`。

■ 3段判定（使える9 / 条件付き4 / 捨てろ13）
判定の正本は**書籍の原稿**（`結合原稿_第3稿.md` の「全判定早見表」）。
検証出力の `summary.csv` にも「判定(下書き)」列があるが、**こちらは古い**
（下書きでは「使える」が17個ある）。起動文の指示どおり書籍の判定を正とする。

★ただし「使える9個」は数字から機械的に再現できる★
  8区分（出口A・B × 上昇/下落/前半/後半）すべてで超過収益が +0.3% を超えたもの
= ちょうど9個で、書籍の「使える」と完全に一致する。
`verify_indicator_verdict.py` がこれを毎回検算する。基準と判定がずれたら落ちる。

「条件付き」と「捨てろ」の境目は数字だけでは決まらない（同じ2区分落ちでも
両方に分かれる）。ここは著者の判断が入るので、原稿を正本として読む。

■ 出すもの・出さないもの
  出す   … 指標名・カテゴリ・3段判定・出口A/Bの勝率/期待値/PF・8区分の超過収益・
           **+0.3% に届かなかった区分**（なぜ条件付き・捨てろなのかが数字で見える）
  出さない … 書籍本文の「一言」（『新規学習は非推奨』のように推奨語を含み、
           そもそも統計ではなく著者の評価）・銘柄名・銘柄コード・価格系列

    python mcp/tools/build_indicator_verdict.py
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from legal_check import assert_clean  # noqa: E402

BOOK = os.path.join(r"C:\Users\kawamura takeshi\書籍販売",
                    "21_本当に使えるテクニカル指標", "結合原稿_第3稿.md")
SRC = os.path.join(r"D:\マイドキュメント\Claude\Projects\株式投資開発",
                   "physics_dex", "results", "summary.csv")
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "docs", "indicator_verdict.json")

BENCH = "ランダム(ベンチマーク)"

# 8区分。ここすべてで +0.3% を超えたものが「使える」
REGIMES = [
    ("A_上昇局面超過%", "A", "上昇局面"),
    ("A_下落局面超過%", "A", "下落局面"),
    ("A_前半超過%", "A", "前半(2016-2020)"),
    ("A_後半超過%", "A", "後半(2021-2025)"),
    ("B_上昇局面超過%", "B", "上昇局面"),
    ("B_下落局面超過%", "B", "下落局面"),
    ("B_前半超過%", "B", "前半(2016-2020)"),
    ("B_後半超過%", "B", "後半(2021-2025)"),
]
BAR = 0.3

VERDICT_KEY = {"使える": "usable", "条件付き": "conditional", "捨てろ": "not_usable"}

# 英語での問い合わせに応えるための別名。指標名は英語で聞かれることが多い
ALIASES = {
    "SMA25クロス": ["sma", "移動平均", "ゴールデンクロス", "moving average cross"],
    "EMA25クロス": ["ema", "指数移動平均", "exponential moving average"],
    "MACD": ["macd", "マックディー"],
    "一目均衡表(雲上抜け)": ["一目", "雲", "ichimoku", "kumo"],
    "パラボリックSAR": ["パラボリック", "sar", "parabolic"],
    "ADX/DMI": ["adx", "dmi", "方向性指数"],
    "エンベロープ": ["envelope", "包絡線"],
    "グランビル第1法則": ["グランビル", "granville"],
    "RSI": ["rsi", "相対力指数", "relative strength index"],
    "ストキャスティクス": ["ストキャス", "stochastic", "stochastics"],
    "RCI": ["rci", "順位相関"],
    "CCI": ["cci", "commodity channel index"],
    "ウィリアムズ%R": ["ウィリアムズ", "williams", "%r"],
    "モメンタム": ["momentum"],
    "移動平均乖離率": ["乖離率", "乖離", "deviation", "disparity"],
    "ボリンジャー-2σ": ["ボリンジャー", "bollinger", "2シグマ", "bollinger bands"],
    "ATRブレイク": ["atr", "average true range", "ブレイク"],
    "ケルトナーチャネル": ["ケルトナー", "keltner"],
    "ドンチャン20日高値": ["ドンチャン", "donchian", "20日高値"],
    "HVスクイーズ": ["hv", "スクイーズ", "squeeze", "historical volatility"],
    "出来高急増": ["出来高", "volume spike", "volume surge"],
    "OBV": ["obv", "on balance volume"],
    "移動VWAP": ["vwap", "出来高加重"],
    "MFI": ["mfi", "money flow index"],
    "価格帯別出来高POC": ["poc", "価格帯別出来高", "volume profile"],
    "A/Dライン": ["a/d", "adライン", "accumulation distribution"],
}


def f(v, nd=2):
    s = str(v).replace("%", "").strip()
    if not s:
        return None
    try:
        return round(float(s), nd)
    except ValueError:
        return None


def read_book_verdicts():
    """原稿の「全判定早見表」から26行の判定を取る。★ここが判定の正本★"""
    s = io.open(BOOK, encoding="utf-8").read()
    i = s.index("# 全判定早見表")
    block = s[i:i + 4000]
    out = {}
    for m in re.finditer(
            r"^\|\s*[◎△×](使える|条件付き|捨てろ)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|",
            block, re.M):
        # 3列目は書籍の「一言」。★取り込まない★（推奨語を含み、統計でもない）
        out[m.group(2)] = m.group(1)
    return out


def build_row(r, verdict_ja):
    regimes = []
    weak = []
    for col, exit_, label in REGIMES:
        v = f(r[col])
        regimes.append({"exit": exit_, "regime": label, "excess_pct": v})
        if v is None or v <= BAR:
            # ★なぜ「使える」でないかが数字で見える★
            weak.append({"exit": exit_, "regime": label, "excess_pct": v})
    return {
        "indicator": r["指標"],
        "category": r["カテゴリ"],
        "aliases": ALIASES.get(r["指標"], []),
        "verdict": VERDICT_KEY[verdict_ja],
        "verdict_ja": verdict_ja,
        "exit_a": {
            "rule": "20営業日で手仕舞い",
            "trades": int(f(r["A_トレード数"], 0) or 0),
            "win_rate_pct": f(r["A_勝率%"]),
            "payoff": f(r["A_ペイオフ"]),
            "expectancy_pct": f(r["A_期待値%"]),
            "excess_expectancy_pct": f(r["A_超過期待値%"]),
        },
        "exit_b": {
            "rule": "8%トレーリングストップ",
            "trades": int(f(r["B_トレード数"], 0) or 0),
            "win_rate_pct": f(r["B_勝率%"]),
            "payoff": f(r["B_ペイオフ"]),
            "expectancy_pct": f(r["B_期待値%"]),
            "excess_expectancy_pct": f(r["B_超過期待値%"]),
            "pf": f(r["B_PF"]),
        },
        "regimes": regimes,
        "below_bar": weak,
        "below_bar_count": len(weak),
    }


def main():
    for p in (BOOK, SRC):
        if not os.path.exists(p):
            print("元データが見つかりません: %s" % p, file=sys.stderr)
            return 1
    book = read_book_verdicts()
    rows = [r for r in csv.DictReader(io.open(SRC, encoding="utf-8-sig"))
            if r["指標"] != BENCH]
    bench = next(r for r in csv.DictReader(io.open(SRC, encoding="utf-8-sig"))
                 if r["指標"] == BENCH)

    missing = [r["指標"] for r in rows if r["指標"] not in book]
    if missing:
        print("原稿の早見表に無い指標があります: %s" % missing, file=sys.stderr)
        return 1

    items = [build_row(r, book[r["指標"]]) for r in rows]
    counts = {}
    for it in items:
        counts[it["verdict"]] = counts.get(it["verdict"], 0) + 1

    doc = {
        "schema": "indicator_verdict/1",
        "source_book": "本当に使えるテクニカル指標（21）",
        "source_file": "株式投資開発/physics_dex/results/summary.csv",
        "universe": "TOPIX500（有効490銘柄）",
        "period": "2016-01-01〜2025-12-31",
        "method": {
            "entry": "教科書どおりの買いシグナルが出た日",
            "exit_a": "20営業日で手仕舞い",
            "exit_b": "8%トレーリングストップ",
            "cost": "往復0.3%を控除",
            "excess": "同じ区分のランダムな売買との差（超過期待値）",
            "regimes":
                "上昇/下落はTOPIX ETFの200日移動平均より上か下か。"
                "前半は2016-2020、後半は2021-2025",
            "trades": "全指標の合計で約50万回",
        },
        "criteria": {
            "usable": "8区分（出口A・B × 上昇/下落/前半/後半）すべてで超過収益が +0.3% を超えた",
            "conditional": "実力はあるが使う場面や出口に条件が付く",
            "not_usable": "超過収益が基準に届かない、または特定の局面・期間で消える",
            "note":
                "「使える」は数字から機械的に決まる（ちょうど9個）。"
                "「条件付き」と「捨てろ」の境目は著者の判断を含む",
        },
        "benchmark": {
            "name": "ランダムな売買",
            "exit_a_win_rate_pct": f(bench["A_勝率%"]),
            "exit_a_expectancy_pct": f(bench["A_期待値%"]),
            "exit_b_win_rate_pct": f(bench["B_勝率%"]),
            "exit_b_pf": f(bench["B_PF"]),
            "note": "すべての超過収益はこのランダムな売買との差",
        },
        "headline": (
            "26指標のうち「使える」は9個（約3割）。"
            "残りは条件が付くか、基準に届かなかった。"),
        "summary": {
            "indicators_tested": len(items),
            "usable": counts.get("usable", 0),
            "conditional": counts.get("conditional", 0),
            "not_usable": counts.get("not_usable", 0),
        },
        # ★この検証でいちばん大事な点★
        "key_finding": (
            "「捨てろ」の13個には、入門書の最初に出てくる指標が並んでいる"
            "（移動平均クロス・MACD・一目均衡表）。"
            "知名度と成績は別で、有名な指標ほど多くの人が同じ場所で売買するため、"
            "後半5年で超過収益が消えているものが多い。"),
        "not_included":
            "銘柄名・銘柄コード・価格系列は含まない。返すのは指標ごとの判定と統計だけ。",
        "items": items,
        "disclaimer":
            "過去の値動きへの当てはめであり、将来の値動きを示すものではありません。"
            "特定の銘柄や売買を勧めるものでもありません。"
            "判定は上の基準に対するもので、指標そのものの優劣を決めるものではありません。",
    }

    # ★書き出す前に止める★ 伏せ字は最後の砦であって、そこに頼らない
    assert_clean(doc)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with io.open(OUT, "w", encoding="utf-8") as fp:
        json.dump(doc, fp, ensure_ascii=False, indent=1)

    size = os.path.getsize(OUT)
    print("→ %s" % OUT)
    print("   指標 %d 種 / %s バイト（1ツール50KB の上限に対して %.0f%%）"
          % (len(items), f"{size:,}" if False else format(size, ","), size / 51200 * 100))
    print("   使える %d / 条件付き %d / 捨てろ %d"
          % (counts.get("usable", 0), counts.get("conditional", 0),
             counts.get("not_usable", 0)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
