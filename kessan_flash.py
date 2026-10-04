# -*- coding: utf-8 -*-
"""決算短信の数字を当日中に出す → docs/kessan_flash.json ＋ docs/kessan_flash_hist/YYYY-MM.json

TDnetの一覧（tdnet_list.py）から「決算短信」を拾い、XBRLのサマリーから数字を抜く。

■ 要素名は実物で確認した（2026-10-03・2026-09-14開示の4本）
  名前空間はどの基準でも `tse-ed-t:`。基準は**要素名の接尾辞**で変わる。
    日本基準 : NetSales / OperatingIncome / OrdinaryIncome
               ProfitAttributableToOwnersOfParent（連結）/ NetIncome（非連結）
    IFRS     : SalesIFRS / OperatingIncomeIFRS / ProfitBeforeTaxIFRS
               ProfitAttributableToOwnersOfParentIFRS（経常利益はIFRSに無いので ord は空になる）
    米国基準 : 同じ形の接尾辞 US。実物に当たれていないので、取れたら入り、取れなければ空
  前年同期比は自分で計算せず `ChangeIn*` をそのまま使う（短信が公表した値を出す）。

■ 文脈（contextRef）の形
  当期実績 : Current{Year|AccumulatedQ1|AccumulatedQ2|AccumulatedQ3}Duration_{Consolidated|NonConsolidated}Member_ResultMember
  通期予想 : 四半期短信 → CurrentYearDuration_..._ForecastMember
             本決算短信 → NextYearDuration_..._ForecastMember（＝来期の新規予想）
  レンジ予想は _LowerMember / _UpperMember（中身が空のこともある）

■ 数値の読み方（ここを間違えると桁がずれる）
  金額は scale="6"（百万円単位で表示）が普通だが千円単位もある。
  実額 = 表示値 × 10^scale、百万円 = 実額 ÷ 1e6 で必ず換算する。
  `ChangeIn*` は scale="-2"（XBRL上は比率）なので、表示値がそのまま%。
  マイナスは文字ではなく sign="-" 属性で表される。△や－は数値でない（前期赤字で比較不能）。

■ 文章は作らない
  判定は機械的な4種（増収増益/増収減益/減収増益/減収減益）と修正方向・進捗率だけ。
  「好決算」「失望」などの評価語は出力に入れない（表示側が判定語を組む）。

■ 訂正短信
  表題に「訂正」を含む回は flag に残し、同じ 銘柄×期×四半期 は**後から出たものを優先**する。

使い方:
  python -X utf8 kessan_flash.py                # tdnet_list.json にある全日
  python -X utf8 kessan_flash.py 2026-09-14     # 日付を絞る（検証用）
"""
import io
import json
import re
import sys
import time
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))
HERE = Path(__file__).parent
LIST = HERE / "docs" / "tdnet_list.json"
OUT = HERE / "docs" / "kessan_flash.json"
HIST = HERE / "docs" / "kessan_flash_hist"

UA = {"User-Agent": "Mozilla/5.0 (compatible; kaburadar.jp/1.0; +https://kaburadar.jp)"}
KEEP_DAYS = 60
SLEEP = 1.0

TAG = re.compile(r'<ix:(nonFraction|nonNumeric)\s([^>]*?)>([\s\S]*?)</ix:\1>')
ATTR = re.compile(r'(\w[\w:-]*)="([^"]*)"')
NUM = re.compile(r"-?[\d,]+(?:\.\d+)?")


