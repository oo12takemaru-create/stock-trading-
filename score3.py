# -*- coding: utf-8 -*-
"""3軸スコア → docs/score3.json（株レーダー AI朝刊・温度計の土台）

AI朝刊のスタンスを「AIの気分」ではなく数値で決めるための機械採点。
AIには合計スコアと内訳を渡し、**イベントリスクによる1段階の下方修正だけ**を許す。
上方修正は許可しない（AIの慎重バイアスと暴走の両方を防ぐ）。

■ 3つの軸（各 -2〜+2）
  軸1 トレンド : 上昇の押し目か、下落の戻りか（日足の位置と市場の広がり）
  軸2 短期リスク: 今日は手を出す日か（VIX・先物ギャップ・為替・イベント）
  軸3 需給     : 今週の傾向（週次データなので重みは軽い）

■ 合計 -6〜+6 → 5段階
  +3以上 強気 / +1〜+2 やや強気 / 0 中立 / -1〜-2 やや守り / -3以下 守り
  ※「中立」はちょうど0のときだけ。5段階にすることで中立の出現を構造的に減らす。

■ 入力はすべて既に毎日/毎週自動更新されているもの＋yfinance
  信用評価損益率と裁定買い残は使わない（前者は公表データから算出不可能、
  後者は新規取得が必要。現状の4本で軸3は組める）。

使い方: python score3.py [出力パス]
"""
import json
import sys
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

JST = timezone(timedelta(hours=9))
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "docs/score3.json")
PAGES = "https://oo12takemaru-create.github.io/stock-trading-"
UA = {"User-Agent": "Mozilla/5.0 (compatible; kaburadar.jp/1.0)"}

# 合計スコア → 5段階。境目はここだけを見れば分かるようにまとめる
STAGES = [
    (3, "attack", "強気"),
    (1, "lean_attack", "やや強気"),
    (0, "neutral", "中立"),
    (-2, "lean_defense", "やや守り"),
    (-99, "defense", "守り"),
]


def site_json(name):
    try:
        req = urllib.request.Request(f"{PAGES}/{name}", headers=UA)
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except Exception as e:
        print(f"  {name}: 取得失敗 {e}", file=sys.stderr)
        return None


def clamp(v):
    return max(-2, min(2, v))


# ────────────────────────── 軸1 トレンド ──────────────────────────
def axis_trend(hm, gauge, px):
    """日経の移動平均線との位置＋市場の広がり（何割が25日線の上か・新高値の数）"""
    pts, notes = 0, []

    n = px.get("n225_close")
    for key, label, w in (("ma25", "25日線", 1), ("ma75", "75日線", 1), ("ma200", "200日線", 1)):
        m = px.get(key)
        if n and m:
            dev = (n / m - 1) * 100
            up = dev >= 0
            pts += w if up else -w
            notes.append(f"日経は{label}の{'上' if up else '下'}（{dev:+.1f}%）")

    # 市場の広がり: 337銘柄のうち25日線より上の割合。指数だけ見ると中身のズレを見落とす
    if hm:
        items = [x for x in hm.get("items", []) if x.get("g25") is not None]
        if len(items) >= 100:
            ratio = sum(1 for x in items if x["g25"] >= 0) / len(items) * 100
            if ratio >= 60:
                pts += 1; notes.append(f"25日線より上の銘柄が{ratio:.0f}%（広がりあり）")
            elif ratio <= 40:
                pts -= 1; notes.append(f"25日線より上の銘柄が{ratio:.0f}%（広がり乏しい）")
            else:
                notes.append(f"25日線より上の銘柄は{ratio:.0f}%")
        # 新高値と新安値の差
        his = [x for x in hm.get("items", []) if x.get("hi") is not None]
        if len(his) >= 100:
            nh = sum(1 for x in his if x["hi"] >= 0)
            nl = sum(1 for x in his if x["hi"] <= -50)
            if nh - nl >= 10:
                pts += 1; notes.append(f"52週高値更新{nh}銘柄 vs 高値から半値以下{nl}銘柄")
            elif nl - nh >= 10:
                pts -= 1; notes.append(f"高値から半値以下{nl}銘柄 vs 高値更新{nh}銘柄")

    # 騰落レシオ（傾斜計④で計算済みの値を再利用）
    tr = None
    for g in (gauge or {}).get("gauges", []) or []:
        if "過熱" in str(g.get("label", "")) or g.get("key") == "overheat":
            tr = g.get("value")
    if isinstance(tr, str) and tr.replace(".", "").replace("-", "").isdigit():
        tr = float(tr)
    if isinstance(tr, (int, float)):
        if tr >= 120:
            pts -= 1; notes.append(f"騰落レシオ{tr:.0f}（過熱圏）")
        elif tr <= 70:
            pts += 1; notes.append(f"騰落レシオ{tr:.0f}（売られすぎ）")

    return clamp(pts), notes


