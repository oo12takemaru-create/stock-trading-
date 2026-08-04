# -*- coding: utf-8 -*-
"""十倍株スキャナー(公開用・GitHub Actions週次実行版)

ミネルヴィニ(トレンドテンプレート8点)+ ロケット投資(検証済み条件3点)+
サイズ(時価総額2点)+ リンチ(業績5点・技術点上位300のみ)の4レンズ・満点18点で
東証全銘柄を機械的に採点し、上位50銘柄を docs/tenbagger.json に書き出す。
株レーダー(kaburadar.jp)が表示する。

これは機械的スクリーニングの出力事実の公開であり、投資助言・銘柄推奨ではない。
ローカル版: 株式投資開発/十倍株スキャナー/tenbagger_screener.py(ロジックは同一)

実行: python tenbagger_rank.py
入力: tenbagger_universe.csv(東証全銘柄・JPX公式一覧から生成)
      tenbagger_shares.csv(発行済株式数・時価総額の近似用)
出力: docs/tenbagger.json
"""

import datetime as dt
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

HERE = Path(__file__).resolve().parent
UNIVERSE_CSV = HERE / "tenbagger_universe.csv"
SHARES_CSV = HERE / "tenbagger_shares.csv"
OUT_JSON = HERE / "docs" / "tenbagger.json"

MIN_DAYS = 250
MIN_TURNOVER = 20e6      # 20日平均売買代金 2,000万円未満は除外
RS_TOP_PCT = 70
CHUNK = 200
TOP_N = 50
LYNCH_POOL = 300         # 業績(リンチレンズ)を取得するのは技術点上位この件数のみ


def evaluate(close_s: pd.Series, vol_s: pd.Series) -> dict | None:
    d = pd.DataFrame({"Close": close_s, "Volume": vol_s}).dropna(subset=["Close"])
    d = d[d["Close"] > 0]
    if len(d) < MIN_DAYS:
        return None
    close = d["Close"].to_numpy("float64")
    vol = d["Volume"].fillna(0).to_numpy("float64")
    c = close[-1]

    ma50 = close[-50:].mean()
    ma150 = close[-150:].mean()
    ma200 = close[-200:].mean()
    ma200_prev = close[-220:-20].mean()
    lo52 = close[-250:].min()
    hi52 = close[-250:].max()

    def ret(days):
        return c / close[-days - 1] - 1 if len(close) > days else np.nan

    rs_raw = 0.4 * ret(63) + 0.2 * ret(126) + 0.2 * ret(189) + 0.2 * ret(250)

    minervini = sum([
        c > ma150 and c > ma200,
        ma150 > ma200,
        ma200 > ma200_prev,
        ma50 > ma150 and ma50 > ma200,
        c > ma50,
        c >= lo52 * 1.30,
        c >= hi52 * 0.75,
    ])
    base_win = close[-66:]
    prev20 = vol[-25:-5]
    r1 = c >= hi52 * 0.95                                       # 新高値圏
    r2 = base_win.max() / base_win.min() <= 1.30                # 発射台(持ち合い)
    r3 = prev20.mean() > 0 and vol[-5:].max() >= prev20.mean() * 2  # 点火出来高
    ma25 = close[-25:].mean()
    turnover = float((d["Close"] * d["Volume"]).tail(20).mean())
    return {
        "close": round(c, 1),
        "rs_raw": rs_raw,
        "minervini7": int(minervini),   # M8(RS)は全銘柄比較後に加点
        "rocket": int(r1 + r2 + r3),
        "near_high": bool(r1),
        "base": bool(r2),
        "ignition": bool(r3),
        "ma25_gap_pct": round((c / ma25 - 1) * 100, 1),
        "off_high_pct": round((c / hi52 - 1) * 100, 1),
        "ret3m_pct": round(ret(63) * 100, 1),
        "turnover": turnover,
        "last_date": d.index[-1],
    }


def lynch_eval(tk: str) -> dict:
    """リンチレンズ(業績5点)。データ欠損は加点なし=業績を確認できない銘柄は上げない。

    L1 PEG(PER÷利益成長率) <1.0で2点 / <1.5で1点
    L2 利益成長が年20〜50%のスイートスポットで1点(50%超は過熱とみなし加点しない)
    L3 有利子負債が自己資本の50%未満で1点
    L4 無名(アナリスト1人以下 かつ 機関保有20%未満)で1点
    成長率は年次純利益の3年成長率(年率)を優先し、無ければ直近四半期YoYで代用。
    """
    out = {"lynch": 0.0, "peg": None, "growth_pct": None, "obscure": False}
    try:
        tick = yf.Ticker(tk)
        info = tick.info or {}
    except Exception:
        return out
    growth = None
    try:
        ist = tick.income_stmt
        if ist is not None and "Net Income" in ist.index:
            ni = ist.loc["Net Income"].dropna()
            if len(ni) >= 2:
                latest, oldest = float(ni.iloc[0]), float(ni.iloc[-1])
                years = (ni.index[0] - ni.index[-1]).days / 365.25
                if latest > 0 and oldest > 0 and years >= 1:
                    growth = (latest / oldest) ** (1 / years) - 1
    except Exception:
        pass
    if growth is None:
        g = info.get("earningsGrowth")
        growth = float(g) if g is not None else None

    pe = info.get("trailingPE")
    peg = pe / (growth * 100) if (pe and growth and pe > 0 and growth > 0) else None
    dte = info.get("debtToEquity")
    analysts = info.get("numberOfAnalystOpinions") or 0
    inst = info.get("heldPercentInstitutions")

    l1 = 2.0 if (peg is not None and peg < 1.0) else (1.0 if peg is not None and peg < 1.5 else 0.0)
    l2 = 1.0 if (growth is not None and 0.20 <= growth <= 0.50) else 0.0
    l3 = 1.0 if (dte is not None and dte < 50) else 0.0
    l4 = 1.0 if (inst is not None and inst < 0.20 and analysts <= 1) else 0.0

    out["lynch"] = l1 + l2 + l3 + l4
    out["peg"] = None if peg is None else round(peg, 2)
    out["growth_pct"] = None if growth is None else round(growth * 100, 1)
    out["obscure"] = l4 == 1.0
    return out


