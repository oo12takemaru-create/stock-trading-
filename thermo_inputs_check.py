# -*- coding: utf-8 -*-
"""相場の温度計の入力を増やしたら良くなるか（検証だけ・公開中の温度は変えない）— 2026-11-17

■ 比べるもの
  今の5つの入力（日経平均と200日線・50日線の差、22営業日前比、S&P500と200日線の差、VIX）に、
  市場の中身の数字を足した版を作り、温度（帯は判定のまま・帯の中の位置だけ変わる）と「その後の日経平均」の関係を比べる。
  足す候補（どれも株価から自前で計算。JPX 由来の生の数値ではない）:
    pct_ma25  25日線より上の銘柄の割合（%）
    adr25     騰落レシオ（25日。値上がり銘柄数の合計 ÷ 値下がり銘柄数の合計 × 100）
    nhnl      52週高値の銘柄数 − 52週安値の銘柄数（銘柄数に対する%）
  銘柄はスキャナーの監視銘柄（daily_scanner_v2_8_0.STOCKS・約340銘柄・今の顔ぶれ）。
  ★今の顔ぶれで過去を作るので、幅の数字は当時の実感より良く出る（生存バイアス）★
  日経VI は Yahoo で過去の日足が取れないので入れていない。

■ 物差し（順位相関＝スピアマン）
  温度 と 日経平均の20・60営業日後の騰落 / 20営業日後までの日々の値動きの荒さ（日次騰落の標準偏差）/ 20営業日の間の最大下落
  日ごとの値は期間が重なるので、毎月の最初の営業日だけを使った値も並べる。

使い方:
  python -X utf8 thermo_inputs_check.py --cache "<precompute/cache/prices/adj のパス>"   # 手元の日足を使う
  python -X utf8 thermo_inputs_check.py                                                 # 無ければ yfinance から（11年）
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))

import thermo100 as T        # noqa: E402
import thermo_long as L      # noqa: E402

EXTRA = ["pct_ma25", "adr25", "nhnl"]


def load_stock_closes(cache: str | None) -> pd.DataFrame:
    if cache and Path(cache).exists():
        cols = {}
        for p in Path(cache).glob("*.csv"):
            df = pd.read_csv(p, index_col=0, parse_dates=True)
            cols[p.stem] = df["Close"]
        return pd.DataFrame(cols).sort_index()
    import yfinance as yf
    from daily_scanner_v2_8_0 import STOCKS
    df = yf.download(list(STOCKS.keys()), period="11y", progress=False, auto_adjust=True, threads=True)["Close"]
    return df.sort_index()


def breadth(closes: pd.DataFrame) -> pd.DataFrame:
    c = closes.dropna(how="all")
    ma25 = c.rolling(25).mean()
    have = ma25.notna()
    pct = ((c > ma25) & have).sum(axis=1) / have.sum(axis=1).replace(0, np.nan) * 100
    ch = c.diff()
    up, dn = (ch > 0).sum(axis=1), (ch < 0).sum(axis=1)
    adr = up.rolling(25).sum() / dn.rolling(25).sum().replace(0, np.nan) * 100
    hi, lo = c.rolling(250).max(), c.rolling(250).min()
    h_ok = hi.notna()
    nh = ((c >= hi) & h_ok).sum(axis=1)
    nl = ((c <= lo) & h_ok).sum(axis=1)
    nhnl = (nh - nl) / h_ok.sum(axis=1).replace(0, np.nan) * 100
    out = pd.DataFrame({"pct_ma25": pct, "adr25": adr, "nhnl": nhnl})
    out.loc[have.sum(axis=1) < 100, "pct_ma25"] = np.nan
    out.loc[h_ok.sum(axis=1) < 100, "nhnl"] = np.nan
    return out


def spearman(a, b) -> float | None:
    m = ~(np.isnan(a) | np.isnan(b))
    if m.sum() < 30:
        return None
    ra = pd.Series(a[m]).rank().values
    rb = pd.Series(b[m]).rank().values
    return float(np.corrcoef(ra, rb)[0, 1])


def targets(n225: pd.Series, dates) -> pd.DataFrame:
    c = n225.reindex(dates)
    r = c.pct_change()
    out = pd.DataFrame(index=dates)
    out["f20"] = (c.shift(-20) / c - 1) * 100
    out["f60"] = (c.shift(-60) / c - 1) * 100
    out["vol20"] = r[::-1].rolling(20).std()[::-1].shift(-1) * 100        # 翌日から20日間の日次騰落の標準偏差
    fmin = c[::-1].rolling(20).min()[::-1].shift(-1)
    out["dd20"] = (fmin / c - 1) * 100                                      # 20日の間の最も安い終値まで
    return out


def run(g, closes, regime_fn):
    n225 = g["^N225"]["Close"].dropna()
    dates = n225.index
    feat = T.features(g, dates)
    pos = T.positions(feat)
    br = breadth(closes).reindex(dates, method="ffill", limit=3)
    for k in EXTRA:
        pos[k] = T.rolling_pct(br[k].values) * 100       # どれも高いほど温度が上がる向き
    calc = dates[199:]
    base_cols = [k for k, *_ in T.INPUTS]
    # 判定は1回だけ
    regimes, _ = L.regimes_and_scores(g, regime_fn, calc, pos[base_cols])
    tg = targets(n225, calc)
    variants = {"今の5つ": base_cols}
    for k in EXTRA:
        variants[f"5つ＋{k}"] = base_cols + [k]
    variants["5つ＋3つ全部"] = base_cols + EXTRA
    # 比べる期間 = 足す入力が全部そろった日から（同じ日で比べる）
    first = pos[EXTRA].dropna().index.min()
    res = {}
    for name, cols in variants.items():
        _, score = L.regimes_and_scores_fast(regimes, calc, pos[cols])
        rows = L.rows_from(calc, regimes, score, T.WINDOW)
        t = pd.Series({r["d"]: r["t"] for r in rows}).reindex(calc)
        sc = pd.Series(score * 100, index=calc)
        res[name] = (t, sc)
    period = calc[(calc >= first)]
    period = period[tg.loc[period, "f60"].notna()]
    monthly = pd.Series(period, index=period).groupby(period.to_period("M")).first().values
    out = {"period": [str(period[0].date()), str(period[-1].date())], "n_days": len(period),
           "n_months": len(monthly), "rows": []}
    base_t = res["今の5つ"][0]
    for name, (t, sc) in res.items():
        row = {"variant": name}
        for tgt in ("f20", "f60", "vol20", "dd20"):
            row[f"rho_t_{tgt}"] = spearman(t.loc[period].values.astype(float), tg.loc[period, tgt].values)
            row[f"rho_t_{tgt}_m"] = spearman(t.loc[monthly].values.astype(float), tg.loc[monthly, tgt].values)
            row[f"rho_score_{tgt}"] = spearman(sc.loc[period].values, tg.loc[period, tgt].values)
        d = (t.loc[period] - base_t.loc[period]).abs()
        row["mean_abs_diff_vs_now_c"] = round(float(d.mean()) * 0.8, 2)
        out["rows"].append(row)
    # 足す入力どうし・今の総合の位置との相関（同じことを測っていないか）
    cr = {}
    sc0 = res["今の5つ"][1].loc[period].values
    for k in EXTRA:
        cr[k] = spearman(pos[k].reindex(period).values, sc0)
    out["corr_with_now_score"] = cr
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default=None, help="precompute/cache/prices/adj（銘柄ごとの CSV）")
    ap.add_argument("--g", default=None, help="指数の日足（thermo_long.fetch の pickle）。無ければ取りに行く")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    from daily_scanner_v2_8_0 import detect_market_regime
    if a.g:
        import pickle
        g = pickle.load(open(a.g, "rb"))
    else:
        g = L.fetch()
    closes = load_stock_closes(a.cache)
    print(f"銘柄 {closes.shape[1]}・{closes.index[0].date()}〜{closes.index[-1].date()}")
    out = run(g, closes, detect_market_regime)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    if a.out:
        Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
