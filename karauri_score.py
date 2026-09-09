# -*- coding: utf-8 -*-
"""機関別の空売り成績（答え合わせ）→ docs/karauri_score.json

■ 答えは「当たっていない」。それを隠さず出すためのスクリプト
  検証（2026-09-09）の結論: 母集団比の20日勝率 49.4%。ほぼコイン投げ。

■ ベンチマークは日経平均ではなく「同じ公表日の母集団の平均」
  最初は日経平均で市場調整したが、それは誤りだった。
  空売り残高0.5%以上の銘柄は中小型・高ベータに偏り、4種別すべてが
  日経比+2.5〜3.5%になった（種別に関係ない母集団のドリフト）。
  日経を引くと「新規空売りの勝率40%」という見せかけの数字が出る。
  機関の巧拙を見るには、同じ日・同じ母集団の平均と比べるしかない。

■ 起点は公表日の翌営業日の始値
  JPXの公表は計算日の2営業日後（T+2）。公表は引け後なので当日は動けない。
  読者が最初に取引できるのは翌営業日の寄り。ここを起点にする。

■ イベント履歴は自前で貯める
  docs/karauri_state.json は最新スナップショットしか持たない。
  JPXの一覧は約70営業日で消えるので、karauri_events/events.json に
  イベントと算出済みリターンを積む（消える前に貯める）。
  2回目以降は新しい公表日と、判定できるようになったイベントだけを処理する。

■ 既存の karauri_fetch.py は無変更。パーサだけ import して使う
"""
import json
import math
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import karauri_fetch as K  # noqa: E402

JST = timezone(timedelta(hours=9))
EVENTS = HERE / "karauri_events" / "events.json"
OUT = HERE / "docs" / "karauri_score.json"

THRESH = 0.005        # 報告義務の下限 0.5%
STEP = 0.001          # 増加/減少とみなす最小変化 0.1pt
H = (5, 20)           # 営業日
MIN_N = 10            # これ未満の機関は「件数不足」として値を出さない
DOWN_IS_WIN = {"新規", "増加"}
MAX_TICKERS = int(os.environ.get("KARAURI_MAX_TICKERS", "700"))


def classify_event(ratio, prev):
    if ratio is not None and ratio < THRESH:
        return "解消" if (prev is not None and prev >= THRESH) else None
    if prev is None or prev < THRESH:
        return "新規"
    d = ratio - prev
    if d >= STEP:
        return "増加"
    if d <= -STEP:
        return "減少"
    return None


def binom_p(hits, n):
    """両側二項検定（p=0.5）。nが数万になると二項係数が浮動小数に収まらないので
    n>=200 は連続修正つきの正規近似にする"""
    if not n:
        return None
    if n < 200:
        def pmf(k):
            return math.comb(n, k) * 0.5 ** n
        obs = pmf(hits)
        return min(1.0, sum(pmf(k) for k in range(n + 1) if pmf(k) <= obs + 1e-15))
    z = max(0.0, (abs(hits - n / 2) - 0.5) / (math.sqrt(n) / 2))
    return min(1.0, math.erfc(z / math.sqrt(2)))


# ---------------------------------------------------------------- 収集
def collect_events(store):
    """JPXの日次ファイルから新しい公表日ぶんのイベントを足す"""
    links = K.find_links(K.http(K.BASE + K.INDEX).decode("utf-8", "replace"))
    for a in K.ARCHIVES:
        try:
            links.update(K.find_links(K.http(K.BASE + a).decode("utf-8", "replace")))
        except Exception as e:
            print(f"  アーカイブ失敗 {a}: {e}", file=sys.stderr)
    done = {e["pub"] for e in store}
    todo = sorted(d for d in links
                  if f"{d[:4]}-{d[4:6]}-{d[6:]}" not in done)
    print(f"JPX公表日 {len(links)}日 / 未処理 {len(todo)}日")
    added = 0
    for d8 in todo:
        pub = f"{d8[:4]}-{d8[4:6]}-{d8[6:]}"
        try:
            rows = K.parse_xls(K.http(links[d8]))
        except Exception as e:
            print(f"  取得/解析失敗 {d8}: {e}", file=sys.stderr)
            continue
        for calc, code, name, seller, ratio, shares, prev in rows:
            ev = classify_event(ratio, prev)
            if not ev:
                continue
            store.append({"pub": pub, "calc": calc, "code": code, "name": name,
                          "seller": seller, "ratio": ratio, "prev": prev, "event": ev})
            added += 1
        time.sleep(0.6)      # JPXへの礼儀
    print(f"  イベント +{added}件（合計 {len(store)}件）")
    return added