# ---------------------------------------------------------------- XBRL
def facts(zip_bytes):
    """サマリーのixbrl → {(要素名, 文脈): {text, shown, scale}} と様式の種別"""
    z = zipfile.ZipFile(io.BytesIO(zip_bytes))
    sm = [n for n in z.namelist() if "/Summary/" in n and n.endswith("-ixbrl.htm")]
    if not sm:
        raise ValueError("SummaryのixbrlがZIPに無い")
    html = z.read(sm[0]).decode("utf-8", "replace")
    # 例: tse-qcedjpsm-63090-... → kind="qcedjpsm"
    kind = sm[0].split("/")[-1].split("-")[1]
    out = {}
    for m in TAG.finditer(html):
        a = dict(ATTR.findall(m.group(2)))
        name = a.get("name", "").split(":")[-1]
        ctx = a.get("contextRef", "")
        text = re.sub(r"<[^>]+>", "", m.group(3)).strip()
        shown = None
        if NUM.fullmatch(text or ""):
            shown = float(text.replace(",", ""))
            if a.get("sign") == "-":
                shown = -shown
        scale = a.get("scale", "")
        f = {"text": text, "shown": shown,
             "scale": int(scale) if re.fullmatch(r"-?\d+", scale) else None}
        # 同じ(要素, 文脈)が2回出ることがある。数値のタグと、注記用の
        # nonNumeric（「―△380」のような比較不能の表示）が同居する短信があり、
        # 後から来たほうで上書きすると数値が消える。数値のほうを残す
        old_f = out.get((name, ctx))
        if old_f is not None:
            if old_f["shown"] is not None and f["shown"] is None:
                continue
            if old_f["shown"] is None and f["shown"] is None and old_f["text"] and not text:
                continue
        out[(name, ctx)] = f
    return out, kind, html


def mil(f):
    """金額のfact → 百万円。実額 = 表示値 × 10^scale"""
    if not f or f["shown"] is None:
        return None
    sc = 6 if f["scale"] is None else f["scale"]
    return round(f["shown"] * (10 ** sc) / 1e6, 1)


def pct(f):
    """ChangeIn* のfact → %（表示値がそのまま%）"""
    return None if (not f or f["shown"] is None) else round(f["shown"], 1)


def txt(f):
    return (f or {}).get("text") or None


def yoy(change_f, cur_f=None, prior_f=None):
    """前年同期比%。**短信が公表した ChangeIn* だけを使う**。

    当期と前期の実額から計算で補うことはしない。2026-09-14の多摩川HD(6838)は
    純利益163→1,950で計算すれば+1096%になるが、短信は「-」と書いている
    （基準変更などで比較に意味が無いと会社が判断した欄）。公表されていない率を
    こちらで作ると、短信と食い違う数字を出すことになる。"""
    return pct(change_f)


# 基準ごとの要素名。接尾辞だけ違うので表にする
STD_NAME = {"": "日本基準", "IFRS": "IFRS", "US": "米国基準"}
SUFFIX = {"jp": "", "if": "IFRS", "us": "US"}
SALES = {"": "NetSales", "IFRS": "SalesIFRS", "US": "NetSalesUS"}
OP = {"": "OperatingIncome", "IFRS": "OperatingIncomeIFRS", "US": "OperatingIncomeUS"}
ORD = {"": "OrdinaryIncome", "IFRS": "ProfitBeforeTaxIFRS", "US": "IncomeBeforeIncomeTaxesUS"}
NP_CONS = {"": "ProfitAttributableToOwnersOfParent",
           "IFRS": "ProfitAttributableToOwnersOfParentIFRS", "US": "NetIncomeUS"}
NP_SOLO = {"": "NetIncome", "IFRS": "ProfitIFRS", "US": "NetIncomeUS"}
QMAP = {1: "1Q", 2: "2Q", 3: "3Q"}


def _cells(row_html):
    return [c for c in (re.sub(r"<[^>]+>|&#160;|\s+", "", x)
                        for x in re.split(r"</t[dh]>", row_html)) if c]


def _num(cell):
    """「22,500」→22500 /「△52」→-52 /「―」「－」→None /「4,000～4,200」→None(レンジ)"""
    c = cell.replace("▲", "△")
    if "～" in c or "〜" in c:
        return None
    neg = c.startswith("△") or c.startswith("-") and len(c) > 1
    c = c.lstrip("△-")
    if not re.fullmatch(r"[\d,]+(?:\.\d+)?", c):
        return None
    v = float(c.replace(",", ""))
    return -v if neg else v


