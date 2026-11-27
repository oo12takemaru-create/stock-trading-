# -*- coding: utf-8 -*-
"""cost_dist.py（含み損益の推定・しこりの壁）のテスト。小さな日足を一時フォルダに作って流す"""
import csv
import gzip
import json
import math
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cost_dist as CD   # noqa: E402

NG = 0
NG_WORDS = ("買い", "売り", "おすすめ", "狙い目", "チャンス", "有望", "過熱", "底", "天井", "青天井")


def ok(cond, label):
    global NG
    if not cond:
        NG += 1
        print(f"  NG {label}")


def series(n=400, seed=1, start=1000.0):
    rng = np.random.default_rng(seed)
    c = start * np.exp(np.cumsum(rng.normal(0, 0.015, n)))
    h = c * (1 + np.abs(rng.normal(0, 0.01, n)))
    l = c * (1 - np.abs(rng.normal(0, 0.01, n)))
    v = rng.uniform(0.002, 0.01, n) * 1e7
    return h, l, c, v


def run(eng, code, h, l, c, v, S, F=1.0, frm=0, to=None, split_at=None, ratio=2.0, full=True):
    to = len(c) if to is None else to
    for t in range(frm, to):
        f = ratio if (split_at is not None and t >= split_at) else 1.0
        spl = {code: ratio} if split_at is not None and t == split_at else None
        Sv = np.full(len(eng.codes), S * f)
        eng.step(f"2026-{1 + t // 28:02d}-{1 + t % 28:02d}" if t < 300 else f"2027-{1 + (t - 300) // 28:02d}-{1 + (t - 300) % 28:02d}",
                 {code: (c[t] / f, h[t] / f, l[t] / f, c[t] / f, v[t] * f)}, Sv, np.full(len(eng.codes), F), spl,
                 {code} if full else None)


def test_split_equivalence():
    h, l, c, v = series()
    a = CD.Engine(["1111"]); run(a, "1111", h, l, c, v, 1e7)
    b = CD.Engine(["1111"]); run(b, "1111", h, l, c, v, 1e7, split_at=320)
    Ja, ma = a.metrics([0]); Jb, mb = b.metrics([0])
    ok(abs(ma["pr"][0] - mb["pr"][0]) < 1e-4, f"分割をはさんでも含み益の割合が同じ {ma['pr'][0]} {mb['pr'][0]}")
    ok(abs(ma["avg"][0] / 2 - mb["avg"][0]) / ma["avg"][0] < 1e-4, "分割のあとの平均買値は半分")
    ok(abs(ma["pr_lo"][0] - mb["pr_lo"][0]) < 1e-4 and abs(ma["pr_hi"][0] - mb["pr_hi"][0]) < 1e-4, "幅も同じ")


def test_conservation_and_extremes():
    h, l, c, v = series()
    e = CD.Engine(["1111"]); run(e, "1111", h, l, c, v, 1e7)
    s = e.D[:, 0, :].sum(axis=1)
    ok(np.allclose(s, 1.0, atol=1e-3), f"分布の合計が1 {s}")
    # 全部の値より上で引けた日 → 含み益ほぼ100%・下 → ほぼ0%
    top = c[-1]
    k = 0
    while top < h.max() * 1.5:          # 出来高0で1日1.9倍ずつ上げる（2.5倍の飛びの守りに当たらない）
        top *= 1.9
        k += 1
        e.step(f"2028-01-{k:02d}", {"1111": (top, top, top, top, 0.0)}, np.array([1e7]), np.array([1.0]))
    J, m = e.metrics([0]); ok(m["pr"][0] > 0.99, f"高値の上 {m['pr'][0]}")
    ok(m["wall_s"][0] == 0, "終値より上に何も無ければ壁なし")


def test_wall_within_30pct():
    h, l, c, v = series(seed=3)
    e = CD.Engine(["1111"]); run(e, "1111", h, l, c, v, 1e7)
    J, m = e.metrics([0])
    if m["wall_s"][0] > 0:
        mid = math.sqrt(m["wall_lo"][0] * m["wall_hi"][0])
        ok(m["c"][0] < mid <= m["c"][0] * 1.31, f"最大のしこりは終値〜+30% {m['c'][0]} {mid}")
    if np.isfinite(m["far_p"][0]):
        ok(m["far_p"][0] > m["c"][0] * 1.3, "遠いしこりは+30%より上")
    ok(m["q05"][0] < m["q95"][0], "9割の範囲")


