# -*- coding: utf-8 -*-
"""シグナル台帳（記録だけ・追記のみ）→ docs/ledger/YYYY-MM.jsonl

■ 何のためか（統括方針 第3章の1本目・起動文 積上① 2026-10-07）
  株レーダーが出している判定（温度計・AI朝刊・3軸スコア・十倍株・着火・無料スキャナー・
  空売りの新規報告・大きく動いた銘柄）を、**その時点で**1件ずつ記録しておく。
  3か月後に「判定のあと実際どうなったか」を出すための土台。今は記録するだけで公開しない。

■ 新しい判定ロジックは作らない
  既にある出力（JSON・CSV）から拾うだけ。条件の値（cond）も出力にある値をそのまま写す。

■ 追記のみ
  一度書いた行は書き換えない。例外は採点（r5/r20/r60/final）だけで、ledger_score.py が埋める。
  訂正は元の行を残し、"<元id>-fix1" の行を "fix_of" つきで足す（この台帳の手作業の約束）。

■ 二重に書かない
  各行は「自然キー」（判定元・日付・銘柄など）で一意。何度走らせても同じ行は1回しか入らない。
  過去分（温度計の段階変化・AI朝刊の履歴・着火・signals_log.csv・movers_hist）は初回に遡って入る。

■ 差分で拾うもの（十倍株の新規）
  前回見た一覧を docs/ledger/state.json に持つ。state が無い初回は git の1つ前の版と比べる
  （それも無ければ、その回は記録せず一覧だけ覚える＝「全部新規」と誤って書かない）。

使い方: python ledger.py            # 記録（毎営業日・ledger-daily.yml）
"""
import csv
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).parent
DOCS = HERE / "docs"
LEDGER = DOCS / "ledger"
STATE = LEDGER / "state.json"
JST = timezone(timedelta(hours=9))

# 判定元の日本語名（型文 text に使う）
LABEL = {
    "regime": "温度計", "ai": "AI朝刊", "score3": "3軸スコア", "tenbagger": "十倍株スキャナー",
    "crash": "着火判定", "free_scanner": "無料スキャナー", "karauri_new": "空売り残高の集計",
    "movers": "値動きの記録",
}
# 着火判定の段階（crash_fetch.py の STAGES と同じ。点数→段階）
CRASH_STAGES = [(4, "calm", "平常圏"), (5, "warn", "警戒"), (99, "danger", "危険")]


