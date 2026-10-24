# -*- coding: utf-8 -*-
"""株式分割の答え合わせ → docs/split_stats.json（＋計算済みの窓をためる docs/splits_hist/_returns.json）

■ 問い
  「分割を発表した銘柄は、その後どう動いたか」「分割の日（権利落ち日）の前後はどうだったか」。
  上がる・下がるを言うためのものではない。結果は結果のまま出す（差が無ければ「関係がなかった」）。

■ 2つの測り方（どちらも同じ日の TOPIX＝1306.T との差＝超過リターン）
  A. 発表のあと（TDnet で発表日が分かる分だけ・2026-09 から蓄積）
     起点＝発表の翌営業日の始値。1・5・20・60営業日目の終値まで（起点の日を1日目と数える）。
  B. 権利落ち日の前後（JPX の月次一覧・2016年〜）
     前20＝権利落ち日の21営業日前の終値 → 権利付き最終日（権利落ち日の前営業日）の終値
     後20＝権利付き最終日の終値 → 権利落ち日から数えて20営業日目の終値
     ※ 効力発生日は基準日の翌日（休みの日のことも多い）。株価が分割後の値段で付き始めるのは
       権利落ち日なので、値動きは権利落ち日を境に測る。

■ 株価は分割調整済みで計算する
  yfinance を auto_adjust=False で取り、「Adj Close」（分割と配当を調整した終値）で騰落を出す。
  始値は Open × (Adj Close ÷ Close) で同じ調整をかける。1306.T も同じ扱い（verify.py と同じ比較対象）。
  分割の日は見かけの株価が 1/N になるが、調整済みの値ではつながる。
  ただし yfinance が分割を取りこぼしている銘柄は、権利落ち日に株価が 1/N に落ちたままになる。
  権利落ち日の前後で終値の比が 1 より 1/N に近いもの、窓の中に前日比が 1/N か N 倍に近い日があるもの
  （分割の調整日がずれている）は「株価が分割調整されていない」として除く（件数を出す）。

■ 区分と件数の作法
  分割比率（2倍未満・2倍・3〜4倍・5倍以上）／分割前の株価帯（1万円以上・5千〜1万・5千未満）／市場の系統。
  30件未満は参考値、10件未満は数字を出さない。
  期間は 2016〜2022年（前半）と 2023年〜（後半＝JPX が投資単位の引下げを要請した 2022-10 の後）に分けて並べる。
  境目は結果を見る前に決めた（年の区切りで固定）。

使い方:
  python -X utf8 split_score.py          # 窓がそろっていない分だけ株価を取り直す
  python -X utf8 split_score.py --all    # 全件を計算し直す
"""
import json
import math
import statistics
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import split_fetch as SF                           # noqa: E402

JST = timezone(timedelta(hours=9))
HERE = Path(__file__).parent
DOCS = HERE / "docs"
CACHE = DOCS / "splits_hist" / "_returns.json"
OUT = DOCS / "split_stats.json"
BENCH = "1306.T"
START = "2015-10-01"
CHUNK = 40
MIN_SHOW, MIN_FIRM = 10, 30
HALF = "2023-01-01"            # 前半／後半の境目
A_WIN = (1, 5, 20, 60)
SKIP_SECTIONS = {"不動産投信(REIT)", "ETF", "ETN(JDR)", "ETF(JDR)", "<外国株>"}


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def download(tickers, start):
    import yfinance as yf
    out = {}
    tickers = sorted(set(tickers))
    for i in range(0, len(tickers), CHUNK):
        part = tickers[i:i + CHUNK]
        for k in range(3):
            try:
                d = yf.download(part, start=start, auto_adjust=False, actions=True, progress=False,
                                threads=True, group_by="ticker")
                break
            except Exception as e:
                log(f"  再試行{k + 1}: {e}")
                time.sleep(5)
        else:
            continue
        for t in part:
            try:
                x = d[t] if isinstance(d.columns, pd.MultiIndex) else d
            except KeyError:
                continue
            x = x.dropna(subset=["Close"])
            x = x[x["Close"] > 0]
            if len(x):
                x.index = pd.to_datetime(x.index).tz_localize(None).normalize()
                out[t] = x
        log(f"  株価 {min(i + CHUNK, len(tickers))}/{len(tickers)}")
        time.sleep(1)
    return out


def clean_bench(x):
    """1306.T の壊れた足を落とす（verify.py と同じ：前後11本の中央値から30%超離れた終値）。
    2026-03-30・31 は終値が 37円台（実際は380円前後）で配信されていた"""
    med = x["Close"].rolling(11, center=True, min_periods=3).median()
    bad = (x["Close"] / med - 1).abs() > 0.30
    return x[~bad], [str(d.date()) for d in x.index[bad]]


