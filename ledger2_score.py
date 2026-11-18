# -*- coding: utf-8 -*-
"""出来事の台帳（v2）の答え合わせ → data/ledger2/scores/YYYY-MM.jsonl（追記のみ）

■ 何か（検証台帳 設計書 v1 段階2・2026-11-18）
  data/ledger2/events/ の出来事ごとに、窓（1・5・20・60営業日）が満了したら答えを1行足す。
  月ファイルは丸ごと書き直さない（events も scores も追記だけ）。scores の月は出来事の date の月。

■ 測り方（設計書 5章）
  起点   = base_date（その情報を知り得たあとの最初の始値。events の行に入っている）の始値
           引け後・立会中に出た情報 → 翌営業日の始値 / 寄り付き前に出た情報 → その日の始値
  終点   = 起点の日から h 営業日後の終値（既存の ledger_score.py と同じ数え方。h=1 は起点の翌営業日の終値）
  raw_ret_h  = 終点 ÷ 起点 − 1（%）
  ret_h      = raw_ret_h − 同じ日の 1306.T（TOPIX 連動 ETF）の raw（%）＝ 対 TOPIX の超過
  max_dd_20  = 起点の日から20営業日後までの安値の最小 ÷ 起点 − 1（%）。h=20 の行に入れる
  市場全体（code=MKT）は日経平均（^N225）で測る（既存のシグナル台帳と同じ）。

■ 株価の取り方
  1) data/bars/YYYY/YYYY-MM-DD.csv.gz（daily_bars.py が毎夕1日1ファイル・その日の夕方に見えていた値）。
     起点〜終点の営業日のファイルが全部そろっているときだけ使う。
     分割の調整: 期間中（起点の翌日〜終点）に権利落ち日がある銘柄は、権利落ち日以降の値に分割比率を掛けて
     起点と同じ物差しにする（docs/splits.json の ex・ratio）。
  2) そろっていなければ yfinance（auto_adjust=False。Yahoo は分割を遡って直した値を返すので分割調整済み・配当は未調整）。
  1日で値が 0.4倍未満／2.5倍超に飛ぶ足が期間中にあれば、分割の情報が無い併合などとみなし invalid にする（黙って間違えない）。

■ invalid（status=invalid の行を1行足して、その出来事の答え合わせを終える）
  - 上場廃止（docs/delisted.json で廃止済み）で、要る日の株価が無い
  - 起点から 120暦日たっても株価がそろわない（データ欠損）
  - 上の説明できない値の飛び

■ 二重に書かない
  (event_id, h) の行がもうあれば書かない。invalid の行があればその出来事は見ない。同じ日に何回走っても同じ。

使い方:
  python -X utf8 ledger2_score.py
  python -X utf8 ledger2_score.py --out <scratchpad> --today 2026-11-18   # 試験（本番の場所に書かない）
"""
import argparse
import json
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import ledger2_detect as L
from jp_bizday import next_bizday

HERE = Path(__file__).resolve().parent
OUT = HERE / "data" / "ledger2"
JST = timezone(timedelta(hours=9))
H = (1, 5, 20, 60)
GIVE_UP_DAYS = 120
JUMP_LO, JUMP_HI = 0.4, 2.5
TOPIX, N225 = "1306", "^N225"


def bizdays_from(d, n):
    """d から n 営業日先までの営業日（d を含む・n+1 個）"""
    out = [d]
    while len(out) < n + 1:
        out.append(next_bizday(out[-1]))
    return out


def ready(end_d, today, now_hm):
    """終点の日の終値が出ているか（大引けのあと）"""
    return end_d < today or (end_d == today and now_hm >= "16:00")


# ──────────────────────────────────────── 価格の系列 {date: (open, high, low, close)}
def series_from_bars(data, code, days):
    """days（営業日の並び）の足を data/bars から。1日でもファイルが無ければ None（＝yfinance に回す）"""
    out = {}
    for d in days:
        b = data.bars(d)
        if b is None:
            return None
        x = b.get(code)
        if x and x.get("open") and x.get("close"):
            out[d] = (x["open"], x.get("high") or x["close"], x.get("low") or x["close"], x["close"])
    return out


