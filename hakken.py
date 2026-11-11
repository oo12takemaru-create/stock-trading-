# -*- coding: utf-8 -*-
"""「今日の発見」— アーサーが気になった3銘柄（2026-11-11・起動文_実務_アーサーのひとことと今日の発見_2026-11-11.md）

■ 役割の分け方
  - 選ぶのは機械（このファイルの pick。karte_ai_prep.py の event の回＝平日 19:30 に呼ぶ）
  - 語るのはアーサー（同じ夜の定期実行が、PROMPT.txt の「今日の発見」の節を読んで3文を書く）
  - 取り込み・5営業日後の「その後」の追記は karte_ai_ingest.py（このファイルの save_day / followups）

■ 選び方（v1・サイトの methods.html#hakken に同じ表を公開）
  その日の出来事ごとに「珍しさ」の点を足し、合計の大きい順。同点は売買代金の大きい順。
    ストップ高・ストップ安   終値で張り付いた 3点／取引時間中に一時 2点
    出来高の急増            20日平均の10倍以上 3点／5倍以上 2点／3倍以上 1点
    空売りの報告            大口の空売り残高の新規報告 1.5点／合計が0.5ポイント以上増えた 1点
    決算の数字の変化        利益の前年同期比の大きさ 100%以上 2点／50%以上 1.5点／30%以上 1点、会社の業績見通しの上方・下方修正 +1点
    大量保有の新規          新規の大量保有報告書（前営業日〜当日の提出） 1.5点
    株式分割の発表          1.5点
    配当の見通しの修正      決算短信の「配当予想の修正：有」 1点（表示は「予想」を使わず「見通し」と書く）
    業種の中で逆に動いた    ±5%以上動き、同じ業種の平均（0.5%以上）と向きが逆 1点
  対象: その日の売買代金 1億円以上・ETF/REIT 等を除く・合計 2点以上
  3銘柄の決まり: 1つは下げた/注意の出来事（ストップ安・前日比 -3%以下・業績見通しの下方修正・空売りの新規/増加）。
                 同じ銘柄は5営業日は選ばない。同じ業種は2つまで。
"""
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

from jp_bizday import is_bizday, next_bizday

FORMULA_V = "v1"
N_PICK = 3
MIN_SCORE = 2.0
MIN_TURNOVER = 1e8          # 売買代金 1億円
COOLDOWN_BD = 5             # 同じ銘柄を選ばない営業日数
SECTOR_MAX = 2
FOLLOW_BD = 5               # 「その後」を追記する営業日
RATIO_CAP = 100
STOP_PT = {"close_high": 3.0, "touch_high": 2.0, "close_low": 3.0, "touch_low": 2.0}
STOP_JP = {"close_high": "終値ストップ高", "touch_high": "一時ストップ高",
           "close_low": "終値ストップ安", "touch_low": "一時ストップ安"}
NOT_STOCK = re.compile(r"ETF|ＥＴＦ|上場投信|受益証券|投資法人|リート|ＲＥＩＴ|REIT|ETN|ＥＴＮ|指数連動")


def _load(docs, name):
    p = Path(docs) / name
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  hakken: {name} が読めない {e}", file=sys.stderr)
        return None


def _jsonl(docs, name):
    p = Path(docs) / name
    out = []
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


def prev_bizday(d):
    for _ in range(30):
        d -= timedelta(days=1)
        if is_bizday(d):
            return d
    return d


def bizdays_between(a, b):
    """a の翌営業日から b まで（a<b）の営業日数。a>=b なら 0"""
    n, d = 0, a
    while d < b and n < 400:
        d += timedelta(days=1)
        if is_bizday(d):
            n += 1
    return n


def next_margin_pub(today):
    """信用残（週次・JPX）の次の公表日の目安＝週の第2営業日（申込日は前の週の最終営業日）"""
    for k in range(3):
        mon = today - timedelta(days=today.weekday()) + timedelta(weeks=k)
        bd = [mon + timedelta(days=i) for i in range(7) if is_bizday(mon + timedelta(days=i))]
        if len(bd) >= 2 and bd[1] > today:
            return bd[1]
    return None


