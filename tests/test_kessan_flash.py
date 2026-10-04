# -*- coding: utf-8 -*-
"""決算速報（kessan_flash.py）の読み取りのテスト

断片はすべて 2026-09-14 にTDnetで開示された実物の短信から採った。
数字の読み違いはそのまま公開ページの誤りになるので、壊れやすい所を固定する。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kessan_flash import (extract, forecast_from_text, fc_rev,  # noqa: E402
                          fy_from_title, kind_of, progress, verdict)

NG = 0


def eq(got, want, label):
    global NG
    if got != want:
        NG += 1
        print(f"  NG {label}\n     期待={want!r}\n     実際={got!r}")


def preamble(rows):
    """PreambleToForecasts の中身（表）を組む"""
    body = "".join("<tr>" + "".join(f"<td><p>{c}</p></td>" for c in r) + "</tr>" for r in rows)
    return ('<ix:nonNumeric contextRef="x" name="tse-ed-t:PreambleToForecasts">'
            f"<table>{body}</table></ix:nonNumeric>")


# ---- 表題の分類（訂正短信を今日の決算に混ぜない）
eq(kind_of("2027年１月期 第２四半期（中間期）決算短信〔日本基準〕（連結）"), "new", "通常の短信")
eq(kind_of("2026年７月期 決算短信（ＲＥＩＴ）"), "new", "REIT短信も短信")
eq(kind_of("(訂正・数値データ訂正)「2024年12月期 第3四半期決算短信〔日本基準〕（連結）」 の一部訂正に関するお知らせ"),
   "fix", "過去の期の訂正短信")
eq(kind_of("四半期連結財務諸表に対して公認会計士等による期中レビューを受けた2026年10月期第３四半期決算短信の開示が四半期末後45日を超えることに関するお知らせ"),
   None, "短信ではないお知らせ")
eq(kind_of("業績予想の修正に関するお知らせ"), None, "短信ではない")

# ---- 期は表題から（XBRLに FiscalYearEnd が無い様式がある）
eq(fy_from_title("2027年４月期 第１四半期決算短信〔日本基準〕（連結）"), "2027/04", "全角数字の期")
eq(fy_from_title("2026年10月期 第3四半期決算短信〔IFRS〕（連結）"), "2026/10", "2桁の月")

# ---- 予想表を文字で埋めている短信（イムラ 3955）
t = forecast_from_text(preamble([
    ["(％表示は、対前期増減率)"],
    ["売上高", "営業利益", "経常利益", "親会社株主に帰属する当期純利益", "1株当たり当期純利益"],
    ["百万円", "％", "百万円", "％", "百万円", "％", "百万円", "％", "円銭"],
    ["通期", "22,500", "3.1", "700", "△38.4", "750", "△36.7", "460", "△51.8", "46.07"],
    ["（注）直近に公表されている業績予想からの修正の有無：無"],
]))
eq((t.get("fc_sales"), t.get("fc_op"), t.get("fc_np"), t.get("fc_rev_flag")),
   (22500.0, 700.0, 460.0, "無"), "文字の予想表を列に当てはめる")

# 赤字予想（ReYuuJapan 9425）
t = forecast_from_text(preamble([
    ["売上高", "営業利益", "経常利益", "当期純利益", "1株当たり当期純利益"],
    ["百万円", "％", "百万円", "％", "百万円", "％", "百万円", "％", "円銭"],
    ["通期", "8,400", "134.2", "△52", "－", "△105", "－", "△107", "－", "△15.67"],
]))
eq((t.get("fc_sales"), t.get("fc_op"), t.get("fc_np")), (8400.0, -52.0, -107.0), "△は負の数")

# 売上と営業利益しか出さない会社（WHY HOW DO 3823）。第2四半期の行は読まない
t = forecast_from_text(preamble([
    ["売上高", "営業利益"],
    ["百万円", "％", "百万円", "％"],
    ["第2四半期(累計)", "―", "―", "―", "―"],
    ["通期", "6,259", "―", "165", "―"],
]))
eq((t.get("fc_sales"), t.get("fc_op"), t.get("fc_np")), (6259.0, 165.0, None), "列が少ない表・通期の行だけ")

# レンジ予想（Link-Uグループ 4446）は1つの数字にしない
t = forecast_from_text(preamble([
    ["売上収益", "営業利益", "税引前利益", "親会社の所有者に帰属する当期利益", "基本的１株当たり当期利益"],
    ["百万円", "％", "百万円", "％", "百万円", "％", "百万円", "％", "円銭"],
    ["通期", "4,000～4,200", "△16.7～△12.5", "400～480", "－～－", "370～450", "－～－",
     "241～293", "－～－", "17.00～20.67"],
]))
eq((t.get("fc_sales"), t.get("fc_op"), t.get("fc_range")), (None, None, True), "レンジ予想は数字にしない")

# セル数が合わない表は読まない（列を取り違えるくらいなら空にする）
t = forecast_from_text(preamble([
    ["売上高", "営業利益"],
    ["百万円", "％", "百万円", "％"],
    ["通期", "6,259", "165"],
]))
eq(t, {}, "セル数が合わない表は読まない")

# ---- 判定
eq(verdict(0.9, -1.9), "増収減益", "増収減益")
eq(verdict(-12.9, None), None, "前年が赤字で営業の率が無ければ判定しない")
base = {"q": "3Q", "fc_op": 5900.0, "fc_sales": 62900.0, "op": 4713.0}
eq(fc_rev({**base, "fc_rev_flag": "無"}, None), "据置", "修正無＝据置")
eq(fc_rev({**base, "fc_rev_flag": "有"}, 6500.0), "下方", "前回より低い＝下方")
eq(fc_rev({**base, "fc_rev_flag": "有"}, None), None, "修正有でも前回が分からなければ空")
eq(fc_rev({"q": "本決算", "fc_op": 485.0, "fc_sales": 3195.0}, None), "新規", "本決算は来期の新規予想")
eq(fc_rev({"q": "1Q", "fc_op": None, "fc_sales": None}, None), "なし", "予想を出していない")
eq(progress(base), 79.9, "進捗率（巴工業 6309）")
eq(progress({"q": "3Q", "fc_op": -552.0, "op": -300.0}), None, "赤字予想の進捗は出さない")


# ---- 売上高のタグ違い（2026-10-04 に直した）
#   小売の「営業収益」は OperatingRevenues、IFRS には NetSalesIFRS の会社がある。
#   NetSales / SalesIFRS に決め打ちしていた頃は売上高が空になり、判定も出せなかった。
def summary_zip(kind, tags):
    """最小のサマリーixbrlを1枚だけ入れたZIPを作る（実物の短信と同じ書式）"""
    import io
    import zipfile
    body = "".join(
        f'<ix:nonFraction name="tse-ed-t:{n}" contextRef="{c}" scale="6" decimals="-6">{v}</ix:nonFraction>'
        for n, c, v in tags)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(f"XBRLData/Summary/tse-{kind}-00000-20260925-ixbrl.htm", f"<html>{body}</html>")
    return buf.getvalue()


CUR = "CurrentAccumulatedQ2Duration_ConsolidatedMember_ResultMember"
PRI = "PriorAccumulatedQ2Duration_ConsolidatedMember_ResultMember"
q2 = ("QuarterlyPeriod", "CurrentAccumulatedQ2Instant", "2")
r = extract(summary_zip("qcedjpsm", [
    q2, ("OperatingRevenues", CUR, "119,023"), ("ChangeInOperatingRevenues", CUR, "6.7"),
    ("OperatingIncome", CUR, "5,708"), ("ChangeInOperatingIncome", CUR, "-2.6")]))
eq((r["sales"], r["sales_yoy"], verdict(r["sales_yoy"], r["op_yoy"])), (119023.0, 6.7, "増収減益"),
   "営業収益で売上を出す会社（ハローズ 2742）")

r = extract(summary_zip("qcedifsm", [
    q2, ("NetSalesIFRS", CUR, "1,986,357"), ("ChangeInNetSalesIFRS", CUR, "2.4"),
    ("OperatingIncomeIFRS", CUR, "100,000"), ("OperatingIncomeIFRS", PRI, "120,000")]))
eq((r["sales"], r["sales_yoy"]), (1986357.0, 2.4), "IFRSで NetSalesIFRS を使う会社（ニデック 6594）")

print(f"{'NG ' + str(NG) + '件' if NG else '全て一致 ✓'}")
sys.exit(1 if NG else 0)