# ---------------------------------------------------------------- 株価
def fill_returns(store):
    """まだリターンが入っていないイベントに、公表日の翌営業日始値からの
    5/20営業日リターンを入れる。銘柄数が多いのでバッチで取る"""
    import pandas as pd
    import yfinance as yf

    need = [e for e in store if "d20" not in e]
    if not need:
        print("リターンの追加なし")
        return 0
    codes = sorted({e["code"] for e in need})[:MAX_TICKERS]
    print(f"リターン未算出 {len(need)}件 / 株価を取る銘柄 {len(codes)}")

    def tick(c):
        c = c.strip()
        return (c[:4] if len(c) == 5 and c.endswith("0") else c) + ".T"

    lo = min(e["pub"] for e in need)
    start = (datetime.fromisoformat(lo) - timedelta(days=10)).date().isoformat()
    px = {}
    B = 60
    for i in range(0, len(codes), B):
        chunk = codes[i:i + B]
        try:
            df = yf.download(" ".join(tick(c) for c in chunk), start=start,
                             progress=False, auto_adjust=False, threads=False,
                             group_by="ticker")
        except Exception as e:
            print(f"  価格取得失敗: {e}", file=sys.stderr)
            continue
        for c in chunk:
            try:
                sub = df[tick(c)] if isinstance(df.columns, pd.MultiIndex) else df
                o, cl = sub["Open"].dropna(), sub["Close"].dropna()
                if len(cl) < 6:
                    continue
                px[c] = ({k.strftime("%Y-%m-%d"): float(v) for k, v in o.items()},
                         {k.strftime("%Y-%m-%d"): float(v) for k, v in cl.items()})
            except Exception:
                continue
        time.sleep(1.0)
    print(f"  価格が取れた銘柄 {len(px)}")

    filled = 0
    for e in need:
        p = px.get(e["code"])
        if not p:
            continue
        opens, closes = p
        after = sorted(d for d in closes if d > e["pub"])
        for h in H:
            if len(after) < h + 1:
                continue
            d0, d1 = after[0], after[h]
            o, c = opens.get(d0), closes.get(d1)
            if not o or not c:
                continue
            e[f"d{h}"] = round((c / o - 1) * 100, 4)
        if "d20" in e or "d5" in e:
            filled += 1
    print(f"  リターンを入れた {filled}件")
    return filled


