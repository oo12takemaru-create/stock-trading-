# -*- coding: utf-8 -*-
"""今日の発見（hakken.py）・アーサーのひとこと（karte_ai_ingest.py の o）・朝の1通の行のテスト。docs/ は一時フォルダの偽物"""
import json
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import hakken as HK            # noqa: E402
import karte_ai_ingest as KI   # noqa: E402
import notify_morning as N     # noqa: E402

NG = 0


def eq(got, want, label):
    global NG
    if got != want:
        NG += 1
        print(f"NG {label}: {got!r} != {want!r}")


def ok(cond, label):
    eq(bool(cond), True, label)


tmp = Path(tempfile.mkdtemp())


def put(name, obj):
    p = tmp / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


T = "2026-11-11"
put("jpx_listed_codes.json", {"codes": [], "names": {"1111": "甲", "2222": "乙", "3333": "丙", "4444": "丁", "5555": "戊ＥＴＦ",
                                                     "6666": "己", "7777": "庚"}})
put("prices.json", {"trade_date": T, "items": {
    "1111": {"p": 1000, "c": 21.0, "vr": 12.0, "v": 500},      # 終値ストップ高 3 + 出来高 3 = 6（売買代金 5億）
    "2222": {"p": 500, "c": -15.0, "vr": 6.0, "v": 1000},      # 終値ストップ安 3 + 出来高 2 = 5（下げ）
    "3333": {"p": 300, "c": 4.0, "vr": 3.5, "v": 1000},        # 決算 +120% 2 + 出来高 1 = 3（同じ業種）
    "4444": {"p": 100, "c": 30.0, "vr": 20.0, "v": 100},       # 売買代金 0.1億 → 対象外
    "5555": {"p": 2000, "c": 9.0, "vr": 15.0, "v": 1000},      # ETF → 対象外
    "6666": {"p": 800, "c": 1.0, "vr": 3.2, "v": 1000},        # 出来高 1 + 大量保有 1.5 = 2.5
    "7777": {"p": 900, "c": 2.0, "vr": 1.0, "v": 1000}}})      # 1点未満 → 対象外
put("stophigh.json", {"recent": {T: [{"c": "1111", "k": "close_high", "chg": 21.0, "s": "情報・通信業"},
                                     {"c": "2222", "k": "close_low", "chg": -15.0, "s": "小売業"}]},
                      "stats": {"close_low": {"n5c": {"n": 382, "median": -8.85, "pos": 23.0}, "from": "2025-10-24", "to": "2026-11-10"}}})
put("kessan_flash.json", {"days": [{"d": T, "items": [{"c": "3333", "op_yoy": 120.0, "fc_rev": "据置"}]}]})
put("kessan.json", {"days": [{"d": "2026-11-20", "items": [{"c": "3333", "s": "情報・通信業"}, {"c": "6666", "s": "情報・通信業"}]}]})
put("edinet_latest.json", {"by_issuer": {"6666": [{"d": "2026-11-10", "type": "大量保有", "filer": "テスト株式会社", "ratio": 5.5}]}})
put("karauri.json", {"report_date": T, "moves": [], "stocks": []})
put("movers.json", {"trade_date": T, "up": [], "down": []})

# 営業日・信用残の公表日
eq(HK.next_margin_pub(date(2026, 11, 11)).isoformat(), "2026-11-17", "水曜の次の信用残の公表は翌週火曜")
eq(HK.next_margin_pub(date(2026, 11, 9)).isoformat(), "2026-11-10", "月曜は同じ週の火曜")
eq(HK.bizdays_between(date(2026, 11, 11), date(2026, 11, 18)), 5, "11/11→11/18 は5営業日")

r = HK.pick(tmp, date(2026, 11, 11))
codes = [x["c"] for x in r["picks"]]
eq(sorted(codes), ["1111", "2222", "3333"], "選ばれる3銘柄（売買代金・ETF・点の下限で外す）")
ok("2222" in codes and next(x for x in r["picks"] if x["c"] == "2222")["dir"] == "down", "1つは下げ")
eq(codes[0], "1111", "点の大きい順")
p1 = next(x for x in r["picks"] if x["c"] == "1111")
eq(p1["score"], 6.0, "1111 の点")
eq([x["k"] for x in p1["parts"]], ["stop", "vol"], "1111 の内訳")
mat = HK.material(r["D"], next(x for x in r["picks"] if x["c"] == "2222"), "### 2222 乙\n- 信用倍率 3.00倍（2026-11-10申込）\n- 値動き")
ok("過去の同じ形: 終値ストップ安の5営業日後" in mat and "382件" in mat, "過去の同じ形の行")

# 5営業日は同じ銘柄を選ばない
put(f"hakken/2026-11-10.json", {"date": "2026-11-10", "picks": [{"c": "1111"}]})
r2 = HK.pick(tmp, date(2026, 11, 11))
ok("1111" not in [x["c"] for x in r2["picks"]] and "1111" in r2["skipped_recent"], "直近5営業日に選んだ銘柄は外す")
eq(sorted(x["c"] for x in r2["picks"]), ["2222", "3333", "6666"], "空いた枠は次の点の銘柄")
(tmp / "hakken" / "2026-11-10.json").unlink()

