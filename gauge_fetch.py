# -*- coding: utf-8 -*-
"""5つの傾斜計 → docs/gauge.json（株レーダー kaburadar.jp）

運営者の著書『暴落は、減衰する』(ASIN B0H8HHC16H) 第2章・第5章の
「5つの傾斜計」を毎日自動採点する。閾値はすべて本の記述に合わせている。

  ① 逆イールド     … 最も再現性が高い。ただし天井まで半年〜1年の時間差がある
  ② 割高（CAPE）   … 長期平均17・中央値16。25超で割高。史上最高は2000年の約44
  ③ 信用の膨張     … 借金で買われた相場は下落に弱い。前年比で急増＝投機的過熱
  ④ 市場の過熱     … 騰落レシオ120超で過熱／70割れで底値圏。天井は苦手・底は得意
  ⑤ 金融の引き締め … 単独では鈍いが、他の4つと組み合わさると鋭くなる

本の判定基準（第2章）:
  1〜2個 … まだ様子見でいい
  4〜5個 … 無視できない警告。砂山は崩れる寸前まで育っている

重要な前提（第5章）:
  点灯は「今すぐ逃げろ」ではなく「備える時」。逆イールドは天井まで半年〜1年、
  CAPEはタイミングを教えない。前兆は行動のきっかけではなく、行動の確率を上げるもの。

■ 取得できなかった傾斜計は「消灯」にせず未判定として扱う
  データが取れないことを「安全」と表示するのが、いちばん危険な嘘になるため。
"""
import json
import re
import sys
import urllib.request
from datetime import datetime, timezone, timedelta
from io import StringIO
from pathlib import Path

import pandas as pd

JST = timezone(timedelta(hours=9))
OUT = Path(__file__).parent / "docs" / "gauge.json"
UA = {"User-Agent": "Mozilla/5.0 (compatible; kaburadar.jp/1.0)"}

FRED = "https://fred.stlouisfed.org/graph/fredgraph.csv?id="
# 本の基準値
CAPE_HIGH = 25.0       # これを超えると一般に割高（本 第2章）
CAPE_AVG = 17.0        # 150年の長期平均
ADR_HOT = 120.0        # 騰落レシオ 過熱
ADR_COLD = 70.0        # 騰落レシオ 底値圏
MARGIN_YOY = 10.0      # 信用の膨張とみなす前年比(%)
POST_INVERSION_M = 18  # 逆イールド解消後も警戒を続ける月数（発生→景気後退入り平均1年半）


def fred(series):
    """FRED の日次/月次CSVを Series で返す"""
    req = urllib.request.Request(FRED + series, headers=UA)
    with urllib.request.urlopen(req, timeout=45) as r:
        txt = r.read().decode("utf-8")
    d = pd.read_csv(StringIO(txt))
    d.columns = ["date", "v"]
    d["date"] = pd.to_datetime(d["date"])
    d["v"] = pd.to_numeric(d["v"], errors="coerce")
    return d.dropna().set_index("date")["v"]


def g(key, no, label, on, value, criterion, detail, asof, source, book):
    return {"key": key, "no": no, "label": label, "on": on, "value": value,
            "criterion": criterion, "detail": detail, "asof": asof,
            "source": source, "book": book}


def fail(key, no, label, criterion, book, why):
    return {"key": key, "no": no, "label": label, "on": None, "value": "取得失敗",
            "criterion": criterion, "detail": why, "asof": None,
            "source": None, "book": book}