# ---------------------------------------------------------------- 集計
def score(store):
    rows = [e for e in store if "d5" in e or "d20" in e]

    # 同じ公表日・同じ母集団の平均を基準にする（日経ではない）
    rel = {}
    for h in H:
        tag = f"d{h}"
        base = defaultdict(list)
        for r in rows:
            if tag in r:
                base[r["pub"]].append(r[tag])
        rel[tag] = {k: sum(v) / len(v) for k, v in base.items()}

    def prep(r, tag):
        sign = -1 if r["event"] in DOWN_IS_WIN else 1
        raw = r[tag]
        return (raw * sign, (raw - rel[tag][r["pub"]]) * sign)

    def summarize(sub):
        out = {"n": len(sub)}
        for h in H:
            tag = f"d{h}"
            s = [r for r in sub if tag in r]
            if not s:
                continue
            al, rl = zip(*(prep(r, tag) for r in s))
            wins = sum(1 for v in al if v > 0)
            rwin = sum(1 for v in rl if v > 0)
            out[tag] = {
                "n": len(s),
                "win_rate": round(100 * wins / len(s), 1),
                "avg": round(sum(al) / len(al), 3),
                "rel_win_rate": round(100 * rwin / len(rl), 1),
                "avg_vs_universe": round(sum(rl) / len(rl), 3),
                "p_value": round(binom_p(rwin, len(rl)), 4),
            }
        return out

    by_seller, by_event = defaultdict(list), defaultdict(list)
    for r in rows:
        by_seller[r["seller"]].append(r)
        by_event[r["event"]].append(r)

    sellers = []
    for s, sub in by_seller.items():
        d = summarize(sub)
        n20 = d.get("d20", {}).get("n", 0)
        if n20 < MIN_N:
            # 件数不足は数字を出さない。存在だけ残す
            sellers.append({"seller": s, "n": d["n"], "low_n": True})
            continue
        d["seller"] = s
        d["low_n"] = False
        sellers.append(d)
    # 表示は件数順（勝率順にすると「おすすめ順」に見える）
    sellers.sort(key=lambda x: -(x.get("d20", {}).get("n") or x.get("n") or 0))

    pubs = sorted({r["pub"] for r in rows})
    return {
        "updated": datetime.now(JST).isoformat(timespec="seconds"),
        "source": "JPX「空売り残高に関する情報」＋日足（Yahoo Finance）",
        "headline": "20日後に下がったのは約半分（母集団と同じ）",
        "method": ("起点は公表日の翌営業日の始値。+5/+20営業日の終値までのリターン。"
                   "新規・増加は下落で勝ち、減少・解消は上昇で勝ち。"
                   "基準は日経平均ではなく「同じ公表日に報告があった銘柄の平均」。"
                   "空売り残高0.5%以上の銘柄は中小型に偏り、日経と比べると"
                   "種別に関係なく+3%ほど上振れてしまうため。"),
        "caveats": [
            "起点は公表日（計算日の2営業日後）。読者が知り得た時点から測っています",
            f"期間は{pubs[0]}〜{pubs[-1]}の{len(pubs)}公表日ぶん。JPXの公開が約3か月分のため短い",
            "上場廃止・統合した銘柄は株価が取れず集計から外れます（生存バイアス）",
            "勝率が高くても平均リターンがマイナスのことがあります",
            "この表は「この機関に乗れば勝てる」という意味ではありません。件数順に並べています",
        ],
        "period": {"from": pubs[0], "to": pubs[-1], "publish_days": len(pubs)},
        "min_n": MIN_N,
        "overall": summarize(rows),
        "by_event": {k: summarize(v) for k, v in sorted(by_event.items())},
        "by_seller": sellers,
    }


def main():
    EVENTS.parent.mkdir(parents=True, exist_ok=True)
    store = []
    if EVENTS.exists():
        store = json.loads(EVENTS.read_text(encoding="utf-8"))
    collect_events(store)
    fill_returns(store)
    EVENTS.write_text(json.dumps(store, ensure_ascii=False, separators=(",", ":")),
                      encoding="utf-8")

    out = score(store)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")),
                   encoding="utf-8")
    o = out["overall"]
    print(f"\nOK karauri_score.json / イベント {len(store)}件")
    for h in H:
        d = o.get(f"d{h}")
        if d:
            print(f"  {h:2d}日: n={d['n']:6d} 母集団比勝率{d['rel_win_rate']:5.1f}% "
                  f"母集団比{d['avg_vs_universe']:+.3f}% p={d['p_value']}")
    shown = sum(1 for s in out["by_seller"] if not s["low_n"])
    print(f"  機関: 表示{shown}社 / 件数不足{len(out['by_seller'])-shown}社")


if __name__ == "__main__":
    main()