def forecast_from_text(html):
    """予想を数値タグに入れず、表を PreambleToForecasts の文字で書いている短信から
    「通期」の行を読む（2026-09-14 の80社中42社がこの形だった）。

    表は3行で決まった形をしている（実物で確認）:
      見出し: 売上高 / 営業利益 / 経常利益 / 親会社株主に帰属する当期純利益 / 1株当たり当期純利益
      単位  : 百万円 ％ 百万円 ％ … 円銭      ← 金額1列が「値・%」の2セル
      通期  : 22,500 3.1 700 △38.4 750 △36.7 460 △51.8 46.07
    単位行の「百万円/千円」のセル位置に、見出しを順に当てはめて読む。
    形が崩れていたら（セル数が合わない等）何も返さない＝推測で当てはめない。"""
    m = re.search(r'<ix:nonNumeric[^>]*name="[^"]*PreambleToForecasts"[^>]*>([\s\S]*?)</ix:nonNumeric>',
                  html)
    if not m:
        return {}
    rows = [_cells(r) for r in re.split(r"</tr>", m.group(1))]
    rows = [r for r in rows if r]
    unit_i = next((i for i, r in enumerate(rows)
                   if r and r[0] in ("百万円", "千円") and "％" in r), None)
    if unit_i is None or unit_i == 0:
        return {}
    heads, units = rows[unit_i - 1], rows[unit_i]
    data = next((r for r in rows[unit_i + 1:] if r and r[0] == "通期"), None)
    if not data or len(data) - 1 != len(units):
        return {}                      # セル数が合わない＝読み違える恐れがあるので読まない
    vals = data[1:]
    out, hi = {}, 0
    for i, u in enumerate(units):
        if u not in ("百万円", "千円"):
            continue
        if hi >= len(heads):
            return {}
        h, v = heads[hi], _num(vals[i])
        hi += 1
        if v is not None and u == "千円":
            v = round(v / 1000, 1)
        if re.search(r"売上|営業収益|^収益", h) and "fc_sales" not in out:
            out["fc_sales"] = v
        elif "営業利益" in h or "営業損益" in h:
            out["fc_op"] = v
        elif re.search(r"親会社|当期純利益|当期利益", h) and "１株" not in h and "1株" not in h:
            out["fc_np"] = v           # 親会社帰属が後ろにあれば上書きされる
    if any("～" in c or "〜" in c for c in vals):
        out["fc_range"] = True         # レンジ予想。1つの数字にしない
    # 修正有無のチェック欄も同じ塊の中に文字で入っている
    # 「（注）直近に公表されている業績予想からの修正の有無：無」
    flag = re.search(r"修正の有無[:：]\s*(有|無)", re.sub(r"<[^>]+>|&#160;", "", m.group(1)))
    if flag:
        out["fc_rev_flag"] = flag.group(1)
    return out


FY_IN_TITLE = re.compile(r"(\d{4})年\s*(\d{1,2})月期")


def fy_from_title(title):
    """「2027年４月期 第１四半期決算短信」→ 2027/04。
    XBRLに FiscalYearEnd を入れていない様式があるので、TDnetの表題から拾う
    （表題はTDnetが公表している文字列。推測ではない）"""
    import unicodedata
    m = FY_IN_TITLE.search(unicodedata.normalize("NFKC", title or ""))
    return f"{m.group(1)}/{int(m.group(2)):02d}" if m else None


