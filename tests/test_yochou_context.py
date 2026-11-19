# -*- coding: utf-8 -*-
"""yochou_context.py（予兆 7章: チャートの文脈・急騰の割合）の小さな例"""
import gzip
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ledger2_panel as P  # noqa: E402
import yochou_context as YC  # noqa: E402
import yochou_context_hyp as HY  # noqa: E402


def _days(n, start=date(2024, 1, 1)):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _panel(tmp_path, closes_by_code, opens_by_code=None):
    days = _days(max(len(v) for v in closes_by_code.values()))
    p = tmp_path / "bars.csv.gz"
    with gzip.open(p, "wt", encoding="utf-8") as f:
        f.write("date,code,open,high,low,close,adj_close,volume\n")
        for code, cs in closes_by_code.items():
            os_ = (opens_by_code or {}).get(code) or cs
            for d, c, o in zip(days, cs, os_):
                f.write(f"{d},{code},{o},{max(c, o) * 1.01},{min(c, o) * 0.99},{c},{c},1000\n")
    return P.Panel([([p], "row")], {}), days


def test_band_of_edges():
    assert YC.band_of({"ma200": 1}, "ma200") == "above"
    assert YC.band_of({"ma200": 0}, "ma200") == "below"
    assert YC.band_of({}, "ma200") is None
    assert YC.band_of({"dev25": -10.0}, "dev25") == "le-10"
    assert YC.band_of({"dev25": -9.99}, "dev25") == "gt-10"
    assert YC.band_of({"ret20": 10.0}, "ret20") == "le10"
    assert YC.band_of({"ret20": 20.0}, "ret20") == "10to20"
    assert YC.band_of({"ret20": 20.01}, "ret20") == "gt20"
    assert YC.band_of({"hi52": -5.0}, "hi52") == "lt-5"
    assert YC.band_of({"hi52": -4.9}, "hi52") == "ge-5"
    assert YC.band_of({"atr": 2.99}, "atr") == "lt3"


def test_hypotheses_are_one_event_one_context():
    ids = [h[0] for h in HY.HYPOTHESES]
    assert len(ids) == len(set(ids)) and 10 <= len(ids) <= 15
    for hid, t, cn, b, d, txt in HY.HYPOTHESES:
        assert cn in HY.BANDS and b in [x[0] for x in HY.BANDS[cn]] and d in (1, -1) and txt


def test_ctx_values_and_no_lookahead(tmp_path):
    rng = np.random.default_rng(1)
    cs = list(np.round(100 * np.cumprod(1 + rng.normal(0, 0.02, 300)), 1))
    pn, days = _panel(tmp_path, {"1111": cs})
    CM = YC.ctx_matrices(pn)
    k = 260
    i = YC.ctx_row_index(pn, days[k + 1], days[k])          # 知り得た日 = days[k]・基準は翌日の始値
    assert i == k
    v = YC.ctx_values(CM, i, 0)
    c = np.array(cs[: k + 1])
    assert v["ma25"] == int(c[-1] > c[-25:].mean())
    assert v["ma75"] == int(c[-1] > c[-75:].mean())
    assert v["ma200"] == int(c[-1] > c[-200:].mean())
    assert abs(v["dev25"] - (c[-1] / c[-25:].mean() - 1) * 100) < 0.01
    assert abs(v["ret20"] - (c[-1] / c[-21] - 1) * 100) < 0.01
    # 寄り付き前に知り得た出来事（base_date = その日）は、前の営業日の終値まで
    assert YC.ctx_row_index(pn, days[k], days[k]) == k - 1
    # 先の値を変えても文脈は変わらない（その日の終値までしか使っていない）
    cs2 = cs[: k + 1] + [x * 3 for x in cs[k + 1:]]
    pn2, _ = _panel(tmp_path, {"1111": cs2})
    v2 = YC.ctx_values(YC.ctx_matrices(pn2), YC.ctx_row_index(pn2, days[k + 1], days[k]), 0)
    assert v == v2


def test_surge_flag_uses_first_open_and_n_closes(tmp_path):
    n = 80
    cs = [100.0] * n
    os_ = [100.0] * n
    # 30行目に始値100で寄り、20本目（行49）の終値が131 → surge20。行50の終値で上げても行30の窓の外
    cs[49] = 131.0
    pn, days = _panel(tmp_path, {"2222": cs}, {"2222": os_})
    SM = YC.surge_matrices(pn)
    assert SM["surge20"][30, 0] == 1
    assert SM["surge20"][29, 0] == 0                       # 行29からの20本は行48まで（131 は窓の外）
    assert np.isnan(SM["surge20"][n - 5, 0])               # 20本そろわない
    assert SM["drop20"][30, 0] == 0


def test_surge_eval_counts():
    obs = [("2024-01-04", 1.0), ("2024-01-04", 0.0), ("2024-01-05", 0.0)]
    base = {"2024-01-04": 0.1, "2024-01-05": 0.1}
    r = YC.surge_eval(obs, base, {"2024-01-04": 0, "2024-01-05": 1}, 20)
    assert r["n"] == 3 and r["hits"] == 1 and abs(r["rate"] - 33.333) < 0.01
    assert abs(r["base_rate"] - 10.0) < 1e-9 and abs(r["lift"] - 3.333) < 0.01
    assert r["status"] == "insufficient"


def test_ctx_text_has_no_banned_words():
    import yochou as Y
    for (cn, b), ja in YC.CTX_JA.items():
        assert not Y.banned_in(ja), ja
    for hid, t, cn, b, d, txt in HY.HYPOTHESES:
        assert not Y.banned_in(YC.pub_text(txt)), txt
    rec = {"median": 1.2, "pos_rate": 55.0, "neg_rate": 45.0, "median_vs_all": 0.8, "context_label": "200日線より上",
           "period": ["2021-11-19", "2026-10-20"], "n": 1234, "days": 456, "window": 20}
    t = YC.ctx_text({"code": "1234", "event_type": "volume_surge"}, rec)
    assert not Y.banned_in(t) and "200日線より上" in t and "1,234回" in t


if __name__ == "__main__":                       # このリポジトリのテストはスクリプトとして走らせる（pytest でも可）
    import inspect
    import tempfile
    ng = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                if "tmp_path" in inspect.signature(fn).parameters:
                    with tempfile.TemporaryDirectory() as d:
                        fn(Path(d))
                else:
                    fn()
                print("ok", name)
            except Exception as ex:          # noqa: BLE001
                ng += 1
                print("NG", name, repr(ex))
    print("NG", ng) if ng else print("OK")
    sys.exit(1 if ng else 0)
