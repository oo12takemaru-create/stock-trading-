# -*- coding: utf-8 -*-
"""指標の「結果別」検証 → event_result.json（第1段＝検証のみ。docsには置かない）

■ 問い
  相場の暦（event_score.py）は「日付」で切った。ここは「結果の中身」で切る。
  各指標がこう出た回は、日経はどう動いたか。

■ ★市場予想（コンセンサス）は使わない
  10年分を無料で取る手段が無く、有料データかスクレイピングになる。
  サイトの原則「公表データのみ・推計しない」に反するため使わない。
  代わりに FRED の公式実績値で切る。
  **したがって「予想を上回った／下回った」は測れない。**
  差が出なかった場合、それは「指標が効かない」ではなく
  「実績の方向だけでは説明できない」という意味になる。

■ 発表月と対象月のズレ
  米CPI・米雇用統計は「前月分」を翌月に発表する。
  発表日に紐づくのは **対象月＝発表月の1つ前** のデータ。

■ FREDの値は改定後の値
  雇用統計（PAYEMS）は毎月改定される。ここで使うのは今日時点の改定後の値で、
  発表当日に市場が見た速報値ではない。CPIも季節調整係数が年1回改定される。

■ 反応日
  相場の暦と同じ。日銀＝当日、FOMC・米CPI・米雇用統計＝翌営業日。
  窓は d0（前日終値→当日終値）と d1（当日終値→翌日終値）の2本。

■ イベント日は作り直さない
  event_dates.py が作った event_dates.json（出所つき）をそのまま読む。

使い方: python -X utf8 event_result.py
"""
import csv
import io
import json
import sys
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

from event_score import prices, summarize, windows

HERE = Path(__file__).parent
DATES = HERE / "event_dates.json"
OUT = HERE / "event_result.json"      # ★docs/ ではない。第1段は公開しない

YEAR_FROM = 2013
RECENT_YEARS = 3
BIG_MOVE = 0.02                        # ±2%

FRED = "https://fred.stlouisfed.org/graph/fredgraph.csv?id="
# ★UAから "+https://..." を落とすと FRED(Akamai) に接続を切られる（2026-09-18 実測・再現性あり）。
#   gauge_fetch.py の UA はこのURL無しなので、FRED系の傾斜計3本が9/14から落ち続けている。
FRED_UA = {"User-Agent": "Mozilla/5.0 (compatible; kaburadar.jp/1.0; +https://kaburadar.jp)"}


def fred(series, tries=4):
    """FREDの月次/日次CSVを Series(date→float) で返す。落ちたら数回待って試す"""
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(FRED + series + "&cosd=2005-01-01", headers=FRED_UA)
            with urllib.request.urlopen(req, timeout=40 + i * 30) as r:
                txt = r.read().decode("utf-8")
            rows = [x for x in csv.reader(io.StringIO(txt)) if len(x) >= 2]
            if len(rows) < 50 or "DATE" not in rows[0][0].upper():
                raise ValueError(f"想定外の形: 行数={len(rows)} ヘッダ={rows[0] if rows else None}")
            s = pd.Series(
                {pd.Timestamp(r[0]): pd.to_numeric(r[1], errors="coerce") for r in rows[1:]})
            return s.dropna().sort_index()
        except Exception as e:
            last = e
            print(f"  FRED {series} 再試行{i + 1}: {e}", file=sys.stderr)
            time.sleep(3 + i * 4)
    raise RuntimeError(f"FRED {series} を取得できない: {last}")


def monthly(s):
    """月次系列を「その月の初日」に正規化した dict にする"""
    return {pd.Timestamp(d.year, d.month, 1): float(v) for d, v in s.items()}


def prev_month(ts, n=1):
    y, m = ts.year, ts.month - n
    while m <= 0:
        y, m = y - 1, m + 12
    return pd.Timestamp(y, m, 1)