def test_warm_start_and_rcap():
    h, l, c, v = series(n=300)
    e = CD.Engine(["1111"])
    run(e, "1111", h, l, c, v, 1e7, to=CD.WARM - 1)
    ok(e.mode[0] == 1, "最初の250日は開始の分布を作り中")
    run(e, "1111", h, l, c, v, 1e7, frm=CD.WARM - 1, to=CD.WARM)
    ok(e.mode[0] == 2 and np.allclose(e.resid[:, 0], 1.0), "250日目に開始の分布で始まる")
    # 出来高が分母の10倍でも、1日で入れ替わるのは α×上限まで
    r0 = e.resid[:, 0].copy()
    e.step("2027-12-01", {"1111": (c[-1], h[-1], l[-1], c[-1], 1e8)}, np.array([1e7]), np.array([1.0]))
    for k, (a, d) in enumerate(CD.CFGS):
        ok(abs(e.resid[k, 0] / r0[k] - (1 - a * CD.RCAP)) < 1e-6, f"回転率の上限 {a}/{d}")
    # 途中から上場（init_full に入っていない）は上場日の K で始まる
    e2 = CD.Engine(["2222"])
    e2.step("2026-01-05", {"2222": (500, 520, 480, 510, 1e6)}, np.array([1e7]), np.array([1.0]))
    ok(e2.mode[0] == 2, "新規上場は初日から回る")
    J, m = e2.metrics([0])
    ok(m["conf"][0] == "N", "60営業日未満はデータ不足")


def test_jump_guard():
    h, l, c, v = series(n=280)
    e = CD.Engine(["1111"]); run(e, "1111", h, l, c, v, 1e7)
    D0 = e.D.copy()
    e.step("2028-02-01", {"1111": (c[-1] * 3, c[-1] * 3, c[-1] * 3, c[-1] * 3, 1e6)}, np.array([1e7]), np.array([1.0]))
    ok(np.array_equal(D0, e.D) and "1111" in e.skipped, "分割の記録なしの3倍は使わない")


def test_save_load(tmp):
    h, l, c, v = series()
    e = CD.Engine(["1111", "2222"])
    run(e, "1111", h, l, c, v, 1e7)
    run(e, "2222", h * 0.3, l * 0.3, c * 0.3, v, 5e6, full=False)
    p = Path(tmp) / "s.npz"
    e.save(p)
    f = CD.Engine.load(p)
    Ja, ma = e.metrics([0, 1]); Jb, mb = f.metrics([0, 1])
    ok(np.allclose(ma["pr_all"], mb["pr_all"], atol=3e-3), f"控えを読み直しても同じ（対数255段）{np.abs(ma['pr_all'] - mb['pr_all']).max()}")
    ok(np.allclose(ma["avg"], mb["avg"], rtol=3e-3), "平均買値も同じ")
    ok(f.date == e.date and f.codes == e.codes, "日付と銘柄")


def write_bars(root, day, rows):
    p = root / "data" / "bars" / day[:4] / f"{day}.csv.gz"
    p.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(p, "wt", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["date", "code", "open", "high", "low", "close", "adj_close", "volume"])
        for c, (hh, ll, cc, vv) in rows.items():
            w.writerow([day, c, cc, hh, ll, cc, cc, int(vv)])


