# -*- coding: utf-8 -*-
"""信用取引の規制（日々公表銘柄・特別周知銘柄・規制措置）の毎日の保存 → data/kisei/YYYY-MM.jsonl（2026-11-20）

■ なぜ
  JPX の「日々公表銘柄」「信用取引に関する規制等（増担保など）」のページは、今の一覧と直近の解除しか載らず、
  過去の記録が残らない。毎営業日そのまま保存して、始めた日から履歴を貯める（画面はまだ作らない）。
  前日との差分から「新規指定・規制の強化・緩和・解除」の出来事を ledger2_detect.py（kisei_*）が作る。

■ 取得元（3ページ・1回の実行で各1回だけ・間に2秒あける）
  nichinichi … https://www.jpx.co.jp/markets/equities/margin-daily/index.html
                「日々公表銘柄」（銘柄名・コード・指定日）と「日々公表銘柄を解除された銘柄」（銘柄名・コード・解除日）
  tokubetsu  … https://www.jpx.co.jp/markets/equities/margin-daily/01.html
                「特別周知銘柄」（銘柄名・コード・周知開始日）
  kisei      … https://www.jpx.co.jp/markets/equities/margin-reg/index.html
                「信用取引に関する規制を行っている銘柄」（銘柄名・コード・実施日・規制の内容・該当基準）と
                「信用取引に関する規制を解除された銘柄」（銘柄名・コード・解除日・規制の内容。解除後1週間程度載る）
  既存の収集（shinyo_fetch.py・shinmei_fetch.py）は信用残の統計ページ（statistics-equities/margin）で、
  この3ページは取っていない（2026-11-20 に確認）。取得は1日1回（ledger-daily.yml の中）。
  JPX のサイトのご利用上の注意: 許諾なしの二次利用・再配信は不可、高頻度・高負荷の自動取得は控える。
  → 1日3ページ・2秒間隔。保存は docs/ の外（GitHub Pages に出さない）。一覧そのものを公開ファイルにしない。

■ 保存の形（1営業日＝1行・追記のみ）
  {"date": その営業日, "fetched_at": 取得時刻(JST), "pages": {名前: {"url","updated" ページの更新日}},
   "nichinichi": [{"銘柄名","コード","指定日"}], "nichinichi_released": [{"銘柄名","コード","解除日"}],
   "tokubetsu": [{"銘柄名","コード","周知開始日"}],
   "kisei": [{"銘柄名","コード","実施日","規制の内容","該当基準"}], "kisei_released": [{"銘柄名","コード","解除日","規制の内容"}]}
  列の名前は元の表の見出しのまま。該当なし（表の「-」だけの行）は空の並び。

■ 決まり
  - その営業日: 営業日の 18:00 JST 以降に走ったらその日。それより前・休場日は直前の営業日（その日の夕方の版とみなす）
  - 同じ日の行がもうあれば何もしない（同じ日に2回走っても増えない・後から書き換えない）
  - 3ページのどれか1つでも取れない・表の見出しが変わっていたら、その日は書かない（後から埋めない）

使い方:
  python -X utf8 kisei_fetch.py                    # 毎営業日（ledger-daily.yml）
  python -X utf8 kisei_fetch.py --out <dir>        # 試験（本番の場所に書かない）
  python -X utf8 kisei_fetch.py --html-dir <dir>   # 保存済みの HTML から（試験・取得しない）
"""
import argparse
import html as _html
import json
import re
import sys
import time
import urllib.request
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path

from jp_bizday import is_bizday

JST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "kisei"
BASE = "https://www.jpx.co.jp"
UA = {"User-Agent": "Mozilla/5.0 (compatible; kaburadar.jp/1.0; +https://kaburadar.jp)"}
READY = dtime(18, 0)       # この時刻より前は、その日の版とみなさない
GAP_SEC = 2.0              # ページの間の待ち

# 名前 → (URL, [(保存のキー, 見出しの手がかり, 期待する列)])
PAGES = {
    "nichinichi": ("/markets/equities/margin-daily/index.html", [
        ("nichinichi", "日々公表銘柄", ["銘柄名", "コード", "指定日"]),
        ("nichinichi_released", "日々公表銘柄を解除された銘柄", ["銘柄名", "コード", "解除日"]),
    ]),
    "tokubetsu": ("/markets/equities/margin-daily/01.html", [
        ("tokubetsu", "特別周知銘柄", ["銘柄名", "コード", "周知開始日"]),
    ]),
    "kisei": ("/markets/equities/margin-reg/index.html", [
        ("kisei", "信用取引に関する規制を行っている銘柄", ["銘柄名", "コード", "実施日", "規制の内容", "該当基準"]),
        ("kisei_released", "信用取引に関する規制を解除された銘柄", ["銘柄名", "コード", "解除日", "規制の内容"]),
    ]),
}
KEYS = [k for _, (_, ts) in PAGES.items() for k, _, _ in ts]


def log(*a):
    print(*a, flush=True)


