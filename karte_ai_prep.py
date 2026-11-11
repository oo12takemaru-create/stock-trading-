# -*- coding: utf-8 -*-
"""銘柄カルテの「AIの読み上げ」（層2）の前段（2026-11-04・起動文_実務_カルテの読み上げ_2026-11-04.md）

■ 仕組み（AI朝刊と同じ prep → routine → ingest）
  1. このスクリプト（karte-ai-prep.yml）: 対象の銘柄を選び、銘柄ごとの事実（数字と出所の日付だけ）を
     50銘柄ずつ1つの PROMPT.txt にまとめる。facts.json（ingest が「文中の数字が入力にあるか」を照らす元）も一緒に作る。
     → ブランチ ai-prompt/karte-YYYY-MM-DD-N（N=1,2,3…）
  2. Claude Code 定期実行（routine・claude-sonnet-5-5）: PROMPT.txt を読み、ai_answer.json を
     ブランチ ai-karte/YYYY-MM-DD-N に push（1回の実行で1バッチ＝50銘柄をまとめて書く）
  3. karte_ai_ingest.py（karte-ai-ingest.yml）: 検査して docs/karte_ai/latest.json と日付の記録に入れる

■ 対象
  - top  : 日曜。ヒートマップの銘柄を売買代金の大きい順に TOP_N（150）→ 50ずつ3バッチ
  - event: 平日 19:30。その日に出来事があった銘柄（決算速報・ストップ高/安・大口の空売りの新規報告・株式分割の発表）
           を売買代金の大きい順に最大 EVENT_MAX（50）→ 1バッチ。0件ならブランチを作らない（exit 3）
           （2026-11-11〜）2バッチ目＝朝の1通に登録された銘柄（kr_notify の codes・コードだけ）＋カルテの閲覧上位20
           を最大 WATCH_MAX（50）。定期実行が2回起きる日だけ書かれる（1回なら今までどおり1バッチ目だけ）。
           1バッチ目の末尾に「今日の発見」（hakken.py が選んだ3銘柄と材料）を足す

■ 書くもの（2026-11-11〜）
  - t: AIの読み上げ（今までどおり）
  - o: アーサーのひとこと（40〜70字・です・ます。カルテの冒頭・保有ボード・朝の1通に出る）
  - hakken: 今日の発見の3銘柄の見出しと3文（1バッチ目だけ）

■ 入力の作り
  - 1銘柄の事実は ROW_MAX（600）字以内。数字と出所の日付だけで、説明文は入れない
  - 読むのは main の docs/*.json（Actions の checkout）。外へは取りに行かない

使い方:
  python -X utf8 karte_ai_prep.py top|event [--date YYYY-MM-DD] [--out DIR]
    → DIR/1/PROMPT.txt, DIR/1/facts.json, DIR/2/... と DIR/batches.txt（作ったバッチ番号を1行ずつ）
"""
import argparse
import hashlib
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))
HERE = Path(__file__).parent
DOCS = HERE / "docs"
TOP_N = 150
EVENT_MAX = 50
BATCH = 50
ROW_MAX = 600
RATIO_CAP = 100       # これを超える信用倍率は数字を出さない（売り残がごく少ない・Fable 判断 2026-11-04）
MODEL = "claude-sonnet-5-5"
STOP_JP = {"close_high": "終値ストップ高", "touch_high": "一時ストップ高",
           "close_low": "終値ストップ安", "touch_low": "一時ストップ安"}