# 取り込み（save_day）→ 5営業日後の「その後」
H = {"date": T, "v": "v1", "price_date": T, "n_cand": 4, "skipped_recent": [], "picks": r["picks"], "material": {}}
HK.save_day(tmp, H, {"1111": {"h": "張り付き", "t": "文"}}, "claude-sonnet-5-5", "2026-11-11T20:00:00+09:00", [])
d = json.loads((tmp / "hakken" / f"{T}.json").read_text(encoding="utf-8"))
eq(d["picks"][0]["t"], "文", "文が通った銘柄")
eq(next(x for x in d["picks"] if x["c"] == "2222")["t"], None, "文が無い銘柄も銘柄は出す")
fu = HK.followups(tmp, "x")
eq(fu["filled"], 0, "当日は追記しない")
put("prices.json", {"trade_date": "2026-11-18", "items": {"1111": {"p": 1100}, "2222": {"p": 400}, "3333": {"p": 300}}})
fu = HK.followups(tmp, "x")
eq(fu["filled"], 3, "5営業日後に追記")
d = json.loads((tmp / "hakken" / f"{T}.json").read_text(encoding="utf-8"))
eq(next(x for x in d["picks"] if x["c"] == "1111")["after"]["chg"], 10.0, "その後の騰落 +10%")
eq(next(x for x in d["picks"] if x["c"] == "2222")["after"]["chg"], -20.0, "外れ（下げ）も残す")
rec = json.loads((tmp / "hakken" / "record.json").read_text(encoding="utf-8"))
eq((rec["all"]["n"], rec["all"]["up"], rec["all"]["down"]), (3, 1, 1), "通算")
lat = json.loads((tmp / "hakken" / "latest.json").read_text(encoding="utf-8"))
eq(lat["date"], T, "latest の日付")
fu = HK.followups(tmp, "x")
eq(fu["filled"], 0, "二重に追記しない")

# ひとことの検査
facts = "### 1111 甲\n- 決算短信 2026-11-11発表（2Q）・営業利益 前年同期比+63.9%\n- 信用倍率 3.39倍（2026-11-10申込）\n- 大口の空売り残高 2機関・合計1.70%"
common = "次の営業日 2026-11-12（大口の空売り残高の次の公表）・信用残（週次）の次の公表 2026-11-17（目安）"
eq(KI.check_o("営業利益は前年同期比+63.9%でした。次に見るのは11/17の信用残です。", facts, common), None, "正しいひとこと")
ok(KI.check_o("信用倍率は3.39倍で、営業利益は前年同期比+63.9%でした。次は11/17です。", facts, common).startswith("JPX"), "信用倍率の数値は不可")
ok(KI.check_o("大口の空売り残高は合計1.70%です。次は11/12の公表です。", facts, common).startswith("JPX"), "空売りの%は不可")
eq(KI.check_o("空売りの報告が増えています。次は11/12の大口の空売り残高の公表です。", facts, common), None, "空売りを言葉で言うのは可")
ok(KI.check_o("営業利益は前年同期比+70.0%でした。次に見るのは11/17の信用残です。", facts, common).startswith("入力に無い数字"), "無い数字")
ok(KI.check_o("営業利益は前年同期比+63.9%で、今後も上がりそうです。", facts, common).startswith("禁止語"), "将来の値動き")
ok(KI.check_o("リアルタイムで見ると営業利益は前年同期比+63.9%でした。", facts, common).startswith("禁止語"), "リアルタイム")
ok(KI.check_o("短い。", facts, common).startswith("20字未満"), "短すぎ")
ok(KI.check_hakken("決算の日に張り付き", "営業利益は前年同期比+63.9%でした。決算の日に出来高が増えました。次に見るのは11/17の信用残です。", facts, common) is None, "発見の正しい形")
ok(KI.check_hakken("見出しが十五字を超えてとても長い見出し", "一文です。二文です。", facts, common).startswith("見出し"), "見出し15字")

# 朝の1通: 各銘柄の最後の行にひとこと・末尾に今日の発見
N.DOCS = tmp
put("prices.json", {"trade_date": "2026-11-11", "items": {"1111": {"p": 1000, "c": 6.2}}})
put("karte_ai/one.json", {"items": {"1111": {"o": "営業利益は前年同期比+63.9%でした。次に見るのは11/17の信用残です。", "d": "2026-11-11"},
                                    "2222": {"o": "古い文です。", "d": "2026-11-01"}}})
put("hakken/latest.json", {"date": "2026-11-11", "picks": [{"c": "1111"}]})
F = N.Facts(date(2026, 11, 12))
F.build()
ok("1111" in F.one and "2222" not in F.one, "7日より古いひとことは出さない")
eq(F.hakken, "2026-11-11", "前営業日の今日の発見")
m = N.compose(F, ["1111"], "a" * 64)
ok("・アーサー（11/11）：営業利益は前年同期比+63.9%" in m["text"], "ひとことの行（テキスト）")
ok("アーサー（11/11）：" in m["html"], "ひとことの行（HTML）")
ok("/hakken/2026-11-11.html" in m["text"], "今日の発見のリンク")

print("OK" if NG == 0 else f"NG {NG}件")
sys.exit(1 if NG else 0)
