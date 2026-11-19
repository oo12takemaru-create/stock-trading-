# -*- coding: utf-8 -*-
"""events_today.json（yochou_events.py）のテスト: 日付の選び方・signal_* を入れない・市場全体の欄・理由の一言・禁止語・生の値を出さない

python -X utf8 tests/test_yochou_events.py
（本番の場所には何も書かない。合成の値はこのファイルの中だけ）
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import yochou_events as E          # noqa: E402

NG = 0


def eq(got, want, label):
    global NG
    if got != want:
        NG += 1
        print(f"  NG {label}\n     期待={want}\n     実際={got}")


def ev(d, code, t, params=None, time=None, origin="live", snapshot=None, **kw):
    r = {"event_id": f"{d}_{code}_{t}", "date": d, "code": code, "event_type": t, "event_params": params or {},
         "time": time, "origin": origin, "snapshot": snapshot or {}}
    r.update(kw)
    return r


rows = [
    ev("2026-11-17", "7203", "volume_surge", {"vr": 3.3}),                       # 2つ前の日 → 入らない
    ev("2026-11-18", "130A", "margin_buy_surge", {"chg": 0.584},
       snapshot={"buy": 386800, "buy_prev": 244200, "sell": 2400, "margin_ratio": 161.17}),
    ev("2026-11-18", "3042", "stop_high", {"chg": 14.64}),
    ev("2026-11-19", "130A", "volume_surge", {"vr": 4.6}),
    ev("2026-11-19", "130A", "volume_surge", {"vr": 4.6}),                       # 重複 → 1件
    ev("2026-11-19", "130A", "short_surge", {"max_chg": 0.557, "n_inst": 2},
       snapshot={"insts": [{"inst": "Nomura International plc", "ratio_pct": 1.37}]}),
    ev("2026-11-19", "2914", "earnings_revision_down", time="15:30"),
    ev("2026-11-19", "3624", "gap_down", {"gap_pct": -10.34}),
    ev("2026-11-19", "1375", "signal_treasure_quiet", {"n": 1}),                 # 既存シグナル台帳 → 入らない
    ev("2026-11-19", "MKT", "signal_ai", {"n": 1}),                              # 同上
    ev("2026-11-19", "MKT", "thermo_stage_change", {"from": "BEARISH", "to": "NEUTRAL"}),
    ev("2026-11-19", "6758", "volume_surge", {"vr": 9.9}, origin="backfill"),     # 遡及 → 入らない
    ev("2026-11-19", "6501", "volume_surge", {"vr": 9.9}, imported_from="x"),     # 取り込み → 入らない
    ev("2026-11-20", "9984", "volume_surge", {"vr": 5.0}),                       # --date より後 → 入らない
]

o = E.build(rows, "2026-11-19", now="2026-11-19T20:00:00+09:00")
eq(o["date"], "2026-11-19", "最新の記録日")
eq(o["prev"], "2026-11-18", "前の記録日")
eq(sorted(o["items"]), ["130A", "2914", "3042", "3624"], "銘柄（signal_*・MKT・遡及・取り込み・範囲外を除く）")
eq([x["type"] for x in o["items"]["130A"]], ["short_surge", "volume_surge", "margin_buy_surge"], "新しい日が先・重複なし")
eq(o["items"]["130A"][1]["note"], "出来高が20日平均の4.6倍", "出来高の倍率")
eq(o["items"]["130A"][2]["note"], "信用買残が前回比 +58%", "信用買残の前回比")
eq(o["items"]["130A"][0]["note"], "機関の空売り残高の割合が前回比 +56%（2社）", "空売り残高の前回比")
eq(o["items"]["3042"][0]["note"], "終値は前日比 +14.6%", "ストップ高")
eq(o["items"]["3624"][0]["note"], "始値が前日終値比 −10.3%", "窓の大きさ")
eq(o["items"]["2914"][0]["note"], "15:30 に開示", "開示の時刻")
eq(len(o["market"]), 1, "市場全体は別の欄")
eq(o["market"][0]["note"], "相場の温度計が「弱気」から「中立」に", "温度計の段階変化")

# 生の値（残高・機関名・倍率の数字そのもの）を出さない
s = json.dumps(o, ensure_ascii=False)
for raw in ("386800", "244200", "161.17", "Nomura", "1.37"):
    eq(raw in s, False, f"生の値 {raw} を出さない")

# 禁止語（「買残」「売残」「空売り残高」は可・「業績予想」は可）
BANNED = ("買い", "売り", "推奨", "おすすめ", "必ず", "リアルタイム", "予想", "狙い目", "有望", "チャンス",
          "面白い", "過熱", "底", "天井", "警戒", "危険")
for t in list(E.LABEL) + [x for v in o["items"].values() for x in v] + o["market"]:
    txt = (t["label"] + t["note"]) if isinstance(t, dict) else E.LABEL[t]
    txt2 = txt.replace("買残", "").replace("売残", "").replace("空売り残高", "").replace("業績予想", "")
    for w in BANNED:
        eq(w in txt2, False, f"禁止語「{w}」: {txt}")

# 何も無いとき
eq(E.build([], None), None, "記録なし → None（書かない）")

print("OK" if NG == 0 else f"NG {NG}件")
sys.exit(1 if NG else 0)
