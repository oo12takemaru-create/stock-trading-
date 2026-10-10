# -*- coding: utf-8 -*-
"""検証3：暴落の傾斜計（5つの前兆）の遡及検証 → docs/verify_crash.json

gauge_fetch.py の5つの傾斜計を、同じ閾値・同じ判定式のまま過去5年ぶん日次で計算し直す。
閾値（本の基準値）は gauge_fetch から読み込んで使う。式は写していない。

ルール（先に固定した）:
  暴落 = 1306.T（TOPIX連動ETF）が直近20営業日の高値から -10%以上下げた日。
         連続する日はひとつの暴落としてまとめ、最初の日を発生日とする
  警戒 = 傾斜計の点灯が3つ以上（本の「注意」以上）
  先行日数 = 暴落の発生日から見て、直前の警戒が立ち上がった日までの営業日数
  見逃し = 暴落の発生日に警戒が出ていなかった暴落
  空振り = 警戒が立ち上がってから120営業日たっても暴落が来なかった回

その時点の情報だけを使う:
  四半期データ（米証拠金債務）は公表までに時間差があるため、75日前までに
  観測された分しか使わない。日次・月次はその日までに出ている分を使う。
"""
import json
import re
import sys
import urllib.request
from pathlib import Path

import pandas as pd
import yfinance as yf

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))
import gauge_fetch as G  # noqa: E402  閾値と fred() を借りる

DOCS = REPO / "docs"
YEARS = 5
CRASH_DD = -0.10       # 暴落とみなす下げ幅
CRASH_WIN = 20         # 直近高値を見る営業日数
WARN_FLAGS = 3         # 警戒とみなす点灯数（本の「注意」以上）
LOOKAHEAD = 120        # 空振りの判定に使う営業日数
MARGIN_LAG = 75        # 四半期データの公表までの日数（その時点で見えていた分だけ使う）
CUT = "2026-08-11"     # 傾斜計が入った日（コミット e5a63d56）
UA = {"User-Agent": "Mozilla/5.0 (compatible; kaburadar.jp/1.0; +https://kaburadar.jp)"}


def cape_series():
    """CAPE（シラーPER）の月次履歴。live版は「今の値」だけを取るので、ここだけ取得先が違う"""
    html = urllib.request.urlopen(urllib.request.Request(
        "https://www.multpl.com/shiller-pe/table/by-month", headers=UA),
        timeout=60).read().decode("utf-8", "replace")
    rows = re.findall(r"<td[^>]*>\s*([A-Z][a-z]{2} \d{1,2}, \d{4})\s*</td>\s*<td[^>]*>[^0-9]*([0-9.]+)", html)
    s = pd.Series({pd.Timestamp(d): float(v) for d, v in rows}).sort_index()
    return s


def adr_series(years):
    """騰落レシオ（25日）。live版と同じ監視銘柄・同じ式"""
    from daily_scanner_v2_8_0 import STOCKS
    raw = yf.download(list(STOCKS.keys()), period=f"{years + 1}y", progress=False,
                      auto_adjust=False, threads=True)["Close"].dropna(how="all")
    chg = raw.diff()
    up, dn = (chg > 0).sum(axis=1), (chg < 0).sum(axis=1)
    return (up.rolling(25).sum() / dn.rolling(25).sum() * 100).dropna(), raw.shape[1]


def at(s, d, lag_days=0):
    """その日までに出ている最後の値（公表の時間差ぶんだけ手前で切る）"""
    t = d - pd.Timedelta(days=lag_days)
    v = s[s.index <= t]
    return (float(v.iloc[-1]), v.index[-1]) if len(v) else (None, None)


