# -*- coding: utf-8 -*-
"""信用取引の規制の保存（kisei_fetch.py）と出来事（ledger2_detect.kisei_*）のテスト

  python -m unittest tests.test_kisei -v
"""
import json
import shutil
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import kisei_fetch as K          # noqa: E402
import ledger2_detect as L       # noqa: E402


def page(updated, sections):
    """sections = [(見出し, 列, 行の並び)] → JPX と同じ形の HTML（h3＋table）"""
    body = [f"<p>{updated} 更新</p>"]
    for head, cols, rows in sections:
        body.append(f'<h3 class="heading">{head}</h3><table><tr class="first">'
                    + "".join(f'<td class="a-center">{c}</td>' for c in cols) + "</tr>")
        for r in rows or [["-"] * len(cols)]:
            body.append("<tr>" + "".join(f"<td>{x}</td>" for x in r) + "</tr>")
        body.append("</table>")
    return "<html><body>" + "\n".join(body) + "</body></html>"


ZOU = "信用取引による新規の売付け及び買付けに係る委託保証金率を50％以上（うち現金20％以上）とする。"
ZOU70 = "信用取引による新規の売付け及び買付けに係る委託保証金率を70％以上（うち現金40％以上）とする。"


def pages(nichi, kisei, toku=None, nichi_rel=None, kisei_rel=None):
    return {
        "nichinichi": page("2026/11/20", [("日々公表銘柄", ["銘柄名", "コード", "指定日"], nichi),
                                          ("日々公表銘柄を解除された銘柄", ["銘柄名", "コード", "解除日"], nichi_rel)]),
        "tokubetsu": page("2020/12/21", [("特別周知銘柄", ["銘柄名", "コード", "周知開始日"], toku)]),
        "kisei": page("2026/11/18", [("信用取引に関する規制を行っている銘柄", ["銘柄名", "コード", "実施日", "規制の内容", "該当基準"], kisei),
                                     ("信用取引に関する規制を解除された銘柄", ["銘柄名", "コード", "解除日", "規制の内容"], kisei_rel)]),
    }


class Src(L.Sources):
    def __init__(self, root):
        super().__init__(root=root)


class TestParse(unittest.TestCase):
    def test_parse_and_empty(self):
        p = pages([["（株）エー", "4288", "2026/11/20"]], None, nichi_rel=[["ビー（株）", "3656", "2026/11/20"]])
        rec = K.build_record("2026-11-20", p, "2026-11-20T19:30:00+09:00")
        self.assertEqual(rec["nichinichi"], [{"銘柄名": "（株）エー", "コード": "4288", "指定日": "2026-11-20"}])
        self.assertEqual(rec["nichinichi_released"][0]["解除日"], "2026-11-20")
        self.assertEqual(rec["kisei"], [])           # 「-」だけの行は該当なし
        self.assertEqual(rec["tokubetsu"], [])
        self.assertEqual(rec["pages"]["kisei"]["updated"], "2026-11-18")

    def test_layout_change_fails(self):
        p = pages([["（株）エー", "4288", "2026/11/20"]], None)
        p["nichinichi"] = p["nichinichi"].replace("指定日", "指定年月日")
        with self.assertRaises(ValueError):
            K.build_record("2026-11-20", p, "x")

    def test_record_date(self):
        J = K.JST
        self.assertEqual(K.record_date(datetime(2026, 11, 20, 19, 30, tzinfo=J)), "2026-11-20")   # 金曜の夕方
        self.assertEqual(K.record_date(datetime(2026, 11, 20, 15, 0, tzinfo=J)), "2026-11-19")    # 18:00 より前は前の営業日
        self.assertEqual(K.record_date(datetime(2026, 11, 21, 10, 0, tzinfo=J)), "2026-11-20")    # 土曜は金曜の版
        self.assertEqual(K.record_date(datetime(2026, 11, 24, 8, 0, tzinfo=J)), "2026-11-20")     # 11/23 勤労感謝の日（月）の翌朝


