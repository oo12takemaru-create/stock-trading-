# -*- coding: utf-8 -*-
"""AI朝刊・週末版の「書き方の指針」をプロンプトに差し込む（2026-11-13〜）

仕組み（学ぶ → 指針に書く → 次の号に効く）:
  - 指針の本体は ai_style_notes.md（統括が毎週そのファイルだけを直す。コードは触らない）
  - ai_analysis.py（朝刊）/ weekly.py（土曜・日曜）がプロンプトを作るたびに読み、
    「# 書き方の指針」の節としてプロンプトに入れる → 翌朝の号から効く
  - 直近5号で3回以上使った言い回しは、機械が数えて同じ節に足す（統括が数えなくてよい）

守ること:
  - 指針で変えられるのは書き方（順番・長さ・言い回し・見出し）だけ。
    スタンスの決め方・入力にない数字を作らない・銘柄名や売買のすすめを書かない・禁止語・答え合わせは
    PROMPT の本体に残り、検査（--answer の validate）も今までどおり。指針の節にも「本体が優先」と書く
  - 指針のファイルが無い・空・壊れている・長すぎるときも、朝刊と週末版は今までどおり書ける
    （読めない行は外して警告だけ。例外は外に出さない）

ファイルの形（ai_style_notes.md）:
  「## 共通」「## 朝刊」「## 週末」「## 土曜」「## 日曜」の下の「- 」で始まる行だけを使う。
  それ以外の行（# のコメント・空行・知らない見出しの下の行）は読まない。
  ルールの行は全体で30行まで（超えた分は外す）。1行160字まで。{ } を含む行と、
  出力の禁止語（買うべき・推奨・「買い」「売り」など）を含む行は外す（例文が本文に漏れないように）。
"""
import json
import re
import sys
from pathlib import Path

NOTES_PATH = Path(__file__).resolve().parent / "ai_style_notes.md"
MAX_RULES = 30
MAX_LINE = 160
# 見出し → その行が入る号の種類（morning=平日の朝刊 / sat=土曜の振り返り / sun=日曜の来週の地図）
SECTIONS = {
    "共通": ("morning", "sat", "sun"),
    "朝刊": ("morning",),
    "週末": ("sat", "sun"),
    "土曜": ("sat",),
    "日曜": ("sun",),
}
# プロンプトの中の節の始まりと終わり（weekly.py の _inputs_only がこの間を取り除く）
BEGIN = "# 書き方の指針"
END = "# （書き方の指針ここまで）"

# 指針の行に入っていたら外す言葉（weekly.py の BANNED_PATTERNS と同じ考え方。例文が本文に漏れないように）
BANNED = [
    r"\d{4}\.T", r"（\d{4}）", r"\(\d{4}\)",
    r"買うべき", r"売るべき", r"必ず上が", r"確実に上が", r"必ず下が", r"確実に下が",
    r"買い時", r"売り時", r"仕込み", r"推奨", r"おすすめ", r"オススメ", r"勝てる", r"儲か",
]
BUYSELL_ALLOW = ["空売り", "買い残", "売り残", "買い戻し", "信用買い", "信用売り",
                 "売買", "買い越し", "売り越し", "売上", "自社株買い"]

# 言い回しを数えるときに読む欄（機械が付ける欄・選択肢の欄は読まない）
SKIP_KEYS = {"match", "bias", "side", "wind", "k", "d", "type", "view", "view_jp", "prev_date",
             "prev_stance", "stance", "machine_stance", "model", "date", "updated", "edition", "kind",
             "weekend", "note", "vol"}
MORNING_KEYS = ("headline", "stance_shift", "stance_reason", "today_watch", "caution", "turn_attack",
                "turn_defense", "review", "world_flow", "sectors", "debate", "deep")
WEEKLY_KEYS = ("title", "view_reason", "points", "world", "flip", "days", "sectors", "miss", "lesson",
               "long_view", "qa")


def _warn(msg):
    print(f"  書き方の指針: {msg}", file=sys.stderr)


def _bad_word(line):
    for pat in BANNED:
        if re.search(pat, line):
            return pat
    t = line
    for a in BUYSELL_ALLOW:
        t = t.replace(a, "")
    m = re.search(r"買い|売り", t)
    return m.group(0) if m else None


def _section_of(title):
    for k in SECTIONS:
        if title.startswith(k):
            return k
    return None


