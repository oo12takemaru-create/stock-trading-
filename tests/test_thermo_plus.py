# -*- coding: utf-8 -*-
"""温度計を強くする（thermo_pos / thermo_long / thermo_map / thermo_stocks）のテスト。作り物のデータ・ネットにつながない

  1. fast_pct は thermo100.rolling_pct と同じ値（欠損・同じ値を含む）
  2. 1990年版の計算は thermo100.build_rows と同じ温度を出す（同じ関数・同じ窓）・先読みしない
  3. 記録（records_at）: 氷点下に入った日・今年◯番目・◯年以来・1週間で◯℃・判定の変わり目
  4. 有名な日: 大きい順・近い日を重ねない・名前は向きが合う日だけ
  5. その後の集計: 初日＋続いた日＝全体・先が足りない日は数えない
  6. 位置の温度: 0〜100 に収まる・℃ の換算・先読みしない・本数が足りなければ出さない
  7. 銘柄の温度・33業種の指数: 本数不足と古い銘柄を出さない・単純平均・外れ値を外す
  8. 言葉と列: 定義文に使わない言葉が無い・JPX 由来の列が無い
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import thermo100 as T            # noqa: E402
import thermo_pos as P           # noqa: E402
import thermo_long as L          # noqa: E402
import thermo_map as M           # noqa: E402
import thermo_stocks as S        # noqa: E402
from daily_scanner_v2_8_0 import detect_market_regime  # noqa: E402

NG = 0
NOW = datetime(2026, 11, 17, 21, 0, tzinfo=timezone(timedelta(hours=9)))


def ok(cond, label):
    global NG
    if not cond:
        NG += 1
        print("  NG", label)


def make_market(seed=7, n=1400):
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


# ── 1. fast_pct ──
rng = np.random.default_rng(3)
a = np.round(rng.normal(0, 1, 1500), 1)
a[:40] = np.nan
a[[300, 301, 900]] = np.nan
for w, m in ((300, 50), (2450, 250)):
    x = T.rolling_pct(a, w, m)
    y = P.fast_pct(pd.Series(a), w, m).values
    ok(np.array_equal(np.isnan(x), np.isnan(y)), f"fast_pct の欠損の位置 w={w}")
    ok(np.nanmax(np.abs(x - y)) < 1e-12, f"fast_pct の値 w={w}")
df2 = pd.DataFrame({"a": a, "b": a[::-1]})
ok(np.allclose(P.fast_pct(df2, 300, 50)["b"].values, T.rolling_pct(a[::-1], 300, 50), equal_nan=True), "fast_pct は列ごと")

# ── 2. 1990年版 = thermo100 と同じ温度・先読みしない ──
g = make_market()
dates = g["^N225"].index
feat = T.features(g, dates)
pos = T.positions(feat)
calc = dates[199:]
want = T.build_rows(g, detect_market_regime, calc, pos)
regimes, score = L.regimes_and_scores(g, detect_market_regime, calc, pos)
got = L.rows_from(calc, regimes, score, T.WINDOW)
ok(len(want) == len(got) and all(x["t"] == y["t"] and x["regime"] == y["regime"] and x["d"] == y["d"]
                                 for x, y in zip(want, got)), "rows_from は thermo100.build_rows と同じ")
_, sc2 = L.regimes_and_scores_fast(regimes, calc, pos)
ok(np.allclose(score, sc2, equal_nan=True), "判定の使い回しでも総合の位置は同じ")
g_short = {k: v.iloc[:1100] for k, v in g.items()}
f_s = T.features(g_short, g_short["^N225"].index)
p_s = T.positions(f_s)
c_s = g_short["^N225"].index[199:]
r_s, s_s = L.regimes_and_scores(g_short, detect_market_regime, c_s, p_s)
short = {r["d"]: r["t"] for r in L.rows_from(c_s, r_s, s_s, T.WINDOW)}
ok(all(short[r["d"]] == r["t"] for r in got if r["d"] in short), "先の日を足しても過去の温度は同じ（1990年版）")
L_START = L.START
L.START = "2019-01-01"
rows, alt, n225 = L.build_long(g, detect_market_regime)
L.START = L_START
ok(len(rows) == len(got) and all(x["t"] == y["t"] for x, y in zip(rows, got)), "build_long の温度 = build_rows")
ok(alt is not None and len(alt) == len(rows), "全部の窓の版も同じ日数")

# ── 3. 記録 ──
def H(tcs, regs=None, start="2025-01-01"):
    ds = pd.bdate_range(start, periods=len(tcs))
    regs = regs or ["NEUTRAL"] * len(tcs)
    return [{"d": str(d.date()), "t": int(round((c + 20) / 0.8)), "tc": c, "r": r, "c": 100.0}
            for d, c, r in zip(ds, tcs, regs)]


h = H([5] * 30 + [-3] + [5] * 40 + [-2])
rec = {r["type"]: r for r in L.records_at(h, len(h) - 1)}
ok("freeze_in" in rec and rec["freeze_in"]["n"] == 41 and rec["freeze_in"]["prev"] == h[30]["d"], "◯営業日ぶりの氷点下")
ok(rec.get("year_cold", {}).get("n") == 2, "今年2番目の寒さ（−3℃ の日の次）")
ok(not L.records_at(h, len(h) - 2) or all(r["type"] != "freeze_in" for r in L.records_at(h, len(h) - 2)), "氷点下でない日は freeze_in なし")
h = H([10] * 300 + [-5] * 1 + [10] * 300 + [-6])
rec = {r["type"]: r for r in L.records_at(h, len(h) - 1)}
ok(rec.get("low_since", {}).get("prev") is None and "1990年以来" in rec["low_since"]["text"], "記録の中でいちばん低い")
h = H([10] * 300 + [-7] + [10] * 300 + [-6])
rec = {r["type"]: r for r in L.records_at(h, len(h) - 1)}
ok(rec.get("low_since", {}).get("prev") == h[300]["d"], "◯年◯月以来の低さ（前回の日付）")
h = H([10] * 300 + [-7] + [10] * 10 + [-6])
ok(all(r["type"] != "low_since" for r in L.records_at(h, len(h) - 1)), "前回が近ければ「以来」は出さない")
h = H([30] * 200 + [30, 25, 20, 15, 10, 5])
rec = {r["type"]: r for r in L.records_at(h, len(h) - 1)}
ok(rec.get("drop5", {}).get("value") == -25 and rec["drop5"]["prev"] is None, "1週間で25℃下がった（初めて）")
regs = ["BULLISH"] * 100 + ["NEUTRAL"] * 30 + ["BULLISH"] * 10 + ["BEARISH"]
h = H([30] * len(regs), regs)
rec = {r["type"]: r for r in L.records_at(h, len(h) - 1)}
ok(rec.get("regime_change", {}).get("n") == 10 and rec["regime_change"]["prev"] == h[130]["d"], "判定が変わったのは◯営業日ぶり")
ok(rec.get("regime_return", {}).get("prev") is None, "その判定は初めて")
ok(L.records_at(H([5, 5]), 0) == [], "1日目は記録なし")

# ── 4. 有名な日 ──
cs = [100.0] * 10 + [90.0] * 2 + [85.0] * 18 + [120.0] * 20   # 10日目 −10%・12日目 −5.6%（近いので大きいほうだけ）・30日目 +41%
hist = [{"d": str(d.date()), "t": 50, "tc": 20, "r": "NEUTRAL", "c": c}
        for d, c in zip(pd.bdate_range("2008-10-01", periods=50), cs)]
fm = L.famous_days(hist, n_drop=3, n_rise=1, gap=5)
ok(fm["drops"][0]["d"] == hist[10]["d"] and all(x["d"] != hist[12]["d"] for x in fm["drops"]), "近い日は大きいほうだけ")
ok(fm["rises"][0]["d"] == hist[30]["d"], "上げの1位")
L.NAMED[hist[30]["d"]] = ("テストの名前", -1)
fm = L.famous_days(hist, n_drop=3, n_rise=1, gap=5)
ok("name" not in fm["rises"][0], "向きが合わない日に名前は付けない")
del L.NAMED[hist[30]["d"]]

# ── 5. その後の集計 ──
h = H([-10, -10, 5, -10, 5, 5] * 30)
for x, c in zip(h, np.linspace(100, 130, len(h))):
    x["c"] = float(c)
G = {x["key"]: x["n225"] for x in L.stat_groups(h)}
ok(G["le20_first"]["n_days"] + G["le20_cont"]["n_days"] == G["le20"]["n_days"], "初日＋続いた日＝温度20以下")
ok(G["le20_first"]["n_days"] == 60 and G["le20_cont"]["n_days"] == 30, "初日と続いた日の数え方")
ok(G["all"]["r60"]["n"] == len(h) - 60, "先が足りない日は数えない")

# ── 6. 位置の温度 ──
idx = pd.bdate_range("2012-01-02", periods=1500)
c = pd.Series(1000 * np.exp(np.cumsum(np.random.default_rng(5).normal(0.0002, 0.012, 1500))), index=idx)
ts = P.temp_series(c)
ok(len(ts) and ts["t"].between(0, 100).all(), "位置の温度は 0〜100")
ok((ts["tc"] == ts["t"].map(T.celsius)).all(), "℃ = round(値×0.8−20)")
ok(ts.index[0] == idx[200 + P.MIN_PERIODS - 2], "本数が足りるまで出さない（200日線の助走＋250本）")
ts_short = P.temp_series(c.iloc[:1200])
ok((ts.loc[ts_short.index, "t"] == ts_short["t"]).all(), "先の日を足しても過去の位置の温度は同じ")
ok(len(P.temp_series(c.iloc[:400])) == 0, "短い系列は出さない")
row = P.summary_row(ts)
ok(row["tc"] == T.celsius(row["value"]) and P.POS_BANDS[P.band_index(row["value"])]["key"] == row["band"], "今日の行")
ok([P.band_index(v) for v in (0, 14, 15, 39, 40, 59, 60, 100)] == [0, 0, 1, 1, 2, 2, 3, 3], "帯の境目")
ok([b["lo"] for b in P.POS_BANDS_C] == [-20, -8, 12, 28], "帯の ℃")
rb = M.rank_block([{"t": "a", "n": "A", "tc": 5}, {"t": "b", "n": "B", "tc": -3}, {"t": "c", "n": "C", "tc": -9, "sub": 1},
                   {"t": "d", "n": "D", "tc": None}])
ok(rb["coldest"]["t"] == "b" and rb["hottest"]["t"] == "a" and rb["order"] == ["b", "a"], "世界の並び（sub と取れない指数は数えない）")
fake = {t: c * (1 + i / 100) for i, (t, *_) in enumerate(M.SECTOR17)}
sec = M.build_sector(NOW, Path("."), fetcher=lambda t: fake[t])
ok(len(sec["items"]) == 17 and all(x["ok"] for x in sec["items"]), "業種の温度（17）")
ok(sorted(s for x in M.SECTOR17 for s in x[2]) == sorted(__import__("heatmap_day").SECTOR_SLUG), "17業種は33業種をちょうど覆う")

# ── 7. 銘柄の温度・33業種 ──
idx = pd.bdate_range("2015-01-01", periods=900)
base = np.exp(np.cumsum(np.random.default_rng(9).normal(0, 0.01, (900, 8)), axis=0)) * 1000
closes = pd.DataFrame(base, index=idx, columns=[f"10{i}0" for i in range(8)])
closes.iloc[:500, 1] = np.nan                     # 本数が足りない
closes.iloc[-20:, 2] = np.nan                     # 古い
items, last = S.stock_items(closes)
ok("1010" not in items and "1020" not in items and "1000" in items, "本数不足・古い銘柄は出さない")
ok(all(len(v) == 5 and v[0] == T.celsius(v[3]) for v in items.values()), "銘柄の列（℃・前日比・帯・0〜100・5日前比）")
sec33 = {f"10{i}0": ("A" if i < 6 else "B") for i in range(8)}
closes2 = closes.copy()
closes2.iloc[400, 3] = closes2.iloc[399, 3] * 10   # 誤った足
si = S.sector_index(closes2, sec33)
ok("A" in si and "B" not in si, "銘柄が5未満の業種は作らない")
r = closes2[[c for c in closes2 if sec33[c] == "A"]].pct_change(fill_method=None)
r = r.where(r.abs() <= S.CLIP)
exp = (1 + r.mean(axis=1).where(r.notna().sum(axis=1) >= S.MIN_MEMBERS).dropna()).cumprod() * 100
ok(np.allclose(si["A"].values, exp.values), "33業種の指数 = 騰落の単純平均（外れ値を外す）")
st, s33 = S.build(closes, sec33, NOW)
ok(st["count"] == len(items) and sum(st["band_counts"]) == st["count"], "銘柄の温度の件数")

# ── 8. 言葉と列 ──
for name, txt in (("1990年版", L.DEFINITION), ("世界", M.DEF_WORLD), ("業種", M.DEF_SECTOR),
                  ("銘柄", S.DEF_STOCK), ("33業種", S.DEF_S33)):
    ok(not P.text_ng(txt), f"{name}の定義文に使わない言葉: {P.text_ng(txt)}")
for nm, _ in L.NAMED.values():
    ok(not P.text_ng(nm), f"名前に使わない言葉: {nm}")
for name, txt in (("1990年版", L.DEFINITION), ("世界", M.DEF_WORLD), ("業種", M.DEF_SECTOR),
                  ("銘柄", S.DEF_STOCK), ("33業種", S.DEF_S33)):
    ok("売買の合図ではありません" in txt, f"{name}の定義文に「売買の合図ではありません」")
for txt in (M.DEF_WORLD, M.DEF_SECTOR, S.DEF_STOCK, S.DEF_S33):
    ok("別の物差し" in txt, "相場の温度計と別の物差しと書く")
JPX = ("shinyo", "karauri", "margin", "investor", "mr", "mb", "sr", "sn", "信用", "空売り")
data, hd = L.build(g, detect_market_regime, NOW, docs=Path("__none__"))
ok(set(hd["items"][0].keys()) == {"d", "t", "tc", "r", "c"}, "1990年版の履歴の列")
blob = str(list(data.keys())) + str(list(sec["items"][0].keys())) + str(list(st.keys())) + str(list(s33["items"][0].keys()))
ok(not any(f"'{k}'" in blob for k in JPX), "JPX 由来の列が無い")
for rr in data["records_recent"]:
    for x in rr["records"]:
        ok(not P.text_ng(x["text"]), f"記録の文に使わない言葉: {x['text']}")

print("OK" if NG == 0 else f"NG {NG}件")
sys.exit(1 if NG else 0)
