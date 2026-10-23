# -*- coding: utf-8 -*-
"""決算短信の「配当の状況」を記録する → docs/dividend_hist/YYYY-MM.jsonl（追記のみ・表示はまだしない）

■ なぜ今から記録するか（2026-10-24 起動文）
  TDnet は開示から31日しか置かない。過去の短信にはさかのぼれないので、配当のページを作る判断
  （11月中旬以降）より先に、**記録だけ今日から始める**。読むのは kessan_flash.py が既に落としている
  XBRL の zip と同じもの（追加のアクセスは無い）。

■ 要素（名前空間 tse-ed-t:。日本基準・IFRS とも同じ名前。_調査_配当データの出所.md §1-(a)-2）
  DividendPerShare                         1株配当（円・scale=0）
  PayoutRatio                              配当性向（%・本決算のみ）
  CorrectionOfDividendForecastInThisQuarter 配当予想の修正の有無（有/無）
  DividendPayableDateAsPlanned             配当支払開始予定日
  NoteToDividends                          注記（特別配当・記念配当の内訳など）
  FiscalYearEnd                            決算期末
  文脈: {Prior|Current|Next}YearDuration_{FirstQuarter|SecondQuarter|ThirdQuarter|YearEnd|Annual}Member
        _NonConsolidatedMember_{Result|Forecast|Lower|Upper}Member

■ 1行の中身（1短信1行。値の無い欄は null。推測で埋めない）
  per                 時期ごとの1株配当 {"prior_result": {"q1","q2","q3","ye","annual"}, "current_result": …,
                      "current_forecast": …, "next_forecast": …}。レンジ予想は "*_lower"/"*_upper"
  dps_prior           前期の年間配当（実績）
  dps_actual          当期の年間配当（実績）。本決算短信だけ
  dps_fc / dps_fc_fy  年間配当の会社予想と、それがどの期の予想か（四半期短信＝当期、本決算＝来期）
  dps_fc_null         dps_fc が null の理由。"予想未定"（短信に「未定」と書いてある）/ "欄なし"
  無配は 0 がタグ付けされているので 0 のまま記録する（「取れない」と区別できる）
"""
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kessan_flash import NF_OPEN, ATTR, facts_html, nn_text_of

JST = timezone(timedelta(hours=9))
HERE = Path(__file__).parent
DIR = HERE / "docs" / "dividend_hist"
SRC = "TDnet 決算短信のXBRLサマリー（配当の状況）"

CTX = re.compile(r"(Prior|Current|Next)YearDuration_(FirstQuarter|SecondQuarter|ThirdQuarter|YearEnd|Annual)"
                 r"Member_NonConsolidatedMember_(Result|Forecast|Lower|Upper)Member$")
SLOT = {"FirstQuarter": "q1", "SecondQuarter": "q2", "ThirdQuarter": "q3", "YearEnd": "ye", "Annual": "annual"}


def _clean(s, n=None):
    s = re.sub(r"\s+", " ", re.sub(r"<[^>]+>|&#160;|&nbsp;", " ", s or "")).strip()
    return (s[:n] if n else s) or None


def _first_text(fs, name):
    """要素名が一致する nonNumeric の文字（文脈は会社で違うので問わない）。最初の空でないもの"""
    for (nm, _ctx), f in fs.items():
        if nm == name and f.get("text"):
            return f["text"]
    return None


def _undetermined(html):
    """配当予想の欄に「未定」と書かれているか（数値タグは空欄の自己終了で、すぐ後ろに文字で「未定」）"""
    for m in NF_OPEN.finditer(html):
        a = dict(ATTR.findall(m.group(1)))
        if not a.get("name", "").endswith("DividendPerShare"):
            continue
        if not re.search(r"(Forecast|Lower|Upper)Member$", a.get("contextRef", "")):
            continue
        cell = html[m.end():m.end() + 400].split("</td>")[0]
        if "未定" in re.sub(r"<[^>]+>", "", cell):
            return True
    note = nn_text_of(html, "NoteToDividends") or ""
    return "未定" in re.sub(r"<[^>]+>", "", note)


