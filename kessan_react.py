# -*- coding: utf-8 -*-
"""決算の答え合わせ → docs/kessan_react.json（株レーダー kaburadar.jp）

「決算を出した銘柄が、そのあと実際どう動いたか」を集計する。
決算カレンダー（kessan.json の過去日）に載っている銘柄の値動きを yfinance で取り直す。

■ なぜ発表「翌営業日」を見るのか
  日本の決算発表はほとんどが大引け後（15時以降）。発表当日の終値には決算内容が
  入っていない。反応が出るのは翌営業日の寄り付きなので、そこを基準にする。
  ただし場中や寄り前の発表もあるため、当日の値動きも参考として併記する。

■ 出すもの
  ・ギャップ（前日終値 → 翌営業日の始値）＝ 決算への最初の反応
  ・翌営業日の終値騰落率      ＝ 1日通しての評価
  ・出来高倍率（20日平均比）  ＝ どれだけ注目されたか
  ・上振れ/下振れの分布       ＝ その日の決算全体の地合い

■ 対象
  主要銘柄（監視341）を優先。1日あたり最大 MAX_PER_DAY 銘柄まで。
  全社やると数千ティッカーになり実行時間が読めないため。

■ 予定日は kessan.json ではなく JPX から取り直す
  kessan.json は容量削減のため過去日の銘柄を主要銘柄だけに間引いている
  （362社発表の日でも14社しか残らない）。答え合わせの母数としては足りないので、
  kessan_fetch.py のパーサを再利用してJPXの元データから全社ぶんを取る。
"""
import json
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pandas as pd

from kessan_fetch import BASE, INDEX, find_files, http, parse

JST = timezone(timedelta(hours=9))
DOCS = Path(__file__).parent / "docs"
OUT = DOCS / "kessan_react.json"
W = "月火水木金土日"

LOOKBACK_DAYS = 21      # 何日前の決算まで振り返るか
MAX_PER_DAY = 90        # 1日あたりの対象銘柄数の上限（主要銘柄を優先）
BIG_MOVE = 5.0          # 「大きく動いた」とみなす騰落率(%)


def load_schedule():
    """JPXから全社の決算発表予定日を取り直す（kessan_fetch と同じ手順）"""
    html = http(BASE + INDEX).decode("utf-8", "replace")
    urls = find_files(html)
    if not urls:
        print("JPXから決算予定ファイルを見つけられませんでした", file=sys.stderr)
        sys.exit(1)
    merged = {}
    for u in urls:
        try:
            got, _ = parse(http(u))
        except Exception as e:
            print(f"取得/解析失敗 {u.rsplit('/',1)[-1]}: {e}", file=sys.stderr)
            continue
        merged.update(got)
        time.sleep(0.5)
    if len(merged) < 100:
        print(f"社数が少なすぎます({len(merged)})", file=sys.stderr)
        sys.exit(1)
    return merged


def major_codes():
    try:
        from daily_scanner_v2_8_0 import STOCKS
        return {t.split(".")[0] for t in STOCKS}
    except Exception as e:
        print(f"主要銘柄リストを読めませんでした: {e}", file=sys.stderr)
        return set()


def pick_targets(merged, today):
    """過去 LOOKBACK_DAYS の発表日ごとに、検証する銘柄を選ぶ"""
    lo = today - timedelta(days=LOOKBACK_DAYS)
    major = major_codes()
    days = {}
    for v in merged.values():
        if not v.get("d"):
            continue
        dt = datetime.strptime(v["d"], "%Y-%m-%d").date()
        if not (lo <= dt < today):
            continue
        v = dict(v, maj=v["c"] in major)
        days.setdefault(v["d"], []).append(v)

    out = {}
    for d, items in days.items():
        dt = datetime.strptime(d, "%Y-%m-%d").date()
        total = len(items)
        # 主要銘柄を優先し、残りはコード順で埋める
        items = sorted(items, key=lambda x: (not x["maj"], x["c"]))[:MAX_PER_DAY]
        out[d] = {"d": d, "w": W[dt.weekday()], "n": total,
                  "rush": total >= 150, "items": items}
    return out


def fetch_prices(codes, start, end):
    """{code: DataFrame} を返す。yfinanceは一括で取ると欠損に強い"""
    import yfinance as yf
    tickers = [c + ".T" for c in codes]
    raw = yf.download(tickers, start=start, end=end, progress=False,
                      auto_adjust=False, threads=True, group_by="ticker")
    out = {}
    for c in codes:
        t = c + ".T"
        try:
            df = raw[t] if len(tickers) > 1 else raw
            df = df.dropna(subset=["Close"])
            if len(df) >= 2:
                out[c] = df
        except Exception:
            continue
    return out


