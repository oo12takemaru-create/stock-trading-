# -*- coding: utf-8 -*-
"""判定の検証 → docs/verify.json（株レーダー kaburadar.jp の通算成績ページが読む）

検証1 温度計: daily_scanner_v2_8_0.detect_market_regime() を過去の日付で呼び直す。
              判定ロジックは再実装しない。
検証2 AI朝刊: docs/ai_record.json（公開後の実績）を、比較対象3つと並べて採点する。
検証2A 3軸スコアの遡及: 市場内部データ（25日線超の比率・騰落レシオ）と信用・空売りの
              履歴が5年ぶん無いため、できない。できない理由をそのままJSONに入れる。

共通の作法: 比較対象を並べる／作った期間と確かめる期間を分ける／その時点の情報だけ／
            外れも全件／ルールを先に固定。
"""
import json
import sys
from pathlib import Path

import pandas as pd
import yfinance as yf

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))
from daily_scanner_v2_8_0 import detect_market_regime  # noqa: E402

DOCS = REPO / "docs"
YEARS = 5
ORDER = ["BULLISH", "NEUTRAL", "BEARISH", "PANIC"]
RANK = {"BULLISH": 3, "NEUTRAL": 2, "BEARISH": 1, "PANIC": 0}

# ロジックの判定条件が最後に変わった日（git の履歴で確認・報告に根拠コミットを書いた）
CUT = {"radar": "2026-05-24", "score3": "2026-09-05", "gauge": "2026-08-11"}
CUT_COMMIT = {"radar": "b5415cf5（v2.8.0）", "score3": "5a4104d4", "gauge": "e5a63d56"}

ATT = {"attack", "lean_attack"}
DEF = {"defense", "lean_defense"}