def clean_stock(x):
    """個別株の「1本だけ飛んで戻る」足を落とす（前日比も翌日比も±40%超で向きが逆）。
    分割の権利落ちは調整済みの値で見るので、ここで落ちるのは配信の誤りだけ"""
    c = x["Adj Close"]
    a = c / c.shift(1) - 1
    b = c.shift(-1) / c - 1
    bad = (a.abs() > 0.4) & (b.abs() > 0.4) & ((a > 0) != (b > 0))
    return x[~bad.fillna(False)]


def adj_open(x):
    return x["Open"] * (x["Adj Close"] / x["Close"])


def raw_close(x, i):
    """i 日目の実際の終値（yfinance の Close は分割調整済みなので、その後の分割の倍率を掛けて戻す）"""
    sp = x["Stock Splits"]
    later = sp[(sp.index > x.index[i]) & (sp > 0)]
    f = 1.0
    for v in later.values:
        f *= float(v)
    return float(x["Close"].iloc[i]) * f


def pos_after(idx, d):
    """d より後の最初の取引日の位置（d 当日は含まない）"""
    p = idx.searchsorted(pd.Timestamp(d), side="right")
    return int(p) if p < len(idx) else None


def pos_on_or_after(idx, d):
    p = idx.searchsorted(pd.Timestamp(d), side="left")
    return int(p) if p < len(idx) else None


def ret(a, b):
    return None if (a is None or b is None or not a or math.isnan(a) or math.isnan(b)) else b / a - 1


def calc(e, x, bx):
    """1件ぶん。窓が来ていないものは None。百分率で返す"""
    r = {}
    idx = x.index
    ac, ao = x["Adj Close"], adj_open(x)
    bac, bao = bx["Adj Close"], adj_open(bx)

    def bench(d0, d1, use_open):
        if d0 not in bac.index or d1 not in bac.index:
            return None
        return ret(float((bao if use_open else bac)[d0]), float(bac[d1]))

    # A. 発表のあと
    if e.get("ann"):
        p0 = pos_after(idx, e["ann"])
        if p0 is not None:
            r["a_d0"] = idx[p0].strftime("%Y-%m-%d")
            r["pre_price"] = round(raw_close(x, p0 - 1), 1) if p0 >= 1 else None
            for n in A_WIN:
                p1 = p0 + n - 1
                if p1 < len(idx):
                    s = ret(float(ao.iloc[p0]), float(ac.iloc[p1]))
                    b = bench(idx[p0], idx[p1], True)
                    if s is not None and b is not None:
                        r[f"a{n}"] = round(s * 100, 2)
                        r[f"a{n}x"] = round((s - b) * 100, 2)
    # B. 権利落ち日の前後
    ex = e.get("ex_jpx") or e.get("ex")
    if ex and ex <= idx[-1].strftime("%Y-%m-%d"):
        pe = pos_on_or_after(idx, ex)
        if pe is not None and pe >= 1 and idx[pe].strftime("%Y-%m-%d") == ex:
            # 分割調整の確認: 権利落ち日の終値÷前日の終値（yfinance の Close は分割調整済みのはず）
            n = e.get("ratio")
            jump = float(x["Close"].iloc[pe]) / float(x["Close"].iloc[pe - 1])
            if n and n >= 1.5 and abs(math.log(jump) - math.log(1 / n)) < abs(math.log(jump)):
                r["unadjusted"] = True
                return r
            # 調整の日がずれている（yfinance の分割の日付が権利落ち日と違う）と、窓の途中に 1/N か N 倍の段差が残る。
            # 調整済みの終値の前日比が 1/N か N に15%以内で近い日が窓の中にあれば測らない（2025-10-30 の 7176 で −99.7% が出た）
            if n and n >= 1.5:
                seg = ac.iloc[max(0, pe - 22):min(len(ac), pe + 21)]
                jr = (seg / seg.shift(1)).dropna()
                if any(abs(math.log(v) - math.log(t)) < 0.15 for v in jr.values if v > 0 for t in (1 / n, n)):
                    r["unadjusted"] = True
                    return r
            sp = x["Stock Splits"]
            r["yf_split"] = bool(((sp.index >= idx[max(0, pe - 3)]) & (sp.index <= idx[min(len(idx) - 1, pe + 3)])
                                  & (sp > 0)).any())
            r["ex_price"] = round(raw_close(x, pe - 1), 1)
            if pe >= 21:
                s = ret(float(ac.iloc[pe - 21]), float(ac.iloc[pe - 1]))
                b = bench(idx[pe - 21], idx[pe - 1], False)
                if s is not None and b is not None:
                    r["pre20"], r["pre20x"] = round(s * 100, 2), round((s - b) * 100, 2)
            if pe + 19 < len(idx):
                s = ret(float(ac.iloc[pe - 1]), float(ac.iloc[pe + 19]))
                b = bench(idx[pe - 1], idx[pe + 19], False)
                if s is not None and b is not None:
                    r["post20"], r["post20x"] = round(s * 100, 2), round((s - b) * 100, 2)
        elif pe is not None:
            r["ex_missing"] = True          # 権利落ち日に株価が無い（売買停止・データ欠け）
    return r


