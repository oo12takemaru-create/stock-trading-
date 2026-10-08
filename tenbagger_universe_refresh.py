# -*- coding: utf-8 -*-
"""tenbagger_universe.csv を JPX公式の上場銘柄一覧（月次 xlsx）から作り直す。

tenbagger-weekly.yml が tenbagger_rank.py の直前に毎週走らせる（JPXの一覧は月次更新なので、
変わらない週は差分なし＝コミットされない）。

■ 経緯
  CSV は 2026-08-03 にローカル（株式投資開発/ロケット投資検証/rocket_verify.py universe）で
  一度だけ作ったまま更新されておらず、8月以降の新規上場（604A ビーエイブル、646A クラサスケミカル等）が
  十倍株スキャナーのユニバースから抜けていた。さらに JPX の一覧は .xls → .xlsx に移り、旧URLは 404。
  抽出ルールは rocket_verify.py の cmd_universe と同一（内国株式のプライム/スタンダード/グロース）。

■ 出力
  tenbagger_universe.csv  列: ticker,code,name,market,sector33,scale（コード順・UTF-8 BOM付き・LF）
  tenbagger_shares.csv    ユニバースに新しく入った銘柄だけ発行済株式数を追記（既存行は触らない）

■ 失敗したら
  一覧が取れない・列が無い・件数が極端に少ない場合は CSV を書き換えずに正常終了する
  （前回の CSV のまま週次ランキングを続けるため）。
"""
import io
import sys
import urllib.request
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
UNIVERSE_CSV = HERE / "tenbagger_universe.csv"
SHARES_CSV = HERE / "tenbagger_shares.csv"
JPX_LIST = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xlsx"
COLS = ["ticker", "code", "name", "market", "sector33", "scale"]
MIN_ROWS = 3000        # 内国株3市場はおよそ3,700銘柄。これを大きく割ったら壊れた一覧とみなす
SHARES_MAX = 200       # 発行済株式数を新規取得する上限（通常は月に数十銘柄）


def log(*a):
    print(*a, flush=True)


def market_of(s) -> str | None:
    """『プライム（内国株式）』→ プライム。ETF/REIT/PRO Market/外国株式などは None"""
    s = str(s)
    if "内国株" not in s:
        return None
    for m in ("プライム", "スタンダード", "グロース"):
        if s.startswith(m):
            return m
    return None


def build_universe() -> pd.DataFrame | None:
    try:
        req = urllib.request.Request(JPX_LIST, headers={"User-Agent": "kaburadar.jp tenbagger_universe_refresh"})
        raw = urllib.request.urlopen(req, timeout=120).read()
        df = pd.read_excel(io.BytesIO(raw), dtype=str)
    except Exception as e:
        log(f"JPX一覧の取得に失敗（CSVは前回のまま）: {e}")
        return None

    df.columns = [str(c).strip() for c in df.columns]
    try:
        col_code = [c for c in df.columns if "コード" in c and "33" not in c and "17" not in c and "規模" not in c][0]
        col_name = [c for c in df.columns if "銘柄名" in c][0]
        col_mkt = [c for c in df.columns if "市場" in c][0]
        col_sec = [c for c in df.columns if "33業種区分" in c][0]
        col_scale = [c for c in df.columns if "規模区分" in c][0]
    except IndexError:
        log(f"JPX一覧の列が想定と違う（CSVは前回のまま）: {list(df.columns)}")
        return None

    df["market"] = df[col_mkt].map(market_of)
    u = df[df["market"].notna()].copy()
    u["code"] = u[col_code].astype(str).str.strip()
    u["ticker"] = u["code"] + ".T"
    u["name"] = u[col_name].astype(str).str.strip()
    u["sector33"] = u[col_sec].astype(str).str.strip()
    u["scale"] = u[col_scale].astype(str).str.strip()
    u = u[COLS].drop_duplicates("ticker").sort_values("code").reset_index(drop=True)

    as_of = df["日付"].iloc[0] if "日付" in df.columns and len(df) else "?"
    log(f"JPX一覧（{as_of}版）: 内国株3市場 {len(u)}銘柄")
    if len(u) < MIN_ROWS:
        log(f"件数が{MIN_ROWS}未満 → 壊れた一覧とみなして CSV は前回のまま")
        return None
    return u


def add_missing_shares(tickers: list[str]) -> None:
    """新しくユニバースに入った銘柄の発行済株式数を yfinance で取る（時価総額＝サイズ点用）。
    取れなくても NaN のまま（tenbagger_rank.py はサイズ点0で扱う）。既存行は変えない。"""
    sh = pd.read_csv(SHARES_CSV) if SHARES_CSV.exists() else pd.DataFrame(columns=["ticker", "shares"])
    todo = [t for t in tickers if t not in set(sh["ticker"])][:SHARES_MAX]
    if not todo:
        return
    import yfinance as yf

    rows = []
    for t in todo:
        v = float("nan")
        try:
            info = yf.Ticker(t).get_info()
            v = info.get("sharesOutstanding") or float("nan")
            if pd.isna(v):
                mc, px = info.get("marketCap"), info.get("previousClose")
                if mc and px:
                    v = mc / px
        except Exception:
            pass
        rows.append({"ticker": t, "shares": v})
    got = sum(pd.notna(r["shares"]) for r in rows)
    log(f"発行済株式数: 新規{len(todo)}銘柄を追記（取得できたのは{got}）")
    out = pd.concat([sh, pd.DataFrame(rows)], ignore_index=True).drop_duplicates("ticker", keep="first")
    out.to_csv(SHARES_CSV, index=False, encoding="utf-8-sig", lineterminator="\n")


def main():
    u = build_universe()
    if u is None:
        return 0

    old = pd.read_csv(UNIVERSE_CSV, dtype=str) if UNIVERSE_CSV.exists() else pd.DataFrame(columns=COLS)
    added = sorted(set(u["ticker"]) - set(old["ticker"]))
    removed = sorted(set(old["ticker"]) - set(u["ticker"]))
    name = dict(zip(u["ticker"], u["name"]))
    log(f"前回 {len(old)} → 今回 {len(u)}（新規 {len(added)} / 消えた {len(removed)}）")
    for t in added[:50]:
        log(f"  + {t} {name[t]}")
    if removed:
        log(f"  - {' '.join(removed[:50])}")

    u.to_csv(UNIVERSE_CSV, index=False, encoding="utf-8-sig", lineterminator="\n")
    log(f"→ {UNIVERSE_CSV.name}")
    log(u["market"].value_counts().to_string())

    add_missing_shares(added)
    return 0


if __name__ == "__main__":
    sys.exit(main())
