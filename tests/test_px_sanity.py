# -*- coding: utf-8 -*-
"""終値の並びの検査（px_sanity.py）と、マイボードの書き出し（myboard_px.py）での使い方のテスト

  python -X utf8 -m unittest tests.test_px_sanity -v
"""
import json
import pickle
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import px_sanity as S       # noqa: E402
import myboard_px as M      # noqa: E402


def days(n, start=date(2025, 11, 18)):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def walk(n, p0=1000.0, step=0.01, start=date(2025, 11, 18)):
    """ゆっくり上下するふつうの並び"""
    ds = days(n, start)
    return [(d, round(p0 * (1 + step * ((i % 7) - 3)), 1)) for i, d in enumerate(ds)]


class TestProblems(unittest.TestCase):
    def test_normal_ok(self):
        self.assertEqual(S.problems(walk(240)), [])

    def test_stop_high_run_ok(self):
        # ストップ高が10日続く（毎日 +30% 前後）: 1日の飛びは 2.5倍に届かないので異常ではない
        ds = days(30)
        pts, p = [], 300.0
        for i, d in enumerate(ds):
            if 10 <= i < 20:
                p += 80          # 200円以上500円未満の値幅は80円
            pts.append((d, p))
        self.assertEqual(S.problems(pts), [])

    def test_low_price_small_jump_ignored(self):
        # 安い方が50円未満の 2.5倍（30円→80円 は値幅の拡大で起こり得る）は見ない
        ds = days(3)
        self.assertEqual(S.problems([(ds[0], 30), (ds[1], 80), (ds[2], 81)]), [])

    def test_big_jump_low_side(self):
        # 383円→38円 のような 1/10 は、安い方が50円未満でも見る
        ds = days(3)
        kinds = [k for k, _, _ in S.problems([(ds[0], 383), (ds[1], 38), (ds[2], 389)])]
        self.assertEqual(kinds, ["jump", "jump"])

    def test_huge_and_nonpos(self):
        ds = days(3)
        kinds = {k for k, _, _ in S.problems([(ds[0], 1.2e9), (ds[1], 0), (ds[2], 1000)])}
        self.assertIn("huge", kinds)
        self.assertIn("nonpos", kinds)

    def test_ref(self):
        pts = walk(30)
        self.assertEqual(S.problems(pts, ref=pts[-1][1]), [])
        self.assertEqual([k for k, _, _ in S.problems(pts, ref=pts[-1][1] * 10)], ["ref"])


class TestCheck(unittest.TestCase):
    def test_squeeze_out_7523(self):
        """株式併合（比率 1/618,000）を分割として取り込んだ並び → 調整前に戻し、併合の日以後を捨てる"""
        r = 1 / 618000
        raw = walk(240, p0=1884)
        cut = raw[-3][0]                          # 併合の日（最後の3日は廃止後の値）
        adj = [(d, c / r) for d, c in raw]        # yfinance は前の値 ÷ 比率 で調整する
        st, pts, pr = S.check(adj, {cut: r})
        self.assertEqual(st, "fixed")
        self.assertEqual(pts[-1][0], raw[-4][0])
        self.assertAlmostEqual(pts[-1][1], raw[-4][1], places=3)
        self.assertTrue(all(c < 1e5 for _, c in pts))
        # 分割の記録が無ければ書かない
        self.assertEqual(S.check(adj)[0], "bad")

    def test_relisted_head_8303(self):
        """再上場より前の値が桁違い → 後ろだけ使う"""
        good = walk(220, p0=1800, start=date(2025, 12, 18))
        head = [(d, 55319998464.0) for d in days(20)]
        st, pts, _ = S.check(head + good, ref=good[-1][1])
        self.assertEqual(st, "trimmed")
        self.assertEqual(pts, good)

    def test_placeholder_head_5537(self):
        """上場前の日に同じ値が並ぶ → 後ろだけ使う"""
        good = walk(200, p0=1755, start=date(2025, 12, 15))
        head = [(d, 264.8) for d in days(19)]
        st, pts, _ = S.check(head + good)
        self.assertEqual(st, "trimmed")
        self.assertEqual(pts[0], good[0])

    def test_unadjusted_split_8227(self):
        """分割の調整が1日ずれて崖ができた → 記録の比率で前の値を割る"""
        pre = walk(60, p0=11040)
        post = walk(60, p0=3680, start=pre[-1][0] + timedelta(days=1))
        st, pts, _ = S.check(pre + post, {(post[0][0] + timedelta(days=1)).isoformat(): 3})
        self.assertEqual(st, "adjusted")
        self.assertEqual(S.problems(pts), [])
        self.assertAlmostEqual(pts[0][1], pre[0][1] / 3, places=3)
        # 比率が合わない記録では直さない（どちらの側が正しいか分からない → 書かない）
        self.assertEqual(S.check(pre + post, {post[0][0].isoformat(): 10})[0], "bad")

    def test_middle_jump_not_trimmed(self):
        """途中の2日だけ 1/10（1306 の形）→ 先頭を切って直すことはしない（書かない）"""
        pts = walk(100, p0=380)
        pts[50] = (pts[50][0], pts[50][1] / 10)
        self.assertEqual(S.check(pts)[0], "bad")

    def test_real_split_adjusted_by_yahoo_ok(self):
        """実際の分割で Yahoo の調整が合っている銘柄（飛びが無い）は、記録があっても触らない"""
        pts = walk(240)
        st, out, _ = S.check(pts, {pts[100][0].isoformat(): 5})
        self.assertEqual(st, "ok")
        self.assertIs(out, pts)