# ------------------------------------------------------------------ 日銀の政策変更
# 公知の事実だけを手入力する。曖昧な回は入れない（起動文の指示）。
# 出所: 日本銀行「金融政策決定会合の結果」 https://www.boj.or.jp/mopo/mpmdeci/mpr_2013/index.htm
#       （年別に /mpr_YYYY/ がある。各回の公表文PDFが一次資料）
BOJ_CHANGES = {
    "2013-04-04": "量的・質的金融緩和（QQE）の導入",
    "2014-10-31": "量的・質的金融緩和の拡大",
    "2016-01-29": "マイナス金利付き量的・質的金融緩和の導入",
    "2016-09-21": "長短金利操作付き量的・質的金融緩和（YCC）の導入",
    "2018-07-31": "政策金利のフォワードガイダンス導入・長期金利の変動幅拡大",
    "2021-03-19": "点検（長期金利の変動幅を±0.25%と明確化・ETF買入れの見直し）",
    "2022-12-20": "YCCの運用見直し（長期金利の変動幅を±0.5%に拡大）",
    "2023-07-28": "YCCの運用柔軟化（1.0%を上限の目途に）",
    "2023-10-31": "YCCの再柔軟化（1.0%を「目途」に）",
    "2024-03-19": "マイナス金利政策の解除・YCC撤廃・ETF買入れ終了",
    "2024-07-31": "政策金利を0.25%程度に引上げ・国債買入れの減額計画",
    "2025-01-24": "政策金利を0.5%程度に引上げ",
}
# ここまでは確度が高い。これより後は手元で裏が取れないので、
# 無担保コールレート（FRED IRSTCI01JPM156N・月次）の水準変化で当たりを付け、
# 動いていた月の会合は「変更なし」に混ぜず「不明」として集計から外す。
BOJ_TABLE_THROUGH = "2025-01-24"
CALL_RATE_STEP = 0.10   # この幅(%)以上動いた月は政策変更があったとみなす


def boj_unknown_months(call, through):
    """手入力の表が切れたあと、コールレートが動いた月を拾う"""
    out = {}
    lim = pd.Timestamp(through)
    prev = None
    for d, v in call.items():
        if prev is not None and d > lim and abs(v - prev) >= CALL_RATE_STEP:
            out[pd.Timestamp(d.year, d.month, 1)] = round(v - prev, 3)
        prev = v
    return out


# ------------------------------------------------------------------ 検定
def compare(a, b):
    """2グループの差。t検定（Welch）とマン・ホイットニーのU検定の両方"""
    a = np.array([x for x in a if x is not None and np.isfinite(x)], dtype=float)
    b = np.array([x for x in b if x is not None and np.isfinite(x)], dtype=float)
    if len(a) < 3 or len(b) < 3:
        return {"n_a": int(len(a)), "n_b": int(len(b))}
    return {
        "n_a": int(len(a)), "n_b": int(len(b)),
        "mean_a_pct": round(float(a.mean()) * 100, 3),
        "mean_b_pct": round(float(b.mean()) * 100, 3),
        "diff_pt": round(float(a.mean() - b.mean()) * 100, 3),
        "p_ttest": round(float(stats.ttest_ind(a, b, equal_var=False).pvalue), 4),
        "p_mannwhitney": round(
            float(stats.mannwhitneyu(a, b, alternative="two-sided").pvalue), 4),
    }


def verdict(s):
    """グループ単体の判定。非イベント日との差で見る
    （そのグループだけの平均で見ると、相場そのものの上昇を拾ってしまう）"""
    if s.get("n", 0) < 20:
        return "件数不足"
    p = s.get("p_vs_base")
    return "効いている" if (p is not None and p < 0.05) else "効いていない"


def cut_verdict(pairs):
    """切り方そのものの判定。グループ間に差があるか（t検定とU検定の小さいほう）"""
    ps = []
    for cm in pairs.values():
        ps += [cm.get("p_ttest"), cm.get("p_mannwhitney")]
    ps = [x for x in ps if x is not None]
    if not ps:
        return "件数不足"
    return "効いている" if min(ps) < 0.05 else "効いていない"


# ------------------------------------------------------------------ 本体
def reaction_index(idx, pos, dates, react):
    """イベント日 → 反応日の位置。休場などで取れない回は落とす"""
    out, miss = {}, 0
    for s in dates:
        t = pd.Timestamp(s)
        if react == "next":
            nxt = [x for x in idx if x > t]
            if not nxt:
                miss += 1
                continue
            t = nxt[0]
        if t not in pos:
            miss += 1
            continue
        out[s] = pos[t]
    return out, miss