def done(e, r):
    """もう計算し直さなくてよいか（窓がすべてそろった・または測れないと分かった）"""
    if r.get("unadjusted") or r.get("ex_missing"):
        return True
    if r.get("no_price"):
        # 上場廃止・コード変更で株価が無いもの。日がたったものは毎日取りに行かない（新しいものは配信待ちのことがある）
        d = e.get("ex_jpx") or e.get("ex") or e.get("ann") or ""
        return d < (datetime.now(JST).date() - timedelta(days=30)).isoformat()
    need = []
    if e.get("ann"):
        need.append("a60")
    if e.get("ex_jpx") or e.get("ex"):
        need.append("post20")
    return all(k in r for k in need)


# ─────────────────────────── 集計
def ratio_band(n):
    if not n:
        return None
    if n < 2:
        return "2倍未満（1.1倍・1.5倍など）"
    if n == 2:
        return "2倍"
    if n < 5:
        return "3〜4倍"
    return "5倍以上"


def price_band(p):
    if p is None:
        return None
    return "1万円以上" if p >= 10000 else "5千〜1万円" if p >= 5000 else "5千円未満"


def summarize(xs):
    v = [x for x in xs if x is not None]
    n = len(v)
    if n < MIN_SHOW:
        return {"n": n}
    out = {"n": n, "mean": round(sum(v) / n, 2), "median": round(statistics.median(v), 2),
           "pos": round(100 * sum(1 for x in v if x > 0) / n, 1), "sankou": n < MIN_FIRM}
    try:
        # 平均が0（TOPIXと同じ）と違うか。区分を何通りも見ているので、p<0.05 が1つ2つ出るのは偶然でも起こる
        from scipy import stats
        out["p"] = round(float(stats.ttest_1samp(v, 0.0).pvalue), 4)
    except Exception:
        pass
    return out


def table(rows, key, labels, metric):
    out = []
    for lab in labels:
        sub = [r for r in rows if r.get(key) == lab]
        out.append({"label": lab, **summarize([r.get(metric) for r in sub])})
    out.append({"label": "全体", **summarize([r.get(metric) for r in rows])})
    return out