def extract_div(html, kind, q=None):
    """サマリーのixbrl → 配当の記録（REIT は None）。q は kessan_flash の四半期（"1Q"…"本決算"）"""
    if kind[2:4] == "re":
        return None
    fs = facts_html(html)
    per = {}
    for (nm, ctx), f in fs.items():
        if nm != "DividendPerShare" or f.get("shown") is None:
            continue
        m = CTX.match(ctx)
        if not m:
            continue
        when, slot, typ = m.groups()
        key = f"{when.lower()}_{'result' if typ == 'Result' else 'forecast' if typ == 'Forecast' else typ.lower()}"
        sc = f["scale"] or 0
        per.setdefault(key, {})[SLOT[slot]] = round(f["shown"] * (10 ** sc), 4)

    def ann(key):
        return (per.get(key) or {}).get("annual")

    # 本決算短信は「当期の実績＋来期の予想」、四半期短信は「当期の予想」
    annual_q = (q == "本決算") if q else ("next_forecast" in per)
    rec = {
        "per": per,
        "dps_prior": ann("prior_result"),
        "dps_actual": ann("current_result") if annual_q else None,
    }
    fc_key = "next_forecast" if annual_q else "current_forecast"
    rec["dps_fc"] = ann(fc_key)
    rec["dps_fc_fy"] = "来期" if annual_q else "当期"
    if rec["dps_fc"] is None:
        lo, hi = ann(fc_key.replace("forecast", "lower")), ann(fc_key.replace("forecast", "upper"))
        if lo is not None or hi is not None:
            rec["dps_fc_null"] = "レンジ予想"
        elif _undetermined(html):
            rec["dps_fc_null"] = "予想未定"
        else:
            rec["dps_fc_null"] = "欄なし"
    if rec["dps_prior"] is None:
        rec["dps_prior_null"] = "欄なし"

    payout = {}
    for (nm, ctx), f in fs.items():
        if nm == "PayoutRatio" and f.get("shown") is not None:
            w = re.match(r"(Prior|Current|Next)", ctx)
            if w:
                payout.setdefault(w.group(1).lower(), round(f["shown"], 1))
    rec["payout"] = payout or None
    rec["rev"] = _first_text(fs, "CorrectionOfDividendForecastInThisQuarter")
    rec["pay_date"] = _first_text(fs, "DividendPayableDateAsPlanned")
    fye = None
    for (nm, ctx), f in fs.items():
        if nm == "FiscalYearEnd" and ctx == "CurrentYearInstant" and f.get("text"):
            fye = f["text"]
    rec["fye"] = fye
    note = _clean(nn_text_of(html, "NoteToDividends"))
    rec["note"] = note[:400] if note else None
    rec["special"] = bool(note and re.search(r"特別配当|記念配当", note))
    return rec


# ---------------------------------------------------------------- 書き込み（追記のみ）
def _month_file(d):
    return DIR / f"{d[:7]}.jsonl"


def known_ids():
    ids = set()
    if DIR.exists():
        for p in DIR.glob("*.jsonl"):
            for line in p.read_text(encoding="utf-8").splitlines():
                try:
                    ids.add(json.loads(line)["id"])
                except Exception:
                    continue
    return ids


def append(rows):
    """rows: [{"d": 開示日, "id": 文書ID, ...}]。既にある id は書かない。書いた件数を返す"""
    DIR.mkdir(parents=True, exist_ok=True)
    have = known_ids()
    n = 0
    for r in sorted(rows, key=lambda x: (x["d"], x.get("t") or "", x["id"])):
        if r["id"] in have:
            continue
        with _month_file(r["d"]).open("a", encoding="utf-8", newline="\n") as fp:
            fp.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")
        have.add(r["id"])
        n += 1
    write_index()
    return n


def write_index():
    """監視用（data_targets.py）。updated は決算速報が走るたびに進む"""
    DIR.mkdir(parents=True, exist_ok=True)
    months, total, codes, fc = {}, 0, set(), 0
    for p in sorted(DIR.glob("*.jsonl")):
        k = 0
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except Exception:
                continue
            k += 1
            codes.add(r.get("c"))
            fc += r.get("dps_fc") is not None
        months[p.stem] = k
        total += k
    (DIR / "index.json").write_text(json.dumps({
        "updated": datetime.now(JST).isoformat(timespec="seconds"),
        "source": SRC, "since": min(months) if months else None,
        "rows": total, "stocks": len(codes), "with_forecast": fc, "months": months,
        "note": "記録だけ（サイトには未掲載）。1短信1行・追記のみ。無配は0、値の無い欄は null と理由",
    }, ensure_ascii=False, indent=1), encoding="utf-8")


def row(item, day, flash_rec, div, flag=None):
    """kessan_flash の一覧の1件 → 1行"""
    r = {"c": item["code"][:4], "n": item["name"], "d": day, "t": item.get("time"),
         "id": item["id"], "title": item.get("title"), "q": (flash_rec or {}).get("q"),
         "fy": (flash_rec or {}).get("fy"), **div, "src": SRC, "pdf": item.get("pdf_url")}
    if flag:
        r["flag"] = flag
    return r
