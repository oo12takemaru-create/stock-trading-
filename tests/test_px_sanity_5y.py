# -*- coding: utf-8 -*-
"""長い並び（予兆レーダーの5年の日足）用の検査 px_sanity.clean と、その使い手のテスト（2026-11-21）

  python -X utf8 -m unittest tests.test_px_sanity_5y -v

  - clean() の新しい規則: e 値の付いていない区間（60営業日以上 同じ終値）・d 単発の桁とび・f 桁違いの値の塊だけを捨てる
  - check()（マイボードの px1y が使う）は変えていない: 公開中の docs/px1y が全部 ok のまま
  - ledger2_panel: 異常の無い銘柄の値は1つも変わらない・直した銘柄は O/H/L/C/V/K がそろって直る
  - 当日記録の守り: 汚れた銘柄は値動きの出来事を飛ばす・答え合わせで「データの異常」の invalid
  - daily_bars の守り: 壊れた行だけ書かない（分割の日の飛び・2.5倍は書く）
"""
import gzip
import json
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import px_sanity as S          # noqa: E402


def days(n, start=date(2021, 11, 22)):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def walk(n, p0=1000.0, step=0.01, start=date(2021, 11, 22)):
    ds = days(n, start)
    return [(d, round(p0 * (1 + step * ((i % 7) - 3)), 1)) for i, d in enumerate(ds)]


class TestClean(unittest.TestCase):
    def test_normal_untouched(self):
        pts = walk(1200)
        self.assertEqual(S.clean(pts), ("ok", pts, [], []))

    def test_flat_head_placeholder(self):
        """上場前に同じ値が何百日も並ぶ（5858・6228・4197・7075 の形。上場の日に飛びが無くても見つける）"""
        head = [(d, 950.0) for d in days(450)]
        good = walk(500, p0=1000, start=head[-1][0] + timedelta(days=1))
        st, pts, pr, dropped = S.clean(head + good)
        self.assertEqual(st, "flat")
        self.assertEqual(pts, good)
        self.assertEqual(len(dropped), 450)
        self.assertEqual(pr[0][0], "flat")
        # check() だけでは見つからない（飛びが無い）＝ px1y の結果は変わらない
        self.assertEqual(S.check(head + good)[0], "ok")

    def test_flat_then_jump_9204(self):
        head = [(d, 26.0) for d in days(260)]
        good = walk(300, p0=1277, start=head[-1][0] + timedelta(days=1))
        st, pts, _, _ = S.clean(head + good)
        self.assertEqual(st, "flat")
        self.assertEqual(pts, good)

    def test_flat_then_placeholder_then_listing_5537(self):
        """仮の値 1,059 が448日 → 4:1 の調整で 264.75 が51日 → 上場（1,755）"""
        a = [(d, 1059.0) for d in days(448)]
        b = [(d, 264.75) for d in days(51, a[-1][0] + timedelta(days=1))]
        good = walk(230, p0=1755, start=b[-1][0] + timedelta(days=1))
        st, pts, _, dropped = S.clean(a + b + good, {"2025-10-09": 4.0})
        self.assertEqual(st, "flat+trimmed")
        self.assertEqual(pts, good)
        self.assertEqual(len(dropped), 448 + 51)

    def test_short_flat_kept(self):
        """売買の少ない銘柄の 1〜2か月の同じ値（5年の日足で最長 33営業日）は触らない"""
        pts = walk(300)
        pts = pts[:100] + [(d, pts[99][1]) for d, _ in pts[100:150]] + pts[150:]     # 51日 同じ値
        self.assertEqual(S.clean(pts)[0], "ok")

    def test_spike_1306(self):
        """383円→38円が2日 → その2日だけ捨てる"""
        pts = walk(300, p0=380)
        for k in (150, 151):
            pts[k] = (pts[k][0], round(pts[k][1] / 12, 1))
        st, out, _, dropped = S.clean(pts)
        self.assertEqual(st, "spikes")
        self.assertEqual(dropped, [pts[150][0], pts[151][0]])
        self.assertEqual(len(out), 298)
        self.assertEqual(S.check(pts)[0], "bad")      # check() は今までどおり（書かない）

    def test_cliff_is_not_spike(self):
        """戻らない崖（分割の調整漏れ）は単発の桁とびとして捨てない"""
        pts, _ = S.drop_spikes(walk(60, p0=11040) + walk(60, p0=3680, start=date(2022, 3, 1)))
        self.assertEqual(len(pts), 120)

    def test_relisted_block_8303(self):
        """旧上場の値 → 再上場までの桁違いの値の塊 → 再上場。塊だけ捨て、旧上場の期間は残す"""
        old = walk(460, p0=2800)
        blk = [(d, 55319998464.0) for d in days(12, old[-1][0] + timedelta(days=30))]
        new = walk(230, p0=1800, start=blk[-1][0] + timedelta(days=30))
        st, pts, _, dropped = S.clean(old + blk + new)
        self.assertEqual(st, "blocks")
        self.assertEqual(pts, old + new)
        self.assertEqual(len(dropped), 12)

    def test_squeeze_out_7523(self):
        r = 1 / 618000
        raw = walk(1200, p0=1884)
        cut = raw[-3][0]
        adj = [(d, c / r) for d, c in raw]
        st, pts, _, dropped = S.clean(adj, {cut: r})
        self.assertEqual(st, "fixed")
        self.assertAlmostEqual(pts[-1][1], raw[-4][1], places=3)
        self.assertEqual(dropped, [d for d, _ in raw[-3:]])

    def test_unadjusted_split_8227(self):
        pre = walk(600, p0=11040)
        post = walk(180, p0=3680, start=pre[-1][0] + timedelta(days=1))
        st, pts, _, dropped = S.clean(pre + post, {(post[0][0] + timedelta(days=1)).isoformat(): 3})
        self.assertEqual(st, "adjusted")
        self.assertEqual(dropped, [])
        self.assertEqual(S.problems(pts), [])

    def test_real_moves_not_caught(self):
        """値幅制限の拡大のストップ高（215→535 の 2.49倍・7603）・連続ストップ高・低位株の 2.5倍は異常でない"""
        pts = walk(100, p0=115)
        seq = [115, 165, 215, 535, 635, 535]
        ds = days(len(seq), pts[-1][0] + timedelta(days=1))
        pts = pts + list(zip(ds, map(float, seq))) + walk(50, p0=535, start=ds[-1] + timedelta(days=1))
        self.assertEqual(S.clean(pts)[0], "ok")
        low = walk(100, p0=30) + [(date(2022, 6, 1), 80.0)] + walk(50, p0=80, start=date(2022, 6, 2))
        self.assertEqual(S.clean(low)[0], "ok")

    def test_unfixable_bad(self):
        pts = walk(100) + walk(30, p0=10000, start=date(2022, 6, 1)) + walk(100, start=date(2022, 8, 1))
        st, out, _, dropped = S.clean(pts)
        self.assertEqual(st, "bad")
        self.assertIsNone(out)
        self.assertEqual(len(dropped), 230)


