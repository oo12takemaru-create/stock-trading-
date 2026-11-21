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

QKEYS = {"no", "o", "h", "l", "c", "v", "ma25", "ma75"}


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
                          "after": [103.0 if up else 97.0] * 20, "ma25": [100.5] * 140, "ma75": [99.5] * 140,
                          "ret": 3.0 if up else -3.0,
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
            self.assertEqual((len(a["ma25"]), len(a["ma75"])), (20, 20))
        for q in day["questions"]:
            self.assertEqual((len(q["ma25"]), len(q["ma75"])), (120, 120))

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


class TestMovingAverage(unittest.TestCase):
    def setUp(self):
        self.days = bizdays("2021-01-04", 400)
        self.rows = series(self.days, drift=0.002, seed=7)

    def test_values(self):
        s = 100
        it = P.make_item("7203", self.rows, s, 3.0, {"name": "テスト", "sector": "輸送用機器"})
        base = self.rows[s][4]
        self.assertEqual((len(it["ma25"]), len(it["ma75"])), (140, 140))
        for k in (0, 1, 60, 119, 120, 139):
            i = s + k
            m25 = sum(x[4] for x in self.rows[i - 24:i + 1]) / 25
            m75 = sum(x[4] for x in self.rows[i - 74:i + 1]) / 75
            self.assertAlmostEqual(it["ma25"][k], round(m25 / base * 100, 1))
            self.assertAlmostEqual(it["ma75"][k], round(m75 / base * 100, 1))
        self.assertNotIn(None, it["ma75"])

    def test_prior(self):
        self.assertTrue(P.has_prior(self.rows, 74))       # 1本目の前に74本 → 1本目から75日線
        self.assertFalse(P.has_prior(self.rows, 73))
        it = P.make_item("7203", self.rows, 30, 3.0, {"name": "テスト", "sector": "輸送用機器"})
        self.assertIsNone(it["ma75"][0])                   # 足りない所は null
        self.assertIsNotNone(it["ma75"][44])               # 31+44=75本目から引ける
        self.assertIsNone(it["ma75"][43])
        self.assertIsNotNone(it["ma25"][0])

    def test_build_drops_without_prior_but_keeps_used(self):
        days = bizdays("2021-01-04", 700)
        data = {str(1000 + k): series(days, drift=(0.004 if k % 2 else -0.004), seed=k) for k in range(12)}
        data["1306"] = series(days, seed=99)
        uni = {c: {"name": "x", "sector": "機械"} for c in data}
        orig_recent = P.RECENT
        try:
            P.RECENT = 10
            items, info = P.build(data, uni, per_side=10, per_code=3)
            drop = [x for x in items if x.get("drop")]
            live = [x for x in items if not x.get("drop")]
            self.assertTrue(drop, "1本目の前が足りない問題が印になる")
            for x in drop:
                self.assertEqual(set(x), {"id", "code", "d1", "dir", "drop"})
            for x in live:
                self.assertNotIn(None, x["ma75"])
            self.assertEqual(info["n"], len(live))
            # もう出題した問題は外さない（線の足りない所は null）
            keep = {drop[0]["id"]}
            items2, info2 = P.build(data, uni, per_side=10, per_code=3, keep_ids=keep)
            self.assertEqual([x["id"] for x in items], [x["id"] for x in items2])   # id と並びは同じ
            k2 = [x for x in items2 if x["id"] in keep][0]
            self.assertNotIn("drop", k2)
            self.assertIsNone(k2["ma75"][0])
        finally:
            P.RECENT = orig_recent


class TestDropAndBackfill(unittest.TestCase):
    def test_drop_is_skipped_without_changing_others(self):
        items = sorted(fake_pool(), key=lambda x: x["id"])
        a = D.pick(items, "2026-11-20", {})
        # 選ばれなかった問題を印にしても、その日の5問は変わらない
        chosen = {x["id"] for x in a}
        items2 = [({"id": x["id"], "code": x["code"], "d1": x["d1"], "dir": x["dir"], "drop": 1}
                   if x["id"] not in chosen else x) for x in items]
        self.assertEqual([x["id"] for x in D.pick(items2, "2026-11-20", {})], [x["id"] for x in a])
        # 選ばれた問題を印にすると、その問題は出ない
        items3 = [({"id": x["id"], "code": x["code"], "d1": x["d1"], "dir": x["dir"], "drop": 1}
                   if x["id"] == a[0]["id"] else x) for x in items]
        b = D.pick(items3, "2026-11-20", {})
        self.assertNotIn(a[0]["id"], [x["id"] for x in b])
        self.assertEqual(len(b), 5)

    def test_backfill_adds_only_lines(self):
        items = fake_pool()
        with tempfile.TemporaryDirectory() as t:
            pool = Path(t) / "pool.jsonl.gz"
            out = Path(t) / "quiz"
            write_fake_pool(pool, items)
            D.run("2026-11-20", pool, out)
            p = out / "2026-11-20.json"
            full = p.read_text(encoding="utf-8")
            data = json.loads(full)
            for x in data["questions"] + data["answers"]:      # 線の無い古い版を作る
                x.pop("ma25"); x.pop("ma75")
            p.write_text(D.dump(data), encoding="utf-8")
            D.run("2026-11-21", pool, out)                     # 次の日の実行で線が付く
            self.assertEqual(p.read_text(encoding="utf-8"), full)
            # 在庫の値と違う問題には足さない
            data = json.loads(full)
            for x in data["questions"] + data["answers"]:
                x.pop("ma25"); x.pop("ma75")
            data["questions"][0]["c"][5] = 123.4
            by_id = {x["id"]: x for x in items}
            self.assertEqual(D.add_ma_to_day(data, by_id), 4)
            self.assertNotIn("ma25", data["questions"][0])


if __name__ == "__main__":
    unittest.main()