# ─── ① 逆イールド ────────────────────────────────────────────
def gauge_yield():
    BOOK = ("5つのなかで最も再現性が高い。ただし灯ってから天井までは半年〜1年ある。"
            "「サインが出た、すぐ逃げろ」ではなく「そろそろ準備を始めろ」。"
            "解消は危険が去った合図ではなく、2000年も2007年も解消後に本格的な下落が始まった。")
    CRIT = f"逆転中（10年−2年がマイナス）または解消から{POST_INVERSION_M}ヶ月以内"
    try:
        s = fred("T10Y2Y")
    except Exception as e:
        return fail("yield", 1, "逆イールド", CRIT, BOOK, f"FRED T10Y2Y: {e}")

    cur = float(s.iloc[-1])
    asof = s.index[-1].strftime("%Y-%m-%d")
    inverted = cur < 0
    # 直近の逆転がいつまで続いていたか
    inv = s[s < 0]
    last_inv = inv.index[-1] if len(inv) else None
    months = None
    if last_inv is not None:
        months = (s.index[-1] - last_inv).days / 30.44

    if inverted:
        on, val, det = True, f"{cur:+.2f}%", "現在も逆転中。景気後退を先読みするサインが点灯している。"
    elif months is not None and months <= POST_INVERSION_M:
        on = True
        val = f"{cur:+.2f}%"
        det = (f"逆転は解消済み（最後の逆転は {last_inv:%Y年%m月}、{months:.0f}ヶ月前）。"
               f"ただし解消後こそカウントダウンの開始とされる期間内。")
    else:
        on = False
        val = f"{cur:+.2f}%"
        det = (f"順イールド。最後の逆転から{months:.0f}ヶ月経過し、警戒期間（{POST_INVERSION_M}ヶ月）を過ぎた。"
               if months is not None else "順イールド。記録上、逆転はない。")
    return g("yield", 1, "逆イールド", on, val, CRIT, det, asof,
             "FRED（米10年債−2年債スプレッド T10Y2Y）", BOOK)


# ─── ② 割高（CAPE） ──────────────────────────────────────────
def gauge_cape():
    BOOK = (f"CAPE（シラーPER）は過去10年の平均利益を物価補正して使うため、"
            f"1年の景気の波にごまかされない。150年の長期平均は約{CAPE_AVG:.0f}、"
            f"{CAPE_HIGH:.0f}超で一般に割高。史上最高は2000年ITバブルの約44倍で、"
            f"直後に約78%の暴落が来た。ただしCAPEは「いつ」を教えない。")
    CRIT = f"{CAPE_HIGH:.0f}超"
    try:
        req = urllib.request.Request("https://www.multpl.com/shiller-pe", headers=UA)
        with urllib.request.urlopen(req, timeout=45) as r:
            html = r.read().decode("utf-8", "replace")
        m = re.search(r"Current Shiller PE Ratio is\s*([0-9]+\.?[0-9]*)", html)
        if not m:
            raise ValueError("ページ構造が変わった可能性（数値を見つけられない）")
        cape = float(m.group(1))
    except Exception as e:
        return fail("cape", 2, "割高（CAPE）", CRIT, BOOK, f"multpl.com: {e}")

    on = cape > CAPE_HIGH
    det = (f"長期平均{CAPE_AVG:.0f}の約{cape/CAPE_AVG:.1f}倍。"
           + ("観測史上、40を超えたのは2000年のITバブルだけ。" if cape >= 40 else
              "割高圏だが、割高なまま何年も上がり続けることはある。" if on else
              "長期平均に近い水準。"))
    return g("cape", 2, "割高（CAPE）", on, f"{cape:.1f}倍", CRIT, det,
             datetime.now(JST).strftime("%Y-%m-%d"), "multpl.com（S&P500 シラーPER）", BOOK)


# ─── ③ 信用の膨張 ────────────────────────────────────────────
def gauge_margin():
    BOOK = ("借金で買った株は下落に弱い。下がると追証を求められ、応じられなければ強制的に売られる。"
            "その売りがさらに株価を下げ、連鎖する。米国では証拠金債務（マージンデット）で量を見る。"
            "前年比で急増していれば投機的な過熱のサイン。")
    CRIT = f"証拠金債務が前年比 +{MARGIN_YOY:.0f}%以上"
    try:
        s = fred("BOGZ1FL663067003Q")  # 米ブローカーの証拠金債務（四半期）
    except Exception as e:
        return fail("margin", 3, "信用の膨張", CRIT, BOOK, f"FRED: {e}")
    if len(s) < 5:
        return fail("margin", 3, "信用の膨張", CRIT, BOOK, "データが4四半期分に足りない")

    cur, prev = float(s.iloc[-1]), float(s.iloc[-5])
    yoy = (cur / prev - 1) * 100
    asof = s.index[-1].strftime("%Y-%m-%d")
    lag = (datetime.now(JST).date() - s.index[-1].date()).days
    on = yoy >= MARGIN_YOY
    det = (f"残高 {cur/1000:,.0f}十億ドル。"
           + ("借金による買いが急増しており、下落時に強制売りの連鎖が起きやすい。"
              if on else "前年比の伸びは急増の水準に届いていない。")
           + f"（四半期データのため{lag}日前の数字）")
    return g("margin", 3, "信用の膨張", on, f"前年比 {yoy:+.1f}%", CRIT, det, asof,
             "FRED（米ブローカー証拠金債務・四半期）", BOOK)