def extract(zip_bytes):
    """XBRL → 企画書§4の契約に沿ったレコード（取れない項目は None のまま）"""
    fs, kind, html = facts(zip_bytes)

    if kind[2:4] == "re":              # anrejpsm = REIT。要素体系が別なので数値は扱わない
        return {"skip": "REIT短信（要素体系が別）"}
    std = SUFFIX.get(kind[4:6], "")    # jp / if / us
    cons = any("_ConsolidatedMember_ResultMember" in k[1] and k[1].startswith("Current")
               for k in fs)
    mem = "ConsolidatedMember" if cons else "NonConsolidatedMember"

    # 四半期番号。本決算短信には QuarterlyPeriod が無い
    qn = None
    for (name, _), v in fs.items():
        if name == "QuarterlyPeriod" and v["shown"] is not None:
            qn = int(v["shown"])
            break
    acc = f"AccumulatedQ{qn}" if qn else "Year"
    cur = f"Current{acc}Duration_{mem}_ResultMember"
    pri = f"Prior{acc}Duration_{mem}_ResultMember"
    # 予想の文脈: 四半期短信は当期通期、本決算短信は来期の新規予想
    fcc = (f"CurrentYearDuration_{mem}_ForecastMember" if qn
           else f"NextYearDuration_{mem}_ForecastMember")

    np_key = NP_CONS[std] if cons else NP_SOLO[std]

    def f(name, ctx):
        return fs.get((name, ctx))

    rec = {
        "q": QMAP.get(qn, "本決算"),
        "cons": cons,
        "std": STD_NAME[std],
        "sales": mil(f(SALES[std], cur)),
        "op": mil(f(OP[std], cur)),
        "ord": mil(f(ORD[std], cur)),
        "np": mil(f(np_key, cur)),
        "sales_yoy": yoy(f("ChangeIn" + SALES[std], cur), f(SALES[std], cur), f(SALES[std], pri)),
        "op_yoy": yoy(f("ChangeIn" + OP[std], cur), f(OP[std], cur), f(OP[std], pri)),
        "np_yoy": yoy(f("ChangeIn" + np_key, cur), f(np_key, cur), f(np_key, pri)),
        "fc_sales": mil(f(SALES[std], fcc)),
        "fc_op": mil(f(OP[std], fcc)),
        "fc_np": mil(f(np_key, fcc)),
    }
    # 会社が出した期末日から「2027/03」を作る（実績の期）
    fy = txt(f("FiscalYearEnd", "CurrentYearInstant"))
    rec["fy"] = f"{fy[:4]}/{fy[5:7]}" if fy and len(fy) >= 7 else None
    # 予想の修正有無（短信のチェック欄）。方向はXBRLに入っていない
    rec["fc_rev_flag"] = (
        txt(f("CorrectionOfConsolidatedFinancialForecastInThisQuarter", fcc))
        or txt(f("CorrectionOfFinancialForecastInThisQuarter", fcc)))
    # 予想を数値タグに入れず、表を丸ごと文字で書いている会社がある
    # （2026-09-14 山王 3441:「通期15,600 0.4 800 △39.2 …」が PreambleToForecasts の中）。
    # 数値は取らないが、「予想なし」と誤って書かないために印だけ付ける
    if rec["fc_sales"] is None and rec["fc_op"] is None and rec["fc_np"] is None:
        t = forecast_from_text(html)
        if t:
            rec.update({k: t.get(k) for k in ("fc_sales", "fc_op", "fc_np")})
            rec["fc_src"] = "text"       # 数値タグでなく、短信内の表の文字から読んだ
            if not rec.get("fc_rev_flag") and t.get("fc_rev_flag"):
                rec["fc_rev_flag"] = t["fc_rev_flag"]
            if t.get("fc_range"):
                rec["fc_range"] = True
    return rec


# ---------------------------------------------------------------- 判定
def verdict(sales_yoy, op_yoy):
    """増収増益/増収減益/減収増益/減収減益。前年が赤字などで比較できなければ None"""
    if sales_yoy is None or op_yoy is None:
        return None
    return ("増収" if sales_yoy >= 0 else "減収") + ("増益" if op_yoy >= 0 else "減益")


def fc_rev(rec, prev_fc_op):
    """上方/下方/据置/新規/なし。

    短信のXBRLには「前回予想」が入っていないので、方向は自分の履歴と比べて決める。
    修正有なのに前回予想が手元に無い回は、嘘を書かず None にする（表示側で出さない）。"""
    has_fc = rec.get("fc_op") is not None or rec.get("fc_sales") is not None
    if not has_fc and rec.get("fc_range"):
        return None                     # レンジ予想。1つの数字が無いので方向も出さない
    if rec["q"] == "本決算":
        return "新規" if has_fc else "なし"
    if not has_fc:
        return "なし"
    if rec.get("fc_rev_flag") == "無":
        return "据置"
    if rec.get("fc_rev_flag") == "有":
        if prev_fc_op is None or rec.get("fc_op") is None:
            return None                 # 方向が分からない。推測で埋めない
        if rec["fc_op"] > prev_fc_op:
            return "上方"
        if rec["fc_op"] < prev_fc_op:
            return "下方"
        return "据置"
    return None


def progress(rec):
    """営業利益の通期予想に対する進捗%。本決算・予想なし・赤字予想は対象外"""
    if rec["q"] == "本決算" or rec.get("fc_op") in (None, 0) or rec.get("op") is None:
        return None
    if rec["fc_op"] <= 0:               # 赤字予想に対する進捗率は意味を持たない
        return None
    return round(100.0 * rec["op"] / rec["fc_op"], 1)