class TestMyboard(unittest.TestCase):
    """myboard_px.main を小さなキャッシュで走らせる（取得なし）"""

    def run_main(self, close, prices=None, splits=None, fetched=None, old=None):
        import pandas as pd
        tmp = Path(tempfile.mkdtemp())
        uni = [{"t": f"{c}.T", "c": c, "n": c} for c in close]
        ser = {f"{c}.T": pd.Series([v for _, v in pts], index=pd.to_datetime([d for d, _ in pts]))
               for c, pts in close.items()}
        with open(tmp / "c.pkl", "wb") as f:
            pickle.dump({"saved": "test", "uni": uni, "close": ser}, f)
        (tmp / "prices.json").write_text(json.dumps(prices or {}), encoding="utf-8")
        (tmp / "splits.json").write_text(json.dumps(splits or {}), encoding="utf-8")
        out = tmp / "out"
        for code, body in (old or {}).items():
            out.mkdir(exist_ok=True)
            (out / f"{code}.json").write_text(json.dumps(body), encoding="utf-8")
        argv = ["myboard_px.py", "--cache", str(tmp / "c.pkl"), "--out", str(out), "--full",
                "--prices", str(tmp / "prices.json"), "--splits", str(tmp / "splits.json")]
        with mock.patch.object(sys, "argv", argv), \
                mock.patch.object(M, "fetch_splits", lambda t: (fetched or {}).get(t)), \
                mock.patch("builtins.print"):
            M.main()
        return out

    def test_bad_not_written_and_old_removed(self):
        good = walk(240)
        r = 1 / 618000
        broken = [(d, c / r) for d, c in walk(240, p0=1884)]
        out = self.run_main({"7203": good, "7523": broken}, old={"7523": {"d": "2026-11-12"}})
        self.assertTrue((out / "7203.json").exists())
        self.assertFalse((out / "7523.json").exists())          # 前に書いた壊れた本体も消える
        rec = json.loads((out / "recent.json").read_text(encoding="utf-8"))
        self.assertEqual(rec["count"], 1)

    def test_fixed_with_fetched_splits(self):
        good = walk(240)
        r = 1 / 618000
        raw = walk(240, p0=1884)
        broken = [(d, c / r) for d, c in raw]
        out = self.run_main({"7203": good, "7523": broken}, fetched={"7523.T": {raw[-3][0]: r}})
        o = json.loads((out / "7523.json").read_text(encoding="utf-8"))
        self.assertEqual(o["d"], raw[-4][0].isoformat())
        self.assertEqual(o["p"], M.rnd(raw[-4][1]))
        self.assertLess(o["h1"], 1e5)

    def test_unadjusted_split_from_splits_json(self):
        pre = walk(120, p0=11040)
        post = walk(120, p0=3680, start=pre[-1][0] + timedelta(days=1))
        sj = {"by_code": {"8227": [{"ratio": 3, "ex": (post[0][0] + timedelta(days=1)).isoformat(),
                                    "status": "実施済み"}]}}
        out = self.run_main({"7203": walk(240), "8227": pre + post}, splits=sj)
        o = json.loads((out / "8227.json").read_text(encoding="utf-8"))
        self.assertLess(o["h1"], 4000)

    def test_normal_untouched(self):
        good = walk(240)
        out = self.run_main({"7203": good, "6758": walk(240, p0=4000)})
        o = json.loads((out / "7203.json").read_text(encoding="utf-8"))
        self.assertEqual(o["c"], [M.rnd(c) for _, c in good][-len(o["c"]):])


if __name__ == "__main__":
    unittest.main()