# routine が守る書き方。PROMPT.txt の先頭に入る（routine の指示文は「この PROMPT.txt に従う」だけにする）
ROLE = """あなたは株レーダー（kaburadar.jp）の銘柄カルテに載せる「AIの読み上げ」を書く係です。
下の「銘柄ごとの事実」は、カルテに出ている数字と、その出所の日付だけです。
各銘柄について、事実の中で「いつもと違う点」を1〜2文で読み上げてください。

■ 書き方（すべて守る。1つでも破った銘柄は取り込みで捨てられます）
1. 使ってよい数字は、その銘柄の事実の行に書いてある数字だけ。書いてある形のまま使う（桁・小数点も同じ）。
   自分で数えた数・足した数・割った数・丸めた数・日数・回数・年数は書かない（「3回のうち2回」「約2倍」「半年」も不可）。
   日付は事実の行の日付を「11/5」の形にしてよい。
2. 1銘柄 120字以内・1〜2文。です・ます調にしない（「〜で、〜から下がった。」の形）。
3. 将来の値動きを言わない（上がりそう・下がりそう・見込み・期待・可能性・今後・〜だろう・〜はず は書かない）。
4. 判断の言葉を使わない: 良い・悪い・割安・割高・強い・弱い・注目・推奨・おすすめ・予想・有望・危険・警戒・チャンス・シグナル。
   「買い」「売り」は語の一部としても使わない（円買い・戻り売り・売りが先行・買い持ち も不可）。
   使ってよいのは 売買代金・空売り・買い越し・売り越し・買い残・売り残 だけ。上げ下げは「上がった／下がった」「増えた／減った」と数字で言う。
5. 「いつもと違う点」の例: 信用倍率が週次の幅の端にある／出来高が20日平均の何倍か／決算の翌営業日の出来高倍率の過去の並び／
   大口の空売りの新規報告／ストップ高・安の直近の日付／PER が過去の幅の外にある。目立つ点が無ければ、事実の中から2つを短く並べる。
6. 銘柄名と証券コードは本文に書かない（ページの見出しにある）。

■ もう1つ: 各銘柄の「アーサーのひとこと」（o）（2026-11-11〜）
株レーダー専属AIエコノミスト「アーサー・グレイ」として、銘柄ごとに1つ書きます。上の t とは別の文です。
1. 40〜70字・1〜2文。丁寧語（です・ます）。名乗らない（「アーサーです」「私は」を書かない）。
2. 中身は「この銘柄でいま一番目を引く事実（数字を1つ）」と「次に見る数字（日付があれば日付も）」。
   例「決算は増収増益でしたが、空売りの報告が同じ日に増えています。次は11/17の信用残です。」
   次に見る日付は、その銘柄の事実の行の日付か、下の「共通の日付」の日付だけを使う。
3. 上の1・3・4・6を守る（数字は事実の行か共通の日付にそのまま書いてあるものだけ・将来の値動きを言わない・
   判断の言葉と「買い」「売り」を使わない・銘柄名と証券コードを書かない）。
   さらに「予想」「リアルタイム」「必ず」「狙い目」「有望」「チャンス」「推奨」「おすすめ」を使わない。
4. 信用倍率・信用買い残・信用売り残・大口の空売り残高の数値（◯倍・◯%・◯ポイント・◯株）は ひとこと に書かない
   （メールにも載るため）。「空売りの報告が増えています」のように言葉で言うのは可。
5. 書けない銘柄（目立つ事実が無い）は o を "" にする（空のひとことはどこにも出ない）。

■ 出力（これだけを ai_answer.json に書く。前後に説明を付けない）
{"batch": "<この PROMPT の batch の値>", "items": [{"c": "<証券コード>", "t": "<1〜2文>", "o": "<アーサーのひとこと>"}, ...]}
- items は下の銘柄すべてを、下と同じ順で1件ずつ（抜かさない・足さない）
- 書く前に、自分の各文の数字が、その銘柄の行にそのまま書いてあるかを1つずつ確かめる。無い数字はその部分を消して書き直す
- この PROMPT の最後に「■ 今日の発見」の節があるときは、その節の指示どおり "hakken" も同じ JSON に足す
"""