def split_adjust(ser, ex_map, base_d):
    """起点より後の権利落ち日以降の値に比率を掛ける（起点と同じ物差しに）。使った分割を返す"""
    used = sorted((e, r) for e, r in ex_map.items() if e > base_d and r and r > 0)
    if not used:
        return ser, []
    out = {}
    for d, (o, h, l, c) in ser.items():
        f = 1.0
        for e, r in used:
            if d >= e:
                f *= r
        out[d] = (o * f, h * f, l * f, c * f)
    return out, [{"ex": e, "ratio": r} for e, r in used]


class YF:
    """yfinance をまとめて1回ずつ取る（足りないものだけ）"""

    def __init__(self, enabled=True):
        self.enabled = enabled
        self.px = {}

    def ticker(self, code):
        if code == N225:
            return N225
        return f"{code}.T"

    def fetch(self, codes, start):
        if not self.enabled:
            return
        import pandas as pd
        import yfinance as yf
        tks = sorted({self.ticker(c) for c in codes} - set(self.px))
        for i in range(0, len(tks), 60):
            chunk = tks[i:i + 60]
            try:
                df = yf.download(" ".join(chunk), start=start, progress=False, auto_adjust=False,
                                 threads=False, group_by="ticker")
            except Exception as e:
                print(f"  価格取得失敗: {e}", file=sys.stderr)
                continue
            for t in chunk:
                try:
                    sub = df[t] if isinstance(df.columns, pd.MultiIndex) else df
                    sub = sub.dropna(subset=["Open", "Close"])
                    self.px[t] = {k.strftime("%Y-%m-%d"): (float(r["Open"]), float(r["High"]), float(r["Low"]), float(r["Close"]))
                                  for k, r in sub.iterrows() if r["Open"] > 0 and r["Close"] > 0}
                except Exception:
                    self.px[t] = {}
            time.sleep(1.0)

    def series(self, code):
        return self.px.get(self.ticker(code))


# ──────────────────────────────────────── 1件の答え
def measure(ser, days, h, base_d):
    """ser の base_d の始値 → days[h] の終値。足りなければ None"""
    if base_d not in ser:
        return None
    end_d = days[h]
    if end_d not in ser:
        return None
    o = ser[base_d][0]
    c = ser[end_d][3]
    return o, c, end_d


