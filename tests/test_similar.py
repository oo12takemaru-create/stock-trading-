# -*- coding: utf-8 -*-
"""そっくり銘柄・真逆銘柄（similar_weekly.py）のテスト

  python -m unittest tests.test_similar -v
"""
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import similar_weekly as S      # noqa: E402


class TestSimilar(unittest.TestCase):
    def setUp(self):
        rng = np.random.default_rng(7)
        T, n = 240, 30
        mkt = rng.normal(0, 0.01, T)
        R = mkt[:, None] + rng.normal(0, 0.01, (T, n))
        R[:, 1] = R[:, 0] * 0.9 + rng.normal(0, 0.002, T)      # 0 と 1 はそっくり
        R[:, 2] = -R[:, 0] + 2 * mkt + rng.normal(0, 0.002, T)  # 市場を引くと 0 と逆
        R[rng.random((T, n)) < 0.05] = np.nan                   # ところどころ欠け
        self.R = R

    def test_pair_corr_matches_numpy(self):
        corr, N = S.pair_corr(self.R)
        for a, b in [(0, 1), (0, 2), (3, 17), (5, 29)]:
            m = np.isfinite(self.R[:, a]) & np.isfinite(self.R[:, b])
            want = np.corrcoef(self.R[m, a], self.R[m, b])[0, 1]
            self.assertEqual(N[a, b], m.sum())
            self.assertAlmostEqual(corr[a, b], want, places=10)
            self.assertAlmostEqual(corr[a, b], corr[b, a], places=12)
        self.assertTrue(np.isnan(corr[0, 0]))

    def test_min_overlap(self):
        R = self.R.copy()
        R[:60, 4] = np.nan                                   # 重なりが 200 日未満になる
        corr, N = S.pair_corr(R)
        self.assertLess(N[4, 0], S.MIN_OVERLAP)
        self.assertTrue(np.isnan(corr[4, 0]))

    def test_market_removed(self):
        ex, _ = S.pair_corr(S.market_removed(self.R))
        self.assertGreater(ex[0, 1], 0.9)
        self.assertLess(ex[0, 2], -0.5)

    def test_returns_drop_zero_volume_and_jumps(self):
        C = np.array([[100, 50], [110, 50], [111, 150], [112, 151]], dtype=float)
        V = np.array([[1, 1], [1, 0], [1, 5], [1, 5]], dtype=float)
        R = S.returns(C, V)
        self.assertAlmostEqual(R[0, 0], 0.1)
        self.assertTrue(np.isnan(R[0, 1]))    # その日の出来高0
        self.assertTrue(np.isnan(R[1, 1]))    # 前日の出来高0（しかも3倍の飛び）
        self.assertAlmostEqual(R[2, 1], 151 / 150 - 1)

    def test_rank_lists_other_sector(self):
        corr, N = S.pair_corr(S.market_removed(self.R))
        raw, _ = S.pair_corr(self.R)
        codes = [f"{1000 + i}" for i in range(self.R.shape[1])]
        uni = {c: {"name": c, "sector": "A" if i < 2 else "B"} for i, c in enumerate(codes)}
        res = {"codes": codes, "ex": corr, "raw": raw, "N": N, "uni": uni,
               "meta": {"asof": "2026-11-20", "from": "2025-11-19", "n_days": 240}}
        ls = S.rank_lists(res)
        self.assertEqual(int(ls["1000"]["s"][0]), 1)            # いちばん似ているのは 1001
        self.assertEqual(int(ls["1000"]["o"][0]), 2)            # いちばん逆は 1002
        self.assertNotIn(1, [int(j) for j in ls["1000"]["sx"]])  # 同じ業種（A）は異業種の並びに入らない
        obj = S.file_obj(res, 0, ls["1000"])
        self.assertEqual(obj["s"][0], "1001")
        self.assertEqual(len(obj["s"]), 10)
        self.assertEqual(len(obj["r"]["1001"]), 5)


if __name__ == "__main__":
    unittest.main()