def next_dates(today):
    """「次に出る数字」の共通の日付（PROMPT の共通の行・ingest の数字の検査に入る）"""
    mp = next_margin_pub(today)
    nb = next_bizday(today)
    parts = []
    if nb:
        parts.append(f"次の営業日 {nb.isoformat()}（大口の空売り残高の次の公表）")
    if mp:
        parts.append(f"信用残（週次）の次の公表 {mp.isoformat()}（目安）")
    return "・".join(parts)


def _yoy_pt(x):
    a = abs(x)
    return 2.0 if a >= 100 else 1.5 if a >= 50 else 1.0 if a >= 30 else 0.0


def _vr_pt(vr):
    return 3.0 if vr >= 10 else 2.0 if vr >= 5 else 1.0 if vr >= 3 else 0.0


class Day:
    """その日の材料（docs/*.json）を銘柄コードで引ける形にする"""

    def __init__(self, docs, today):
        self.docs = Path(docs)
        self.today = today
        t = today.isoformat()
        self.t = t
        self.p1 = prev_bizday(today).isoformat()
        pr = _load(docs, "prices.json") or {}
        self.price_date = pr.get("trade_date")
        self.price = pr.get("items") or {}
        lc = _load(docs, "jpx_listed_codes.json") or {}
        nm = lc.get("names") or {}
        self.names = dict(nm) if isinstance(nm, dict) else dict(zip(lc.get("codes") or [], nm))
        self.sector = {}
        ke = _load(docs, "kessan.json") or {}
        for day in (ke.get("days") or []):
            for it in day.get("items") or []:
                if it.get("c") and it.get("s"):
                    self.sector.setdefault(it["c"], it["s"])
        for it in (ke.get("undecided") or []):
            if it.get("c") and it.get("s"):
                self.sector.setdefault(it["c"], it["s"])
        sh = _load(docs, "stophigh.json") or {}
        self.stop_stats = sh.get("stats") or {}
        rec = sh.get("recent") or {}
        self.stop = {}
        for it in (rec.get(t) or []) if isinstance(rec, dict) else []:
            if it.get("c") and it.get("k") in STOP_PT:
                self.stop[it["c"]] = it
                if it.get("s"):
                    self.sector[it["c"]] = it["s"]
        mv = _load(docs, "movers.json") or {}
        self.movers = {}
        if mv.get("trade_date") == t:
            for side in ("up", "down"):
                for it in mv.get(side) or []:
                    if it.get("c"):
                        self.movers[it["c"]] = it
                        if it.get("s"):
                            self.sector.setdefault(it["c"], it["s"])
        ka = _load(docs, "karauri.json") or {}
        self.ka_date = ka.get("report_date")
        self.karauri = {it["c"]: it for it in (ka.get("stocks") or []) if it.get("c")}
        self.ka_new = {}
        if self.ka_date == t:
            for m in ka.get("moves") or []:
                if m.get("kind") == "new" and m.get("c"):
                    self.ka_new.setdefault(m["c"], []).append(m)
        fl = _load(docs, "kessan_flash.json") or {}
        self.flash = {}
        for day in fl.get("days") or []:
            if day.get("d") == t:
                for it in day.get("items") or []:
                    if it.get("c") and not it.get("suspect"):
                        self.flash[it["c"]] = it
        ed = _load(docs, "edinet_latest.json") or {}
        self.holder_new = {}
        for c, docs_ in (ed.get("by_issuer") or {}).items():
            for e in docs_ or []:
                if e.get("type") == "大量保有" and self.p1 <= (e.get("d") or "")[:10] <= t:
                    self.holder_new.setdefault(c, e)
        sp = _load(docs, "splits.json") or {}
        self.split = {e["code"]: e for e in (sp.get("recent") or [])
                      if e.get("code") and e.get("ann") == t and e.get("status") != "中止"}
        self.div_rev = {}
        for row in _jsonl(docs, f"dividend_hist/{t[:7]}.jsonl"):
            if row.get("c") and row.get("rev") == "有" and row.get("d") == t:
                self.div_rev.setdefault(row["c"], row)
        sd = _load(docs, "shinyo_daily.json") or {}
        self.mdaily = sd.get("items") or {}

    def turnover(self, c):
        p = self.price.get(c) or {}
        return (p.get("p") or 0) * (p.get("v") or 0) * 1000

    def margin_hidden(self, c):
        """信用倍率が100倍超・売残0 → 倍率・残高を材料に入れない"""
        days = self.mdaily.get(c)
        cur = days[-1] if days else None
        if not cur:
            return False
        s_, _, b, _ = cur
        return (not s_) or (b / s_ > RATIO_CAP)

    def score(self, c):
        """(点, 内訳, 向き)。内訳は [{"k","l","pt"}]（l は数字つきの言葉）"""
        parts = []
        p = self.price.get(c) or {}
        chg = p.get("c")
        s = self.stop.get(c)
        if s:
            parts.append({"k": "stop", "l": STOP_JP[s["k"]] + (f"（前日比 {s['chg']:+.1f}%）" if s.get("chg") is not None else ""),
                          "pt": STOP_PT[s["k"]]})
        vr = p.get("vr")
        if vr is not None and _vr_pt(vr):
            parts.append({"k": "vol", "l": f"出来高 20日平均の{vr:.1f}倍", "pt": _vr_pt(vr)})
        if c in self.ka_new:
            parts.append({"k": "short_new", "l": "大口の空売り残高の新規報告", "pt": 1.5})
        ka = self.karauri.get(c)
        if ka and self.ka_date == self.t and (ka.get("d1") or 0) >= 0.5:
            parts.append({"k": "short_up", "l": f"大口の空売り残高の合計が前回から{ka['d1']:.2f}ポイント増", "pt": 1.0})
        f = self.flash.get(c)
        if f:
            key, lab = next(((k, l) for k, l in (("op_yoy", "営業利益"), ("ord_yoy", "経常利益"), ("np_yoy", "純利益"))
                             if f.get(k) is not None), (None, None))
            if key and _yoy_pt(f[key]):
                parts.append({"k": "kessan", "l": f"決算短信の{lab} 前年同期比{f[key]:+.1f}%", "pt": _yoy_pt(f[key])})
            if f.get("fc_rev") in ("上方", "下方"):
                parts.append({"k": "rev_" + ("up" if f["fc_rev"] == "上方" else "down"),
                              "l": f"会社の業績見通しを{f['fc_rev']}修正", "pt": 1.0})
        h = self.holder_new.get(c)
        if h:
            who = (h.get("filer") or "").replace("株式会社", "").strip()
            parts.append({"k": "holder", "l": f"大量保有の新規報告（{who} {h['ratio']:.2f}%）" if h.get("ratio") is not None
                          else "大量保有の新規報告", "pt": 1.5})
        sp = self.split.get(c)
        if sp:
            parts.append({"k": "split", "l": "株式分割の発表" + (f"（1:{format(sp['ratio'], 'g')}）" if sp.get("ratio") else ""),
                          "pt": 1.5})
        if c in self.div_rev:
            fc = self.div_rev[c].get("dps_fc")
            parts.append({"k": "div", "l": "配当の見通しの修正" + (f"（年間 {fc:g}円）" if fc is not None else ""), "pt": 1.0})
        mv = self.movers.get(c)
        if mv and chg is not None:
            avg = sector_avg(mv)
            if avg is not None and abs(avg[1]) >= 0.5 and (chg > 0) != (avg[1] > 0):
                parts.append({"k": "contra", "l": f"同じ業種（{avg[0]}）の平均 {avg[1]:+.1f}% と逆向き（前日比 {chg:+.1f}%）",
                              "pt": 1.0})
        total = round(sum(x["pt"] for x in parts), 2)
        down = ((s and s["k"].endswith("_low")) or (chg is not None and chg <= -3)
                or any(x["k"] in ("rev_down", "short_new", "short_up") for x in parts))
        up = (s and s["k"].endswith("_high")) or (chg is not None and chg >= 3)
        return total, parts, ("down" if down else "up" if up else "flat")


