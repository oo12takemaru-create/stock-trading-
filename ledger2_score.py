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


# ════════════════════════════════════════ 決算の前（pre_earnings_up_10 / _5）の答え合わせ（2026-11-19 追加）
# 既存の窓（1・5・20・60）とは別の欄。data/ledger2/scores_pre/YYYY-MM.jsonl に1件1行（追記のみ・event_id で1回だけ）。
# 既存の答え合わせ（main）は変えていない。`--pre-earnings` のときだけ、こちらを走らせる。
#
#   窓    = 基準価格（base_date の始値＝予定日の N 営業日前）→ 実際の決算発表の日の前営業日の終値
#           （発表が場中・引け後・寄り前のどれでも、発表の日の前営業日まで。発表の日の値動きは入れない）
#   答え  = raw_ret（生）・ret（対TOPIX＝raw − 1306 の raw）・vs_all（全銘柄との差＝raw − 同じ窓の全銘柄の raw の平均）
#   全銘柄 = data/bars の起点の日と終点の日に値がある内国株（1306・指数を除く）の等ウエイト平均。
#            期間中に分割の権利落ちがある銘柄・1本で 0.4倍未満/2.5倍超の銘柄は外す。data/bars が欠けていれば vs_all は null
#   予定どおりか: 実際の発表の日 == 記録したときの予定日 → on_schedule=true。ずれたら false と、ずれ（営業日・符号つき）を残す
#   実際の発表が基準価格の日より前（前倒し）→ 窓が無いので status=no_window（答えなし・ずれた事実だけ）
#   予定日から 45暦日たっても発表が見つからない → status=invalid
PRE_GIVE_UP_DAYS = 45


def _all_mean(data, base_d, end_d, stocks):
    b0, b1 = data.bars(base_d), data.bars(end_d)
    if not b0 or not b1:
        return None, 0
    v = []
    for c, x in b0.items():
        if c in (TOPIX, N225) or c not in stocks or not x.get("open"):
            continue
        y = b1.get(c)
        if not y or not y.get("close"):
            continue
        if any(base_d < e <= end_d for e in data.split_ex(c)):
            continue
        r = y["close"] / x["open"]
        if r < JUMP_LO or r > JUMP_HI:
            continue
        v.append((r - 1) * 100)
    return (sum(v) / len(v), len(v)) if v else (None, 0)


def score_pre_earnings(out, data, today, now_hm, use_yf=True):
    out = Path(out)
    (out / "scores_pre").mkdir(parents=True, exist_ok=True)
    events = [e for f in sorted((out / "events").glob("*.jsonl")) for e in L.jsonl(f)
              if (e.get("event_type") or "").startswith("pre_earnings_up_")]
    done = {r.get("event_id") for f in sorted((out / "scores_pre").glob("*.jsonl")) for r in L.jsonl(f)}
    kd = data.kessan_dates()
    nowiso = datetime.now(JST).isoformat(timespec="seconds")
    todo = []
    for e in events:
        if e["event_id"] in done:
            continue
        p = e.get("event_params") or {}
        yotei = p.get("yotei") or (e.get("snapshot") or {}).get("yotei")
        # 実際の発表: 出来事の日より後の最初の決算短信
        act = next(((d0, t0) for d0, t0 in kd.get(e["code"], []) if d0 > e["date"]), None)
        if not act:
            if yotei and (date.fromisoformat(today) - date.fromisoformat(yotei)).days > PRE_GIVE_UP_DAYS:
                todo.append((e, yotei, None, None))
            continue
        end_d = L.prev_bizday(act[0])
        if not ready(end_d, today, now_hm):
            continue
        todo.append((e, yotei, act, end_d))
    print(f"決算の前の出来事 {len(events)}件 / 答え合わせの候補 {len(todo)}件")

    yf = YF(enabled=use_yf)
    need = set()
    spans = {}
    for e, yotei, act, end_d in todo:
        if not act or end_d < e["base_date"]:
            continue
        span = [e["base_date"]]
        while span[-1] < end_d:
            span.append(next_bizday(span[-1]))
        spans[e["event_id"]] = span
        for c in (e["code"], TOPIX):
            if series_from_bars(data, c, span) is None:
                need.add(c)
    if need:
        start = (min(date.fromisoformat(spans[k][0]) for k in spans) - timedelta(days=10)).isoformat()
        yf.fetch(need, start)

    stocks = None
    lines = []
    for e, yotei, act, end_d in todo:
        eid, b = e["event_id"], e["base_date"]
        p = e.get("event_params") or {}
        row = {"event_id": eid, "date": e["date"], "code": e["code"], "n": p.get("n"), "yotei": yotei,
               "base_date": b, "scored_at": nowiso}
        if not act:
            row.update(status="invalid", reason=f"予定日から{PRE_GIVE_UP_DAYS}暦日たっても決算短信が見つからない")
            lines.append(row)
            continue
        on = act[0] == yotei
        shift = 0 if on else (L.bizdays_after(yotei, act[0]) if act[0] > yotei else -L.bizdays_after(act[0], yotei))
        row.update(actual=act[0], actual_time=act[1] or None, on_schedule=on, shift_bizdays=shift, end_date=end_d)
        if end_d < b:
            row.update(status="no_window", reason="実際の発表が基準価格の日より前（前倒し）で、測る窓が無い")
            lines.append(row)
            continue
        span = spans[eid]
        ser = series_from_bars(data, e["code"], span)
        src = "bars"
        if ser is None:
            ser, src = yf.series(e["code"]), "yfinance"
        tser = series_from_bars(data, TOPIX, span) or yf.series(TOPIX)
        adj = []
        if src == "bars" and ser:
            ser, adj = split_adjust(ser, data.split_ex(e["code"]), b)
        if not ser or not tser or b not in ser or end_d not in ser or b not in tser or end_d not in tser:
            if (date.fromisoformat(today) - date.fromisoformat(b)).days > GIVE_UP_DAYS:
                row.update(status="invalid", reason="株価がそろわない")
                lines.append(row)
            continue                                   # まだ取れない → 次の回に
        jd = jump_check(ser, span)
        if jd:
            row.update(status="invalid", reason=f"説明できない値の飛び（{jd}）", src=src)
            lines.append(row)
            continue
        o, c = ser[b][0], ser[end_d][3]
        raw = (c / o - 1) * 100
        traw = (tser[end_d][3] / tser[b][0] - 1) * 100
        if stocks is None:
            stocks = data.stock_codes(today)
        am, n_all = _all_mean(data, b, end_d, stocks)
        row.update(status="scored", bars_n=len(span), base_open=round(o, 4), end_close=round(c, 4),
                   raw_ret=round(raw, 3), topix_ret=round(traw, 3), ret=round(raw - traw, 3),
                   all_ret=round(am, 3) if am is not None else None, n_all=n_all,
                   vs_all=round(raw - am, 3) if am is not None else None, src=src)
        if adj:
            row["split_adj"] = adj
        lines.append(row)

    by_month = {}
    for r in lines:
        by_month.setdefault(r["date"][:7], []).append(r)
    for ym, rows in sorted(by_month.items()):
        with open(out / "scores_pre" / f"{ym}.jsonl", "a", encoding="utf-8", newline="\n") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")
    from collections import Counter
    print(f"決算の前の答えを追記 {len(lines)}行 {dict(Counter(r['status'] for r in lines))}")
    return 0


