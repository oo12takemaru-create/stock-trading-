# -*- coding: utf-8 -*-
"""相場の温度計を 0〜100 の1つの数字にする → docs/thermo100.json・docs/thermo100_hist.json

■ 何か（2026-11-15）
  サイトの「相場の温度計」（radar.html）の判定は daily_scanner_v2_8_0.detect_market_regime() の
  4段階（強気 BULLISH／中立 NEUTRAL／弱気 BEARISH／総崩れ PANIC）。
  その判定に 0〜100 の「温度」を **足す**。判定そのもの・radar.json・radar_history.json・
  3軸スコア（score3）・AI朝刊・答え合わせ（verify.json）は一切変えない。

■ 作り方（帯は判定で決め、帯の中の位置は過去10年での位置で決める）
  1) 判定は detect_market_regime() をそのまま呼ぶ（再実装しない）。判定 → 帯
        総崩れ 0〜14 ／ 弱気 15〜39 ／ 中立 40〜59 ／ 強気 60〜100
     → 温度の帯と判定は構造上かならず一致する（tests/test_thermo100.py が全日を確かめる）
  2) 判定と同じ入力を5つの連続値にする
        日経平均と200日線の差・日経平均と50日線の差・日経平均の22営業日前比・
        S&P500と200日線の差・VIX（VIX だけは低いほど温度が上がる向き）
  3) それぞれを「その日までの過去10年（約2,450営業日）の中で何%の位置か」にして平均 → 総合の位置
  4) 総合の位置を「その日までの過去10年のうち、同じ判定だった日の中で何%の位置か」にする → 帯の中の位置
     （強気の日はもともと入力が高いので、全日の中の位置のままだと強気の帯の上の方に固まる。
       同じ判定の日どうしで比べると、帯の下の方から上の方まで同じくらいずつ出る）
     その日までのデータだけを使うので、あとから来た値で過去の温度は変わらない
  5) 温度 = 帯の下限 + 帯の中の位置 ×（帯の上限 − 下限）を四捨五入
  判定が切り替わる日は、数字が帯をまたいで大きく動く（矛盾をゼロにする代わりに受け入れる）。

■ 公開してよいもの
  入力は公開の指数（日経平均・S&P500・VIX）だけ。JPX 由来の数字（信用残・空売り・投資部門別）は
  一切使わず、履歴にも入れない。

■ 使い方
  python thermo100.py            # docs/ に2つの JSON を書く
  python thermo100.py --dry-run  # 書かずに要約だけ表示
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))

JST = timezone(timedelta(hours=9))
DOCS = REPO / "docs"

NAME = "相場の温度計"
HIST_YEARS = 10           # 履歴として出す期間
WINDOW = 2450             # 位置を測る過去の窓（日本の営業日で約10年）
MIN_PERIODS = 250         # 窓の中にこれだけ無い入力は使わない
FETCH_PERIOD = "21y"      # 履歴10年＋窓10年＋200日線の助走

# 判定 → 帯（両端を含む整数）。境目はここだけを見れば分かるようにまとめる
BANDS = [
    {"regime": "PANIC",   "jp": "総崩れ", "lo": 0,  "hi": 14},
    {"regime": "BEARISH", "jp": "弱気",   "lo": 15, "hi": 39},
    {"regime": "NEUTRAL", "jp": "中立",   "lo": 40, "hi": 59},
    {"regime": "BULLISH", "jp": "強気",   "lo": 60, "hi": 100},
]
BAND = {b["regime"]: b for b in BANDS}

# ℃ 表示（2026-11-17 本人指示「−20℃〜60℃ で（0℃ が寒い）」）。
# 0〜100 の値はそのまま残し、表示用に ℃ = round(値 × 0.8 − 20) を並べるだけ。
C_MIN, C_MAX = -20, 60


def celsius(v):
    """0〜100 の温度 → ℃（−20〜60 の整数）。値×0.8 の端数は .0/.2/.4/.6/.8 だけなので四捨五入の向きで迷わない"""
    if v is None:
        return None
    return int(round(float(v) * 0.8 - 20))


def c_label(c: int) -> str:
    """℃ の表記（負の数は全角のマイナス「−」）"""
    return f"{'−' if c < 0 else ''}{abs(c)}℃"


# 帯の ℃（総崩れ −20〜−9／弱気 −8〜11／中立 12〜27／強気 28〜60）
BANDS_C = [{"regime": b["regime"], "jp": b["jp"], "lo": celsius(b["lo"]), "hi": celsius(b["hi"])} for b in BANDS]

# 入力（key, 表示名, 単位, 向き）。向き +1 = 値が大きいほど温度が上がる
INPUTS = [
    ("n225_ma200", "日経平均と200日線の差", "%", +1),
    ("n225_ma50",  "日経平均と50日線の差",  "%", +1),
    ("n225_chg22", "日経平均の22営業日前比", "%", +1),
    ("sp500_ma200", "S&P500と200日線の差",  "%", +1),
    ("vix",        "VIX（米国株の値動きの荒さ）", "", -1),
]

DEFINITION = (
    "相場の温度計（0〜100）は、日経平均・S&P500・VIX の今の位置を過去10年と比べて、"
    "日本株の地合いを1つの数字にまとめたものです。0に近いほど総悲観、100に近いほど過熱を表します。"
    "数字の帯は温度計の4段階の判定と一致します（総崩れ0〜14・弱気15〜39・中立40〜59・強気60〜100）。"
    "帯の中の位置は、判定に使う5つの値（日経平均と200日線・50日線の差、日経平均の22営業日前比、"
    "S&P500と200日線の差、VIX）が過去10年の中でどのあたりにあるかを平均し、"
    "過去10年の同じ判定の日と比べて決めます。"
    "判定が変わった日は数字が大きく動きます。これは地合いの要約で、売買の合図ではありません。"
    "表示では、0〜100 の位置を −20℃〜60℃ に読み替えて（℃ = 値 × 0.8 − 20）、0℃ を下回るほど冷え込んだ地合いとして示します。"
)
DEFINITION_C = ("0〜100 の位置を −20℃〜60℃ に読み替えたもの（℃ = 値 × 0.8 − 20 を四捨五入）。"
                "帯は 総崩れ −20〜−9℃・弱気 −8〜11℃・中立 12〜27℃・強気 28〜60℃。")
DEFINITION_SHORT = "日経平均・S&P500・VIX の位置を過去10年と比べて0〜100にまとめた地合いの要約（0が総悲観・100が過熱）。"

# 定義文・表示文に使わない言葉（売買の合図に読める言葉）
NG_WORDS = ("買い", "売り", "推奨", "おすすめ", "オススメ", "必ず", "予想", "恐怖と強欲", "体温計")


# ────────────────────────── 純粋な計算（テスト対象） ──────────────────────────
def band_of(regime: str) -> dict:
    return BAND[regime]


def regime_of_value(v: int) -> str:
    """温度 → 帯の判定。温度と判定が一致しているかを確かめるのに使う"""
    for b in BANDS:
        if b["lo"] <= v <= b["hi"]:
            return b["regime"]
    raise ValueError(f"温度が範囲外: {v}")


def place(regime: str, pos: float) -> int:
    """帯の中に位置（0〜1）を置いて温度（整数）にする。必ず帯の中に収まる"""
    b = BAND[regime]
    pos = min(1.0, max(0.0, float(pos)))
    v = int(round(b["lo"] + pos * (b["hi"] - b["lo"])))
    return min(b["hi"], max(b["lo"], v))


def rolling_pct(values: np.ndarray, window: int = WINDOW, min_periods: int = MIN_PERIODS) -> np.ndarray:
    """各日の値が「その日までの window 本（その日を含む）」の中で何%の位置か（0〜1）。
    同じ値は半分ずつ数える。欠損（NaN）は窓から除く。窓に min_periods 本ない日は NaN"""
    a = np.asarray(values, dtype=float)
    out = np.full(len(a), np.nan)
    for i in range(len(a)):
        x = a[i]
        if np.isnan(x):
            continue
        w = a[max(0, i - window + 1):i + 1]
        w = w[~np.isnan(w)]
        if len(w) < min_periods:
            continue
        less = np.count_nonzero(w < x)
        eq = np.count_nonzero(w == x)
        out[i] = (less + 0.5 * (eq - 1)) / (len(w) - 1) if len(w) > 1 else 0.5
    return out


def features(g: dict, dates: pd.DatetimeIndex) -> pd.DataFrame:
    """判定と同じ入力を日本の営業日ごとの連続値にする（その日以前の最後の値＝判定と同じ見方）"""
    out = pd.DataFrame(index=dates)
    n = g.get("^N225")
    if n is not None and len(n):
        c = n["Close"].dropna()
        f = pd.DataFrame({
            "n225_ma200": (c / c.rolling(200).mean() - 1) * 100,
            "n225_ma50": (c / c.rolling(50).mean() - 1) * 100,
            "n225_chg22": (c / c.shift(21) - 1) * 100,
        })
        out = out.join(f.reindex(dates, method="ffill"))
    s = g.get("^GSPC")
    if s is not None and len(s):
        c = s["Close"].dropna()
        out["sp500_ma200"] = ((c / c.rolling(200).mean() - 1) * 100).reindex(dates, method="ffill")
    v = g.get("^VIX")
    if v is not None and len(v):
        out["vix"] = v["Close"].dropna().reindex(dates, method="ffill")
    for k, *_ in INPUTS:
        if k not in out:
            out[k] = np.nan
    return out[[k for k, *_ in INPUTS]]


def positions(feat: pd.DataFrame) -> pd.DataFrame:
    """各入力の位置（0〜100・温度が上がる向きにそろえた値）"""
    pos = pd.DataFrame(index=feat.index)
    for k, _, _, sign in INPUTS:
        p = rolling_pct(feat[k].values)
        pos[k] = (p if sign > 0 else 1 - p) * 100
    return pos


MIN_SAME = 20   # 同じ判定の日が窓の中にこれだけ無いときは、全日の中の位置をそのまま使う


def cond_pct(score: np.ndarray, regimes: list, window: int = WINDOW, min_same: int = MIN_SAME):
    """各日の総合の位置（score）が、その日までの window 本のうち「同じ判定だった日」の中で
    何%の位置か（0〜1）。同じ判定の日が min_same 本に満たなければ score をそのまま返す。
    こうすると帯の中で温度が偏らず、帯の下の方・上の方がそれぞれ同じくらい出る"""
    a = np.asarray(score, dtype=float)
    reg = np.asarray(regimes, dtype=object)
    out = np.full(len(a), np.nan)
    for i in range(len(a)):
        x = a[i]
        if np.isnan(x) or reg[i] is None:
            continue
        lo = max(0, i - window + 1)
        w = a[lo:i + 1][(reg[lo:i + 1] == reg[i])]
        w = w[~np.isnan(w)]
        if len(w) < min_same:
            out[i] = x
            continue
        less = np.count_nonzero(w < x)
        eq = np.count_nonzero(w == x)
        out[i] = (less + 0.5 * (eq - 1)) / (len(w) - 1)
    return out


def build_rows(g: dict, regime_fn, dates: pd.DatetimeIndex, pos: pd.DataFrame,
               keep_from=None) -> list[dict]:
    """日ごとに判定（regime_fn）と温度を出す。
    1) 5つの入力の位置（0〜100）の平均 = 総合の位置
    2) 総合の位置を、過去10年の同じ判定の日の中での位置にする = 帯の中の位置
    3) 帯の中に置く
    入力の位置が1つも無い日は温度を出さない。keep_from より前の日は計算には使うが返さない"""
    regimes, score = [], []
    for d in dates:
        r = regime_fn(g, d.date())[0]
        p = pos.loc[d].dropna()
        ok = r in BAND and len(p)
        regimes.append(r if ok else None)
        score.append(float(p.mean()) / 100 if ok else np.nan)
    inband = cond_pct(np.array(score), regimes)
    rows = []
    for i, d in enumerate(dates):
        if regimes[i] is None or np.isnan(inband[i]):
            continue
        if keep_from is not None and d < keep_from:
            continue
        p = pos.loc[d].dropna()
        rows.append({"d": d, "regime": regimes[i], "pos": float(inband[i]),
                     "score": round(score[i] * 100, 1), "t": place(regimes[i], inband[i]),
                     "parts": {k: round(float(v), 1) for k, v in p.items()}})
    return rows


def forward_stats(hist: list[dict], px: pd.Series, horizons=(5, 20)) -> dict:
    """温度ごとの「その日の終値から n 営業日後の終値まで」の騰落（%）を集める。
    px は終値の Series（日付の索引）。px に無い日・先が足りない日は数えない"""
    idx = px.index
    pos_of = {d: i for i, d in enumerate(idx)}
    out = {}
    for h in hist:
        i = pos_of.get(pd.Timestamp(h["d"]))
        r = {}
        for n in horizons:
            if i is None or i + n >= len(idx):
                r[n] = None
            else:
                r[n] = (float(px.iloc[i + n]) / float(px.iloc[i]) - 1) * 100
        out[h["d"]] = r
    return out


def summarize(days: list[str], fwd: dict, all_days: list[str], horizons=(5, 20),
              whole: bool = False) -> dict:
    """件数・かたまりの数（続いた日を1つと数える）・平均・中央値・上がった割合"""
    s = {"n_days": len(days), "episodes": _episodes(days, all_days)}
    for n in horizons:
        v = [fwd[d][n] for d in days if d in fwd and fwd[d][n] is not None]
        s[f"r{n}"] = {
            "n": len(v),
            "avg": round(float(np.mean(v)), 2) if v else None,
            "med": round(float(np.median(v)), 2) if v else None,
            "up_pct": round(sum(1 for x in v if x > 0) / len(v) * 100, 1) if v else None,
        }
    s["sankou"] = bool((not whole and s["episodes"] < 5) or s["r20"]["n"] < 30)
    return s


def _episodes(days: list[str], all_days: list[str]) -> int:
    """続いた日（履歴の並びで隣り合う日）を1つのかたまりとして数える"""
    order = {d: i for i, d in enumerate(all_days)}
    ix = sorted(order[d] for d in days if d in order)
    return sum(1 for j, i in enumerate(ix) if j == 0 or i != ix[j - 1] + 1)


def lookback(hist: list[dict], n: int):
    """n 営業日前（履歴の並びで数える）の行。足りなければ None"""
    return hist[-1 - n] if len(hist) > n else None


def text_ok(s: str) -> list[str]:
    return [w for w in NG_WORDS if w in s]


# ────────────────────────── データの取得 ──────────────────────────
def dl(ticker, period=FETCH_PERIOD, adjust=False, fill=True):
    import yfinance as yf
    df = yf.download(ticker, period=period, progress=False, auto_adjust=adjust, threads=False)
    if df is None or not len(df):
        raise RuntimeError(f"{ticker}: 取得できず")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.index = pd.to_datetime(df.index).tz_localize(None) if df.index.tz is not None else pd.to_datetime(df.index)
    df.index = df.index.normalize()
    df = df[~df.index.duplicated(keep="last")].sort_index()
    if fill:
        # 最新日の終値が空欄で返ることがある（yf_latest 参照）。立会いが終わっていれば確定値で埋める
        from yf_latest import fill_latest
        c = fill_latest(ticker, df["Close"], verbose=False)
        df = df.loc[df.index.isin(c.index)].copy()
        df["Close"] = c.reindex(df.index)
    return df.dropna(subset=["Close"])


def topix_etf():
    """1306.T（TOPIX連動ETF・分割調整済み）。Yahoo の桁違いの足は verify_radar.py と同じ基準で落とす"""
    t = dl("1306.T", adjust=True, fill=False)
    med = t["Close"].rolling(11, center=True, min_periods=3).median()
    bad = (t["Close"] / med - 1).abs() > 0.30
    return t.loc[~bad, "Close"]


# ────────────────────────── 組み立て ──────────────────────────
def build(g: dict, regime_fn, etf: pd.Series | None, now: datetime) -> tuple[dict, dict]:
    n225 = g["^N225"]["Close"].dropna()
    jp_dates = n225.index
    feat = features(g, jp_dates)
    pos = positions(feat)

    start = jp_dates[-1] - pd.Timedelta(days=int(365.25 * HIST_YEARS))
    # 判定は200日線が引ける日から（それより前は判定がデータ不足の扱いになる）
    calc = jp_dates[199:] if len(jp_dates) > 199 else jp_dates
    rows = build_rows(g, regime_fn, calc, pos, keep_from=start)
    if not rows:
        raise RuntimeError("温度を1日も出せなかった")

    # 履歴（公開の指数値と自作の温度だけ）
    nxt = (n225.shift(-1) / n225 - 1) * 100
    hist = []
    for r in rows:
        d = r["d"]
        nx = nxt.get(d)
        hist.append({"d": str(d.date()), "t": r["t"], "tc": celsius(r["t"]), "r": r["regime"],
                     "c": round(float(n225[d]), 2),
                     "n1": None if nx is None or pd.isna(nx) else round(float(nx), 2)})
    all_days = [h["d"] for h in hist]

    # 極端な日のその後（日経平均・1306）
    fwd_n = forward_stats(hist, n225)
    fwd_e = forward_stats(hist, etf) if etf is not None and len(etf) else None
    groups = [
        ("le20", "温度20以下", [h["d"] for h in hist if h["t"] <= 20]),
        ("ge80", "温度80以上", [h["d"] for h in hist if h["t"] >= 80]),
    ] + [(f"band_{b['regime']}", f"{b['jp']}の帯（{b['lo']}〜{b['hi']}）",
          [h["d"] for h in hist if h["r"] == b["regime"]]) for b in BANDS] + [
        ("all", "全期間", all_days),
    ]
    # 同じ区分の ℃ の表記（区分そのものは 0〜100 のまま）
    label_c = {"le20": f"{c_label(celsius(20))}以下", "ge80": f"{c_label(celsius(80))}以上", "all": "全期間"}
    for b in BANDS_C:
        label_c[f"band_{b['regime']}"] = f"{b['jp']}の帯（{c_label(b['lo'])}〜{c_label(b['hi'])}）"
    stats = {
        "period": [all_days[0], all_days[-1]],
        "how": "温度が出た日の終値から、5営業日後・20営業日後の終値までの騰落（%）。"
               "続いた日は1つのかたまりとして episodes に数える（日ごとの値は期間が重なるので独立ではない）。"
               "手数料・税金は含まない。",
        "note_scale": "日経平均（^N225）で集計。温度計ページの既存の成績表は TOPIX 連動ETF（1306）を"
                      "翌営業日の寄り付きから測っているので、物差しが違う。1306 の同じ集計を etf に並べる"
                      "（終値から終値・分割調整済み）。",
        "sankou_rule": "かたまりが5未満、または20営業日後の件数が30未満なら参考値（sankou=true）",
        "groups": [],
    }
    for key, label, ds in groups:
        g_ = {"key": key, "label": label, "label_c": label_c[key],
              "n225": summarize(ds, fwd_n, all_days, whole=key == "all")}
        if fwd_e is not None:
            g_["etf1306"] = summarize(ds, fwd_e, all_days, whole=key == "all")
        stats["groups"].append(g_)

    # 今日の値と比較
    last, cur = rows[-1], hist[-1]
    prev = lookback(hist, 1)

    def cmp(h):
        return None if h is None else {"d": h["d"], "value": h["t"], "regime": h["r"],
                                       "regime_jp": BAND[h["r"]]["jp"], "diff": cur["t"] - h["t"]}

    yr_from = str((pd.Timestamp(cur["d"]) - pd.Timedelta(days=365)).date())
    yr = [h for h in hist if h["d"] > yr_from]
    hi = max(yr, key=lambda h: (h["t"], h["d"]))
    lo = min(yr, key=lambda h: (h["t"], h["d"]))

    comps = []
    for k, label, unit, sign in INPUTS:
        raw = feat.loc[last["d"], k]
        comps.append({
            "key": k, "label": label, "unit": unit,
            "direction": "高いほど温度が上がる" if sign > 0 else "低いほど温度が上がる",
            "raw": None if pd.isna(raw) else round(float(raw), 2),
            "pos": last["parts"].get(k),     # 過去10年の中の位置（0〜100・温度が上がる向き）
            "weight": round(1 / len(INPUTS), 3) if k in last["parts"] else 0,
        })
    b = BAND[last["regime"]]
    data = {
        "name": NAME,
        "updated": now.isoformat(timespec="seconds"),
        "trade_date": cur["d"],
        "value": cur["t"],
        "regime": cur["r"],
        "regime_jp": b["jp"],
        "band": {"lo": b["lo"], "hi": b["hi"]},
        "position_in_band": round(last["pos"] * 100, 1),
        "overall_position": last["score"],
        "regime_changed": bool(prev is not None and prev["r"] != cur["r"]),
        "prev_regime": prev["r"] if prev else None,
        "compare": {"yesterday": cmp(prev), "week": cmp(lookback(hist, 5)),
                    "month": cmp(lookback(hist, 20))},
        "year": {"from": yr_from, "high": {"d": hi["d"], "value": hi["t"]},
                 "low": {"d": lo["d"], "value": lo["t"]}},
        "components": comps,
        "bands": BANDS,
        "definition": DEFINITION,
        "definition_short": DEFINITION_SHORT,
        "method": {
            "regime": "daily_scanner_v2_8_0.detect_market_regime（温度計の判定そのもの）",
            "position": f"5つの入力それぞれの、その日までの過去{WINDOW}営業日（約10年）の中の位置を平均し（overall_position）、"
                        "それを同じ期間の同じ判定の日の中での位置にしたもの（position_in_band）",
            "temperature": "帯の下限 + 位置 ×（帯の上限 − 下限）を四捨五入",
            "lookback": "昨日=1営業日前・1週間前=5営業日前・1か月前=20営業日前（履歴の並びで数える）",
            "sources": "Yahoo! Finance の ^N225・^GSPC・^VIX（公開の指数値のみ）",
        },
        "celsius": celsius_block(cur, prev, lookback(hist, 5), lookback(hist, 20), hi, lo, yr_from),
        "history_file": "thermo100_hist.json",
        "history_days": len(hist),
        "stats": stats,
    }
    hist_doc = {
        "name": NAME,
        "updated": data["updated"],
        "fields": {"d": "日付", "t": "温度（0〜100）", "tc": "温度（℃・−20〜60。t × 0.8 − 20 を四捨五入）",
                   "r": "温度計の判定",
                   "c": "日経平均の終値", "n1": "翌営業日の日経平均の騰落（%）"},
        "bands": BANDS,
        "bands_c": BANDS_C,
        "definition_short": DEFINITION_SHORT,
        "definition_c": DEFINITION_C,
        "items": hist,
    }
    return data, hist_doc


def celsius_block(cur: dict, prev, week, month, hi: dict, lo: dict, yr_from: str) -> dict:
    """thermo100.json の celsius（今日・比較・1年の高安・帯の境目を ℃ で）。0〜100 の値は別のキーにそのまま残す"""
    v = celsius(cur["t"])

    def cmp(h):
        if h is None:
            return None
        c = celsius(h["t"])
        return {"d": h["d"], "value": c, "label": c_label(c), "regime_jp": BAND[h["r"]]["jp"], "diff": v - c}

    b = next(x for x in BANDS_C if x["regime"] == cur["r"])
    return {
        "value": v,
        "label": c_label(v),
        "scale": {"min": C_MIN, "max": C_MAX},
        "band": {"lo": b["lo"], "hi": b["hi"]},
        "compare": {"yesterday": cmp(prev), "week": cmp(week), "month": cmp(month)},
        "year": {"from": yr_from,
                 "high": {"d": hi["d"], "value": celsius(hi["t"]), "label": c_label(celsius(hi["t"]))},
                 "low": {"d": lo["d"], "value": celsius(lo["t"]), "label": c_label(celsius(lo["t"]))}},
        "bands": BANDS_C,
        "stats_thresholds": {"le20": celsius(20), "ge80": celsius(80)},
        "formula": "℃ = round(値 × 0.8 − 20)",
        "definition": DEFINITION_C,
    }


def radar_check(data: dict, docs: Path = DOCS) -> dict | None:
    """公開中の radar.json と同じ日なら判定が同じかを残す（違えば気づけるように）"""
    try:
        r = json.loads((docs / "radar.json").read_text(encoding="utf-8"))
    except Exception:
        return None
    day = str(r.get("scanner_timestamp") or r.get("updated") or "")[:10]
    return {"radar_json_regime": r.get("regime"), "radar_json_day": day,
            "same": r.get("regime") == data["regime"] if day == data["trade_date"] else None}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--docs", default=str(DOCS))
    a = ap.parse_args()
    docs = Path(a.docs)

    from daily_scanner_v2_8_0 import detect_market_regime
    print("指数を取得中…", file=sys.stderr)
    g = {t: dl(t) for t in ("^N225", "^GSPC", "^VIX")}
    try:
        etf = topix_etf()
    except Exception as e:
        print(f"  1306.T: 取得できず（日経平均だけで集計）{e}", file=sys.stderr)
        etf = None

    data, hist = build(g, detect_market_regime, etf, datetime.now(JST))
    data["radar_check"] = radar_check(data, docs)

    bad = text_ok(DEFINITION + DEFINITION_SHORT + DEFINITION_C)
    if bad:
        raise SystemExit(f"定義文に使わない言葉: {bad}")

    # 古いデータで上書きしない（Yahoo が古い日までしか返さなかった回）
    out, hp = docs / "thermo100.json", docs / "thermo100_hist.json"
    try:
        old = json.loads(out.read_text(encoding="utf-8")).get("trade_date") or ""
    except Exception:
        old = ""
    print(f"温度 {data['value']}（{data['regime_jp']}） {data['trade_date']} / 履歴 {len(hist['items'])}日"
          f" / 前日から判定が変わった: {data['regime_changed']} / radar.json 照合: {data['radar_check']}")
    for gr in data["stats"]["groups"]:
        s = gr["n225"]
        print(f"  {gr['label']}: {s['n_days']}日（{s['episodes']}かたまり） 20日後 平均{s['r20']['avg']}%"
              f" 上昇{s['r20']['up_pct']}% {'参考' if s['sankou'] else ''}")
    if a.dry_run:
        return
    if old and data["trade_date"] < old:
        print(f"既存（{old}）より古い {data['trade_date']} なので書かない", file=sys.stderr)
        return
    docs.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    hp.write_text(json.dumps(hist, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"書いた: {out.name} / {hp.name}")


if __name__ == "__main__":
    main()