# ────────────────────────── 軸2 短期リスク ──────────────────────────
def axis_risk(crash, px, events):
    pts, notes = 0, []

    vix = px.get("vix")
    if vix:
        if vix >= 25:
            pts -= 2; notes.append(f"VIX {vix:.1f}（不安定）")
        elif vix >= 20:
            pts -= 1; notes.append(f"VIX {vix:.1f}（やや不安定）")
        elif vix <= 15:
            pts += 1; notes.append(f"VIX {vix:.1f}（落ち着き）")
        else:
            notes.append(f"VIX {vix:.1f}")

    if px.get("fut_gap_skip"):
        notes.append("CME先物ギャップ: " + px["fut_gap_skip"])
    gap = px.get("fut_gap")
    if gap is not None:
        if gap <= -1.0:
            pts -= 2; notes.append(f"CME先物は現物比{gap:+.2f}%（大きく下振れ示唆）")
        elif gap <= -0.3:
            pts -= 1; notes.append(f"CME先物は現物比{gap:+.2f}%（下振れ示唆）")
        elif gap >= 1.0:
            pts += 2; notes.append(f"CME先物は現物比{gap:+.2f}%（大きく上振れ示唆）")
        elif gap >= 0.3:
            pts += 1; notes.append(f"CME先物は現物比{gap:+.2f}%（上振れ示唆）")
        else:
            notes.append(f"CME先物は現物比{gap:+.2f}%（ほぼフラット）")

    # 急な円高は輸出採算と指数を直接押し下げる（円安側は軸1のトレンドに現れるので加点しない）
    u5 = px.get("usdjpy_chg5")
    if u5 is not None:
        if u5 <= -2.0:
            pts -= 1; notes.append(f"ドル円は5日で{u5:+.1f}%（急な円高）")
        else:
            notes.append(f"ドル円は5日で{u5:+.1f}%")

    # 着火判定（暴落メーター）が警戒以上なら短期リスクとして反映
    if crash:
        sk = crash.get("stage_key")
        if sk == "danger":
            pts -= 2; notes.append(f"着火判定は「{crash.get('stage')}」（{crash.get('score')}/{crash.get('flag_total')}）")
        elif sk == "warn":
            pts -= 1; notes.append(f"着火判定は「{crash.get('stage')}」（{crash.get('score')}/{crash.get('flag_total')}）")
        else:
            notes.append(f"着火判定は「{crash.get('stage')}」（{crash.get('score')}/{crash.get('flag_total')}）")

    if events:
        pts -= 1
        notes.append("5営業日以内の大型イベント: " + " / ".join(events[:3]))
    else:
        notes.append("5営業日以内に大型イベントなし")

    return clamp(pts), notes


