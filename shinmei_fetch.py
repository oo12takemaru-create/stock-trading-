# -*- coding: utf-8 -*-
"""銘柄別の信用取引残高（日次）→ docs/shinyo_meigara.json ほか（株レーダー kaburadar.jp）

■ 2026-09-25 申込分から JPX は銘柄別の信用残を「日次」公表に移した
  旧: margin/05.html に週末残高のPDF（syumatsuYYYYMMDD00.pdf・前週比）
  新: margin/01.html に申込日ごとのPDF（YYYYMMDD_mtall.pdf・前日比）。直近5日ぶんだけ並ぶ
  旧ページが消えたあと、この取り込みは「PDFリンクが見つからない」と警告を出しながら
  ジョブは success のまま止まっていた（9/18 申込分で止まっていた・2026-10-05 に発見）。
  旧形式の週次PDFは JPX から削除済みで、遡れない。

■ 何を作るか
  docs/shinyo_daily/YYYY-MM.json … 日次の全銘柄ぶんを月別に貯める（資産。JPXは5日で消す）
  docs/shinyo_daily.json         … 直近5営業日 × 全銘柄（銘柄ページの日次表に使う）
  docs/shinyo_meigara.json       … 最新日の残高。sd/bd は「5営業日前比」
     ※保有ボード・スクリーナー・銘柄ページは「前週比」として読んでいる。週次だった頃と
       意味を揃えるため、直近5日の前日比を足し合わせて5営業日前比を出す（JPXの公表値の和）

■ 公表形式はPDFのみ（Excel/CSVなし）
  機械生成の定型PDFなので pymupdf でテキスト抽出する。
  抽出の正しさは「一般信用＋制度信用＝合計」が全行で一致することで毎回検証し、
  不一致が1%を超えたら公開せずに失敗させる（壊れたデータを出さない）。
■ 単位は株（PDFの原単位のまま）。信用倍率は表示側で 買残÷売残 を計算する。
■ 5桁コードの末尾が0以外（優先株式など）は除外（4桁コードが普通株式と衝突するため）。

■ 2026-10-21 直近5営業日を毎回取り直す（Fable 判断）
  10/20 申込分で JPX の総合計の買い残が前日比 −27.7%。取り込みは JPX と一致していたが、
  JPX が後日 PDF を差し替えても「取得済みは取り直さない」作りでは拾えなかった。
  → ページに並ぶ5本を毎回読み、中身が違えば上書きする（約9MB/日）。月別の蓄積も同じ日を上書き。
    上書きしたら shinyo_daily.json の replaced に {date, before, after, at}（買い残の合計）を残す
    （healthcheck が「差し替わった」と知らせる）。差し替え版が検算で落ちたら古いほうを残す。
■ 市場全体の買い残が前日比±15%を超えた申込日は anomaly に [{date, pct}] で持つ
  （直近5営業日に入っている間だけ＝5営業日前比に含まれる間だけ。過ぎたら自動で消える）。
  shinyo_daily.json と shinyo_meigara.json の両方に入れる（銘柄ページの注記・ヒートマップの凡例）。
"""
import json
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

from stale_guard import keep_newest

JST = timezone(timedelta(hours=9))
DOCS = Path(__file__).parent / "docs"
OUT = DOCS / "shinyo_meigara.json"
DAILY = DOCS / "shinyo_daily.json"
ARCH = DOCS / "shinyo_daily"            # 月別の蓄積 YYYY-MM.json
SEED = DOCS / "shinyo_weekly_seed.json"  # 旧週次（8/7〜9/18）。git履歴から復元した7週
WEEKLY = DOCS / "shinyo_weekly.json"     # 週次の推移（銘柄ページのグラフ用）
WEEKS_KEEP = 104                         # 2年ぶん

BASE = "https://www.jpx.co.jp"
PAGE = "/markets/statistics-equities/margin/01.html"
UA = {"User-Agent": "Mozilla/5.0 (compatible; kaburadar.jp/1.0; +https://kaburadar.jp)"}

# 「JP3257200000 株数 Shs.」の行が株数、「… 金額 Val.」の行が金額。株数だけを読む
ISIN_SHARES = re.compile(r"^([A-Z]{2}[A-Z0-9]{9}\d)\s*株数")
NUM_RE = re.compile(r"^(▲\s?)?[\d,]+$")
RATIO_RE = re.compile(r"^(-?[\d.]+%|\*|-)$")       # 上場比（ETF等は * や -）