# ---------------------------------------------------------------- 表題の分類
def kind_of(title):
    """TDnetの表題 → "new"（新しい決算短信）/ "fix"（訂正短信）/ None（短信ではない）

    「決算短信」を含む表題には、短信そのもの以外のお知らせも混ざる（実物で確認）:
      「過年度の決算短信の訂正に関するお知らせ」     … 短信ではない
      「(訂正・数値データ訂正)「2024年12月期 第3四半期決算短信…」の一部訂正に関するお知らせ」
                                                    … 過去の期の訂正短信（XBRLは訂正後の数字）
    訂正短信を「今日の決算」に混ぜると、2年前の四半期が今日の一覧に出てしまう。"""
    if "決算短信" not in title:
        return None
    if "訂正" in title:
        return "fix"
    if "お知らせ" in title:
        return None
    return "new"


# ---------------------------------------------------------------- 本体
def fetch(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=90) as r:
        return r.read()


def load_hist():
    """月別履歴を読み、{(code, fy): 最後に見た通期営業利益予想} と月別の中身を返す"""
    prev_fc, by_month = {}, {}
    if HIST.exists():
        for p in sorted(HIST.glob("*.json")):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except Exception as e:
                print(f"  履歴が読めない {p.name}: {e}", file=sys.stderr)
                continue
            by_month[p.stem] = d
            for day in d.get("days", []):
                for it in day.get("items", []):
                    if it.get("fc_op") is not None and it.get("fy"):
                        prev_fc[(it["c"], it["fy"])] = it["fc_op"]
    return prev_fc, by_month


