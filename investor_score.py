# -*- coding: utf-8 -*-
"""誰が買っているか — 需給の答え合わせ → docs/investor_score.json

■ 3つ測る
  1. 累積: 起点からの net の積み上げ＝「誰が買い続け、誰が売り続けてきたか」
  2. 物差し: 今週の買い越し／売り越しが、この期間で何番目か
  3. 答え合わせ: 「Xが買い越した週の**その後**、日経はどう動いたか」

■ 起点は「読者が知り得た時点」
  JPXの公表は木曜で、対象は前週。公表前の数字で測ると先読みになる。
  週の終了日より後の最初の木曜を公表日とし、**その翌営業日の始値**を起点にする。
  （空売りの答え合わせと同じ原則）

■ 必ず全週平均と比べる
  日経自体が上昇しているので、「買い越した週の翌週は平均+0.2%」だけ見ると効いて見える。
  同じ窓の全週平均を基準に置き、その差で判断する。

■ 市場区分が2022年4月に変わっている
  東証1部とプライムは構成銘柄が違う。累積は黙って1本に繋がず、変更日を出力に残す。
"""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

HERE = Path(__file__).parent
SRC = HERE / "docs" / "investor_hist.json"
OUT = HERE / "docs" / "investor_score.json"
W1, W4 = 5, 20          # 営業日。およそ翌週・翌4週


def prices():
    import yfinance as yf
    for i in range(3):
        try:
            d = yf.download("^N225", start="2015-06-01", progress=False,
                            auto_adjust=False, threads=False)
            if len(d) > 1000:
                break
        except Exception as e:
            print(f"  価格再試行{i+1}: {e}", file=sys.stderr)
    if isinstance(d.columns, pd.MultiIndex):
        d.columns = d.columns.get_level_values(0)
    d = d[["Open", "Close"]].dropna()
    d.index = pd.to_datetime(d.index).tz_localize(None).normalize()
    return d


def publish_start(end_iso, idx):
    """週の終了日 → 公表日（その後の最初の木曜）→ 起点（公表の翌営業日）の位置"""
    e = pd.Timestamp(end_iso)
    thu = e + pd.Timedelta(days=(3 - e.weekday()) % 7 or 7)   # 終了日より後の最初の木曜
    after = [i for i, t in enumerate(idx) if t > thu]
    return after[0] if after else None


def ret(px, i, n):
    """起点の始値 → n営業日後の終値"""
    o = px["Open"].values
    c = px["Close"].values
    if i is None or i + n >= len(c):
        return None
    if not o[i] or not c[i + n]:
        return None
    return c[i + n] / o[i] - 1


def summarize(v, base):
    v = np.array([x for x in v if x is not None and np.isfinite(x)], float)
    if len(v) < 5:
        return {"n": int(len(v))}
    up = int((v > 0).sum())
    o = {"n": int(len(v)), "mean_pct": round(float(v.mean()) * 100, 3),
         "up_rate": round(100.0 * up / len(v), 1),
         "p_up": round(float(stats.binomtest(up, len(v), 0.5).pvalue), 4),
         "p_mean": round(float(stats.ttest_1samp(v, 0.0).pvalue), 4)}
    b = np.array([x for x in base if x is not None and np.isfinite(x)], float)
    if len(b) > 5:
        o["base_mean_pct"] = round(float(b.mean()) * 100, 3)
        o["base_up_rate"] = round(100.0 * float((b > 0).mean()), 1)
        o["diff_mean_pt"] = round(o["mean_pct"] - o["base_mean_pct"], 3)
        o["diff_up_pt"] = round(o["up_rate"] - o["base_up_rate"], 1)
        o["p_vs_base"] = round(float(stats.ttest_ind(v, b, equal_var=False).pvalue), 4)
    o["verdict"] = ("件数不足" if o["n"] < 20 else
                    ("効いている" if o.get("p_vs_base", 1) < 0.05 else "効いていない"))
    return o