class TestStoreAndEvents(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.out = self.tmp / "data" / "kisei"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def save(self, d, p):
        if not K.have_date(self.out, d):
            K.append(self.out, K.build_record(d, p, d + "T19:30:00+09:00"))

    def lines(self):
        return [json.loads(x) for f in sorted(self.out.glob("*.jsonl")) for x in f.read_text(encoding="utf-8").splitlines()]

    def test_once_per_day(self):
        p = pages([["エー", "4288", "2026/11/20"]], None)
        self.save("2026-11-20", p)
        self.save("2026-11-20", p)
        self.assertEqual(len(self.lines()), 1)

    def test_main_does_not_write_twice(self):
        hd = self.tmp / "html"
        hd.mkdir()
        for k, v in pages([["エー", "4288", "2026/11/20"]], None).items():
            (hd / f"{k}.html").write_text(v, encoding="utf-8")
        argv = sys.argv
        try:
            for _ in range(2):
                sys.argv = ["kisei_fetch.py", "--out", str(self.out), "--html-dir", str(hd), "--now", "2026-11-20T19:30"]
                self.assertEqual(K.main(), 0)
        finally:
            sys.argv = argv
        self.assertEqual([r["date"] for r in self.lines()], ["2026-11-20"])

    def test_events(self):
        # 11/19: A=日々公表・B=規制50%・C=日々公表・E=規制50%
        self.save("2026-11-19", pages([["A", "1111", "2026/11/10"], ["B", "2222", "2026/11/01"], ["C", "3333", "2026/11/02"],
                                       ["E", "5555", "2026/11/03"]],
                                      [["B", "2222", "2026/11/12", ZOU, "x"], ["E", "5555", "2026/11/12", ZOU, "x"]]))
        # 11/20: A→規制50%（強化）・B→70%（強化）・C→外れた（解除）・D 新規・E→規制だけ外れて日々公表（緩和）
        self.save("2026-11-20", pages([["A", "1111", "2026/11/10"], ["B", "2222", "2026/11/01"], ["D", "4444", "2026/11/20"],
                                       ["E", "5555", "2026/11/03"]],
                                      [["A", "1111", "2026/11/20", ZOU, "x"], ["B", "2222", "2026/11/20", ZOU70, "x"]]))
        data = Src(self.tmp)
        got = {}
        for fn in (L.detect_kisei_new, L.detect_kisei_tighten, L.detect_kisei_ease, L.detect_kisei_release):
            for e in fn("2026-11-20", data):
                got.setdefault(e["event_type"], []).append(e["code"])
                self.assertEqual(e["known"], "after_close")
        self.assertEqual(got, {"kisei_new": ["4444"], "kisei_tighten": ["1111", "2222"],
                               "kisei_ease": ["5555"], "kisei_release": ["3333"]})
        # 初日（前の営業日の行が無い）は出来事を作らない
        self.assertEqual(L.detect_kisei_new("2026-11-19", data), [])

    def test_gap_no_events(self):
        # 前の営業日（11/19）が無く、11/18 と 11/20 だけ → 11/20 は作らない
        self.save("2026-11-18", pages([["A", "1111", "2026/11/10"]], None))
        self.save("2026-11-20", pages([["D", "4444", "2026/11/20"]], None))
        data = Src(self.tmp)
        self.assertEqual(L.kisei_changes("2026-11-20", data), [])

    def test_registered(self):
        for t in ("kisei_new", "kisei_tighten", "kisei_ease", "kisei_release"):
            self.assertIn(t, L.TYPES)
        import yochou as Y
        for t in ("kisei_new", "kisei_tighten", "kisei_ease", "kisei_release"):
            self.assertIn(t, Y.HYPOTHESES)
            self.assertEqual(Y.banned_in(Y.PHRASE[t] + L.TYPES[t]["label"] + Y.HYPOTHESES[t][1]), [])


if __name__ == "__main__":
    unittest.main()
