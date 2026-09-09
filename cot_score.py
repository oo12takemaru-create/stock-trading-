# -*- coding: utf-8 -*-
"""投機筋の答え合わせ（CFTC 10年分）→ docs/cot_score.json

■ 答えは「翌週の予測にはならない」。6商品すべて同方向率46.8〜53.1%・p>0.15。
  極端域だけ一部に差が出るが、無条件平均との差で見ると24通り中5通り。

■ 設問1: 投機筋がネットポジションを前週比で増やした/減らした週の、翌週の値動き
  CFTCは**火曜時点のデータを金曜夕（日本時間の土曜）に公表**する。
  読者が動けるのは**翌週の月曜の寄り**なので、
  「翌週月曜始値 → 同週金曜終値」で測る。火曜〜金曜の値動きは使わない（先読み禁止）。

■ 設問2: ポジションが極端（過去3年のパーセンタイルで上位/下位10%）のときの翌4週・12週

■ 「一致」の定義
  ネットを増やした（買い越し方向）→ 価格が上昇したら一致
  ネットを減らした（売り越し方向）→ 価格が下落したら一致
  ※これは「投機筋についていけば勝てる」という主張ではない。
    ただの同方向率で、有意かどうかは二項検定のp値で判断する。

■ 円だけ符号の意味が逆になる点に注意
  JPY=X は「1ドル＝何円」なので、**数値が上がる＝円安**。
  CFTCの円ネットロング増加は「円高方向に賭けた」なので、
  一致を見るには JPY=X のリターンの符号を反転させる。ここを間違えると結論が逆になる。
"""
import json
import math
import random
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import pandas as pd
import yfinance as yf

API = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"
HERE = Path(__file__).parent
OUT = HERE / "docs" / "cot_score.json"

# (key, 表示名, CFTCの市場名プレフィックス, yfinanceのティッカー, 価格の向きを反転するか)
SPECS = [
    ("jpy",    "日本円",              ["JAPANESE YEN - CHICAGO"],           "JPY=X", True),
    ("nikkei", "日経平均先物(円建て)", ["NIKKEI STOCK AVERAGE YEN DENOM"],   "^N225", False),
    # ※CFTCは2022年2月に市場名を変えている。新旧の両方を足さないと
    #   2022年2月以降しか取れず、10年の検証にならない（実際に239週しか出なかった）
    ("sp500",  "S&P500 (E-mini)",     ["E-MINI S&P 500 - CHICAGO",
                                       "E-MINI S&P 500 STOCK INDEX - CHICAGO"], "^GSPC", False),
    ("nasdaq", "ナスダック100 (mini)", ["NASDAQ MINI - CHICAGO",
                                       "NASDAQ-100 STOCK INDEX (MINI) - CHICAGO"], "^NDX", False),
    ("gold",   "金",                  ["GOLD - COMMODITY EXCHANGE"],        "GC=F",  False),
    ("wti",    "WTI原油",             ["CRUDE OIL, LIGHT SWEET-WTI - ICE",
                                       "CRUDE OIL, LIGHT SWEET - NEW YORK"], "CL=F", False),
]
YEARS = 10


def fetch_cot(prefixes):
    """10年分の週次データを古い順で返す。
    市場名が途中で変わる商品があるので、候補すべてを取って日付でまとめる"""
    merged = {}
    for p in prefixes:
        params = {
            "$where": (f"market_and_exchange_names like '{p}%' "
                       f"AND report_date_as_yyyy_mm_dd > '{2026-YEARS}-01-01'"),
            "$order": "report_date_as_yyyy_mm_dd ASC",
            "$limit": "1200",
            "$select": ("report_date_as_yyyy_mm_dd,"
                        "noncomm_positions_long_all,noncomm_positions_short_all"),
        }
        url = API + "?" + urllib.parse.urlencode(params)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "kaburadar/1.0"})
            with urllib.request.urlopen(req, timeout=60) as r:
                rows = json.load(r)
            for r_ in rows:
                merged.setdefault(r_["report_date_as_yyyy_mm_dd"][:10], r_)
        except Exception as e:
            print(f"  取得失敗 {p}: {e}", file=sys.stderr)
    return [merged[k] for k in sorted(merged)]