def check_cost_map_cols(cm, root):
    """cost_map.json の列: 前の7列は位置も意味も同じ・後ろに平均買値の幅としこり（銘柄ごとのファイルと同じ値）"""
    cols = cm["cols"]
    ok(cols[:7] == ["t", "p", "lo", "hi", "q", "a", "d"], f"前の7列は変えない {cols}")
    ok(cols[7:] == ["al", "ah", "wl", "wh", "wd", "ws"], f"足した列 {cols}")
    ok(all(c in cm["col_names"] for c in cols), "列の説明")
    ok(all(len(r) == len(cols) for r in cm["rows"]), "行の長さ")
    ix = {c: i for i, c in enumerate(cols)}
    for r in cm["rows"]:
        if r[ix["q"]] == "N":
            ok(all(r[ix[c]] is None for c in cols[7:]), "データ不足は空")
            continue
        al, ah, a = r[ix["al"]], r[ix["ah"]], r[ix["a"]]
        ok(al is not None and ah is not None and al <= ah, f"平均買値の幅 {al} {ah}")
        ok(al * 0.99 <= a <= ah * 1.01, f"中心は幅の中（a は有効3桁） {al} {a} {ah}")
        p = root / "docs" / "cost" / f"{r[0]}.json"
        if p.exists():
            js = json.loads(p.read_text(encoding="utf-8"))
            ok([al, ah] == js["avg"][1:], f"銘柄ごとのファイルの平均買値の幅と同じ {al} {ah} {js['avg']}")
            w = js["wall"]
            got = [r[ix[c]] for c in ("wl", "wh", "wd", "ws")]
            ok(got == ([w["lo"], w["hi"], w["d"], w["s"]] if w else [None] * 4), f"しこりも同じ {got} {w}")
        if r[ix["wl"]] is not None:
            ok(r[ix["wl"]] < r[ix["wh"]] and 0 <= r[ix["wd"]] <= 31 and 0 < r[ix["ws"]] <= 100, "しこりの値の範囲")


def test_cost_map_no_wall():
    """終値より上に何も無い銘柄は、しこりの列が空（None）"""
    h, l, c, v = series()
    e = CD.Engine(["1111"]); run(e, "1111", h, l, c, v, 1e7)
    top, k = c[-1], 0
    while top < h.max() * 1.5:
        top *= 1.9; k += 1
        e.step(f"2028-01-{k:02d}", {"1111": (top, top, top, top, 0.0)}, np.array([1e7]), np.array([1.0]))
    J, m = e.metrics([0])
    cm = CD.cost_map(e, J, m, "2028-01-10")
    r = cm["rows"][0]
    ok(len(r) == 13 and r[9:] == [None] * 4 and r[7] is not None, f"壁なしの行 {r}")


