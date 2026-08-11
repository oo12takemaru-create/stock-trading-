# -*- coding: utf-8 -*-
"""暴落警戒度メーター → docs/crash.json（株レーダー kaburadar.jp）

「今日の終値から20営業日以内に-10%以上下げるか」を、当日までに観測できる
7つの固定閾値フラグで判定する。フラグの数で3段階に分ける。

■ なぜ3段階なのか
  検証したところ、5段階に細かく分けると中間の段階がアウトオブサンプル
  （2016年以降）で平常圏と区別がつかなかった。実測で差が出る境目だけを
  段階の境目にしている。

■ 発生率は毎回その場で再計算する
  数字をコードに焼き込むと実データとズレていく。26年分を毎回計算して、
  ページに出す数字と実際のデータが必ず一致するようにしてある。

■ 検証結果（2000年〜2026年、20営業日以内に-10%以上下落した割合）
  平常圏(0〜4個)  90.7%の日   7.2%  → 全体の0.9倍
  警戒  (5個)      6.7%の日  16.0%  → 1.9倍
  危険  (6個以上)  2.6%の日  27.9%  → 3.4倍
  2016年以降だけで見ると 4.8% / 16.5% / 28.9%（0.8倍 / 2.9倍 / 5.0倍）
"""
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

JST = timezone(timedelta(hours=9))
OUT = Path(__file__).parent / "docs" / "crash.json"

HORIZON = 20          # 営業日（≒1ヶ月）
CRASH = -0.10         # 「暴落」の定義
START = "1998-01-01"
SPLIT = "2016-01-01"  # アウトオブサンプルの境目

TICKERS = {"n225": "^N225", "vix": "^VIX", "usdjpy": "JPY=X",
           "gold": "GC=F", "spx": "^GSPC"}

# key: (説明, 閾値の表示, 判定方向, 現在値の書式)
#   direc="below" → 値 < thr で点灯 / "above" → 値 >= thr で点灯
FLAGS = [
    ("spx_ma200_dev",  "S&P500が200日線割れ",      0.0,   "below", "pct",
     "米国株が長期トレンドを割ると、日本株の下落も深くなりやすい"),
    ("vix_chg5",       "VIXが5日で15%以上上昇",     0.15,  "above", "pct",
     "恐怖指数の急な立ち上がり。水準より変化のほうが効く"),
    ("usdjpy_chg20",   "20営業日で3%以上の円高",    -0.03, "below", "pct",
     "急な円高は輸出企業の採算と指数を直接押し下げる"),
    ("ma200_dev",      "日経が200日線割れ",         0.0,   "below", "pct",
     "長期トレンドの下。下げが下げを呼ぶ状態に入りやすい"),
    ("ret20",          "日経が20営業日でマイナス",   0.0,   "below", "pct",
     "すでに下げている。ボラティリティは連続する"),
    ("vix_high",       "VIXが20以上",              20.0,  "above", "num",
     "市場が平常時より不安定と見ている水準"),
    ("gold_chg20",     "金が20営業日で5%以上上昇",   0.05,  "above", "pct",
     "株を持ちながら保険を買う動き。逃避needsの表れ"),
]

STAGES = [(4, "calm", "平常圏"), (5, "warn", "警戒"), (99, "danger", "危険")]


def load():
    raw = yf.download(list(TICKERS.values()), start=START, auto_adjust=False,
                      progress=False, threads=True)
    px = raw["Close"].rename(columns={v: k for k, v in TICKERS.items()})
    px = px.reindex(columns=list(TICKERS))
    # 日本の営業日を基準に。他市場が休みの日は前日値（=実際にその日見られる情報）
    px = px[px["n225"].notna()].ffill()
    return px.dropna(subset=["n225"])


def features(px):
    f = pd.DataFrame(index=px.index)
    n = px["n225"]
    f["ma200_dev"] = n / n.rolling(200).mean() - 1
    f["ret20"] = n.pct_change(20)
    f["vix_high"] = px["vix"]
    f["vix_chg5"] = px["vix"].pct_change(5)
    f["usdjpy_chg20"] = px["usdjpy"].pct_change(20)
    f["gold_chg20"] = px["gold"].pct_change(20)
    f["spx_ma200_dev"] = px["spx"] / px["spx"].rolling(200).mean() - 1
    return f


def flag_matrix(f):
    cols = {}
    for key, _, thr, direc, _, _ in FLAGS:
        s = f[key]
        cols[key] = (s < thr if direc == "below" else s >= thr).astype(float)
    F = pd.DataFrame(cols, index=f.index)
    F[f[[k for k, *_ in FLAGS]].isna().any(axis=1)] = np.nan
    return F


def fwd_drawdown(px, horizon=HORIZON):
    """t+1〜t+horizon の最安値 / 当日終値 - 1（先読みなし）"""
    n = px["n225"]
    fwd_min = n.shift(-1).rolling(horizon, min_periods=horizon).min().shift(-(horizon - 1))
    return fwd_min / n - 1


def stage_of(score):
    for lim, key, jp in STAGES:
        if score <= lim:
            return key, jp
    return STAGES[-1][1], STAGES[-1][2]


