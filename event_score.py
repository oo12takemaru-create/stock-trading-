# -*- coding: utf-8 -*-
"""相場の暦＝イベントの答え合わせ → docs/event_score.json

■ 問い
  「次はいつ」は各社が出している。ここで出すのは「**その日、日経は過去どう動いたか**」。

■ 測り方
  反応日 t を決めて（日銀＝当日／FOMC・米CPI・雇用統計＝翌営業日）、4つの窓を測る。
    d0 = 前日終値 → 当日終値
    d1 = 当日終値 → 翌日終値
    m5 = 6営業日前の終値 → 前日終値（イベント前の5営業日）
    p5 = 当日終値 → 5営業日後の終値
  副次に「値幅」＝(高値−安値)÷前日終値。「上がるか」より「動くか」のほうが言いやすい。

■ 必ず非イベント日と比べる
  日経自体が上昇している期間なので、イベント日の平均だけ見ると全部プラスに見える。
  「10年の物差し」で踏んだ罠と同じ。**同じ期間の非イベント日**を基準に置く。

■ 日付は event_dates.py が作った表（出所つき）を読む。ここでは推測しない。
  SQ・権利確定日だけは機械算出（ルールを下に明記）。
"""
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

HERE = Path(__file__).parent
DATES = HERE / "event_dates.json"
OUT = HERE / "docs" / "event_score.json"

START = "2010-01-01"
YEAR_FROM = 2013          # 集計対象の開始年（日付の出所が揃う年）
RECENT_YEARS = 3


# ---------------------------------------------------------------- 価格
def prices():
    import yfinance as yf
    for attempt in range(3):
        try:
            d = yf.download("^N225", start=START, progress=False,
                            auto_adjust=False, threads=False)
            if len(d) > 1000:
                break
        except Exception as e:
            print(f"  価格再試行{attempt+1}: {e}", file=sys.stderr)
    if isinstance(d.columns, pd.MultiIndex):
        d.columns = d.columns.get_level_values(0)
    d = d[["Open", "High", "Low", "Close"]].dropna()
    d.index = pd.to_datetime(d.index).tz_localize(None).normalize()
    return d


# ---------------------------------------------------------------- 機械算出の日付
def major_sq(days, months=(3, 6, 9, 12)):
    """メジャーSQ＝3・6・9・12月の第2金曜。休場ならその前の営業日（SQ算出日は当日の寄り）
    months を変えるとマイナーSQ（それ以外の月の第2金曜）にも使う"""
    out = []
    ds = set(days)
    for y in range(YEAR_FROM, days[-1].year + 1):
        for m in months:
            first = date(y, m, 1)
            fri = first + timedelta(days=(4 - first.weekday()) % 7 + 7)   # 第2金曜
            t = pd.Timestamp(fri)
            # まだ株価の無い未来の第2金曜は数えない。下の「休場なら前の営業日」に落ちると
            # 未来のSQが今日の日付で入ってしまう（2026-10-08: 10/9 のSQが 10/8 として入った）
            if t > days[-1]:
                continue
            while t not in ds and t > pd.Timestamp(first):
                t -= pd.Timedelta(days=1)
            if t in ds:
                out.append(t.strftime("%Y-%m-%d"))
    return out


def kenri(days):
    """権利付き最終日。3月末・9月末の最終営業日から受渡日数ぶん戻す。
    2019-07-16 以降は T+2 = 最終営業日の2営業日前、それ以前は T+3 = 3営業日前。
    例: 2024年3月末は最終営業日3/29(金)、権利付き最終日3/27(水)、権利落ち日3/28(木)"""
    out = []
    ds = list(days)
    for y in range(YEAR_FROM, days[-1].year + 1):
        for m in (3, 9):
            last = [t for t in ds if t.year == y and t.month == m]
            if not last:
                continue
            end = last[-1]
            # まだ終わっていない月は使わない（今日が月内最後の取引日に見えてしまう）
            nxt = [t for t in ds if t > end]
            if not nxt or (nxt[0].year, nxt[0].month) == (end.year, end.month):
                continue
            back = 2 if end >= pd.Timestamp("2019-07-16") else 3
            i = ds.index(end) - back
            if i >= 0:
                out.append(ds[i].strftime("%Y-%m-%d"))
    return out


