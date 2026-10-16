# -*- coding: utf-8 -*-
"""次の波（局面の地図）→ docs/wave.json と docs/wave_history.json（株レーダー wave.html が読む）

■ 何を出すか（起動文 `起動文_実務_次の波_2026-10-16.md`）
  1. いまの波の位置：温度計の判定と連続日数／日経・TOPIX(1306.T)の直近高値からの距離と高値日／
     VIX／傾斜計の点灯数／信用買い残の全体の前週比／次の大波
  2. 3つの分岐（上抜け・横ばい・崩れ）と、その引き金（数値）。今日の値と並べて「あと◯」を出す
  3. 過去の似た局面（過去5年）のその後20営業日：分岐の割合・20日後リターンの中央値・最大下落率の中央値
  4. 動き方の型（文章は data/wave_rules.json。本人が直せる）
  5. 答え合わせ：毎日の地図を docs/wave_history.json に追記し、20営業日後に結果を埋める

■ 新しい外部データ源は足さない
  指数（^N225・^GSPC・^VIX・1306.T）は verify.py と同じ yfinance の同じ銘柄。
  温度計の過去の判定は verify.py と同じく daily_scanner_v2_8_0.detect_market_regime() を
  過去の日付で呼び直したもの（＝verify.json の遡及判定と同じ計算。再実装しない）。
  傾斜計の過去の点灯数は verify_crash.json の series を読む。
  今日の値は radar.json・radar_history.json・gauge.json・shinyo.json・event_score.json から。

■ ルールは先に固定する（2026-10-16）。結果を見てから変えない。変えるときは変える前の結果も残す
  しきい値は下の定数と RULE_HISTORY にだけ書く。

■ 用語（先に固定した定義）
  直近高値 = 直近20営業日（当日を含む）の日経平均の終値の最高値。
             このリポジトリで「直近高値」を使っている既存の定義（verify_crash.py の暴落＝直近20営業日の
             高値から-10%）に揃えた。分岐を見る期間（20営業日）とも同じ長さ
  上抜け   = その後20営業日のうちに、日経の終値が判定日の直近高値を上回る
  崩れ     = その後20営業日のうちに、次のどれかが起きる
             ・温度計が BEARISH/PANIC を2営業日連続
             ・日経の終値が判定日の直近高値から-5%以下
             （・傾斜計が3つ以上点灯 … 下の GAUGE_CLAUSE を参照。保留中）
  横ばい   = 20営業日のうちに上の2つのどちらも起きない
  先に起きたほうをその日の結果とする。同じ日に両方起きたら「崩れ」（守る側に倒す）

■ 傾斜計の条項を保留している理由（要判断・2026-10-16）
  起動文の崩れの引き金には「傾斜計が3つ以上点灯」が入っているが、verify_crash.json では
  過去5年の 93% 超の日が3つ以上点灯しており、今日も3つ点灯している。このまま入れると
  ほぼ全ての日が初日に「崩れ」に分類され、分岐の地図として意味を持たない（設計と矛盾）。
  推測で直さず、この条項だけ判定から外して表示上「保留中」とし、Fable の判断を待つ。
  条項を入れた場合の結果も similar.alt_with_gauge に残す（変える前の結果を残す作法）。
"""
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))
from daily_scanner_v2_8_0 import detect_market_regime  # noqa: E402
from verify import dl  # noqa: E402  verify.py と同じ取り方
from jp_bizday import is_bizday  # noqa: E402

DOCS = REPO / "docs"
OUT = DOCS / "wave.json"
HIST = DOCS / "wave_history.json"
RULES = REPO / "data" / "wave_rules.json"
JST = timezone(timedelta(hours=9))

# ───────────── 先に固定したルール（2026-10-16）。結果を見てから変えない
YEARS = 5              # 似た局面を探す期間
HIGH_WIN = 20          # 直近高値を見る営業日数（当日を含む）
HORIZON = 20           # 分岐を見る営業日数
DOWN_PCT = -5.0        # 崩れ：直近高値からの下げ（%）
BEAR_RUN = 2           # 崩れ：温度計 BEARISH/PANIC の連続営業日数
GAUGE_MIN = 3          # 崩れ：傾斜計の点灯数（保留中）
GAUGE_CLAUSE = False   # 傾斜計の条項を判定に使うか（False＝保留中・要判断）
SPACING = 20           # 同じ局面が続くときは20営業日おきに1件
N_HIDE = 10            # これ未満は数字を出さない
N_SANKOU = 30          # これ未満は「参考値」
CUT = "2026-05-24"     # 作った期間と確かめる期間の境目（温度計の判定条件が最後に変わった日・verify.py と同じ）
BEAR = {"BEARISH", "PANIC"}

