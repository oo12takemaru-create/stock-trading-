# -*- coding: utf-8 -*-
"""営業日計算と ToSTNeT-3 買付日ルールのテスト

祝日を1日でも取り違えると buy_date が静かにずれるので、
2026年の休場日を丸ごと固定値で持って突き合わせる（出典: JPXの取引所カレンダー）。
"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jp_bizday import holidays, is_bizday, next_bizday   # noqa: E402
from buyback_daily import buy_date_by_rule, rename_tostnet_period   # noqa: E402

NG = 0


def eq(got, want, label):
    global NG
    if got != want:
        NG += 1
        print(f"  NG {label}\n     期待={want}\n     実際={got}")


# 2026年の東証休場日（元日〜3日・祝日・振替休日・国民の休日・大晦日）
HOLIDAYS_2026 = [
    "2026-01-01", "2026-01-02", "2026-01-03",
    "2026-01-12",                       # 成人の日
    "2026-02-11", "2026-02-23",
    "2026-03-20",                       # 春分の日
    "2026-04-29",
    "2026-05-03", "2026-05-04", "2026-05-05",
    "2026-05-06",                       # 憲法記念日が日曜のための振替休日
    "2026-07-20",                       # 海の日
    "2026-08-11",                       # 山の日
    "2026-09-21",                       # 敬老の日
    "2026-09-22",                       # 国民の休日（敬老の日と秋分の日に挟まれた日）
    "2026-09-23",                       # 秋分の日
    "2026-10-12",                       # スポーツの日
    "2026-11-03", "2026-11-23",
    "2026-12-31",
]
eq(sorted(d.isoformat() for d in holidays(2026)), HOLIDAYS_2026, "2026年の休場日")

# 翌営業日
eq(next_bizday("2026-08-10"), "2026-08-12", "山の日を飛ばす")     # 8/11は休場
eq(next_bizday("2026-09-04"), "2026-09-07", "金曜→月曜")
eq(next_bizday("2026-09-03"), "2026-09-04", "平日→翌日")
eq(next_bizday("2026-09-18"), "2026-09-24", "連休(敬老・国民・秋分)を飛ばす")
eq(next_bizday("2026-12-30"), "2027-01-04", "年末年始を飛ばす")
eq(is_bizday(date(2026, 8, 11)), False, "8/11は休場")
eq(is_bizday(date(2026, 8, 12)), True, "8/12は立会あり")

# 買付日ルール（実表題）
CASES = [
    ("2026-09-04", "自己株式の取得及び自己株式立会外買付取引（ToSTNeT-3）による自己株式の買付けに関するお知らせ",
     "2026-09-07"),                                             # 引け後開示 → 翌営業日
    ("2026-08-10", "自己株式の取得及び自己株式立会外買付取引（ToSTNeT-3）による自己株式の買付けに関するお知らせ",
     "2026-08-12"),                                             # 祝日をまたぐ
    ("2026-07-31", "自己株式立会外買付取引（ToSTNeT-3）による自己株式の買付価格確定のお知らせ",
     "2026-07-31"),                                             # 結果報告 → 開示日そのもの
    ("2026-08-20", "自己株式の取得及び自己株式立会外買付取引（Ｎ－ＮＥＴ３）による自己株式の買付けに関するお知らせ",
     "2026-08-21"),                                             # 名証も同じ扱い
    ("2026-08-12", "自己株式立会外買付取引（ToSTNeT-3）による自己株式の買付並びに主要株主の異動（見込み）に関するお知らせ",
     "2026-08-13"),
]
for d, title, want in CASES:
    eq(buy_date_by_rule({"date": d, "title": title}), want, title[:30])

# ToSTNeT-3 の period_* は親の取得枠の期間なので parent_period_* に改名する。
# decision / change のものは本物の取得期間なので触らない
tos = {"type": "tostnet3", "period_from": "2026-01-30", "period_to": "2027-01-29",
       "extract_note": "欠け: period_from,period_to"}
dec = {"type": "decision", "period_from": "2026-01-30", "period_to": "2027-01-29"}
rename_tostnet_period({"a": tos, "b": dec})
eq(sorted(tos), ["extract_note", "parent_period_from", "parent_period_to", "type"], "tostnet3を改名")
eq(tos["extract_note"], "欠け: parent_period_from,parent_period_to", "extract_noteの項目名も揃える")
eq(sorted(dec), ["period_from", "period_to", "type"], "decisionは触らない")

print(f"{'NG ' + str(NG) + '件' if NG else '全て一致 ✓'}")
sys.exit(1 if NG else 0)