def sector_avg(mv):
    """movers.json の facts の「同業種（小売業・320銘柄）の平均は+0.1%」から (業種, 平均) を読む"""
    for f in mv.get("facts") or []:
        if f.get("k") == "sector":
            m = re.search(r"同業種（(.+?)・\d+銘柄）の平均は([+-]?\d+(?:\.\d+)?)%", f.get("t") or "")
            if m:
                return m.group(1), float(m.group(2))
    return None


def recent_picks(docs, today, bd=COOLDOWN_BD):
    """直近 bd 営業日（今日を含まない）に選んだ銘柄コード"""
    out, d, seen = set(), today, 0
    while seen < bd:
        d = prev_bizday(d)
        seen += 1
        j = _load(docs, f"hakken/{d.isoformat()}.json")
        for p in (j or {}).get("picks") or []:
            out.add(p.get("c"))
    return out


def pick(docs, today, n=N_PICK):
    """その日の3銘柄を選ぶ。戻り値 {"picks": [...], "n_cand", "skipped_recent"}。対象が無ければ picks は空"""
    D = Day(docs, today)
    if D.price_date != D.t:
        print(f"  hakken: prices.json の日付が {D.price_date}（今日 {D.t} ではない）→ 選ばない", file=sys.stderr)
        return {"picks": [], "n_cand": 0, "skipped_recent": [], "D": D}
    codes = (set(D.stop) | set(D.ka_new) | set(D.flash) | set(D.holder_new) | set(D.split) | set(D.div_rev)
             | set(D.movers) | {c for c, v in D.price.items() if (v.get("vr") or 0) >= 3}
             | {c for c, v in D.karauri.items() if D.ka_date == D.t and (v.get("d1") or 0) >= 0.5})
    cand = []
    for c in codes:
        p = D.price.get(c) or {}
        nm = D.names.get(c) or ""
        if not p.get("p") or p.get("stale") or NOT_STOCK.search(nm) or D.turnover(c) < MIN_TURNOVER:
            continue
        sc, parts, dr = D.score(c)
        if sc >= MIN_SCORE:
            cand.append({"c": c, "n": nm, "s": D.sector.get(c, ""), "score": sc, "parts": parts, "dir": dr,
                         "p0": p["p"], "chg": p.get("c"), "tv": round(D.turnover(c) / 1e8, 1)})
    cand.sort(key=lambda x: (-x["score"], -x["tv"], x["c"]))
    recent = recent_picks(docs, today)
    skipped = [x["c"] for x in cand[:20] if x["c"] in recent]
    pool = [x for x in cand if x["c"] not in recent]
    picks = []
    down = next((x for x in pool if x["dir"] == "down"), None)
    if down:
        picks.append(down)
    for x in pool:
        if len(picks) >= n:
            break
        if x in picks:
            continue
        if x["s"] and sum(1 for y in picks if y["s"] == x["s"]) >= SECTOR_MAX:
            continue
        picks.append(x)
    picks.sort(key=lambda x: (-x["score"], -x["tv"]))
    return {"picks": picks, "n_cand": len(cand), "skipped_recent": skipped, "D": D}


