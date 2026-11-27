# -*- coding: utf-8 -*-
"""shuyaku_rank.py（売買代金の週の順位とシェア）のテスト。小さな日足を一時フォルダに作って流す"""
import csv
import gzip
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import shuyaku_rank as S   # noqa: E402

NG = 0


def eq(got, want, label):
    global NG
    if got != want:
        NG += 1
        print(f"  NG {label}\n     期待={want}\n     実際={got}")


def write_bars(root, day, rows, backfill=False):
    if backfill:
        p = root / "data" / "bars_backfill" / f"{day[:7]}.csv.gz"
        mode = "at" if p.exists() else "wt"
    else:
        p = root / "data" / "bars" / day[:4] / f"{day}.csv.gz"
        mode = "wt"
    p.parent.mkdir(parents=True, exist_ok=True)
    new = mode == "wt"
    with gzip.open(p, mode, encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        if new:
            w.writerow(["date", "code", "open", "high", "low", "close", "adj_close", "volume", "origin"])
        for c, close, vol in rows:
            w.writerow([day, c, close, close, close, close, close, vol, "test"])


def setup(root):
    with open(root / "tenbagger_universe.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["code", "name", "sector33"])
        w.writerow(["1301", "極洋", "水産・農林業"])        # 13xx の普通株（範囲で外さない）
        w.writerow(["285A", "キオクシアＨＤ", "電気機器"])   # 英字入りのコード・名前は NFKC
        w.writerow(["7203", "トヨタ自動車", "輸送用機器"])
        w.writerow(["6758", "ソニーG", "電気機器"])
        w.writerow(["7523", "アールビバン", "小売業"])
    # 2026-11-16（月）〜20（金）は backfill、2026-11-24（火）〜26（木）は data/bars（11/23 は休日）
    wk1 = ["2026-11-16", "2026-11-17", "2026-11-18", "2026-11-19", "2026-11-20"]
    for d in wk1:
        write_bars(root, d, [("1301", 100, 1000), ("285A", 1000, 1000), ("7203", 500, 1000), ("6758", 200, 1000),
                             ("1306", 400, 10 ** 9), ("^N225", 50000, 0), ("7523", 1.1e9, 5)], backfill=True)
    for d in ["2026-11-24", "2026-11-25", "2026-11-26"]:
        write_bars(root, d, [("1301", 100, 1000), ("285A", 1000, 1000), ("7203", 500, 3000), ("6758", 200, 1000),
                             ("1306", 400, 10 ** 9)])


with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    setup(root)
    # ── 週の途中（木曜の夜）: 当週は暫定
    rc = S.main(["--root", td, "--today", "2026-11-26", "--weeks", "2"])
    eq(rc, 0, "終了コード")
    rows = S.read_store(root / "data" / "shuyaku")
    wk = {(r["unit"], r["week"], r["code"]): r for r in rows}
    eq(sorted({r["week"] for r in rows}), ["2026-11-16", "2026-11-23"], "週（月曜）")
    eq(wk[("stock", "2026-11-23", "7203")]["rank"], "1", "当週の1位（7203 が出来高3倍で逆転）")
    eq(wk[("stock", "2026-11-16", "285A")]["rank"], "1", "前週の1位（英字入りのコードも入る）")
    eq(wk[("stock", "2026-11-23", "7203")]["provisional"], "1", "当週は暫定")
    eq(wk[("stock", "2026-11-16", "7203")]["provisional"], "0", "前週は確定")
    eq(wk[("stock", "2026-11-23", "7203")]["n_days"], "3", "当週の日数（休日の月曜は数えない）")
    eq(wk[("stock", "2026-11-23", "7203")]["week_end"], "2026-11-26", "週の最後の日")
    eq(("stock", "2026-11-16", "1306") in wk, False, "1306 は銘柄に入れない")
    eq(("stock", "2026-11-16", "^N225") in wk, False, "指数は入れない")
    eq(("stock", "2026-11-16", "7523") in wk, False, "規則1（終値1億円超）の行は使わない")
    eq(("stock", "2026-11-16", "1301") in wk, True, "13xx の普通株は入る")
    # 前週: 285A 5000万×… シェアの合計は 100
    tot = sum(float(r["share_pct"]) for r in rows if r["unit"] == "stock" and r["week"] == "2026-11-16")
    eq(round(tot), 100, "銘柄のシェアの合計（銘柄が少ないので全部）")
    secs = [r for r in rows if r["unit"] == "sector" and r["week"] == "2026-11-16"]
    eq([r["code"] for r in sorted(secs, key=lambda r: int(r["rank"]))], ["電気機器", "輸送用機器", "水産・農林業"], "業種の順位")
    eq(wk[("sector", "2026-11-16", "電気機器")]["tv_oku"], "0.1", "業種の売買代金（億円）")
    # ── 公開 JSON: 売買代金の値を持たない・名前は NFKC
    pub = json.loads((root / "docs" / "rank_shift.json").read_text(encoding="utf-8"))
    eq([w["w"] for w in pub["weeks"]], ["2026-11-16", "2026-11-23"], "公開の週")
    eq(pub["weeks"][-1]["p"], 1, "公開の最後の週は暫定")
    txt = json.dumps(pub, ensure_ascii=False)
    eq("tv" in txt, False, "公開 JSON に売買代金の欄が無い")
    eq(any(x in txt for x in ("50000000", "5e7")), False, "公開 JSON に売買代金の値が無い")
    names = {x["c"]: x["n"] for x in pub["stock"]}
    eq(names.get("285A"), "キオクシアHD", "名前の全角を半角に")
    eq(set(pub), {"name", "note", "updated", "weeks", "sector", "stock"}, "公開 JSON の欄")
    for k in ("資金", "買い", "売り", "注目", "有望", "狙い目", "チャンス", "過熱"):
        eq(k in pub["note"] + pub["name"], False, f"説明文に「{k}」を使わない")

    # ── 金曜の夜に流し直す: 当週は確定に変わる（金曜の足が無くても、今日が金曜より後なら確定）
    S.main(["--root", td, "--today", "2026-11-28", "--weeks", "2"])
    rows = S.read_store(root / "data" / "shuyaku")
    eq({r["provisional"] for r in rows if r["week"] == "2026-11-23"}, {"0"}, "週が終わったら確定")

    # ── 上場廃止: 一覧から消えても codes.json に残る（過去の業種の合計から漏れない）
    with open(root / "tenbagger_universe.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["code", "name", "sector33"])
        w.writerow(["285A", "キオクシアＨＤ", "電気機器"])
    S.main(["--root", td, "--today", "2026-11-28", "--weeks", "2"])
    codes = json.loads((root / "data" / "shuyaku" / "codes.json").read_text(encoding="utf-8"))
    eq(codes.get("7203"), ["トヨタ自動車", "輸送用機器"], "消えた銘柄の業種を残す")
    rows = S.read_store(root / "data" / "shuyaku")
    eq(sum(1 for r in rows if r["unit"] == "sector" and r["week"] == "2026-11-16"), 3, "業種は3つのまま")

    # ── seed: 保存に無い週だけ足す・作り直した週は日足の値が勝つ
    seed = root / "seed.csv"
    with open(seed, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=S.FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerow(dict(unit="stock", code="9999", week="2026-11-09", week_end="2026-11-13", n_days=5, rank=1,
                        share_pct=50, tv_oku=1, provisional=0))
        w.writerow(dict(unit="stock", code="9999", week="2026-11-16", week_end="2026-11-20", n_days=5, rank=1,
                        share_pct=50, tv_oku=1, provisional=0))
    S.main(["--root", td, "--today", "2026-11-28", "--weeks", "2", "--seed", str(seed)])
    rows = S.read_store(root / "data" / "shuyaku")
    eq(sorted({r["week"] for r in rows}), ["2026-11-09", "2026-11-16", "2026-11-23"], "seed の週が足される")
    eq([r["code"] for r in rows if r["week"] == "2026-11-16" and r["rank"] == "1" and r["unit"] == "stock"], ["285A"],
       "日足で作れる週は seed で上書きしない")
    # 作り直さない古い週は、次の実行でも消えない
    S.main(["--root", td, "--today", "2026-11-28", "--weeks", "1"])
    rows = S.read_store(root / "data" / "shuyaku")
    eq("2026-11-09" in {r["week"] for r in rows}, True, "古い週は次の実行でも残る")
    pub = json.loads((root / "docs" / "rank_shift.json").read_text(encoding="utf-8"))
    it = {x["c"]: x for x in pub["stock"]}
    eq(it["9999"]["r"], [1, None, None], "公開: 上位に入った週の外は null")

print("NG" if NG else "OK", f"（NG {NG}）")
sys.exit(1 if NG else 0)