def test_end_to_end(tmp):
    root = Path(tmp) / "repo"
    (root / "docs" / "yuho").mkdir(parents=True)
    with open(root / "tenbagger_universe.csv", "w", encoding="utf-8") as f:
        f.write("code,name,sector33\n1111,テストA,化学\n2222,テストB,化学\n")
    (root / "tenbagger_shares.csv").write_text("ticker,shares\n1111.T,10000000\n2222.T,5000000\n", encoding="utf-8")
    (root / "docs" / "yuho" / "latest.json").write_text(json.dumps({"codes": {"1111": {"sub": "2026-01-01", "fy": [
        {"ni": 1e9, "eps": 100.0, "na": 2e10, "bps": 2000.0}]}}}), encoding="utf-8")
    (root / "docs" / "splits.json").write_text(json.dumps({"by_code": {"2222": [{"ex": "2024-03-04", "ratio": 2}]}}), encoding="utf-8")
    (root / "docs" / "holders.json").write_text(json.dumps({"by_issuer": {"1111": [
        {"name": "親会社株式会社", "ratio_own": 40.0}, {"name": "ブラックロック・ジャパン株式会社", "ratio_own": 6.0}]}}), encoding="utf-8")
    px = Path(tmp) / "px5y"; px.mkdir()
    h, l, c, v = series(n=320)
    import datetime as dt
    days, d = [], dt.date(2025, 1, 6)
    while len(days) < 325:
        if d.weekday() < 5:
            days.append(d.isoformat())
        d += dt.timedelta(days=1)
    with gzip.open(px / "chunk_0000.csv.gz", "wt", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["date", "code", "open", "high", "low", "close", "adj_close", "volume"])
        for t in range(300):
            w.writerow([days[t], "1111", c[t], h[t], l[t], c[t], c[t], int(v[t])])
            if t >= 200:   # 2222 は途中から（新規上場の扱い）
                w.writerow([days[t], "2222", c[t] / 2, h[t] / 2, l[t] / 2, c[t] / 2, c[t] / 2, int(v[t])])
    (px / "meta.json").write_text(json.dumps({"adjusted_asof": days[299]}), encoding="utf-8")
    for t in range(300, 305):
        write_bars(root, days[t], {"1111": (h[t], l[t], c[t], v[t]), "2222": (h[t] / 2, l[t] / 2, c[t] / 2, v[t])})
    S, F, src = CD.load_denoms(root, ["1111", "2222"], days[304], CD.split_table(root))
    ok(abs(S[0] - 1e7) < 1 and abs(F[0] - 0.6) < 1e-9, f"分母: 有報の利益÷EPS・運用会社を除いた大量保有 {S[0]} {F[0]}")
    ok(abs(S[1] - 5e6) < 1, "有報が無ければ yfinance")
    eng, *_ = CD.run_init(root, px)
    CD.finish_outputs(root, eng, eng.date, True, {})
    ok(eng.date == days[304], f"初回は data/bars の最後の日まで {eng.date}")
    ok((root / "data" / "cost" / "state.npz").exists(), "控え")
    js = json.loads((root / "docs" / "cost" / "1111.json").read_text(encoding="utf-8"))
    ok(js["label"] == "推定" and len(js["bins"]["v"]) > 3 and js["pr"][1] <= js["pr"][0] <= js["pr"][2], "銘柄ごとの公開ファイル")
    ok(len(json.dumps(js, ensure_ascii=False)) < 4000, "1件 4KB 未満")
    ok("volume" not in json.dumps(js) and "v" in js["bins"], "出来高を入れない")
    cm = json.loads((root / "docs" / "cost_map.json").read_text(encoding="utf-8"))
    ok([r[0] for r in cm["rows"]] == ["1111", "2222"], "cost_map の銘柄")
    check_cost_map_cols(cm, root)
    txt = json.dumps(cm, ensure_ascii=False) + json.dumps(js, ensure_ascii=False) + \
        (root / "docs" / "cost" / "index.json").read_text(encoding="utf-8")
    bad = [w for w in NG_WORDS if w in txt]
    ok(not bad, f"公開ファイルの言葉 {bad}")
    ok(txt.count("推定") >= 3, "推定を付ける")
    # 毎日: 1日足して進める（同じ週なら控えは書き換えない）
    mt = (root / "data" / "cost" / "state.npz").stat().st_mtime_ns
    write_bars(root, days[305], {"1111": (h[305], l[305], c[305], v[305]), "2222": (h[305] / 2, l[305] / 2, c[305] / 2, v[305])})
    CD.run_daily(root)
    ok((root / "data" / "cost" / "days" / days[305][:4] / f"{days[305]}.csv.gz").exists(), "その日の指標")
    cm2 = json.loads((root / "docs" / "cost_map.json").read_text(encoding="utf-8"))
    ok(cm2["asof"] == days[305], "cost_map は毎日")
    same_month = days[305][:7] == days[304][:7]
    ok(((root / "data" / "cost" / "state.npz").stat().st_mtime_ns == mt) == same_month, "控えは月の最初の取引日だけ")
    same_week = CD.week_key(days[305]) == CD.week_key(days[304])
    ix = json.loads((root / "docs" / "cost" / "index.json").read_text(encoding="utf-8"))
    ok((ix["asof"] == days[304]) == same_week, "銘柄ごとのファイルは週の最初の取引日だけ")
    # 2日目: 控え（前の月）から進め直しても、1日目の結果と同じ日付まで来る
    write_bars(root, days[306], {"1111": (h[306], l[306], c[306], v[306]), "2222": (h[306] / 2, l[306] / 2, c[306] / 2, v[306])})
    CD.run_daily(root)
    cm3 = json.loads((root / "docs" / "cost_map.json").read_text(encoding="utf-8"))
    ok(cm3["asof"] == days[306], "控えから2日進める")


if __name__ == "__main__":
    test_split_equivalence()
    test_conservation_and_extremes()
    test_wall_within_30pct()
    test_warm_start_and_rcap()
    test_jump_guard()
    test_cost_map_no_wall()
    with tempfile.TemporaryDirectory() as tmp:
        test_save_load(tmp)
        test_end_to_end(tmp)
    print("cost_dist: " + ("すべて通った" if NG == 0 else f"{NG}件 NG"))
    sys.exit(1 if NG else 0)