def jump_check(ser, days_upto):
    prev = None
    for d in days_upto:
        if d not in ser:
            continue
        c = ser[d][3]
        if prev:
            r = c / prev
            if r < JUMP_LO or r > JUMP_HI:
                return d
        prev = c
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--today", help="今日の日付を差し替える（試験用・この日の大引け後として扱う）")
    ap.add_argument("--docs", help="docs/ を差し替える（試験用）")
    ap.add_argument("--bars", help="data/bars を差し替える（試験用）")
    ap.add_argument("--no-yf", action="store_true", help="yfinance を使わない（data/bars だけ）")
    a = ap.parse_args()

    out = Path(a.out)
    (out / "scores").mkdir(parents=True, exist_ok=True)
    now = datetime.now(JST)
    if a.today:
        today, now_hm = a.today, "23:59"
    else:
        today, now_hm = now.date().isoformat(), now.strftime("%H:%M")
    data = L.Sources(docs=a.docs, bars=a.bars) if (a.docs or a.bars) else L.Sources()

    events = []
    for f in sorted((out / "events").glob("*.jsonl")):
        events += L.jsonl(f)
    done, closed = set(), set()
    for f in sorted((out / "scores").glob("*.jsonl")):
        for r in L.jsonl(f):
            if r.get("status") == "invalid":
                closed.add(r["event_id"])
            elif r.get("h") is not None:
                done.add((r["event_id"], r["h"]))

    delisted = {k: v for k, v in ((L.jload(data.docs / "delisted.json", {}) or {}).get("items") or {}).items()
                if v.get("status") == "delisted"}

    # 答えを出す候補（終点の終値が出ている窓があるもの）
    todo = []
    for e in events:
        if e.get("event_id") in closed or not e.get("base_date"):
            continue
        b = e["base_date"]
        if b > today:
            continue
        days = bizdays_from(b, max(H))
        hs = [h for h in H if (e["event_id"], h) not in done and ready(days[h], today, now_hm)]
        gave_up = (date.fromisoformat(today) - date.fromisoformat(b)).days > GIVE_UP_DAYS
        if hs or gave_up:
            todo.append((e, days, hs, gave_up))
    print(f"出来事 {len(events)}件 / 答え合わせの候補 {len(todo)}件")

    # 価格: まず data/bars、足りないものは yfinance
    yf = YF(enabled=not a.no_yf)
    need_yf = set()
    bar_series = {}
    for e, days, hs, _ in todo:
        code = N225 if e["code"] == "MKT" else e["code"]
        span = days[:(max(hs) if hs else 0) + 1]
        for c in (code, TOPIX):
            key = (c, span[0], span[-1])
            if key not in bar_series:
                bar_series[key] = series_from_bars(data, c, span)
            if bar_series[key] is None:
                need_yf.add(c)
    if need_yf:
        start = (min(date.fromisoformat(e["base_date"]) for e, *_ in todo) - timedelta(days=10)).isoformat()
        print(f"  data/bars に無い分を yfinance で取る: {len(need_yf)}銘柄（{start}〜）")
        yf.fetch(need_yf, start)

    def get_series(c, span):
        s = bar_series.get((c, span[0], span[-1]))
        if s is not None:
            return s, "bars"
        ys = yf.series(c)
        return (ys, "yfinance") if ys is not None else (s, "bars")

    lines, nowiso = [], now.isoformat(timespec="seconds")
    for e, days, hs, gave_up in todo:
        eid, b = e["event_id"], e["base_date"]
        code = N225 if e["code"] == "MKT" else e["code"]
        span = days[:(max(hs) if hs else 0) + 1]
        ser, src = get_series(code, span)
        tser, tsrc = get_series(TOPIX, span)
        ser = ser or {}
        adj = []
        if src == "bars" and code not in (N225, TOPIX):
            ser, adj = split_adjust(ser, data.split_ex(code), b)
        wrote_any = False
        jd = jump_check(ser, span)
        if jd:
            lines.append({"event_id": eid, "date": e["date"], "status": "invalid",
                          "reason": f"説明できない値の飛び（{jd}）。分割の情報が無い併合などの可能性", "src": src,
                          "scored_at": nowiso})
            continue
        for h in hs:
            m = measure(ser, days, h, b)
            t = measure(tser or {}, days, h, b)
            if not m or not t:
                continue
            o, c, end_d = m
            raw = (c / o - 1) * 100
            traw = (t[1] / t[0] - 1) * 100
            row = {"event_id": eid, "date": e["date"], "h": h, "base_date": b, "base_open": round(o, 4),
                   "end_date": end_d, "end_close": round(c, 4), "raw_ret": round(raw, 3),
                   "topix_ret": round(traw, 3), "ret": round(raw - traw, 3),
                   "status": "scored" if h == max(H) else "open", "src": src, "scored_at": nowiso}
            if code == N225:
                row["bench"] = "^N225"
            if adj:
                row["split_adj"] = adj
            if h == 20:
                lows = [ser[d][2] for d in days[:21] if d in ser]
                row["max_dd_20"] = round((min(lows) / o - 1) * 100, 3) if lows else None
            lines.append(row)
            done.add((eid, h))
            wrote_any = True
        missing = [h for h in H if (eid, h) not in done]
        if missing and not wrote_any:
            dl = delisted.get(e["code"])
            if dl and dl.get("d") and dl["d"] <= days[missing[0]]:
                lines.append({"event_id": eid, "date": e["date"], "status": "invalid",
                              "reason": f"上場廃止（{dl['d']}・{dl.get('reason') or ''}）", "missing_h": missing,
                              "scored_at": nowiso})
            elif gave_up:
                lines.append({"event_id": eid, "date": e["date"], "status": "invalid",
                              "reason": f"起点から{GIVE_UP_DAYS}暦日たっても株価がそろわない", "missing_h": missing,
                              "scored_at": nowiso})

    by_month = {}
    for r in lines:
        by_month.setdefault(r["date"][:7], []).append(r)
    for ym, rows in sorted(by_month.items()):
        with open(out / "scores" / f"{ym}.jsonl", "a", encoding="utf-8", newline="\n") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")
    n_inv = sum(1 for r in lines if r.get("status") == "invalid")
    print(f"答えを追記 {len(lines) - n_inv}行 / invalid {n_inv}行")
    return 0


if __name__ == "__main__":
    sys.exit(main())
