# -*- coding: utf-8 -*-
"""今日のチャート当てのテスト（標準の unittest）

python -X utf8 -m unittest tests/test_quiz.py
（本番の場所には何も書かない。合成の日足と一時フォルダだけ使う）
"""
import gzip
import json
import random
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import quiz_daily as D   # noqa: E402
import quiz_pool as P    # noqa: E402

QKEYS = {"no", "o", "h", "l", "c", "v"}


def bizdays(start, n):
    d, out = date.fromisoformat(start), []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def series(days, start=1000.0, drift=0.0, seed=1, vol=200000):
    rng = random.Random(seed)
    rows, c = [], start
    for d in days:
        o = c * (1 + rng.uniform(-0.01, 0.01))
        c = c * (1 + drift + rng.uniform(-0.02, 0.02))
        h = max(o, c) * 1.01
        l = min(o, c) * 0.99
        rows.append((d, round(o, 1), round(h, 1), round(l, 1), round(c, 1), float(vol)))
    return rows


class TestWindow(unittest.TestCase):
    def setUp(self):
        self.days = bizdays("2021-01-04", 400)
        self.cal_ix = {d: i for i, d in enumerate(self.days)}

    def test_ok_and_ret(self):
        rows = series(self.days, drift=0.004)
        r = P.check_window(rows, 0, self.cal_ix)
        self.assertIsInstance(r, float)
        want = (rows[139][4] / rows[119][4] - 1) * 100
        self.assertAlmostEqual(r, want, places=9)

    def test_thin(self):
        rows = series(self.days, vol=1000)          # 1000円×1000株 = 100万円
        self.assertEqual(P.check_window(rows, 0, self.cal_ix), "thin")

    def test_zero_volume(self):
        rows = series(self.days)
        rows[50] = rows[50][:5] + (0.0,)
        self.assertEqual(P.check_window(rows, 0, self.cal_ix), "zero")

    def test_jump(self):
        rows = series(self.days)
        d, o, h, l, c, v = rows[60]
        rows[60] = (d, o, c * 1.6, l, c * 1.5, v)
        self.assertEqual(P.check_window(rows, 0, self.cal_ix), "jump")

    def test_flat_run(self):
        rows = series(self.days)
        for i in (70, 71):
            d, o, h, l, c, v = rows[i]
            rows[i] = (d, c, c, c, c, v)
        self.assertEqual(P.check_window(rows, 0, self.cal_ix), "flat")

    def test_small_move(self):
        rows = series(self.days)
        base = rows[119][4]
        for i in range(120, 140):
            d, o, h, l, c, v = rows[i]
            rows[i] = (d, base, base * 1.015, base * 0.995, base * 1.01, v)
        self.assertEqual(P.check_window(rows, 0, self.cal_ix), "flatret")

    def test_gap(self):
        rows = series(self.days)
        del rows[30:34]                               # 営業日が4日欠ける
        self.assertEqual(P.check_window(rows, 0, self.cal_ix), "gap")


class TestItem(unittest.TestCase):
    def test_index_and_hidden(self):
        days = bizdays("2021-01-04", 200)
        rows = series(days, drift=0.003)
        ret = (rows[139][4] / rows[119][4] - 1) * 100
        it = P.make_item("7203", rows, 0, ret, {"name": "テスト", "sector": "輸送用機器"})
        self.assertEqual(it["c"][0], 100.0)                       # 1本目の終値=100
        self.assertEqual(max(it["v"]), 100)                       # 出来高の最大=100
        self.assertEqual(len(it["c"]), 120)
        self.assertEqual(len(it["after"]), 20)
        self.assertAlmostEqual(it["after"][-1], round(rows[139][4] / rows[0][4] * 100, 1))
        self.assertEqual(it["dir"], "up" if ret > 0 else "down")
        self.assertEqual(it["id"], P.qid("7203", days[0]))
        self.assertEqual((it["d0"], it["dq"], it["d1"]), (days[0], days[119], days[139]))