def react_for(df, ann_date):
    """発表日 ann_date に対する反応を計算する。

    返す: (前日終値, 翌営業日の始値/終値/出来高倍率, ギャップ%, 翌日終値%, 当日終値%)
    翌営業日がまだ来ていない（＝データが無い）場合は None
    """
    idx = [d.date() for d in df.index]
    # 発表日「以前」の最後の営業日 = 発表当日（休日発表なら直前の営業日）
    prev_i = None
    for i, d in enumerate(idx):
        if d <= ann_date:
            prev_i = i
        else:
            break
    if prev_i is None or prev_i + 1 >= len(df):
        return None

    prev = df.iloc[prev_i]          # 発表当日（終値には決算が入っていない）
    nxt = df.iloc[prev_i + 1]       # 翌営業日＝反応が出る日
    prev_close = float(prev["Close"])
    if prev_close <= 0:
        return None

    # 当日の騰落（寄り前・場中発表のケースを拾うための参考値）
    same_day = None
    if prev_i >= 1:
        pc = float(df.iloc[prev_i - 1]["Close"])
        if pc > 0:
            same_day = (prev_close / pc - 1) * 100

    # 出来高倍率（翌営業日 ÷ そこまでの20日平均）
    vr = None
    vol = df["Volume"].iloc[max(0, prev_i - 19):prev_i + 1]
    vol = vol[vol > 0]
    if len(vol) >= 5 and float(nxt["Volume"]) > 0:
        vr = float(nxt["Volume"]) / float(vol.mean())

    return {
        "prev_close": round(prev_close, 1),
        "open": round(float(nxt["Open"]), 1),
        "close": round(float(nxt["Close"]), 1),
        "gap": round((float(nxt["Open"]) / prev_close - 1) * 100, 2),
        "chg": round((float(nxt["Close"]) / prev_close - 1) * 100, 2),
        "same_day": None if same_day is None else round(same_day, 2),
        "vr": None if vr is None else round(vr, 1),
        "react_date": idx[prev_i + 1].strftime("%Y-%m-%d"),
    }


def main():
    merged = load_schedule()
    today = datetime.now(JST).date()
    days = pick_targets(merged, today)
    if not days:
        print("振り返る対象の決算がありません（直近に発表日なし）")

    codes = sorted({i["c"] for d in days.values() for i in d["items"]})
    print(f"対象: {len(days)}日 / {len(codes)}銘柄")

    prices = {}
    if codes:
        start = (today - timedelta(days=LOOKBACK_DAYS + 60)).strftime("%Y-%m-%d")
        end = (today + timedelta(days=1)).strftime("%Y-%m-%d")
        # 一度に投げすぎるとyfinance側で落ちるので分割
        CH = 120
        for i in range(0, len(codes), CH):
            part = codes[i:i + CH]
            try:
                prices.update(fetch_prices(part, start, end))
            except Exception as e:
                print(f"株価取得失敗 {i}〜: {e}", file=sys.stderr)
            time.sleep(0.5)
        print(f"株価取得: {len(prices)}/{len(codes)}銘柄")

    out_days = []
    for dkey in sorted(days, reverse=True):
        day = days[dkey]
        ann = datetime.strptime(dkey, "%Y-%m-%d").date()
        rows = []
        for it in day["items"]:
            df = prices.get(it["c"])
            if df is None:
                continue
            r = react_for(df, ann)
            if r is None:
                continue
            rows.append({"c": it["c"], "n": it["n"], "s": it.get("s", ""),
                         "q": it.get("q", ""), "maj": bool(it.get("maj")), **r})
        if not rows:
            continue
        rows.sort(key=lambda x: -x["chg"])
        ups = [r for r in rows if r["chg"] > 0]
        big_up = [r for r in rows if r["chg"] >= BIG_MOVE]
        big_dn = [r for r in rows if r["chg"] <= -BIG_MOVE]
        avg = sum(r["chg"] for r in rows) / len(rows)
        out_days.append({
            "d": dkey, "w": day["w"], "n": day["n"], "rush": day["rush"],
            "react_date": rows[0]["react_date"],
            "checked": len(rows),
            "up": len(ups), "down": len(rows) - len(ups),
            "big_up": len(big_up), "big_dn": len(big_dn),
            "avg": round(avg, 2),
            "top": rows[:8],
            "bottom": rows[-8:][::-1],
        })

    allrows = [r for d in out_days for r in (d["top"] + d["bottom"])]
    data = {
        "updated": datetime.now(JST).isoformat(timespec="seconds"),
        "today": today.strftime("%Y-%m-%d"),
        "lookback_days": LOOKBACK_DAYS,
        "max_per_day": MAX_PER_DAY,
        "big_move": BIG_MOVE,
        "days": out_days,
        "note": ("日本の決算はほとんどが大引け後の発表のため、反応が出る翌営業日の値動きを集計しています。"
                 "ギャップは前日終値→翌営業日始値、騰落率は前日終値→翌営業日終値です。"
                 "1日あたり主要銘柄を優先して最大{}社まで対象としています。").format(MAX_PER_DAY),
    }
    DOCS.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    kb = OUT.stat().st_size // 1024
    print(f"OK kessan_react.json ({kb}KB): {len(out_days)}日ぶん / 延べ{len(allrows)}行")
    for d in out_days[:5]:
        print(f"  {d['d']}({d['w']}) 発表{d['n']:>4}社 → 検証{d['checked']:>3}社  "
              f"上昇{d['up']:>3}/下落{d['down']:>3}  平均{d['avg']:+.2f}%  "
              f"±5%超 {d['big_up']}/{d['big_dn']}")


if __name__ == "__main__":
    main()
