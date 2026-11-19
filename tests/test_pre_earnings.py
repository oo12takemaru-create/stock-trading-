# -*- coding: utf-8 -*-
"""決算の前（pre_earnings_up_10 / _5）のテスト: 営業日の数え方・予定日の記録・見通しの修正の判定・出来事・答え合わせ・公開 JSON

python -X utf8 tests/test_pre_earnings.py
（標準ライブラリだけ。pytest は使わない）
"""
import gzip
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import ledger2_detect as L        # noqa: E402
import ledger2_score as S         # noqa: E402
import pre_earnings as P          # noqa: E402
import yochou_events as YE        # noqa: E402

NG = 0


def eq(got, want, label):
    global NG
    if got != want:
        NG += 1
        print(f"  NG {label}\n     期待={want}\n     実際={got}")


def wj(p, obj):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


# ── 営業日（2026-11-23 は勤労感謝の日・月曜）
eq(L.bizdays_after("2026-11-19", "2026-11-25"), 3, "11/19 → 11/25 はあと3営業日（11/20・11/24・11/25）")
eq(L.bizdays_after("2026-11-19", "2026-11-19"), 0, "同じ日は0")
eq(L.prev_bizday("2026-11-24"), "2026-11-20", "11/24 の前営業日は 11/20（土日と祝日を飛ばす）")
eq(L.bizdays_after("2026-10-28", "2026-11-13"), 11, "10/28 → 11/13 は11営業日（11/3 は祝日）")


# ── 合成データ
def build_world(tmp):
    docs = Path(tmp) / "docs"
    today = "2026-12-01"
    # 予定日: A=12/16（11営業日後）・B=12/09（6営業日後）・C=12/16（前回の決算が記録より前）・D=12/16（修正なし）
    #        E=12/16（子会社の修正だけ）・F=12/16（中間期の修正だけ）・G=12/16（向きが読めない）・H=12/16（前回の日が不明）
    #        I=12/03（前回の決算が近すぎる）
    kj = {"today": today, "updated": "2026-12-01T18:05:00+09:00", "asof": "2026-11-27",
          "days": [{"d": "2026-11-30", "past": True, "items": [{"c": "9999", "n": "過去", "q": "-"}]},
                   {"d": "2026-12-03", "items": [{"c": "1009", "n": "Ｉ社", "q": "第１四半期"}]},
                   {"d": "2026-12-09", "items": [{"c": "1002", "n": "Ｂ社", "q": "第３四半期"}]},
                   {"d": "2026-12-16", "items": [{"c": c, "n": f"{c}社", "q": "第２四半期"}
                                                  for c in ("1001", "1003", "1004", "1005", "1006", "1007", "1008")]},
                   {"d": "2027-03-01", "items": [{"c": "1010", "n": "遠い", "q": "本決算"}]}]}
    wj(docs / "kessan.json", kj)
    wj(docs / "kessan_flash_hist" / "2026-10.json",
       {"days": [{"d": "2026-10-10", "items": [{"c": c, "t": "15:00"} for c in ("1001", "1002", "1004", "1005", "1006", "1007")]}]})
    wj(docs / "kessan_flash_hist" / "2026-09.json", {"days": [{"d": "2026-09-12", "items": [{"c": "1003", "t": "15:00"}]}]})
    wj(docs / "kessan_flash_hist" / "2026-11.json", {"days": [{"d": "2026-11-10", "items": [{"c": "1009", "t": "15:00"}]}]})

    def rv(c, d, prev, cur, per="通期", ti="業績予想の修正に関するお知らせ", k="rvfc"):
        return {"d": d, "c": c + "0", "t": "15:30", "ti": ti, "id": f"id{c}{d}", "per": per, "k": k,
                "prev": {"op": prev} if prev is not None else {}, "cur": {"op": cur} if cur is not None else {}}
    items = [rv("1001", "2026-11-05", 1000, 1120),                      # +12% → 当たり
             rv("1001", "2026-10-09", 1000, 1300),                      # 前回の決算より前 → 数えない
             rv("1002", "2026-11-20", 500, 530),                        # +6% → 当たり（5営業日前）
             rv("1003", "2026-10-20", 100, 200),                        # 前回の決算が記録より前の会社
             rv("1004", "2026-11-05", 1000, 1050),                      # ちょうど +5% は超えていない
             rv("1005", "2026-11-05", 1000, 1500, ti="連結子会社（株式会社ＸＸ）の業績予想の修正に関するお知らせ"),
             rv("1006", "2026-11-05", 1000, 1500, per="中間期"),
             rv("1007", "2026-11-05", None, None),                       # 営業利益が読めない
             rv("1001", "2026-12-02", 1000, 2000)]                       # その日より後 → 数えない
    wj(docs / "fc_revision" / "2026-11.json", {"items": {x["id"]: x for x in items}})
    return docs, today