class TestColumns(unittest.TestCase):
    def test_clean_columns_only_flagged(self):
        ds = [d.isoformat() for d in days(300)]
        C = np.full((300, 3), np.nan)
        C[:, 0] = [c for _, c in walk(300)]
        C[:200, 1] = 950.0
        C[200:, 1] = [c for _, c in walk(100, p0=1000)]
        C[50:, 2] = [c for _, c in walk(250, p0=500)]
        res = S.clean_columns(ds, C, ["A", "B", "C"], {})
        self.assertEqual(sorted(res), [1])
        st, fac, _ = res[1]
        self.assertEqual(st, "flat")
        self.assertTrue(np.isnan(fac[:200]).all())
        self.assertTrue((fac[200:] == 1.0).all())


class TestPx1yUnchanged(unittest.TestCase):
    def test_check_same_as_main(self):
        """マイボードの px1y が使う check() は変えていない: docs/px1y の全銘柄で、origin/main の px_sanity.py と同じ結果"""
        import importlib.util
        import subprocess
        files = sorted((ROOT / "docs" / "px1y").glob("*.json"))
        try:
            src = subprocess.run(["git", "-C", str(ROOT), "show", "origin/main:px_sanity.py"], capture_output=True, check=True).stdout
        except Exception:
            self.skipTest("origin/main の px_sanity.py が読めない")
        if not files:
            self.skipTest("docs/px1y が無い")
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "px_sanity_main.py"
            p.write_bytes(src)
            spec = importlib.util.spec_from_file_location("px_sanity_main", p)
            M = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(M)
        diff, n = [], 0
        for f in files:
            j = json.loads(f.read_text(encoding="utf-8"))
            if not j.get("x") or not j.get("c"):
                continue
            pts, d = [], date.fromisoformat(j["d0"])
            for dx, c in zip(j["x"], j["c"]):
                d = d + timedelta(days=dx)
                pts.append((d, c))
            n += 1
            if S.check(pts) != M.check(pts):
                diff.append(f.stem)
        self.assertGreater(n, 3000)
        self.assertEqual(diff, [])