def main():
    args = [a for a in sys.argv[1:] if re.fullmatch(r"\d{4}-\d{2}-\d{2}", a)]
    try:
        src = json.loads(LIST.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"docs/tdnet_list.json が読めない（先に tdnet_list.py を走らせる）: {e}",
              file=sys.stderr)
        return 1

    prev_fc, by_month = load_hist()
    # 既に数字を取った短信は読み直さない（TDnetへのアクセスを減らす）
    done = {}
    for d in by_month.values():
        for day in d.get("days", []):
            for it in day.get("items", []):
                if it.get("id"):
                    done[it["id"]] = it

    days = [d for d in src.get("days", []) if not args or d["d"] in args]
    stat = {"対象": 0, "新規取得": 0, "再利用": 0, "XBRL無し": 0, "REIT": 0, "失敗": 0,
            "訂正で上書き": 0, "訂正の元が手元に無い": 0}
    fixes = []                       # 訂正短信は最後にまとめて当てる
    fails = []
    out_days = {}
    for day in sorted(days, key=lambda x: x["d"]):      # 古い順＝履歴の前回予想が育つ
        ks = [x for x in day["items"] if kind_of(x["title"]) == "new"]
        fixes += [(day["d"], x) for x in day["items"] if kind_of(x["title"]) == "fix"]
        items = {}
        for x in ks:
            stat["対象"] += 1
            if x["id"] in done:
                rec = dict(done[x["id"]])
                stat["再利用"] += 1
            elif not x["xbrl_url"]:
                stat["XBRL無し"] += 1
                fails.append((x["code"][:4], x["name"], "XBRLが添付されていない"))
                continue
            else:
                try:
                    r = extract(fetch(x["xbrl_url"]))
                    time.sleep(SLEEP)
                except Exception as e:
                    stat["失敗"] += 1
                    fails.append((x["code"][:4], x["name"], str(e)[:60]))
                    continue
                if r.get("skip"):
                    stat["REIT"] += 1
                    fails.append((x["code"][:4], x["name"], r["skip"]))
                    continue
                stat["新規取得"] += 1
                rec = {"c": x["code"][:4], "n": x["name"], "t": x["time"], **r,
                       "pdf": x["pdf_url"], "id": x["id"]}
                if not rec.get("fy"):
                    rec["fy"] = fy_from_title(x["title"])
            rec["verdict"] = verdict(rec.get("sales_yoy"), rec.get("op_yoy"))
            rec["fc_rev"] = fc_rev(rec, prev_fc.get((rec["c"], rec.get("fy"))))
            rec["progress_op"] = progress(rec)
            rec.pop("skip", None)
            # 同じ銘柄×期×四半期は後から出たもの（訂正）を優先
            items[(rec["c"], rec.get("fy"), rec["q"])] = rec
            if rec.get("fc_op") is not None and rec.get("fy"):
                prev_fc[(rec["c"], rec["fy"])] = rec["fc_op"]
        if items:
            rows = sorted(items.values(), key=lambda r: (r["t"], r["c"]), reverse=True)
            out_days[day["d"]] = {"d": day["d"], "count": len(rows), "items": rows}

    # ---- 訂正短信: 手元にある同じ 銘柄×期×四半期 の数字を訂正後で置き換える。
    #      元の決算が手元に無い（TDnetの保持期間より前など）なら何もしない。
    #      訂正は「今日の決算」として一覧に足さない
    index = {}
    for src_days in (out_days, *[{x["d"]: x for x in m.get("days", [])} for m in by_month.values()]):
        for dd, day in src_days.items():
            for i, it in enumerate(day["items"]):
                index.setdefault((it["c"], it.get("fy"), it["q"]), (dd, i))
    for dfix, x in fixes:
        if not x["xbrl_url"] or x["id"] in done:
            continue
        try:
            r = extract(fetch(x["xbrl_url"]))
            time.sleep(SLEEP)
        except Exception as e:
            fails.append((x["code"][:4], x["name"], "訂正短信: " + str(e)[:50]))
            continue
        if r.get("skip"):
            continue
        if not r.get("fy"):
            r["fy"] = fy_from_title(x["title"])
        key = (x["code"][:4], r.get("fy"), r["q"])
        if key not in index:
            stat["訂正の元が手元に無い"] += 1
            continue
        dd, i = index[key]
        holder = out_days.get(dd)
        if holder is None:
            holder = next(day for m in by_month.values() for day in m.get("days", [])
                          if day["d"] == dd)
            out_days[dd] = holder          # 履歴の日を書き戻す対象にする
        base = holder["items"][i]
        base.update({k: v for k, v in r.items() if k != "skip"})
        base["verdict"] = verdict(base.get("sales_yoy"), base.get("op_yoy"))
        base["fc_rev"] = fc_rev(base, None) if base.get("fc_rev") is None else base["fc_rev"]
        base["progress_op"] = progress(base)
        base["flag"] = "訂正"
        base["fix_date"] = dfix
        base["fix_pdf"] = x["pdf_url"]
        stat["訂正で上書き"] += 1

    # ---- 月別履歴（追記のみ。TDnetは約1か月で消えるので、ここが資産になる）
    HIST.mkdir(parents=True, exist_ok=True)
    for d, day in out_days.items():
        mon = d[:7]
        cur = by_month.get(mon) or {"month": mon, "days": []}
        kept = {x["d"]: x for x in cur.get("days", [])}
        kept[d] = day
        cur["days"] = sorted(kept.values(), key=lambda x: x["d"])
        cur["updated"] = datetime.now(JST).isoformat(timespec="seconds")
        cur["count"] = sum(x["count"] for x in cur["days"])
        by_month[mon] = cur
        (HIST / f"{mon}.json").write_text(
            json.dumps(cur, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    # ---- 直近60日（履歴から組み直すので、過去分の訂正も反映される）
    alld = {}
    for d in by_month.values():
        for day in d.get("days", []):
            alld[day["d"]] = day
    lo = (datetime.now(JST) - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    recent = [alld[k] for k in sorted(alld) if k >= lo]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "updated": datetime.now(JST).isoformat(timespec="seconds"),
        "source": "TDnetの決算短信（XBRLサマリー）。PDF本体は再配布せずURLだけ持つ",
        "note": "金額は百万円。前年同期比は短信が公表した値をそのまま使っている。"
                "verdict は売上と営業利益の前年同期比の符号だけで機械的に決めた4種。"
                "fc_rev の方向は自分の履歴の前回予想と比べたもので、"
                "修正有でも前回予想が手元に無い回は null にしている",
        "keep_days": KEEP_DAYS,
        "count": sum(x["count"] for x in recent),
        "days": recent,
    }, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    print("  " + " / ".join(f"{k}={v}" for k, v in stat.items()))
    for c, n, why in fails[:20]:
        print(f"    取れず {c} {n[:14]}: {why}")
    print(f"→ docs/kessan_flash.json {len(recent)}日 {sum(x['count'] for x in recent)}件 "
          f"({OUT.stat().st_size / 1024:.0f}KB) / 履歴 {len(by_month)}か月")
    return 0


if __name__ == "__main__":
    sys.exit(main())