# ---------------------------------------------------------------- 集計
def windows(px, idx, i):
    """反応日の位置 i から4つの窓と値幅を返す"""
    c = px["Close"].values
    o = {}
    if i >= 1:
        o["d0"] = c[i] / c[i - 1] - 1
        o["range"] = (px["High"].values[i] - px["Low"].values[i]) / c[i - 1]
    if i + 1 < len(c):
        o["d1"] = c[i + 1] / c[i] - 1
    if i >= 6:
        o["m5"] = c[i - 1] / c[i - 6] - 1
    if i + 5 < len(c):
        o["p5"] = c[i + 5] / c[i] - 1
    return o


def summarize(vals, base=None):
    """n・平均・中央値・上昇割合・最大最小・p値。baseがあれば非イベント日との差も"""
    v = np.array([x for x in vals if x is not None and np.isfinite(x)], dtype=float)
    if len(v) < 3:
        return {"n": int(len(v))}
    up = int((v > 0).sum())
    out = {
        "n": int(len(v)),
        "mean_pct": round(float(v.mean()) * 100, 3),
        "median_pct": round(float(np.median(v)) * 100, 3),
        "up_rate": round(100.0 * up / len(v), 1),
        "max_pct": round(float(v.max()) * 100, 2),
        "min_pct": round(float(v.min()) * 100, 2),
        # 平均が0と違うか
        "p_mean": round(float(stats.ttest_1samp(v, 0.0).pvalue), 4),
        # 上昇割合が50%と違うか
        "p_up": round(float(stats.binomtest(up, len(v), 0.5).pvalue), 4),
    }
    if base is not None and len(base) > 3:
        b = np.array(base, dtype=float)
        out["base_mean_pct"] = round(float(b.mean()) * 100, 3)
        out["base_up_rate"] = round(100.0 * float((b > 0).mean()), 1)
        out["diff_mean_pt"] = round(out["mean_pct"] - out["base_mean_pct"], 3)
        out["diff_up_pt"] = round(out["up_rate"] - out["base_up_rate"], 1)
        # イベント日 vs 非イベント日（等分散を仮定しないWelch）
        out["p_vs_base"] = round(float(stats.ttest_ind(v, b, equal_var=False).pvalue), 4)
    return out


def verdict(s):
    """効いている／効いていない／件数不足 の3分類。
    非イベント日との差で見る（イベント日だけの平均で見ると、相場の上昇を拾ってしまう）"""
    if s.get("n", 0) < 20:
        return "件数不足"
    p = s.get("p_vs_base")
    if p is None:
        p = s.get("p_mean")
    return "効いている" if (p is not None and p < 0.05) else "効いていない"



# ---------------------------------------------------------------- 今後の日程（日本時間）
# ★日程の正は event_dates.json（event_dates.py が作る）1か所★（積上⑥ Fable判断 2026-10-08）
#   サイトの events.js（gen_events.py が作る）と AI朝刊（ai_analysis.py）は、ここで出す
#   docs/event_score.json の schedule を読む。手書きの日程表を増やさない。
# 今年の1月1日から（サイトの「年内の全日程」は過ぎた回も灰色で並べる）
SCHEDULE_AHEAD_DAYS = 400       # 1年＋α。サイトの日付ページは12か月先まで
# 次期TOPIX の段階的ウエイト低減（JPX総研「TOPIX等の見直しについて」2026年5月の移行係数表）。
# 実施日は月末最終営業日の大引け
TOPIX_NEXT = [(2026, 10, "移行100%（初回定期入替）"), (2027, 1, "移行係数87.5%"), (2027, 4, "移行係数75.0%"),
              (2027, 7, "移行係数62.5%"), (2027, 10, "移行係数50.0%・再評価"), (2028, 1, "移行係数37.5%"),
              (2028, 4, "移行係数25.0%"), (2028, 7, "移行係数12.5%"), (2028, 10, "移行係数0%・移行完了")]
# 日程の先行きの見張り（healthcheck_content.py）で見る種別。SQ・権利日は規則で計算するので見ない
HORIZON_KINDS = ("boj", "fomc", "cpi", "payroll")