def jload(p, default=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return default


def git_prev(rel, differs=None):
    """rel の、いまの版と中身が違う一つ前のコミットの版（無ければ None）。
    differs(old) が True を返す最初の版を返す"""
    try:
        shas = subprocess.run(["git", "log", "--format=%H", "-n", "40", "--", rel], cwd=HERE,
                              capture_output=True, text=True, check=True).stdout.split()
    except Exception:
        return None
    for sha in shas[1:]:
        try:
            raw = subprocess.run(["git", "show", f"{sha}:{rel}"], cwd=HERE,
                                 capture_output=True, check=True).stdout
            old = json.loads(raw.decode("utf-8"))
        except Exception:
            continue
        if differs is None or differs(old):
            return old
    return None


def hhmm(iso):
    try:
        return datetime.fromisoformat(iso).astimezone(JST).strftime("%H:%M")
    except Exception:
        return None


def jdate(iso):
    try:
        return datetime.fromisoformat(iso).astimezone(JST).date().isoformat()
    except Exception:
        return None


def rec(source, date, code, cond, nk, time=None, what=""):
    """1件。nk は二重記録を防ぐ自然キー。what は型文の「条件」部分"""
    d = date.replace("-", "")
    return {"_nk": nk, "date": date, "time": time, "source": source, "code": code,
            "cond": cond,
            "text": f"{int(d[4:6])}月{int(d[6:8])}日、{LABEL[source]}が{what}を検知",
            "r5": None, "r20": None, "r60": None, "final": False}


# ──────────────────────────────────────── 判定元ごとの拾い方
def from_regime():
    """温度計の段階が前日と変わった日（radar_history.json・遡って全部）"""
    items = sorted((jload(DOCS / "radar_history.json", {}) or {}).get("items", []),
                   key=lambda x: x.get("d", ""))
    out, prev = [], None
    for it in items:
        rg = it.get("regime")
        if prev and rg and rg != prev:
            out.append(rec("regime", it["d"], "MKT",
                           {"regime": rg, "prev": prev, "vix": it.get("vix"), "n225": it.get("n225")},
                           f"regime|{it['d']}", what=f"地合いの段階の変化（{prev}→{rg}）"))
        prev = rg or prev
    return out


def from_ai():
    """AI朝刊の stance（毎営業日1件・履歴も）"""
    a = jload(DOCS / "ai_analysis.json", {}) or {}
    rows = list(a.get("history") or []) + ([a["latest"]] if a.get("latest") else [])
    out, seen = [], set()
    for x in rows:
        d, st = x.get("date"), x.get("stance")
        if not d or not st or d in seen:
            continue
        seen.add(d)
        out.append(rec("ai", d, "MKT",
                       {"stance": st, "machine_stance": x.get("machine_stance"),
                        "machine_total": x.get("machine_total"),
                        "stance_corrected": x.get("stance_corrected") or None},
                       f"ai|{d}", time=hhmm(x.get("updated") or ""), what=f"相場の構え（{st}）"))
    return out


def from_score3():
    """3軸スコアの機械判定（毎営業日1件）。日付は判定を出した日（JST）"""
    s = jload(DOCS / "score3.json", {}) or {}
    d = jdate(s.get("updated") or "")
    if not d or not s.get("stance"):
        return []
    return [rec("score3", d, "MKT",
                {"stance": s["stance"], "total": s.get("total"), "trade_date": s.get("trade_date"),
                 "axes": {a.get("key"): a.get("score") for a in s.get("axes", [])},
                 "inputs_ok": s.get("inputs_ok")},
                f"score3|{d}", time=hhmm(s["updated"]), what=f"3軸の合計{s.get('total')}（{s['stance']}）")]


def from_crash():
    """着火判定が平常圏から外れた日（crash.json の history・遡って全部）"""
    def stage(sc):
        for hi, key, _ in CRASH_STAGES:
            if sc <= hi:
                return key
        return CRASH_STAGES[-1][1]
    out, prev = [], "calm"
    for it in sorted((jload(DOCS / "crash.json", {}) or {}).get("history", []), key=lambda x: x["d"]):
        st = stage(it.get("s") or 0)
        if st != "calm" and st != prev:
            out.append(rec("crash", it["d"], "MKT", {"score": it.get("s"), "stage": st, "prev": prev},
                           f"crash|{it['d']}", what=f"段階の上昇（{prev}→{st}・点数{it.get('s')}）"))
        prev = st
    return out


def from_free_scanner():
    """無料スキャナー（signals_log.csv）の抽出銘柄。過去分も全部"""
    p = HERE / "signals_log.csv"
    if not p.exists():
        return []
    out = []
    with open(p, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            st, tk = (r.get("strategy") or "").strip(), (r.get("ticker") or "").strip()
            if not tk or not st or st.startswith("("):
                continue
            code = tk.replace(".T", "")
            sid = r.get("signal_id") or f"{r.get('scan_date')}_{st}_{code}"
            ts = r.get("scan_timestamp") or ""
            out.append(rec("free_scanner", r["scan_date"], code,
                           {"strategy": st, "slot": r.get("scan_slot"), "regime": r.get("regime"),
                            "entry": _f(r.get("entry_price")), "stop": _f(r.get("stop_price")),
                            "target": _f(r.get("target_price")), "signal_id": sid},
                           f"free_scanner|{sid}", time=ts[11:16] or None,
                           what=f"{st}の条件（{r.get('name') or code}）"))
    return out


def _f(v):
    try:
        return round(float(v), 2)
    except Exception:
        return None


def from_movers():
    """±5%以上動いた銘柄（movers_hist の全日・movers.json）"""
    days = {}
    idx = jload(DOCS / "movers_hist" / "index.json", {}) or {}
    for d in idx.get("dates", []):
        x = jload(DOCS / "movers_hist" / f"{d}.json")
        if x:
            days[d] = x
    m = jload(DOCS / "movers.json")
    if m and m.get("trade_date"):
        days.setdefault(m["trade_date"], m)
    out = []
    for d, x in sorted(days.items()):
        for side in ("up", "down"):
            for it in x.get(side, []):
                out.append(rec("movers", d, it["c"],
                               {"chg": it.get("chg"), "vr": it.get("vr"), "close": it.get("p"),
                                "value_oku": round((it.get("v") or 0) / 100, 1) if it.get("v") else None},
                               f"movers|{d}|{it['c']}", time="15:30",
                               what=f"前日比{(it.get('chg') or 0):+.1f}%の値動き（{it.get('n') or it['c']}）"))
    return out


def from_tenbagger(state):
    """十倍株スキャナーに新しく入った銘柄（前回の一覧との差分）"""
    t = jload(DOCS / "tenbagger.json", {}) or {}
    cur_date = t.get("data_date")
    cur = {it["code"]: it for it in t.get("items", [])}
    if not cur_date or not cur:
        return []
    st = state.get("tenbagger") or {}
    if st.get("data_date") == cur_date:
        return []                                   # この版はもう見た
    prev_codes = st.get("codes")
    if prev_codes is None:
        old = git_prev("docs/tenbagger.json", lambda o: o.get("data_date") != cur_date)
        prev_codes = [it["code"] for it in (old or {}).get("items", [])] if old else None
    state["tenbagger"] = {"data_date": cur_date, "codes": sorted(cur)}
    if prev_codes is None:
        print("  十倍株: 前回の一覧が無いので今回は覚えるだけ")
        return []
    out = []
    for c in sorted(set(cur) - set(prev_codes)):
        it = cur[c]
        out.append(rec("tenbagger", cur_date, c, {"rank": it.get("rank"), "score": it.get("score")},
                       f"tenbagger|{cur_date}|{c}",
                       what=f"新規の入選（{it.get('name') or c}・{it.get('rank')}位）"))
    return out


def git_versions(rel, n=15):
    """rel の直近 n 版（新しい順）。毎日コミットされるJSONの「その日ごとの版」を読むため"""
    try:
        shas = subprocess.run(["git", "log", "--format=%H", "-n", str(n), "--", rel], cwd=HERE,
                              capture_output=True, text=True, check=True).stdout.split()
    except Exception:
        return []
    out = []
    for sha in shas:
        try:
            raw = subprocess.run(["git", "show", f"{sha}:{rel}"], cwd=HERE,
                                 capture_output=True, check=True).stdout
            out.append(json.loads(raw.decode("utf-8")))
        except Exception:
            continue
    return out


def from_karauri():
    """空売り残高の新規報告＝0.5%以上に**初めて**入った 銘柄×機関（前回の報告が無いもの）。日付は公表日。

    - 毎日の分: karauri.json の moves で kind=="new"（karauri_fetch.py の定義＝前回報告なし）。
      取りこぼした日も拾えるよう、git の直近15版をさかのぼる
    - 過去分: karauri_events/events.json の「新規」のうち prev が無いもの（同じ定義に揃える）。
      events.json の「新規」は 0.5%未満からの再浮上も含むので、そこは入れない"""
    seen = {}
    cur = jload(DOCS / "karauri.json")
    for k in ([cur] if cur else []) + git_versions("docs/karauri.json"):
        rep = (k or {}).get("report_date")
        for m in (k or {}).get("moves", []):
            if m.get("kind") == "new" and rep:
                seen.setdefault((rep, m["c"], m["s"]), (m.get("n"), m.get("r"), m.get("calc")))
    ev = jload(HERE / "karauri_events" / "events.json", []) or []
    for e in ev:
        if e.get("event") == "新規" and e.get("prev") is None and (e.get("ratio") or 0) >= 0.005:
            seen.setdefault((e["pub"], e["code"], e["seller"]),
                            (e.get("name"), round(e["ratio"] * 100, 2), e.get("calc")))
    out = []
    for (rep, c, sl), (n, r, calc) in sorted(seen.items()):
        c = c[:4] if len(c) == 5 and c.endswith("0") else c
        out.append(rec("karauri_new", rep, c, {"inst": sl, "ratio_pct": r, "calc_date": calc},
                       f"karauri_new|{rep}|{c}|{sl}",
                       what=f"新規の報告（{n or c}・{sl}・{r}%）"))
    return out


# ──────────────────────────────────────── 書き込み
def load_all():
    rows = []
    for f in sorted(LEDGER.glob("*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return rows


def main():
    LEDGER.mkdir(parents=True, exist_ok=True)
    state = jload(STATE, {}) or {}
    have = load_all()
    nks = {r.get("nk") for r in have}
    ids = {r["id"] for r in have}

    found = []
    for fn in (from_regime, from_ai, from_score3, from_crash, from_free_scanner, from_movers,
               from_karauri):
        try:
            got = fn()
        except Exception as e:          # 1つの判定元が壊れても他は記録する
            print(f"::warning::{fn.__name__} で失敗: {e}")
            got = []
        found += got
    for fn in (from_tenbagger,):
        try:
            found += fn(state)
        except Exception as e:
            print(f"::warning::{fn.__name__} で失敗: {e}")

    new = [r for r in found if r["_nk"] not in nks]
    # 同じ自然キーが今回の中で重複していたら1件に
    uniq, seen = [], set()
    for r in sorted(new, key=lambda r: (r["date"], r["time"] or "", r["source"], r["code"])):
        if r["_nk"] in seen:
            continue
        seen.add(r["_nk"])
        uniq.append(r)

    by_month = {}
    for r in uniq:
        base = f"{r['date'].replace('-', '')}-{r['source']}-{r['code']}"
        n = 1
        while f"{base}-{n}" in ids:
            n += 1
        rid = f"{base}-{n}"
        ids.add(rid)
        row = {"id": rid, "nk": r.pop("_nk"), **r}
        by_month.setdefault(r["date"][:7], []).append(row)

    for ym, rows in sorted(by_month.items()):
        with open(LEDGER / f"{ym}.jsonl", "a", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")

    STATE.write_text(json.dumps(state, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    from collections import Counter
    cnt = Counter(r["source"] for rows in by_month.values() for r in rows)
    print(f"台帳に追記 {sum(cnt.values())}件 " + " ".join(f"{k}:{v}" for k, v in sorted(cnt.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
