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
■ 2026-11-11〜（起動文_実務_アーサーのひとことと今日の発見_2026-11-11.md）
  - items の "o"（アーサーのひとこと）: t とは別に検査（20〜80字・1〜2文・禁止語・数字は事実か共通の日付にあるもの・
    信用/空売りの数値を入れない＝メールに載るため）→ docs/karte_ai/one.json（コード → {o, d, m}）。o が無い回答は今までどおり
  - "hakken"（今日の発見・1バッチ目だけ）: 選んだ銘柄は facts.json の hakken（機械）が正本。見出し15字以内・本文2〜4文220字以内
    → docs/hakken/YYYY-MM-DD.json。文が落ちても銘柄と点の内訳は出す。どの回の取り込みでも5営業日後の「その後」を追記
    （hakken.followups → docs/hakken/latest.json・record.json・index.json）

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


# ─────────────────────────── アーサーのひとこと（o）・今日の発見（hakken）（2026-11-11）
O_MIN, O_MAX = 20, 80            # 書き方は 40〜70字。取り込みは少し幅を持たせる
HK_H_MAX, HK_T_MAX = 15, 220
# ひとこと・今日の発見で追加で止める言葉（起動文の言葉の決まり）
ARTHUR_BANNED = ["リアルタイム", "面白い", "アーサーです"]
# ひとことはメールにも載る → JPX 由来の数値（信用倍率・信用残・空売り残高）を入れない
JPX_WORD = re.compile(r"信用|空売り|買い残|売り残|貸借")
JPX_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?\s*(?:倍|%|％|ポイント|pt|株)")


def jpx_number(text):
    """「信用」「空売り」などを含む句に、倍・%・ポイント・株 の数値があれば、その句を返す"""
    for part in re.split(r"[。、，,]|で、|が、", text.translate(Z2H)):
        if JPX_WORD.search(part) and JPX_NUM.search(part):
            return part.strip()
    return None


def common_check(t, have_text, max_len, min_len=1, max_sent=2, min_sent=1):
    if not isinstance(t, str) or not t.strip():
        return "文が空"
    t = t.strip()
    if len(t) > max_len:
        return f"{max_len}字超（{len(t)}字）"
    if len(t) < min_len:
        return f"{min_len}字未満（{len(t)}字）"
    n_sent = len([x for x in re.split(r"[。！？]", t) if x.strip()])
    if n_sent > max_sent or n_sent < min_sent:
        return f"文の数が{n_sent}"
    w = banned_word(t) or next((b for b in ARTHUR_BANNED if b in t), None)
    if w:
        return f"禁止語「{w}」"
    big = ratio_over(t)
    if big:
        return f"100倍超の信用倍率 {big}"
    have = set(nums(have_text))
    miss = [x for x in nums(t) if x not in have]
    if miss:
        return "入力に無い数字 " + ",".join(miss[:5])
    return None


def check_o(o, facts, common):
    why = common_check(o, facts + "\n" + (common or ""), O_MAX, O_MIN)
    if why:
        return why
    j = jpx_number(o)
    if j:
        return f"JPX の数値（{j[:20]}）"
    return None


def check_hakken(h, t, material, common):
    if not isinstance(h, str) or not h.strip():
        return "見出しが空"
    h = h.strip()
    if len(h) > HK_H_MAX:
        return f"見出し{HK_H_MAX}字超（{len(h)}字）"
    if nums(h):
        return "見出しに数字"
    w = banned_word(h) or next((b for b in ARTHUR_BANNED if b in h), None)
    if w:
        return f"見出しに禁止語「{w}」"
    return common_check(t, material + "\n" + (common or ""), HK_T_MAX, 40, 4, 2)


ONE_KEEP_DAYS = 10      # one.json に残す日数（サイト・メールは7日以内だけ出す）


