# -*- coding: utf-8 -*-
"""業績予想・配当予想の修正の XBRL 読み取り（fc_revision.py）のテスト

数字は 2026-11-12 に TDnet で開示された実物の XBRL（0912 の zip）から採り、同じ開示の PDF と手で照合した。
  7264 ムロコーポレーション 連結業績予想の修正（通期 営業利益 1,660→1,244＝下方。中間期の修正も同じ書類にある）
  3744 サイオス           業績予想の修正（営業利益 450→210＝下方・売上高 20,000→21,000＝上方＝まちまち）
  3182 オイシックス       業績予想の修正及び配当予想の修正（営業利益は据置 8,700・純利益 5,210→5,920＝上方・期末配当 30→34円）
  3775 ガイアックス       通期連結業績予想の修正（営業利益 250 → 0～50 のレンジ＝下方）
  2323 （上方修正の表題） 売上高だけ 3,599→3,965、利益は据置
"""
import io
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fc_revision import decide, direction, parse, RV_ZIP  # noqa: E402

NG = 0


def eq(got, want, label):
    global NG
    if got != want:
        NG += 1
        print(f"  NG {label}\n     期待={want!r}\n     実際={got!r}")


def nf(name, ctx, shown, scale=6, sign=False):
    s = ' sign="-"' if sign else ""
    return (f'<ix:nonFraction name="tse-ed-t:{name}" contextRef="{ctx}" unitRef="JPY" decimals="-6" '
            f'scale="{scale}" format="ixt:numdotdecimal"{s}>{shown}</ix:nonFraction>')


def fc(name, mem, pv, cv, per="CurrentYear", cons="ConsolidatedMember", kind="Forecast"):
    out = []
    for m, v in (("PreviousMember", pv), ("CurrentMember", cv)):
        if v is None:
            continue
        if isinstance(v, tuple):
            out.append(nf(name, f"{per}Duration_{cons}_{m}_LowerMember", f"{abs(v[0]):,}", sign=v[0] < 0))
            out.append(nf(name, f"{per}Duration_{cons}_{m}_UpperMember", f"{abs(v[1]):,}", sign=v[1] < 0))
        else:
            out.append(nf(name, f"{per}Duration_{cons}_{m}_{kind}Member", f"{abs(v):,}", sign=v < 0))
    return "".join(out)


def zipped(*docs):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for i, (kind, body) in enumerate(docs):
            # 空欄は自己終了タグ（xsi:nil）で書かれる。読み飛ばすこと
            nil = '<ix:nonFraction name="tse-ed-t:NetSales" contextRef="CurrentYearDuration_ConsolidatedMember_PreviousMember_LowerMember" xsi:nil="true"/>'
            z.writestr(f"tse-{kind}-00000-2026111200000{i}-ixbrl.htm", f"<html><body>{nil}{body}</body></html>")
            z.writestr(f"tse-{kind}-00000-2026111200000{i}.xsd", "")
    return b.getvalue()


# ── 7264 ムロコーポレーション: 通期を選ぶ（中間期も書類にあるが通期が先）
muro = zipped(("rvfc", fc("NetSales", 0, 24139, 24010) + fc("OperatingIncome", 0, 1660, 1244)
                + fc("OrdinaryIncome", 0, 1720, 1409) + fc("ProfitAttributableToOwnersOfParent", 0, 1212, 1016)
                + fc("NetSales", 0, 11928, 12021, per="CurrentAccumulatedQ2")
                + fc("OperatingIncome", 0, 894, 584, per="CurrentAccumulatedQ2")))
r = parse(muro)
eq((r["per"], r["cons"], r["dir"], r["by"], r["mix"]), ("通期", True, "下方", "営業利益", None), "7264 通期・下方")
eq((r["prev"], r["cur"]), ({"sales": 24139.0, "op": 1660.0, "ord": 1720.0, "np": 1212.0},
                           {"sales": 24010.0, "op": 1244.0, "ord": 1409.0, "np": 1016.0}), "7264 数字は書類のまま（百万円）")
eq(r["k"], "rvfc", "7264 様式")

# ── 3744 サイオス: 利益は下方・売上高は上方＝まちまち（dir は営業利益で決める）
sios = zipped(("rvfc", fc("NetSales", 0, 20000, 21000) + fc("OperatingIncome", 0, 450, 210)
                + fc("OrdinaryIncome", 0, 510, 270) + fc("ProfitAttributableToOwnersOfParent", 0, 370, 170)))