# ────────────────────────── 軸3 需給 ──────────────────────────
def axis_flow(flow, cot, shinyo, karauri):
    """週次データが中心なので、日々の判定に効かせすぎないよう刻みを小さくする。
    キー名は各JSONの実体に合わせている（investor_flow=items/net_oku、cot=items/change、
    shinyo=buy_chg_oku、karauri=summary.up/down）。構造が変わったら黙って0点にせず notes に残す。"""
    pts, notes = 0, []

    # 海外投資家（現物・週次）: net_oku は億円
    if flow:
        row = next((r for r in flow.get("items", []) or []
                    if r.get("key") == "foreigners" or "海外" in str(r.get("label", ""))), None)
        v = (row or {}).get("net_oku")
        if isinstance(v, (int, float)):
            if v >= 2000:
                pts += 1; notes.append(f"海外投資家は+{v:,.0f}億円の買い越し")
            elif v <= -2000:
                pts -= 1; notes.append(f"海外投資家は{v:,.0f}億円の売り越し")
            else:
                notes.append(f"海外投資家は{v:+,.0f}億円")
        else:
            notes.append("海外投資家: 取得できず")

    # CFTC投機筋の日経先物（円建て）の前週比（枚）
    if cot:
        row = next((r for r in cot.get("items", []) or []
                    if "日経" in str(r.get("label", ""))), None)
        ch = (row or {}).get("change")
        if isinstance(ch, (int, float)):
            if ch >= 3000:
                pts += 1; notes.append(f"投機筋の日経先物は前週比+{ch:,.0f}枚")
            elif ch <= -3000:
                pts -= 1; notes.append(f"投機筋の日経先物は前週比{ch:,.0f}枚")
            else:
                notes.append(f"投機筋の日経先物は前週比{ch:+,.0f}枚")
        else:
            notes.append("投機筋(日経先物): 取得できず")

    # 信用買い残の前週比（億円）。増えすぎは上値の重さ＝将来の戻り売り
    if shinyo:
        ch = shinyo.get("buy_chg_oku")
        if isinstance(ch, (int, float)):
            if ch >= 1500:
                pts -= 1; notes.append(f"信用買い残が前週比+{ch:,.0f}億円（上値が重くなりやすい）")
            elif ch <= -1500:
                pts += 1; notes.append(f"信用買い残が前週比{ch:,.0f}億円（整理が進んだ）")
            else:
                notes.append(f"信用買い残は前週比{ch:+,.0f}億円")
        else:
            notes.append("信用買い残: 取得できず")

    # 大口の空売り: up=売り増し件数 / down=買い戻し件数（買い戻しは買い注文として市場に出る）
    if karauri:
        sm = karauri.get("summary") or {}
        inc, dec = sm.get("up"), sm.get("down")
        if isinstance(inc, int) and isinstance(dec, int) and (inc + dec) >= 50:
            if dec >= inc * 1.5:
                pts += 1; notes.append(f"大口の空売りは買い戻し{dec}件 > 売り増し{inc}件")
            elif inc >= dec * 1.5:
                pts -= 1; notes.append(f"大口の空売りは売り増し{inc}件 > 買い戻し{dec}件")
            else:
                notes.append(f"大口の空売りは売り増し{inc}件 / 買い戻し{dec}件")
        else:
            notes.append("大口の空売り: 件数が少なく判定に使わず")

    return clamp(pts), notes


def market_prices():
    """yfinanceで指数・為替・先物を取る。取れなかった項目はNoneのままにする"""
    out = {}
    try:
        import yfinance as yf
    except ImportError:
        print("  yfinance未導入", file=sys.stderr)
        return out

    def closes(t, period="400d"):
        # yfinanceは単一ティッカーでも列がMultiIndex（("Close", ticker)）になることがある。
        # そのままだと Series ではなく DataFrame が返り float() が落ちる。
        try:
            df = yf.download(t, period=period, progress=False, auto_adjust=False, threads=False)
            if df is None or len(df) == 0:
                return None
            c = df["Close"]
            if hasattr(c, "columns"):
                c = c.iloc[:, 0]
            c = c.dropna()
            return c if len(c) else None
        except Exception as e:
            print(f"  {t}: 取得失敗 {e}", file=sys.stderr)
            return None

    n = closes("^N225")
    if n is not None:
        out["n225_close"] = float(n.iloc[-1])
        for key, w in (("ma25", 25), ("ma75", 75), ("ma200", 200)):
            if len(n) >= w:
                out[key] = float(n.iloc[-w:].mean())
    v = closes("^VIX", "10d")
    if v is not None:
        out["vix"] = float(v.iloc[-1])
    u = closes("JPY=X", "20d")
    if u is not None and len(u) >= 6:
        out["usdjpy"] = float(u.iloc[-1])
        out["usdjpy_chg5"] = (float(u.iloc[-1]) / float(u.iloc[-6]) - 1) * 100
    f = closes("NIY=F", "10d")
    if f is not None and n is not None:
        # 先物ギャップは「現物の引け」と「その後に付いた先物」を比べて初めて意味がある。
        # 先物側が現物と同日以前＝まだ夜間の値が付いていないので、その日は使わない。
        fd, nd = f.index[-1].date(), n.index[-1].date()
        gap = (float(f.iloc[-1]) / float(n.iloc[-1]) - 1) * 100
        out["fut_date"], out["n225_date"] = str(fd), str(nd)
        if fd <= nd:
            out["fut_gap_skip"] = f"先物{fd}が現物{nd}より新しくないため使用しない"
        elif abs(gap) > 8:
            # ±8%超は指数の分割・データ欠損などの事故を疑う（実際の窓でこの幅は稀）
            out["fut_gap_skip"] = f"乖離{gap:+.1f}%が大きすぎるためデータ異常として使用しない"
        else:
            out["fut_gap"] = gap
    return out