def main():
    d = json.loads(SRC.read_text(encoding="utf-8"))
    weeks = d["weeks"]
    cats = [c["key"] for c in d["categories"]]
    labels = {c["key"]: c["label"] for c in d["categories"]}
    px = prices()
    idx = list(px.index)
    print(f"週数 {len(weeks)}（{weeks[0]['s']} 〜 {weeks[-1]['e']}）／^N225 {len(idx)}営業日")

    # 起点を先に全週ぶん求める
    for w in weeks:
        w["_i"] = publish_start(w["e"], idx)
        w["_r1"] = ret(px, w["_i"], W1)
        w["_r4"] = ret(px, w["_i"], W4)
    base1 = [w["_r1"] for w in weeks]
    base4 = [w["_r4"] for w in weeks]

    out = {
        "updated": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": d["source"],
        "price_source": "^N225 日足（Yahoo Finance）",
        "unit": "億円",
        "coverage": d["coverage"],
        "market_change": d["market_change"],
        "market_note": d["market_note"],
        "windows": {"w1": f"公表の翌営業日の始値 → {W1}営業日後の終値（およそ翌週）",
                    "w4": f"公表の翌営業日の始値 → {W4}営業日後の終値（およそ翌4週）"},
        "caveats": [
            "JPXの公表は木曜で、対象は前週です。この検証は公表の翌営業日を起点にしています",
            "日経平均そのものが上昇している期間なので、必ず同じ期間の全週平均と比べています",
            "6主体×2つの窓＝12通りを同時に見ています。p<0.05が1つ出るのは偶然でも起こります",
            "2022年4月4日に対象が東証1部からプライムに変わりました。構成銘柄が違います",
            "過去にこう動いたという記録であって、次にどう動くかを示すものではありません",
        ],
        "latest": {"week": {"s": weeks[-1]["s"], "e": weeks[-1]["e"]}, "net": weeks[-1]["net"]},
        "cumulative": {}, "rank": {}, "score": {}, "corr": {},
    }

    # 1) 累積（市場区分ごとに分けて積む。黙って1本に繋がない）
    for k in cats:
        series = []
        acc = {"tse1": 0, "prime": 0}
        for w in weeks:
            v = w["net"].get(k)
            if v is None:
                continue
            acc[w["m"]] += v
            series.append([w["e"], acc[w["m"]], w["m"]])
        out["cumulative"][k] = {
            "label": labels[k],
            "tse1_total": acc["tse1"], "prime_total": acc["prime"],
            "series": series[::4],        # 4週ごとに間引く（画面用。全点は要らない）
        }

    # 2) 今週は何番目か
    for k in cats:
        vals = [w["net"][k] for w in weeks if w["net"].get(k) is not None]
        cur = weeks[-1]["net"].get(k)
        if cur is None or not vals:
            continue
        srt = sorted(vals, reverse=True)
        out["rank"][k] = {
            "label": labels[k], "value": cur,
            "rank_buy": srt.index(cur) + 1, "of": len(vals),
            "pctile": round(100.0 * sum(1 for x in vals if x <= cur) / len(vals), 1),
            "max": max(vals), "min": min(vals),
            "abs_rank": sorted(vals, key=lambda x: -abs(x)).index(cur) + 1,
        }

    # 3) 答え合わせ: 買い越した週／売り越した週のその後
    for k in cats:
        buy = [w for w in weeks if (w["net"].get(k) or 0) > 0]
        sell = [w for w in weeks if (w["net"].get(k) or 0) < 0]
        out["score"][k] = {
            "label": labels[k],
            "buy": {"w1": summarize([w["_r1"] for w in buy], base1),
                    "w4": summarize([w["_r4"] for w in buy], base4)},
            "sell": {"w1": summarize([w["_r1"] for w in sell], base1),
                     "w4": summarize([w["_r4"] for w in sell], base4)},
        }

    # 4) 主体間の相関（海外が買うとき誰が売っているか）
    mat = {k: np.array([w["net"].get(k, np.nan) for w in weeks], float) for k in cats}
    for a in cats:
        row = {}
        for b in cats:
            if a == b:
                continue
            m = np.isfinite(mat[a]) & np.isfinite(mat[b])
            if m.sum() > 30:
                row[b] = round(float(np.corrcoef(mat[a][m], mat[b][m])[0, 1]), 3)
        out["corr"][a] = row

    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    # ---- 第1段の報告用に画面へ出す ----
    print("\n■ 累積（起点からの積み上げ・億円）")
    for k in cats:
        c = out["cumulative"][k]
        print(f"  {c['label'][:14]:16s} 東証1部期 {c['tse1_total']:+9,}／プライム期 {c['prime_total']:+9,}")
    print(f"\n■ 今週（{weeks[-1]['s']}〜{weeks[-1]['e']}）は{len(weeks)}週で何番目か")
    for k in cats:
        r = out["rank"].get(k)
        if r:
            print(f"  {r['label'][:14]:16s} {r['value']:+7,}億円  買い越し{r['rank_buy']:3d}位/{r['of']}  "
                  f"下から{r['pctile']:5.1f}%  絶対値{r['abs_rank']:3d}位")
    print("\n■ 答え合わせ（買い越した週／売り越した週のその後の日経）")
    hdr = f"  {'主体':16s} {'向き':4s} {'窓':3s} {'n':>4s} {'平均%':>7s} {'上昇%':>6s} {'全週平均':>8s} {'差pt':>7s} {'p':>8s}  判定"
    print(hdr)
    for k in cats:
        s = out["score"][k]
        for side in ("buy", "sell"):
            for w in ("w1", "w4"):
                x = s[side][w]
                if not x.get("n"):
                    continue
                print(f"  {s['label'][:14]:16s} {'買越' if side=='buy' else '売越':4s} {w:3s} {x['n']:4d} "
                      f"{x.get('mean_pct',0):7.3f} {x.get('up_rate',0):6.1f} {x.get('base_mean_pct',0):8.3f} "
                      f"{x.get('diff_mean_pt',0):7.3f} {x.get('p_vs_base',1):8.4f}  {x['verdict']}")
    print("\n■ 主体間の相関（海外投資家から見て）")
    for b, v in sorted(out["corr"]["foreigners"].items(), key=lambda x: x[1]):
        print(f"  {labels[b][:16]:18s} {v:+.3f}")
    print(f"\n→ {OUT.name} ({OUT.stat().st_size/1024:.1f}KB)")


if __name__ == "__main__":
    main()