def size_score(mcap_oku) -> float:
    if pd.isna(mcap_oku):
        return 0.0
    if mcap_oku < 100:
        return 2.0
    if mcap_oku < 300:
        return 1.5
    if mcap_oku < 1000:
        return 1.0
    return 0.0


def main():
    uni = pd.read_csv(UNIVERSE_CSV).set_index("ticker")
    shares = pd.read_csv(SHARES_CSV).set_index("ticker")["shares"]
    tickers = list(uni.index)
    print(f"ユニバース: {len(tickers)}銘柄 / 2年日足をチャンク取得")

    rows = {}
    young = failed = 0
    for i in range(0, len(tickers), CHUNK):
        chunk = tickers[i:i + CHUNK]
        for attempt in (1, 2, 3):
            try:
                raw = yf.download(chunk, period="2y", auto_adjust=False,
                                  group_by="ticker", progress=False, threads=True)
                break
            except Exception as e:
                print(f"  チャンク{i // CHUNK + 1} 取得失敗({attempt}/3): {e}")
                time.sleep(20 * attempt)
        else:
            failed += len(chunk)
            continue
        for t in chunk:
            try:
                d = raw[t] if isinstance(raw.columns, pd.MultiIndex) else raw
                res = evaluate(d["Close"], d["Volume"])
            except Exception:
                failed += 1
                continue
            if res is None:
                young += 1
                continue
            rows[t] = res
        print(f"  {min(i + CHUNK, len(tickers))}/{len(tickers)} 済")
        time.sleep(1)

    t = pd.DataFrame.from_dict(rows, orient="index")
    t = t.join(uni[["code", "name", "market", "sector33"]])
    t["mcap_oku"] = (t["close"] * t.index.map(shares) / 1e8).round(1)
    t["rs_pct"] = t["rs_raw"].rank(pct=True).mul(100).round(1)
    t["minervini"] = t["minervini7"] + (t["rs_pct"] >= RS_TOP_PCT).astype(int)
    t["size"] = t["mcap_oku"].map(size_score)
    t["score"] = t["minervini"] + t["rocket"] + t["size"]

    n_all = len(t)
    illiquid = t["turnover"] < MIN_TURNOVER
    t = t[~illiquid].sort_values(["score", "rs_pct"], ascending=False)

    # リンチレンズ: 技術点上位のみ業績を取得して加点(最終審査)
    pool = list(t.head(LYNCH_POOL).index)
    print(f"リンチレンズ: 技術点上位{len(pool)}銘柄の業績を取得(10分前後)")
    lynch_rows = {}
    for j, tk in enumerate(pool, 1):
        lynch_rows[tk] = lynch_eval(tk)
        if j % 50 == 0:
            print(f"  {j}/{len(pool)} 済")
        time.sleep(0.2)
    t = t.join(pd.DataFrame.from_dict(lynch_rows, orient="index"))
    t["lynch"] = t["lynch"].fillna(0.0)
    t["score"] = t["score"] + t["lynch"]
    t = t.sort_values(["score", "rs_pct"], ascending=False)

    data_date = max(r["last_date"] for r in rows.values())
    items = []
    for rank, (_, r) in enumerate(t.head(TOP_N).iterrows(), 1):
        items.append({
            "rank": rank,
            "code": str(r["code"]),
            "name": r["name"],
            "market": r["market"],
            "sector": r["sector33"],
            "close": r["close"],
            "mcap_oku": None if pd.isna(r["mcap_oku"]) else r["mcap_oku"],
            "score": round(float(r["score"]), 1),
            "minervini": int(r["minervini"]),
            "rocket": int(r["rocket"]),
            "size": float(r["size"]),
            "lynch": float(r["lynch"]),
            "peg": None if pd.isna(r["peg"]) else float(r["peg"]),
            "growth_pct": None if pd.isna(r["growth_pct"]) else float(r["growth_pct"]),
            "obscure": bool(r["obscure"]) if not pd.isna(r["obscure"]) else False,
            "near_high": bool(r["near_high"]),
            "base": bool(r["base"]),
            "ignition": bool(r["ignition"]),
            "ma25_gap_pct": float(r["ma25_gap_pct"]),
            "rs": float(r["rs_pct"]),
            "off_high_pct": float(r["off_high_pct"]),
            "ret3m_pct": float(r["ret3m_pct"]),
            "turnover_mm": round(r["turnover"] / 1e6),
        })

    jst = dt.timezone(dt.timedelta(hours=9))
    out = {
        "updated": dt.datetime.now(jst).isoformat(timespec="seconds"),
        "data_date": pd.Timestamp(data_date).strftime("%Y-%m-%d"),
        "universe": int(n_all),
        "excluded_young": int(young),
        "excluded_illiquid": int(illiquid.sum()),
        "max_score": 18,
        "lynch_pool": len(pool),
        "items": items,
    }
    OUT_JSON.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"完了: 評価{n_all} / 上場1年未満{young} / 流動性除外{int(illiquid.sum())} / 取得失敗{failed}")
    print(f"→ {OUT_JSON}")


if __name__ == "__main__":
    main()
