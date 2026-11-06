# -*- coding: utf-8 -*-
"""世界の地図の鮮度の検査（2026-11-06。7時の確定版で日経平均など6つが 11/4 のまま出た件）

python -X utf8 -m pytest tests/test_world_stale.py
"""
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import data_targets  # noqa: E402
import world_fetch  # noqa: E402


def _it(t, d, g="as", k="idx", n=None):
    return {"t": t, "n": n or t, "g": g, "k": k, "ok": 1, "d": d, "r": {"d1": 1.0}}


def test_mark_stale_flags_asia_older_than_prev_tokyo_day():
    items = [_it("^N225", "2026-11-04", n="日経平均"), _it("1306.T", "2026-11-05"),
             _it("^HSI", "2026-11-04", n="香港 ハンセン"), _it("^GSPC", "2026-11-04", g="am")]
    out = world_fetch.mark_stale(items, date(2026, 11, 6), last_session=lambda t: date(2026, 11, 5))
    assert out == ["日経平均", "香港 ハンセン"]
    assert items[0]["stale"] == 1 and "前日の値が取れず" in items[0]["note"]
    assert "stale" not in items[1] and "stale" not in items[3]


def test_mark_stale_skips_local_holiday():
    # 上海が国慶節で休み: 取引所の直近の立会いの日が最終の取引日と同じなら付けない
    items = [_it("000001.SS", "2026-09-30")]
    assert world_fetch.mark_stale(items, date(2026, 10, 6), last_session=lambda t: date(2026, 9, 30)) == []


def test_mark_stale_unknown_session_is_stale():
    items = [_it("^KS11", "2026-11-04")]
    assert world_fetch.mark_stale(items, date(2026, 11, 6), last_session=lambda t: None) == ["^KS11"]


def test_mark_stale_tokyo_holiday():
    # 11/3（文化の日）明けの 11/4 の朝: 前の東京の営業日は 11/2 なので 11/2 の日経平均は古くない
    items = [_it("^N225", "2026-11-02")]
    assert world_fetch.mark_stale(items, date(2026, 11, 4), last_session=lambda t: None) == []


def test_summary_excludes_stale():
    items = [_it("^N225", "2026-11-05"), _it("^HSI", "2026-11-04")]
    items[1]["stale"] = 1
    _s, c = world_fetch.summary_line(items, "d1")
    assert c == {"up": 1, "n": 1}


def _write(tmp_path, items, updated="2026-11-06T07:01:00+09:00"):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "world.json").write_text(json.dumps({"updated": updated, "items": items}), encoding="utf-8")


def test_world_asia_lag_red_on_old_nikkei(tmp_path):
    _write(tmp_path, [_it("^N225", "2026-11-04", n="日経平均"), _it("^HSI", "2026-11-05")])
    lag, bad = data_targets.world_asia_lag(tmp_path)
    assert lag == 1 and bad == ["日経平均 2026-11-04"]


def test_world_asia_lag_ok(tmp_path):
    _write(tmp_path, [_it("^N225", "2026-11-05"), _it("^HSI", "2026-11-05")])
    assert data_targets.world_asia_lag(tmp_path) == (0, [])


def test_world_asia_lag_allows_foreign_holiday_but_not_stale_flag(tmp_path):
    items = [_it("^N225", "2026-11-05"), _it("000001.SS", "2026-11-02"), _it("^KS11", "2026-11-05")]
    items[2]["stale"] = 1
    _write(tmp_path, items)
    lag, bad = data_targets.world_asia_lag(tmp_path)
    assert lag == 1 and bad == ["^KS11 2026-11-05"]


def test_stale_list_includes_world(tmp_path):
    _write(tmp_path, [_it("^N225", "2026-11-04", n="日経平均")])
    out = data_targets.stale_list(tmp_path, skip=set())
    w = [x for x in out if x["label"] == data_targets.WORLD_LABEL]
    assert w and w[0]["items"] == ["日経平均 2026-11-04"]