STREAK_BANDS = [("1–5日", 1, 5), ("6–20日", 6, 20), ("21日以上", 21, 10 ** 9)]
DIST_BANDS = [("0〜−2%", -2.0), ("−2〜−5%", -5.0), ("−5〜−10%", -10.0), ("−10%超", None)]
VIX_BANDS = [("15未満", 15), ("15–20", 20), ("20–30", 30.0001), ("30超", None)]
BIG_KINDS = {"boj", "fomc", "cpi", "payroll", "sq", "topix"}   # events.js の「次の大波」と同じ種類

RULE_HISTORY = [
    {"d": "2026-10-16", "change": "初期値で固定（起動文の値）。直近高値は直近20営業日の終値の最高値とした。"
                                  "傾斜計3つ以上の条項は、過去5年の9割超の日が該当するため判定から外し保留（要判断）"},
]

BRANCH_JP = {"up": "上抜け", "flat": "横ばい", "down": "崩れ"}
BANNED = ["買い", "売り", "買え", "売れ"]


def band_of(bands, v, kind):
    if v is None:
        return None
    if kind == "streak":
        for lab, lo, hi in bands:
            if lo <= v <= hi:
                return lab
        return None
    if kind == "dist":            # v は 0 以下の%
        for lab, lim in bands:
            if lim is None or v > lim:
                return lab
    if kind == "vix":
        for lab, lim in bands:
            if lim is None or v < lim:
                return lab
    return None


