# -*- coding: utf-8 -*-
"""銘柄カルテの「AIの読み上げ」（層2）の取り込み（2026-11-04・起動文_実務_カルテの読み上げ_2026-11-04.md）

routine が ai-karte/YYYY-MM-DD-N に push した ai_answer.json を、同じ番号の ai-prompt/karte-YYYY-MM-DD-N の
facts.json（routine に渡した入力そのもの）と照らして検査し、通った銘柄だけを
  docs/karte_ai/latest.json   … 銘柄コード → {t: 文, d: 日付, m: モデル, b: バッチ}（kaburadar の gen_stocks.py が読む）
  docs/karte_ai/YYYY-MM-DD.json … その日の記録（バッチごとの件数・落ちた理由・通った文と入力の事実）。1か月後の答え合わせの元
に入れる。

■ 銘柄ごとの検査（1つでも落ちたらその銘柄は捨てる。ほかの銘柄は通す）
  - 銘柄コードが入力にある・同じ銘柄が2回出ない
  - 文中の数字がすべて、その銘柄の入力（facts）にある（全角は半角に、1.20 と 1.2 は同じとみなす）
  - 120字以内・1〜2文
  - 禁止語なし（判断の言葉・将来の値動き。「買い」「売り」は語の一部でも不可。売買代金・空売り・買い越し・売り越し・買い残・売り残 は可）
■ 回答そのものが壊れている（JSON でない・batch が入力と違う）ときは exit 2（ブランチは調査用に残る）

使い方:
  python -X utf8 karte_ai_ingest.py --answer ai_answer.json --facts facts.json [--model-label claude-sonnet-5-5] [--docs docs]
"""
import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

JST = timezone(timedelta(hours=9))
MAX_LEN = 120
KEEP_DAYS = 14          # latest.json に残す日数（サイトは8日以内だけ出す）

# 判断の言葉（起動文の層1と同じ）＋ 将来の値動き ＋ gen_stocks.py の BANNED
BANNED = ["良い", "悪い", "割安", "割高", "強い", "弱い", "注目", "推奨", "おすすめ", "オススメ", "予想",
          "有望", "危険", "警戒", "チャンス", "シグナル", "狙い目", "妙味", "過熱", "仕込み", "利確",
          "買うべき", "売るべき", "見込み", "見込ま", "期待", "可能性", "今後", "だろう", "でしょう", "はず",
          "上がりそう", "下がりそう", "反発しそう", "必ず", "確実"]
# 「買い」「売り」は語の一部でも不可。先に取り除いてよい名詞（weekly.py の BUYSELL_ALLOW・gen_stocks.py の ALLOW と同じ）
BUYSELL_ALLOW = ["空売り", "買い残", "売り残", "買い戻し", "信用買い", "信用売り",
                 "売買", "買い越し", "売り越し", "売上"]
Z2H = str.maketrans("０１２３４５６７８９．，－＋％", "0123456789.,-+%")
NUM_RE = re.compile(r"\d+(?:\.\d+)?")


def norm_num(s):
    try:
        d = Decimal(s)
    except InvalidOperation:
        return s
    t = format(d.normalize(), "f")
    return t


def nums(text):
    t = text.translate(Z2H)
    t = re.sub(r"(?<=\d),(?=\d{3})", "", t)       # 3,028 → 3028
    return [norm_num(x) for x in NUM_RE.findall(t)]


def banned_word(text):
    for b in BANNED:
        if b in text:
            return b
    t = text
    for a in BUYSELL_ALLOW:
        t = t.replace(a, "")
    m = re.search(r"買い|売り", t)
    return m.group(0) if m else None


RATIO_CAP = 100      # これを超える信用倍率は数字を出さない（売り残がごく少ない・Fable 判断 2026-11-04）


def ratio_over(text):
    """「信用倍率」を含む文の中の「◯倍」で RATIO_CAP を超えるもの（あれば最初の1つ）"""
    for sent in re.split(r"[。]", text.translate(Z2H)):
        if "信用倍率" not in sent and "週次" not in sent:
            continue
        for x in re.findall(r"(\d[\d,]*(?:\.\d+)?)倍", sent):
            if float(x.replace(",", "")) > RATIO_CAP:
                return x
    return None