class TestPanel(unittest.TestCase):
    def _panel(self, rows, splits=None):
        import ledger2_panel as P
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "a.csv.gz"
            with gzip.open(p, "wt", encoding="utf-8") as f:
                f.write("date,code,open,high,low,close,adj_close,volume\n")
                for d, c, o, h, l, cl, v in rows:
                    f.write(f"{d},{c},{o},{h},{l},{cl},{cl},{v}\n")
            return P.Panel([([p], "2026-11-21")], splits or {})

    def test_untouched_and_fixed(self):
        rows = []
        for d, c in walk(300):
            rows.append((d.isoformat(), "1111", c, c * 1.01, c * 0.99, c, 1000))
        for i, (d, c) in enumerate(walk(300)):
            cl = 950.0 if i < 120 else c
            rows.append((d.isoformat(), "2222", cl, cl, cl, cl, 0 if i < 120 else 500))
        pre = walk(150, p0=11040)
        post = walk(150, p0=3680, start=pre[-1][0] + timedelta(days=1))
        for d, c in pre + post:
            rows.append((d.isoformat(), "3333", c, c, c, c, 100))
        ex = (post[0][0] + timedelta(days=1)).isoformat()
        pn = self._panel(rows, {"3333": [(ex, 3.0)]})
        j1, j2, j3 = (pn.cpos[c] for c in ("1111", "2222", "3333"))
        self.assertNotIn("1111", pn.sanity)
        self.assertEqual(float(pn.C[0, j1]), walk(300)[0][1])
        self.assertEqual(pn.sanity["2222"]["status"], "flat")
        self.assertTrue(np.isnan(pn.C[:120, j2]).all() and np.isnan(pn.V[:120, j2]).all())
        self.assertEqual(int(np.isfinite(pn.C[:, j2]).sum()), 180)
        self.assertEqual(pn.sanity["3333"]["status"], "adjusted")
        self.assertAlmostEqual(float(pn.C[0, j3]), pre[0][1] / 3, places=6)
        self.assertAlmostEqual(float(pn.H[0, j3]), pre[0][1] / 3, places=6)
        self.assertAlmostEqual(float(pn.V[0, j3]), 300, places=6)
        # 記録の日と同じ日の値（R）: 調整漏れを直した後は、その日に見えていた値（11,040 前後）に戻る
        self.assertAlmostEqual(float(pn.C[0, j3] * pn.K[0, j3]), pre[0][1], places=6)
        # 汚れた日: 2222 は先頭の120日、3333 は崖の日
        self.assertEqual(pn.sanity["3333"]["dirty"], [post[0][0].isoformat()])
        self.assertIn("2222", pn.dirty_codes(walk(300)[130][0].isoformat(), 30))
        self.assertNotIn("2222", pn.dirty_codes(walk(300)[299][0].isoformat(), 30))

    def test_squeeze_out_keeps_seen_price(self):
        """極端な分割の記録（7523）: 調整済み A を戻しても、その日の値 R（= A×K）は変わらない"""
        r = 1 / 618000
        raw = walk(300, p0=1884)
        ex = raw[-3][0].isoformat()
        rows = [(d.isoformat(), "7523", c / r, c / r, c / r, c / r, 100 * r) for d, c in raw]
        pn = self._panel(rows, {"7523": [(ex, r)]})
        j = pn.cpos["7523"]
        self.assertEqual(pn.sanity["7523"]["status"], "fixed")
        self.assertAlmostEqual(float(pn.C[0, j]), raw[0][1], places=3)
        self.assertAlmostEqual(float(pn.C[0, j] * pn.K[0, j]), raw[0][1], places=3)
        self.assertAlmostEqual(float(pn.V[0, j]), 100, places=3)
        self.assertTrue(np.isnan(pn.C[-3:, j]).all())
        # 汚れた日は記録の日以後だけ（その前の日に見えていた値は正しかった）
        self.assertEqual(pn.sanity["7523"]["dirty"], [d.isoformat() for d, _ in raw[-3:]])


class _FakePanel:
    def __init__(self, dirty):
        self.dirty = dirty                 # {code: [日付]}

    def dirty_codes(self, d, lb):
        lo = (date.fromisoformat(d) - timedelta(days=lb)).isoformat()
        return {c: "flat" for c, ds in self.dirty.items() if any(lo <= x <= d for x in ds)}


