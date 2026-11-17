# -*- coding: utf-8 -*-
"""銘柄ごとの温度・東証33業種の温度（週1回）→ docs/thermo_stocks.json・docs/thermo_sector33.json（2026-11-17）

■ 2026-11-17 統括決定: 平日の夕方に毎日（thermo-stocks-weekly.yml の 19:50 JST）＋土曜の朝。
  業種の温度の主役は17業種（thermo_map.py）。33業種は単純平均の参考値として JSON にだけ残す。
■ 銘柄の温度（thermo_stocks.json）
  東証の全銘柄（docs/prices.json のコード）について、株価と200日線・50日線の差、22営業日前比の3つが、
  その銘柄自身の過去約10年（その日までのデータだけ。上場が新しい銘柄はある分だけ）の中でどのあたりにあるかを
  平均して 0〜100・℃ にする（thermo_pos.py の「位置の温度」）。相場の温度計とは別の物差し。
  **売買の合図ではなく、株価の位置の要約。**
  日足が 449本（200日線の助走＋位置を測る250本）に満たない銘柄・最終の取引日が古い銘柄は出さない。
■ 33業種の温度（thermo_sector33.json）
  東証の業種別指数は Yahoo で取れないので、同じ日足から **構成銘柄の1日の騰落の単純平均を積み上げた指数** を作り、
  同じ位置の温度を出す。今の上場銘柄だけで作るので（上場廃止した銘柄は入らない）、昔の値は実際より強めに出やすい。
  毎日の業種の温度は thermo_map.py（TOPIX-17業種の ETF）。こちらは週1回の細かい版。

■ 取得: yfinance の日足（調整済み・11年）を200銘柄ずつ・間に2秒（movers_daily.py と同じ作法）。
  欠けたチャンクは30秒待って1回だけ取り直す。約3,700銘柄で5分前後。毎日のワークフローには入れず、土曜に1回。
■ JPX 由来の数字（信用残・空売り・投資部門別）は使わない。銘柄の一覧と33業種の区分だけ既存のファイルから読む。

使い方: python -X utf8 thermo_stocks.py [--dry-run] [--limit 400] [--out-dir DIR]
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))

import thermo_pos as P                    # noqa: E402
from thermo100 import JST, celsius        # noqa: E402

DOCS = REPO / "docs"
UNIVERSE = REPO / "tenbagger_universe.csv"
CHUNK = 200
WAIT = 2.0
PERIOD = "11y"
MIN_BARS = 200 + P.MIN_PERIODS - 1        # 449本
STALE_DAYS = 7                            # 最終の取引日が全体の最新日よりこれ以上古い銘柄は出さない
MIN_MEMBERS = 5                           # 33業種の指数: その日に騰落がある銘柄がこれ未満の日は作らない
CLIP = 0.5                                # 33業種の指数: 1日の騰落 ±50% を超える値は誤った足として外す

DEF_STOCK = (
    "銘柄の温度は、株価と200日線の差・50日線の差・22営業日前比の3つが、その銘柄自身の過去約10年"
    "（その日までのデータだけ。上場が新しい銘柄はある分だけ）の中でどのあたりにあるか（0〜100）を平均し、"
    "−20℃〜60℃（℃ = 値 × 0.8 − 20）に読み替えたものです。"
    "帯は 冷え込み（−20〜−9℃）・寒い（−8〜11℃）・ふつう（12〜27℃）・暖かい（28〜60℃）。"
    "相場の温度計とは別の物差しで、株価の位置の要約です。売買の合図ではありません。平日の夕方に、その日の終値までで計算します。"
)
DEF_S33 = (
    "33業種の温度は、その業種の今の上場銘柄の1日の騰落を単純に平均して積み上げた指数について、"
    "指数と200日線の差・50日線の差・22営業日前比が過去約10年の中でどのあたりにあるかを平均し、℃ に読み替えたものです。"
    "上場をやめた銘柄が入っていないので、昔の値は実際より強めに出やすい点に注意してください。"
    "東証の業種別指数そのものではありません。単純平均の参考値で、TOPIX-17業種の温度（thermo_sector.json・大型株の影響が大きい）とは数字が違うことがあります。相場の温度計とは別の物差しで、売買の合図ではありません。"
)


def log(*a):
    print(*a, flush=True)


def load_codes(docs: Path) -> list[str]:
    d = json.loads((docs / "prices.json").read_text(encoding="utf-8"))
    return sorted(d["items"].keys())


def load_sector33() -> dict:
    out = {}
    try:
        with open(UNIVERSE, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                if r.get("code") and r.get("sector33"):
                    out[r["code"].strip()] = r["sector33"].strip()
    except Exception as e:
        log(f"33業種の区分が読めない: {e}")
    return out


def fetch_chunk(tickers):
    import yfinance as yf
    df = yf.download(tickers, period=PERIOD, interval="1d", progress=False, auto_adjust=True,
                     group_by="column", threads=True)
    if df is None or df.empty:
        return pd.DataFrame()
    c = df["Close"] if isinstance(df.columns, pd.MultiIndex) else df[["Close"]].rename(columns={"Close": tickers[0]})
    c.index = pd.to_datetime(c.index).tz_localize(None) if c.index.tz is not None else pd.to_datetime(c.index)
    return c.dropna(axis=1, how="all")


def fetch_all(codes):
    tickers = [f"{c}.T" for c in codes]
    parts, miss = [], []
    chunks = [tickers[i:i + CHUNK] for i in range(0, len(tickers), CHUNK)]
    for i, ch in enumerate(chunks, 1):
        try:
            got = fetch_chunk(ch)
        except Exception as e:
            log(f"  チャンク{i}/{len(chunks)} 失敗: {e}")
            got = pd.DataFrame()
        parts.append(got)
        miss += [t for t in ch if t not in got.columns]
        log(f"  チャンク{i}/{len(chunks)}: {got.shape[1]}/{len(ch)}")
        time.sleep(WAIT)
    if miss:
        log(f"  取り直し: {len(miss)}銘柄（30秒待ってから）")
        time.sleep(30)
        for i in range(0, len(miss), CHUNK):
            try:
                parts.append(fetch_chunk(miss[i:i + CHUNK]))
            except Exception as e:
                log(f"  取り直し失敗: {e}")
            time.sleep(WAIT)
    closes = pd.concat([p for p in parts if len(p)], axis=1)
    closes = closes.loc[:, ~closes.columns.duplicated()]
    closes.columns = [c[:-2] if c.endswith(".T") else c for c in closes.columns]
    return closes.sort_index()


def stock_items(closes: pd.DataFrame) -> tuple[dict, str]:
    """コード → [℃, 前営業日からの℃の差, 帯の番号, 0〜100, 5営業日前からの℃の差]"""
    last_all = closes.dropna(how="all").index[-1]
    items = {}
    for code in closes.columns:
        c = closes[code].dropna()
        c = c[c > 0]
        if len(c) < MIN_BARS or (last_all - c.index[-1]).days > STALE_DAYS:
            continue
        f = P.features(c)
        p = P.fast_pct(f) * 100
        t = p.mean(axis=1, skipna=False).dropna()
        if len(t) < 2:
            continue
        v = int(round(float(t.iloc[-1])))
        v1 = int(round(float(t.iloc[-2])))
        v5 = int(round(float(t.iloc[-6]))) if len(t) > 5 else None
        items[code] = [celsius(v), celsius(v) - celsius(v1), P.band_index(v), v,
                       None if v5 is None else celsius(v) - celsius(v5)]
    return items, str(last_all.date())


def sector_index(closes: pd.DataFrame, sec: dict) -> dict[str, pd.Series]:
    """33業種ごとに、構成銘柄の1日の騰落の単純平均を積み上げた指数（今の上場銘柄だけ）"""
    ret = closes.pct_change(fill_method=None)
    ret = ret.where(ret.abs() <= CLIP)
    out = {}
    for s in sorted(set(sec.values())):
        cols = [c for c in closes.columns if sec.get(c) == s]
        if len(cols) < MIN_MEMBERS:
            continue
        r = ret[cols]
        m = r.mean(axis=1, skipna=True).where(r.notna().sum(axis=1) >= MIN_MEMBERS)
        m = m.dropna()
        if len(m) < MIN_BARS:
            continue
        out[s] = (1 + m).cumprod() * 100
    return out


def build(closes: pd.DataFrame, sec: dict, now: datetime):
    items, last = stock_items(closes)
    slug = {}
    try:
        from heatmap_day import SECTOR_SLUG as slug  # noqa: N811
    except Exception:
        pass
    s_items = []
    for s, idx in sector_index(closes, sec).items():
        row = P.summary_row(P.temp_series(idx))
        if row is None:
            continue
        n_mem = sum(1 for c in closes.columns if sec.get(c) == s)
        s_items.append({"s": s, "slug": slug.get(s), "members": n_mem, **row,
                        "h": [[str(d.date()), int(v)] for d, v in P.temp_series(idx)["tc"].iloc[-60:].items()]})
    s_items.sort(key=lambda x: x["tc"])
    bands_n = [0] * len(P.POS_BANDS)
    for v in items.values():
        bands_n[v[2]] += 1
    stocks = {
        "name": "銘柄の温度",
        "updated": now.isoformat(timespec="seconds"),
        "trade_date": last,
        "count": len(items),
        "universe": int(closes.shape[1]),
        "fields": ["tc（℃）", "d1（前営業日からの ℃ の差）", "band（bands の番号）", "v（0〜100）", "d5（5営業日前からの ℃ の差）"],
        "bands": P.POS_BANDS, "bands_c": P.POS_BANDS_C,
        "band_counts": bands_n,
        "median_tc": int(np.median([v[0] for v in items.values()])) if items else None,
        "definition": DEF_STOCK,
        "skip_rule": f"日足が{MIN_BARS}本未満、または最終の取引日が{last}より{STALE_DAYS}日以上古い銘柄は出さない",
        "items": items,
    }
    s33 = {
        "name": "33業種の温度（構成銘柄の平均から作った指数）",
        "role": "参考値（業種の温度の主役は thermo_sector.json の17業種。2026-11-17 統括決定でサイトには当面出さない）",
        "updated": stocks["updated"],
        "trade_date": last,
        "items": s_items,
        "coldest": s_items[0]["s"] if s_items else None,
        "hottest": s_items[-1]["s"] if s_items else None,
        "bands": P.POS_BANDS, "bands_c": P.POS_BANDS_C,
        "definition": DEF_S33,
        "how": f"構成銘柄（tenbagger_universe.csv の33業種区分）の1日の騰落（調整済み終値）の単純平均を積み上げた指数。"
               f"騰落がある銘柄が{MIN_MEMBERS}未満の日は作らない。1日の騰落が ±{int(CLIP * 100)}% を超える値は誤った足として外す。",
    }
    return stocks, s33


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--docs", default=str(DOCS), help="prices.json を読む場所")
    ap.add_argument("--out-dir", default=None, help="書く場所（既定は --docs と同じ）")
    a = ap.parse_args()
    docs = Path(a.docs)
    out = Path(a.out_dir or a.docs)
    out.mkdir(parents=True, exist_ok=True)
    bad = P.text_ng(DEF_STOCK + DEF_S33)
    if bad:
        raise SystemExit(f"定義文に使わない言葉: {bad}")
    t0 = time.time()
    codes = load_codes(docs)
    if a.limit:
        codes = codes[:a.limit]
    log(f"{len(codes)}銘柄の日足（{PERIOD}）を取得")
    closes = fetch_all(codes)
    log(f"取れた: {closes.shape[1]}銘柄 / {time.time() - t0:.0f}秒")
    stocks, s33 = build(closes, load_sector33(), datetime.now(JST))
    log(f"銘柄の温度: {stocks['count']}銘柄（{stocks['trade_date']}） 帯ごと {stocks['band_counts']} 中央値 {stocks['median_tc']}℃")
    for x in s33["items"][:3] + s33["items"][-3:]:
        log(f"  {x['s']} {x['label']}（{x['members']}銘柄）")
    if stocks["count"] < len(codes) * 0.6:
        raise SystemExit("出せた銘柄が少なすぎる（取得の失敗）。書かない")
    if a.dry_run:
        return
    (out / "thermo_stocks.json").write_text(json.dumps(stocks, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    (out / "thermo_sector33.json").write_text(json.dumps(s33, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    log(f"書いた: thermo_stocks.json / thermo_sector33.json（{time.time() - t0:.0f}秒）")


if __name__ == "__main__":
    main()
