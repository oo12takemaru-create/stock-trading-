# -*- coding: utf-8 -*-
"""相場の温度計（0〜100）のテスト。数字は作り物（実データは使わない・ネットにつながない）

  1. 境目: 温度の帯と温度計の判定（detect_market_regime）が全日で一致する。帯の端の値も正しい
  2. 欠損日: 入力の一部が欠けた日も温度が出る（帯は一致）。全部欠けた日は温度を出さない
  3. 休場日: 米国だけ休み・日本だけ休みの日の扱い。履歴は日本の営業日だけ。比較の「n営業日前」は履歴の並びで数える
  4. 先読みしない: 先の日のデータを足しても、過去の温度は変わらない
  5. 公開してよい列だけ: 履歴の列は d/t/tc/r/c/n1 だけ（JPX 由来の数字を入れない）
  6. 定義文に使わない言葉が無い・「判定が変わった日は数字が大きく動きます」がある
  7. その後の集計: 先が足りない日は数えない・かたまりの数え方
  8. ℃: ℃ = round(値×0.8−20)・帯の境目・比較・1年の高安・集計区分の ℃ の表記・既存のキーと値は変えない
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import thermo100 as T                        # noqa: E402
from daily_scanner_v2_8_0 import detect_market_regime  # noqa: E402

NG = 0
NOW = datetime(2026, 11, 15, 20, 0, tzinfo=timezone(timedelta(hours=9)))


def ok(cond, label):
    global NG
    if not cond:
        NG += 1
        print("  NG", label)


def make_market(seed=7, n=1400):
    """強気・中立・弱気・総崩れがすべて出るよう区間ごとに傾きと VIX を変えた作り物の相場"""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2019-01-01", periods=n)
    plan = [(300, 0.0010, 0.0008, 14), (90, -0.0015, 0.0005, 18), (80, 0.0012, 0.0010, 15),
            (40, -0.0080, -0.0060, 34), (120, -0.0005, -0.0002, 24), (310, 0.0009, 0.0007, 16),
            (60, -0.0070, -0.0050, 33), (400, 0.0006, 0.0006, 17)]
    n_dr, s_dr, vx = [], [], []
    for days, a, b, v in plan:
        n_dr += [a] * days; s_dr += [b] * days; vx += [v] * days
    n_dr, s_dr, vx = np.array(n_dr[:n]), np.array(s_dr[:n]), np.array(vx[:n], dtype=float)
    n225 = 20000 * np.exp(np.cumsum(n_dr + rng.normal(0, 0.011, n)))
    sp = 3000 * np.exp(np.cumsum(s_dr + rng.normal(0, 0.009, n)))
    vix = np.clip(vx + rng.normal(0, 2.5, n), 9, 80)
    frame = lambda x, i=idx: pd.DataFrame({"Close": x}, index=i)  # noqa: E731
    return {"^N225": frame(n225), "^GSPC": frame(sp), "^VIX": frame(vix)}


# ── 1. 境目 ──
for b in T.BANDS:
    ok(T.place(b["regime"], 0) == b["lo"], f"{b['jp']} 位置0 → 下限")
    ok(T.place(b["regime"], 1) == b["hi"], f"{b['jp']} 位置1 → 上限")
    ok(T.place(b["regime"], -3) == b["lo"] and T.place(b["regime"], 9) == b["hi"], f"{b['jp']} 範囲外の位置は帯に収める")
    for v in (b["lo"], b["hi"]):
        ok(T.regime_of_value(v) == b["regime"], f"温度{v} の帯 = {b['jp']}")
# 帯は 0〜100 を隙間も重なりもなく覆う
cover = [T.regime_of_value(v) for v in range(101)]
ok(len(cover) == 101, "0〜100 のすべての整数に帯がある")
ok([b["lo"] for b in T.BANDS] == [0, 15, 40, 60] and T.BANDS[-1]["hi"] == 100, "帯の境目が決めたとおり")

g = make_market()
data, hist = T.build(g, detect_market_regime, None, NOW)
items = hist["items"]
seen = {h["r"] for h in items}
for r in ("BULLISH", "NEUTRAL", "BEARISH", "PANIC"):
    ok(r in seen, f"作り物の相場に {r} が無い（テストとして弱い）")
bad = [h for h in items if T.regime_of_value(h["t"]) != h["r"]]
ok(not bad, f"温度の帯と判定の不一致 {len(bad)}日 例={bad[:2]}")
# 判定は detect_market_regime そのもの
diff = [h["d"] for h in items[::7] if detect_market_regime(g, pd.Timestamp(h["d"]).date())[0] != h["r"]]
ok(not diff, f"履歴の判定が detect_market_regime と違う {diff[:3]}")
ok(data["value"] == items[-1]["t"] and data["regime"] == items[-1]["r"], "今日の値 = 履歴の最後")
ok(T.regime_of_value(data["value"]) == data["regime"], "今日の値の帯と判定が一致")
# 帯の中で偏らない（各帯の下半分・上半分の両方に出る）
for b in T.BANDS:
    v = [h["t"] for h in items if h["r"] == b["regime"]]
    if len(v) >= 40:
        mid = (b["lo"] + b["hi"]) / 2
        ok(any(x < mid for x in v) and any(x > mid for x in v), f"{b['jp']}の帯の片側に固まっている")
# 判定の変わり目フラグ
chg_days = [i for i in range(1, len(items)) if items[i]["r"] != items[i - 1]["r"]]
ok(len(chg_days) > 0, "作り物の相場に判定の変わり目が無い")
i = chg_days[-1]
d2, h2 = T.build({k: v[v.index <= pd.Timestamp(items[i]["d"])] for k, v in g.items()},
                 detect_market_regime, None, NOW)
ok(d2["regime_changed"] is True and d2["prev_regime"] == items[i - 1]["r"], "判定が変わった日に regime_changed=true")
ok(data["regime_changed"] == (items[-1]["r"] != items[-2]["r"]), "regime_changed が前日との比較と一致")

# ── 2. 欠損日 ──
gm = {k: v.copy() for k, v in g.items()}
holes = g["^VIX"].index[900:905]
gm["^VIX"].loc[holes, "Close"] = np.nan          # VIX が5日欠けた（終値が空）
gm["^GSPC"] = gm["^GSPC"].drop(g["^GSPC"].index[950:953])   # S&P の行そのものが無い
dm, hm = T.build(gm, detect_market_regime, None, NOW)
dd = {h["d"] for h in hm["items"]}
ok(all(str(d.date()) in dd for d in holes), "VIX が欠けた日も温度が出る（前の値を使う）")
ok(all(T.regime_of_value(h["t"]) == h["r"] for h in hm["items"]), "欠損ありでも帯と判定が一致")
# 入力が1つも無い日（日経の終値が空）は履歴に出さない
gn = {k: v.copy() for k, v in g.items()}
gone = g["^N225"].index[1000]
gn["^N225"].loc[gone, "Close"] = np.nan
_, hn = T.build(gn, detect_market_regime, None, NOW)
ok(str(gone.date()) not in {h["d"] for h in hn["items"]}, "日経の終値が空の日は履歴に出さない")

# ── 3. 休場日 ──
# 米国だけ休み（S&P・VIX に行が無い）→ 日本の営業日なので温度は出る。日本だけ休み → 履歴に出ない
us_hol = g["^N225"].index[1100]
jp_hol = g["^N225"].index[1110]
gh = {k: v.copy() for k, v in g.items()}
gh["^GSPC"] = gh["^GSPC"].drop(us_hol)
gh["^VIX"] = gh["^VIX"].drop(us_hol)
gh["^N225"] = gh["^N225"].drop(jp_hol)
dh, hh = T.build(gh, detect_market_regime, None, NOW)
hd = [h["d"] for h in hh["items"]]
ok(str(us_hol.date()) in hd, "米国だけ休みの日も日本の温度は出る")
ok(str(jp_hol.date()) not in hd, "日本の休場日は履歴に出ない")
ok(all(pd.Timestamp(d).weekday() < 5 for d in hd), "履歴に土日が無い")
# n営業日前は履歴の並びで数える（休場日をまたいでも 5営業日前 = 5行前）
ok(dh["compare"]["week"]["d"] == hd[-6] and dh["compare"]["month"]["d"] == hd[-21]
   and dh["compare"]["yesterday"]["d"] == hd[-2], "昨日・1週間前・1か月前の数え方")
ok(dh["compare"]["week"]["diff"] == dh["value"] - hh["items"][-6]["t"], "比較の差")
# 翌営業日の騰落は、休場日をはさんでも次の行の終値で測る
pos = hd.index(str((jp_hol - pd.offsets.BDay(1)).date()))
a, b = hh["items"][pos], hh["items"][pos + 1]
ok(b["d"] == str((jp_hol + pd.offsets.BDay(1)).date())
   and abs(a["n1"] - round((b["c"] / a["c"] - 1) * 100, 2)) < 0.011, "休場日をはさんだ翌営業日の騰落")
ok(hh["items"][-1]["n1"] is None, "最後の日の翌営業日の騰落は空")

# ── 4. 先読みしない ──
cut = g["^N225"].index[1200]
gc = {k: v[v.index <= cut] for k, v in g.items()}
_, hc = T.build(gc, detect_market_regime, None, NOW)
full = {h["d"]: h["t"] for h in items}
moved = [h["d"] for h in hc["items"] if full.get(h["d"]) != h["t"]]
ok(not moved, f"先のデータを足すと過去の温度が変わる {len(moved)}日 例={moved[:3]}")
p1 = T.rolling_pct(np.arange(300.0))
p2 = T.rolling_pct(np.r_[np.arange(300.0), np.zeros(50)])
ok(np.allclose(p1, p2[:300], equal_nan=True), "rolling_pct が先の値を見ている")
ok(np.isnan(T.rolling_pct(np.arange(10.0))).all(), "窓が足りない日は位置を出さない")

# ── 5. 公開してよい列だけ ──
ok(all(set(h) == {"d", "t", "tc", "r", "c", "n1"} for h in items), "履歴の列は d/t/tc/r/c/n1 だけ")
ok(all(0 <= h["t"] <= 100 and isinstance(h["t"], int) for h in items), "温度は0〜100の整数")
for k in ("value", "compare", "year", "components", "definition", "updated", "stats", "regime_changed", "bands"):
    ok(k in data, f"thermo100.json に {k} が無い")
ok(len(data["components"]) == 5 and abs(sum(c["weight"] for c in data["components"]) - 1) < 0.01, "内訳5つ・重みの合計1")
yr = data["year"]
ok(yr["low"]["value"] <= data["value"] <= yr["high"]["value"], "1年の高値・安値の間に今日がある")

# ── 6. 言葉 ──
ok(not T.text_ok(T.DEFINITION + T.DEFINITION_SHORT), f"定義文に使わない言葉 {T.text_ok(T.DEFINITION)}")
ok("判定が変わった日は数字が大きく動きます" in T.DEFINITION, "定義文に判定の変わり目の一文が無い")
ok("売買の合図ではありません" in T.DEFINITION, "定義文に「売買の合図ではない」が無い")
ok(not T.text_ok("".join(c["label"] for c in data["components"])), "入力の名前に使わない言葉")
ok(T.NAME == "相場の温度計", "名前")

# ── 7. その後の集計 ──
px = pd.Series([100.0, 101, 102, 103, 104, 105, 106], index=pd.bdate_range("2026-01-05", periods=7))
hs = [{"d": str(d.date())} for d in px.index]
fw = T.forward_stats(hs, px, horizons=(5,))
ok(abs(fw["2026-01-05"][5] - 5.0) < 1e-9 and fw["2026-01-06"][5] is not None and fw["2026-01-07"][5] is None,
   "先が足りない日は数えない")
alld = [h["d"] for h in hs]
ok(T._episodes(["2026-01-05", "2026-01-06", "2026-01-08"], alld) == 2, "続いた日は1つのかたまり")
s = T.summarize(["2026-01-05"], {"2026-01-05": {5: 1.0, 20: None}}, alld)
ok(s["sankou"] is True and s["r20"]["n"] == 0, "件数が少ないと参考値")
gl = {x["key"]: x for x in data["stats"]["groups"]}
ok(set(gl) >= {"le20", "ge80", "all"}, "極端な日の集計がある")
ok(gl["all"]["n225"]["n_days"] == len(items), "全期間の件数 = 履歴の日数")

# ── 8. ℃ ──
ok(T.celsius(0) == -20 and T.celsius(100) == 60 and T.celsius(25) == 0 and T.celsius(50) == 20, "℃ の端と0℃")
ok(T.celsius(None) is None, "値が無ければ ℃ も無い")
ok([(b["lo"], b["hi"]) for b in T.BANDS_C] == [(-20, -9), (-8, 11), (12, 27), (28, 60)], f"帯の ℃ {T.BANDS_C}")
ok(T.celsius(20) == -4 and T.celsius(80) == 44, "集計区分 20以下=−4℃以下・80以上=44℃以上")
ok(all(T.celsius(v) == int(round(v * 0.8 - 20)) for v in range(101)), "全値で式どおり")
# ℃ の帯と 0〜100 の帯が全値で同じ判定になる（境目が食い違わない）
for v in range(101):
    c = T.celsius(v)
    rc = next(b["regime"] for b in T.BANDS_C if b["lo"] <= c <= b["hi"])
    ok(rc == T.regime_of_value(v), f"温度{v}（{c}℃）の帯が一致しない")
ok(all(h["tc"] == T.celsius(h["t"]) for h in items), "履歴の tc = celsius(t)")
cz = data["celsius"]
ok(cz["value"] == T.celsius(data["value"]) and cz["scale"] == {"min": -20, "max": 60}, "今日の ℃")
ok(cz["label"] == T.c_label(cz["value"]) and T.c_label(-4) == "−4℃" and T.c_label(44) == "44℃", "℃ の表記")
bc = next(b for b in T.BANDS_C if b["regime"] == data["regime"])
ok(cz["band"] == {"lo": bc["lo"], "hi": bc["hi"]} and cz["band"]["lo"] <= cz["value"] <= cz["band"]["hi"], "今日の ℃ が帯の中")
for k in ("yesterday", "week", "month"):
    a, b = data["compare"][k], cz["compare"][k]
    ok((a is None) == (b is None), f"比較 {k} の有無")
    if a and b:
        ok(b["d"] == a["d"] and b["value"] == T.celsius(a["value"]) and b["diff"] == cz["value"] - b["value"], f"比較 {k} の ℃")
ok(cz["year"]["high"]["value"] == T.celsius(data["year"]["high"]["value"])
   and cz["year"]["low"]["value"] == T.celsius(data["year"]["low"]["value"]), "1年の高安の ℃")
gl2 = {x["key"]: x for x in data["stats"]["groups"]}
ok(gl2["le20"]["label_c"] == "−4℃以下" and gl2["ge80"]["label_c"] == "44℃以上", "集計区分の ℃ の表記")
ok(gl2["le20"]["label"] == "温度20以下" and gl2["ge80"]["label"] == "温度80以上", "既存の区分名は変えない")
ok(all("label_c" in x for x in data["stats"]["groups"]), "全区分に ℃ の表記")
ok(isinstance(data["value"], int) and 0 <= data["value"] <= 100 and data["bands"] == T.BANDS, "既存の value・bands はそのまま")
ok("−20℃〜60℃" in T.DEFINITION and "−20℃〜60℃" in T.DEFINITION_C, "定義文に ℃ の読み替えの一文")
ok(not T.text_ok(T.DEFINITION_C), "℃ の定義文に使わない言葉")
ok(hist["bands_c"] == T.BANDS_C and "tc" in hist["fields"], "履歴の ℃ の列の説明")

print("NG" if NG else "OK", f"(不一致 {NG})")
sys.exit(1 if NG else 0)