def load_rules(kind, path=None):
    """kind（morning / sat / sun）に入る指針の行。無い・読めない・壊れているときは []（例外を出さない）"""
    p = Path(path) if path else NOTES_PATH
    try:
        text = p.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return []
    except Exception as e:   # 文字コードが壊れている など
        _warn(f"{p.name} を読めず（指針なしで書く）: {e}")
        return []
    rules, total, sec = [], 0, None
    for raw in text.splitlines():
        s = raw.strip()
        m = re.match(r"^##\s*(.+?)\s*$", s)
        if m:
            sec = _section_of(m.group(1))
            continue
        if not s.startswith("- ") or sec is None:
            continue
        body = s[2:].strip()
        if not body:
            continue
        total += 1
        if total > MAX_RULES:
            _warn(f"ルールの行が{MAX_RULES}行を超えたので、{MAX_RULES + 1}行目から外した")
            break
        if len(body) > MAX_LINE:
            _warn(f"{MAX_LINE}字を超える行を外した: {body[:30]}…")
            continue
        if "{" in body or "}" in body:
            _warn(f"{{ }} を含む行を外した: {body[:30]}…")
            continue
        bad = _bad_word(body)
        if bad:
            _warn(f"出力の禁止語（{bad}）を含む行を外した: {body[:30]}…")
            continue
        if kind in SECTIONS[sec]:
            rules.append(body)
    return rules


# ---------------------------------------------------------------- 言い回しを数える

def _strings(x, key=None):
    if key in SKIP_KEYS:
        return
    if isinstance(x, str):
        yield x
    elif isinstance(x, dict):
        for k, v in x.items():
            yield from _strings(v, k)
    elif isinstance(x, list):
        for v in x:
            yield from _strings(v, key)


def prose(entry, keys):
    """号の entry から、AIが書いた文だけをつないだもの"""
    if not isinstance(entry, dict):
        return ""
    return "\n".join(s for k in keys if k in entry for s in _strings(entry[k], k))


# 数字・記号・句読点で区切って、その中の文字の並びだけを数える（数字の入った言い回しは毎回変わるので数えない）
_SPLIT = re.compile(r"[0-9０-９A-Za-zＡ-Ｚａ-ｚ.,，．%％+\-−–〜~・/／()（）「」『』【】\[\]、。:：;；!！?？\s→←]+")
_HIRA = re.compile(r"[ぁ-ゖー]")
_KANJI_KANA = re.compile(r"[一-龯々ァ-ヺ]")
# PROMPT 本体が「この形で書け」と言っている言い回し・アーサーの締めの決まり文句は、くり返しても癖ではないので数える前に消す
_STOP = ("可能性", "になりやすい", "しやすい", "やすい", "次に見るのは", "次に見る数字")


def data_only(text, template="", drop_sections=()):
    """言い回しを数えるときに外す「データの用語」の元。
    text（入力データ・プロンプト）から、template（PROMPT 本体）の地の文と、
    drop_sections（前回のAI分析など、AI自身が書いた文の節）を取り除く"""
    t = text or ""
    for h in drop_sections:
        t = re.sub(re.escape(h) + r".*?(?=\n#|\Z)", "\n", t, flags=re.S)
    if template:
        for chunk in re.split(r"\{[a-z_0-9]+\}", template.replace("{{", "{").replace("}}", "}")):
            if len(chunk.strip()) >= 8:
                t = t.replace(chunk, "\n")
    return t
_PARTICLE_HEAD = "ゃゅょっぁぃぅぇぉーをんはがのにでとへもやか"


def repeated_phrases(texts, min_issues=3, min_len=6, max_len=16, top=8, exclude=""):
    """texts（1号＝1つの文字列）の中で、min_issues 号以上に出た言い回し。[(言い回し, 号数)]（多い順）。
    ひらがなを3割以上含み、漢字かカタカナを1字以上含む6〜16字の並びだけを見る（業種名などの用語を拾わない）。
    exclude（その日の入力データ・PROMPT 本体）にそのまま出てくる並びは、データの用語なので外す"""
    texts = [t for t in texts if t]
    if len(texts) < min_issues:
        return []
    df = {}
    for t in texts:
        for s in _STOP:
            t = t.replace(s, "|")
        grams = set()
        for run in _SPLIT.split(t.replace("|", " ")):
            n = len(run)
            # 語の途中で切れた並び（「た翌日に」「比重が大き」）を数えないよう、切れ目は
            # 始まり＝区切りの直後か「ひらがな→漢字・カタカナ」、終わり＝区切りの直前か次が漢字・カタカナ、に限る
            starts = [i for i in range(n) if i == 0 or (_KANJI_KANA.match(run[i]) and _HIRA.match(run[i - 1]))]
            ends = {j for j in range(1, n + 1)
                    if j == n or (_HIRA.match(run[j - 1]) and _KANJI_KANA.match(run[j]))}
            for i in starts:
                for L in range(min_len, min(max_len, n - i) + 1):
                    if i + L not in ends:
                        continue
                    g = run[i:i + L]
                    if len(_HIRA.findall(g)) < L * 0.3 or not _KANJI_KANA.search(g):
                        continue
                    grams.add(g)
        for g in grams:
            df[g] = df.get(g, 0) + 1
    def is_term(g):
        # 入力の用語＋前後の助詞（「米10年債利回りは」「条件なしの全期間では」）も用語として外す
        return any(len(g) - a - b >= 4 and g[a:len(g) - b] in exclude for a in (0, 1) for b in (0, 1, 2))

    hits = {g: c for g, c in df.items() if c >= min_issues and not (exclude and is_term(g))}
    # 同じ号数で、もっと長い並びに含まれるものは外す（いちばん長い形だけ残す）
    keep = []
    for g, c in sorted(hits.items(), key=lambda kv: (-len(kv[0]), -kv[1])):
        if any(g in k and c <= kc for k, kc in keep):
            continue
        keep.append((g, c))
    # 見せるときは頭の助詞を落とす（「も併せて確認したい」→「併せて確認したい」）
    out, seen = [], set()
    for g, c in sorted(keep, key=lambda kv: (-kv[1], -len(kv[0]))):
        s = g.lstrip(_PARTICLE_HEAD)
        if len(s) < min_len - 1 or s in seen:
            continue
        # すでに選んだ言い回しと5字以上重なるもの（同じ文の別の切り方）は外す
        if any(_overlap(s, o) >= 5 for o, _c in out):
            continue
        seen.add(s)
        out.append((s, c))
    return out[:top]