def http(url, tries=3):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=40 + i * 20) as r:
                return r.read()
        except Exception as e:
            last = e
            time.sleep(3 * (i + 1))
    raise last


def to_int(tok):
    neg = tok.startswith("▲")
    v = int(tok.replace("▲", "").replace(",", "").strip())
    return -v if neg else v


def parse_pdf(pdf, ratios=None):
    """日次PDF → (申込日, {code: [売残, 売前日比, 買残, 買前日比]}, 検算NG件数)
    ratios に dict を渡すと、買残の上場比（%）を {code: 0.52} で入れて返す（進化③ Q2 用・2026-10-10）

    1銘柄の株数の行は「売残 前日比 上場比 買残 前日比 上場比」＋内訳8個
    （売の一般・前日比・制度・前日比、買の一般・前日比・制度・前日比）。"""
    import pymupdf
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    head = doc[0].get_text()
    m = re.search(r"(\d{4})/(\d{1,2})/(\d{1,2})\s*申込み現在", head)
    if not m:
        raise ValueError("申込日が本文に見つからない（様式変更の可能性）")
    asof = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"

    items, bad = {}, 0
    for page in doc:
        lines = [l.strip() for l in page.get_text().splitlines()]
        for i, ln in enumerate(lines):
            if not ISIN_SHARES.match(ln):
                continue
            code5 = lines[i - 1] if i >= 1 else ""
            if not re.match(r"^[0-9][0-9A-Z]{3}[0-9]$", code5):
                bad += 1
                continue
            toks, j = [], i + 1
            while j < len(lines) and len(toks) < 14:
                t = lines[j]
                if NUM_RE.match(t) or RATIO_RE.match(t):
                    toks.append(t)
                    j += 1
                else:
                    break
            if len(toks) < 14 or not RATIO_RE.match(toks[2]) or not RATIO_RE.match(toks[5]):
                bad += 1
                continue
            try:
                s, sd, b, bd = (to_int(toks[k]) for k in (0, 1, 3, 4))
                d = [to_int(t) for t in toks[6:14]]
            except ValueError:
                bad += 1
                continue
            # 抽出の正しさを毎行検算: 一般＋制度＝合計（残高も前日比も）
            if d[0] + d[2] != s or d[1] + d[3] != sd or d[4] + d[6] != b or d[5] + d[7] != bd:
                bad += 1
                continue
            if code5[4] != "0":            # 優先株式などは除外（4桁コードの衝突防止）
                continue
            items.setdefault(code5[:4], [s, sd, b, bd])
            if ratios is not None and toks[5].endswith("%"):
                try:
                    ratios.setdefault(code5[:4], float(toks[5].rstrip("%")))
                except ValueError:
                    pass
    return asof, items, bad