def build_cuts(tbl, idx, pos):
    """6つの切り方を作る。各グループは {ラベル: {イベント日: 反応日の位置}}"""
    cuts = []

    def ev(key):
        e = tbl[key]
        ds = [x["d"] for x in e["dates"] if str(YEAR_FROM) <= x["d"][:4]]
        r, miss = reaction_index(idx, pos, ds, e.get("react", "same"))
        return e, r, miss

    # ---- 1. FOMC: 利上げ／据え置き／利下げ（前回会合からのFF金利上限の変化）
    e, rmap, miss = ev("fomc")
    ffr = fred("DFEDTARU")
    def level_after(dstr):
        """会合日の決定が効いた後の水準。決定は翌営業日に実施されるので7日まで見る"""
        t = pd.Timestamp(dstr)
        w = ffr[(ffr.index >= t) & (ffr.index <= t + timedelta(days=7))]
        return float(w.iloc[-1]) if len(w) else None
    g = {"利上げ": {}, "据え置き": {}, "利下げ": {}}
    moves, skipped = [], 0
    prev_lv, prev_d = None, None
    for dstr in sorted(rmap):
        lv = level_after(dstr)
        if lv is None:
            skipped += 1
            continue
        if prev_lv is not None:
            chg = round(lv - prev_lv, 3)
            lab = "据え置き" if abs(chg) < 1e-9 else ("利上げ" if chg > 0 else "利下げ")
            g[lab][dstr] = rmap[dstr]
            if lab != "据え置き":
                moves.append({"d": dstr, "prev": prev_d, "from": prev_lv, "to": lv, "chg": chg})
        prev_lv, prev_d = lv, dstr
    cuts.append({
        "key": "fomc_rate", "label": "FOMC: 利上げ／据え置き／利下げ", "event": "FOMC",
        "react": "next", "windows": ["d0", "d1"], "groups": g, "all": rmap,
        "fred": "DFEDTARU（FF金利の誘導目標レンジ上限・日次）",
        "note": "前回会合からのFF金利上限の変化で分けた。初回は前回が無いので除く。"
                "会合の決定は翌営業日に実施されるため、会合日から7日以内の最後の値を「会合後の水準」とした。"
                "臨時会合での変更は、その次の定例会合の変化として計上される",
        "extra": {"変更のあった回": moves, "水準が取れず除外": skipped,
                  "反応日が取れず除外": miss},
    })

    # ---- 2/3. 米CPI（対象月＝発表月の1つ前）
    e, rmap, miss = ev("cpi")
    cpi = monthly(fred("CPIAUCSL"))
    g_mom = {"前月比が加速": {}, "前月比が減速": {}}
    g_yoy = {"前年比が3%超": {}, "前年比が3%以下": {}}
    lack = 0
    for dstr, i in rmap.items():
        rel = pd.Timestamp(dstr)
        ref = prev_month(pd.Timestamp(rel.year, rel.month, 1))      # 対象月
        need = [ref, prev_month(ref), prev_month(ref, 2), prev_month(ref, 12)]
        if any(x not in cpi for x in need):
            lack += 1
            continue
        mom = cpi[ref] / cpi[prev_month(ref)] - 1
        mom_prev = cpi[prev_month(ref)] / cpi[prev_month(ref, 2)] - 1
        g_mom["前月比が加速" if mom > mom_prev else "前月比が減速"][dstr] = i
        yoy = cpi[ref] / cpi[prev_month(ref, 12)] - 1
        g_yoy["前年比が3%超" if yoy > 0.03 else "前年比が3%以下"][dstr] = i
    cuts.append({
        "key": "cpi_mom", "label": "米CPI: 前月比が前回より加速／減速", "event": "米CPI",
        "react": "next", "windows": ["d0", "d1"], "groups": g_mom, "all": rmap,
        "fred": "CPIAUCSL（米消費者物価指数・季節調整済・月次）",
        "note": "CPIは前月分を翌月に発表する。発表日に紐づけたのは対象月＝発表月の1つ前。"
                "その月の前月比を、さらに1つ前の月の前月比と比べた",
        "extra": {"対象月のデータが無く除外": lack, "反応日が取れず除外": miss},
    })
    cuts.append({
        "key": "cpi_yoy", "label": "米CPI: 前年比が3%超／3%以下", "event": "米CPI",
        "react": "next", "windows": ["d0", "d1"], "groups": g_yoy, "all": rmap,
        "fred": "CPIAUCSL（米消費者物価指数・季節調整済・月次）",
        "note": "対象月＝発表月の1つ前。前年比は季節調整済系列で計算している"
                "（公表される見出しの前年比は季節調整前。ほぼ同じだが完全には一致しない）",
        "extra": {"対象月のデータが無く除外": lack, "反応日が取れず除外": miss},
    })

    # ---- 4. 米雇用統計
    e, rmap, miss = ev("payroll")
    pay = monthly(fred("PAYEMS"))
    g = {"3か月平均より上": {}, "3か月平均より下": {}}
    lack = 0
    for dstr, i in rmap.items():
        rel = pd.Timestamp(dstr)
        ref = prev_month(pd.Timestamp(rel.year, rel.month, 1))
        need = [prev_month(ref, k) for k in range(0, 5)]
        if any(x not in pay for x in need):
            lack += 1
            continue
        def chg(m):
            return pay[m] - pay[prev_month(m)]
        avg3 = sum(chg(prev_month(ref, k)) for k in (1, 2, 3)) / 3.0
        g["3か月平均より上" if chg(ref) > avg3 else "3か月平均より下"][dstr] = i
    cuts.append({
        "key": "payroll_3m", "label": "米雇用統計: 前月増加分が直近3か月平均より上／下",
        "event": "米雇用統計", "react": "next", "windows": ["d0", "d1"], "groups": g, "all": rmap,
        "fred": "PAYEMS（米非農業部門雇用者数・季節調整済・月次）",
        "note": "対象月＝発表月の1つ前。直近3か月平均は対象月を含めない（1〜3か月前の増加分の平均）。"
                "★FREDの値は改定後。発表当日に市場が見た速報値ではない",
        "extra": {"対象月のデータが無く除外": lack, "反応日が取れず除外": miss},
    })

    # ---- 5. 日銀: 政策変更あり／なし
    e, rmap, miss = ev("boj")
    call = fred("IRSTCI01JPM156N")
    unknown_m = boj_unknown_months(call, BOJ_TABLE_THROUGH)
    g = {"政策変更あり": {}, "政策変更なし": {}}
    unknown = []
    for dstr, i in rmap.items():
        if dstr in BOJ_CHANGES:
            g["政策変更あり"][dstr] = i
            continue
        m = pd.Timestamp(dstr)
        m = pd.Timestamp(m.year, m.month, 1)
        if dstr > BOJ_TABLE_THROUGH and m in unknown_m:
            unknown.append({"d": dstr, "call_rate_change": unknown_m[m]})
            continue          # 手元で裏が取れない。「変更なし」に混ぜない
        g["政策変更なし"][dstr] = i
    cuts.append({
        "key": "boj_change", "label": "日銀: 政策変更あり／なし", "event": "日銀 金融政策決定会合",
        "react": "same", "windows": ["d0", "d1"], "groups": g, "all": rmap,
        "fred": "手入力の表＋IRSTCI01JPM156N（無担保コールレート・月次）で補助確認",
        "note": f"政策変更の回は公知の事実だけを手入力した（{BOJ_TABLE_THROUGH}まで）。"
                "それ以降でコールレートが動いた月の会合は、手元で裏が取れないので"
                "「変更なし」に混ぜず集計から外した",
        "extra": {"政策変更の回": [{"d": d, "what": w} for d, w in sorted(BOJ_CHANGES.items())],
                  "不明として除外": unknown, "反応日が取れず除外": miss},
    })

    # ---- 6. イベント当日に±2%以上動いた回の、その翌営業日
    allhits = {}
    for key in ("boj", "fomc", "cpi", "payroll"):
        e2 = tbl[key]
        ds = [x["d"] for x in e2["dates"] if str(YEAR_FROM) <= x["d"][:4]]
        r, _ = reaction_index(idx, pos, ds, e2.get("react", "same"))
        for dstr, i in r.items():
            allhits.setdefault(i, []).append(e2["label"])
    return cuts, allhits


