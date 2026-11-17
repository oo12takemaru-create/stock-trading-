# -*- coding: utf-8 -*-
"""相場の温度計を 1990年までさかのぼる → docs/thermo_long.json・docs/thermo_long_hist.json（2026-11-17）

■ 何か
  公開中の温度（thermo100.py・過去10年）と **同じ関数・同じ窓** で、1990年からの温度を出す。
  判定は detect_market_regime、5つの入力の位置は thermo100.positions、帯の中の位置は thermo100.cond_pct。
  窓（約10年＝2,450営業日）も同じなので、直近10年の温度は thermo100_hist.json と同じ値になる
  （vs_thermo100 に照合の結果を残す）。thermo100.py・thermo100.json・thermo100_hist.json は書き換えない。

■ 出すもの
  thermo_long_hist.json  1990年からの日ごとの温度（d・t・tc・r・c）
  thermo_long.json
    today          今日の温度（thermo100.json と同じ値のはず）
    records        今日の記録（「◯日ぶりの氷点下」など。当たらない日は空）と直近10営業日の記録
    famous         有名な日の温度（日経平均の1日の騰落が大きい順に機械的に拾った日・各年でいちばん寒かった日・名前の付いた日）
    stats          温度ごとのその後（35年と10年を並べる。初日／続いた日の区別つき）
    window_compare 帯の中の位置を「過去10年の窓」と「1990年からその日までの全部」で測った場合の、直近10年の違い
                   （公開中の値は変えない。変えるかは統括が決める）

■ 1990年の扱い
  VIX は 1990-01-02 から。日経平均・S&P500 は 1987年から取り、200日線の助走に使う。
  位置は「その日までの本数が250本以上」の入力だけで出すので、1990年は VIX の位置がまだ無く4つの入力で出る
  （1991年1月から5つ）。帯の中の位置も、同じ判定の日が20日に満たないうちは全日の中の位置を使う（thermo100 と同じ）。

■ 使い方
  python -X utf8 thermo_long.py            # docs/ に2つの JSON を書く
  python -X utf8 thermo_long.py --dry-run  # 書かずに要約だけ
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))

import thermo100 as T                                    # noqa: E402
from thermo100 import BAND, BANDS, BANDS_C, celsius, c_label, JST  # noqa: E402
from thermo_pos import text_ng                           # noqa: E402

DOCS = REPO / "docs"
START = "1990-01-01"          # 温度を出し始める日
FETCH_FROM = "1987-01-01"     # 200日線の助走
EXPANDING = 100_000           # 「1990年からその日までの全部」を窓にするときの本数（実質無限）

NAME = "相場の温度計（1990年から）"
DEFINITION = (
    "公開中の相場の温度計と同じ計算（判定・5つの入力・過去約10年の窓）を、1990年までさかのぼって出したものです。"
    "直近10年の値は公開中の温度計と同じです。1990年は VIX の位置がまだ出ないため、4つの入力で出しています。"
    "℃ は 0〜100 の値を −20℃〜60℃ に読み替えたもの（℃ = 値 × 0.8 − 20）です。"
    "これは地合いの要約で、売買の合図ではありません。"
)

# 名前を付ける日（日付と出来事が確かなものだけ）。期待する向き（日経平均の騰落の符号）が合わない日は名前を付けない
NAMED = {
    "2001-09-12": ("米国同時多発テロの翌日", -1),
    "2008-09-16": ("リーマン・ブラザーズ破綻の翌営業日", -1),
    "2008-10-14": ("リーマン・ショック後の急反発", +1),
    "2008-10-16": ("リーマン・ショック後の急落", -1),
    "2011-03-14": ("東日本大震災の翌営業日", -1),
    "2011-03-15": ("東日本大震災の2営業日後", -1),
    "2013-05-23": ("2013年5月23日の急落", -1),
    "2015-08-24": ("人民元の切り下げ後の世界的な株安", -1),
    "2016-06-24": ("英国のEU離脱を決めた国民投票の開票日", -1),
    "2018-12-25": ("2018年12月の急落", -1),
    "2020-03-13": ("新型コロナの世界的な広がりによる急落", -1),
    "2024-08-05": ("2024年8月5日の急落（下げ幅が過去最大）", -1),
    "2024-08-06": ("2024年8月5日の翌日の急反発", +1),
}

HORIZONS = (5, 20, 60)


# ────────────────────────── 計算（thermo100 の関数をそのまま使う） ──────────────────────────
def regimes_and_scores(g: dict, regime_fn, dates: pd.DatetimeIndex, pos: pd.DataFrame):
    """thermo100.build_rows の前半と同じ（判定と総合の位置）。判定は1回だけ計算して2通りの窓で使う"""
    regimes, score = [], []
    for d in dates:
        r = regime_fn(g, d.date())[0]
        p = pos.loc[d].dropna()
        ok = r in BAND and len(p)
        regimes.append(r if ok else None)
        score.append(float(p.mean()) / 100 if ok else np.nan)
    return regimes, np.array(score)


def rows_from(dates, regimes, score, window, keep_from=None) -> list[dict]:
    """thermo100.build_rows の後半と同じ（帯の中の位置 → 温度）。window だけ変えられる"""
    inband = T.cond_pct(score, regimes, window=window)
    rows = []
    for i, d in enumerate(dates):
        if regimes[i] is None or np.isnan(inband[i]):
            continue
        if keep_from is not None and d < keep_from:
            continue
        rows.append({"d": d, "regime": regimes[i], "pos": float(inband[i]), "t": T.place(regimes[i], inband[i])})
    return rows


def build_long(g: dict, regime_fn, window_pos=T.WINDOW, also_expanding=True):
    """1990年からの温度（公開中と同じ窓）と、比較用に「その日までの全部」を窓にした温度"""
    n225 = g["^N225"]["Close"].dropna()
    dates = n225.index
    feat = T.features(g, dates)
    pos = T.positions(feat)                     # 窓 = thermo100.WINDOW（公開中と同じ）
    calc = dates[199:] if len(dates) > 199 else dates
    regimes, score = regimes_and_scores(g, regime_fn, calc, pos)
    keep = pd.Timestamp(START)
    rows = rows_from(calc, regimes, score, T.WINDOW, keep_from=keep)
    alt = None
    if also_expanding:
        # 5つの入力の位置も、帯の中の位置も「1990年からその日までの全部」で測る（先読みはしない）
        pos_e = pd.DataFrame(index=feat.index)
        for k, _, _, sign in T.INPUTS:
            p = T.rolling_pct(feat[k].values, window=EXPANDING)
            pos_e[k] = (p if sign > 0 else 1 - p) * 100
        reg_e, score_e = regimes_and_scores_fast(regimes, calc, pos_e)
        alt = {str(r["d"].date()): r["t"] for r in rows_from(calc, reg_e, score_e, EXPANDING, keep_from=keep)}
    return rows, alt, n225


def regimes_and_scores_fast(regimes, dates, pos):
    """判定は計算済みのものを使い回し、総合の位置だけ別の位置から作る"""
    score = []
    for r, d in zip(regimes, dates):
        p = pos.loc[d].dropna()
        score.append(float(p.mean()) / 100 if (r is not None and len(p)) else np.nan)
    return regimes, np.array(score)


# ────────────────────────── その後の集計（note の下書きと同じ定義） ──────────────────────────
def fwd(C: list, i: int, n: int):
    if i + n >= len(C) or C[i] is None or C[i + n] is None:
        return None
    return (C[i + n] / C[i] - 1) * 100


def episodes(idx: list[int]) -> list[list[int]]:
    out = []
    for i in sorted(idx):
        if out and i == out[-1][-1] + 1:
            out[-1].append(i)
        else:
            out.append([i])
    return out


def summ(idx: list[int], C: list, whole=False) -> dict:
    s = {"n_days": len(idx), "episodes": len(episodes(idx))}
    for n in HORIZONS:
        v = [r for r in (fwd(C, i, n) for i in idx) if r is not None]
        s[f"r{n}"] = {"n": len(v),
                      "avg": round(float(np.mean(v)), 2) if v else None,
                      "med": round(float(np.median(v)), 2) if v else None,
                      "up_pct": round(sum(r > 0 for r in v) / len(v) * 100, 1) if v else None}
    s["sankou"] = bool((not whole and s["episodes"] < 5) or s["r20"]["n"] < 30)
    return s


def stat_groups(hist: list[dict]) -> list[dict]:
    """区分は note の下書き（_下書き_温度計の検証_計算.py）と同じ。
    初日 = 前の営業日が条件の外だった日 / 続いた日 = 前の営業日も条件の中だった日"""
    T_ = [h["t"] for h in hist]
    C = [h["c"] for h in hist]
    N = len(hist)
    ALL = list(range(N))
    le20 = [i for i in ALL if T_[i] <= 20]
    ge80 = [i for i in ALL if T_[i] >= 80]
    G = [("all", "全期間", "全期間", ALL, True),
         ("le20", "温度20以下", f"{c_label(celsius(20))}以下", le20, False),
         ("le20_first", "温度20以下に入った初日", f"{c_label(celsius(20))}以下に入った初日",
          [i for i in le20 if i == 0 or T_[i - 1] > 20], False),
         ("le20_cont", "温度20以下が続いた日（2日目以降）", f"{c_label(celsius(20))}以下が続いた日（2日目以降）",
          [i for i in le20 if i > 0 and T_[i - 1] <= 20], False),
         ("ge80", "温度80以上", f"{c_label(celsius(80))}以上", ge80, False),
         ("ge80_first", "温度80以上に入った初日", f"{c_label(celsius(80))}以上に入った初日",
          [i for i in ge80 if i == 0 or T_[i - 1] < 80], False),
         ("ge80_cont", "温度80以上が続いた日（2日目以降）", f"{c_label(celsius(80))}以上が続いた日（2日目以降）",
          [i for i in ge80 if i > 0 and T_[i - 1] >= 80], False)]
    for lo, hi in [(0, 14), (15, 39), (40, 59), (60, 79), (80, 100)]:
        G.append((f"t{lo}_{hi}", f"温度{lo}〜{hi}", f"{c_label(celsius(lo))}〜{c_label(celsius(hi))}",
                  [i for i in ALL if lo <= T_[i] <= hi], False))
    out = []
    for key, label, label_c, idx, whole in G:
        out.append({"key": key, "label": label, "label_c": label_c, "n225": summ(idx, C, whole)})
    return out


def stats_block(hist: list[dict]) -> dict:
    last = hist[-1]["d"]
    from10 = str((pd.Timestamp(last) - pd.Timedelta(days=int(365.25 * T.HIST_YEARS))).date())
    h10 = [h for h in hist if h["d"] >= from10]
    return {
        "how": "日経平均の、温度が出た日の終値から 5・20・60営業日後の終値までの騰落（%）。"
               "n営業日後は履歴の並びで数える。続いた日は1つのかたまり（episodes）。日ごとの値は期間が重なるので独立ではない。"
               "初日＝前の営業日が条件の外だった日、続いた日＝前の営業日も条件の中だった日。手数料・税金は含まない。",
        "sankou_rule": "かたまりが5未満、または20営業日後の件数が30未満なら参考値（sankou=true）",
        "y35": {"period": [hist[0]["d"], last], "groups": stat_groups(hist)},
        "y10": {"period": [h10[0]["d"], last], "groups": stat_groups(h10)},
    }


# ────────────────────────── 有名な日 ──────────────────────────
def day_row(hist, i, C, chg=None):
    h = hist[i]
    r = {"d": h["d"], "t": h["t"], "tc": h["tc"], "label_c": c_label(h["tc"]),
         "r": h["r"], "regime_jp": BAND[h["r"]]["jp"], "c": h["c"]}
    if chg is not None:
        r["chg"] = round(chg, 2)
    for n in (20, 60):
        v = fwd(C, i, n)
        r[f"after{n}"] = None if v is None else round(v, 2)
    return r


def famous_days(hist: list[dict], n_drop=15, n_rise=8, gap=10) -> dict:
    """日経平均の1日の騰落が大きい順に機械的に拾う。近い日（gap 営業日以内）に、より大きく動いた日があれば飛ばす"""
    C = [h["c"] for h in hist]
    chg = [None] + [(C[i] / C[i - 1] - 1) * 100 for i in range(1, len(C))]
    order = sorted((i for i in range(1, len(C))), key=lambda i: chg[i])

    def pick(seq, k, sign):
        got = []
        for i in seq:
            if (chg[i] < 0) != (sign < 0):
                break
            if any(abs(i - j) <= gap for j in got):
                continue
            got.append(i)
            if len(got) >= k:
                break
        out = []
        for i in got:
            r = day_row(hist, i, C, chg[i])
            nm = NAMED.get(hist[i]["d"])
            if nm and (chg[i] > 0) == (nm[1] > 0):
                r["name"] = nm[0]
            out.append(r)
        return out

    drops = pick(order, n_drop, -1)
    rises = pick(list(reversed(order)), n_rise, +1)
    # 各年でいちばん寒かった日（同じ温度なら早い日）
    by_year = {}
    for i, h in enumerate(hist):
        y = h["d"][:4]
        if y not in by_year or h["t"] < hist[by_year[y]]["t"]:
            by_year[y] = i
    year_cold = [day_row(hist, i, C, chg[i]) for _, i in sorted(by_year.items())]
    pos = {h["d"]: i for i, h in enumerate(hist)}
    named = []
    for d, (nm, sign) in sorted(NAMED.items()):
        i = pos.get(d)
        if i is None or chg[i] is None or (chg[i] > 0) != (sign > 0):
            continue
        r = day_row(hist, i, C, chg[i])
        r["name"] = nm
        named.append(r)
    return {
        "how": f"日経平均の1日の騰落（前営業日の終値比）が大きい順に、下げ{n_drop}日・上げ{n_rise}日を機械的に拾った。"
               f"{gap}営業日以内により大きく動いた日があれば、その日は飛ばす（同じ出来事を何度も数えない）。"
               "name は日付と出来事が確かな日だけに付けた。after20・after60 はその日の終値から20・60営業日後の終値までの騰落（%）。",
        "drops": drops, "rises": rises, "year_coldest": year_cold, "named": named,
    }


# ────────────────────────── 記録（毎日の一言の材料） ──────────────────────────
FREEZE = 0      # ℃ がこれ未満 = 氷点下
HOT = 40        # ℃ がこれ以上 = 40℃超え
MOVE5 = 15      # 5営業日で ℃ がこれ以上動いた日だけ見る
SINCE_MIN = 250  # 「◯年◯月以来」と言うのは、前回がこれより前（営業日）のときだけ
MOVE_SINCE_MIN = 60


def ym(d: str) -> str:
    return f"{int(d[:4])}年{int(d[5:7])}月"


def records_at(hist: list[dict], i: int) -> list[dict]:
    """i 日目の記録。種類（type）・数字・前回の日付（prev）・機械的な一文（text）"""
    if i <= 0:
        return []
    h = hist[i]
    tc = [x["tc"] for x in hist]
    rg = [x["r"] for x in hist]
    D = [x["d"] for x in hist]
    out = []
    # 1) 氷点下に入った日・40℃以上に入った日
    if tc[i] < FREEZE <= tc[i - 1]:
        j = next((k for k in range(i - 1, -1, -1) if tc[k] < FREEZE), None)
        out.append({"type": "freeze_in", "value": tc[i], "n": None if j is None else i - j,
                    "prev": None if j is None else D[j],
                    "text": f"{i - j}営業日ぶりの氷点下（{c_label(tc[i])}）。前回は{D[j]}" if j is not None
                    else f"1990年以来はじめての氷点下（{c_label(tc[i])}）"})
    if tc[i] >= HOT > tc[i - 1]:
        j = next((k for k in range(i - 1, -1, -1) if tc[k] >= HOT), None)
        out.append({"type": "hot_in", "value": tc[i], "n": None if j is None else i - j,
                    "prev": None if j is None else D[j],
                    "text": f"{i - j}営業日ぶりの{HOT}℃以上（{c_label(tc[i])}）。前回は{D[j]}" if j is not None
                    else f"1990年以来はじめての{HOT}℃以上（{c_label(tc[i])}）"})
    # 2) 今年◯番目の寒さ・暑さ（その年の20営業日目以降・3番目まで）
    y = D[i][:4]
    yr = [k for k in range(i + 1) if D[k][:4] == y]
    if len(yr) >= 20:
        cold_rank = 1 + sum(1 for k in yr if tc[k] < tc[i])
        hot_rank = 1 + sum(1 for k in yr if tc[k] > tc[i])
        ties = sum(1 for k in yr if tc[k] == tc[i]) - 1
        if cold_rank <= 3:
            out.append({"type": "year_cold", "value": tc[i], "n": cold_rank, "ties": ties, "days": len(yr),
                        "prev": None,
                        "text": f"今年{'いちばん' if cold_rank == 1 else f'{cold_rank}番目'}の寒さ（{c_label(tc[i])}"
                                f"{'・同じ温度の日あり' if ties else ''}）"})
        if hot_rank <= 3:
            out.append({"type": "year_hot", "value": tc[i], "n": hot_rank, "ties": ties, "days": len(yr),
                        "prev": None,
                        "text": f"今年{'いちばん' if hot_rank == 1 else f'{hot_rank}番目'}の暑さ（{c_label(tc[i])}"
                                f"{'・同じ温度の日あり' if ties else ''}）"})
    # 3) ◯年◯月以来の低さ・高さ（前回が約1年より前のときだけ）
    j = next((k for k in range(i - 1, -1, -1) if tc[k] <= tc[i]), None)
    if j is None or i - j >= SINCE_MIN:
        if j is not None or i >= SINCE_MIN:
            out.append({"type": "low_since", "value": tc[i], "n": None if j is None else i - j,
                        "prev": None if j is None else D[j],
                        "text": f"{ym(D[j])}以来の低さ（{c_label(tc[i])}）" if j is not None
                        else f"1990年以来いちばん低い（{c_label(tc[i])}）"})
    j = next((k for k in range(i - 1, -1, -1) if tc[k] >= tc[i]), None)
    if j is None or i - j >= SINCE_MIN:
        if j is not None or i >= SINCE_MIN:
            out.append({"type": "high_since", "value": tc[i], "n": None if j is None else i - j,
                        "prev": None if j is None else D[j],
                        "text": f"{ym(D[j])}以来の高さ（{c_label(tc[i])}）" if j is not None
                        else f"1990年以来いちばん高い（{c_label(tc[i])}）"})
    # 4) 1週間（5営業日）で◯℃下がった・上がった（同じだけ動いたのが約3か月より前のときだけ）
    if i >= 5:
        dlt = tc[i] - tc[i - 5]
        if abs(dlt) >= MOVE5:
            if dlt < 0:
                j = next((k for k in range(i - 1, 4, -1) if tc[k] - tc[k - 5] <= dlt), None)
            else:
                j = next((k for k in range(i - 1, 4, -1) if tc[k] - tc[k - 5] >= dlt), None)
            if j is None or i - j >= MOVE_SINCE_MIN:
                word = "下がった" if dlt < 0 else "上がった"
                out.append({"type": "drop5" if dlt < 0 else "rise5", "value": dlt,
                            "n": None if j is None else i - j, "prev": None if j is None else D[j],
                            "text": f"1週間で{abs(dlt)}℃{word}のは{ym(D[j])}以来" if j is not None
                            else f"1週間で{abs(dlt)}℃{word}のは1990年以来はじめて"})
    # 5) 判定が変わった（前回の変わり目から何営業日ぶりか）・その判定は何営業日ぶりか
    if rg[i] != rg[i - 1]:
        j = next((k for k in range(i - 1, 0, -1) if rg[k] != rg[k - 1]), None)
        out.append({"type": "regime_change", "value": rg[i], "from": rg[i - 1],
                    "n": None if j is None else i - j, "prev": None if j is None else D[j],
                    "text": f"判定が{BAND[rg[i - 1]]['jp']}から{BAND[rg[i]]['jp']}に変わった"
                            + (f"（判定が変わったのは{i - j}営業日ぶり）" if j is not None else "")})
        j2 = next((k for k in range(i - 1, -1, -1) if rg[k] == rg[i]), None)
        if j2 is None or i - j2 >= 20:
            out.append({"type": "regime_return", "value": rg[i], "n": None if j2 is None else i - j2,
                        "prev": None if j2 is None else D[j2],
                        "text": f"{BAND[rg[i]]['jp']}の判定は{i - j2}営業日ぶり（前回は{D[j2]}）" if j2 is not None
                        else f"{BAND[rg[i]]['jp']}の判定は1990年以来はじめて"})
    return out


# ────────────────────────── 比べる ──────────────────────────
def window_compare(hist: list[dict], alt: dict) -> dict:
    last = hist[-1]["d"]
    from10 = str((pd.Timestamp(last) - pd.Timedelta(days=int(365.25 * T.HIST_YEARS))).date())
    pairs = [(h["tc"], celsius(alt[h["d"]])) for h in hist if h["d"] >= from10 and h["d"] in alt]
    if not pairs:
        return {}
    d = np.array([b - a for a, b in pairs])
    by_year = {}
    for h in hist:
        if h["d"] >= from10 and h["d"] in alt:
            by_year.setdefault(h["d"][:4], []).append(celsius(alt[h["d"]]) - h["tc"])
    return {
        "how": "帯の中の位置を、公開中の作り（その日までの過去約10年＝2,450営業日の窓）と、"
               "1990年からその日までの全部の窓で測った場合の、直近10年の温度（℃）の差。どちらも先読みはしない。"
               "判定（帯）は同じなので、差は帯の中の位置だけ。diff = 全部の窓 − 公開中。",
        "period": [from10, last], "n_days": len(pairs),
        "same_pct": round(float(np.mean(d == 0)) * 100, 1),
        "within4_pct": round(float(np.mean(np.abs(d) <= 4)) * 100, 1),
        "mean_abs_c": round(float(np.mean(np.abs(d))), 2),
        "mean_c": round(float(np.mean(d)), 2),
        "max_abs_c": int(np.max(np.abs(d))),
        "today": {"d": last, "now_c": hist[-1]["tc"], "expanding_c": celsius(alt.get(last)) if last in alt else None},
        "by_year_mean_c": {y: round(float(np.mean(v)), 2) for y, v in sorted(by_year.items())},
    }


def vs_thermo100(hist: list[dict], docs: Path = DOCS) -> dict | None:
    """公開中の thermo100_hist.json と同じ日の温度が同じか"""
    try:
        h = json.loads((docs / "thermo100_hist.json").read_text(encoding="utf-8"))["items"]
    except Exception:
        return None
    mine = {x["d"]: x["t"] for x in hist}
    both = [x for x in h if x["d"] in mine]
    diff = [x for x in both if x["t"] != mine[x["d"]]]
    return {"n_days": len(both), "n_diff": len(diff),
            "max_abs": max((abs(x["t"] - mine[x["d"]]) for x in diff), default=0),
            "examples": [{"d": x["d"], "thermo100": x["t"], "long": mine[x["d"]]} for x in diff[:5]]}


# ────────────────────────── 組み立て ──────────────────────────
def build(g: dict, regime_fn, now: datetime, docs: Path = DOCS) -> tuple[dict, dict]:
    rows, alt, n225 = build_long(g, regime_fn)
    if not rows:
        raise RuntimeError("温度を1日も出せなかった")
    hist = [{"d": str(r["d"].date()), "t": r["t"], "tc": celsius(r["t"]), "r": r["regime"],
             "c": round(float(n225[r["d"]]), 2)} for r in rows]
    i = len(hist) - 1
    recent = []
    for k in range(max(1, i - 9), i + 1):
        rec = records_at(hist, k)
        if rec:
            recent.append({"d": hist[k]["d"], "tc": hist[k]["tc"], "records": rec})
    cur = hist[-1]
    data = {
        "name": NAME,
        "updated": now.isoformat(timespec="seconds"),
        "trade_date": cur["d"],
        "today": {"value": cur["t"], "tc": cur["tc"], "label_c": c_label(cur["tc"]),
                  "regime": cur["r"], "regime_jp": BAND[cur["r"]]["jp"]},
        "records": records_at(hist, i),
        "records_recent": recent,
        "records_rules": {
            "freeze_in": "℃ が0未満に入った日（前の営業日は0以上）。n = 前回の氷点下の日から何営業日ぶりか",
            "hot_in": f"℃ が{HOT}以上に入った日。n = 前回から何営業日ぶりか",
            "year_cold / year_hot": "その年の20営業日目以降で、その年の中で3番目までに低い（高い）日。ties = 同じ℃の日の数",
            "low_since / high_since": f"その日以下（以上）の℃ だった直前の日が{SINCE_MIN}営業日より前のとき。prev = その日",
            "drop5 / rise5": f"5営業日前から{MOVE5}℃以上動き、同じかそれ以上動いた直前の日が{MOVE_SINCE_MIN}営業日より前のとき",
            "regime_change": "判定が前の営業日から変わった日。n = 前回の変わり目から何営業日ぶりか",
            "regime_return": "その判定になったのが20営業日以上ぶりのとき",
        },
        "famous": famous_days(hist),
        "stats": stats_block(hist),
        "window_compare": window_compare(hist, alt) if alt else None,
        "vs_thermo100": vs_thermo100(hist, docs),
        "bands": BANDS, "bands_c": BANDS_C,
        "definition": DEFINITION,
        "sources": "Yahoo! Finance の ^N225・^GSPC・^VIX（公開の指数値のみ）",
        "history_file": "thermo_long_hist.json", "history_days": len(hist),
    }
    hist_doc = {"name": NAME, "updated": data["updated"],
                "fields": {"d": "日付", "t": "温度（0〜100）", "tc": "温度（℃）", "r": "温度計の判定", "c": "日経平均の終値"},
                "bands": BANDS, "bands_c": BANDS_C, "items": hist}
    return data, hist_doc


def fetch():
    g = {}
    for t in ("^N225", "^GSPC", "^VIX"):
        df = T.dl(t, period="max")
        g[t] = df[df.index >= pd.Timestamp(FETCH_FROM)]
    return g


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--docs", default=str(DOCS))
    a = ap.parse_args()
    docs = Path(a.docs)
    from daily_scanner_v2_8_0 import detect_market_regime
    bad = text_ng(DEFINITION) + [w for v in NAMED.values() for w in text_ng(v[0])]
    if bad:
        raise SystemExit(f"定義文に使わない言葉: {bad}")
    g = fetch()
    data, hist = build(g, detect_market_regime, datetime.now(JST), docs)
    print(f"{data['trade_date']} {data['today']['label_c']}（{data['today']['regime_jp']}） / 履歴 {len(hist['items'])}日"
          f"（{hist['items'][0]['d']}〜） / thermo100 との照合 {data['vs_thermo100']}")
    for r in data["records"]:
        print("  記録:", r["text"])
    wc = data["window_compare"]
    if wc:
        print(f"  窓の比較: 同じ {wc['same_pct']}% / ±4℃以内 {wc['within4_pct']}% / 平均の差 {wc['mean_abs_c']}℃ / 最大 {wc['max_abs_c']}℃")
    if a.dry_run:
        return
    out, hp = docs / "thermo_long.json", docs / "thermo_long_hist.json"
    try:
        old = json.loads(out.read_text(encoding="utf-8")).get("trade_date") or ""
    except Exception:
        old = ""
    if old and data["trade_date"] < old:
        print(f"既存（{old}）より古い {data['trade_date']} なので書かない", file=sys.stderr)
        return
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    hp.write_text(json.dumps(hist, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"書いた: {out.name} / {hp.name}")


if __name__ == "__main__":
    main()