# 「今日の発見」（平日の出来事の回の1バッチ目だけ。選ぶのは機械＝hakken.py、語るのはアーサー）
HAKKEN_ROLE = """
■ 今日の発見（アーサーが気になった3銘柄）
下の銘柄は、その日の出来事の「珍しさ」の点で機械が選びました（点の内訳つき）。選び直さず、下の順のまま書きます。
各銘柄について、見出し h と本文 t を書きます。
- h: 15字以内のひとこと。銘柄名・証券コード・数字は入れない。判断の言葉を使わない（例「決算の翌日に張り付き」）
- t: 3文。丁寧語（です・ます）。名乗らない。
  1文目＝何が起きたか（数字で）。
  2文目＝なぜ目を引くか（ほかの数字とつなぐ。材料に「過去の同じ形」があれば、それを1つだけ使ってよい）。
  3文目＝「次に見るのは〜です。」の形で、次に見る数字と日付（その銘柄の材料の日付か、共通の日付）。
- 1銘柄 200字以内。数字はその銘柄の「材料」か共通の日付にそのまま書いてあるものだけ（桁・小数点も同じ。数え直し・計算をしない）。
- 将来の値動きを言わない。上がった・下がったは数字のまま。上の判断の言葉と「買い」「売り」、
  「予想」「リアルタイム」「必ず」「狙い目」「有望」「チャンス」「推奨」「おすすめ」「面白い」を使わない。
- 出力: 同じ ai_answer.json に "hakken": [{"c": "<証券コード>", "h": "<見出し>", "t": "<3文>"}, ...] を足す（下の銘柄すべて・同じ順）
"""
WATCH_MAX = 50       # 保有ボードの銘柄（朝の1通の登録）とよく見られる銘柄のバッチの上限（平日・event の2バッチ目）
VIEWS_TOP = 20       # views.json の7日の閲覧上位から足す数
NOTIFY_MIN = 3       # 朝の1通の銘柄は、確認済みの登録者がこの人数以上持っている銘柄だけ（統括判断 2026-11-11）


def jload(name):
    f = DOCS / name
    if not f.exists():
        print(f"  {name}: 無い（その項目は出さない）", file=sys.stderr)
        return None
    return json.loads(f.read_text(encoding="utf-8"))


def md(iso):
    return f"{int(iso[5:7])}/{int(iso[8:10])}"


def f2(v, d=2):
    return f"{v:.{d}f}"