def http(url, tries=3):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=40) as r:
                return r.read().decode("utf-8", errors="replace")
        except Exception as e:
            last = e
            time.sleep(5 * (i + 1))
    raise last


def _text(s):
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", "", s))).strip()


def parse_page(name, html):
    """ページ → ({保存のキー: [行]}, ページの更新日)。見出しや列が想定と違えば ValueError"""
    m = re.search(r"(\d{4})/(\d{2})/(\d{2})\s*更新", html)
    updated = f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None
    # 見出し（h2〜h4）と表を出てくる順に並べる
    parts = []
    for mm in re.finditer(r"<h[2-4][^>]*>(.*?)</h[2-4]>|<table.*?</table>", html, flags=re.S):
        if mm.group(0).startswith("<h"):
            parts.append(("h", _text(mm.group(1))))
        else:
            rows = [[_text(td) for td in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, flags=re.S)]
                    for tr in re.findall(r"<tr.*?</tr>", mm.group(0), flags=re.S)]
            parts.append(("t", rows))
    out = {}
    for key, head, cols in PAGES[name][1]:
        tbl = None
        for i, (kind, v) in enumerate(parts):
            if kind == "h" and v == head:
                tbl = next((w for k2, w in parts[i + 1:] if k2 == "t"), None)
                break
        if tbl is None:
            raise ValueError(f"{name}: 見出し「{head}」の表が見つからない（様式変更の可能性）")
        if not tbl or tbl[0] != cols:
            raise ValueError(f"{name}: 「{head}」の列が想定と違う {tbl[0] if tbl else None} ≠ {cols}")
        rows = []
        for r in tbl[1:]:
            if len(r) != len(cols):
                raise ValueError(f"{name}: 「{head}」の行の列数が違う {r}")
            if all(x in ("-", "－", "") for x in r):        # 該当なし
                continue
            row = dict(zip(cols, r))
            if not re.fullmatch(r"[0-9][0-9A-Z]{3}", row["コード"]):
                raise ValueError(f"{name}: コードの形が違う {row}")
            for k in cols:
                if k.endswith("日") and row[k]:
                    if not re.fullmatch(r"\d{4}/\d{2}/\d{2}", row[k]):
                        raise ValueError(f"{name}: 日付の形が違う {row}")
                    row[k] = row[k].replace("/", "-")
            rows.append(row)
        out[key] = rows
    return out, updated


def record_date(now):
    """その営業日（18:00 以降の営業日はその日・それ以外は直前の営業日）"""
    d = now.date()
    if is_bizday(d) and now.time() >= READY:
        return d.isoformat()
    d -= timedelta(days=1)
    while not is_bizday(d):
        d -= timedelta(days=1)
    return d.isoformat()


def have_date(out, d):
    p = out / f"{d[:7]}.jsonl"
    if not p.exists():
        return False
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            if json.loads(line).get("date") == d:
                return True
        except Exception:
            continue
    return False


def build_record(d, pages_html, fetched_at):
    rec = {"date": d, "fetched_at": fetched_at, "pages": {}}
    for name, (path, _) in PAGES.items():
        got, updated = parse_page(name, pages_html[name])
        rec["pages"][name] = {"url": BASE + path, "updated": updated}
        rec.update(got)
    return rec


def append(out, rec):
    out.mkdir(parents=True, exist_ok=True)
    with open(out / f"{rec['date'][:7]}.jsonl", "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--html-dir", help="保存済みの HTML（nichinichi.html・tokubetsu.html・kisei.html）から作る")
    ap.add_argument("--now", help="今の時刻を差し替える（試験用・ISO 形式・JST）")
    a = ap.parse_args()
    out = Path(a.out)
    now = datetime.fromisoformat(a.now).replace(tzinfo=JST) if a.now else datetime.now(JST)
    d = record_date(now)
    if have_date(out, d):
        log(f"{d} の規制の一覧はもう保存してある。何もしない")
        return 0
    pages = {}
    try:
        for i, (name, (path, _)) in enumerate(PAGES.items()):
            if a.html_dir:
                pages[name] = (Path(a.html_dir) / f"{name}.html").read_text(encoding="utf-8")
                continue
            if i:
                time.sleep(GAP_SEC)
            pages[name] = http(BASE + path)
        rec = build_record(d, pages, now.isoformat(timespec="seconds"))
    except Exception as e:
        log(f"::error::規制の一覧を保存できない（{d} は書かない）: {type(e).__name__}: {e}")
        return 1
    append(out, rec)
    upd = ", ".join(k + "=" + str(v.get("updated")) for k, v in rec["pages"].items())
    log(f"{d} を保存: 日々公表 {len(rec['nichinichi'])}・特別周知 {len(rec['tokubetsu'])}・規制 {len(rec['kisei'])}"
        f"（解除の欄: 日々公表 {len(rec['nichinichi_released'])}・規制 {len(rec['kisei_released'])}）"
        f" ページの更新日 {upd}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
