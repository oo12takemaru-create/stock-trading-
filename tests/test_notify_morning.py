# -*- coding: utf-8 -*-
"""朝の1通（notify_morning.py）の文面と日付の窓のテスト。docs/ は一時フォルダの偽物を使う"""
import json
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import notify_morning as N   # noqa: E402

NG = 0


def eq(got, want, label):
    global NG
    if got != want:
        NG += 1
        print(f"NG {label}: {got!r} != {want!r}")


def ok(cond, label):
    eq(bool(cond), True, label)


tmp = Path(tempfile.mkdtemp())
N.DOCS = tmp


def put(name, obj):
    p = tmp / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


# 今日 2026-11-11（水）→ P1=11/10・P2=11/9・P3=11/6
put("jpx_listed_codes.json", {"codes": ["1111", "2222", "3333", "4444"], "names": ["甲", "乙", "丙", "丁"]})
put("prices.json", {"trade_date": "2026-11-10", "items": {
    "1111": {"p": 1000, "c": 6.2}, "2222": {"p": 500, "c": -4.9}, "3333": {"p": 300, "c": 20.0}, "4444": {"p": 10, "c": -7, "stale": True}}})
put("stophigh.json", {"recent": {"2026-11-10": [{"c": "3333", "k": "close_high", "chg": 20.0}]}})
put("kessan_flash.json", {"days": [
    {"d": "2026-11-09", "items": [{"c": "2222", "q": "2Q", "sales": 100}]},               # P2 は窓の外
    {"d": "2026-11-10", "items": [{"c": "1111", "q": "2Q", "sales": 25000, "sales_yoy": 3.2, "op": 1500, "verdict": "増収増益"},
                                  {"c": "4444", "q": "2Q", "sales": 1, "suspect": True}]}]})
put("edinet_latest.json", {"by_issuer": {
    "2222": [{"d": "2026-11-09", "type": "大量保有", "filer": "テスト株式会社", "ratio": 5.1}],   # (P3, P2] に入る
    "1111": [{"d": "2026-11-10", "type": "変更", "filer": "X", "ratio": 6.0}]}})                    # P1 は翌日に回る
put("karauri.json", {"report_date": "2026-11-10", "moves": [{"c": "2222", "kind": "new"}], "stocks": [{"c": "2222", "d1": 0.6}]})
put("kessan.json", {"days": [{"d": "2026-11-11", "items": [{"c": "4444", "n": "丁社", "q": "第２四半期"}]},
                             {"d": "2026-11-13", "items": [{"c": "2222", "q": "第２四半期"}]}]})   # 週の最初でないので「今週」は出さない
put("radar.json", {"regime": "NEUTRAL", "scanner_timestamp": "2026-11-10T18:01:00"})

F = N.Facts(date(2026, 11, 11))
F.build()
eq((F.p1.isoformat(), F.p2.isoformat(), F.p3.isoformat()), ("2026-11-10", "2026-11-09", "2026-11-06"), "営業日の窓")
L1 = F.lines("1111")
eq(len(L1), 2, "1111 は2行")
ok(L1[0].startswith("決算の発表（第2四半期・11/10）：売上高 250.0億円（前年同期比 +3.2%）"), "決算の数字1行")
ok("前日比 +6.2%" in L1[1], "±5%")
eq(F.lines("3333"), ["終値ストップ高（11/10・前日比 +20.0%）"], "ストップ高の日は値動きを重ねない")
L2 = F.lines("2222")
ok(L2[0].startswith("大量保有の報告（新規・11/9提出）：テスト 5.10%"), "大量保有は (P3,P2]")
eq(L2[1], "大口の空売り残高の新しい報告がありました（新規の報告あり・合計が増加・11/10公表）", "空売りは数値なし")
ok(all("4.9" not in x for x in L2), "−4.9% は出さない")
L4 = F.lines("4444")
eq(L4, ["今日 決算発表の予定（第２四半期）"], "疑わしい決算・約定なしの値動きは出さない")
eq(F.names["4444"], "丁社", "決算予定の社名を使う")

m = N.compose(F, ["1111", "9999"], "a" * 64)
ok(m and m["n"] == 1, "出来事のある銘柄だけ")
ok("a=nstop&t=" + "a" * 64 in m["text"] and "a=nstop&t=" in m["html"], "停止のリンク")
ok("中立（NEUTRAL）" in m["text"], "温度計の1行")
ok("hold.html" in m["text"] and "ai.html" in m["text"], "AI朝刊・AIに聞く")
eq(N.compose(F, ["9999"], "a" * 64), None, "出来事が無ければ送らない")
ok(not any(N.BANNED.search(x) for c in F.ev for x in F.lines(c)), "出来事の行に使わない言葉が入らない")
F.add("1111", 9, "x")
F.add("1111", 9, "y")
ok("（ほか2件はカルテで）" in F.lines("1111")[1], "3件目からはほか n 件")
F.add("1111", 0, "今が買い時")
ok(all("買い時" not in x for x in F.lines("1111")), "使わない言葉の行は落とす")

print("OK" if not NG else f"{NG} 件 NG")
sys.exit(1 if NG else 0)