class Src:
    """docs/*.json を銘柄コードで引ける形に積み替える（kaburadar の gen_stocks.py と同じ元・同じ計算）"""

    def __init__(self, today):
        self.today = today
        hm = jload("heatmap.json") or {}
        self.hm = {it["t"]: it for it in hm.get("items", []) if it.get("t")}
        pr = jload("prices.json") or {}
        self.price_date = pr.get("trade_date")
        self.price = pr.get("items") or {}
        lc = jload("jpx_listed_codes.json") or {}
        nm = lc.get("names") or {}
        self.names = dict(nm) if isinstance(nm, dict) else dict(zip(lc.get("codes") or [], nm))
        for c, it in self.hm.items():
            self.names.setdefault(c, it.get("n"))
        sd = jload("shinyo_daily.json") or {}
        self.mdaily, self.mdates = sd.get("items") or {}, sd.get("dates") or []
        sw = jload("shinyo_weekly.json") or {}
        self.mweekly, self.mweeks = sw.get("items") or {}, sw.get("weeks") or []
        ka = jload("karauri.json") or {}
        self.ka_date = ka.get("report_date")
        self.karauri = {it["c"]: it for it in ka.get("stocks", [])}
        self.ka_new = {}
        for m in ka.get("moves", []):
            # moves は直近の公表日の前回比。公表日が今日でなければ「今日の新規報告」ではない（休場日・朝の未更新）
            if m.get("kind") == "new" and self.ka_date == today:
                self.ka_new.setdefault(m["c"], []).append(m)
        ke = jload("kessan.json") or {}
        self.kessan = {}
        for day in ke.get("days", []):
            if day.get("d", "") < today:
                continue
            for it in day.get("items", []):
                self.kessan.setdefault(it["c"], {"d": day["d"], "q": it.get("q")})
        fl = jload("kessan_flash.json") or {}
        self.flash = {}
        self.flash_today = set()
        for day in sorted(fl.get("days", []), key=lambda x: x["d"]):
            for it in day.get("items", []):
                self.flash[it["c"]] = {**it, "d": day["d"]}
                if day["d"] == today:
                    self.flash_today.add(it["c"])
        kr = jload("kessan_react_hist/all.json") or {}
        cols = kr.get("cols") or []
        self.krh = {c: [dict(zip(cols, r)) for r in rows] for c, rows in (kr.get("items") or {}).items()}
        st = jload("stophigh.json") or {}
        self.stop = st.get("by_code") or {}
        self.stop_today = {it["c"]: it for it in (st.get("recent") or {}).get(today, [])}
        sp = jload("splits.json") or {}
        self.split_up = {}
        for e in sp.get("upcoming", []):
            if e.get("record") and e.get("status") != "中止":
                self.split_up.setdefault(e["code"], e)
        self.split_today = {e["code"]: e for e in sp.get("recent", [])
                            if e.get("ann") == today and e.get("status") != "中止"}
        ep = jload("eps_hist.json") or {}
        self.eps = ep.get("codes") or {}

    def turnover(self, c):
        """売買代金の大きい順に並べる物差し。ヒートマップの銘柄は v（売買代金）、それ以外は終値×出来高"""
        h = self.hm.get(c)
        if h and h.get("v") is not None:
            return h["v"] * 1e8
        p = self.price.get(c) or {}
        return (p.get("p") or 0) * (p.get("v") or 0) * 1000

    def events(self, c):
        ev = []
        if c in self.flash_today:
            ev.append("決算速報")
        s = self.stop_today.get(c)
        if s:
            ev.append(STOP_JP.get(s.get("k"), "ストップ高・安"))
        if c in self.ka_new:
            ev.append("大口の空売りの新規報告")
        if c in self.split_today:
            ev.append("株式分割の発表")
        return ev


def margin_line(S, c):
    days = S.mdaily.get(c)
    cur = days[-1] if days else None
    if not cur or not cur[0]:
        return None
    s_, sd, b, bd = cur
    ratio = b / s_
    # 100倍を超える倍率は売り残がごく少ないだけで意味のある数字にならない。数字は出さない（Fable 判断 2026-11-04）
    out = (f"信用倍率 {f2(ratio)}倍（{S.mdates[-1]}申込）" if ratio <= RATIO_CAP
           else f"信用倍率 売り残がごく少ないため倍率は大きく出ています（{S.mdates[-1]}申込）")
    if len(days) == 5 and all(days):
        s5 = s_ - sum(d[1] for d in days)
        b5 = b - sum(d[3] for d in days)
        if s5 > 0:
            r5 = b5 / s5
            out += (f"／5営業日前 {f2(r5)}倍" if r5 <= RATIO_CAP
                    else "／5営業日前 売り残がごく少ないため倍率は大きく出ています")
    wk_all = [r[1] / r[0] for r in (S.mweekly.get(c) or []) if r and r[0]]
    wk = [x for x in wk_all if x <= RATIO_CAP]
    if len(wk) >= 4:
        out += (f"／週次{len(wk)}回の幅 {f2(min(wk))}〜{f2(max(wk))}倍"
                + ("（売り残がごく少ない週を除く）" if len(wk) < len(wk_all) else ""))
    return out


def per_line(S, c):
    e = S.eps.get(c) or {}
    fy = e.get("fy") or []
    ok = [x for x in fy if x.get("per") is not None and x["eps"] > 0 and x["per"] <= 100]
    if len(ok) < 3:
        return None
    lo, hi = min(x["per"] for x in ok), max(x["per"] for x in ok)
    p = (S.price.get(c) or {}).get("p")
    last = fy[-1]
    now = ""
    if p and last.get("eps") and last["eps"] > 0 and not e.get("hold"):
        now = f"実績 {p / last['eps']:.1f}倍／"
    return f"PER {now}過去{len(ok)}期の幅 {lo:.1f}〜{hi:.1f}倍"


