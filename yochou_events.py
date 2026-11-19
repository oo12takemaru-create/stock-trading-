# -*- coding: utf-8 -*-
"""マイボードの「確認が必要な銘柄」用: その日（と直前の記録日）に台帳 v2 が記録した出来事を銘柄ごとに（2026-11-19）

■ 入力  data/ledger2/events/*.jsonl（当日記録 origin=live。imported・backfill は読まない）
■ 出力  docs/yochou/events_today.json（サイトの hold.html・トップのマイボードの1行が読む）
  {
    "v": 1, "updated": ISO, "date": 最新の記録日, "prev": その前の記録日, "dates": [prev, date],
    "items":  {コード: [{"d": 日付, "type": 種類, "label": 種類名, "note": 理由の一言}]},
    "market": [{"d", "type", "label", "note"}]   … 市場全体（温度計の段階変化・暴落の傾斜計）。code なし
  }
  note は理由の一言に使う派生値だけ（出来高の倍率・信用買残の前回比%・始値の前日比%・開示の時刻など）。
  JPX の残高そのもの・機関名・株価そのものなど生の値は入れない。
  既存シグナル台帳の取り込み分（event_type が signal_* ＝宝探し・急騰急落の一覧など）は入れない。

■ 失敗しても既存の公開（today.json など）は止めない: ledger-daily.yml で yochou.py の後の別ステップ・continue-on-error。

使い方:
  python -X utf8 yochou_events.py                         # 毎営業日（ledger-daily.yml）
  python -X utf8 yochou_events.py --date 2026-11-19 --out <file>   # 試験（本番の場所に書かない）
"""
import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data" / "ledger2"
OUT = HERE / "docs" / "yochou" / "events_today.json"
JST = timezone(timedelta(hours=9))

STAGE_JA = {"PANIC": "総崩れ", "BEARISH": "弱気", "NEUTRAL": "中立", "BULLISH": "強気"}
GAUGE_JA = {"watch": "注意", "prepare": "備え"}
MKT_TYPES = ("thermo_stage_change", "gauge_stage_up")

# 種類名（yochou.py の label_of と同じ。import できないときの控え）
LABEL = {
    "volume_surge": "出来高の急増", "stop_high": "ストップ高（終値）", "stop_low": "ストップ安（終値）",
    "stop_high_touch": "一時ストップ高", "stop_low_touch": "一時ストップ安",
    "high52_close": "終値で52週高値", "low52_close": "終値で52週安値",
    "ytd_high_close": "終値で年初来高値", "ytd_low_close": "終値で年初来安値",
    "gap_up": "窓を開けて高く寄り付き", "gap_down": "窓を開けて安く寄り付き",
    "earnings_revision_up": "業績見通しの上方修正", "earnings_revision_down": "業績見通しの下方修正",
    "dividend_up": "増配", "dividend_down": "減配", "buyback_decision": "自己株式の取得の決議",
    "stock_split": "株式分割の発表", "yutai_new": "株主優待の新設", "yutai_end": "株主優待の廃止",
    "margin_buy_surge": "信用買残の急増", "short_surge": "空売り残高の急増", "short_new_inst": "空売り残高の新しい機関",
    "thermo_stage_change": "温度計の段階が変わった", "gauge_stage_up": "暴落の傾斜計の段階が上がった",
    "pre_earnings_up_10": "見通しを上げていて、決算発表が近い", "pre_earnings_up_5": "見通しを上げていて、決算発表が近い",
}


def label_of(t):
    if t.startswith("pre_earnings_"):
        return LABEL.get(t, t)        # 台帳の種類名（「決算発表の10営業日前・…」）ではなく、理由として読める名前
    try:
        import yochou as Y            # 同じ名前を使う（無い・壊れていても止めない）
        return Y.label_of(t)
    except Exception:
        return LABEL.get(t, t)


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _pct(v, digits=0):
    """+38% / −12%（符号つき・派生値）"""
    s = f"{abs(v):.{digits}f}"
    return ("+" if v > 0 else "−" if v < 0 else "±") + s + "%"