def upcoming(events_list, days=5):
    """今後N営業日以内の大型イベント（events.jsと同じ並び）"""
    today = datetime.now(JST).date()
    out = []
    for d, name in events_list:
        try:
            dd = datetime.strptime(d, "%Y-%m-%d").date()
        except ValueError:
            continue
        n = 0
        cur = today
        while cur < dd and n <= days:
            cur += timedelta(days=1)
            if cur.weekday() < 5:
                n += 1
        if today <= dd and n <= days:
            out.append(f"{dd:%m/%d} {name}")
    return out


EVENTS = [
    ("2026-09-04", "米雇用統計(8月分)"),
    ("2026-09-11", "メジャーSQ(9月限)"),
    ("2026-09-11", "米CPI(8月分)"),
    ("2026-09-17", "FOMC結果発表"),
    ("2026-09-18", "日銀会合 結果発表"),
    ("2026-10-02", "米雇用統計(9月分)"),
    ("2026-10-14", "米CPI(9月分)"),
    ("2026-10-29", "FOMC結果発表"),
    ("2026-10-30", "日銀会合 結果発表"),
    ("2026-11-06", "米雇用統計(10月分)"),
    ("2026-11-10", "米CPI(10月分)"),
    ("2026-12-04", "米雇用統計(11月分)"),
    ("2026-12-10", "FOMC結果発表"),
    ("2026-12-10", "米CPI(11月分)"),
    ("2026-12-11", "メジャーSQ(12月限)"),
    ("2026-12-18", "日銀会合 結果発表"),
]


def stage_of(total):
    for lim, key, jp in STAGES:
        if total >= lim:
            return key, jp
    return STAGES[-1][1], STAGES[-1][2]


def main():
    print("入力を集めています...", file=sys.stderr)
    hm = site_json("heatmap.json")
    gauge = site_json("gauge.json")
    crash = site_json("crash.json")
    flow = site_json("investor_flow.json")
    cot = site_json("cot.json")
    shinyo = site_json("shinyo.json")
    karauri = site_json("karauri.json")
    px = market_prices()
    ev = upcoming(EVENTS)

    a1, n1 = axis_trend(hm, gauge, px)
    a2, n2 = axis_risk(crash, px, ev)
    a3, n3 = axis_flow(flow, cot, shinyo, karauri)
    total = a1 + a2 + a3
    key, jp = stage_of(total)

    # 判定に使えた材料が少なすぎるときは黙って中立を出さず、その旨を残す
    filled = sum(1 for x in (hm, crash, px.get("n225_close")) if x)
    data = {
        "updated": datetime.now(JST).isoformat(timespec="seconds"),
        "trade_date": (hm or {}).get("trade_date"),
        "metrics": {k: (round(v, 3) if isinstance(v, float) else v)
                    for k, v in px.items()},
        "axes": [
            {"key": "trend", "label": "トレンド", "score": a1, "notes": n1},
            {"key": "risk", "label": "短期リスク", "score": a2, "notes": n2},
            {"key": "flow", "label": "需給", "score": a3, "notes": n3},
        ],
        "total": total,
        "stance": key,
        "stance_jp": jp,
        "range": {"min": -6, "max": 6},
        "stages": [{"min": lim, "key": k, "jp": j} for lim, k, j in STAGES],
        "inputs_ok": filled == 3,
        "events_5d": ev,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"OK {OUT.name}: 合計{total:+d} → {jp} "
          f"(トレンド{a1:+d} / 短期リスク{a2:+d} / 需給{a3:+d})")
    for ax in data["axes"]:
        print(f"  [{ax['score']:+d}] {ax['label']}")
        for t in ax["notes"]:
            print(f"        - {t}")


if __name__ == "__main__":
    main()