def facts_row(S, c, ev):
    """1銘柄の事実（数字と出所の日付だけ）。ROW_MAX 字を超えたら後ろの項目から落とす"""
    nm = S.names.get(c) or c
    lines = []
    if ev:
        lines.append("今日の出来事: " + "・".join(ev))
    k = S.kessan.get(c)
    fl = S.flash.get(c)
    if k and not (fl and fl["d"] >= k["d"]):      # 今日すでに短信が出た銘柄は「次の決算」を出さない
        lines.append(f"次の決算発表 {k['d']}（{k.get('q') or ''}）")
    if fl and fl["d"] >= (date.fromisoformat(S.today) - timedelta(days=45)).isoformat() and not fl.get("suspect"):
        part = [f"決算短信 {fl['d']}発表（{fl.get('q') or ''}）"]
        for a, kk in (("売上", "sales_yoy"), ("営業利益", "op_yoy"), ("純利益", "np_yoy")):
            if fl.get(kk) is not None:
                part.append(f"{a} 前年同期比{fl[kk]:+.1f}%")
        if fl.get("fc_rev") in ("上方", "下方"):
            part.append(f"会社の業績見通しを{fl['fc_rev']}修正")   # 「予想」は禁止語なので言い換える（2026-11-11）
        if fl.get("progress_op") is not None and fl["progress_op"] >= 0:
            part.append(f"営業利益の進捗率 {fl['progress_op']:.1f}%")
        lines.append("・".join(part))
    h = [e for e in (S.krh.get(c) or []) if e.get("vr") is not None or e.get("chg") is not None][-6:]
    if h:
        lines.append(f"決算の翌営業日（記録{len(h)}回・古い順）: 騰落 "
                     + ",".join(f"{e['chg']:+.1f}%" if e.get("chg") is not None else "—" for e in h)
                     + "／出来高倍率 " + ",".join(f"{e['vr']:.1f}" if e.get("vr") is not None else "—" for e in h))
    m = margin_line(S, c)
    if m:
        lines.append(m)
    ka = S.karauri.get(c)
    if ka:
        t = f"大口の空売り残高 {ka.get('cnt')}機関・合計{ka['total']:.2f}%（{S.ka_date}公表）"
        if ka.get("d1"):
            t += f"／前回比 {ka['d1']:+.2f}ポイント"
        nw = S.ka_new.get(c)
        if nw:
            t += f"／新規報告 {len(nw)}件（" + "・".join(f"{x['r']:.2f}%" for x in nw[:3]) + "）"
        lines.append(t)
    p = S.price.get(c) or {}
    if p.get("p") is not None:
        t = f"値動き（{S.price_date}）: 終値 {p['p']:,.1f}円"
        for a, kk in (("前日比", "c"), ("5日", "c5"), ("20日", "c20"), ("52週高値から", "hi52")):
            if p.get(kk) is not None:
                t += f"・{a} {p[kk]:+.1f}%"
        if p.get("vr") is not None:
            t += f"・出来高 20日平均の{p['vr']:.1f}倍"
        lines.append(t)
    st = S.stop.get(c)
    if st and st.get("last"):
        n = st.get("n") or {}
        hi = (n.get("close_high") or 0) + (n.get("touch_high") or 0)
        lo = (n.get("close_low") or 0) + (n.get("touch_low") or 0)
        d0, k0 = st["last"][0]
        lines.append(f"直近1年 ストップ高{hi}回・ストップ安{lo}回（最近 {d0} {STOP_JP.get(k0, '')}）")
    sp = S.split_up.get(c)
    if sp:
        r = sp.get("ratio")
        lines.append(f"株式分割の予定 {('1:' + format(r, 'g')) if r else ''}（基準日 {sp['record']}・発表 {sp.get('ann') or ''}）")
    pl = per_line(S, c)
    if pl:
        lines.append(pl)
    head = f"### {c} {nm}"
    # 長すぎたら後ろの項目から落とす（今日の出来事・決算・信用・空売り・値動きは前にある）
    while lines and len(head) + sum(len(x) + 3 for x in lines) > ROW_MAX:
        lines.pop()
    return head + "\n" + "\n".join("- " + x for x in lines)


