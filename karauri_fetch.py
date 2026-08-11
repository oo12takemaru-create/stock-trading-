# -*- coding: utf-8 -*-
"""空売り残高トラッカー → docs/karauri.json（株レーダー kaburadar.jp）

JPXが毎営業日公表する「空売り残高に関する情報」(Short_Positions.xls) を集計する。
発行済株式の0.5%以上を空売りしている機関に報告義務があり、その明細が公開されている。

■ 大事な性質: 各日のファイルは「その日に報告された変化分」だけ
  全ポジションのスナップショットではない。だから (銘柄, 機関) ごとの最新値を
  docs/karauri_state.json に蓄積し続けて、銘柄ごとの残高合計を復元する。
  残高割合が0.5%を下回った報告（ratio<0.005）が来たら、そのポジションは解消として消す。

■ 初回はアーカイブ約3ヶ月分をバックフィルして状態を作る
  2回目以降は未処理の日付だけ差分処理（1日1ファイル・約280KB）。

■ 公表は T+2（計算年月日の2営業日後の夕方）。リアルタイムではない。
"""
import json
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone, timedelta
from io import BytesIO
from pathlib import Path

import pandas as pd

JST = timezone(timedelta(hours=9))
DOCS = Path(__file__).parent / "docs"
OUT = DOCS / "karauri.json"
STATE = DOCS / "karauri_state.json"

BASE = "https://www.jpx.co.jp"
INDEX = "/markets/public/short-selling/index.html"
ARCHIVES = ["/markets/public/short-selling/00-archives-%02d.html" % i for i in (1, 2, 3)]
UA = {"User-Agent": "Mozilla/5.0 (compatible; kaburadar.jp/1.0)"}

THRESH = 0.005          # 報告義務の下限 0.5%
MOVE_MIN = 0.0005       # 「動き」とみなす最小変化 0.05pt


def http(url, tries=3):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=40 + i * 20) as r:
                return r.read()
        except Exception as e:
            last = e
            time.sleep(2 + i * 3)
    raise last


def find_links(html):
    """(日付文字列, 絶対URL) のリスト"""
    out = {}
    for m in re.finditer(r'href="(/markets/public/short-selling/[^"]*?(\d{8})_Short_Positions\.xls)"', html):
        out[m.group(2)] = BASE + m.group(1)
    return out


def clean_name(s):
    s = str(s).replace("　", " ").strip()
    s = re.sub(r"\s*(普通株式|優先株式)$", "", s)
    return s


def clean_seller(s):
    return re.sub(r"\s+", " ", str(s).replace("　", " ")).strip()


def parse_xls(data):
    """[(calc_date, code, name, seller, ratio, shares, prev_ratio)] を返す"""
    d = pd.read_excel(BytesIO(data), sheet_name=0, header=None)
    rows = []
    for _, r in d.iloc[8:].iterrows():
        code = str(r[2]).strip()
        if not re.match(r"^[0-9A-Z]{4,5}$", code):
            continue
        # float(NaN)は例外にならずnanを返す。nanは全ての比較がFalseになるので、
        # ここで潰しておかないと新規建玉が「動き」から黙って消える（実際に起きた）
        ratio = pd.to_numeric(r[10], errors="coerce")
        if pd.isna(ratio):
            continue
        ratio = float(ratio)
        prev = pd.to_numeric(r[14], errors="coerce")
        prev = None if pd.isna(prev) else float(prev)
        try:
            shares = int(float(r[11]))
        except (TypeError, ValueError):
            shares = 0
        calc = str(r[1])[:10]
        rows.append((calc, code, clean_name(r[3]), clean_seller(r[5]), ratio, shares, prev))
    return rows


def load_state():
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {"positions": {}, "processed": []}


def apply_rows(state, rows):
    """1日分の報告を状態に反映"""
    pos = state["positions"]
    for calc, code, name, seller, ratio, shares, prev in rows:
        key = code + "||" + seller
        if ratio < THRESH:
            pos.pop(key, None)      # 0.5%を下回った＝報告義務の外に出た（解消扱い）
        else:
            pos[key] = {"c": code, "n": name, "s": seller, "r": ratio, "sh": shares, "d": calc}