r = parse(sios)
eq((r["dir"], r["by"], r["mix"], r.get("mixed")), ("下方", "営業利益", "売上高は上方", True), "3744 まちまち")

# ── 3182 オイシックス: 営業利益は据置 → 純利益で上方。配当（期末＝年間）30→34円
oisix = zipped(("rvfc", fc("NetSales", 0, 252000, 252000) + fc("OperatingIncome", 0, 8700, 8700)
                 + fc("ProfitAttributableToOwnersOfParent", 0, 5210, 5920)
                 + nf("DividendPerShare", "CurrentYearDuration_AnnualMember_NonConsolidatedMember_PreviousMember_ForecastMember", "30.00", scale=0)
                 + nf("DividendPerShare", "CurrentYearDuration_AnnualMember_NonConsolidatedMember_CurrentMember_ForecastMember", "34.00", scale=0)))
r = parse(oisix)
eq((r["dir"], r["by"], r["mix"]), ("上方", "純利益", None), "3182 主の利益が同じなら次の利益")
eq(r["div"], {"part": "年間", "prev": 30.0, "cur": 34.0, "dir": "増配"}, "3182 配当")

# ── 3775 ガイアックス: レンジ予想
gaia = zipped(("rvfc", fc("NetSales", 0, 3300, 3300) + fc("OperatingIncome", 0, 250, (0, 50))
                + fc("OrdinaryIncome", 0, 220, (0, 50)) + fc("ProfitAttributableToOwnersOfParent", 0, 180, (-30, 20))))
r = parse(gaia)
eq((r["dir"], r["cur"]["op"], r["cur"]["np"]), ("下方", [0.0, 50.0], [-30.0, 20.0]), "3775 レンジ")

# ── 2323: 売上高だけ上方・利益は据置
r = parse(zipped(("rvfc", fc("NetSales", 0, 3599, 3965) + fc("OperatingIncome", 0, 472, 472)
                   + fc("OrdinaryIncome", 0, 447, 447))))
eq((r["dir"], r["by"], r["mix"], r.get("mixed")), ("上方", "売上高", "利益は据置", None), "2323 売上だけの修正")

# ── 連結への移行（連結は修正前なし）＋ 個別の修正 → 個別で決める
r = parse(zipped(("rvfc", fc("NetSales", 0, None, 3000) + fc("OperatingIncome", 0, None, 200)
                   + fc("NetSales", 0, 2700, 3000, cons="NonConsolidatedMember")
                   + fc("OperatingIncome", 0, 190, 260, cons="NonConsolidatedMember"))))
eq((r["cons"], r["dir"]), (False, "上方"), "連結移行＋個別の修正")

# ── 修正前の数字が無い（新しく公表した予想）
r = parse(zipped(("rvfc", fc("NetSales", 0, None, 3000) + fc("OperatingIncome", 0, None, 200))))
eq((r["dir"], r["why"]), (None, "修正前の数字が無い（新しく公表した予想）"), "新規の公表は向きなし")

# ── 配当だけ（rvdf）
r = parse(zipped(("rvdf", nf("DividendPerShare", "CurrentYearDuration_AnnualMember_NonConsolidatedMember_PreviousMember_ForecastMember", "106.00", scale=0)
                   + nf("DividendPerShare", "CurrentYearDuration_AnnualMember_NonConsolidatedMember_CurrentMember_ForecastMember", "108.00", scale=0))))
eq((r["k"], "dir" in r, r["div"]["dir"]), ("rvdf", False, "増配"), "配当だけの修正")

# ── 赤字の縮小は上方・赤字への転落は下方（数の大小で決める）
r = parse(zipped(("rvfc", fc("OperatingIncome", 0, -300, -100))))
eq(r["dir"], "上方", "赤字縮小")
r = parse(zipped(("rvfc", fc("OperatingIncome", 0, 50, -211))))
eq(r["dir"], "下方", "赤字転落")

# ── 部品
eq(direction((100, 100), (50, 120)), None, "幅が重なれば決めない")
eq(direction((100, 200), (250, 300)), "上", "幅どうし")
eq(decide({"op": (1, 1)}, {"op": (1, 1)}), ("据置", "op", None), "全部同じは据置")
eq(bool(RV_ZIP.search("https://www.release.tdnet.info/inbs/091220261112576461.zip")), True, "0912＝修正")
eq(bool(RV_ZIP.search("https://www.release.tdnet.info/inbs/081220261112576273.zip")), False, "0812＝短信")

print("fc_revision: " + ("OK" if NG == 0 else f"NG {NG}件"))
sys.exit(1 if NG else 0)