def main():
    print("データを取得中…", file=sys.stderr)
    t10y2y = G.fred("T10Y2Y", years=YEARS + 3)
    margin = G.fred("BOGZ1FL663067003Q", years=YEARS + 3)
    ffr = G.fred("DFEDTARU", years=YEARS + 3)
    boj = G.fred("IRSTCI01JPM156N", years=YEARS + 3)
    cape = cape_series()
    adr, ntk = adr_series(YEARS)
    n225 = yf.download("^N225", period=f"{YEARS + 1}y", progress=False, auto_adjust=False)["Close"].dropna()
    if isinstance(n225, pd.DataFrame):
        n225 = n225.iloc[:, 0]

    topix = yf.download("1306.T", period=f"{YEARS + 1}y", progress=False,
                        auto_adjust=True, threads=False)
    if isinstance(topix.columns, pd.MultiIndex):
        topix.columns = topix.columns.get_level_values(0)
    topix = topix.dropna(subset=["Close"])
    med = topix["Close"].rolling(11, center=True, min_periods=3).median()
    bad = (topix["Close"] / med - 1).abs() > 0.30
    dropped = [str(d.date()) for d in topix.index[bad]]
    topix = topix[~bad]

    end = topix.index[-1]
    days = [d for d in topix.index if d >= end - pd.Timedelta(days=int(365.25 * YEARS))]

    rows = []
    for d in days:
        f = {}
        # ① 逆イールド
        cur, _ = at(t10y2y, d)
        if cur is None:
            f["yield"] = None
        else:
            past = t10y2y[t10y2y.index <= d]
            inv = past[past < 0]
            if cur < 0:
                f["yield"] = True
            elif len(inv):
                f["yield"] = ((d - inv.index[-1]).days / 30.44) <= G.POST_INVERSION_M
            else:
                f["yield"] = False
        # ② 割高（CAPE）
        c, _ = at(cape, d)
        f["cape"] = None if c is None else (c > G.CAPE_HIGH)
        # ③ 信用の膨張（四半期・公表の時間差を引く）
        mv = margin[margin.index <= d - pd.Timedelta(days=MARGIN_LAG)]
        f["margin"] = None if len(mv) < 5 else \
            ((float(mv.iloc[-1]) / float(mv.iloc[-5]) - 1) * 100 >= G.MARGIN_YOY)
        # ④ 市場の過熱（騰落レシオ＋ワニの口）
        a, _ = at(adr, d)
        ny = n225[(n225.index <= d) & (n225.index >= d - pd.Timedelta(days=365))]
        if a is None or not len(ny):
            f["overheat"] = None
        else:
            from_hi = float(ny.iloc[-1]) / float(ny.max()) - 1
            f["overheat"] = bool(a > G.ADR_HOT or (from_hi >= -0.05 and a < 100))
        # ⑤ 金融の引き締め
        rise = []
        for s in (ffr, boj):
            cur2, idx2 = at(s, d)
            if cur2 is None:
                continue
            old = s[s.index <= idx2 - pd.Timedelta(days=365)]
            if len(old):
                rise.append(cur2 - float(old.iloc[-1]) > 0)
        f["tighten"] = None if not rise else any(rise)

        on = sum(1 for v in f.values() if v is True)
        unknown = sum(1 for v in f.values() if v is None)
        rows.append({"d": d, "on": on, "unknown": unknown, **{k: v for k, v in f.items()}})

    df = pd.DataFrame(rows).set_index("d")

    # 暴落（直近20営業日の高値から -10%）
    c = topix["Close"].reindex(df.index)
    dd = c / c.rolling(CRASH_WIN, min_periods=2).max() - 1
    df["dd"] = dd
    hit = dd <= CRASH_DD
    events, last = [], None
    for i, (d, v) in enumerate(hit.items()):
        if not v:
            continue
        if last is not None and (i - last) < 60:
            last = i
            continue
        events.append(d)
        last = i

    idx = list(df.index)
    ev_i = [idx.index(d) for d in events]
    LIT_KEYS = ("yield", "cape", "margin", "overheat", "tighten")

    def measure(th):
        """点灯{th}個以上を警戒としたときの、見逃し・空振り・先行日数"""
        warn = (df["on"] >= th).tolist()

        def start_before(i):
            if not warn[i]:
                return None
            j = i
            while j > 0 and warn[j - 1]:
                j -= 1
            return j

        cases = []
        for d in events:
            i = idx.index(d)
            j = start_before(i)
            cases.append({
                "d": str(d.date()),
                "dd": round(float(df["dd"].iloc[i]) * 100, 1),
                "flags_on": int(df["on"].iloc[i]),
                "warned": bool(warn[i]),
                "warn_start": str(idx[j].date()) if j is not None else None,
                "lead_days": int(i - j) if j is not None else None,
                "lit": [k for k in LIT_KEYS if df[k].iloc[i] is True or df[k].iloc[i] == True],
            })
        starts = [i for i in range(1, len(idx)) if warn[i] and not warn[i - 1]]
        fp = [{"d": str(idx[i].date()),
               "hit": bool(any(i <= e <= i + LOOKAHEAD for e in ev_i))} for i in starts]
        miss = [x for x in cases if not x["warned"]]
        leads = [x["lead_days"] for x in cases if x["lead_days"] is not None]
        return {
            "threshold": th,
            "warn_days": int(sum(warn)),
            "warn_pct": round(sum(warn) / len(warn) * 100, 1),
            "cases": cases,
            "miss": len(miss),
            "miss_rate": round(len(miss) / len(events) * 100, 1) if events else None,
            "warn_starts": len(starts), "warn_starts_rows": fp,
            "false_rate": round(sum(1 for x in fp if not x["hit"]) / len(fp) * 100, 1) if fp else None,
            "lead_median": int(pd.Series(leads).median()) if leads else None,
        }

    cut = pd.Timestamp(CUT)
    out = {
        "updated": pd.Timestamp.now(tz="Asia/Tokyo").isoformat(timespec="seconds"),
        "rule": {"crash": f"1306.T が直近{CRASH_WIN}営業日の高値から{CRASH_DD*100:.0f}%以上下げた日",
                 "warn": f"傾斜計の点灯が{WARN_FLAGS}つ以上（本の「注意」以上）",
                 "lookahead": LOOKAHEAD, "margin_lag": MARGIN_LAG},
        "index": "1306.T（TOPIX連動ETF）", "dropped_bars": dropped,
        "period": [str(df.index[0].date()), str(df.index[-1].date())], "days": len(df),
        "tickers": ntk, "cut": CUT,
        "crashes": len(events),
        "default_threshold": WARN_FLAGS,
        "by_threshold": [measure(t) for t in (3, 4, 5)],
        "flags_dist": {str(k): int(v) for k, v in df["on"].value_counts().sort_index().items()},
        "tested_note": f"傾斜計が入ったのは {CUT}。それ以前は作った期間（参考）",
        "tested_days": int((df.index >= cut).sum()),
        "tested_crashes": sum(1 for d in events if d >= cut),
    }
    (DOCS / "verify_crash.json").write_text(json.dumps(out, ensure_ascii=False, indent=1),
                                            encoding="utf-8")
    print(f"verify_crash.json: {out['days']}営業日 / 暴落 {out['crashes']}回")
    for m in out["by_threshold"]:
        print(f"  点灯{m['threshold']}個以上を警戒とすると: 警戒だった日 {m['warn_pct']}% / "
              f"見逃し {m['miss_rate']}% / 空振り {m['false_rate']}% / "
              f"先行の中央値 {m['lead_median']}営業日（立ち上がり {m['warn_starts']}回）")
    for x in out["by_threshold"][1]["cases"]:
        print(f"   暴落 {x['d']} {x['dd']}% 点灯{x['flags_on']} "
              f"{'警戒あり' if x['warned'] else '★警戒なし（見逃し）'} "
              f"先行{x['lead_days']}営業日 {x['lit']}")


if __name__ == "__main__":
    main()
