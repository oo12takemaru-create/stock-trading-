# -*- coding: utf-8 -*-
"""kessan_ato.py の起点・物差し・表題の読み取りの試験（python -X utf8 tests/test_kessan_ato.py）"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import kessan_ato as K  # noqa: E402

CAL = ["2026-11-02", "2026-11-04", "2026-11-05", "2026-11-06", "2026-11-09", "2026-11-10", "2026-11-11", "2026-11-12"]


def px():
    T = len(CAL)
    O = np.array([[100 + i, 50.0] for i in range(T)], dtype=float)
    C = np.array([[100.5 + i, 50.0] for i in range(T)], dtype=float)
    return K.Px(CAL, ["1111", "1306"], O, C)


def test_timing():
    assert K.timing_of("2026-11-05", "08:30") == "pre"
    assert K.timing_of("2026-11-05", "13:55") == "in"
    assert K.timing_of("2026-11-05", "15:15") == "in"       # 2024-11-05 から引けは 15:30
    assert K.timing_of("2024-08-01", "15:15") == "after"    # それより前は 15:00
    assert K.timing_of("2026-11-05", "15:30") == "after"
    assert K.timing_of("2026-11-05", None) is None


def test_anchor():
    p = px()
    # 引け後: p＝その日、a＝翌営業日
    assert p.idx("2026-11-05", "15:30", "after") == (2, 3)
    # 場中: p＝前の営業日、a＝翌営業日
    assert p.idx("2026-11-05", "13:55", "in") == (1, 3)
    # 寄り前: p＝前の営業日、a＝その日
    assert p.idx("2026-11-05", "08:00", "pre") == (1, 2)
    # 休日（11/03）の引け後: p＝前の営業日、a＝次の営業日
    assert p.idx("2026-11-03", "15:30", "after") == (0, 1)
    # 休日の寄り前でも同じ
    assert p.idx("2026-11-03", "08:00", "pre") == (0, 1)
    # 表の外
    assert p.idx("2026-11-12", "15:30", "after") is None


def test_measure():
    p = px()
    m = K.measure(p, {"code": "1111", "date": "2026-11-05", "time": "15:30"})
    # p=2（終値102.5）a=3（始値103・終値103.5）、5本目 a+4=7（終値107.5）
    assert m["anchor"] == "2026-11-06"
    assert abs(m["gap"] - round((103 / 102.5 - 1) * 100, 2)) < 1e-9
    assert abs(m["d1"] - round((103.5 / 103 - 1) * 100, 2)) < 1e-9
    assert abs(m["d5"] - round((107.5 / 103 - 1) * 100, 2)) < 1e-9
    assert abs(m["c1"] - round((103.5 / 102.5 - 1) * 100, 2)) < 1e-9
    assert m["x1"] == m["d1"]      # TOPIX は動いていない
    # 5本目がまだ無い回は d5 なし
    m2 = K.measure(p, {"code": "1111", "date": "2026-11-09", "time": "15:30"})
    assert m2["d5"] is None and m2["d1"] is not None


def test_jump_guard():
    p = px()
    p.C[3, 0] = 400.0      # 1日で4倍＝取り込み漏れの類
    m = K.measure(p, {"code": "1111", "date": "2026-11-05", "time": "15:30"})
    assert m is None or m["d1"] is None or m["gap"] is None


def test_title():
    assert K.q_from_title("2027年３月期 第２四半期（中間期）決算短信〔日本基準〕（連結）") == "2Q"
    assert K.q_from_title("2026年12月期 第３四半期決算短信〔日本基準〕(連結)") == "3Q"
    assert K.q_from_title("2026年９月期 決算短信〔日本基準〕（非連結）") == "本決算"
    assert K.q_from_title("2027年３月期 中間決算短信〔日本基準〕（連結）") == "2Q"
    assert K.q_from_title("（訂正）「2027年３月期 第２四半期（中間期）決算短信〔日本基準〕（連結）」の一部訂正について") is None
    assert K.q_from_title("自己株式の取得状況に関するお知らせ") is None
    assert K.fy_from_title("2027年３月期 第２四半期決算短信") == "2027-03"


def test_summary():
    evs = [{"date": "2025-01-01", "time": "15:30", "timing": "after", "gap": 3.0, "d1": 1.0, "x1": 0.5, "c1": 4.0},
           {"date": "2025-04-01", "time": "15:30", "timing": "after", "gap": -5.0, "d1": -2.0, "x1": -1.0, "c1": -7.0},
           {"date": "2025-07-01", "time": "13:00", "timing": "in", "gap": 1.0, "d1": 0.5, "x1": 0.2, "c1": 1.5}]
    s = K.summarize(evs)
    assert s["n"] == 3 and s["up_x1"] == 2 and s["med_abs_d1"] == 1.0 and s["med_abs_gap"] == 3.0
    assert s["usual_time"] == "15:30" and s["usual_time_n"] == 2
    assert s["max"]["date"] == "2025-04-01"


if __name__ == "__main__":
    for k, f in list(globals().items()):
        if k.startswith("test_") and callable(f):
            f()
            print("ok", k)