def _overlap(a, b):
    """a と b に共通する、いちばん長い連続した並びの長さ"""
    best = 0
    for i in range(len(a)):
        for j in range(i + best + 1, len(a) + 1):
            if a[i:j] in b:
                best = j - i
            else:
                break
    return best


def recent_morning_texts(dst, today, n=5):
    """docs/ai_analysis.json の latest + history から、今日より前の直近 n 号の文"""
    try:
        d = json.loads(Path(dst).read_text(encoding="utf-8"))
    except Exception:
        return []
    es = [e for e in [d.get("latest")] + list(d.get("history") or []) if isinstance(e, dict) and e.get("date")]
    seen, out = set(), []
    for e in sorted(es, key=lambda e: e["date"], reverse=True):
        if e["date"] >= today or e["date"] in seen:
            continue
        seen.add(e["date"])
        out.append(prose(e, MORNING_KEYS))
        if len(out) >= n:
            break
    return out


def recent_weekly_texts(days_dir, today, n=5):
    """docs/ai_weekly_days/*.json（土曜・日曜の記録）から、今日より前の直近 n 号の文"""
    es = []
    try:
        for p in Path(days_dir).glob("????-??-??-s??.json"):
            try:
                e = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(e, dict) and e.get("date") and e["date"] < today:
                es.append(e)
    except Exception:
        return []
    es.sort(key=lambda e: e["date"], reverse=True)
    return [prose(e, WEEKLY_KEYS) for e in es[:n]]


# ---------------------------------------------------------------- プロンプトの節

def style_block(kind, recent_texts=None, path=None, exclude=""):
    """プロンプトに入れる「書き方の指針」の節。指針も言い回しも無ければ ""（今までどおりのプロンプト）。
    exclude: その日の入力データと PROMPT 本体（そこにある言葉は言い回しとして数えない）"""
    try:
        rules = load_rules(kind, path)
    except Exception as e:   # 念のため（ここで朝刊を止めない）
        _warn(f"読めず（指針なしで書く）: {e}")
        rules = []
    try:
        reps = repeated_phrases(recent_texts or [], exclude=exclude)
    except Exception as e:
        _warn(f"言い回しを数えられず: {e}")
        reps = []
    if not rules and not reps:
        return ""
    lines = [BEGIN + "（毎週見直す・書き方だけ）",
             "次は読みやすさのための指針です。上の役割・厳守事項、下の出力の形・字数の上限とぶつかるときは、"
             "そちらを優先してください。指針のために入力にない数字を作ったり、判定を変えたりしないこと。"]
    lines += [f"- {r}" for r in rules]
    if reps:
        n = len([t for t in (recent_texts or []) if t])
        lines.append(f"- 直近{n}号で続けて使った言い回し（今回は使わず、別の言い方にする）: "
                     + "／".join(f"「{g}」" for g, _c in reps))
    lines.append(END)
    return "\n".join(lines) + "\n"


def strip_block(text):
    """プロンプトから「書き方の指針」の節を取り除く（入力データの数字だけで照らす検査のため）"""
    return re.sub(re.escape(BEGIN) + r".*?" + re.escape(END), "\n", text, flags=re.S)


if __name__ == "__main__":
    # 手元の確認用: python ai_style.py morning|sat|sun → その号に入る節を表示
    k = sys.argv[1] if len(sys.argv) > 1 else "morning"
    from datetime import datetime, timedelta, timezone
    today = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d")
    texts = (recent_morning_texts("docs/ai_analysis.json", today) if k == "morning"
             else recent_weekly_texts("docs/ai_weekly_days", today))
    print(style_block(k, texts) or "（指針なし）")
