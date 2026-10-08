# -*- coding: utf-8 -*-
"""大量保有の人物・機関（holders_build.py）のテスト。ネットに出ない"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from holders_build import is_proposal, build, check_private, iso_date      # noqa: E402

NG = 0


def eq(got, want, label):
    global NG
    if got != want:
        NG += 1
        print(f"  NG {label}\n     期待={want!r}\n     実際={got!r}")


# ---- 「重要提案」の否定は数えない（実データの言い回し）
eq(is_proposal("経営の安定を図る安定株主として保有しており、重要提案行為等を行う予定はありません。"), False, "予定はありません")
eq(is_proposal("純投資。重要提案行為等は行いません。"), False, "行いません")
eq(is_proposal("長期投資並びに株主価値の向上及び保全のため重要提案行為等を行うことがある。"), True, "行うことがある")
eq(is_proposal("純投資及び状況に応じて重要提案行為等を行うこと。"), True, "行うこと")


def doc(i, d, typ, code, ratio, holders, special=False, n=1):
    return {"id": i, "d": d, "tm": "09:00", "type": typ, "special": special, "issuer": {"code": code, "name": "N" + code},
            "ratio": ratio, "ratio_prev": None, "n_holders": n, "holders": holders}


def h(e, ratio, purpose="純投資", kind="法人(株式会社)", trades=(), occ=None):
    x = {"e": e, "name": e + "社", "kind": kind, "ratio": ratio, "purpose": purpose, "trades": list(trades)}
    if occ:
        x["occupation"] = occ
    return x


docs = [
    doc("S1", "2026-01-05", "大量保有", "1111", 6.0, [h("E1", 6.0)]),
    doc("S2", "2026-02-05", "変更", "1111", 7.5, [h("E1", 7.5, "重要提案行為等を行うことがある")]),
    doc("S3", "2026-03-05", "訂正", "1111", 2.0, [h("E1", 2.0)]),                       # 訂正は状態に使わない
    doc("S4", "2026-01-10", "大量保有", "2222", 5.5, [h("E1", 3.0), h("E2", 2.5, kind="個人", occ="会社役員")], n=2),
    doc("S5", "2026-03-10", "変更", "2222", 4.0, [h("E1", 2.0), h("E2", 2.0, kind="個人", occ="会社役員")], n=2),
    doc("S6", "2026-03-11", "変更", "3333", 5.2, [h("E3", 5.2)], special=True),
]
ev = {"S1": {"r20": 1.0, "r60": 2.0}, "S4": {"r20": -1.0, "r60": None}}
H_, by_issuer, latest, moves, changes = build(docs, {"E1"}, {"E1": 1}, ev)
eq([x["code"] for x in H_["E1"]["holdings"]], ["1111"], "共同保有の合計が5%未満になった銘柄は保有中から外す・訂正で消えない")
eq(H_["E1"]["holdings"][0]["ratio_own"], 7.5, "訂正ではなく最新の変更報告の割合")
eq([x["code"] for x in H_["E1"]["exited"]], ["2222"], "5%未満に下がった銘柄は exited")
eq(H_["E1"]["stats"]["n60"], 1, "60営業日が出た新規報告だけ数える")
eq(sorted(by_issuer), ["1111", "3333"], "銘柄側の索引は保有中だけ")
eq(set(H_["E2"]) & {"kind", "reason"}, set(), "個人は区分・提出事由を持たない")
eq(H_["E2"]["occupation"], "会社役員", "個人は職業を出す")
eq([(c["code"], c["d"]) for c in changes], [("1111", "2026-02-05")], "純投資→重要提案の変化")
eq(latest["date"], "2026-03-11", "いちばん新しい提出日")
eq([m["id"] for m in moves], ["S5"], "注目者の直近30日の報告（訂正を除く。2/9以降はS5だけ）")
H_["E2"]["occupation"] = "東京都港区六本木1-2-3"
eq(check_private(H_), ["E2"], "個人の欄に住所の形があれば止める")

# ---- 取得・処分の日付は和暦などが混ざる → ISO にそろえる
eq(iso_date("令和8年1月9日"), "2026-01-09", "令和")
eq(iso_date("令和元年5月1日"), "2019-05-01", "令和元年")
eq(iso_date("2026年1月9日"), "2026-01-09", "西暦の年月日")
eq(iso_date("2026/1/9"), "2026-01-09", "スラッシュ")
eq(iso_date("2026-01-09"), "2026-01-09", "ISO")
eq(iso_date("不明"), None, "読めない日付")

print("OK" if NG == 0 else f"NG {NG}件")
sys.exit(1 if NG else 0)