def fake_pool(n_codes=40, per=3):
    items = []
    for k in range(n_codes):
        code = str(1000 + k)
        for j in range(per):
            d0 = (date(2022, 1, 3) + timedelta(days=200 * j + k)).isoformat()
            d1 = (date.fromisoformat(d0) + timedelta(days=196)).isoformat()
            up = (k + j) % 2 == 0
            items.append({"id": P.qid(code, d0), "code": code, "name": f"銘柄{k}", "sector": "機械",
                          "d0": d0, "dq": d1, "d1": d1, "o": [100.0] * 120, "h": [101.0] * 120,
                          "l": [99.0] * 120, "c": [100.0] * 120, "v": [50] * 120,
                          "after": [103.0 if up else 97.0] * 20, "ret": 3.0 if up else -3.0,
                          "dir": "up" if up else "down"})
    # 直近1年に入る問題（出してはいけない）
    items.append({**items[0], "id": "qrecent0000", "code": "9999", "d0": "2026-01-05", "dq": "2026-06-20",
                  "d1": "2026-07-01"})
    return items


def write_fake_pool(path, items):
    with gzip.open(path, "wt", encoding="utf-8") as f:
        f.write(json.dumps({"_meta": {"built": "2026-11-20", "n": len(items), "bars": 120, "ahead": 20}}) + "\n")
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")


class TestDaily(unittest.TestCase):
    def test_pick_rules(self):
        items = sorted(fake_pool(), key=lambda x: x["id"])
        a = D.pick(items, "2026-11-20", {})
        b = D.pick(items, "2026-11-20", {})
        self.assertEqual([x["id"] for x in a], [x["id"] for x in b])      # 同じ日は同じ
        self.assertEqual(len(a), 5)
        self.assertEqual(len({x["code"] for x in a}), 5)                   # 別の銘柄
        n_up = sum(1 for x in a if x["dir"] == "up")
        self.assertIn(n_up, (2, 3))
        self.assertNotIn("qrecent0000", [x["id"] for x in a])              # 直近1年は出さない
        c = D.pick(items, "2026-11-21", {})
        self.assertNotEqual([x["id"] for x in a], [x["id"] for x in c])

    def test_no_clues_in_questions(self):
        items = sorted(fake_pool(), key=lambda x: x["id"])
        day = D.make_day("2026-11-20", D.pick(items, "2026-11-20", {}), {"bars": 120, "ahead": 20})
        for q in day["questions"]:
            self.assertEqual(set(q), QKEYS)
        for a in day["answers"]:
            for k in ("code", "name", "sector", "period", "from", "to", "ret", "dir", "after"):
                self.assertIn(k, a)

    def test_run_idempotent_and_no_repeat(self):
        items = fake_pool()
        with tempfile.TemporaryDirectory() as t:
            pool = Path(t) / "pool.jsonl.gz"
            out = Path(t) / "quiz"
            write_fake_pool(pool, items)
            days = [(date(2026, 11, 1) + timedelta(days=i)).isoformat() for i in range(12)]
            for d in days:
                D.run(d, pool, out)
            first = (out / f"{days[-1]}.json").read_text(encoding="utf-8")
            D.run(days[-1], pool, out, force=True)                         # 作り直しても同じ
            self.assertEqual(first, (out / f"{days[-1]}.json").read_text(encoding="utf-8"))
            seen, ups, prev = set(), 0, set()
            for d in days:
                data = json.loads((out / f"{d}.json").read_text(encoding="utf-8"))
                ids = [a["id"] for a in data["answers"]]
                codes = {a["code"] for a in data["answers"]}
                self.assertFalse(seen & set(ids), f"{d} に前の日の問題")
                self.assertFalse(prev & codes, f"{d} に前の日の銘柄")
                seen |= set(ids)
                prev = codes
                ups += data["n_up"]
            self.assertTrue(2.0 * len(days) <= ups <= 3.0 * len(days))
            lt = json.loads((out / "latest.json").read_text(encoding="utf-8"))
            self.assertEqual(lt["date"], days[-1])
            self.assertEqual(lt["dates"][0], days[-1])

    def test_period_ja(self):
        self.assertEqual(D.period_ja("2023-04-03", "2023-10-20"), "2023年4月〜10月")
        self.assertEqual(D.period_ja("2023-09-01", "2024-03-15"), "2023年9月〜2024年3月")


if __name__ == "__main__":
    unittest.main()