class TestLiveGuard(unittest.TestCase):
    def _src(self, dirty):
        import ledger2_detect as L
        s = L.Sources(root=ROOT)
        s._c["panel"] = _FakePanel(dirty)
        return s

    def test_skip_price_events_only_in_window(self):
        import ledger2_detect as L
        s = self._src({"8227": ["2026-02-18"]})
        d = "2026-11-20"
        self.assertTrue(L.dirty_skip(d, {"code": "8227", "event_type": "high52_close"}, s))      # 366日の中
        self.assertFalse(L.dirty_skip(d, {"code": "8227", "event_type": "volume_surge"}, s))     # 31日の外
        self.assertFalse(L.dirty_skip(d, {"code": "7203", "event_type": "high52_close"}, s))     # 汚れの無い銘柄
        self.assertFalse(L.dirty_skip(d, {"code": "8227", "event_type": "signal_treasure_contra"}, s))  # 取り込みは飛ばさない

    def test_anomaly_invalid_rows(self):
        import ledger2_score as LS
        s = self._src({"8227": ["2026-02-18"], "9225": ["2023-06-27"]})
        evs = [{"event_id": "20261120_8227_signal_treasure_contra", "date": "2026-11-20", "code": "8227",
                "event_type": "signal_treasure_contra", "origin": "live"},
               {"event_id": "20261120_8227_signal_movers", "date": "2026-11-20", "code": "8227",
                "event_type": "signal_movers", "origin": "live"},
               {"event_id": "20261120_9225_low52_close", "date": "2026-11-20", "code": "9225",
                "event_type": "low52_close", "origin": "live"},
               {"event_id": "20211201_8227_high52_close", "date": "2021-12-01", "code": "8227",
                "event_type": "high52_close", "origin": "backfill"}]
        closed = set()
        rows = LS.mark_anomalies(evs, closed, s, "2026-11-21T19:00:00+09:00")
        self.assertEqual([r["event_id"] for r in rows], ["20261120_8227_signal_treasure_contra"])
        self.assertEqual(rows[0]["status"], "invalid")
        self.assertEqual(rows[0]["anomaly"], "px_sanity")
        self.assertIn("データの異常", rows[0]["reason"])
        # 2回目は何も足さない
        self.assertEqual(LS.mark_anomalies(evs, closed, s, "x"), [])

    def test_context_nulls_dirty_fields(self):
        import ledger2_detect as L
        s = self._src({"8227": ["2026-02-18"]})
        s._c["prices"] = {"trade_date": "2026-11-20", "items": {
            "8227": {"p": 3619, "c": 0.5, "c20": 9.7, "hi52": -68.1, "g25": 5.6, "vr": 0.8, "v": 5000},
            "7203": {"p": 3000, "c": 0.5, "c20": 1.0, "hi52": -2.0, "g25": 1.0, "vr": 1.0, "v": 9000}}}
        s._c["thermo_hist"] = {}
        s._c["thermo100"] = None
        s._c["universe"] = {}
        s._c["listed"] = {}
        ch = L.context("2026-11-20", "8227", s)["chart"]
        self.assertIsNone(ch["from_high52"])
        self.assertEqual(ch["ret20"], 9.7)                      # 31日の外の欄はそのまま
        self.assertEqual(L.context("2026-11-20", "7203", s)["chart"]["from_high52"], -2.0)

    def test_yochou_drops_anomaly_events(self):
        import yochou as Y
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            (base / "events").mkdir()
            (base / "scores").mkdir()
            ev = [{"event_id": "e1", "date": "2026-11-20", "code": "8227", "event_type": "signal_x", "origin": "live",
                   "base_date": "2026-11-24"},
                  {"event_id": "e2", "date": "2026-11-20", "code": "7203", "event_type": "signal_x", "origin": "live",
                   "base_date": "2026-11-24"}]
            (base / "events" / "2026-11.jsonl").write_text("\n".join(json.dumps(e) for e in ev) + "\n", encoding="utf-8")
            sc = [{"event_id": "e1", "h": 1, "ret": 1.0}, {"event_id": "e2", "h": 1, "ret": 2.0},
                  {"event_id": "e1", "status": "invalid", "anomaly": "px_sanity", "reason": "データの異常"}]
            (base / "scores" / "2026-11.jsonl").write_text("\n".join(json.dumps(r) for r in sc) + "\n", encoding="utf-8")
            events, scores = Y.load_all(base)
            self.assertIn("e1", events)
            self.assertEqual(sorted(scores), [("e2", 1)])


class TestDailyBarsGuard(unittest.TestCase):
    def test_guard_rows(self):
        import daily_bars as DB
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            DB.write_gz(out / "2026" / "2026-11-19.csv.gz",
                        [["2026-11-19", c, "", "", "", str(v), "", "100"] for c, v in
                         (("1306", 383.0), ("7203", 3000.0), ("6861", 60000.0), ("7603", 215.0), ("9999", 1000.0))])
            rows = [["2026-11-20", c, "", "", "", str(v), "", "100"] for c, v in
                    (("1306", 38.3), ("7203", 3010.0), ("6861", 6000.0), ("7603", 535.0), ("9999", 2e9), ("1111", 500.0))]
            keep, skipped = DB.guard_rows(rows, out, date(2026, 11, 20), splits={"6861": [("2026-11-20", 10.0)]})
            self.assertEqual([r[1] for r in keep], ["7203", "6861", "7603", "1111"])
            self.assertEqual([c for c, _ in skipped], ["1306", "9999"])


if __name__ == "__main__":
    unittest.main()