def note_of(e):
    """理由の一言（派生値だけ）。作れなければ ""（サイトは label だけ出す）"""
    t, p = e.get("event_type"), e.get("event_params") or {}
    if t == "volume_surge":
        vr = _num(p.get("vr"))
        return f"出来高が20日平均の{vr:.1f}倍" if vr else ""
    if t == "margin_buy_surge":
        c = _num(p.get("chg"))
        return f"信用買残が前回比 {_pct(c * 100)}" if c is not None else ""
    if t == "short_surge":
        c = _num(p.get("max_chg"))
        n = p.get("n_inst")
        if c is None:
            return ""
        return f"機関の空売り残高の割合が前回比 {_pct(c * 100)}" + (f"（{int(n)}社）" if n and int(n) > 1 else "")
    if t == "short_new_inst":
        n = p.get("n_inst")
        return f"空売り残高の報告に新しい機関（{int(n)}社）" if n else "空売り残高の報告に新しい機関"
    if t in ("stop_high", "stop_low", "stop_high_touch", "stop_low_touch"):
        c = _num(p.get("chg"))
        return f"終値は前日比 {_pct(c, 1)}" if c is not None else ""
    if t in ("gap_up", "gap_down"):
        g = _num(p.get("gap_pct"))
        return f"始値が前日終値比 {_pct(g, 1)}" if g is not None else ""
    if t in ("earnings_revision_up", "earnings_revision_down", "dividend_up", "dividend_down",
             "buyback_decision", "stock_split", "yutai_new", "yutai_end"):
        tm = e.get("time")
        return f"{tm} に開示" if tm else ""
    if t.startswith("pre_earnings_up_"):
        # 出来事の日の引け後の時点: 予定日まであと (N+1) 営業日。上げ幅は前回の決算のあとの修正のうち最大
        left, y, pct = p.get("days_left"), p.get("yotei"), _num(p.get("rev_pct_max"))
        if not left or not y:
            return ""
        s = f"見通しを上げていて、決算発表まであと{int(left)}営業日（予定日 {int(y[5:7])}/{int(y[8:10])}）"
        return s + (f"・営業利益の見通しを {_pct(pct, 1)}" if pct is not None else "")
    if t == "thermo_stage_change":
        f, to = p.get("from"), p.get("to")
        if f and to:
            return f"相場の温度計が「{STAGE_JA.get(f, f)}」から「{STAGE_JA.get(to, to)}」に"
        return ""
    if t == "gauge_stage_up":
        f, to = p.get("from"), p.get("to")
        if to:
            return f"暴落の傾斜計が「{GAUGE_JA.get(to, to)}」の段階に" + (f"（前は「{GAUGE_JA.get(f, f)}」）" if f else "")
        return ""
    return ""


def load_live(data_dir, months):
    rows = []
    for m in months:
        p = Path(data_dir) / "events" / f"{m}.jsonl"
        if not p.exists():
            continue
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("origin") != "live" or r.get("imported_from"):
                    continue
                rows.append(r)
    return rows


def build(rows, day=None, now=None):
    """rows（events の行）→ 公開する dict。day を省くと最新の記録日"""
    rows = [r for r in rows if r.get("origin") == "live" and not r.get("imported_from")]
    dates = sorted({r["date"] for r in rows if r.get("date")})
    if day:
        dates = [d for d in dates if d <= day]
    if not dates:
        return None
    day = dates[-1]
    prev = dates[-2] if len(dates) > 1 else None
    want = {day} | ({prev} if prev else set())
    items, market, seen = {}, [], set()
    order = {d: i for i, d in enumerate(sorted(want, reverse=True))}     # 新しい日が先
    for r in sorted(rows, key=lambda r: (order.get(r.get("date"), 9), r.get("event_type") or "", r.get("code") or "")):
        t, d, code = r.get("event_type") or "", r.get("date"), r.get("code") or ""
        if d not in want or not t or t.startswith("signal_"):
            continue
        key = (d, code, t)
        if key in seen:
            continue
        seen.add(key)
        x = {"d": d, "type": t, "label": label_of(t), "note": note_of(r)}
        if code == "MKT" or t in MKT_TYPES:
            market.append(x)
        else:
            items.setdefault(code, []).append(x)
    return {"v": 1, "updated": now or datetime.now(JST).isoformat(timespec="seconds"),
            "date": day, "prev": prev, "dates": sorted(want),
            "note": "台帳（出来事の記録）から、銘柄ごとにその日と前の記録日の出来事だけ。理由の値は派生値（倍率・前回比など）。過去の統計ではありません。",
            "items": dict(sorted(items.items())), "market": market}


def months_for(day):
    if day:
        y, m = int(day[:4]), int(day[5:7])
    else:
        n = datetime.now(JST)
        y, m = n.year, n.month
    py, pm = (y - 1, 12) if m == 1 else (y, m - 1)
    return [f"{py}-{pm:02d}", f"{y}-{m:02d}"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(DATA))
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--date", help="この日までの最新の記録日（既定: 最新）")
    a = ap.parse_args()
    obj = build(load_live(a.data, months_for(a.date)), a.date)
    if not obj:
        print("当日記録が無いので書かない")
        return 0
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(out)
    n = sum(len(v) for v in obj["items"].values())
    print(f"events_today: {obj['prev']}〜{obj['date']}・{len(obj['items'])}銘柄・{n}件・市場 {len(obj['market'])}件・{out.stat().st_size:,}B")
    return 0


if __name__ == "__main__":
    sys.exit(main())
