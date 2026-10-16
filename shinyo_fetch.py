# -*- coding: utf-8 -*-
"""信用取引の需給（信用買い残・信用倍率）→ docs/shinyo.json（株レーダー kaburadar.jp）

JPXが毎週公表する「信用取引現在高」を集計する。
著書『暴落は、減衰する』第2章の傾斜計③「信用の膨張」の日本版にあたる。

■ 信用評価損益率は載せない（重要）
  本書は日本の傾斜計として「信用評価損益率」を挙げているが、この指標は
  各建玉の取得価格が必要で、公表データからは機械的に算出できない。
  松井証券などが自社顧客のデータや独自推計で公表しているもので、
  当サイトの「公表データを機械的に集計する。推計はしない」という方針に合わない。
  → 代わりに、本書がもう一つ挙げている「量」の指標＝信用買い残を扱う。
     （米国のマージンデットに対応する日本のデータ）

■ JPXは直近5週分しか置いていない
  前年比を出すには履歴が要るので、docs/shinyo_state.json に毎週ぶんを蓄積する。
  貯まるまでは「N週分を蓄積中」と正直に出す。前週比はファイル自体に入っている。

■ 単位
  株数＝千株 / 金額＝百万円（JPXの原単位のまま保持し、表示側で換算する）
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
OUT = DOCS / "shinyo.json"
STATE = DOCS / "shinyo_state.json"

BASE = "https://www.jpx.co.jp"
PAGE = "/markets/statistics-equities/margin/04.html"
UA = {"User-Agent": "Mozilla/5.0 (compatible; kaburadar.jp/1.0)"}

# 二市場計の行と、委託ぶんの列（ヘッダ行5で確認済み）
ROW_SHARES, ROW_VALUE = 6, 7
COL = {"sell": 3, "sell_chg": 4, "buy": 5, "buy_chg": 6}

# 水準の「高い/低い」は固定の閾値ではなく、蓄積した履歴の中での位置で判断する。
# 市場全体の信用倍率が何倍なら過熱かを裏付ける手元データがないため、
# 根拠のない閾値で断定しない（26週たまるまでは数値の提示に留める）。
MIN_WEEKS_FOR_LEVEL = 26


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


def find_files(html):
    """申込み日(YYYYMMDD) → ファイルURL。

    ★2026-09-25 申込み分から JPX の様式が変わった★
      旧: mtseisanYYYYMMDD00.xls（委託・自己・合計を横に並べた表）
      新: YYYYMMDD_mtcurrent.xlsx（制度信用と一般信用を縦に分けた表）
      旧の正規表現だけだと新しいファイルを1つも拾えず、9/18 のまま4週止まっていた
      （ワークフローは「成功」し updated も毎日進んだので、鮮度の監視では気づけなかった）。
      同じ日の両方があれば新様式を使う。
    """
    out = {}
    for m in re.finditer(r'href="(/markets/statistics-equities/margin/[^"]*?mtseisan(\d{8})\d*\.xls)"', html):
        out[m.group(2)] = BASE + m.group(1)
    for m in re.finditer(r'href="(/markets/statistics-equities/margin/[^"]*?/(\d{8})_mtcurrent\.xlsx)"', html):
        out[m.group(2)] = BASE + m.group(1)
    return out


def num(v):
    v = pd.to_numeric(v, errors="coerce")
    return None if pd.isna(v) else float(v)


def parse_new(d):
    """新様式（2026-09-25〜）。二市場の「委託」＝制度信用の委託＋一般信用の委託。
    旧様式の「委託」（制度＋一般）と同じものになることを、9/25 の前週比から確かめた
    （9/25 の委託の買残金額 − 前週比 ＝ 6,453,374百万円 ≒ 旧様式 9/18 の 6,453,287百万円）。
    列は 4:売残高 5:前週比 6:買残高 7:前週比（二市場）"""
    v = d.iloc[0, 1]
    if not hasattr(v, "year"):
        raise ValueError("申込み日を読み取れない（新様式）")
    date = f"{v.year}-{v.month:02d}-{v.day:02d}"
    acc = {"sh": {k: 0.0 for k in COL}, "va": {k: 0.0 for k in COL}}
    hits = {"sh": 0, "va": 0}
    cur = None
    for i in range(len(d)):
        if "委託" in str(d.iloc[i, 2]):
            cur = "委託"
        elif str(d.iloc[i, 2]) not in ("nan", "None"):
            cur = None
        if pd.notna(d.iloc[i, 1]) and "合計" in str(d.iloc[i, 1]):
            break                      # 信用取引残高合計 以降（自己を含む・貸借など）は使わない
        unit = str(d.iloc[i, 3])
        if cur != "委託" or not ("株数" in unit or "金額" in unit):
            continue
        key = "sh" if "株数" in unit else "va"
        for k, c in zip(("sell", "sell_chg", "buy", "buy_chg"), (4, 5, 6, 7)):
            x = num(d.iloc[i, c])
            if x is None:
                raise ValueError(f"新様式の{k}が読めない（行{i}）")
            acc[key][k] += x
        hits[key] += 1
    if hits["sh"] != 2 or hits["va"] != 2:
        raise ValueError(f"新様式の委託の行が想定と違う（株数{hits['sh']}・金額{hits['va']}。制度・一般の2行ずつのはず）")
    return date, acc["sh"], acc["va"]


def parse(data):
    d = pd.read_excel(BytesIO(data), sheet_name=0, header=None)
    if not re.search(r"\d{4}/\d{1,2}/\d{1,2}", str(d.iloc[0, 0])):
        date, sh, va = parse_new(d)
        return _rec(date, sh, va)
    # 「信用取引現在高（2026/8/7申込み現在）」から申込み日を取る
    m = re.search(r"(\d{4})/(\d{1,2})/(\d{1,2})", str(d.iloc[0, 0]))
    if not m:
        raise ValueError("申込み日を読み取れない")
    date = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"

    sh = {k: num(d.iloc[ROW_SHARES, c]) for k, c in COL.items()}
    va = {k: num(d.iloc[ROW_VALUE, c]) for k, c in COL.items()}
    return _rec(date, sh, va)


def _rec(date, sh, va):
    if not sh["buy"] or not va["buy"]:
        raise ValueError("二市場計の買残高が読めない")
    return {
        "d": date,
        "buy_sh": sh["buy"], "sell_sh": sh["sell"],
        "buy_sh_chg": sh["buy_chg"], "sell_sh_chg": sh["sell_chg"],
        "buy_va": va["buy"], "sell_va": va["sell"],
        "buy_va_chg": va["buy_chg"], "sell_va_chg": va["sell_chg"],
        # 信用倍率は株数ベースが一般的
        "ratio": round(sh["buy"] / sh["sell"], 2) if sh["sell"] else None,
    }


def load_state():
    if STATE.exists():
        try:
            return json.loads(STATE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"weeks": {}}


def yoy(hist, cur):
    """52週前（±3週で最も近いもの）と比べる。無ければ None"""
    if not cur:
        return None, None
    target = datetime.strptime(cur["d"], "%Y-%m-%d").date() - timedelta(days=364)
    best, gap = None, 99
    for w in hist:
        dt = datetime.strptime(w["d"], "%Y-%m-%d").date()
        g = abs((dt - target).days)
        if g <= 21 and g < gap:
            best, gap = w, g
    if not best or not best["buy_va"]:
        return None, None
    return round((cur["buy_va"] / best["buy_va"] - 1) * 100, 1), best["d"]


def main():
    html = http(BASE + PAGE).decode("utf-8", "replace")
    links = find_files(html)
    if not links:
        print("JPXから信用取引現在高のファイルを見つけられませんでした", file=sys.stderr)
        sys.exit(1)

    state = load_state()
    weeks = state["weeks"]
    added = 0
    for key in sorted(links):
        try:
            rec = parse(http(links[key]))
        except Exception as e:
            print(f"取得/解析失敗 {key}: {e}", file=sys.stderr)
            continue
        if rec["d"] not in weeks:
            added += 1
        weeks[rec["d"]] = rec
        time.sleep(0.5)

    if not weeks:
        print("有効なデータがありません", file=sys.stderr)
        sys.exit(1)

    hist = [weeks[k] for k in sorted(weeks)]
    cur = hist[-1]
    y, ybase = yoy(hist[:-1], cur)

    # 判定（すべて公表値そのもの。推計はしない）
    r = cur["ratio"]
    pct = None
    if r is None:
        level = "unknown"
        msg = "信用倍率を算出できませんでした。"
    elif len(hist) < MIN_WEEKS_FOR_LEVEL:
        level = "measuring"
        msg = (f"信用倍率{r:.2f}倍（買い残が売り残の{r:.1f}倍）。信用買いは6ヶ月以内に決済されるため、"
               f"買い残は将来の売り圧力として残ります。"
               f"なお「この水準が高いのか低いのか」の判定は、履歴が{MIN_WEEKS_FOR_LEVEL}週分たまってから"
               f"出します（現在{len(hist)}週）。根拠のない閾値で過熱と断定しないためです。")
    else:
        rs = sorted(w["ratio"] for w in hist if w["ratio"] is not None)
        pct = round(sum(1 for x in rs if x <= r) / len(rs) * 100)
        if pct >= 80:
            level = "hot"
            where = f"蓄積した{len(rs)}週の中で上位{100-pct}%"
            tail = "買い方への偏りが、この期間では大きいほうです。"
        elif pct <= 20:
            level = "cold"
            where = f"蓄積した{len(rs)}週の中で下位{pct}%"
            tail = "売り残が相対的に多く、買い戻しが支えになりやすい形です。"
        else:
            level = "mid"
            where = f"蓄積した{len(rs)}週の中で中位（下から{pct}%）"
            tail = "極端な水準ではありません。"
        msg = (f"信用倍率{r:.2f}倍（買い残が売り残の{r:.1f}倍）。{where}の水準です。{tail}"
               f"信用買いは6ヶ月以内に決済されるため、買い残は将来の売り圧力として残ります。")

    data = {
        "updated": datetime.now(JST).isoformat(timespec="seconds"),
        "date": cur["d"],
        "level": level,
        "message": msg,
        "ratio_pct": pct,
        "min_weeks_for_level": MIN_WEEKS_FOR_LEVEL,
        "buy_oku": round(cur["buy_va"] / 100, 0),          # 百万円 → 億円
        "sell_oku": round(cur["sell_va"] / 100, 0),
        "buy_chg_oku": round(cur["buy_va_chg"] / 100, 0) if cur["buy_va_chg"] is not None else None,
        "sell_chg_oku": round(cur["sell_va_chg"] / 100, 0) if cur["sell_va_chg"] is not None else None,
        "buy_man_kabu": round(cur["buy_sh"] / 10, 0),      # 千株 → 万株
        "sell_man_kabu": round(cur["sell_sh"] / 10, 0),
        "ratio": r,
        "yoy": y, "yoy_base": ybase,
        "weeks_stored": len(hist),
        "history": [{"d": w["d"], "buy": round(w["buy_va"] / 100, 0),
                     "sell": round(w["sell_va"] / 100, 0), "r": w["ratio"]} for w in hist[-60:]],
        "note": ("JPX「信用取引現在高」の二市場計・委託分。毎週金曜申込み分が翌週火曜ごろ公表されます。"
                 "信用評価損益率は各建玉の取得価格が必要で公表データから機械的に算出できないため、"
                 "当サイトでは扱っていません。"),
    }
    DOCS.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    state["weeks"] = weeks
    STATE.write_text(json.dumps(state, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    print(f"OK shinyo.json: {cur['d']}申込み分 / 新規{added}週 / 蓄積{len(hist)}週")
    print(f"  信用買い残 {data['buy_oku']:,.0f}億円（前週比 {data['buy_chg_oku']:+,.0f}億円）")
    print(f"  信用売り残 {data['sell_oku']:,.0f}億円（前週比 {data['sell_chg_oku']:+,.0f}億円）")
    print(f"  信用倍率 {r}倍 → {level}")
    print(f"  前年比: {y if y is not None else '（52週分たまるまで算出できません）'}")


if __name__ == "__main__":
    main()