def _us_dst(d):
    """米国の夏時間（3月第2日曜〜11月第1日曜）"""
    def nth_sunday(y, m, n):
        f = date(y, m, 1)
        return f + timedelta(days=(6 - f.weekday()) % 7 + 7 * (n - 1))
    return nth_sunday(d.year, 3, 2) <= d < nth_sunday(d.year, 11, 1)


def _prev_bd(d):
    import jp_bizday
    while not jp_bizday.is_bizday(d):
        d -= timedelta(days=1)
    return d


def _add_bd(d, n):
    import jp_bizday
    step = 1 if n > 0 else -1
    for _ in range(abs(n)):
        d += timedelta(days=step)
        while not jp_bizday.is_bizday(d):
            d += timedelta(days=step)
    return d


def build_schedule(tbl, today):
    lo, hi = date(today.year, 1, 1), today + timedelta(days=SCHEDULE_AHEAD_DAYS)
    out = []

    def add(d, k, t, hhmm, label):
        if lo <= d <= hi:
            out.append({"d": d.isoformat(), "k": k, "t": t, "time": label,
                        "iso": f"{d.isoformat()}T{hhmm}:00+09:00"})

    for x in tbl["boj"]["dates"]:
        d = date.fromisoformat(x["d"])
        st = x.get("start")
        held = (f"{int(st[5:7])}/{int(st[8:10])}-{d.day}" if st and st != x["d"] else f"{d.month}/{d.day}")
        add(d, "boj", f"日銀会合 結果発表（{held}開催）", "12:00", "昼ごろ")
    for x in tbl["fomc"]["dates"]:
        us = date.fromisoformat(x["d"])
        st = us - timedelta(days=1)
        dst = _us_dst(us)
        add(us + timedelta(days=1), "fomc", f"FOMC結果発表（{st.month}/{st.day}-{us.day}開催）",
            "03:00" if dst else "04:00", "午前3:00" if dst else "午前4:00")
    for k, nm in (("cpi", "米CPI"), ("payroll", "米雇用統計")):
        for x in tbl[k]["dates"]:
            d = date.fromisoformat(x["d"])
            ref = 12 if d.month == 1 else d.month - 1
            dst = _us_dst(d)
            add(d, k, f"{nm}（{ref}月分）", "21:30" if dst else "22:30", "21:30" if dst else "22:30")
    y, m = lo.year, lo.month
    while date(y, m, 1) <= hi:
        first = date(y, m, 1)
        sq = _prev_bd(first + timedelta(days=(4 - first.weekday()) % 7 + 7))
        major = m in (3, 6, 9, 12)
        add(sq, "sq" if major else "sq_minor", f"{'メジャー' if major else 'マイナー'}SQ（{m}月限）", "09:00", "寄付")
        if m in (3, 9):
            nxt = date(y + (m == 12), m % 12 + 1, 1)
            k = _add_bd(_prev_bd(nxt - timedelta(days=1)), -2)
            add(k, "kenri", f"権利付き最終日（{m}月末）", "15:30", "大引け")
            add(_add_bd(k, 1), "kenri_ex", f"権利落ち日（{m}月末）", "09:00", "寄付")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    for y, m, note in TOPIX_NEXT:
        nxt = date(y + (m == 12), m % 12 + 1, 1)
        add(_prev_bd(nxt - timedelta(days=1)), "topix", f"次期TOPIX 段階的ウエイト低減（{note}）"
            if "初回" not in note else f"次期TOPIX {note}", "15:30", "大引け")
    order = ["boj", "fomc", "cpi", "payroll", "sq", "sq_minor", "kenri", "kenri_ex", "topix"]
    return sorted(out, key=lambda e: (e["iso"], order.index(e["k"])))


def build_horizon(tbl):
    """種別ごとに、日程表で分かっている一番先の日付（見張り用）"""
    return {k: max(x["d"] for x in tbl[k]["dates"]) for k in HORIZON_KINDS if tbl.get(k, {}).get("dates")}