def main():
    state = load_state()
    done = set(state["processed"])

    # リンク収集（初回=状態が空ならアーカイブもさかのぼる）
    html = http(BASE + INDEX).decode("utf-8", "replace")
    links = find_links(html)
    if not state["positions"]:
        print("初回バックフィル: アーカイブを取得")
        for a in ARCHIVES:
            try:
                links.update(find_links(http(BASE + a).decode("utf-8", "replace")))
            except Exception as e:
                print(f"  アーカイブ取得失敗 {a}: {e}", file=sys.stderr)

    todo = sorted(d for d in links if d not in done)
    # 未処理がなくても最新日は再処理する。しないと再実行のたびに
    # 「当日の動き」が空のJSONで上書きされてしまう（apply_rowsは同値上書きなので安全）
    if links:
        newest = max(links)
        if newest not in todo:
            todo.append(newest)
    if not todo:
        print("新しい報告なし")
    latest_rows = []
    for d8 in todo:
        try:
            rows = parse_xls(http(links[d8]))
        except Exception as e:
            print(f"取得/解析失敗 {d8}: {e}", file=sys.stderr)
            continue
        apply_rows(state, rows)
        done.add(d8)
        latest_rows = rows          # 昇順処理なので最後が最新日
        print(f"  {d8}: {len(rows)}報告 → 蓄積{len(state['positions'])}ポジション")
        time.sleep(0.6)             # JPXへの礼儀

    state["processed"] = sorted(done)[-400:]
    latest_date = sorted(done)[-1] if done else None

    # ─── 当日の動き ───
    moves = []
    for calc, code, name, seller, ratio, shares, prev in latest_rows:
        delta = ratio - (prev or 0)
        if prev is None and ratio >= THRESH:
            kind = "new"
        elif ratio < THRESH:
            kind, delta = "out", ratio - (prev or 0)
        elif delta >= MOVE_MIN:
            kind = "up"
        elif delta <= -MOVE_MIN:
            kind = "down"
        else:
            continue
        moves.append({"c": code, "n": name, "s": seller, "kind": kind,
                      "r": round(ratio * 100, 2), "pr": round((prev or 0) * 100, 2),
                      "d": round(delta * 100, 2), "calc": calc})
    moves.sort(key=lambda x: -abs(x["d"]))

    # ─── 銘柄ごとの残高合計 ───
    by_stock = {}
    for p in state["positions"].values():
        g = by_stock.setdefault(p["c"], {"c": p["c"], "n": p["n"], "total": 0.0, "sellers": []})
        g["total"] += p["r"]
        g["n"] = p["n"]  # 最新の名前で上書き
        g["sellers"].append({"s": p["s"], "r": round(p["r"] * 100, 2), "d": p["d"]})
    day_delta = {}
    for m in moves:
        day_delta[m["c"]] = round(day_delta.get(m["c"], 0) + m["d"], 2)
    stocks = []
    for g in by_stock.values():
        g["sellers"].sort(key=lambda x: -x["r"])
        stocks.append({"c": g["c"], "n": g["n"], "total": round(g["total"] * 100, 2),
                       "cnt": len(g["sellers"]), "d1": day_delta.get(g["c"], 0),
                       "sellers": g["sellers"][:12]})
    stocks.sort(key=lambda x: -x["total"])

    # ─── 機関ごとの集計 ───
    by_inst = {}
    for p in state["positions"].values():
        g = by_inst.setdefault(p["s"], {"s": p["s"], "cnt": 0, "tops": []})
        g["cnt"] += 1
        g["tops"].append({"c": p["c"], "n": p["n"], "r": round(p["r"] * 100, 2)})
    inst = sorted(by_inst.values(), key=lambda x: -x["cnt"])[:20]
    for g in inst:
        g["tops"] = sorted(g["tops"], key=lambda x: -x["r"])[:5]

    kinds = {k: sum(1 for m in moves if m["kind"] == k) for k in ("up", "down", "new", "out")}
    data = {
        "updated": datetime.now(JST).isoformat(timespec="seconds"),
        "report_date": latest_date and f"{latest_date[:4]}-{latest_date[4:6]}-{latest_date[6:]}",
        "summary": {"stocks": len(stocks), "positions": len(state["positions"]),
                    "institutions": len(by_inst), **kinds},
        "moves": moves[:100],
        "stocks": stocks,
        "inst": inst,
        "note": "空売り残高割合は発行済株式総数に対する割合。0.5%以上の大口のみ報告義務があるため、実際の空売り総量はこれより多い。公表は計算日の2営業日後。",
    }
    DOCS.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    STATE.write_text(json.dumps(state, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    kb = OUT.stat().st_size // 1024
    print(f"OK karauri.json ({kb}KB): 報告日{data['report_date']} "
          f"{len(stocks)}銘柄 {len(state['positions'])}ポジション "
          f"↑{kinds['up']} ↓{kinds['down']} 新規{kinds['new']} 解消{kinds['out']}")
    if stocks:
        print("残高合計Top5:")
        for s in stocks[:5]:
            print(f"  {s['n']}({s['c']}) {s['total']:.2f}% ({s['cnt']}機関)")


if __name__ == "__main__":
    main()