def main():
    full = "--all" in sys.argv
    ev = [e for e in SF.build_events(SF.load_hist())
          if e["status"] != "中止" and e.get("section") not in SKIP_SECTIONS]
    try:
        cache = {} if full else json.loads(CACHE.read_text(encoding="utf-8"))
    except Exception:
        cache = {}
    key = lambda e: f"{e['code']}|{e.get('record') or e.get('ann')}"   # noqa: E731
    todo = [e for e in ev if not (key(e) in cache and done(e, cache[key(e)]))]
    # まだ権利落ち日も発表の翌営業日も来ていないものは株価を取りに行かない
    today = datetime.now(JST).date().isoformat()
    todo = [e for e in todo if (e.get("ann") and e["ann"] < today) or ((e.get("ex_jpx") or e.get("ex") or "9") <= today)]
    log(f"分割 {len(ev)}件（計算済み {len(ev) - len(todo)}・今回 {len(todo)}）")
    if todo:
        lo = min((e.get("ann") or e.get("ex_jpx") or e.get("ex")) for e in todo)
        start = min(START, (date.fromisoformat(lo) - timedelta(days=60)).isoformat()) if full else \
            max(START, (date.fromisoformat(lo) - timedelta(days=60)).isoformat())
        px = download([f"{e['code']}.T" for e in todo] + [BENCH], start)
        bx = px.get(BENCH)
        if bx is None:
            log("::error::1306.T が取れない")
            return 1
        bx, dropped = clean_bench(bx)
        if dropped:
            log(f"  1306.T の壊れた足を除いた: {dropped}")
        miss = 0
        for e in todo:
            x = px.get(f"{e['code']}.T")
            if x is None or len(x) < 2:
                cache[key(e)] = {"no_price": True}
                miss += 1
                continue
            cache[key(e)] = calc(e, clean_stock(x), bx)
        log(f"  株価が取れない銘柄 {miss}件（上場廃止・コード変更など）")
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(cache, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    rows = []
    for e in ev:
        r = cache.get(key(e)) or {}
        n = e.get("ratio")
        rows.append({**{k: e.get(k) for k in ("code", "name", "ann", "record", "ex", "ex_jpx", "ratio", "group")},
                     **r, "rb": ratio_band(n),
                     "pb": price_band(r.get("ex_price") or r.get("pre_price")),
                     "half": "前半" if (e.get("ex_jpx") or e.get("ex") or e.get("ann") or "") < HALF else "後半"})
    jpx = [r for r in rows if (r.get("ex_jpx") or r.get("ex")) and not r.get("unadjusted")
           and not r.get("no_price") and not r.get("ex_missing")]
    ann = [r for r in rows if r.get("ann") and "a1x" in r]
    RB = ["2倍未満（1.1倍・1.5倍など）", "2倍", "3〜4倍", "5倍以上"]
    PB = ["1万円以上", "5千〜1万円", "5千円未満"]
    MG = ["プライム（旧一部）", "スタンダード（旧二部・JASDAQスタンダード）", "グロース（旧マザーズ・JASDAQグロース）"]
    blocks = {}
    for half in ("前半", "後半"):
        sub = [r for r in jpx if r["half"] == half]
        blocks[half] = {
            "n": len(sub),
            "post20x": {"ratio": table(sub, "rb", RB, "post20x"), "price": table(sub, "pb", PB, "post20x"),
                        "market": table(sub, "group", MG, "post20x")},
            "pre20x": {"ratio": table(sub, "rb", RB, "pre20x"), "price": table(sub, "pb", PB, "pre20x"),
                       "market": table(sub, "group", MG, "pre20x")},
        }
    a_tbl = {f"a{n}x": table(ann, "rb", RB, f"a{n}x") for n in A_WIN}
    counts = {
        "events": len(ev),
        "with_ex": sum(1 for r in rows if r.get("ex_jpx") or r.get("ex")),
        "measured_ex": len([r for r in jpx if "post20x" in r]),
        "unadjusted": sum(1 for r in rows if r.get("unadjusted")),
        "no_price": sum(1 for r in rows if r.get("no_price")),
        "ex_missing": sum(1 for r in rows if r.get("ex_missing")),
        "yf_split_found": sum(1 for r in jpx if r.get("yf_split")),
        "yf_split_checked": sum(1 for r in jpx if "yf_split" in r),
        "with_ann": sum(1 for r in rows if r.get("ann")),
        "measured_ann": len(ann),
    }
    out = {
        "updated": datetime.now(JST).isoformat(timespec="seconds"),
        "bench": "1306.T（TOPIX連動ETF・分配金調整済み）",
        "price_source": "Yahoo Finance 日足（yfinance auto_adjust=False の Adj Close＝分割・配当調整済み）",
        "half_border": HALF, "min_show": MIN_SHOW, "min_firm": MIN_FIRM,
        "windows": {"a": "発表の翌営業日の始値 → n営業日目の終値（起点の日を1日目と数える）",
                    "pre20": "権利落ち日の21営業日前の終値 → 権利付き最終日の終値",
                    "post20": "権利付き最終日の終値 → 権利落ち日から数えて20営業日目の終値"},
        "counts": counts,
        "ex": blocks,
        "ann": {"n": len(ann), "tables": a_tbl,
                "since": min((r["ann"] for r in rows if r.get("ann")), default=None)},
        # 銘柄ページ・split.html の明細（発表日のある分と、権利落ち日の前後が測れた分）
        "events": [{k: r[k] for k in ("code", "name", "ann", "record", "ex", "ex_jpx", "ratio", "a_d0", "a1x", "a5x", "a20x",
                                      "a60x", "a1", "a5", "a20", "a60", "pre20x", "post20x", "pre20", "post20",
                                      "ex_price", "pre_price", "unadjusted")
                    if r.get(k) is not None} for r in rows],
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    log(f"→ split_stats.json {OUT.stat().st_size / 1024:.0f}KB  " + json.dumps(counts, ensure_ascii=False))
    for half, b in blocks.items():
        a = b["post20x"]["ratio"][-1]
        log(f"  {half}: 権利落ち後20日 TOPIX比 n={a.get('n')} 平均={a.get('mean')} 中央={a.get('median')} 勝ち={a.get('pos')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
