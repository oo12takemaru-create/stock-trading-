# -*- coding: utf-8 -*-
"""出来事の台帳（v2）のテスト: 表題の分類・起点の日・分割の調整・値の飛び

python -X utf8 tests/test_ledger2.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ledger2_detect as L        # noqa: E402
import ledger2_score as S         # noqa: E402

NG = 0


def eq(got, want, label):
    global NG
    if got != want:
        NG += 1
        print(f"  NG {label}\n     期待={want}\n     実際={got}")


# 表題の分類（サイト側 gen_kaiji.classify と同じ規則）
eq(L.classify("2026年12月期通期連結業績予想の修正に関するお知らせ"), [("shusei", "")], "業績の修正（向きなし）")
eq(L.classify("業績予想の上方修正に関するお知らせ"), [("shusei", "上方")], "上方修正")
eq(L.classify("配当予想の修正（増配）に関するお知らせ"), [("haito", "増配")], "増配")
eq(L.classify("自己株式取得に係る事項の決定に関するお知らせ"), [("jisha", "")], "自己株式の取得の決議")
eq(L.classify("自己株式の取得状況に関するお知らせ"), [], "取得状況は決議ではない")
eq(L.classify("株式分割及び定款の一部変更に関するお知らせ"), [("bunkatsu", "")], "株式分割")
eq(L.classify("株式分割に伴う定款の一部変更に関するお知らせ"), [], "分割に伴う〜は分割の発表ではない")
eq(L.classify("会社分割（吸収分割）に関するお知らせ"), [], "会社分割は株式分割ではない")
eq(L.classify("株主優待制度の新設に関するお知らせ"), [("yutai", "新設")], "優待の新設")
eq(L.classify("株主優待制度の廃止に関するお知らせ"), [("yutai", "廃止")], "優待の廃止")
eq(L.classify("（訂正）業績予想の修正に関するお知らせ"), [], "訂正は外す")

# いつ知り得たか → 起点の日
eq(L.time_known("2026-11-18", "08:30"), "pre_open", "寄り前")
eq(L.time_known("2026-11-18", "11:30"), "intraday", "立会中")
eq(L.time_known("2026-11-18", "15:30"), "after_close", "15:30 は引け後")
eq(L.time_known("2026-11-21", "08:00"), "after_close", "土曜の朝は休日")
eq(L.time_known("2026-11-18", None), "after_close", "時刻なし")
eq(L.base_date("2026-11-18", "pre_open"), "2026-11-18", "寄り前 → その日の始値")
eq(L.base_date("2026-11-18", "after_close"), "2026-11-19", "引け後 → 翌営業日")
eq(L.base_date("2026-11-20", "after_close"), "2026-11-24", "金曜の引け後 → 祝日（11/23 勤労感謝の日）明けの火曜")
eq(L.base_date("2026-11-21", "pre_open"), "2026-11-24", "休日の寄り前 → 次の営業日")
eq(S.bizdays_from("2026-11-19", 5), ["2026-11-19", "2026-11-20", "2026-11-24", "2026-11-25", "2026-11-26", "2026-11-27"],
   "営業日の並び（祝日を飛ばす）")

# 分割の調整（起点より後の権利落ち日以降に比率を掛ける）
ser = {"2026-11-18": (50000, 50500, 49000, 50200), "2026-11-19": (10100, 10300, 9900, 10200)}
adj, used = S.split_adjust(ser, {"2026-11-19": 5.0, "2019-11-19": 2.0}, "2026-11-18")
eq(adj["2026-11-19"][3], 51000.0, "分割後の終値 ×5")
eq(adj["2026-11-18"][0], 50000, "起点の日はそのまま")
eq(used, [{"ex": "2026-11-19", "ratio": 5.0}], "使った分割は期間内のものだけ")
adj2, used2 = S.split_adjust(ser, {"2026-11-18": 5.0}, "2026-11-18")
eq(used2, [], "起点の日が権利落ち日なら調整しない（起点がもう分割後）")

# 値の飛び（分割の情報が無い併合など）
eq(S.jump_check({"a": (1, 1, 1, 100), "b": (1, 1, 1, 30)}, ["a", "b"]), "b", "0.4倍未満は飛び")
eq(S.jump_check({"a": (1, 1, 1, 100), "b": (1, 1, 1, 130)}, ["a", "b"]), None, "+30% は飛びではない")

# 検出関数の登録（1種類＝1関数）
names = [n for n, *_ in L.REGISTRY]
eq(len(names), len(set(names)), "検出関数の名前が重ならない")
for t in ("margin_buy_surge", "short_new_inst", "short_surge", "stop_high", "stop_low", "volume_surge",
          "buyback_decision", "stock_split", "thermo_stage_change"):
    eq(t in L.TYPES, True, f"今日から記録する7種: {t}")

print("OK" if NG == 0 else f"NG {NG}件")
sys.exit(1 if NG else 0)
