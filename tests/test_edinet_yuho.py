# -*- coding: utf-8 -*-
"""edinet_yuho.py の読み取りのテスト（主要な経営指標等の推移）

XBRL の例は 2026 年の実物（キユーピー 2809・トヨタ 7203・東京海上 8766・ウェザーニューズ 4825）の形を、
要る要素だけに縮めて書き写したもの。値は有報の PDF の表と照合済み。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import edinet_yuho as Y   # noqa: E402

NG = 0


def eq(got, want, label):
    global NG
    if got != want:
        NG += 1
        print(f"  NG {label}\n     期待={want}\n     実際={got}")


def ctxs(ends, solo=False):
    """期末日の古い順 → Prior4..Current の Duration と Instant の文脈"""
    sfx = "_NonConsolidatedMember" if solo else ""
    out = []
    n = len(ends)
    for i, e in enumerate(ends):
        k = n - 1 - i
        p = "Current" if k == 0 else f"Prior{k}"
        y = int(e[:4]) - 1
        s = f"{y}-{int(e[5:7]) % 12 + 1:02d}-01"
        out.append(f'<xbrli:context id="{p}YearDuration{sfx}"><xbrli:entity/><xbrli:period>'
                   f'<xbrli:startDate>{s}</xbrli:startDate><xbrli:endDate>{e}</xbrli:endDate></xbrli:period></xbrli:context>')
        out.append(f'<xbrli:context id="{p}YearInstant{sfx}"><xbrli:entity/><xbrli:period>'
                   f'<xbrli:instant>{e}</xbrli:instant></xbrli:period></xbrli:context>')
    return "".join(out)


def doc(body, ends, solo_ends=None, ext_ns="jpcrp030000-asr_E00000-000"):
    c = ctxs(ends) + (ctxs(solo_ends, True) if solo_ends else "")
    return (f'<?xml version="1.0" encoding="UTF-8"?>'
            f'<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance"'
            f' xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
            f' xmlns:jpcrp_cor="http://disclosure.edinet-fsa.go.jp/taxonomy/jpcrp/2025-11-01/jpcrp_cor"'
            f' xmlns:ext="http://disclosure.edinet-fsa.go.jp/{ext_ns}/2026-06-10">'
            f'{c}{body}</xbrli:xbrl>').encode("utf-8")


def el(name, ctx, v, ns="jpcrp_cor"):
    return f'<{ns}:{name} contextRef="{ctx}" decimals="-6" unitRef="JPY">{v}</{ns}:{name}>'


E5 = ["2021-11-30", "2022-11-30", "2023-11-30", "2024-11-30", "2025-11-30"]
P = ["Prior4", "Prior3", "Prior2", "Prior1", "Current"]

# ── 日本基準・連結（キユーピーの形）。配当は単体の文脈にある。重複した要素は最初の1つ
body = ""
for p, rev, ord_, ni, eps, div in zip(P, [407039, 430304, 455086, 483985, 513417], [29698, 27249, 20490, 36874, 37389],
                                      [18014, 16033, 13174, 21419, 30506], [128.17, 115.34, 94.78, 154.10, 220.63],
                                      [47, 47, 50, 54, 64]):
    body += el("NetSalesSummaryOfBusinessResults", f"{p}YearDuration", rev * 10**6)
    body += el("NetSalesSummaryOfBusinessResults", f"{p}YearDuration", 1)      # インラインXBRL由来の重複
    body += el("OrdinaryIncomeLossSummaryOfBusinessResults", f"{p}YearDuration", ord_ * 10**6)
    body += el("ProfitLossAttributableToOwnersOfParentSummaryOfBusinessResults", f"{p}YearDuration", ni * 10**6)
    body += el("BasicEarningsLossPerShareSummaryOfBusinessResults", f"{p}YearDuration", eps)
    body += el("RateOfReturnOnEquitySummaryOfBusinessResults", f"{p}YearDuration", 0.097)
    body += el("NetAssetsSummaryOfBusinessResults", f"{p}YearInstant", 347600 * 10**6)
    body += el("NetSalesSummaryOfBusinessResults", f"{p}YearDuration_NonConsolidatedMember", 211682 * 10**6)
    body += el("NetIncomeLossSummaryOfBusinessResults", f"{p}YearDuration_NonConsolidatedMember", 25244 * 10**6)
    body += el("DividendPaidPerShareSummaryOfBusinessResults", f"{p}YearDuration_NonConsolidatedMember", div)
body += '<jpcrp_cor:OperatingRevenue1SummaryOfBusinessResults contextRef="CurrentYearDuration" xsi:nil="true"/>'
r = Y.parse_xbrl(doc(body, E5, E5))
eq((r["std"], r["cons"], r["rev_l"], len(r["fy"])), ("JP", True, "売上高", 5), "JP 連結: 基準・連結・呼び名・期の数")
eq([f["rev"] for f in r["fy"]], [x * 10**6 for x in [407039, 430304, 455086, 483985, 513417]], "JP 連結: 売上高（重複は最初）")
eq(r["fy"][-1].get("ord"), 37389 * 10**6, "JP 連結: 経常利益")
eq(r["fy"][-1].get("ni"), 30506 * 10**6, "JP 連結: 純利益は連結（単体の NetIncomeLoss ではない）")
eq([f.get("div") for f in r["fy"]], [47, 47, 50, 54, 64], "JP 連結: 配当は単体の行から")
eq(r["fy"][-1].get("na"), 347600 * 10**6, "JP 連結: 純資産（Instant の文脈）")
eq(("op" in r["fy"][-1], "pbt" in r["fy"][-1]), (False, False), "JP 連結: 営業利益・税引前は無ければ出さない")
eq((r["fy"][0]["s"], r["fy"][0]["e"]), ("2020-12-01", "2021-11-30"), "JP 連結: 期の始まりと終わり")

# ── 単体だけの会社
body = ""
for p in P:
    body += el("NetSalesSummaryOfBusinessResults", f"{p}YearDuration_NonConsolidatedMember", 5539581000)
    body += el("NetIncomeLossSummaryOfBusinessResults", f"{p}YearDuration_NonConsolidatedMember", 401136000)
r = Y.parse_xbrl(doc(body, [], E5))
eq((r["cons"], r["fy"][-1].get("ni")), (False, 401136000), "単体だけ: 単体の当期純利益")

# ── IFRS・売上が会社独自の行（トヨタの形）。呼び名は _lab.xml、並びは _pre.xml。営業利益の独自行も拾う
E3 = ["2024-03-31", "2025-03-31", "2026-03-31"]
body = ""
for p, rev in zip(P[2:], [45095325, 48036704, 50684952]):
    body += el("OperatingRevenuesIFRSKeyFinancialData", f"{p}YearDuration", rev * 10**6, ns="ext")
    body += el("NumberOfEmployeesIFRSSummaryOfBusinessResults", f"{p}YearDuration", 390927, ns="ext")
    body += el("OperatingProfitLossIFRSKeyFinancialData", f"{p}YearDuration", 4795586 * 10**6, ns="ext")
    body += el("ProfitLossBeforeTaxIFRSSummaryOfBusinessResults", f"{p}YearDuration", 5152996 * 10**6)
    body += el("ProfitLossAttributableToOwnersOfParentIFRSSummaryOfBusinessResults", f"{p}YearDuration", 3848098 * 10**6)
    body += el("BasicEarningsLossPerShareIFRSSummaryOfBusinessResults", f"{p}YearDuration", 295.25)
    body += el("EquityToAssetRatioIFRSSummaryOfBusinessResults", f"{p}YearInstant", 3062.82)
    body += el("RatioOfOwnersEquityToGrossAssetsIFRSSummaryOfBusinessResults", f"{p}YearInstant", 0.378)
lab = ('<link:linkbase xmlns:link="x" xmlns:xlink="y"><link:labelLink>'
       '<link:loc xlink:type="locator" xlink:href="a.xsd#jpcrp030000-asr_E02144-000_OperatingRevenuesIFRSKeyFinancialData"'
       ' xlink:label="L1" />'
       '<link:label xlink:type="resource" xlink:label="R1" xlink:role="http://www.xbrl.org/2003/role/label"'
       ' xml:lang="ja">営業収益</link:label>'
       '<link:label xlink:type="resource" xlink:label="R1v" xlink:role="http://www.xbrl.org/2003/role/verboseLabel"'
       ' xml:lang="ja">営業収益（IFRS）、経営指標等</link:label>'
       '<link:labelArc xlink:type="arc" xlink:from="L1" xlink:to="R1" />'
       '<link:labelArc xlink:type="arc" xlink:from="L1" xlink:to="R1v" />'
       '</link:labelLink></link:linkbase>').encode("utf-8")
r = Y.parse_xbrl(doc(body, E3), lab=lab)
eq((r["std"], r["rev_l"], len(r["fy"])), ("IFRS", "営業収益", 3), "IFRS 独自行: 基準・呼び名（会社のラベル）")
f = r["fy"][-1]
eq((f.get("rev"), f.get("pbt"), f.get("op"), f.get("bps"), f.get("eqr"), "ord" in f),
   (50684952 * 10**6, 5152996 * 10**6, 4795586 * 10**6, 3062.82, 0.378, False),
   "IFRS 独自行: 売上・税引前・営業利益・1株持分（EquityToAssetRatioIFRS）・持分比率")

# ── 独自行が2つ以上: 標準の売上があれば標準。無ければ表示の順（_pre.xml）の先頭
body = ""
for p in P[2:]:
    body += el("NetRevenueSummaryOfBusinessResults", f"{p}YearDuration", 2, ns="ext")
    body += el("SalesAndFinancialServicesRevenueIFRSKeyFinancialData", f"{p}YearDuration", 1, ns="ext")
    body += el("ProfitLossAttributableToOwnersOfParentIFRSSummaryOfBusinessResults", f"{p}YearDuration", 5)
pre = ('<x xlink:href="a.xsd#jpcrp030000-asr_E01777-000_SalesAndFinancialServicesRevenueIFRSKeyFinancialData"/>'
       '<x xlink:href="a.xsd#jpcrp030000-asr_E01777-000_NetRevenueSummaryOfBusinessResults"/>').encode("utf-8")
r = Y.parse_xbrl(doc(body, E3), pre=pre)
eq((r["fy"][-1]["rev"], r["rev_l"]), (1, "売上高等"), "独自行が2つ: 表示の順の先頭・ラベルが無ければ「売上高等」")
body2 = body + "".join(el("RevenueIFRSSummaryOfBusinessResults", f"{p}YearDuration", 9) for p in P[2:])
r = Y.parse_xbrl(doc(body2, E3), pre=pre)
eq((r["fy"][-1]["rev"], r["rev_l"]), (9, "売上収益"), "標準の売上収益があれば独自行より優先")

# ── 期の途中で IFRS へ移った会社（東京海上の形）: 古い期は日本基準の行（経常収益・経常利益）
body = ""
for p in P[:3]:
    body += el("OrdinaryIncomeSummaryOfBusinessResults", f"{p}YearDuration", 100)
    body += el("OrdinaryIncomeLossSummaryOfBusinessResults", f"{p}YearDuration", 10)
    body += el("ProfitLossAttributableToOwnersOfParentSummaryOfBusinessResults", f"{p}YearDuration", 7)
for p in P[3:]:
    body += el("InsuranceRevenueIFRSKeyFinancialData", f"{p}YearDuration", 200, ns="ext")
    body += el("NumberOfEmployeesJapanGAAPKeyFinancialData", f"{p}YearDuration", 3, ns="ext")
    body += el("ProfitLossBeforeTaxIFRSSummaryOfBusinessResults", f"{p}YearDuration", 20)
    body += el("ProfitLossAttributableToOwnersOfParentIFRSSummaryOfBusinessResults", f"{p}YearDuration", 14)
r = Y.parse_xbrl(doc(body, E5))
eq([f["std"] for f in r["fy"]], ["JP", "JP", "JP", "IFRS", "IFRS"], "基準の移行: 期ごとの基準")
eq([f.get("rev_l") for f in r["fy"]], ["経常収益", "経常収益", "経常収益", None, None], "基準の移行: 主と違う呼び名だけ期に書く")
eq([f["rev"] for f in r["fy"]], [100, 100, 100, 200, 200], "基準の移行: 売上の行")
eq((r["fy"][0].get("ord"), r["fy"][-1].get("pbt")), (10, 20), "基準の移行: 経常利益と税引前利益")

# ── 主要な経営指標の行が無い書類
eq(Y.parse_xbrl(doc("", E5)), None, "行が無ければ None")

print("OK" if not NG else f"NG {NG}件")
sys.exit(1 if NG else 0)
