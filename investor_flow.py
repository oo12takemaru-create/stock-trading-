"""JPX投資部門別売買状況(週間・東証プライム・金額) → docs/investor_flow.json (株レーダー用)

JPX公式の週間Excelから、海外投資家・個人・信託銀行などの売買代金と
ネット(買い越し/売り越し)を億円単位で抽出する。毎週木曜15時ごろ更新→木曜夕方に実行。

使い方: python investor_flow.py docs/investor_flow.json
依存: pandas, xlrd（旧形式）, openpyxl（新形式）

■ 2026年9月第3週（9/14〜）からJPXのファイル形式が変わった
  旧: stock_val_1_YYMMWW.xls（シート "TSE Prime"、主体が行）
  新: stock_1_w_YYYYMMDD_YYYYMMDD.xlsx（1シート・横持ち）
  新形式を優先して読む。新形式の読み方は investor_hist.parse_new() に1本化してある。
  旧形式のリンクしか無い場合に備えて、旧形式の読み方（parse_file）も残す。
  旧形式も investor_hist.parse_old_df() を使う（「前週｜今週」の2ブロックのうち今週を読む。
  以前の first_number() は左の前週ブロックを拾っていた → 2026-10-23 修正）。
"""
import json
import re
import sys
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from investor_hist import (NEW_FILE_RE, is_new_format, new_week_meta, parse_new,  # noqa: E402
                           parse_old_df, read_old_sheet)

JST = timezone(timedelta(hours=9))
INDEX_URL = "https://www.jpx.co.jp/markets/statistics-equities/investor-type/index.html"
BASE = "https://www.jpx.co.jp"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# (Excel上のラベル前方一致, key, 表示名)
CATEGORIES = [
    ("海外投資家", "foreigners",  "海外投資家"),
    ("個",         "individuals", "個人投資家"),
    ("信託銀行",   "trust_banks", "信託銀行（年金など）"),
    ("投資信託",   "inv_trusts",  "投資信託"),
    ("事業法人",   "business",    "事業法人（自社株買いなど）"),
    ("自己",       "proprietary", "証券自己（プロップ）"),
]


def fetch(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def find_latest_files():
    """最新週と前週のURL。新形式（stock_1_w_開始日_終了日.xlsx）を優先し、
    開始日の新しい順に並べる。新形式が2本に満たなければ旧形式で埋める"""
    html = fetch(INDEX_URL).decode("utf-8", "replace")
    new = {}
    for m in re.finditer(r'href="([^"]*' + NEW_FILE_RE.pattern + ')"', html):
        new.setdefault(m.group(2), BASE + m.group(1))
    links = [new[k] for k in sorted(new, reverse=True)]
    old = re.findall(r'href="([^"]*stock_val_1_\d+\.xls)"', html)
    links += [BASE + l for l in old]
    if not links:
        raise RuntimeError("投資部門別売買状況のファイル（stock_1_w_*.xlsx / stock_val_1_*.xls）が見つからない")
    return links[:2]  # 最新週と前週


def _items(flows):
    """{key: {"sell","buy"}}(千円) → 画面用の item（億円）"""
    disp = {key: d for _, key, d in CATEGORIES}
    return {key: {"key": key, "label": disp[key],
                  "sell_oku": round(f["sell"] / 100000),
                  "buy_oku": round(f["buy"] / 100000),
                  "net_oku": round((f["buy"] - f["sell"]) / 100000)}   # 千円 → 億円
            for key, f in flows.items()}


def parse_new_file(url, raw):
    """新形式。戻り値は parse_file と同じ (週ラベル, {key: item}, シート名)"""
    w = parse_new(raw)
    _, _, week_label = new_week_meta(url, w["code"])
    return week_label, _items(w["flows"]), w["sheet"]


def parse_file(url):
    raw = fetch(url)
    if is_new_format(raw):
        return parse_new_file(url, raw)
    # 旧形式: 今週ブロックを読む（ファイル名の週コードとラベルの突き合わせは parse_old_df 側）
    m = re.search(r"stock_val_1_(\d{6})\.xls", url)
    df, sheet = read_old_sheet(raw, "TSE Prime")
    w = parse_old_df(df, m.group(1) if m else None)
    return w["label"], _items(w["flows"]), sheet

def main():
    dst = sys.argv[1] if len(sys.argv) > 1 else "docs/investor_flow.json"
    urls = find_latest_files()
    week_label, latest, sheet = parse_file(urls[0])
    prev = {}
    if len(urls) > 1:
        try:
            _, prev, _ = parse_file(urls[1])
        except Exception as e:
            print(f"前週の取得失敗(継続): {e}", file=sys.stderr)

    items = []
    for _, key, disp in CATEGORIES:
        if key not in latest:
            print(f"カテゴリ欠落: {disp}", file=sys.stderr)
            continue
        it = latest[key]
        it["prev_net_oku"] = prev.get(key, {}).get("net_oku")
        items.append(it)

    if len(items) < 4:
        print("抽出カテゴリが少なすぎるため中止", file=sys.stderr)
        sys.exit(1)

    out = {
        "updated": datetime.now(JST).isoformat(timespec="seconds"),
        "week": week_label,
        "market": "東証プライム(金額・委託と自己の合計)",
        "unit": "億円",
        "source": "JPX 投資部門別売買状況(週間)",
        "source_file": urls[0],
        # 市場区分の判定に使う。日付ではなくシート名で決める（investor_hist.market_of）
        # 新形式はシートが1枚だけなので、市場名の列で東証プライムを選んで "TSE Prime" を入れている
        "sheet": sheet,
        "items": items,
    }
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"investor_flow.json 生成: {week_label}")
    for it in items:
        print(f"  {it['label']}: net {it['net_oku']:+,}億円")


if __name__ == "__main__":
    main()
