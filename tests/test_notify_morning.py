# -*- coding: utf-8 -*-
"""朝の1通（notify_morning.py）の文面と日付の窓のテスト。docs/ は一時フォルダの偽物を使う"""
import json
import re
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
eq(L2[0], "大口の空売り残高の新しい報告がありました（新規の報告あり・合計が増加・11/10公表）", "空売りは数値なし")
ok(L2[1].startswith("大量保有の報告（新規・11/9提出）：テスト 5.10%"), "大量保有は (P3,P2]")
ok(all("4.9" not in x for x in L2), "−4.9% は出さない")
L4 = F.lines("4444")
eq(L4, ["今日 決算発表の予定（第２四半期）"], "疑わしい決算・約定なしの値動きは出さない")
eq(F.names["4444"], "丁社", "決算予定の社名を使う")

m = N.compose(F, ["1111", "9999"], "a" * 64)
ok(m and m["n"] == 1, "出来事のある銘柄だけ")
ok("a=nstop&t=" + "a" * 64 in m["text"] and "a=nstop&t=" in m["html"], "停止のリンク")
ok("中立（NEUTRAL）" in m["text"], "温度計の1行")
ok("大量保有の報告は1営業日遅れで載ります" in m["text"], "大量保有の遅れの1行")
eq(N.DAILY_CAP, 60, "1日の上限 60 通")
ok("hold.html" in m["text"] and "ai.html" in m["text"], "AI朝刊・AIに聞く")
eq(N.compose(F, ["9999"], "a" * 64), None, "出来事が無ければ送らない")
ok(not any(N.BANNED.search(x) for c in F.ev for x in F.lines(c)), "出来事の行に使わない言葉が入らない")
# 増収増益などの札: 営業利益が無い会社（IFRS 等）は記録にある利益で判定・判定できなければ付けない
eq(N.flash_verdict({"sales_yoy": 7.4, "op_yoy": 84.3, "np_yoy": 98.9, "verdict": "増収増益"}), "増収増益", "札（営業利益あり）")
eq(N.flash_verdict({"sales_yoy": 8.8, "op_yoy": None, "np_yoy": 40.0, "verdict": None}), "増収増益", "札（営業利益なし→純利益）")
eq(N.flash_verdict({"sales_yoy": 8.8, "ord_yoy": -3.0, "np_yoy": 40.0}), "増収減益", "札（経常利益を純利益より先に）")
eq(N.flash_verdict({"sales_yoy": -1.0, "np_yoy": 5.0}), "減収増益", "札（減収）")
eq(N.flash_verdict({"sales_yoy": 8.8, "op_yoy": None, "np_yoy": None}), None, "札（利益の比較なし→付けない）")
eq(N.flash_verdict({"sales_yoy": None, "np_yoy": 5.0}), None, "札（売上の比較なし→付けない）")

# 並び: 決算の発表 → ストップ高/安・±5% → 空売り/大量保有/分割/配当 → 決算の予定（同じ段はボードの順）
m2 = N.compose(F, ["4444", "2222", "3333", "1111"], "b" * 64)
order = [m2["text"].index(f"（{c}）") for c in ("1111", "3333", "2222", "4444")]
eq(order, sorted(order), "重い出来事の銘柄から並ぶ")
ok(m2["subject"].endswith("甲・丙 ほか2銘柄"), "件名は先頭2銘柄（形はそのまま）")

# 冒頭の保有ボードのボタン・utm・文字の大きさ
H = "https://kaburadar.jp/hold.html?utm_source=notify&utm_medium=email"
ok(m["text"].splitlines()[1] == f"保有ボードで損益を見る → {H}", "テキストの2行目に保有ボード")
ok(m["html"].index("保有ボードで損益を見る →") < m["html"].index("（1111）"), "ボタンは銘柄より上")
ok(f'href="{H}"' in m["html"], "ボタンの URL")
for u in ("radar.html", "ai.html", "hold.html", "stocks/1111.html"):
    ok(f"{u}?utm_source=notify&utm_medium=email" in m["html"], f"utm: {u}")
ok("max-width:600px" in m["html"] and "padding:0 16px" in m["html"], "幅 600px・余白 16px")
ok("font-size:15px;line-height:1.7" in m["html"], "本文 15px・行間 1.7")
ok('font-size:16px;font-weight:bold' in m["html"], "銘柄名 16px 太字")
ok("utm" not in m["stop"], "停止のリンクに utm は付けない")
# 注記の打ち消し（売買の推奨ではありません）・空売り・配当予想は事実の言葉なので除いて見る
body = re.sub("売買の推奨ではありません|空売り|配当予想", "", m2["text"] + m2["html"])
eq([w for w in ("買い", "売り", "推奨", "おすすめ", "必ず", "リアルタイム", "予想", "狙い目") if w in body], [],
   "文面に使わない言葉を足さない")

F.add("1111", 9, "x")
F.add("1111", 9, "y")
ok("（ほか2件はカルテで）" in F.lines("1111")[1], "3件目からはほか n 件")
F.add("1111", 0, "今が買い時")
ok(all("買い時" not in x for x in F.lines("1111")), "使わない言葉の行は落とす")

# 地合いと AI朝刊の結論だけ（銘柄を登録していない人・2026-11-13）
put("ai_analysis.json", {"latest": {"date": "2026-11-11", "updated": "2026-11-11T06:18:00+09:00", "stance": "lean_defense",
                                    "headline": "金利上昇でやや守り"}})
F3 = N.Facts(date(2026, 11, 11))
F3.build()
mm = N.compose(F3, [], "c" * 64)
ok(mm and mm["n"] == 0, "銘柄なしでも地合いの1通を作る")
ok("中立（NEUTRAL）" in mm["text"] and "見立て「やや守り」：金利上昇でやや守り" in mm["text"], "温度計と AI朝刊の結論")
ok("radar.html?utm_source=notify" in mm["html"] and "ai.html?utm_source=notify" in mm["html"], "温度計・AI朝刊へのリンク")
ok("a=nstop&t=" + "c" * 64 in mm["text"], "停止のリンク（地合いだけ）")
ok(mm["subject"] == "株レーダー 朝の1通（11/11）地合い 中立・AI朝刊 やや守り", "件名（地合いだけ）")
bodym = re.sub("売買の推奨ではありません", "", mm["text"] + mm["html"])
eq([w for w in ("買い", "売り", "推奨", "おすすめ", "必ず", "リアルタイム", "予想", "狙い目") if w in bodym], [], "地合いの1通に使わない言葉を足さない")
put("ai_analysis.json", {"latest": {"date": "2026-11-10", "stance": "attack", "headline": "x"}})
F4 = N.Facts(date(2026, 11, 11))
F4.build()
m4 = N.compose(F4, [], "d" * 64)
ok(m4 and "AI朝刊" not in m4["subject"] and "見立て「" not in m4["text"], "昨日の AI朝刊は出さない（温度計だけ）")
put("ai_analysis.json", {"latest": {"date": "2026-11-11", "stance": "attack", "headline": "今が買い時"}})
F5 = N.Facts(date(2026, 11, 11))
F5.build()
ok("買い時" not in N.compose(F5, [], "e" * 64)["text"] and "見立て「攻め」" in N.compose(F5, [], "e" * 64)["text"], "使わない言葉の1行は落とし見立てだけ")
F5.regime = None
F5.ai = None
eq(N.compose(F5, [], "e" * 64), None, "温度計も AI朝刊も無い日は送らない")

print("OK" if not NG else f"{NG} 件 NG")
sys.exit(1 if NG else 0)