def boot_ci(vals, n_boot=5000, seed=42):
    """平均のブートストラップ95%信頼区間。
    件数が少ない極端域で「差がある」と言えるかを判断するために使う。
    0を含む＝差があるとは言えない（参考値扱い）。"""
    if not vals or len(vals) < 5:
        return None
    rnd = random.Random(seed)
    n = len(vals)
    means = []
    for _ in range(n_boot):
        means.append(sum(vals[rnd.randrange(n)] for _ in range(n)) / n)
    means.sort()
    lo = means[int(0.025 * n_boot)]
    hi = means[int(0.975 * n_boot)]
    return [round(lo, 3), round(hi, 3)]


def binom_p(hits, n, p=0.5):
    """両側二項検定のp値。50%と差があるかを見るだけなので正規近似は使わず厳密に計算"""
    if n == 0:
        return None
    def pmf(k):
        return math.comb(n, k) * p ** k * (1 - p) ** (n - k)
    obs = pmf(hits)
    # 観測値以下の確率を持つ全ての k を足す（両側）
    tot = sum(pmf(k) for k in range(n + 1) if pmf(k) <= obs + 1e-15)
    return min(1.0, tot)


def main():
    prices = {}
    for _, _, _, tick, _ in SPECS:
        if tick in prices:
            continue
        for attempt in range(3):
            try:
                d = yf.download(tick, start=f"{2026-YEARS-1}-06-01", end="2026-09-10",
                                progress=False, auto_adjust=False, threads=False)
                break
            except Exception as e:
                print(f"  価格再試行 {tick} {e}", file=sys.stderr)
                time.sleep(5)
        o, c = d["Open"].dropna(), d["Close"].dropna()
        if isinstance(o, pd.DataFrame):
            o, c = o.iloc[:, 0], c.iloc[:, 0]
        prices[tick] = pd.DataFrame({"open": o, "close": c}).dropna()
        print(f"  {tick}: {len(prices[tick])}日")

    out = {
        "years": YEARS,
        "updated": None,
        "source": "CFTC Commitments of Traders（non-commercial）＋日足（Yahoo Finance）",
        "headline": "翌週の予測にはなりません",
        "method": ("CFTCは火曜時点の建玉を金曜夕（日本時間の土曜）に公表する。"
                   "読者が動けるのは翌週月曜の寄りなので、"
                   "「翌週月曜始値→同週金曜終値」で測る。火曜〜金曜の値動きは使わない。"
                   "円は JPY=X が『1ドル＝何円』なので、円買い方向との一致を見るため符号を反転している。"),
        "caveats": [
            "公表は火曜時点のデータで、土曜まで分かりません。起点は翌週月曜の寄りです",
            "極端域は無条件（全週）の平均との差で見ています。資産自体が10年で上昇しているため",
            "極端域の信頼区間はブートストラップ（5000回）。0を含むものは参考値です",
            "6商品×3通りの検定をしているので、p<0.05がいくつか出るのは偶然でも起こります",
            "投機筋に乗れば勝てるという意味ではありません",
        ],
        "items": [],
    }
    for key, label, prefixes, tick, invert in SPECS:
        rows = fetch_cot(prefixes)
        if not rows:
            out["items"].append({"key": key, "label": label, "note": "CFTCデータを取得できず"})
            continue
        recs = []
        for r in rows:
            try:
                d = pd.Timestamp(r["report_date_as_yyyy_mm_dd"][:10])
                net = int(r["noncomm_positions_long_all"]) - int(r["noncomm_positions_short_all"])
                recs.append((d, net))
            except Exception:
                continue
        recs.sort()
        px = prices[tick]

        def ret(start_ts, weeks):
            """公表(火曜基準)の翌週月曜の寄り → weeks週後の金曜引け"""
            mon = start_ts + pd.Timedelta(days=6)          # 火曜 +6日 = 翌週月曜
            end = mon + pd.Timedelta(days=weeks * 7 - 3)   # その週の金曜
            a = px[px.index >= mon]
            b = px[px.index <= end]
            if a.empty or b.empty or b.index[-1] < a.index[0]:
                return None
            o = a["open"].iloc[0]
            c = b["close"].iloc[-1]
            if not o or not c:
                return None
            r = (c / o - 1) * 100
            return -r if invert else r    # 円は「数値上昇＝円安」なので反転

        # --- 設問1: 増やした/減らした週の翌週 ---
        def agg(sel, weeks=1):
            hit = tot = 0
            rs = []
            for i, (d, net) in enumerate(recs):
                if i == 0:
                    continue
                chg = net - recs[i - 1][1]
                if not sel(chg):
                    continue
                r = ret(d, weeks)
                if r is None:
                    continue
                tot += 1
                rs.append(r)
                if (chg > 0 and r > 0) or (chg < 0 and r < 0):
                    hit += 1
            if not tot:
                return None
            return {"n": tot, "match_rate": round(100 * hit / tot, 1),
                    "avg_return": round(sum(rs) / len(rs), 3),
                    "p_value": round(binom_p(hit, tot), 4)}

        item = {
            "key": key, "label": label, "ticker": tick,
            "period": {"from": str(recs[0][0].date()), "to": str(recs[-1][0].date()),
                       "weeks": len(recs)},
            "increased": agg(lambda c: c > 0),
            "decreased": agg(lambda c: c < 0),
        }
        # 増減あわせた全体の同方向率
        item["all"] = agg(lambda c: c != 0)

        # 直近1年
        cut = recs[-1][0] - pd.Timedelta(days=365)
        rec_all = recs
        recs = [r for r in rec_all if r[0] >= cut]
        item["last_1y"] = agg(lambda c: c != 0)
        recs = rec_all

        # --- 設問2: 極端な水準（過去3年のパーセンタイル上位/下位10%）---
        # ※資産そのものが10年で上昇しているので、平均が+でも意味があるとは限らない。
        #   無条件（全週）の平均を基準として必ず併記し、その差で判断する。
        #   空売りの検証で日経を基準にして同じ罠を踏んだので、ここでも基準を置く。
        ext = {"high": {"n": 0}, "low": {"n": 0}, "baseline": {}}
        for h in (4, 12):
            allr = [x for x in (ret(d, h) for d, _ in recs) if x is not None]
            if allr:
                ext["baseline"][f"w{h}_avg"] = round(sum(allr) / len(allr), 3)
                ext["baseline"][f"w{h}_n"] = len(allr)
        for h in (4, 12):
            hi, lo = [], []
            for i, (d, net) in enumerate(recs):
                past = [n for dd, n in recs[:i] if dd >= d - pd.Timedelta(days=365 * 3)]
                if len(past) < 100:
                    continue
                s = pd.Series(past)
                r = ret(d, h)
                if r is None:
                    continue
                if net >= s.quantile(0.90):
                    hi.append(r)
                elif net <= s.quantile(0.10):
                    lo.append(r)
            for side, vals in (("high", hi), ("low", lo)):
                if not vals:
                    continue
                ci = boot_ci(vals)
                ext[side]["n"] = len(vals)
                ext[side][f"w{h}_avg"] = round(sum(vals) / len(vals), 3)
                ext[side][f"w{h}_ci95"] = ci
                # 信頼区間が0を含まなければ主表示、含めば参考値
                ext[side][f"w{h}_solid"] = bool(ci and (ci[0] > 0 or ci[1] < 0))
                # 無条件平均との差。これが「極端域だから何か違う」の本体
                base = ext["baseline"].get(f"w{h}_avg")
                if base is not None:
                    diff = [v - base for v in vals]
                    dci = boot_ci(diff)
                    ext[side][f"w{h}_vs_base"] = round(sum(diff) / len(diff), 3)
                    ext[side][f"w{h}_vs_base_ci95"] = dci
                    ext[side][f"w{h}_solid"] = bool(dci and (dci[0] > 0 or dci[1] < 0))
        item["extreme"] = ext
        out["items"].append(item)
        a = item["all"]
        print(f"{label:22s} n={a['n']:4d} 同方向{a['match_rate']:5.1f}% "
              f"平均{a['avg_return']:+.2f}% p={a['p_value']}")

    import datetime as _dt
    out["updated"] = _dt.datetime.now(
        _dt.timezone(_dt.timedelta(hours=9))).isoformat(timespec="seconds")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")),
                   encoding="utf-8")
    print("\n→ cot_score.json")


if __name__ == "__main__":
    main()