def jload(p, default=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return default


def r2(v, n=2):
    return None if v is None or pd.isna(v) else round(float(v), n)


# ───────────── 過去の局面
def history_frame():
    """日経の日次（終値・始値）＋ その日の温度計の遡及判定・連続日数・高値からの距離・VIX"""
    g = {t: dl(t) for t in ("^N225", "^GSPC", "^VIX")}
    n = g["^N225"]
    now = datetime.now(JST)
    # 手動で場中に回したとき、当日の途中の足を終値として使わない
    if len(n) and n.index[-1].date() == now.date() and (now.hour, now.minute) < (15, 45):
        n = n.iloc[:-1]
        g["^N225"] = n
    df = pd.DataFrame({"open": n["Open"].astype(float), "close": n["Close"].astype(float)})
    df["high"] = df["close"].rolling(HIGH_WIN, min_periods=1).max()
    # 高値日
    hd = []
    cl = df["close"].tolist()
    for i in range(len(cl)):
        lo = max(0, i - HIGH_WIN + 1)
        w = cl[lo:i + 1]
        hd.append(df.index[lo + max(range(len(w)), key=lambda k: (w[k], k))])
    df["high_d"] = hd
    df["dist"] = (df["close"] / df["high"] - 1) * 100

    # 温度計（遡及）。MA200 が出る日から
    days = df.index[200:]
    reg = {}
    for d in days:
        reg[d] = detect_market_regime(g, d.date())[0]
    df["regime"] = pd.Series(reg)
    # 連続日数（同じ判定が何日続いているか・当日を含む）
    st, prev, run = [], None, 0
    for r in df["regime"]:
        if r is None or pd.isna(r):
            run, prev = 0, None
        elif r == prev:
            run += 1
        else:
            run, prev = 1, r
        st.append(run)
    df["streak"] = st
    # VIX：その日までに出ていた最後の終値（日本の夕方の時点で、米国の当日の終値はまだ無い）
    vx = g["^VIX"]["Close"].astype(float)
    vals = []
    for d in df.index:
        s = vx[vx.index < d]
        vals.append(float(s.iloc[-1]) if len(s) else None)
    df["vix"] = vals
    return df, g


def gauge_series():
    v = jload(DOCS / "verify_crash.json", {}) or {}
    s = v.get("series") or {}
    if not s.get("d") or not s.get("on"):
        return None
    return pd.Series([int(c) for c in s["on"]], index=pd.to_datetime(s["d"]))


def outcome(df, i, regimes=None, lit=None):
    """判定日 i（位置）からその後 HORIZON 営業日で、どの分岐になったか。
    regimes: 温度計の判定の並び（None なら df の遡及判定）／lit: 点灯数の並び（傾斜計条項を使うときだけ）"""
    if i + HORIZON >= len(df):
        return None
    reg = df["regime"].tolist() if regimes is None else regimes
    up_lv = float(df["high"].iloc[i])
    dn_lv = up_lv * (1 + DOWN_PCT / 100)
    for k in range(1, HORIZON + 1):
        j = i + k
        c = float(df["close"].iloc[j])
        bear = reg[j] in BEAR and reg[j - 1] in BEAR
        gz = lit is not None and lit[j] is not None and lit[j] >= GAUGE_MIN
        if c <= dn_lv or bear or gz:
            return {"res": "down", "k": k, "d": str(df.index[j].date())}
        if c > up_lv:
            return {"res": "up", "k": k, "d": str(df.index[j].date())}
    return {"res": "flat", "k": HORIZON, "d": str(df.index[i + HORIZON].date())}


def fwd(df, i):
    """翌営業日の始値から：20営業日後の終値までのリターンと、その間の最大下落（%）"""
    if i + HORIZON >= len(df):
        return None, None
    o = float(df["open"].iloc[i + 1])
    if not o:
        return None, None
    c20 = float(df["close"].iloc[i + HORIZON])
    lo = float(df["close"].iloc[i + 1:i + HORIZON + 1].min())
    return (c20 / o - 1) * 100, (lo / o - 1) * 100


def similar(df, cond, lit_series=None):
    """条件3つが一致する日を、20営業日おきに1件ずつ拾ってその後を数える"""
    end = df.index[-1]
    start = end - pd.Timedelta(days=int(365.25 * YEARS))
    lit = None
    if lit_series is not None:
        lit = [int(lit_series.get(d)) if d in lit_series.index else None for d in df.index]
    picked, last = [], -10 ** 9
    for i, d in enumerate(df.index):
        if d < start or pd.isna(df["regime"].iloc[i]):
            continue
        if i + HORIZON >= len(df):
            break                    # その後20営業日がまだ無い
        if cond is not None:          # cond=None は比較対象（条件なし＝5年の全日から20営業日おき）
            if df["regime"].iloc[i] != cond["regime"]:
                continue
            if band_of(STREAK_BANDS, int(df["streak"].iloc[i]), "streak") != cond["streak_band"]:
                continue
            if band_of(DIST_BANDS, float(df["dist"].iloc[i]), "dist") != cond["dist_band"]:
                continue
            if band_of(VIX_BANDS, df["vix"].iloc[i], "vix") != cond["vix_band"]:
                continue
        if i - last < SPACING:
            continue
        last = i
        o = outcome(df, i, lit=lit)
        r20, dd20 = fwd(df, i)
        picked.append({"d": str(d.date()), "res": o["res"], "res_d": o["d"], "k": o["k"],
                       "ret20": r2(r20), "dd20": r2(dd20),
                       "dist": r2(df["dist"].iloc[i]), "vix": r2(df["vix"].iloc[i]),
                       "streak": int(df["streak"].iloc[i])})
    return picked


def agg(rows):
    n = len(rows)
    out = {"n": n, "enough": n >= N_HIDE, "sankou": n < N_SANKOU}
    if n < N_HIDE:
        out.update({"up": None, "flat": None, "down": None, "ret20_med": None, "dd20_med": None,
                    "major": None})
        return out
    for b in ("up", "flat", "down"):
        out[b] = round(sum(1 for x in rows if x["res"] == b) / n * 100, 1)
    r = [x["ret20"] for x in rows if x["ret20"] is not None]
    dd = [x["dd20"] for x in rows if x["dd20"] is not None]
    out["ret20_med"] = r2(pd.Series(r).median()) if r else None
    out["dd20_med"] = r2(pd.Series(dd).median()) if dd else None
    top = max(("up", "flat", "down"), key=lambda b: out[b])
    # 同率1位があるときは多数派なし
    out["major"] = top if sum(1 for b in ("up", "flat", "down") if out[b] == out[top]) == 1 else None
    return out


# ───────────── 今日の値
def published_items():
    """温度計の公開した判定の並び（radar_history.json ＋ 当日の radar.json）"""
    h = jload(DOCS / "radar_history.json", {"items": []}) or {"items": []}
    items = list(h.get("items") or [])
    rd = jload(DOCS / "radar.json", {}) or {}
    today = (rd.get("updated") or "")[:10]
    if today and rd.get("regime") and (not items or items[-1]["d"] != today):
        items.append({"d": today, "regime": rd["regime"], "vix": rd.get("vix"), "n225": rd.get("n225")})
    return items, rd


def topix_pos():
    t = dl("1306.T", adjust=True)
    med = t["Close"].rolling(11, center=True, min_periods=3).median()
    t = t[~((t["Close"] / med - 1).abs() > 0.30)]       # verify.py と同じ異常足の除外
    c = t["Close"].astype(float)
    w = c.iloc[-HIGH_WIN:]
    hi = float(w.max())
    return {"name": "TOPIX（1306.T で代用）", "close": r2(c.iloc[-1]), "high": r2(hi),
            "high_d": str(w.idxmax().date()), "dist": r2((float(c.iloc[-1]) / hi - 1) * 100),
            "asof": str(c.index[-1].date())}


def next_event(now):
    es = jload(DOCS / "event_score.json", {}) or {}
    for e in es.get("schedule") or []:
        if e.get("k") not in BIG_KINDS:
            continue
        try:
            dt = datetime.fromisoformat(e["iso"])
        except Exception:
            continue
        if dt > now:
            days = (dt.astimezone(JST).date() - now.date()).days
            return {"t": e.get("t"), "iso": e["iso"], "time": e.get("time"), "days": days}
    return None


def shinyo_pos():
    s = jload(DOCS / "shinyo.json", {}) or {}
    b, c = s.get("buy_oku"), s.get("buy_chg_oku")
    if b is None or c is None or not (b - c):
        return None
    return {"buy_oku": b, "chg_oku": c, "chg_pct": round(c / (b - c) * 100, 2), "asof": s.get("date")}


def load_rules():
    r = jload(RULES, {}) or {}
    types = r.get("types") or {}
    bad = []
    for k, lines in types.items():
        for ln in lines:
            for w in BANNED:
                if w in ln:
                    bad.append((k, w, ln))
            if re.search(r"\d{4}", ln):
                bad.append((k, "4桁の数字（銘柄コード?）", ln))
    if bad:
        for x in bad:
            print(f"::error::動き方の型に使えない語: {x}", file=sys.stderr)
        # 型だけ出さない（数字の部分は出す）
        types = {k: [] for k in types}
    return {"types": {k: list(v)[:3] for k, v in types.items()},
            "link": r.get("link") or {}, "note": r.get("note")}


# ───────────── 答え合わせ（追記のみ）
SCORE_KEYS = {"res", "res_d", "res_k", "hit", "res_note"}


def score_history(hist, df, pub):
    pos = {d.date().isoformat(): i for i, d in enumerate(df.index)}
    reg_by_d = {x["d"]: x.get("regime") for x in pub}
    regs = [reg_by_d.get(str(d.date())) for d in df.index]
    changed = 0
    for row in hist:
        if row.get("res") is not None:
            continue
        i = pos.get(row["d"])
        if i is None or i + HORIZON >= len(df):
            continue                       # 20営業日たっていない
        # 判定日に出していた水準そのもので採点する（あとから計算し直さない）
        up_lv, dn_lv = row["up_level"], row["down_level"]
        res = None
        for k in range(1, HORIZON + 1):
            j = i + k
            c = float(df["close"].iloc[j])
            bear = regs[j] in BEAR and regs[j - 1] in BEAR
            if c <= dn_lv or bear:
                res = ("down", k, str(df.index[j].date()))
                break
            if c > up_lv:
                res = ("up", k, str(df.index[j].date()))
                break
        if res is None:
            res = ("flat", HORIZON, str(df.index[i + HORIZON].date()))
        row["res"], row["res_k"], row["res_d"] = res
        major = (row.get("sim") or {}).get("major")
        row["hit"] = (major == res[0]) if major else None
        if any(regs[i + k] is None for k in range(0, HORIZON + 1)):
            row["res_note"] = "温度計の公開記録が欠けた日を含む（欠けた日は BEARISH/PANIC でない扱い）"
        changed += 1
    return changed


def record_of(hist):
    scored = [r for r in hist if r.get("res") is not None]
    judged = [r for r in scored if r.get("hit") is not None]
    hits = sum(1 for r in judged if r["hit"])
    by = {}
    for b in ("up", "flat", "down"):
        xs = [r for r in judged if r["sim"]["major"] == b]
        by[b] = {"n": len(xs), "hit": sum(1 for r in xs if r["hit"])}
    return {"days": len(hist), "scored": len(scored), "judged": len(judged), "hits": hits,
            "pct": round(hits / len(judged) * 100, 1) if judged else None,
            "by_major": by,
            "res_count": {b: sum(1 for r in scored if r["res"] == b) for b in ("up", "flat", "down")},
            "rows": [{"d": r["d"], "major": r["sim"].get("major"), "n": r["sim"].get("n"),
                      "res": r["res"], "res_d": r["res_d"], "hit": r["hit"]} for r in scored]}


def main():
    now = datetime.now(JST)
    print("指数を取得中…", file=sys.stderr)
    df, g = history_frame()
    pub, rd = published_items()
    if not rd.get("regime"):
        sys.exit("::error::radar.json が読めない")
    asof = str(df.index[-1].date())

    # ── 1. いまの波の位置
    # 温度計は公開した判定（radar.html と同じ数え方）
    # 連続日数は「営業日」で数える（過去の似た局面も営業日で数えているので、同じ物差しにする）。
    # radar_history には休場日にも記録が入る（例: 2026-09-23 秋分の日に NEUTRAL）。休場日の記録は数えない。
    # 温度計ページ（radar.html）の「◯日連続」は休場日の記録も数えるので、そちらの数も残す
    regime = rd["regime"]
    pub_biz = [x for x in pub if is_bizday(date.fromisoformat(x["d"]))]
    streak = 0
    for x in reversed(pub_biz):
        if x.get("regime") != regime:
            break
        streak += 1
    streak_radar = 0
    for x in reversed(pub):
        if x.get("regime") != regime:
            break
        streak_radar += 1
    last = df.iloc[-1]
    close, high = float(last["close"]), float(last["high"])
    dist = (close / high - 1) * 100
    vix = rd.get("vix")
    gz = jload(DOCS / "gauge.json", {}) or {}
    lit, total = gz.get("lit"), gz.get("total", 5)
    pos = {
        "regime": regime, "streak": streak, "streak_radar": streak_radar,
        "streak_replay": int(df["streak"].iloc[-1]) if df["regime"].iloc[-1] == regime else 0,
        "regime_replay": df["regime"].iloc[-1],
        "regime_asof": (rd.get("updated") or "")[:10],
        "n225": {"close": r2(close), "high": r2(high), "high_d": str(last["high_d"].date()),
                 "dist": r2(dist), "asof": asof, "radar_n225": rd.get("n225")},
        "topix": None,
        "vix": vix,
        "gauge": {"lit": lit, "total": total, "asof": (gz.get("updated") or "")[:10]},
        "shinyo": shinyo_pos(),
        "next_event": next_event(now),
    }
    try:
        pos["topix"] = topix_pos()
    except Exception as e:
        print(f"  1306.T を取れない: {e}", file=sys.stderr)

    cond = {"regime": regime,
            "streak_band": band_of(STREAK_BANDS, streak, "streak"),
            "dist_band": band_of(DIST_BANDS, dist, "dist"),
            "vix_band": band_of(VIX_BANDS, vix, "vix")}

    # ── 2. 3つの分岐と引き金（今日の値と並べる）
    up_lv = high
    dn_lv = high * (1 + DOWN_PCT / 100)
    last_bear = 1 if regime in BEAR else 0
    bear_need = 0 if (last_bear and len(pub_biz) > 1 and pub_biz[-2].get("regime") in BEAR) else (BEAR_RUN - last_bear)
    rules = load_rules()
    hdt = last["high_d"]
    branches = [
        {"key": "up", "label": "上抜け",
         "triggers": [{"text": f"日経平均が直近高値 {up_lv:,.0f}円（{hdt.month}/{hdt.day}）を終値で上回る",
                       "level": r2(up_lv), "today": r2(close), "gap": r2(up_lv - close), "unit": "円",
                       "gap_pct": r2((up_lv / close - 1) * 100), "met": close > up_lv}]},
        {"key": "flat", "label": "横ばい",
         "triggers": [{"text": f"{HORIZON}営業日のうちに、上抜けも崩れも起きない", "level": None,
                       "today": None, "gap": None, "unit": None, "met": False}]},
        {"key": "down", "label": "崩れ",
         "triggers": [
             {"text": f"温度計が BEARISH/PANIC を{BEAR_RUN}営業日連続", "level": BEAR_RUN,
              "today": regime, "gap": bear_need, "unit": "営業日", "met": bear_need == 0},
             {"text": f"日経平均が直近高値から{DOWN_PCT:.0f}%（{dn_lv:,.0f}円）を終値で下回る",
              "level": r2(dn_lv), "today": r2(close), "gap": r2(close - dn_lv), "unit": "円",
              "gap_pct": r2((dn_lv / close - 1) * 100), "met": close <= dn_lv},
             {"text": f"傾斜計が{GAUGE_MIN}つ以上点灯", "level": GAUGE_MIN, "today": lit,
              "gap": (max(0, GAUGE_MIN - lit) if lit is not None else None), "unit": "つ",
              "met": lit is not None and lit >= GAUGE_MIN,
              "hold": not GAUGE_CLAUSE,
              "hold_note": "過去5年の9割超の日が3つ以上点灯しているため、引き金に数えるかを確認中です（いまは分岐の判定に使っていません）"},
         ]},
    ]
    for b in branches:
        b["type"] = rules["types"].get(b["key"], [])

    # ── 3. 過去の似た局面
    rows = similar(df, cond)
    cut = CUT
    built = [x for x in rows if x["d"] < cut]
    tested = [x for x in rows if x["d"] >= cut]
    lit_s = gauge_series()
    alt = None
    if lit_s is not None:
        arows = similar(df, cond, lit_series=lit_s)
        alt = {"note": "傾斜計3つ以上の条項を崩れに入れた場合（起動文どおり）。参考に残す",
               "whole": agg(arows),
               "gauge_series_period": [str(lit_s.index[0].date()), str(lit_s.index[-1].date())]}
    sim = {"cond": cond, "cut": cut,
           "whole": agg(rows), "built": agg(built), "tested": agg(tested),
           # 比較対象：条件をつけずに5年の全日から20営業日おきに拾った場合
           "base": agg(similar(df, None)),
           "cases": rows, "alt_with_gauge": alt,
           "period": [str((df.index[-1] - pd.Timedelta(days=int(365.25 * YEARS))).date()), asof]}

    # ── 5. 答え合わせ（追記のみ）
    hist = (jload(HIST, {"items": []}) or {"items": []}).get("items", [])
    orig = json.loads(json.dumps(hist))
    changed = score_history(hist, df, pub)
    if not any(r["d"] == asof for r in hist):
        hist.append({
            "d": asof, "regime": regime, "streak": streak, "n225": r2(close), "high": r2(high),
            "dist": r2(dist), "vix": vix, "lit": lit,
            "up_level": r2(up_lv), "down_level": r2(dn_lv), "bear_need": bear_need,
            "cond": cond,
            "sim": {k: sim["whole"][k] for k in ("n", "up", "flat", "down", "major")},
            "res": None, "res_d": None, "res_k": None, "hit": None,
        })
        print(f"wave_history.json: {asof} を追記")
    else:
        print(f"wave_history.json: {asof} は記録済み（追記しない）")
    # 採点の欄以外が変わっていないことを確かめてから書く
    if len(hist) < len(orig):
        sys.exit("::error::wave_history.json の行が減った。書き込みを止めます")
    for a, b in zip(orig, hist):
        if {k: v for k, v in a.items() if k not in SCORE_KEYS} != \
           {k: v for k, v in b.items() if k not in SCORE_KEYS}:
            sys.exit(f"::error::{a.get('d')} の採点以外の欄が変わった。書き込みを止めます")
    HIST.write_text(json.dumps({"items": hist}, ensure_ascii=False, separators=(",", ":")),
                    encoding="utf-8")

    out = {
        "updated": now.isoformat(timespec="seconds"),
        "asof": asof,
        "position": pos,
        "branches": branches,
        "similar": sim,
        "record": record_of(hist),
        "rules": {
            "high_win": HIGH_WIN, "horizon": HORIZON, "down_pct": DOWN_PCT, "bear_run": BEAR_RUN,
            "gauge_min": GAUGE_MIN, "gauge_clause": GAUGE_CLAUSE, "spacing": SPACING,
            "n_hide": N_HIDE, "n_sankou": N_SANKOU, "years": YEARS,
            "streak_bands": [x[0] for x in STREAK_BANDS], "dist_bands": [x[0] for x in DIST_BANDS],
            "vix_bands": [x[0] for x in VIX_BANDS],
            "fixed_on": RULE_HISTORY[0]["d"], "history": RULE_HISTORY,
        },
        "link": rules["link"],
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    w = sim["whole"]
    print(f"wave.json: {asof} {regime} {streak}日 / 高値から {dist:+.2f}% / VIX {vix} / "
          f"似た局面 n={w['n']}（作った期間 {sim['built']['n']}・確かめる期間 {sim['tested']['n']}）"
          f" 上{w['up']} 横{w['flat']} 下{w['down']} / 採点を更新 {changed}件")


if __name__ == "__main__":
    main()