def stats_for(px, idx, groups, allpos, window, recent_from=None):
    """グループごとの要約と、非イベント日との比較。recent_from があればその日以降だけ"""
    hitset = set(allpos)
    lo = min(allpos) if allpos else 0
    base_idx = [i for i in range(max(lo, 6), len(idx) - 2) if i not in hitset]
    if recent_from is not None:
        base_idx = [i for i in base_idx if idx[i] >= recent_from]
    base = [x for x in (windows(px, idx, i).get(window) for i in base_idx) if x is not None]
    out, vals = {}, {}
    for lab, gm in groups.items():
        ii = sorted(gm.values())
        if recent_from is not None:
            ii = [i for i in ii if idx[i] >= recent_from]
        v = [windows(px, idx, i).get(window) for i in ii]
        vals[lab] = [x for x in v if x is not None]
        out[lab] = summarize(v, base)
    return out, vals


def count_pvalues(obj):
    """報告に出したp値の総数。多重検定の規模を隠さないために自分で数える。
    直近3年の再計算は参考なので数えない（本編の判定に使っていない）"""
    n = 0
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "recent":
                continue
            n += 1 if (k.startswith("p_") and v is not None) else count_pvalues(v)
    elif isinstance(obj, list):
        for v in obj:
            n += count_pvalues(v)
    return n