def check_one(c, t, facts):
    if not isinstance(t, str) or not t.strip():
        return "文が空"
    t = t.strip()
    if len(t) > MAX_LEN:
        return f"{MAX_LEN}字超（{len(t)}字）"
    n_sent = len([x for x in re.split(r"[。！？]", t) if x.strip()])
    if n_sent > 2:
        return f"3文以上（{n_sent}文）"
    w = banned_word(t)
    if w:
        return f"禁止語「{w}」"
    big = ratio_over(t)
    if big:
        return f"100倍超の信用倍率 {big}"
    have = set(nums(facts))
    miss = [x for x in nums(t) if x not in have]
    if miss:
        return "入力に無い数字 " + ",".join(miss[:5])
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--answer", required=True)
    ap.add_argument("--facts", required=True)
    ap.add_argument("--model-label", default="")
    ap.add_argument("--docs", default="docs")
    a = ap.parse_args()

    F = json.loads(Path(a.facts).read_text(encoding="utf-8"))
    raw = Path(a.answer).read_text(encoding="utf-8").strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
    try:
        ans = json.loads(raw)
    except Exception as e:
        print(f"::error::ai_answer.json が JSON として読めません: {e}", file=sys.stderr)
        sys.exit(2)
    if not isinstance(ans, dict) or not isinstance(ans.get("items"), list):
        print("::error::ai_answer.json に items の配列がありません", file=sys.stderr)
        sys.exit(2)
    if ans.get("batch") != F["batch"]:
        print(f"::error::batch が入力と違います（回答 {ans.get('batch')} ／ 入力 {F['batch']}）", file=sys.stderr)
        sys.exit(2)

    day = F["date"]
    model = a.model_label or F.get("model") or ""
    bid = F["batch"].split(":")[0]
    n = bid.rsplit("-", 1)[1]
    codes = F["codes"]
    ok, drops, seen = {}, [], set()
    for it in ans["items"]:
        c = str((it or {}).get("c") or "")
        t = (it or {}).get("t")
        if c not in F["facts"]:
            drops.append({"c": c, "why": "入力に無い銘柄コード"})
            continue
        if c in seen:
            drops.append({"c": c, "why": "同じ銘柄が2回"})
            ok.pop(c, None)
            continue
        seen.add(c)
        why = check_one(c, t, F["facts"][c])
        if why:
            drops.append({"c": c, "why": why, "t": t})
        else:
            ok[c] = t.strip()
    missing = [c for c in codes if c not in seen]
    for c in missing:
        drops.append({"c": c, "why": "回答に無い"})

    out = Path(a.docs) / "karte_ai"
    out.mkdir(parents=True, exist_ok=True)
    now = datetime.now(JST).isoformat(timespec="seconds")

    # その日の記録（バッチごと）。同じバッチを取り込み直したら上書き
    rec_p = out / f"{day}.json"
    rec = json.loads(rec_p.read_text(encoding="utf-8")) if rec_p.exists() else {"date": day, "batches": {}, "items": {}}
    for c in list(rec["items"]):
        if rec["items"][c].get("b") == bid:
            del rec["items"][c]
    rec["batches"][n] = {"batch": F["batch"], "mode": F.get("mode"), "model": model, "ingested": now,
                         "n_in": len(codes), "n_ok": len(ok), "n_drop": len(drops), "drops": drops}
    for c, t in ok.items():
        rec["items"][c] = {"t": t, "b": bid, "ev": F.get("events", {}).get(c, []), "facts": F["facts"][c]}
    rec_p.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")

    # 最新（サイトが読む）。古いものは KEEP_DAYS で落とす
    lat_p = out / "latest.json"
    lat = json.loads(lat_p.read_text(encoding="utf-8")) if lat_p.exists() else {"items": {}}
    cut = (date.fromisoformat(day) - timedelta(days=KEEP_DAYS)).isoformat()
    items = {c: v for c, v in (lat.get("items") or {}).items() if v.get("d", "") >= cut}
    for c, t in ok.items():
        items[c] = {"t": t, "d": day, "m": model, "b": bid}
    lat_p.write_text(json.dumps({"updated": now, "note": "カルテの数字をAIが読み上げたもの（投資判断ではない）。"
                                 "文中の数字はすべて入力の事実にあるものだけ（karte_ai_ingest.py が検査）",
                                 "count": len(items), "items": items},
                                ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    # 日付の一覧（答え合わせ用）
    idx = sorted(p.stem for p in out.glob("20??-??-??.json"))
    (out / "index.json").write_text(json.dumps({"dates": idx}, ensure_ascii=False), encoding="utf-8")

    print(f"{bid}: 入力 {len(codes)}銘柄 → 通過 {len(ok)}・不採用 {len(drops)}（latest {len(items)}銘柄）")
    from collections import Counter
    why = Counter(re.sub(r"[ （].*", "", d["why"]) for d in drops)
    for k, v in why.most_common():
        print(f"  不採用の理由: {k} {v}件")
    for d in drops[:20]:
        print(f"   - {d['c']}: {d['why']}" + (f"｜{d.get('t')}" if d.get("t") else ""))


if __name__ == "__main__":
    main()
