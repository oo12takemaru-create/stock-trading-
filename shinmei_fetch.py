# -*- coding: utf-8 -*-
"""銘柄別の信用取引週末残高 → docs/shinyo_meigara.json（株レーダー kaburadar.jp）

JPXが毎週公表する「銘柄別信用取引週末残高」(margin/05.html) を取り込む。
市場全体の集計は shinyo_fetch.py（信用取引現在高）、こちらは銘柄別。
スクリーナーの「信用倍率」条件と銘柄カルテの需給表示に使う。

■ 公表形式はPDFのみ（Excel/CSVなし）
  機械生成の定型PDFなので pymupdf でテキスト抽出する。
  抽出の正しさは「一般信用＋制度信用＝合計」が全行で一致することで毎回検証し、
  不一致が1%を超えたら公開せずに失敗させる（壊れたデータを出さない）。

■ 単位は株（PDFの原単位のまま）。信用倍率は表示側で 買残÷売残 を計算する。
■ 5桁コードの末尾が0以外（優先株式など）は除外（4桁コードが普通株式と衝突するため）。
"""
import json
import re
import sys
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

JST = timezone(timedelta(hours=9))
DOCS = Path(__file__).parent / "docs"
OUT = DOCS / "shinyo_meigara.json"

BASE = "https://www.jpx.co.jp"
PAGE = "/markets/statistics-equities/margin/05.html"
UA = {"User-Agent": "Mozilla/5.0 (compatible; kaburadar.jp/1.0)"}

ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}\d$")
NUM_RE = re.compile(r"^(▲\s?)?[\d,]+$")


def http(url, tries=3):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=40 + i * 20) as r:
                return r.read()
        except Exception as e:
            last = e
    raise last


def to_int(tok):
    neg = tok.startswith("▲")
    v = int(tok.replace("▲", "").replace(",", "").strip())
    return -v if neg else v


def main():
    import pymupdf

    html = http(BASE + PAGE).decode("utf-8", "ignore")
    links = re.findall(r'href="(/markets/[^"]+/syumatsu(\d{8})00\.pdf)"', html)
    if not links:
        print("PDFリンクが見つからない", file=sys.stderr)
        sys.exit(1)
    link, ymd = max(links, key=lambda x: x[1])
    asof_file = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}"

    # 同じ申込日のデータを既に公開済みなら何もしない（PDF 850KBの無駄な再取得を避ける）
    try:
        prev = json.loads(OUT.read_text(encoding="utf-8"))
        if prev.get("asof") == asof_file:
            print(f"既に公開済み: {asof_file} → スキップ")
            return
    except Exception:
        pass

    pdf = http(BASE + link)
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    print(f"取得: {link} ({len(pdf)//1024}KB, {len(doc)}p)", file=sys.stderr)

    # 公表日（申込日と別にPDF先頭に載っている）
    head = doc[0].get_text()
    m = re.search(r"(\d{4}/\d{1,2}/\d{1,2})\s*申込み現在", head)
    asof = asof_file
    if m:
        y, mo, d = m.group(1).split("/")
        asof = f"{y}-{int(mo):02d}-{int(d):02d}"
        if asof != asof_file:
            # ファイル名と本文の申込日が食い違ったら本文を信じるが、警告は出す
            print(f"注意: ファイル名{asof_file} と本文{asof} が不一致", file=sys.stderr)

    items, bad, seen = [], 0, set()
    for page in doc:
        lines = [l.strip() for l in page.get_text().splitlines()]
        for i, ln in enumerate(lines):
            if not ISIN_RE.match(ln):
                continue
            code5 = lines[i - 1] if i >= 1 else ""
            if not re.match(r"^[0-9][0-9A-Z]{3}[0-9]$", code5):
                bad += 1
                continue
            # 数値12個（合計売残/前週比/合計買残/前週比/売一般/比/売制度/比/買一般/比/買制度/比）
            nums, j = [], i + 1
            while j < len(lines) and len(nums) < 12:
                t = lines[j]
                if NUM_RE.match(t):
                    nums.append(to_int(t))
                    j += 1
                elif t == "-":
                    nums.append(0)
                    j += 1
                else:
                    break
            if len(nums) < 12:
                bad += 1
                continue
            s, sd, b, bd = nums[0], nums[1], nums[2], nums[3]
            # 抽出の正しさを毎行検算: 一般＋制度＝合計（残高も前週比も）
            if (nums[4] + nums[6] != s or nums[5] + nums[7] != sd
                    or nums[8] + nums[10] != b or nums[9] + nums[11] != bd):
                bad += 1
                continue
            if code5[4] != "0":  # 優先株式などは除外（4桁コードの衝突防止）
                continue
            code = code5[:4]
            if code in seen:
                continue
            seen.add(code)
            items.append({"c": code, "s": s, "sd": sd, "b": b, "bd": bd})

    total = len(items) + bad
    print(f"抽出: {len(items)}銘柄 / 検算不一致・不完全 {bad}", file=sys.stderr)
    if total == 0 or bad / total > 0.01:
        print("検算不一致が1%超 → 公開しない（PDFの様式が変わった可能性）", file=sys.stderr)
        sys.exit(1)
    if len(items) < 2000:
        print(f"銘柄数が少なすぎる: {len(items)}", file=sys.stderr)
        sys.exit(1)

    out = {
        "updated": datetime.now(JST).isoformat(timespec="seconds"),
        "asof": asof,           # 申込日（この週の金曜日）
        "count": len(items),
        "unit": "株",
        "source": "JPX 銘柄別信用取引週末残高",
        "items": items,
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")),
                   encoding="utf-8")
    kb = OUT.stat().st_size // 1024
    r1 = sum(1 for x in items if x["s"] > 0 and x["b"] / x["s"] <= 1)
    print(f"shinyo_meigara.json 生成: {len(items)}銘柄 asof={asof} {kb}KB 倍率1以下={r1}")


if __name__ == "__main__":
    main()