def cut_one(day):
    return (date.fromisoformat(day) - timedelta(days=ONE_KEEP_DAYS)).isoformat()


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

    # アーサーのひとこと（o）。t とは別に検査する（o が落ちても t は出る・o が無い回答は今までどおり）
    common = F.get("common") or ""
    one_ok, one_drops, oseen = {}, [], set()
    for it in ans["items"]:
        c = str((it or {}).get("c") or "")
        o = (it or {}).get("o")
        if c not in F["facts"] or c in oseen or o is None or (isinstance(o, str) and not o.strip()):
            continue
        oseen.add(c)
        why = check_o(o, F["facts"][c], common)
        if why:
            one_drops.append({"c": c, "why": why, "o": o})
        else:
            one_ok[c] = o.strip()

    # 今日の発見（1バッチ目だけ）。選んだ銘柄は facts.json の hakken（機械）が正本。文が落ちても銘柄と材料は出す
    H = F.get("hakken")
    hk_ok, hk_drops = {}, []
    if H and H.get("picks"):
        got = {str((x or {}).get("c") or ""): x for x in (ans.get("hakken") or []) if isinstance(x, dict)}
        for x in H["picks"]:
            c = x["c"]
            g = got.get(c)
            if not g:
                hk_drops.append({"c": c, "why": "回答に無い"})
                continue
            why = check_hakken(g.get("h"), g.get("t"), (H.get("material") or {}).get(c, ""), common)
            if why:
                hk_drops.append({"c": c, "why": why, "h": g.get("h"), "t": g.get("t")})
            else:
                hk_ok[c] = {"h": g["h"].strip(), "t": g["t"].strip()}

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
                         "n_in": len(codes), "n_ok": len(ok), "n_drop": len(drops), "drops": drops,
                         "n_one": len(one_ok), "one_drops": one_drops}
    for c, t in ok.items():
        rec["items"][c] = {"t": t, "b": bid, "ev": F.get("events", {}).get(c, []), "facts": F["facts"][c]}
    # ひとことも翌月の答え合わせの元として残す（t が落ちた銘柄でも o は残す）
    for c, o in one_ok.items():
        e = rec["items"].setdefault(c, {"b": bid, "ev": F.get("events", {}).get(c, []), "facts": F["facts"][c]})
        e["o"] = o
    if common:
        rec.setdefault("common", {})[n] = common
    rec_p.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")

    # ひとことの最新（サイトのカルテ・保有ボード・朝の1通が読む）。7日より古い文は出さない（サイト側でも見る）
    one_p = out / "one.json"
    one = json.loads(one_p.read_text(encoding="utf-8")) if one_p.exists() else {"items": {}}
    oi = {c: v for c, v in (one.get("items") or {}).items() if v.get("d", "") >= cut_one(day)}
    for c, o in one_ok.items():
        oi[c] = {"o": o, "d": day, "m": model}
    one_p.write_text(json.dumps({"updated": now, "note": "アーサーのひとこと（AI・株レーダー専属AIエコノミスト）。"
                                 "文中の数字はすべて入力の事実にあるものだけ。事実の整理で、売買のすすめではありません",
                                 "count": len(oi), "items": oi}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    # 今日の発見（1バッチ目のときだけ day の記録を作る）と、5営業日後の「その後」の追記（どのバッチの取り込みでも回す）
    hk_line = ""
    try:
        import hakken as HK
        if H and H.get("picks"):
            HK.save_day(a.docs, H, hk_ok, model, now, hk_drops)
            hk_line = f"今日の発見: {len(H['picks'])}銘柄（文が通った {len(hk_ok)}・差し戻し {len(hk_drops)}）"
        fu = HK.followups(a.docs, now)
        if fu:
            hk_line += f"／その後の追記 {fu['filled']}件（通算 {fu['done']}/{fu['all']}）"
    except Exception as e:      # 発見の失敗で読み上げの取り込みを止めない
        print(f"::warning::今日の発見の取り込みに失敗: {e}", file=sys.stderr)

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
    # ひとこと・今日の発見は「差し戻し」と書く（上の「不採用」＝t とは別。t は出る）
    print(f"アーサーのひとこと: 通過 {len(one_ok)}・差し戻し {len(one_drops)}（one.json {len(oi)}銘柄）")
    for d in one_drops[:20]:
        print(f"   - ひとことの差し戻し {d['c']}: {d['why']}｜{d.get('o')}")
    if hk_line:
        print(hk_line)
    for d in hk_drops:
        print(f"   - 今日の発見の差し戻し {d['c']}: {d['why']}" + (f"｜{d.get('h')}｜{d.get('t')}" if d.get("t") else ""))


if __name__ == "__main__":
    main()
