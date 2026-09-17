# -*- coding: utf-8 -*-
"""レバレッジ・インバース ETF の減価の検証結果を JSON にする（書籍35）。

■ 何に使うか
「ダブルインバースは長期で持てるか」「下げ相場のヘッジに使えるか」に、
**12年の実測**で答えるための元データ。
`docs/etf_decay.json` → MCP の `get_etf_decay`。

■ この検証でいちばん大事な点
起動文の指定どおり **「戻り待ちはほぼ必ず成功する」** を入れる。
年初来高値の翌日に買って「戻るまで待つ」を305回試すと、**99.3% は戻る**。
中央値はわずか2営業日。だから続けられる。
ただし戻ったときの取り分は中央値 +1.1% で、戻らなかった試行は −32.5%。
**成功率と損益の大きさが釣り合っていない**——ここが数字で見える。

■ 出すもの・出さないもの
  出す   … 年別の理論値と実績・保有日数別の勝率と理論乖離・条件別の PF と最大DD・
           戻り待ちシミュレーションの結果・**各検証の「解釈で注意すべき点」**
  出さない … 日足の価格系列（`data/` は開かない）・個別株の銘柄コード

★ETF の銘柄コード（1357 等）は返す★
起動文 §4-4 が `code` 引数を明示している。これは**検証の対象を指す識別子**で、
個別株を名指しして売買を促すのとは性質が違う。`not_included` にその線を書く。

★「解釈で注意すべき点」を必ず返す★
「日経が12年で約4倍になった上昇相場のデータ」という前提を落とすと、
この検証結果はまるごと誤読される。数字だけ返してはいけない。

    python mcp/tools/build_etf_decay.py
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

BASE = os.path.join(r"C:\Users\kawamura takeshi\書籍販売",
                    "35_日経ダブルインバース", "results")
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "docs", "etf_decay.json")

# 検証対象。コードは「何を調べたか」の識別子として返す
PRODUCTS = {
    "1357": {
        "name": "日経ダブルインバース",
        "aliases": ["ダブルインバース", "ダブルインバ", "1357", "インバース",
                    "double inverse", "inverse etf", "ベア"],
        "type": "日々の値動きが日経平均のマイナス2倍になるよう設計されたETF",
        "primary": True,
    },
    "1570": {
        "name": "日経レバレッジ",
        "aliases": ["レバレッジ", "日経レバ", "1570", "leveraged etf", "ブル"],
        "type": "日々の値動きが日経平均の2倍になるよう設計されたETF",
        "primary": False,
    },
}


def f(v, nd=2):
    s = str(v).replace("%", "").strip()
    if not s or s == "nan":
        return None
    try:
        return round(float(s), nd)
    except ValueError:
        return None


def rows(name):
    return list(csv.DictReader(io.open(os.path.join(BASE, name), encoding="utf-8-sig")))


def md_section(name, header):
    """summary_*.md の1節から箇条書きを取る。★文章を手で写さない★"""
    p = os.path.join(BASE, name)
    if not os.path.exists(p):
        return []
    s = io.open(p, encoding="utf-8").read()
    i = s.find(header)
    if i < 0:
        return []
    body = s[i + len(header):]
    end = body.find("\n## ")
    if end >= 0:
        body = body[:end]
    out = []
    for line in body.split("\n"):
        t = line.strip()
        if t.startswith("- "):
            # 強調記号は読み手に意味が無いので落とす
            out.append(re.sub(r"\*\*(.+?)\*\*", r"\1", t[2:]).strip())
    return out


# ★元の md をそのまま出せない2つの理由★
#
# 1) 著者が自分に宛てた執筆メモが混ざっている。
#    「章立てへの示唆: 6章を…に書き換える」は検証結果ではない。落とす。
#    （酒田の逆三尊で「回避フィルターの候補」を落としたのと同じ線）
# 2) 「買い時点」のように、NG語（買い時）を含む普通の日本語がある。
#    そのまま出すと本番の scrub で「各［表現調整］点」になり文が壊れる。
#    意味を変えない言い換えに置き換える。
DROP_PREFIXES = ("章立てへの示唆", "本文への示唆", "図版", "TODO")
SAFE_REWRITE = {
    # 「各買い時点の」→「各購入時点の」。「買った時点」だと「各買った時点」になり
    # 日本語として不自然になる。前に付く語を選ばない言い換えにする
    "買い時点": "購入時点",
    "売り時点": "売却時点",
}


def caveats(name):
    out = []
    for line in md_section(name, "## 解釈で注意すべき点"):
        if any(line.startswith(p) for p in DROP_PREFIXES):
            continue
        for a, b in SAFE_REWRITE.items():
            line = line.replace(a, b)
        out.append(line)
    return out


def main():
    if not os.path.isdir(BASE):
        print("元データが見つかりません: %s" % BASE, file=sys.stderr)
        return 1

    # --- 年別（00_yearly.csv）
    yearly = []
    for r in rows("00_yearly.csv"):
        k = list(r.keys())
        yearly.append({
            "year": r[k[0]],
            "nikkei_pct": f(r["日経平均%"]),
            "theory_pct": f(r["理論値(−2×日経)%"]),
            "actual_1357_pct": f(r["1357実績%"]),
            "decay_pct": f(r["減価(実績−理論)%"]),
            "actual_1570_pct": f(r["1570実績%"]),
        })

    # --- 保有日数別（02_table.csv）
    holding = []
    for r in rows("02_table.csv"):
        k = list(r.keys())
        holding.append({
            "hold_days": int(f(r[k[0]], 0)),
            "cases": int(f(r["件数"], 0)),
            "mean_pct": f(r["平均%"]),
            "median_pct": f(r["中央値%"]),
            "win_rate_pct": f(r["勝率%"]),
            "win_rate_with_cost_pct": f(r["勝率(コスト込)%"]),
            "worst_5pct": f(r["下位5%"]),
            "worst_pct": f(r["最悪%"]),
            "theory_mean_pct": f(r["理論値平均%"]),
            "gap_mean_pct": f(r["乖離平均%"]),
            "worse_than_theory_pct": f(r["理論より悪い割合%"]),
        })

    # --- 条件別（06_table.csv）
    cond = []
    for r in rows("06_table.csv"):
        k = list(r.keys())
        cond.append({
            "condition": r[k[0]],
            "hold_days": int(f(r["保有日数"], 0)),
            "cost": r["コスト"],
            "cases": int(f(r["件数"], 0)),
            "win_rate_pct": f(r["勝率%"]),
            "mean_pct": f(r["平均%"]),
            "median_pct": f(r["中央値%"]),
            "pf": f(r["PF"]),
            "max_dd_pct": f(r["最大DD%"]),
        })

    # --- 戻り待ちシミュレーション（05_C_summary.csv）★起動文の指定★
    wait = []
    for r in rows("05_C_summary.csv"):
        k = list(r.keys())
        wait.append({
            "rule": r[k[0]],
            "trials": int(f(r["試行数"], 0)),
            "recovered_pct": f(r["回収率%"]),
            "return_mean_pct": f(r["損益率平均%"]),
            "return_median_pct": f(r["損益率中央値%"]),
            "positive_pct": f(r["プラス率%"]),
            "days_to_recover_median": f(r["回収日数中央値"]),
            "days_to_recover_max": f(r["回収日数最大"]),
            "unrecovered_mean_pct": f(r["未回収の平均損益率%"]),
            "worst_unrealized_pct": f(r["最大含み損率の最悪%"]),
            "extra_buys_mean": f(r["追加回数平均"]),
        })

    doc = {
        "schema": "etf_decay/1",
        "source_book": "日経ダブルインバースの全検証（35）",
        "source_files": ["00_yearly.csv", "02_table.csv", "06_table.csv",
                         "05_C_summary.csv"],
        "period": "2014-07-15〜2026-09-03（約12年・2,964営業日）",
        "products": PRODUCTS,
        "mechanism": {
            "why_it_decays":
                "日々の値動きを−2倍にする商品なので、上下を往復するだけで元に戻らない。"
                "日経が+10%のあと−9.1%で元の水準に戻る2日間で、"
                "ダブルインバースは 100 → 80 → 94.5 になる。往復するだけで5.5%減る。",
            "not_a_period_multiple":
                "「期間の−2倍」ではなく「日々の−2倍」。"
                "期間をまたぐほど、期間の−2倍からずれていく。",
        },
        "headline": (
            "日経が約4倍になった12年で、ダブルインバースは−99.4%。"
            "理論値（期間の−2倍）より悪かった年は13年中9年で、年平均−6.1%の減価。"),
        # ★起動文が名指しした知見★
        "key_finding": (
            "「戻るまで待つ」はほとんどの場合うまくいく。"
            "年初来高値の翌日に買って戻りを待つ試行を305回行うと、99.3%が投入額まで戻り、"
            "そこまでの日数は中央値でわずか2営業日だった。"
            "ただし戻ったときの取り分は中央値 +1.1% で、戻らなかった試行は −32.5%。"
            "成功する回数は多いが、1回の失敗の大きさが釣り合っていない。"),
        "yearly": yearly,
        "by_holding_days": holding,
        "by_condition": cond,
        "wait_for_recovery": {
            "setup":
                "年初来高値を更新した翌日に10万円ぶん買い、"
                "そこから+3%上がるごとに10万円ずつ追加（上限10回）、"
                "戻ったら全部売る、を2014年以降で305回試した",
            "results": wait,
        },
        # 節ごとの注意書き。★元の md に無い節は作らない★
        # （空のリストを置くと「注意が無い」と読める。無いものは出さない）
        "verification_caveats": {
            **{k: v for k, v in (
                ("by_holding_days", caveats("summary_02.md")),
                ("by_condition", caveats("summary_06.md")),
                ("wait_for_recovery", caveats("summary_05.md")),
            ) if v},
            "note":
                "★この前提を落とすと結果を読み違える★"
                "日経平均が12年で約4倍になった上昇相場のデータであり、"
                "下落相場が長引けば分布は変わる。"
                "ただし減価そのものは相場の方向に関係なく起きる。",
        },
        "not_included":
            "日足の価格系列は含まない。個別株の銘柄名・銘柄コードも含まない。"
            "ETFのコード（1357 など）は「何を検証したか」を示す識別子として載せている。",
        "disclaimer":
            "過去の値動きの記録であり、将来の値動きを示すものではありません。"
            "特定の商品や売買を勧めるものでも、保有・売却を促すものでもありません。"
            "取引コスト・スプレッド・信用取引の金利は原則として含んでいません。",
    }

    # ★書き出す前に止める★ 伏せ字に頼らない（legal_check.py の説明を参照）
    assert_clean(doc)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with io.open(OUT, "w", encoding="utf-8") as fp:
        json.dump(doc, fp, ensure_ascii=False, indent=1)

    size = os.path.getsize(OUT)
    print("→ %s" % OUT)
    print("   年別 %d / 保有日数 %d / 条件別 %d / 戻り待ち %d"
          % (len(yearly), len(holding), len(cond), len(wait)))
    print("   %s バイト（1ツール50KB の上限に対して %.0f%%）"
          % (format(size, ","), size / 51200 * 100))
    got = [k for k, v in doc["verification_caveats"].items() if isinstance(v, list)]
    print("   注意書きを載せた節: %s" % got)
    return 0


if __name__ == "__main__":
    sys.exit(main())