def dl(ticker, period="8y", adjust=False):
    df = yf.download(ticker, period=period, progress=False, auto_adjust=adjust, threads=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df.dropna(subset=["Close"])


def radar_table():
    g = {t: dl(t) for t in ("^N225", "^GSPC", "^VIX")}
    topix = dl("1306.T", adjust=True)
    med = topix["Close"].rolling(11, center=True, min_periods=3).median()
    bad = (topix["Close"] / med - 1).abs() > 0.30
    dropped = [str(d.date()) for d in topix.index[bad]]
    topix = topix[~bad]

    end = topix.index[-1]
    days = [d for d in topix.index if d >= end - pd.Timedelta(days=int(365.25 * YEARS))]
    df = pd.DataFrame([{"d": d, "regime": detect_market_regime(g, d.date())[0]}
                       for d in days]).set_index("d")

    idx, o, c = topix.index, topix["Open"], topix["Close"]
    for n in (1, 5, 20):
        vals = []
        for d in df.index:
            i = idx.get_loc(d)
            vals.append((float(c.iloc[i + n]) / float(o.iloc[i + 1]) - 1) * 100
                        if i + n < len(idx) and float(o.iloc[i + 1]) else None)
        df[f"r{n}"] = vals
    dd = []
    for d in df.index:
        i = idx.get_loc(d)
        dd.append((float(c.iloc[i + 1:i + 21].min()) / float(o.iloc[i + 1]) - 1) * 100
                  if i + 20 < len(idx) and float(o.iloc[i + 1]) else None)
    df["dd20"] = dd

    def agg(sub, label):
        s = sub.dropna(subset=["r20"])
        if not len(s):
            return None
        return {"label": label, "n": int(len(s)),
                "r1": round(s["r1"].mean(), 2), "r5": round(s["r5"].mean(), 2),
                "r20": round(s["r20"].mean(), 2), "r20_med": round(s["r20"].median(), 2),
                "up20": round((s["r20"] > 0).mean() * 100, 1),
                "dd20": round(s["dd20"].mean(), 2),
                "sankou": bool(len(s) < 30)}

    def block(sub):
        out = {"all": agg(sub, "全期間平均"), "rows": []}
        for r in ORDER:
            a = agg(sub[sub["regime"] == r], r)
            if a:
                out["rows"].append(a)
        return out

    cut = pd.Timestamp(CUT["radar"])
    df["prev"] = df["regime"].shift(1)
    worse = df[df.apply(lambda x: x["prev"] in RANK
                        and RANK[x["regime"]] < RANK[x["prev"]], axis=1)]

    # 実際に出した判定との一致（確認項目1）
    same = diff = 0
    diffs = []
    try:
        hm = {x["d"]: x["regime"]
              for x in json.loads((DOCS / "radar_history.json").read_text(encoding="utf-8"))["items"]}
        for d in df.index:
            k = str(d.date())
            if k in hm:
                if hm[k] == df.loc[d, "regime"]:
                    same += 1
                else:
                    diff += 1
                    diffs.append({"d": k, "published": hm[k], "replay": df.loc[d, "regime"]})
    except Exception:
        pass

    return {
        "index": "1306.T（TOPIX連動ETF）",
        "period": [str(df.index[0].date()), str(df.index[-1].date())],
        "days": int(len(df)),
        "dropped_bars": dropped,
        "cut": CUT["radar"], "cut_commit": CUT_COMMIT["radar"],
        "built": block(df[df.index < cut]),
        "tested": block(df[df.index >= cut]),
        "whole": block(df),
        "worse": agg(worse, "判定が悪化した日"),
        "match": {"same": same, "diff": diff,
                  "pct": round(same / (same + diff) * 100, 1) if same + diff else None,
                  "rows": diffs},
    }


def ai_table():
    d = json.loads((DOCS / "ai_record.json").read_text(encoding="utf-8"))
    rows = d["ai"]["rows"]
    ret = {r["d"]: r["ret"] for r in rows if r.get("ret") is not None}
    dual = {x["d"]: x for x in d["dual"]["rows"]}
    reg = {x["d"]: x["s"] for x in d["regime"]["rows"]}
    MAP = {"BULLISH": "attack", "BEARISH": "defense", "PANIC": "defense", "NEUTRAL": None}

    def hits(pairs, label):
        h = j = 0
        for dd, st in pairs:
            if dd not in ret or st is None:
                continue
            r = ret[dd]
            if st in ATT:
                j += 1
                h += r > 0
            elif st in DEF:
                j += 1
                h += r < 0
        return {"label": label, "hit": h, "judged": j,
                "pct": round(h / j * 100, 1) if j else None, "sankou": bool(j < 30)}

    main = hits([(r["d"], r["s"]) for r in rows], "AI朝刊（公開値）")
    comps = [
        hits([(k, v["m"]) for k, v in dual.items()], "機械の3軸だけ（AIの修正前）"),
        hits([(r["d"], "attack") for r in rows], "毎日「攻め」"),
        hits([(rows[i]["d"], rows[i - 1]["s"]) for i in range(1, len(rows))], "前日と同じ判定"),
        hits([(k, MAP.get(v)) for k, v in reg.items()], "温度計の判定そのまま"),
    ]
    RK = {"attack": 4, "lean_attack": 3, "neutral": 2, "lean_defense": 1, "defense": 0}
    down = [{"d": k, "m": v["m"], "a": v["a"], "ret": v.get("ret"),
             "ok": bool(v.get("ret") is not None and v["ret"] < 0)}
            for k, v in dual.items() if RK.get(v["a"], 9) < RK.get(v["m"], 9)]
    ok = sum(1 for x in down if x["ok"])
    return {
        "period": [rows[0]["d"], rows[-1]["d"]], "days": len(rows),
        "cut": CUT["score3"], "cut_commit": CUT_COMMIT["score3"],
        "main": main, "comparisons": comps,
        "beats_all": bool(main["pct"] is not None
                          and all(c["pct"] is None or main["pct"] > c["pct"] for c in comps)),
        "downgrades": {"rows": down, "ok": ok, "n": len(down),
                       "pct": round(ok / len(down) * 100, 1) if down else None},
    }


def main():
    out = {
        "updated": pd.Timestamp.now(tz="Asia/Tokyo").isoformat(timespec="seconds"),
        "radar": radar_table(),
        "ai": ai_table(),
        "score3_backfill": {
            "possible": False,
            "reason": "3軸スコアの入力のうち、25日線より上の銘柄の比率・騰落レシオ・"
                      "52週高値更新数（市場内部データ）と、信用買い残・大口の空売りの"
                      "銘柄別残高に、過去5年ぶんの履歴が無い。"
                      "いまある手持ちのデータでは当時のスコアを再現できないため、遡及検証はしない。",
            "instead": "2026-10-10 から docs/score3_hist.json に毎日の判定を残している。"
                       "件数がたまったら、公開後の実績として採点する。",
        },
        "crash": {"status": "未実施", "note": "傾斜計の遡及検証は未着手"},
    }
    (DOCS / "verify.json").write_text(json.dumps(out, ensure_ascii=False, indent=1),
                                      encoding="utf-8")
    r, a = out["radar"], out["ai"]
    print(f"verify.json: 温度計 {r['days']}日（確かめる期間 "
          f"{r['tested']['all']['n'] if r['tested']['all'] else 0}日）"
          f" / 一致率 {r['match']['pct']}% / AI朝刊 {a['main']['pct']}%"
          f"（比較対象に勝っている: {a['beats_all']}）")


if __name__ == "__main__":
    main()