# ─── 市場データ（④とステージ判定で共用。341銘柄を2度落とさないため）───
_MD = {}


def market_data():
    """(騰落レシオ系列, 日経終値系列, VIX系列, 銘柄数) を返す。1回だけ取得してキャッシュ"""
    if _MD:
        return _MD["adr"], _MD["n225"], _MD["vix"], _MD["n"]
    import yfinance as yf
    from daily_scanner_v2_8_0 import STOCKS
    tickers = list(STOCKS.keys())
    raw = yf.download(tickers, period="1y", progress=False,
                      auto_adjust=False, threads=True)["Close"].dropna(how="all")
    chg = raw.diff()
    up, dn = (chg > 0).sum(axis=1), (chg < 0).sum(axis=1)
    # 騰落レシオ = 25日間の値上がり銘柄数合計 / 値下がり銘柄数合計 × 100
    adr = (up.rolling(25).sum() / dn.rolling(25).sum() * 100).dropna()

    def series(t, period):
        d = yf.download(t, period=period, progress=False, auto_adjust=False)["Close"].dropna()
        return d.iloc[:, 0] if isinstance(d, pd.DataFrame) else d

    _MD.update(adr=adr, n225=series("^N225", "2y"), vix=series("^VIX", "2y"),
               n=raw.shape[1])
    return _MD["adr"], _MD["n225"], _MD["vix"], _MD["n"]


# ─── ④ 市場の過熱（騰落レシオ＋ワニの口）─────────────────────
def gauge_overheat():
    BOOK = (f"騰落レシオは値上がり銘柄数と値下がり銘柄数の比率。{ADR_HOT:.0f}超で過熱、"
            f"{ADR_COLD:.0f}割れで底値圏。ただし底を当てるのは得意だが天井を当てるのは苦手。"
            "指数が最高値を更新する一方で騰落レシオが低い形を「ワニの口」と呼ぶ。"
            "一部の銘柄だけが指数を押し上げ、市場の土台が指数の高さに追いついていない状態。")
    CRIT = f"騰落レシオ{ADR_HOT:.0f}超（過熱）、または「ワニの口」（指数が52週高値の5%以内 かつ 騰落レシオ100未満）"
    try:
        adr, n225, _vix, ntk = market_data()
        if adr.empty:
            raise ValueError("25日分のデータが揃わない")
        cur_adr = float(adr.iloc[-1])
        y1 = n225[n225.index >= n225.index[-1] - pd.Timedelta(days=365)]
        n, hi = float(y1.iloc[-1]), float(y1.max())
        from_hi = n / hi - 1
    except Exception as e:
        return fail("overheat", 4, "市場の過熱", CRIT, BOOK, f"{type(e).__name__}: {e}")

    hot = cur_adr > ADR_HOT
    croc = (from_hi >= -0.05) and (cur_adr < 100)
    on = hot or croc
    if hot:
        det = f"騰落レシオ{cur_adr:.0f}は過熱水域。ただし天井の時期までは読めない。"
    elif croc:
        det = (f"「ワニの口」。日経は52週高値から{from_hi:+.1%}の高値圏にいるのに、"
               f"騰落レシオは{cur_adr:.0f}と幅が伴っていない。一部の銘柄が指数を押し上げている構図。")
    else:
        det = (f"騰落レシオ{cur_adr:.0f}、日経は52週高値から{from_hi:+.1%}。"
               + ("底値圏の水域にある。" if cur_adr < ADR_COLD else "過熱もワニの口も出ていない。"))
    return g("overheat", 4, "市場の過熱", on, f"騰落レシオ {cur_adr:.0f}", CRIT, det,
             datetime.now(JST).strftime("%Y-%m-%d"),
             f"自前計算（監視{ntk}銘柄の25日騰落レシオ）＋日経52週高値", BOOK)