def past_line(D, x):
    """過去に同じ形があったときの記録（材料に1つ。無ければ None）"""
    s = D.stop.get(x["c"])
    if s:
        st = (D.stop_stats.get(s["k"]) or {})
        n5 = st.get("n5c")
        if n5 and n5.get("n", 0) >= 30:
            return (f"過去の同じ形: {STOP_JP[s['k']]}の5営業日後の終値の騰落（{st.get('from')}〜{st.get('to')}・{n5['n']:,}件）"
                    f" 中央値 {n5['median']:+.2f}%・上がった割合 {n5['pos']:.1f}%")
    return None


def material(D, x, facts_row):
    """PROMPT の「今日の発見」の1銘柄ぶん（数字と出所の日付だけ）"""
    lines = [f"選んだ理由（点の内訳・合計 {x['score']:g}点）: " + "・".join(f"{p['l']} {p['pt']:g}点" for p in x["parts"])]
    if x.get("chg") is not None:
        lines.append(f"終値 {x['p0']:,.1f}円・前日比 {x['chg']:+.1f}%・売買代金 {x['tv']:,.1f}億円（{D.price_date}）")
    pl = past_line(D, x)
    if pl:
        lines.append(pl)
    row = facts_row
    if D.margin_hidden(x["c"]):
        # 倍率100倍超・売残0 の銘柄は信用倍率の行を材料に入れない
        row = "\n".join(l for l in row.splitlines() if not l.startswith("- 信用倍率"))
    return "\n".join("- " + l for l in lines) + "\n" + "\n".join(l for l in row.splitlines()[1:])