def rates(score, y, mask):
    """段階ごとの実測発生率"""
    out = {}
    base = float((y[mask] <= CRASH).mean())
    for lim, key, jp in STAGES:
        lo = 0 if key == "calm" else STAGES[[s[1] for s in STAGES].index(key) - 1][0] + 1
        m = mask & (score >= lo) & (score <= lim)
        if m.sum() == 0:
            continue
        out[key] = {
            "days": int(m.sum()),
            "share": round(float(m.sum() / mask.sum()) * 100, 1),
            "d7": round(float((y[m] <= -0.07).mean()) * 100, 1),
            "d10": round(float((y[m] <= -0.10).mean()) * 100, 1),
            "d15": round(float((y[m] <= -0.15).mean()) * 100, 1),
            "ratio": round(float((y[m] <= CRASH).mean()) / base, 1),
        }
    out["_base"] = round(base * 100, 1)
    out["_n"] = int(mask.sum())
    return out


def fmt(v, kind):
    return f"{v:+.2%}" if kind == "pct" else f"{v:.2f}"


def fmt_thr(v, kind, direc):
    s = f"{v:.0%}" if kind == "pct" else f"{v:.0f}"
    return (f"{s}未満" if direc == "below" else f"{s}以上")


def distance(v, thr, direc, kind):
    """点灯まで（消灯まで）どれだけか。ユーザーが一番知りたい情報"""
    gap = (v - thr) if direc == "below" else (thr - v)
    if kind == "pct":
        return f"あと{abs(gap):.2%}" if gap > 0 else f"{abs(gap):.2%}超過"
    return f"あと{abs(gap):.2f}" if gap > 0 else f"{abs(gap):.2f}超過"


def main():
    px = load()
    f = features(px)
    F = flag_matrix(f)
    score = F.sum(axis=1, min_count=len(FLAGS))
    y = fwd_drawdown(px)

    ok = score.notna() & y.notna()
    if ok.sum() < 2000:
        print(f"検証に使えるデータが少なすぎます: {ok.sum()}", file=sys.stderr)
        sys.exit(1)

    stats_all = rates(score, y, ok)
    stats_recent = rates(score, y, ok & (px.index >= SPLIT))

    # 各フラグ単独の倍率（全期間）
    base = float((y[ok] <= CRASH).mean())
    ratios = {}
    for key, *_ in FLAGS:
        on = ok & (F[key] == 1)
        ratios[key] = round(float((y[on] <= CRASH).mean()) / base, 2) if on.sum() >= 100 else None

    cur_score = int(score.iloc[-1])
    skey, sjp = stage_of(cur_score)
    last = px.index[-1]

    flags = []
    for key, label, thr, direc, kind, why in FLAGS:
        v = float(f[key].iloc[-1])
        on = bool(F[key].iloc[-1] == 1)
        flags.append({
            "key": key, "label": label, "on": on,
            "value": fmt(v, kind), "threshold": fmt_thr(thr, kind, direc),
            "distance": distance(v, thr, direc, kind),
            "ratio": ratios[key], "why": why,
        })
    # 点灯中を上に、消灯は「点灯に近い順」に。
    # VIXは「pt」・他は「%」で単位が違うので、そのまま引き算した値では比べられない。
    # 各指標自身の標準偏差で割って無単位にしてから並べる。
    sd = {k: float(f[k].std()) or 1.0 for k, *_ in FLAGS}
    thr_of = {k: t for k, _, t, *_ in FLAGS}
    near = {k: abs(float(f[k].iloc[-1]) - thr_of[k]) / sd[k] for k, *_ in FLAGS}
    flags.sort(key=lambda x: (not x["on"], near[x["key"]]))

    hist = [{"d": d.strftime("%Y-%m-%d"), "s": int(v)}
            for d, v in score.tail(120).items() if not np.isnan(v)]

    data = {
        "updated": datetime.now(JST).isoformat(timespec="seconds"),
        "trade_date": last.strftime("%Y-%m-%d"),
        "n225": round(float(px["n225"].iloc[-1]), 2),
        "horizon": HORIZON,
        "crash_def": CRASH,
        "score": cur_score,
        "flag_total": len(FLAGS),
        "stage": sjp,
        "stage_key": skey,
        "prob": stats_all.get(skey, {}).get("d10"),
        "prob_recent": stats_recent.get(skey, {}).get("d10"),
        "ratio": stats_all.get(skey, {}).get("ratio"),
        "flags": flags,
        "stats": {"all": stats_all, "recent": stats_recent},
        # ダウンロード開始年ではなく、200日線や先行20営業日が揃って
        # 実際に検証に使えた最初の日を書く
        "period": {"all": f"{px.index[ok][0]:%Y}年〜{last:%Y}年",
                   "recent": f"2016年〜{last:%Y}年"},
        "history": hist,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"OK {OUT.name}: {last:%Y-%m-%d} 該当{cur_score}/{len(FLAGS)}個 → {sjp} "
          f"(実測{data['prob']}% / 全体の{data['ratio']}倍)")
    for fl in flags:
        print(f"  [{'●' if fl['on'] else '○'}] {fl['label']:24s} {fl['value']:>9s} "
              f"/ {fl['threshold']:>8s} / {fl['distance']}")


if __name__ == "__main__":
    main()