def anomaly_lookback(event_type):
    """「データの異常」で invalid にするかを見る期間（暦日）。値動きの種類は検出の守りと同じ、取り込み（signal_*）は入力しだい"""
    if event_type in L.DIRTY_LOOKBACK:
        return L.DIRTY_LOOKBACK[event_type]
    if (event_type or "").startswith("signal_"):
        return L.DIRTY_LOOKBACK_SIGNAL.get(event_type, L.DIRTY_LOOKBACK_SIGNAL_DEFAULT)
    return None


def mark_anomalies(events, closed, data, nowiso):
    """当日記録（origin=live）の出来事のうち、判定に使った期間にその銘柄の日足が汚れている（px_sanity の検査で
    異常か捨てた日がある）ものに「データの異常」の invalid の行を作る（出来事の行は消さない・書き換えない）。
    もう invalid の行がある出来事は見ない。返り値は足す行の一覧（closed にも足す）"""
    rows = []
    for e in events:
        eid = e.get("event_id")
        if not eid or eid in closed or e.get("origin") != "live" or e.get("code") in (None, "MKT"):
            continue
        lb = anomaly_lookback(e.get("event_type"))
        if lb is None:
            continue
        st = data.px_dirty(e["date"], lb).get(e["code"])
        if st is None:
            continue
        rows.append({"event_id": eid, "date": e["date"], "status": "invalid", "anomaly": "px_sanity",
                     "reason": f"データの異常（株価の検査で {e['code']} の日足に {st}。判定に使った{lb}暦日の中）",
                     "scored_at": nowiso})
        closed.add(eid)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--today", help="今日の日付を差し替える（試験用・この日の大引け後として扱う）")
    ap.add_argument("--docs", help="docs/ を差し替える（試験用）")
    ap.add_argument("--bars", help="data/bars を差し替える（試験用）")
    ap.add_argument("--no-yf", action="store_true", help="yfinance を使わない（data/bars だけ）")
    ap.add_argument("--pre-earnings", action="store_true",
                    help="決算の前（pre_earnings_up_*）の答え合わせだけを走らせる（scores_pre/・既存の窓とは別）")
    a = ap.parse_args()
    if a.pre_earnings:
        now = datetime.now(JST)
        today, now_hm = (a.today, "23:59") if a.today else (now.date().isoformat(), now.strftime("%H:%M"))
        data = L.Sources(docs=a.docs, bars=a.bars) if (a.docs or a.bars) else L.Sources()
        return score_pre_earnings(a.out, data, today, now_hm, use_yf=not a.no_yf)

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
    nowiso0 = now.isoformat(timespec="seconds")
    anomalies = mark_anomalies(events, closed, data, nowiso0)     # 株価の汚れ（2026-11-21）
    if anomalies:
        print(f"データの異常で invalid: {len(anomalies)}件 {[r['event_id'] for r in anomalies][:10]}")

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

    lines, nowiso = list(anomalies), nowiso0
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
