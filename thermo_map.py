# -*- coding: utf-8 -*-
"""世界の温度・業種の温度（位置の温度）→ docs/thermo_world.json・docs/thermo_sector.json（2026-11-17）

■ 何か
  相場の温度計（thermo100.json）とは別の物差し。指数ごとに「終値と200日線・50日線の差、22営業日前比」が、
  その指数自身の過去約10年の中でどのあたりにあるかを0〜100（と ℃）にしたもの（作り方は thermo_pos.py）。
  VIX・S&P500 を使う判定は市場全体のものなので、ここでは使わない。だから
  「日経平均の位置の温度」と「相場の温度計」は同じ数字にならない（両方を出し、名前を分ける）。

■ 世界（thermo_world.json）
  world_fetch.py の株価指数（kind=idx）をそのまま使う（一覧を二重に持たない）。TOPIX は指数が Yahoo で取れないので
  連動ETF（1306.T・分割と配当を調整した値）で代用。各指数は自分の取引日の並びで計算する（休場日は行が無い）。
■ 業種（thermo_sector.json）
  東証の業種別の指数そのものは Yahoo で取れないので、TOPIX-17業種の連動ETF（NEXT FUNDS 1617〜1633・2008年3月〜）の
  分割と配当を調整した終値で作る。17業種と東証33業種の対応（s33）を付ける。
  業種の温度の主役はこの17業種（2026-11-17 統括決定）。33業種は構成銘柄の単純平均から作る参考値
  （thermo_stocks.py → thermo_sector33.json・サイトには当面出さない）。

■ 公開してよいもの: Yahoo の公開の指数・ETF の終値だけ。JPX 由来の数字（信用残・空売り・投資部門別）は使わない。

使い方: python -X utf8 thermo_map.py [--dry-run] [--only world|sector]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))

import thermo_pos as P                       # noqa: E402
from thermo100 import JST, dl, celsius, c_label  # noqa: E402

DOCS = REPO / "docs"
WAIT = 1.0      # 1本ずつ取りに行く間隔（秒）

# TOPIX-17業種 ETF（NEXT FUNDS）と東証33業種の対応
SECTOR17 = [
    ("1617.T", "食品", ["水産・農林業", "食料品"]),
    ("1618.T", "エネルギー資源", ["鉱業", "石油・石炭製品"]),
    ("1619.T", "建設・資材", ["建設業", "ガラス・土石製品", "金属製品"]),
    ("1620.T", "素材・化学", ["繊維製品", "パルプ・紙", "化学"]),
    ("1621.T", "医薬品", ["医薬品"]),
    ("1622.T", "自動車・輸送機", ["ゴム製品", "輸送用機器"]),
    ("1623.T", "鉄鋼・非鉄", ["鉄鋼", "非鉄金属"]),
    ("1624.T", "機械", ["機械"]),
    ("1625.T", "電機・精密", ["電気機器", "精密機器"]),
    ("1626.T", "情報通信・サービスその他", ["その他製品", "情報・通信業", "サービス業"]),
    ("1627.T", "電力・ガス", ["電気・ガス業"]),
    ("1628.T", "運輸・物流", ["陸運業", "海運業", "空運業", "倉庫・運輸関連業"]),
    ("1629.T", "商社・卸売", ["卸売業"]),
    ("1630.T", "小売", ["小売業"]),
    ("1631.T", "銀行", ["銀行業"]),
    ("1632.T", "金融（除く銀行）", ["証券、商品先物取引業", "保険業", "その他金融業"]),
    ("1633.T", "不動産", ["不動産業"]),
]

DEF_COMMON = (
    "終値と200日線の差・50日線の差・22営業日前比の3つが、その指数自身の過去約10年（その日までのデータだけ）の中で"
    "どのあたりにあるか（0〜100）を平均し、−20℃〜60℃（℃ = 値 × 0.8 − 20）に"
    "読み替えたものです。帯は 冷え込み（−20〜−9℃）・寒い（−8〜11℃）・ふつう（12〜27℃）・暖かい（28〜60℃）。"
    "VIX や S&P500 を使う相場の温度計の判定は入っていないので、相場の温度計とは別の物差しです（同じ数字にはなりません）。"
    "株価の位置の要約で、売買の合図ではありません。"
)
DEF_WORLD = "世界の温度は、各国の株価指数の位置の温度です。" + DEF_COMMON + "各指数はその国の取引日で計算し、最終の取引日（d）がそろわないことがあります。"
DEF_SECTOR = ("業種の温度は、TOPIX-17業種の連動ETF（NEXT FUNDS・分割と配当を調整した終値）で測った位置の温度です。"
              + DEF_COMMON)


def world_items():
    from world_fetch import ITEMS
    out = []
    for t, n, g, kind, cur, extra in ITEMS:
        if kind != "idx":
            continue
        src = extra.get("alt") or t
        out.append({"t": t, "src": src, "n": n, "g": g, "sub": extra.get("sub", 0),
                    "note": "TOPIX は連動ETF（1306.T）で代用" if extra.get("alt") else None})
    return out


def fetch_close(src: str) -> pd.Series:
    """指数は調整なし・空欄の最新日を確定値で埋める。ETF（.T）は分割と配当を調整し、桁違いの足を落とす"""
    if src.endswith(".T"):
        return P.clean_close(dl(src, period="max", adjust=True, fill=False)["Close"])
    return dl(src, period="max")["Close"].dropna()


def one(src: str, close: pd.Series | None = None, tail: int = 60) -> dict | None:
    c = close if close is not None else fetch_close(src)
    df = P.temp_series(c)
    row = P.summary_row(df)
    if row is None:
        return None
    row["h"] = [[str(d.date()), int(v)] for d, v in df["tc"].iloc[-tail:].items()]
    return row


def rank_block(items: list[dict], key="tc") -> dict:
    ok = [x for x in items if x.get("tc") is not None and not x.get("sub")]
    ok.sort(key=lambda x: (x[key], x["n"]))
    return {"order": [x["t"] for x in ok],
            "coldest": {"t": ok[0]["t"], "n": ok[0]["n"], "tc": ok[0]["tc"]} if ok else None,
            "hottest": {"t": ok[-1]["t"], "n": ok[-1]["n"], "tc": ok[-1]["tc"]} if ok else None}


def market_thermo(docs: Path) -> dict | None:
    try:
        d = json.loads((docs / "thermo100.json").read_text(encoding="utf-8"))
        return {"trade_date": d.get("trade_date"), "value": d.get("value"), "tc": celsius(d.get("value")),
                "regime_jp": d.get("regime_jp"),
                "note": "相場の温度計（判定つき・別の物差し）。日経平均の位置の温度とは同じ数字にならない"}
    except Exception:
        return None


def build_world(now, docs, fetcher=fetch_close):
    items = []
    for it in world_items():
        try:
            r = one(it["src"], fetcher(it["src"]))
        except Exception as e:
            print(f"  {it['t']}: 取れず {e}", file=sys.stderr)
            r = None
        row = {"t": it["t"], "n": it["n"], "g": it["g"], "sub": it["sub"]}
        if it["note"]:
            row["note"] = it["note"]
        if r is None:
            row.update({"ok": 0, "tc": None})
        else:
            row.update({"ok": 1, **r})
        items.append(row)
        time.sleep(WAIT if fetcher is fetch_close else 0)
    return {
        "name": "世界の温度",
        "updated": now.isoformat(timespec="seconds"),
        "items": items,
        "rank": rank_block(items),
        "market_thermo": market_thermo(docs),
        "bands": P.POS_BANDS, "bands_c": P.POS_BANDS_C,
        "fields": {"value": "位置の温度（0〜100）", "tc": "℃", "diff1_c": "前の取引日からの ℃ の差",
                   "diff5_c": "5取引日前からの ℃ の差", "diff20_c": "20取引日前からの ℃ の差",
                   "parts": "3つの入力（raw = 値%・pos = 過去約10年の中の位置 0〜100）",
                   "h": "直近60取引日の ℃", "since": "温度が出ている最初の日", "sub": "1 = まとめの順位に数えない（SOX指数）"},
        "definition": DEF_WORLD,
        "sources": "Yahoo! Finance の各国の株価指数（TOPIX は 1306.T）",
    }


def build_sector(now, docs, fetcher=fetch_close):
    items = []
    for t, n, s33 in SECTOR17:
        try:
            r = one(t, fetcher(t))
        except Exception as e:
            print(f"  {t}: 取れず {e}", file=sys.stderr)
            r = None
        row = {"t": t, "n": n, "s33": s33}
        row.update({"ok": 0, "tc": None} if r is None else {"ok": 1, **r})
        items.append(row)
        time.sleep(WAIT if fetcher is fetch_close else 0)
    ok = [x for x in items if x["ok"]]
    dates = sorted({x["d"] for x in ok})
    return {
        "name": "業種の温度（TOPIX-17業種）",
        "updated": now.isoformat(timespec="seconds"),
        "trade_date": dates[-1] if dates else None,
        "items": items,
        "rank": rank_block(items),
        "market_thermo": market_thermo(docs),
        "bands": P.POS_BANDS, "bands_c": P.POS_BANDS_C,
        "fields": {"t": "ETF のコード", "n": "業種（TOPIX-17）", "s33": "含まれる東証33業種",
                   "value": "位置の温度（0〜100）", "tc": "℃", "diff1_c": "前営業日からの ℃ の差",
                   "diff5_c": "5営業日前からの ℃ の差", "diff20_c": "20営業日前からの ℃ の差",
                   "parts": "3つの入力（raw = 値%・pos = 過去約10年の中の位置 0〜100）", "h": "直近60営業日の ℃"},
        "definition": DEF_SECTOR,
        "sources": "Yahoo! Finance の NEXT FUNDS TOPIX-17業種ETF（1617.T〜1633.T）の調整済み終値",
    }


def save(p: Path, data: dict):
    p.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", choices=["world", "sector"])
    ap.add_argument("--docs", default=str(DOCS))
    a = ap.parse_args()
    docs = Path(a.docs)
    bad = P.text_ng(DEF_WORLD + DEF_SECTOR)
    if bad:
        raise SystemExit(f"定義文に使わない言葉: {bad}")
    now = datetime.now(JST)
    for kind, fn, name in (("world", build_world, "thermo_world.json"), ("sector", build_sector, "thermo_sector.json")):
        if a.only and a.only != kind:
            continue
        data = fn(now, docs)
        ok = [x for x in data["items"] if x.get("ok")]
        print(f"{data['name']}: {len(ok)}/{len(data['items'])}")
        for x in sorted(ok, key=lambda x: x["tc"]):
            print(f"  {x['n']:<16} {x['label']:>5} {x['band_jp']:<4} 前日比{x['diff1_c']:+} （{x['d']}）")
        if len(ok) < len(data["items"]) * 0.7:
            print(f"  取れた数が少ないので {name} は書かない", file=sys.stderr)
            continue
        if not a.dry_run:
            save(docs / name, data)
            print(f"書いた: {name}")


if __name__ == "__main__":
    main()