def pick(S, mode):
    if mode == "top":
        codes = sorted((c for c in S.hm if c in S.price),
                       key=lambda c: -(S.hm[c].get("v") or 0))[:TOP_N]
        return [(c, S.events(c)) for c in codes]
    cand = set(S.flash_today) | set(S.stop_today) | set(S.ka_new) | set(S.split_today)
    cand = [c for c in cand if c in S.price]
    cand.sort(key=lambda c: -S.turnover(c))
    print(f"  出来事のあった銘柄: {len(cand)}（決算速報 {len(S.flash_today)}・ストップ高/安 {len(S.stop_today)}・"
          f"空売り新規 {len(S.ka_new)}・分割発表 {len(S.split_today)}）→ 上位 {min(len(cand), EVENT_MAX)}",
          file=sys.stderr)
    return [(c, S.events(c)) for c in cand[:EVENT_MAX]]


def notify_codes():
    """朝の1通（kr_notify・確認済み）に登録された銘柄コード（コードだけ。メールは読まない）。
    Secrets が無い・表が無い・失敗したときは空（今までどおり動く）"""
    if not (os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SERVICE_ROLE_KEY")):
        return []
    try:
        import notify_morning as NM
        rows = NM.sb("kr_notify?select=codes&confirmed=eq.true&limit=1000") or []
    except (SystemExit, Exception) as e:      # 表が無い（SQL の前）・通信の失敗。理由だけ出す（中身は出さない）
        print(f"  朝の1通の銘柄: 読めない（{type(e).__name__}）→ 足さない", file=sys.stderr)
        return []
    # 統括判断（2026-11-11）: 公開リポジトリのブランチに載るので、確認済みの登録者が NOTIFY_MIN 人以上持っている銘柄だけ。
    # それ未満の銘柄は入れない（件数もログに出さない）。誰の登録かは分からない形で、コードだけを集計する
    cnt = {}
    for r in rows:
        for c in {str(x).strip().upper() for x in (r.get("codes") or []) if str(x).strip()}:
            cnt[c] = cnt.get(c, 0) + 1
    return sorted(c for c, n in cnt.items() if n >= NOTIFY_MIN)


def views_codes():
    """よく見られる銘柄（views.json の7日の閲覧上位・カルテのページ）"""
    v = jload("views.json") or {}
    return [x["code"] for x in (v.get("top7") or [])[:VIEWS_TOP] if x.get("code")]


def pick_watch(S, taken):
    """平日の2バッチ目: 保有ボードの銘柄（朝の1通の登録）→ よく見られる銘柄。1バッチ目に入った銘柄は除く"""
    nt, vw = notify_codes(), views_codes()
    seen, out = set(taken), []
    for c in sorted((c for c in nt if c in S.price), key=lambda c: -S.turnover(c)) + [c for c in vw if c in S.price]:
        if c not in seen:
            seen.add(c)
            out.append(c)
    # 公開リポジトリのログなので、朝の1通の銘柄の件数は出さない（合計だけ）
    print(f"  2バッチ目（保有ボードとよく見られる銘柄）: {min(len(out), WATCH_MAX)}銘柄", file=sys.stderr)
    return [(c, S.events(c)) for c in out[:WATCH_MAX]]


def hakken_section(S, today):
    """今日の発見の3銘柄（機械が選ぶ）と、アーサーに渡す材料。選べなければ (None, None)"""
    try:
        import hakken as HK
        r = HK.pick(DOCS, date.fromisoformat(today))
    except Exception as e:                    # 発見の失敗で読み上げ（本体）を止めない
        print(f"::warning::今日の発見の選定に失敗: {e}", file=sys.stderr)
        return None, None
    picks = r["picks"]
    print(f"  今日の発見: 候補 {r['n_cand']}・直近5営業日に選んだので外した {len(r['skipped_recent'])} → {len(picks)}銘柄",
          file=sys.stderr)
    if not picks:
        return None, None
    D = r["D"]
    mat = {x["c"]: HK.material(D, x, facts_row(S, x["c"], S.events(x["c"]))) for x in picks}
    txt = HAKKEN_ROLE + "\n" + "\n\n".join(f"### {x['c']} {x['n']}\n{mat[x['c']]}" for x in picks) + "\n"
    H = {"date": today, "v": HK.FORMULA_V, "price_date": D.price_date, "n_cand": r["n_cand"],
         "skipped_recent": r["skipped_recent"], "picks": picks, "material": mat}
    return txt, H


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["top", "event"])
    ap.add_argument("--date", default=datetime.now(JST).date().isoformat())
    ap.add_argument("--out", default="karte_prompt")
    a = ap.parse_args()
    S = Src(a.date)
    sel = pick(S, a.mode)
    groups = [(sel[i:i + BATCH], "週1回・売買代金の上位" if a.mode == "top" else "その日に出来事があった銘柄")
              for i in range(0, len(sel), BATCH)]
    if a.mode == "event" and S.price_date == a.date:     # 休場日（株価がその日のものでない）は足さない
        w = pick_watch(S, [c for c, _ in sel])
        if w:
            groups.append((w, "保有ボードに入っている銘柄と、よく見られる銘柄"))
    if not groups:
        print("対象の銘柄が0件 → ブランチを作らない", file=sys.stderr)
        sys.exit(3)
    try:
        import hakken as HK
        common = HK.next_dates(date.fromisoformat(a.date))
    except Exception:
        common = ""
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    made = []
    for i, (part, label) in enumerate(groups):
        n = i + 1
        rows = {c: facts_row(S, c, ev) for c, ev in part}
        body = "\n\n".join(rows[c] for c, _ in part)
        hk_txt, H = (hakken_section(S, a.date) if (a.mode == "event" and n == 1 and sel) else (None, None))
        bid = f"karte-{a.date}-{n}"
        sig = hashlib.sha1((body + (hk_txt or "")).encode("utf-8")).hexdigest()[:10]
        batch = f"{bid}:{sig}"
        prompt = (ROLE + f"\nbatch: {batch}\n対象: {len(part)}銘柄（{label}）\n"
                  + (f"\n■ 共通の日付（ひとこと・今日の発見の「次に見る数字」に使ってよい）: {common}\n" if common else "")
                  + f"\n■ 銘柄ごとの事実（{a.date} 時点・出所は JPX・TDnet・EDINET・Yahoo!ファイナンスの配信値）\n\n"
                  + body + "\n" + (hk_txt or ""))
        d = out / str(n)
        d.mkdir(exist_ok=True)
        (d / "PROMPT.txt").write_text(prompt, encoding="utf-8")
        F = {"batch": batch, "date": a.date, "mode": a.mode, "model": MODEL,
             "codes": [c for c, _ in part], "events": {c: ev for c, ev in part if ev},
             "facts": rows, "common": common}
        if H:
            F["hakken"] = H
        (d / "facts.json").write_text(json.dumps(F, ensure_ascii=False, indent=1), encoding="utf-8")
        per = [len(rows[c]) for c, _ in part]
        print(f"  {bid}: {len(part)}銘柄（{label}）・PROMPT {len(prompt):,}字（事実 1銘柄 平均{sum(per) / len(per):.0f}字・最大{max(per)}字）"
              + (f"・今日の発見 {len(H['picks'])}銘柄（{len(hk_txt):,}字）" if H else ""), file=sys.stderr)
        made.append(str(n))
    (out / "batches.txt").write_text("\n".join(made) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