# ─────────────────────────── 取り込み側（karte_ai_ingest.py から）
def save_day(docs, H, answers, model, now, drops):
    """docs/hakken/YYYY-MM-DD.json を書く。answers = {c: {"h","t"}}（検査を通ったものだけ）"""
    out = Path(docs) / "hakken"
    out.mkdir(parents=True, exist_ok=True)
    day = H["date"]
    p = out / f"{day}.json"
    old = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    old_after = {x["c"]: x.get("after") for x in old.get("picks") or []}
    picks = []
    for x in H["picks"]:
        a = answers.get(x["c"]) or {}
        picks.append({**{k: x[k] for k in ("c", "n", "s", "score", "parts", "dir", "p0", "chg", "tv")},
                      "h": a.get("h"), "t": a.get("t"), "m": model if a.get("t") else None,
                      "after": old_after.get(x["c"])})
    rec = {"date": day, "v": H.get("v", FORMULA_V), "made": now, "price_date": H.get("price_date"),
           "n_cand": H.get("n_cand"), "skipped_recent": H.get("skipped_recent") or [],
           "note": "選ぶのは機械（点の合計の大きい順・methods.html#hakken）、文はアーサー（AI）。事実の整理で、売買のすすめではありません",
           "drops": drops, "picks": picks}
    p.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    return rec


def followups(docs, now):
    """5営業日たった日の「その後」を機械で追記し、latest.json・record.json・index.json を作り直す"""
    out = Path(docs) / "hakken"
    if not out.exists():
        return None
    pr = _load(docs, "prices.json") or {}
    td, items = pr.get("trade_date"), pr.get("items") or {}
    days = sorted(out.glob("20??-??-??.json"))
    filled = 0
    allrec = []
    for f in days:
        j = json.loads(f.read_text(encoding="utf-8"))
        changed = False
        for x in j.get("picks") or []:
            if not x.get("after") and td:
                bd = bizdays_between(date.fromisoformat(j["date"]), date.fromisoformat(td))
                v = items.get(x["c"]) or {}
                if bd >= FOLLOW_BD and v.get("p") and not v.get("stale") and x.get("p0"):
                    x["after"] = {"d": td, "bd": bd, "p": v["p"], "chg": round((v["p"] / x["p0"] - 1) * 100, 2)}
                    changed = True
                    filled += 1
            allrec.append({"d": j["date"], **{k: x.get(k) for k in ("c", "n", "dir", "score", "p0", "h", "after")}})
        if changed:
            f.write_text(json.dumps(j, ensure_ascii=False, indent=1), encoding="utf-8")
    done = [r for r in allrec if r.get("after")]

    def stat(rs):
        c = sorted(r["after"]["chg"] for r in rs)
        if not c:
            return {"n": 0}
        m = len(c) // 2
        med = c[m] if len(c) % 2 else (c[m - 1] + c[m]) / 2
        return {"n": len(c), "up": sum(1 for v in c if v > 0), "down": sum(1 for v in c if v < 0),
                "mean": round(sum(c) / len(c), 2), "median": round(med, 2)}

    record = {"updated": now, "note": "取り上げた日の終値から5営業日後（追記した日）の終値までの騰落。外れも消さない。"
                                      "株式分割の権利落ちを挟んだ銘柄は値が大きくずれることがある",
              "all": stat(done), "by_dir": {k: stat([r for r in done if r["dir"] == k]) for k in ("up", "down", "flat")},
              "n_picks": len(allrec), "items": sorted(allrec, key=lambda r: (r["d"], -(r.get("score") or 0)), reverse=True)}
    (out / "record.json").write_text(json.dumps(record, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    dates = [f.stem for f in days]
    (out / "index.json").write_text(json.dumps({"dates": dates}, ensure_ascii=False), encoding="utf-8")
    if dates:
        last = json.loads((out / f"{dates[-1]}.json").read_text(encoding="utf-8"))
        recent = []
        for dd in dates[-10:][::-1]:
            j = json.loads((out / f"{dd}.json").read_text(encoding="utf-8"))
            recent.append({"date": dd, "picks": [{k: x.get(k) for k in ("c", "n", "h", "dir", "after")} for x in j["picks"]]})
        (out / "latest.json").write_text(json.dumps(
            {"updated": now, "date": last["date"], "v": last.get("v"), "picks": last["picks"],
             "recent": recent, "record": {"all": record["all"]}},
            ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return {"filled": filled, "done": len(done), "all": len(allrec), "mean": record["all"].get("mean")}