def save_listed(path, asof, items, ratios):
    """上場株式数の推計 = 買残 ÷ 上場比。買残0・上場比0の銘柄は割り戻せないので入れない"""
    out = {}
    for c, r in ratios.items():
        b = (items.get(c) or [None, None, None])[2]
        if b and r and r > 0:
            out[c] = int(round(b / (r / 100)))
    path.write_text(json.dumps({
        "updated": datetime.now(JST).isoformat(timespec="seconds"), "asof": asof, "unit": "株",
        "note": "上場株式数の推計（日次PDFの買残÷上場比）。上場比は小数2桁なので数%の誤差がある。買残が0の銘柄は無い",
        "items": dict(sorted(out.items()))}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"上場株式数の推計: {len(out)}銘柄（{asof}）", file=sys.stderr)


def total_buy(items):
    """全銘柄の買い残の合計（株）"""
    return sum(v[2] for v in items.values() if v)


ANOMALY_PCT = 15.0      # 市場全体の買い残の前日比がこれを超えた日に注記を出す


def anomalies(days, last5):
    """直近5営業日のうち、市場全体の買い残の前日比が±15%を超えた申込日。
    前日比は JPX の公表値（各銘柄の前日比の和）÷前日の残高で出す（前日のファイルが無くても出せる）"""
    out = []
    for d in last5:
        it = days.get(d) or {}
        b = total_buy(it)
        bd = sum(v[3] for v in it.values() if v)
        if b - bd > 0:
            pct = round(bd / (b - bd) * 100, 1)
            if abs(pct) > ANOMALY_PCT:
                out.append({"date": d, "pct": pct})
    return out


def load_archive():
    """月別の蓄積を全部読む → {申込日: {code: [s, sd, b, bd]}}"""
    days = {}
    if ARCH.exists():
        for p in sorted(ARCH.glob("*.json")):
            days.update(json.loads(p.read_text(encoding="utf-8")).get("days", {}))
    return days


def save_archive(days):
    by_month = {}
    for d, v in days.items():
        by_month.setdefault(d[:7], {})[d] = v
    ARCH.mkdir(parents=True, exist_ok=True)
    for ym, dd in by_month.items():
        (ARCH / f"{ym}.json").write_text(json.dumps(
            {"month": ym, "unit": "株",
             "fields": ["売残", "売残の前日比", "買残", "買残の前日比"],
             "source": "JPX 銘柄別信用取引残高（日次・申込日ベース）",
             "days": dict(sorted(dd.items()))},
            ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def build_weekly(days):
    """週次の推移。旧週次の7週＋日次の各週の最後の申込日。

    グラフは週次・表は日次で、同じ線に混ぜない（起動文PV④）。
    日次からは「その週の最後の申込日」だけを1点にする。無い週は点を作らない（埋めない）。"""
    weeks = {}
    try:
        weeks.update(json.loads(SEED.read_text(encoding="utf-8")).get("weeks", {}))
    except Exception as e:
        print(f"  旧週次を読めない（日次だけで作る）: {e}", file=sys.stderr)
    last_of_week = {}
    for d in sorted(days):
        y, w, _ = datetime.strptime(d, "%Y-%m-%d").isocalendar()
        last_of_week[(y, w)] = d
    for d in last_of_week.values():
        weeks[d] = {c: [v[0], v[2]] for c, v in days[d].items()}
    order = sorted(weeks)[-WEEKS_KEEP:]
    codes = set().union(*(weeks[w].keys() for w in order)) if order else set()
    return {
        "updated": datetime.now(JST).isoformat(timespec="seconds"),
        "asof": order[-1] if order else None,
        "weeks": order,
        "fields": ["売残", "買残"], "unit": "株",
        "note": "8/7〜9/18 は旧週次（週末残高）、10/2 以降は日次の各週の最後の申込日。"
                "9/25 は旧週次が公表前に廃止・日次の取得前のため欠けている",
        "source": "JPX 銘柄別信用取引残高",
        "items": {c: [weeks[w].get(c) for w in order] for c in sorted(codes)},
    }


def main():
    html = http(BASE + PAGE).decode("utf-8", "ignore")
    links = sorted(set(re.findall(r'href="(/markets/[^"]+/(\d{8})_mtall\.pdf)"', html)),
                   key=lambda x: x[1])
    if not links:
        # 黙って success にしない（旧ページが消えたときはこれで2週間気づけなかった）
        print("日次PDFのリンクが見つからない（JPXのページ構成が変わった可能性）", file=sys.stderr)
        sys.exit(1)

    days = load_archive()
    fetched = 0
    # ── 上場株式数（進化③ Q2「買い残が上場株式数の0.5%以上」）。上場比（買残÷上場株式数）から割り戻す。
    #    無ければ最新のPDFを1本だけ読み直す（取得済みでも）。上場株式数はめったに変わらないので最新の値で足りる
    LISTED = DOCS / "shinyo_listed.json"
    if not LISTED.exists() and links:
        link, ymd = links[-1]
        ratios = {}
        asof_l, items_l, _ = parse_pdf(http(BASE + link), ratios)
        save_listed(LISTED, asof_l, items_l, ratios)
    replaced = []
    for link, ymd in links:
        key = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}"
        had = key in days                # 取得済みでも取り直す（JPX の差し替えを拾う・2026-10-21）
        pdf = http(BASE + link)
        ratios = {}
        asof, items, bad = parse_pdf(pdf, ratios)
        total = len(items) + bad
        print(f"取得: {ymd} → 申込日 {asof} / {len(items)}銘柄 / 検算NG {bad}", file=sys.stderr)
        if asof != key:
            print(f"注意: ファイル名{key} と本文{asof} が不一致（本文を採用）", file=sys.stderr)
        broken = total == 0 or bad / total > 0.01 or len(items) < 2000
        if broken and (had or asof in days):
            print(f"::warning::{asof} の取り直しが検算で落ちた → 前に取り込んだ分を残す", file=sys.stderr)
            continue
        if total == 0 or bad / total > 0.01:
            print("検算不一致が1%超 → 公開しない（PDFの様式が変わった可能性）", file=sys.stderr)
            sys.exit(1)
        if len(items) < 2000:
            print(f"銘柄数が少なすぎる: {len(items)}", file=sys.stderr)
            sys.exit(1)
        old = days.get(asof)
        if old is not None:
            if old == items:
                continue                 # 前と同じ（差し替え無し）
            b0, b1 = total_buy(old), total_buy(items)
            replaced.append({"date": asof, "before": b0, "after": b1,
                             "at": datetime.now(JST).strftime("%Y-%m-%d")})
            print(f"::warning::{asof} 分が差し替わった（買い残の合計 {b0:,} → {b1:,}）", file=sys.stderr)
        days[asof] = items
        if asof == max(days) or not (DOCS / "shinyo_listed.json").exists():
            save_listed(DOCS / "shinyo_listed.json", asof, items, ratios)
        fetched += 1
        time.sleep(2)

    if not days:
        sys.exit(1)
    save_archive(days)

    order = sorted(days)
    last5 = order[-5:]
    latest = order[-1]
    now = datetime.now(JST).isoformat(timespec="seconds")

    # ── 直近5営業日 × 全銘柄（銘柄ページの日次表）
    # 公表日＝申込日の翌営業日（JPXの公表ルール。10/2 申込 → 10/5 公表で確認）。
    # 銘柄ページに「申込日と公表日」を並べて出すために持たせる
    from jp_bizday import next_bizday
    published = {d: next_bizday(datetime.strptime(d, "%Y-%m-%d").date()).isoformat()
                 for d in last5}
    anom = anomalies(days, last5)
    # 差し替えの記録は直近5営業日に入っている間だけ持つ（同じ日に何度走っても消えないよう前の分を引き継ぐ）
    try:
        prev_rep = json.loads(DAILY.read_text(encoding="utf-8")).get("replaced") or []
    except Exception:
        prev_rep = []
    rep_keep = [r for r in prev_rep + replaced if r.get("date") in last5]
    daily = {
        "updated": now, "asof": latest, "dates": last5, "published": published, "unit": "株",
        "fields": ["売残", "売残の前日比", "買残", "買残の前日比"],
        "source": "JPX 銘柄別信用取引残高（日次・申込日ベース）",
        "items": {c: [days[d].get(c) for d in last5] for c in days[latest]},
    }
    if anom:
        daily["anomaly"] = anom
    if rep_keep:
        daily["replaced"] = rep_keep
    keep_newest(DAILY, daily, "asof", "日次信用残")

    # ── 週次の推移（銘柄ページのグラフ）
    wk = build_weekly(days)
    keep_newest(WEEKLY, wk, "asof", "信用残の週次推移")

    # ── 最新日（互換）。sd/bd は直近5日の前日比の和＝5営業日前比
    #    5日のうち1日でも欠けた銘柄（新規上場など）は和にせず null にする
    items = []
    for c, (s, sd1, b, bd1) in days[latest].items():
        rows = [days[d].get(c) for d in last5]
        full = len(last5) == 5 and all(rows)
        items.append({"c": c, "s": s, "b": b,
                      "sd": sum(r[1] for r in rows) if full else None,
                      "bd": sum(r[3] for r in rows) if full else None,
                      "sd1": sd1, "bd1": bd1})
    wrote = keep_newest(OUT, {
        "updated": now,
        "asof": latest,          # 申込日
        "count": len(items),
        "unit": "株",
        "basis": "sd/bd は5営業日前比（直近5日の前日比の和）、sd1/bd1 は前日比",
        "source": "JPX 銘柄別信用取引残高（日次）",
        **({"anomaly": anom} if anom else {}),
        "items": items,
    }, "asof", "銘柄別信用残")
    r1 = sum(1 for x in items if x["s"] > 0 and x["b"] / x["s"] <= 1)
    print(f"週次の推移: {len(wk['weeks'])}週（{wk['weeks'][0]}〜{wk['weeks'][-1]}）")
    if anom:
        print(f"市場全体の買い残が±{ANOMALY_PCT:.0f}%超: {anom}")
    print(f"新規取得 {fetched}日・差し替え {len(replaced)}日 / 蓄積 {len(order)}日（{order[0]}〜{latest}）"
          f" / shinyo_meigara.json {'更新' if wrote else '見送り'}: {len(items)}銘柄 倍率1以下={r1}")


if __name__ == "__main__":
    main()