def count_pvalues_key(obj, name):
    """特定のp値だけ数える（多重検定の内訳用）"""
    n = 0
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "recent":
                continue
            n += 1 if (k == name and v is not None) else count_pvalues_key(v, name)
    elif isinstance(obj, list):
        for v in obj:
            n += count_pvalues_key(v, name)
    return n


def main():
    px = prices()
    idx = list(px.index)
    pos = {t: i for i, t in enumerate(idx)}
    print(f"^N225 {len(idx)}営業日 {idx[0].date()} 〜 {idx[-1].date()}")

    tbl = json.loads(DATES.read_text(encoding="utf-8"))
    cuts, allhits = build_cuts(tbl, idx, pos)

    # ---- 6つ目: イベント当日に±2%以上動いた回の、その翌営業日
    g6 = {"+2%以上": {}, "−2%以下": {}, "±2%未満": {}}
    for i in sorted(allhits):
        d0 = windows(px, idx, i).get("d0")
        if d0 is None:
            continue
        lab = "+2%以上" if d0 >= BIG_MOVE else ("−2%以下" if d0 <= -BIG_MOVE else "±2%未満")
        g6[lab][idx[i].strftime("%Y-%m-%d")] = i
    cuts.append({
        "key": "big_move", "label": "イベント当日に±2%以上動いた回の、その翌営業日",
        "event": "日銀・FOMC・米CPI・米雇用統計の反応日すべて", "react": "-",
        "windows": ["d1"], "groups": g6,
        "all": {idx[i].strftime("%Y-%m-%d"): i for i in allhits},
        "fred": "不要（価格のみ）",
        "note": "反応日の当日リターン（d0）で分け、その翌営業日（d1）を測った。"
                "同じ日に複数のイベントが重なる回は1日として数える",
        "extra": {"対象の反応日": len(allhits)},
    })

    last = idx[-1]
    recent_from = last - pd.DateOffset(years=RECENT_YEARS)
    out = {
        "updated": datetime.now().astimezone().isoformat(timespec="seconds"),
        "price_source": "^N225 日足（Yahoo Finance）",
        "period": {"from": str(idx[0].date()), "to": str(last.date())},
        "year_from": YEAR_FROM,
        "windows": {"d0": "前日終値→当日終値（反応日）", "d1": "当日終値→翌日終値"},
        "caveats": [
            "市場予想（コンセンサス）は使っていません。10年分を無料で取る手段が無く、"
            "「公表データのみ・推計しない」の原則に反するためです。"
            "したがって『予想を上回った／下回った』は測れていません。"
            "差が出なかった場合、それは『指標が効かない』ではなく"
            "『実績の方向だけでは説明できない』という意味です",
            "米CPI・米雇用統計は前月分を翌月に発表します。発表日に紐づけたのは対象月＝発表月の1つ前です",
            "FREDの値は改定後の値です。特に雇用統計は毎月改定されるため、"
            "発表当日に市場が見た速報値とは違います",
            "反応日は、日銀が当日、FOMC・米CPI・米雇用統計が翌営業日です",
            "日経平均そのものが上昇している期間なので、必ず同じ期間の非イベント日と比べています",
            "過去にこう動いたという記録であって、次にどう動くかを示すものではありません",
        ],
        "cuts": [],
    }

    for c in cuts:
        allpos = sorted(set(c["all"].values()))
        co = {k: c[k] for k in ("key", "label", "event", "react", "fred", "note", "extra")}
        co["n_all"] = len(allpos)
        co["groups"] = {}
        co["between"] = {}
        co["recent"] = {}
        for w in c["windows"]:
            s, vals = stats_for(px, idx, c["groups"], allpos, w)
            labs = [k for k in c["groups"] if s[k].get("n", 0) >= 3]
            pairs = {}
            for a in range(len(labs)):
                for b in range(a + 1, len(labs)):
                    pairs[f"{labs[a]} vs {labs[b]}"] = compare(vals[labs[a]], vals[labs[b]])
            for lab in c["groups"]:
                s[lab]["verdict"] = verdict(s[lab])
            co["groups"][w] = s
            co["between"][w] = pairs
            co.setdefault("cut_verdict", {})[w] = cut_verdict(pairs)
            rs, _ = stats_for(px, idx, c["groups"], allpos, w, recent_from)
            co["recent"][w] = rs
        out["cuts"].append(co)

    n_tests = count_pvalues(out["cuts"])
    def count_key(name):
        return count_pvalues_key(out["cuts"], name)
    out["multiple_testing"] = {
        "n_tests": n_tests,
        "breakdown": {
            "グループ vs 非イベント日（Welch）": count_key("p_vs_base"),
            "グループ間（Welch）": count_key("p_ttest"),
            "グループ間（マン・ホイットニーU）": count_key("p_mannwhitney"),
            "グループ単体・平均が0か（t検定）": count_key("p_mean"),
            "グループ単体・上昇割合が50%か（二項検定）": count_key("p_up"),
        },
        "note": f"この検証で見たp値は{n_tests}個です。p<0.05は、まったく効果が無くても"
                f"{n_tests}回に{n_tests * 0.05:.1f}個くらいは偶然出ます。"
                "1つだけ光った結果は、偶然と区別できません",
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    # ------------------------------------------------ 画面の表（報告用）
    for co in out["cuts"]:
        print()
        print("■ " + co["label"] + f"（反応日 {co['n_all']}回）")
        hdr = (f"{'グループ':18s} {'窓':4s} {'n':>4s} {'平均%':>8s} {'上昇%':>7s} "
               f"{'非ev平均':>9s} {'差pt':>7s} {'p(vs非ev)':>10s}  判定")
        print("  " + hdr)
        print("  " + "-" * len(hdr))
        for w, s in co["groups"].items():
            for lab, x in s.items():
                if not x.get("n"):
                    continue
                print(f"  {lab:18s} {w:4s} {x['n']:4d} {x.get('mean_pct', 0):8.3f} "
                      f"{x.get('up_rate', 0):7.1f} {x.get('base_mean_pct', 0):9.3f} "
                      f"{x.get('diff_mean_pt', 0):7.3f} {x.get('p_vs_base', 1):10.4f}  "
                      f"{x.get('verdict', '')}")
        for w, pairs in co["between"].items():
            print(f"    ［{w}］切り方そのものの判定: {co['cut_verdict'][w]}")
            for k, cm in pairs.items():
                if "p_ttest" not in cm:
                    print(f"    [{w}] {k}: 件数不足 (n={cm['n_a']}/{cm['n_b']})")
                    continue
                print(f"    [{w}] {k}: 差{cm['diff_pt']:+.3f}pt  "
                      f"t検定 p={cm['p_ttest']:.4f}  U検定 p={cm['p_mannwhitney']:.4f}")
        r = co["recent"].get("d0") or {}
        rr = [f"{lab} n={x.get('n', 0)}" for lab, x in r.items()]
        if rr:
            print(f"    直近{RECENT_YEARS}年(d0): " + " / ".join(rr))

    print()
    print(f"多重検定: 見たp値は {n_tests} 個（偶然でも約{n_tests * 0.05:.1f}個は p<0.05 になる）")
    print(f"→ {OUT.name} ({OUT.stat().st_size / 1024:.1f}KB)  ※docs/ には置いていない")


if __name__ == "__main__":
    main()