# ─── ⑤ 金融の引き締め ────────────────────────────────────────
def gauge_tightening():
    BOOK = ("相場を動かす最も根源的な力はお金の量。中央銀行が蛇口を締めれば株を買う力は弱まる。"
            "1929年の前も2000年の前も利上げがあり、2024年8月の日本の暴落も引き金は日銀の利上げだった。"
            "この傾斜計は単独では鈍いが、他の4つと組み合わせると鋭くなる。")
    CRIT = "米FF金利または日本の政策金利が、直近12ヶ月で上昇している"
    got, parts = [], []
    for sid, name in (("DFEDTARU", "米FF金利上限"), ("IRSTCI01JPM156N", "日本の政策金利")):
        try:
            s = fred(sid)
            cutoff = s.index[-1] - pd.Timedelta(days=365)
            past = s[s.index <= cutoff]
            if past.empty:
                continue
            cur, old = float(s.iloc[-1]), float(past.iloc[-1])
            got.append((name, cur, cur - old, s.index[-1]))
            parts.append(f"{name} {cur:.2f}%（12ヶ月で{cur-old:+.2f}pt）")
        except Exception:
            continue
    if not got:
        return fail("tighten", 5, "金融の引き締め", CRIT, BOOK, "FREDから政策金利を取得できなかった")

    rising = [x for x in got if x[2] > 0]
    on = len(rising) > 0
    asof = max(x[3] for x in got).strftime("%Y-%m-%d")
    det = "／".join(parts) + "。" + (
        "引き締め方向。積み上がった砂山に、蛇口が締まる圧力がかかっている。" if on
        else "どちらも引き締め方向ではない。強い引き締めのサインは出ていない。")
    return g("tighten", 5, "金融の引き締め", on, ("引き締め" if on else "緩和・据え置き"),
             CRIT, det, asof, "FRED（米FF金利上限・日本の政策金利）", BOOK)


# ─── 暴落のステージ判定（本 第9章）────────────────────────────
# VIXの水準（本 第9章）
VIX_BANDS = [(20, "平時", "市場は落ち着いている"),
             (25, "警戒感", "警戒感が出始めた水準"),
             (30, "乱高下", "相場の乱高下が意識され始める水準"),
             (40, "強い不安", "市場に強い不安が広がっている状態"),
             (999, "パニック", "パニックに近い局面。2008年は約90、2020年は約85まで跳ねた")]
BIG_MOVE = 0.02   # 「大きな値動き」の閾値（余震の数え方）
ACTIVE_DD = -0.08  # 本震とみなすドローダウン
ACTIVE_VIX = 25.0


def vix_band(v):
    for lim, name, desc in VIX_BANDS:
        if v < lim:
            return name, desc
    return VIX_BANDS[-1][1], VIX_BANDS[-1][2]


