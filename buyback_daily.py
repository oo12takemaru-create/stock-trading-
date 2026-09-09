# -*- coding: utf-8 -*-
"""自社株買い: Workerが貯めた一覧を読み、新規分のPDFから数値を抜く

    docs/buyback.json          直近60日・全項目
    docs/buyback_history.json  全件・追記のみ（消さない）

■ 2段構えの後半
  Worker(kaburadar-buyback)が10分ごとにTDnetの一覧をKVへ貯めている。
  こちらは「中身を読む」担当。Actionsのcronが数時間遅れても、
  一覧はWorker側に残っているので取りこぼさない。

■ 数値は取れなければ空。推測で埋めない
  PDFを読むのは decision / tostnet3 / change のみ。
  progress（毎月の進捗・件数が多い）と other は一覧の情報だけ持つ。

■ 品質の基準（2026-09-07 合意）
  decision と tostnet3 の pct・amount_max を8割以上に保つ。
  change は訂正・中止が中心で原文に数値が無いことが多いため対象外。
  tostnet3 の「期間」は単日買付なので対象外（buy_date に入れる）。

■ ToSTNeT-3 の買付日（2026-09-09 合意）
  本文に「2026年9月7日午前8時45分の…立会外買付取引において買付けの委託を行う」と
  書かれるので、そこから取る（buy_date_src="pdf"）。
  TDnetのPDFは27営業日で消えるため、古い開示は本文を読めない。その分は
  制度上の日付を機械的に入れ、buy_date_src="rule" で必ず区別する:
    「買付価格確定 / 取得結果」型 = その日の朝に買い付けた結果の報告 → 開示日そのもの
    「買付け」型                  = 引け後の開示（実測で全件16〜17時）→ 翌営業日の寄付前
  どちらの型にも当てはまらない表題は空のまま残し、件数と表題例を出す。
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from buyback_classify import classify, normalize   # noqa: E402
from buyback_extract import extract                # noqa: E402
from jp_bizday import next_bizday                  # noqa: E402

JST = timezone(timedelta(hours=9))
HERE = Path(__file__).parent
WORKER = os.environ.get(
    "BUYBACK_WORKER",
    "https://kaburadar-buyback.oo12takemaru.workers.dev") + "/buyback.json"
OUT_RECENT = HERE / "docs" / "buyback.json"
OUT_HISTORY = HERE / "docs" / "buyback_history.json"

# PDFを読む種別。progress は毎月の進捗で件数が多く、決議の数値は載らない
PDF_TYPES = {"decision", "tostnet3", "change"}
RECENT_DAYS = 60
# 1回の実行で読むPDFの上限。初回は多いので分けて処理する（TDnetに優しく）
MAX_PDF = int(os.environ.get("BUYBACK_MAX_PDF", "260"))

UA = {"User-Agent": "Mozilla/5.0 (compatible; kaburadar.jp/1.0; +https://kaburadar.jp)"}


def get_json(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def load(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def fetch_pdf(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


# ToSTNeT-3 の買付日を、本文から取れないときに制度から決めるルール
RE_RESULT = re.compile(r"買付価格確定|取得結果|買付.?結果|買付価格の確定")
RE_ORDER = re.compile(r"買付|取得")


def buy_date_by_rule(rec):
    """制度上の買付日。当てはまらなければ None（推測では埋めない）"""
    t = normalize(rec["title"])
    if RE_RESULT.search(t):
        return rec["date"]                  # その日の朝の買付けを引け後に報告している
    if RE_ORDER.search(t):
        return next_bizday(rec["date"])     # 引け後の開示 → 翌営業日の寄付前に執行
    return None


def fill_buy_date(known, budget):
    """buy_date_src を持たない ToSTNeT-3 に買付日を入れる。

    まだ生きているPDFは本文から取り（pdf）、消えたものはルールで埋める（rule）。
    一度 buy_date_src が付けば二度と読み直さないので、次回以降は素通りする。
    古い buy_date（period_from を流用していた誤り）はここで捨てて入れ直す。"""
    todo = [r for r in known.values()
            if r["type"] == "tostnet3" and "buy_date_src" not in r]
    todo.sort(key=lambda r: r["date"], reverse=True)   # 新しい＝PDFが生きている順
    n_pdf = n_rule = 0
    unmatched = []
    fetched = 0
    for rec in todo:
        rec.pop("buy_date", None)
        bd = None
        if fetched < budget:
            fetched += 1                    # 404も1回のアクセス。連打しないよう必ず数える
            try:
                bd = extract(fetch_pdf(rec["pdf"]), year_hint=rec["date"][:4]).get("buy_date")
            except Exception:
                pass                        # 404＝27営業日を過ぎて消えた。ルールに回す
            time.sleep(0.35)
        if bd:
            rec["buy_date"], rec["buy_date_src"] = bd, "pdf"
            n_pdf += 1
            continue
        bd = buy_date_by_rule(rec)
        if bd:
            rec["buy_date"], rec["buy_date_src"] = bd, "rule"
            n_rule += 1
        else:
            unmatched.append(rec)           # 空のまま残す
    return n_pdf, n_rule, unmatched


def link_parent(rec, history_index):
    """change / tostnet3 / complete に、同じ銘柄の直近60日の decision を紐づける。
    見つからなければ付けない（無理に結び付けない）"""
    if rec["type"] not in ("change", "tostnet3", "complete", "progress"):
        return None
    lo = (datetime.fromisoformat(rec["date"]) - timedelta(days=60)).date().isoformat()
    best = None
    for h in history_index.get(rec["code"], []):
        if h["type"] != "decision":
            continue
        if lo <= h["date"] <= rec["date"] and (best is None or h["date"] > best["date"]):
            best = h
    return best["id"] if best else None


def main():
    now = datetime.now(JST)
    try:
        feed = get_json(WORKER)
    except Exception as e:
        print(f"Workerから取得できません: {e}", file=sys.stderr)
        sys.exit(1)
    items = feed.get("items", [])
    print(f"Worker: {len(items)}件（{feed.get('range_from')}以降）")

    history = load(OUT_HISTORY, {"items": []})
    known = {h["id"]: h for h in history.get("items", [])}

    # 分類はWorker側の値をそのまま使わず、こちらでも計算して食い違いを検出する
    mismatch = 0
    for it in items:
        ty, tos = classify(it["title"])
        if it.get("type") and it["type"] != ty:
            mismatch += 1
        it["type"], it["tostnet"] = ty, tos
    if mismatch:
        print(f"⚠ 分類がWorkerと食い違う: {mismatch}件（classify.js と buyback_classify.py を確認）",
              file=sys.stderr)

    # 「まだPDFを読んでいないもの」を対象にする。
    # 一覧だけ先に保存したレコード（extract_ok を持たない）も読み直す対象に含める。
    # id の有無だけで判定すると、一覧だけ入った決議が永久に未抽出のまま残る
    def needs_pdf(it):
        if it["type"] not in PDF_TYPES:
            return False
        h = known.get(it["id"])
        return h is None or "extract_ok" not in h

    todo = [it for it in items if needs_pdf(it)]
    todo.sort(key=lambda r: r["date"], reverse=True)   # 新しいものから
    print(f"PDFを読む対象: {len(todo)}件（上限{MAX_PDF}）")

    done = 0
    for it in todo[:MAX_PDF]:
        rec = dict(it)
        try:
            rec.update(extract(fetch_pdf(it["pdf"]), year_hint=it["date"][:4]))
        except Exception as e:
            rec.update({"extract_ok": False, "extract_note": f"PDF取得失敗: {e}"})
        known[it["id"]] = rec
        done += 1
        time.sleep(0.35)
    if len(todo) > MAX_PDF:
        print(f"  残り{len(todo)-MAX_PDF}件は次回に回します")

    # PDFを読まない種別も一覧情報だけ残す（履歴は欠けさせない）
    for it in items:
        known.setdefault(it["id"], dict(it))

    # ToSTNeT-3 の買付日を埋める（本文優先・消えたPDFはルール）
    n_pdf, n_rule, unmatched = fill_buy_date(known, budget=int(os.environ.get("BUYBACK_MAX_BUYDATE", "150")))

    # 親の決議を紐づける
    by_code = {}
    for h in known.values():
        by_code.setdefault(h["code"], []).append(h)
    for h in known.values():
        pid = link_parent(h, by_code)
        if pid:
            h["parent_id"] = pid

    allrows = sorted(known.values(), key=lambda r: (r["date"], r["time"], r["id"]))
    lo = (now - timedelta(days=RECENT_DAYS)).date().isoformat()
    recent = [r for r in allrows if r["date"] >= lo]

    def buy_date_src_counts(rows):
        c = {}
        for r in rows:
            if r["type"] != "tostnet3":
                continue
            k = r.get("buy_date_src", "none")
            c[k] = c.get(k, 0) + 1
        return c

    def stats(rows):
        out = {}
        for ty in ("decision", "tostnet3"):
            s = [r for r in rows if r["type"] == ty and "extract_ok" in r]
            if not s:
                continue
            out[ty] = {
                "n": len(s),
                "pct": round(100 * sum(1 for r in s if r.get("pct") is not None) / len(s), 1),
                "amount_max": round(100 * sum(1 for r in s if r.get("amount_max") is not None) / len(s), 1),
            }
        return out

    meta = {
        "updated": now.isoformat(timespec="seconds"),
        "source": "TDnet（適時開示情報閲覧サービス）の公表資料",
        "note": ("表題から機械的に分類し、決議・ToSTNeT-3・変更のPDFから数値を抜いたもの。"
                 "取れなかった項目は空。推測では埋めていない。"
                 "progressは毎月の取得状況報告、otherは自己株式の処分など取得以外。"),
        "types": ["decision", "tostnet3", "progress", "complete", "cancel", "change", "other"],
        "extract_rate": stats(allrows),
        # ToSTNeT-3 の買付日をどこから得たか。pdf=本文に書かれていた / rule=制度から機械的に決めた
        # （TDnetのPDFは27営業日で消えるため、古い開示は本文を読めない）
        "buy_date_src": buy_date_src_counts(allrows),
    }
    counts = {}
    for r in recent:
        counts[r["type"]] = counts.get(r["type"], 0) + 1

    OUT_RECENT.parent.mkdir(parents=True, exist_ok=True)
    OUT_RECENT.write_text(json.dumps(
        {**meta, "range_from": lo, "count": len(recent), "type_counts": counts, "items": recent},
        ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    OUT_HISTORY.write_text(json.dumps(
        {**meta, "count": len(allrows), "items": allrows},
        ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    print(f"OK buyback.json: {len(recent)}件（{lo}以降） / buyback_history.json: {len(allrows)}件")
    print(f"  今回PDFを読んだ: {done}件")
    tos = [r for r in allrows if r["type"] == "tostnet3"]
    src = {}
    for r in tos:
        src[r.get("buy_date_src", "なし")] = src.get(r.get("buy_date_src", "なし"), 0) + 1
    print(f"  ToSTNeT-3 買付日 {len(tos)}件: " +
          " ".join(f"{k}={v}" for k, v in sorted(src.items())) +
          f"（今回 pdf={n_pdf} rule={n_rule}）")
    if unmatched:
        print(f"  ⚠ ルールが当てはまらず空のまま: {len(unmatched)}件", file=sys.stderr)
        for r in unmatched[:10]:
            print(f"      {r['date']} {r['code']} {r['title']}", file=sys.stderr)
    for ty, s in meta["extract_rate"].items():
        flag = " ⚠8割未満" if min(s["pct"], s["amount_max"]) < 80 else ""
        print(f"  {ty:9s} n={s['n']:4d} pct={s['pct']:5.1f}% amount={s['amount_max']:5.1f}%{flag}")


if __name__ == "__main__":
    main()