def main():
    px = prices()
    idx = list(px.index)
    pos = {t: i for i, t in enumerate(idx)}
    print(f"^N225 {len(idx)}営業日 {idx[0].date()} 〜 {idx[-1].date()}")

    tbl = json.loads(DATES.read_text(encoding="utf-8"))
    # 機械算出の2つを足す
    tbl["sq"] = {"label": "メジャーSQ", "react": "same",
                 "note": "3・6・9・12月の第2金曜（休場ならその前の営業日）。SQ値は当日の寄りで決まる",
                 "dates": [{"d": d, "source": "機械算出（第2金曜ルール）"} for d in major_sq(idx)]}
    # マイナーSQ（積上⑥・2026-10-08）。オプションだけのSQ。計算はメジャーと同じ
    tbl["sq_minor"] = {"label": "マイナーSQ", "react": "same",
                       "note": "3・6・9・12月以外の毎月第2金曜（休場ならその前の営業日）。"
                               "日経225オプションのみのSQで、先物は満期を迎えない",
                       "dates": [{"d": d, "source": "機械算出（第2金曜ルール）"}
                                 for d in major_sq(idx, (1, 2, 4, 5, 7, 8, 10, 11))]}
    # 前回のTOPIX段階的ウエイト低減（10回）。実施日はすべて四半期末の最終営業日
    # （topix.html の検証と同じ日付。あちらは個別銘柄 vs TOPIX、ここは日経平均の動き）
    topix = []
    for y, m in [(2022, 10), (2023, 1), (2023, 4), (2023, 7), (2023, 10),
                 (2024, 1), (2024, 4), (2024, 7), (2024, 10), (2025, 1)]:
        md = [t for t in idx if t.year == y and t.month == m]
        if md:
            topix.append(md[-1].strftime("%Y-%m-%d"))
    tbl["topix"] = {"label": "TOPIX段階的ウエイト低減（前回10回）", "react": "same",
                    "note": "実施日は四半期末の最終営業日。日経平均の動きを見たもので、"
                            "個別銘柄の対TOPIX検証は topix.html にある",
                    "dates": [{"d": d, "source": "JPX公表資料＋実施日は四半期末最終営業日"} for d in topix]}

    tbl["kenri"] = {"label": "権利付き最終日（3月末・9月末）", "react": "same",
                    "note": "3月末・9月末の最終営業日から受渡日数を戻して算出。"
                            "2019-07-16以降はT+2（2営業日前）、それ以前はT+3（3営業日前）。"
                            "翌営業日が権利落ち日",
                    "dates": [{"d": d, "source": "機械算出（JPXの受渡ルール）"} for d in kenri(idx)]}

    last = idx[-1]
    recent_from = last - pd.DateOffset(years=RECENT_YEARS)
    out = {
        "updated": datetime.now().astimezone().isoformat(timespec="seconds"),
        "price_source": "^N225 日足（Yahoo Finance）",
        "period": {"from": str(idx[0].date()), "to": str(last.date())},
        "year_from": YEAR_FROM,
        "windows": {"d0": "前日終値→当日終値", "d1": "当日終値→翌日終値",
                    "m5": "6営業日前→前日終値（イベント前の5営業日）",
                    "p5": "当日終値→5営業日後の終値", "range": "(高値−安値)÷前日終値"},
        "caveats": [
            "反応日は、日銀・SQ・権利付き最終日が当日、FOMC・米CPI・米雇用統計が翌営業日です",
            "日経平均そのものが上昇している期間なので、必ず同じ期間の非イベント日と比べています",
            f"イベント{len(tbl)}種×窓4本＝{len(tbl) * 4}通りを同時に見ています。p<0.05が1〜2個出るのは偶然でも起こります",
            "2020年3月のFOMC（臨時会合）のように、予定が変わった回は定例の一覧から外れています",
            "過去にこう動いたという記録であって、次にどう動くかを示すものではありません",
        ],
        "events": {},
    }

    for key, ev in tbl.items():
        ds = [x["d"] for x in ev["dates"] if x["d"][:4] >= str(YEAR_FROM)]
        react = ev.get("react", "same")
        hit, miss = [], 0
        for s in ds:
            t = pd.Timestamp(s)
            if react == "next":
                nxt = [x for x in idx if x > t]
                if not nxt:
                    miss += 1
                    continue
                t = nxt[0]
            if t not in pos:
                # 当日反応のイベントが休場（臨時休場など）なら、その回は数えない
                miss += 1
                continue
            hit.append(pos[t])
        hits = sorted(set(hit))
        hitset = set(hits)

        # 非イベント日（同じ期間のそれ以外の営業日）
        i0 = min(hits) if hits else 0
        base_idx = [i for i in range(max(i0, 6), len(idx) - 5) if i not in hitset]

        ev_out = {"label": ev["label"], "react": react, "note": ev.get("note", ""),
                  "n_dates": len(ds), "n_used": len(hits), "n_skipped": miss,
                  "source_sample": ev["dates"][0]["source"] if ev["dates"] else "",
                  "first": ds[0] if ds else None, "last": ds[-1] if ds else None,
                  "windows": {}, "recent": {}, "rows": [], "rows_all": []}

        for w in ("d0", "d1", "m5", "p5", "range"):
            vals = [windows(px, idx, i).get(w) for i in hits]
            base = [x for x in (windows(px, idx, i).get(w) for i in base_idx) if x is not None]
            s = summarize(vals, base)
            s["verdict"] = verdict(s)
            ev_out["windows"][w] = s
            # 直近3年だけ
            rh = [i for i in hits if idx[i] >= recent_from]
            rb = [i for i in base_idx if idx[i] >= recent_from]
            if rh:
                ev_out["recent"][w] = summarize(
                    [windows(px, idx, i).get(w) for i in rh],
                    [x for x in (windows(px, idx, i).get(w) for i in rb) if x is not None])

        # 明細。rows＝直近10回（koyomi.js の表示用・従来どおり）、
        # rows_all＝全回（種別ページの年別の表用・積上⑥ 2026-10-08）
        for i in hits:
            w = windows(px, idx, i)
            ev_out["rows_all"].append({
                "d": idx[i].strftime("%Y-%m-%d"),
                "d0": round(w.get("d0", float("nan")) * 100, 2) if "d0" in w else None,
                "d1": round(w.get("d1", float("nan")) * 100, 2) if "d1" in w else None,
                "range": round(w.get("range", float("nan")) * 100, 2) if "range" in w else None,
            })
        ev_out["rows"] = ev_out["rows_all"][-10:]
        out["events"][key] = ev_out

    today = datetime.now().date()
    out["schedule"] = build_schedule(json.loads(DATES.read_text(encoding="utf-8")), today)
    out["horizon"] = build_horizon(json.loads(DATES.read_text(encoding="utf-8")))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    # ---- 画面にも表で出す（第1段の報告用）----
    print()
    hdr = f"{'イベント':22s} {'窓':5s} {'n':>5s} {'平均%':>8s} {'上昇%':>7s} {'非ev平均':>9s} {'差pt':>7s} {'p(vs非ev)':>10s}  判定"
    print(hdr); print("-" * len(hdr))
    for key, e in out["events"].items():
        for w in ("m5", "d0", "d1", "p5"):
            s = e["windows"].get(w, {})
            if not s.get("n"):
                continue
            print(f"{e['label'][:20]:22s} {w:5s} {s['n']:5d} {s.get('mean_pct',0):8.3f} "
                  f"{s.get('up_rate',0):7.1f} {s.get('base_mean_pct',0):9.3f} "
                  f"{s.get('diff_mean_pt',0):7.3f} {s.get('p_vs_base',1):10.4f}  {s['verdict']}")
        r = e["windows"].get("range", {})
        if r.get("n"):
            ratio = (r["mean_pct"] / r["base_mean_pct"]) if r.get("base_mean_pct") else 0
            print(f"{'':22s} {'値幅':5s} {r['n']:5d} {r['mean_pct']:8.3f} {'':7s} "
                  f"{r.get('base_mean_pct',0):9.3f} {'':7s} {r.get('p_vs_base',1):10.4f}  "
                  f"平常の{ratio:.2f}倍  {r['verdict']}")
        print()
    print(f"→ {OUT.name} ({OUT.stat().st_size/1024:.1f}KB)")


if __name__ == "__main__":
    main()
