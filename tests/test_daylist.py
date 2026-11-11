# -*- coding: utf-8 -*-
"""日付つき一覧の部品のテスト（tob_fetch.classify / extract・kessan_yotei_hist.merge）。TDnet の実際の表題（2026-10-29〜11-11）で

    python -X utf8 tests/test_daylist.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import kessan_yotei_hist as K  # noqa: E402
import tob_fetch as T  # noqa: E402

CASES = [
    ("株式会社NSSK-EPSILON1による当社株券に対する公開買付けに関する意見表明（賛同）及び応募推奨のお知らせ", ("opinion", True)),
    ("株式会社NSSK-EPSILON1によるブロードメディア株式会社（証券コード：4347）の株券に対する公開買付けの開始に関するお知らせ", ("start", False)),
    ("自己株式の取得及び自己株式の公開買付けに関するお知らせ", ("self", True)),
    ("アルビス株式会社（証券コード：7475）に対する公開買付けの結果及び子会社の異動に関するお知らせ", ("result", False)),
    ("株式会社ライフコーポレーションによる当社株式に対する公開買付けの結果並びに親会社、主要株主及び主要株主である筆頭株主の異動に関するお知らせ", ("result", True)),
    ("Kamgras １株式会社による株式会社カカクコム（証券コード：2371）の株券等に対する公開買付けの買付条件等の変更に関するお知らせ", ("change", False)),
    ("合同会社広大による当社株式に対する公開買付け（上場維持前提のMBOの実施）に関する意見表明並びに資本業務提携契約の解消及び業務提携契約締結のお知らせ", ("opinion", True)),
    # TOB ではない（買集め・応募・損益）
    ("主要株主の異動（予定）および公開買付けに準ずる行為として政令で定める買集め行為に関するお知らせ", None),
    ("株式会社中電工による自己株式の公開買付けへの応募および特別利益の計上見込みについて", None),
    ("公開買付けに伴う株式売却に係る「持分法による投資利益」の計上（見込み）および業績への影響に関するお知らせ", None),
    ("2026年９月期 決算短信〔日本基準〕（連結）", None),
]


def main():
    ng = 0
    for title, want in CASES:
        got = T.classify(title)
        if got != want:
            ng += 1
            print(f"NG classify: {title[:50]} → {got}（期待 {want}）")
    ex = T.extract(T.norm("1.買付け等の価格 普通株式1株につき、金2,500円 2.買付け等の期間 2026年10月30日（金曜日）から2026年12月14日（月曜日）まで（30営業日）"
                          " 本公開買付けは成立いたしました"))
    if ex.get("price") != 2500 or ex.get("start") != "2026-10-30" or ex.get("end") != "2026-12-14" or ex.get("success") is not True:
        ng += 1
        print(f"NG extract: {ex}")
    ex = T.extract(T.norm("2026年8月28日付で公表いたしました「株式会社電通総研の株式に対する公開買付けの開始予定に関するお知らせ」"))
    if ex.get("pre_ann") != "2026-08-28":
        ng += 1
        print(f"NG pre_ann: {ex}")
    # 決算発表予定の記録: 過ぎた日は縮んだ一覧で上書きしない・今日以降は置き換える
    store = {}
    K.merge(store, {"d": "2026-11-12", "n": 3, "past": False, "items": [{"c": "1"}, {"c": "2"}, {"c": "3"}]})
    K.merge(store, {"d": "2026-11-12", "n": 3, "past": True, "items": [{"c": "1"}]})
    if len(store["2026-11-12"]["items"]) != 3:
        ng += 1
        print("NG merge: 過ぎた日を上書きした")
    K.merge(store, {"d": "2026-11-13", "n": 1, "past": False, "items": [{"c": "9"}]})
    K.merge(store, {"d": "2026-11-13", "n": 2, "past": False, "items": [{"c": "9"}, {"c": "8"}]})
    if len(store["2026-11-13"]["items"]) != 2:
        ng += 1
        print("NG merge: 今日以降を置き換えない")
    print("OK" if not ng else f"NG {ng}件")
    return 1 if ng else 0


if __name__ == "__main__":
    sys.exit(main())