with tempfile.TemporaryDirectory() as tmp:
    docs, today = build_world(tmp)
    data = L.Sources(docs=docs, root=tmp)
    data.yotei_dir = Path(tmp) / "data" / "ledger2" / "kessan_yotei_asof"

    # 予定日の記録（その日の版だけ・2回目は書かない）
    asof_dir = data.yotei_dir
    P.snapshot(data, asof_dir)
    P.snapshot(data, asof_dir)
    lines = L.jsonl(asof_dir / "2026-12.jsonl")
    eq(len(lines), 1, "予定日の記録は1日1行（2回走っても増えない）")
    eq(lines[0]["items"].get("1001"), "2026-12-16", "予定日を記録")
    eq("9999" in lines[0]["items"], False, "過ぎた日は入れない")
    eq("1010" in lines[0]["items"], False, "45暦日より先は入れない")
    eq(L.yotei_from_kessan({"today": "2026-11-30", "days": []}, "2026-12-01"), None, "別の日の版からは作らない")

    # 記録のあとに kessan.json の予定日が変わっても、その日の記録は変わらない
    data2 = L.Sources(docs=docs, root=tmp)
    data2.yotei_dir = asof_dir
    kj = json.loads((docs / "kessan.json").read_text(encoding="utf-8"))
    kj["days"][3]["d"] = "2026-12-18"
    wj(docs / "kessan.json", kj)
    eq(data2.yotei_asof(today)["items"]["1001"], "2026-12-16", "後で予定日が変わっても記録は書き換えない")

    rows, _ = L.pre_earnings_state(today, data2, max_left=11)
    st = {r["code"]: r["status"] for r in rows}
    eq(st, {"1001": "hit", "1002": "hit", "1003": "prev_before_records", "1004": "no_raise", "1005": "no_raise",
            "1006": "no_raise", "1007": "unknown_dir", "1008": "prev_unknown", "1009": "recent_kessan"}, "会社ごとの判定")
    r1 = next(r for r in rows if r["code"] == "1001")
    eq([(x["d"], x["pct"]) for x in r1["up"]], [("2026-11-05", 12.0)], "前回の決算のあと〜その日だけの修正・上げ幅")
    eq(r1["left"], 11, "あと11営業日")

    evs = L.detect_pre_earnings(today, data2)
    got = sorted((e["event_type"], e["code"]) for e in evs)
    eq(got, [("pre_earnings_up_10", "1001"), ("pre_earnings_up_5", "1002")], "出来事: 11営業日前→_10・6営業日前→_5")
    e10 = next(e for e in evs if e["code"] == "1001")
    eq(e10["known"], "after_close", "知り得た時点は引け後")
    eq(L.base_date(today, e10["known"]), "2026-12-02", "基準価格は翌営業日の始値（＝予定日の10営業日前）")
    eq(e10["event_params"]["yotei"], "2026-12-16", "予定日を出来事に残す")
    eq(e10["snapshot"]["yotei_asof"], today, "その日の記録から")
    eq(L.detect_pre_earnings("2026-11-18", data2), [], "記録を始めた日より前は記録しない")

    # 公開 JSON（10営業日以内だけ・過去の統計の数字は無い）
    out = P.build(today, data2, Path(tmp) / "data" / "ledger2", now="x")
    eq([x["code"] for x in out["items"]], ["1002"], "一覧は10営業日以内の当たり（1001 はあと11営業日なので明日から）")
    eq(out["items"][0]["note"], "見通しを上げていて、決算発表まであと6営業日", "マイボード用の一言")
    eq(out["record"]["windows"]["10"]["scored"], 0, "答えはまだ0件")
    eq(out["record"]["windows"]["10"]["avg_vs_all"], None, "平均はまだ無い")
    txt = json.dumps(out, ensure_ascii=False)
    for w in ("買い", "売り", "推奨", "おすすめ", "必ず", "狙い目", "有望", "チャンス", "予想", "1.92", "1.17"):
        eq(w in txt, False, f"公開 JSON に「{w}」が無い")

    # 理由の一言（events_today）
    note = YE.note_of({"event_type": "pre_earnings_up_10", "event_params": e10["event_params"]})
    eq(note, "見通しを上げていて、決算発表まであと11営業日（予定日 12/16）・営業利益の見通しを +12.0%", "events_today の理由")

    # ── 答え合わせ（data/bars の合成）
    out_dir = Path(tmp) / "data" / "ledger2"
    (out_dir / "events").mkdir(parents=True, exist_ok=True)
    rows_ev = []
    for e in evs:
        rows_ev.append({"event_id": f"20261201_{e['code']}_{e['event_type']}", "date": today, "code": e["code"],
                        "event_type": e["event_type"], "event_params": e["event_params"], "snapshot": e["snapshot"],
                        "base_date": L.base_date(today, e["known"]), "origin": "live"})
    with open(out_dir / "events" / "2026-12.jsonl", "w", encoding="utf-8") as f:
        for r in rows_ev:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    bars = Path(tmp) / "bars"
    days = ["2026-12-02"]
    while days[-1] < "2026-12-18":
        days.append(L.next_bizday(days[-1]))
    for i, d in enumerate(days):
        p = bars / d[:4] / f"{d}.csv.gz"
        p.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(p, "wt", encoding="utf-8") as f:
            f.write("code,open,high,low,close,adj_close,volume\n")
            f.write(f"1001,{100 + i},{100 + i},{100 + i},{100 + i},,1\n")      # 毎日 +1
            f.write(f"1002,{200},{200},{200},{200},,1\n")                      # 動かない
            f.write(f"1306,{1000},{1000},{1000},{1000 + i},,1\n")              # TOPIX は終値だけ +1
            f.write(f"5555,{50},{50},{50},{50 + i * 0.5},,1\n")                # 全銘柄の平均に入る
    # 実際の発表: 1001 は予定どおり 12/16、1002 は予定 12/09 → 12/10 にずれた
    wj(docs / "kessan_flash_hist" / "2026-12.json",
       {"days": [{"d": "2026-12-16", "items": [{"c": "1001", "t": "15:00"}]},
                 {"d": "2026-12-10", "items": [{"c": "1002", "t": "11:30"}]}]})
    data3 = L.Sources(docs=docs, root=tmp, bars=bars)
    data3._c["stock_codes"] = {"1001", "1002", "5555"}
    S.score_pre_earnings(out_dir, data3, "2026-12-16", "23:59", use_yf=False)
    S.score_pre_earnings(out_dir, data3, "2026-12-16", "23:59", use_yf=False)     # 2回目は増えない
    sc = {r["code"]: r for r in L.jsonl(out_dir / "scores_pre" / "2026-12.jsonl")}
    eq(len(L.jsonl(out_dir / "scores_pre" / "2026-12.jsonl")), 2, "答えは1件1行（2回走っても増えない）")
    a = sc["1001"]
    eq((a["base_date"], a["end_date"], a["bars_n"]), ("2026-12-02", "2026-12-15", 10), "窓: 予定日の10営業日前の始値〜前営業日の終値（10本）")
    eq((a["on_schedule"], a["shift_bizdays"]), (True, 0), "予定どおり")
    eq(a["raw_ret"], 9.0, "生の騰落（100→109）")
    eq(a["topix_ret"], 0.9, "TOPIX（1000→1009）")
    eq(a["ret"], 8.1, "対TOPIX")
    # 全銘柄: 1001 +9%・1002 0%・5555 (50→54.5) +9% → 平均 6%
    eq((a["all_ret"], a["vs_all"], a["n_all"]), (6.0, 3.0, 3), "全銘柄との差")
    b = sc["1002"]
    eq((b["on_schedule"], b["shift_bizdays"], b["actual"], b["end_date"]), (False, 1, "2026-12-10", "2026-12-09"),
       "ずれた事実（予定 12/09 → 実際 12/10・場中の発表でも前営業日まで）")

    out2 = P.build(today, data2, out_dir, now="x")
    w10, w5 = out2["record"]["windows"]["10"], out2["record"]["windows"]["5"]
    eq((w10["recorded"], w10["scored"], w10["avg_vs_all"], w10["on_schedule"]["scored"]), (1, 1, 3.0, 1), "答えの件数と平均（10）")
    eq((w5["scored"], w5["shifted"]["scored"], w5["on_schedule"]["scored"]), (1, 1, 0), "ずれたものは別に数える（5）")

    # 前倒し（基準価格の日より前に発表）→ 窓なし
    with open(out_dir / "events" / "2026-12.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"event_id": "20261201_1004_pre_earnings_up_10", "date": today, "code": "1004",
                            "event_type": "pre_earnings_up_10", "event_params": {"n": 10, "yotei": "2026-12-16"},
                            "base_date": "2026-12-02", "origin": "live"}) + "\n")
    wj(docs / "kessan_flash_hist" / "2026-12.json",
       {"days": [{"d": "2026-12-16", "items": [{"c": "1001", "t": "15:00"}]},
                 {"d": "2026-12-10", "items": [{"c": "1002", "t": "11:30"}]},
                 {"d": "2026-12-02", "items": [{"c": "1004", "t": "15:00"}]}]})
    data4 = L.Sources(docs=docs, root=tmp, bars=bars)
    S.score_pre_earnings(out_dir, data4, "2026-12-16", "23:59", use_yf=False)
    sc = {r["code"]: r for r in L.jsonl(out_dir / "scores_pre" / "2026-12.jsonl")}
    eq((sc["1004"]["status"], sc["1004"]["on_schedule"]), ("no_window", False), "前倒しで窓が無い")

print("OK" if NG == 0 else f"NG {NG}件")
sys.exit(1 if NG else 0)