def compute_stage():
    """暴落局面かを判定し、局面なら初期/中盤/後半を返す。

    本 第9章の要点:
      ・VIXは水準より「向き」が重要。高いだけで底と判断するのが最大の過ち
      ・注目すべきはVIXがピークをつけて低下に転じる瞬間
      ・騰落レシオは底を当てるのが得意。70割れが売られすぎの水域
      ・2つの向きが揃って変わったとき、恐怖は峠を越えたと判断してよい
    """
    try:
        adr, n225, vix, _ = market_data()
    except Exception as e:
        return {"ok": False, "why": f"{type(e).__name__}: {e}"}

    v = float(vix.iloc[-1])
    band, band_desc = vix_band(v)
    a = float(adr.iloc[-1])

    # 直近60営業日の高値からのドローダウン
    win = n225.tail(60)
    peak_idx = win.idxmax()
    peak = float(win.max())
    cur = float(n225.iloc[-1])
    dd = cur / peak - 1

    # VIXの向き: 直近20営業日のピークと、そこからの経過・下落率
    v20 = vix.tail(20)
    vpeak = float(v20.max())
    vpeak_at = v20.idxmax()
    days_since_vpeak = int((vix.index[-1] - vpeak_at).days)
    off_peak = v / vpeak - 1 if vpeak else 0.0
    # 「上昇中」は5%以上の上昇に限る。平常水準の小さな揺れを上昇と取ると、
    # 恐怖が引いたあとの日が「初期」に戻ってしまう（2024/8/20で実際に起きた）
    rising = v > float(vix.iloc[-4]) * 1.05 if len(vix) >= 4 else False
    # VIXが平時圏に戻り、ピークからも大きく下がっていれば、小さな揺れに関係なく後半
    calm_returned = (v < 20) and (off_peak <= -0.30)

    active = (v >= ACTIVE_VIX) or (dd <= ACTIVE_DD)

    # 余震の減衰（本 第6章・大森公式の日足での見方）
    # 本震以降、|前日比|が2%以上の日が週あたり何日出ているかの推移
    aftershocks = []
    shock_at = None
    if active:
        after = n225[n225.index >= peak_idx]
        r = after.pct_change().dropna()
        if len(r):
            shock_at = r.idxmin()          # 最も下げた日を本震とみなす
            post = r[r.index >= shock_at]
            for wk in range(0, min(6, (len(post) + 4) // 5)):
                seg = post.iloc[wk * 5:(wk + 1) * 5]
                if len(seg) == 0:
                    break
                aftershocks.append({
                    "week": wk + 1,
                    "days": int(len(seg)),
                    "big": int((seg.abs() >= BIG_MOVE).sum()),
                    "max": round(float(seg.abs().max()) * 100, 2),
                })

    if not active:
        return {"ok": True, "active": False,
                "vix": round(v, 2), "vix_band": band, "vix_band_desc": band_desc,
                "adr": round(a, 1), "drawdown": round(dd * 100, 1),
                "stage_key": "none", "stage": "暴落局面ではない",
                "message": (f"VIXは{v:.1f}（{band}）、日経は直近60営業日の高値から{dd:+.1%}。"
                            f"本の第9章の道具は、暴落が始まってから使うものです。"
                            f"いま出番はありません。"),
                "action": "平常どおり。傾斜計（燃料）の確認を続ける段階です。"}

    # ステージ判定
    oversold = a < ADR_COLD
    peaked = calm_returned or ((off_peak <= -0.15) and (days_since_vpeak >= 2) and not rising)

    if peaked and a >= ADR_COLD:
        key, name = "late", "後半"
        msg = (f"VIXは直近ピーク{vpeak:.1f}から{off_peak:+.1%}下がり（{days_since_vpeak}日前がピーク）、"
               f"騰落レシオも{a:.0f}と売られすぎの水域から戻ってきています。"
               f"2つの向きが揃って変わった形——本の言葉では、恐怖が峠を越えた局面です。")
        act = "余震が目に見えて減衰し始めた段階。分割で買い向かう本番。ただし本震が二度、三度と連なる局面では単純な読み方は通用しません。"
    elif peaked:
        key, name = "late", "後半"
        msg = (f"VIXは直近ピーク{vpeak:.1f}から{off_peak:+.1%}下がっていますが、"
               f"騰落レシオは{a:.0f}でまだ売られすぎの水域です。恐怖の向きは変わりかけています。")
        act = "2つの向きが揃うのを待つ段階。分割の1回目までなら検討できる局面です。"
    elif oversold or v >= 30:
        key, name = "mid", "中盤"
        msg = (f"VIXは{v:.1f}（{band}）で高止まりし、騰落レシオは{a:.0f}"
               f"{'——売られすぎの水域に入りました' if oversold else 'と売られすぎに近づいています'}。"
               f"VIXはまだ明確なピークをつけていません。")
        act = "事前に決めた備えを実行に移し始める段階。分割での買い向かいの準備を進めてよい局面です。"
    else:
        key, name = "early", "初期"
        msg = (f"VIXが{v:.1f}（{band}）まで{'上昇中' if rising else '上昇'}し、"
               f"騰落レシオは{a:.0f}でまだ極端な水準に達していません。本震の直後にあたります。")
        act = "焦って買い向かう場面ではありません。VIXが高いだけで「そろそろ底」と判断するのが、最大の過ちです。"

    return {"ok": True, "active": True,
            "vix": round(v, 2), "vix_band": band, "vix_band_desc": band_desc,
            "vix_peak": round(vpeak, 2), "vix_off_peak": round(off_peak * 100, 1),
            "vix_peak_days": days_since_vpeak, "vix_rising": bool(rising),
            "adr": round(a, 1), "adr_oversold": bool(oversold),
            "drawdown": round(dd * 100, 1),
            "peak_date": peak_idx.strftime("%Y-%m-%d"),
            "shock_date": shock_at.strftime("%Y-%m-%d") if shock_at is not None else None,
            "aftershocks": aftershocks,
            "stage_key": key, "stage": name, "message": msg, "action": act}


def main():
    gauges = [gauge_yield(), gauge_cape(), gauge_margin(), gauge_overheat(), gauge_tightening()]
    stage = compute_stage()

    lit = sum(1 for x in gauges if x["on"] is True)
    unknown = sum(1 for x in gauges if x["on"] is None)
    judged = len(gauges) - unknown

    # 本 第2章の基準
    if lit >= 4:
        key, label = "prepare", "無視できない警告"
        msg = ("4つ以上が同時に灯っている。本の基準では、砂山は崩れる寸前まで育っている状態。"
               "ただしこれは「今すぐ逃げろ」ではなく「備える時」。現金比率を段階的に上げ、"
               "損切りラインを事前に決めておく段階。")
    elif lit == 3:
        key, label = "watch", "注意"
        msg = ("3つが灯っている。様子見と警告の境目。あと1つ灯れば、本の基準では"
               "無視できない警告の水準に入る。")
    else:
        key, label = "calm", "様子見"
        msg = ("灯っているのは2つ以下。本の基準では、まだ様子見でよい段階。"
               "ただし傾斜計は崩落のずっと手前で静かに振れ始める。定期的に確認を。")
    if unknown:
        msg += f"（{unknown}個は今回データを取得できず未判定。灯っている可能性を排除できない）"

    data = {
        "updated": datetime.now(JST).isoformat(timespec="seconds"),
        "lit": lit, "total": len(gauges), "judged": judged, "unknown": unknown,
        "stage_key": key, "stage": label, "message": msg,
        "gauges": gauges,
        "stage": stage,
        "book": {"title": "暴落は、減衰する", "asin": "B0H8HHC16H",
                 "url": "https://www.amazon.co.jp/dp/B0H8HHC16H?tag=ruletrade-22",
                 "basis": "第2章「5つの前兆を、一つずつ読む」／第5章「今の日米相場を、実データで採点する」"},
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"OK {OUT.name}: {lit}/{len(gauges)}個 点灯 → {label}"
          + (f"（未判定{unknown}）" if unknown else ""))
    for x in gauges:
        mark = "●" if x["on"] is True else ("○" if x["on"] is False else "？")
        print(f"  [{mark}] {x['no']}. {x['label']:14s} {x['value']:>18s}  {x['detail'][:60]}")

    if stage.get("ok"):
        print(f"\nステージ: {stage['stage']}  VIX {stage['vix']}（{stage['vix_band']}）"
              f" / 騰落レシオ {stage['adr']} / 高値から {stage['drawdown']:+.1f}%")
        if stage.get("aftershocks"):
            print("  余震（2%以上動いた日数／週）: "
                  + " ".join(f"{w['week']}週{w['big']}/{w['days']}日" for w in stage["aftershocks"]))
    else:
        print(f"\nステージ判定: 取得失敗（{stage.get('why')}）", file=sys.stderr)
    if unknown:
        print("\n未判定があります。ページ側では消灯扱いにせず「未判定」と表示されます。", file=sys.stderr)


if __name__ == "__main__":
    main()
