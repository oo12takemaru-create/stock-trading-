# -*- coding: utf-8 -*-
"""株レーダー 保有銘柄の「朝の1通」（希望者だけ・平日の朝 7:30 ごろ）

起動文: kaburadar\\起動文_実務_保有銘柄の朝の1通_2026-11-10.md

■ 流れ
  1. Supabase の kr_notify（確認済み・今日まだ送っていない人）を読む。申込み・確認・停止は kaburadar の api/board.js
  2. このリポジトリの docs/ の公開 JSON（前の晩までに出来ている分）から、銘柄ごとの「前営業日の出来事」を作る
  3. 出来事が1つでもある人にだけ Resend で1通送り、last_sent を書く。何も無い人には送らない
     銘柄を登録していない人（codes が空＝「地合いと AI朝刊の結論だけ」で申し込んだ人・2026-11-13）には、
     温度計の判定と今朝の AI朝刊の結論（見立てと1行）を毎営業日1通（compose_market）。どちらも無い日は送らない
  4. 確認されないまま7日たった申込みを消す

■ 出来事（1銘柄1〜2行。3つ目からは「ほか n 件」）
  P1 = 前営業日、P2 = その前の営業日、P3 = さらに前の営業日（東証の営業日・jp_bizday.py）
  - ストップ高/安（stophigh.json の recent[P1]）
  - 決算の発表（kessan_flash.json の (P2, P1] の日。疑わしい行＝suspect は出さない）。数字1行
  - ±5%以上の値動き（prices.json・trade_date が P1 のときだけ）。ストップ高/安の日は重ねない
  - 株式分割の発表（splits.json の recent・発表日が (P2, P1]・中止は除く）
  - 配当予想の修正（dividend_hist の決算短信で「修正の有無：有」・開示日が (P2, P1]）
  - 大量保有の報告（edinet_latest.json・提出日が (P3, P2]）
      EDINET の取り込み（edinet-daily）は朝 7:30 の後に前日ぶんを入れるので、7:30 の時点で揃っているのは
      P2 まで。1日遅れになるが、毎日同じ幅で数えるので取りこぼしも二重もない（提出日を書く）
  - 空売り残高の報告の新規・増加（karauri.json・report_date が P1 のとき）。JPX 由来の数値は出さず「報告がありました」だけ
  - 決算の予定（kessan.json）: 今日・明日（＝次の営業日）は毎日、今週のほかの日は週の最初の営業日だけ
  銘柄は出来事の重い順（決算の発表 → ストップ高/安・±5% → 空売り/大量保有/分割/配当 → 決算の予定）
  増収増益などの札は売上と営業利益の前年同期比（営業利益が無い会社は経常利益→純利益）。判定できなければ付けない
  冒頭に保有ボードへのボタン。末尾に温度計の判定1行（radar.json）・AI朝刊・保有ボード（AIに聞く）のリンク、停止のリンク

■ 守り
  - prices.json の trade_date が前営業日でなければ**誰にも送らない**（古いデータで届けない・exit 1 で気づく）
  - 同じ日に二重に送らない（last_sent が今日の人は飛ばす）
  - 1日の上限 DAILY_CAP=60 通（Resend の無料枠 100通/日はルールトレードの通知・ログインメールと共用）。50通を超えた日は警告、超える分は送らない
  - 表（kr_notify）が無い・RESEND_API_KEY が無いときは、何もせずに理由を出して終わる（exit 0）
  - 文面は事実だけ（推奨の言葉なし・「リアルタイム」なし）。BANNED に当たる行は落とす

■ 使い方
  python notify_morning.py                     # 送る（平日の朝・cron-worker から notify-morning.yml）
  python notify_morning.py --dry-run           # 誰に何を送るかを表示するだけ（送らない・書かない）
  python notify_morning.py --only me           # 本人（Secrets の NOTIFY_TO）の1通だけ送る（試し。確認済みの登録が要る）
  python notify_morning.py --preview 7203,6315 # Supabase を使わずに、その銘柄の文面を表示するだけ
  --today YYYY-MM-DD で「今日」を変えられる（試し用）。--preview に --html out.html を足すと HTML も書く
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from html import escape
from pathlib import Path

from jp_bizday import is_bizday, next_bizday

HERE = Path(__file__).resolve().parent
DOCS = HERE / "docs"
SITE = "https://kaburadar.jp"
JST = timezone(timedelta(hours=9))
FROM = "株レーダー <noreply@ruletrade.jp>"
DAILY_CAP = 60           # 1日に送る上限（Resend 無料枠 100通/日はルールトレードの通知・掲示板のログインメールと共用。統括 11/10）
WARN_AT = 50             # これを超えた日は Actions に警告（メール・銘柄は出さない）
MOVE_PCT = 5.0           # 値動きの閾値（前日比の絶対値）
MAX_LINES = 2            # 1銘柄の行数
UNCONFIRMED_DAYS = 7
ONE_DAYS = 7             # アーサーのひとことを出す日数（これより古い文は出さない）
UTM = "utm_source=notify&utm_medium=email"
HOLD_URL = f"{SITE}/hold.html?{UTM}"   # 冒頭のボタン（毎日サイトを開く入口）
WD = "月火水木金土日"
Q_JP = {"1Q": "第1四半期", "2Q": "第2四半期", "3Q": "第3四半期", "本決算": "本決算"}
STOP_JP = {"close_high": "終値ストップ高", "touch_high": "一時ストップ高",
           "close_low": "終値ストップ安", "touch_low": "一時ストップ安"}
REGIME_JP = {"BULLISH": "強気", "NEUTRAL": "中立", "BEARISH": "弱気", "PANIC": "パニック"}
AI_JP = {"attack": "攻め", "lean_attack": "やや攻め", "neutral": "中立", "lean_defense": "やや守り", "defense": "守り"}
# 文面に入れない言葉（推奨・断定・リアルタイム）。当たった行は落とす
BANNED = re.compile(r"買い時|売り時|買うべき|売るべき|おすすめ|オススメ|推奨|狙い目|チャンス|必ず|確実|絶対|急騰確定|リアルタイム")


# ─────────────────────────────── 日付
def prev_biz(d):
    for _ in range(30):
        d -= timedelta(days=1)
        if is_bizday(d):
            return d
    raise ValueError(f"{d} の前の営業日が見つからない")


def md(iso):
    d = date.fromisoformat(iso[:10])
    return f"{d.month}/{d.day}"


def mdw(iso):
    d = date.fromisoformat(iso[:10])
    return f"{d.month}/{d.day}（{WD[d.weekday()]}）"


# ─────────────────────────────── 公開 JSON（docs/）
def load(name):
    p = DOCS / name
    if not p.exists():
        print(f"  {name}: 無い（この出来事は出さない）", file=sys.stderr)
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # 壊れた1本で全員の配信を止めない
        print(f"  {name}: 読めない {e}（この出来事は出さない）", file=sys.stderr)
        return None


def load_jsonl(name):
    p = DOCS / name
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


def oku(v):
    """決算短信の金額（百万円）を読める単位に（gen_stocks.py の _oku と同じ）"""
    if v is None:
        return "—"
    if abs(v) >= 1000000:
        return f"{v / 1000000:,.2f}兆円"
    if abs(v) >= 100:
        return f"{v / 100:,.1f}億円"
    return f"{v:,.0f}百万円"


def flash_verdict(it):
    """増収増益などの札。kessan_flash.py の verdict（売上と営業利益の前年同期比の符号）を使い、
    営業利益が無い会社（IFRS 等）は記録にある利益（経常利益→純利益）の前年同期比で判定する。判定できなければ None（付けない）"""
    if it.get("verdict"):
        return it["verdict"]
    s = it.get("sales_yoy")
    pr = next((it[k] for k in ("op_yoy", "ord_yoy", "np_yoy") if it.get(k) is not None), None)
    if s is None or pr is None:
        return None
    return ("増収" if s >= 0 else "減収") + ("増益" if pr >= 0 else "減益")


# 出来事の順位（小さいほど重い。1銘柄の中の行の順）
#   0 決算の発表 / 1 ストップ高・安 / 2 ±5% / 3 空売り / 4 大量保有 / 5 分割 / 6 配当 / 7 決算の予定
# 銘柄の並びの段: 決算の発表 → ストップ高/安・±5% → 空売り/大量保有/分割/配当 → 決算の予定
TIER = {0: 0, 1: 1, 2: 1, 3: 2, 4: 2, 5: 2, 6: 2, 7: 3}


class Facts:
    """銘柄ごとの出来事。ev[code] = [(順位, 文), ...]（順位の小さいものから出す）"""

    def __init__(self, today):
        self.today = today
        self.p1 = prev_biz(today)
        self.p2 = prev_biz(self.p1)
        self.p3 = prev_biz(self.p2)
        self.ev = {}
        self.names = {}
        self.price_date = None
        self.regime = None
        self.one = {}          # code → (アーサーのひとこと, 日付)
        self.hakken = None     # 今日の発見の日付（前営業日のものがあるときだけ）
        self.ai = None         # 今朝の AI朝刊の結論 (見立て, 1行, 生成時刻)。今日の号があるときだけ（2026-11-13）

    def add(self, code, rank, text):
        if BANNED.search(text):
            print(f"  {code}: 使わない言葉が入るので落とした: {text}", file=sys.stderr)
            return
        self.ev.setdefault(code, []).append((rank, text))

    def build(self):
        p1, p2, p3 = self.p1.isoformat(), self.p2.isoformat(), self.p3.isoformat()
        in12 = lambda d: p2 < (d or "")[:10] <= p1        # (P2, P1]
        in23 = lambda d: p3 < (d or "")[:10] <= p2        # (P3, P2]

        d = load("jpx_listed_codes.json") or {}
        nm = d.get("names")
        if isinstance(nm, dict):
            self.names.update(nm)
        elif isinstance(nm, list) and isinstance(d.get("codes"), list):
            self.names.update(dict(zip(d["codes"], nm)))

        # 株価（鮮度の関門。呼び出し側が price_date を見る）
        pr = load("prices.json") or {}
        self.price_date = pr.get("trade_date")
        stop_codes = set()
        sh = load("stophigh.json") or {}
        for it in (sh.get("recent") or {}).get(p1, []) if isinstance(sh.get("recent"), dict) else []:
            c, k = it.get("c"), it.get("k")
            if c and k in STOP_JP:
                stop_codes.add(c)
                chg = it.get("chg")
                self.add(c, 1, f"{STOP_JP[k]}（{md(p1)}" + (f"・前日比 {chg:+.1f}%" if chg is not None else "") + "）")
        if self.price_date == p1:
            for c, v in (pr.get("items") or {}).items():
                ch = v.get("c")
                if ch is None or v.get("stale") or c in stop_codes or abs(ch) < MOVE_PCT:
                    continue
                p = v.get("p")
                self.add(c, 2, f"{md(p1)}の値動き 前日比 {ch:+.1f}%" + (f"（終値 {p:,.0f}円）" if p else ""))

        # 決算の発表（数字1行）
        fl = load("kessan_flash.json") or {}
        for day in fl.get("days") or []:
            if not in12(day.get("d")):
                continue
            for it in day.get("items") or []:
                c = it.get("c")
                if not c or it.get("suspect"):
                    continue
                parts = [f"{a} {oku(it.get(k))}" + (f"（前年同期比 {it[k + '_yoy']:+.1f}%）" if it.get(k + "_yoy") is not None else "")
                         for a, k in (("売上高", "sales"), ("営業利益", "op"), ("純利益", "np")) if it.get(k) is not None]
                q = Q_JP.get(it.get("q"), it.get("q") or "")
                t = f"決算の発表（{q + '・' if q else ''}{md(day['d'])}）"
                if parts:
                    t += "：" + "・".join(parts)
                v = flash_verdict(it)
                if v:
                    t += f"（{v}）"
                self.add(c, 0, t)

        # 株式分割の発表
        sp = load("splits.json") or {}
        for e in sp.get("recent") or []:
            if e.get("code") and e.get("status") != "中止" and in12(e.get("ann")):
                r = e.get("ratio")
                self.add(e["code"], 5, f"株式分割の発表（{md(e['ann'])}" + (f"・1株→{format(r, 'g')}株" if r else "") + "）")

        # 配当予想の修正（決算短信の「直近に公表されている配当予想からの修正の有無：有」）
        idx = load("dividend_hist/index.json") or {}
        months = sorted({p1[:7], p2[:7]} & set(idx.get("months") or [p1[:7], p2[:7]]))
        seen = set()
        for m in months:
            for row in load_jsonl(f"dividend_hist/{m}.jsonl"):
                c = row.get("c")
                if not c or row.get("rev") != "有" or not in12(row.get("d")) or c in seen:
                    continue
                seen.add(c)
                fc = row.get("dps_fc")
                self.add(c, 6, f"配当予想の修正（{md(row['d'])}の決算短信" + (f"・年間 {fc:g}円の予想" if fc is not None else "") + "）")

        # 大量保有の報告（提出日が (P3, P2]・1日遅れ）
        ed = load("edinet_latest.json") or {}
        for c, docs in (ed.get("by_issuer") or {}).items():
            for e in docs or []:
                if not in23(e.get("d")):
                    continue
                who = (e.get("filer") or "").replace("株式会社", "").strip()
                r = e.get("ratio")
                typ = {"大量保有": "新規"}.get(e.get("type"), e.get("type") or "報告")
                self.add(c, 4, f"大量保有の報告（{typ}・{md(e['d'])}提出）"
                         + (f"：{who}" if who else "") + (f" {r:.2f}%" if r is not None else ""))

        # 空売り残高の報告（新規・増加）。JPX 由来の数値・機関名は出さない
        ka = load("karauri.json") or {}
        if ka.get("report_date") == p1:
            # moves は動きの大きい100件だけなので、増加は銘柄ごとの合計の動き（stocks の d1＝その日の報告の差の合計）で見る
            kinds = {}
            for m in ka.get("moves") or []:
                if m.get("kind") == "new" and m.get("c"):
                    kinds.setdefault(m["c"], set()).add("new")
            for st in ka.get("stocks") or []:
                if st.get("c") and (st.get("d1") or 0) > 0:
                    kinds.setdefault(st["c"], set()).add("up")
            for c, ks in kinds.items():
                w = "・".join(x for x, k in (("新規の報告あり", "new"), ("合計が増加", "up")) if k in ks)
                self.add(c, 3, f"大口の空売り残高の新しい報告がありました（{w}・{md(p1)}公表）")

        # 決算の予定（今日・明日は毎日。今週のほかの日は週の最初の営業日だけ）
        ks = load("kessan.json") or {}
        t0 = self.today.isoformat()
        t1 = next_bizday(self.today).isoformat()
        first_of_week = self.p1.isocalendar()[:2] != self.today.isocalendar()[:2]
        fri = (self.today + timedelta(days=4 - self.today.weekday())).isoformat()
        for day in ks.get("days") or []:
            dd = day.get("d") or ""
            if dd == t0:
                w = "今日"
            elif dd == t1:
                w = "明日" if (date.fromisoformat(t1) - self.today).days == 1 else f"次の営業日 {mdw(t1)}"
            elif first_of_week and t1 < dd <= fri:
                w = f"今週 {mdw(dd)}"
            else:
                continue
            for it in day.get("items") or []:
                if it.get("c"):
                    if it.get("n"):
                        self.names[it["c"]] = it["n"]
                    q = it.get("q") or ""
                    self.add(it["c"], 7, f"{w} 決算発表の予定" + (f"（{q}）" if q else ""))

        rd = load("radar.json") or {}
        if rd.get("regime"):
            self.regime = (rd["regime"], (rd.get("scanner_timestamp") or rd.get("updated") or "")[:16])

        # アーサーのひとこと（2026-11-11）。収集側の karte_ai_ingest.py が検査した文（JPX の数値は入っていない）。
        # 7日より古い文は出さない。各銘柄の最後の行に出す（出来事のある銘柄だけ＝メールに載る銘柄だけ）
        one = load("karte_ai/one.json") or {}
        cut = (self.today - timedelta(days=ONE_DAYS)).isoformat()
        for c, v in (one.get("items") or {}).items():
            o = (v.get("o") or "").strip()
            if o and (v.get("d") or "") >= cut and not BANNED.search(o):
                self.one[c] = (o, v["d"])
        # 今日の発見（前営業日の夜に選んだ3銘柄）。末尾に1行のリンク
        hk = load("hakken/latest.json") or {}
        if hk.get("date") == p1 and hk.get("picks"):
            self.hakken = hk["date"]
        # 今朝の AI朝刊（6:15 ごろの定期実行が書く ai_analysis.json）。今日の号だけ。1行（headline）は使わない言葉に当たれば出さない
        L = (load("ai_analysis.json") or {}).get("latest") or {}
        if L.get("stance") in AI_JP and (L.get("date") or (L.get("updated") or "")[:10]) == self.today.isoformat():
            hd = (L.get("headline") or "").strip()
            self.ai = (L["stance"], "" if (not hd or BANNED.search(hd) or len(hd) > 80) else hd, (L.get("updated") or "")[:16])

    def weight(self, code):
        """銘柄の並び順に使う段（いちばん重い出来事の段）"""
        return min((TIER.get(r, 9) for r, _ in self.ev.get(code) or []), default=9)

    def lines(self, code):
        ev = sorted(self.ev.get(code) or [], key=lambda x: x[0])
        out = [t for _, t in ev[:MAX_LINES]]
        if len(ev) > MAX_LINES:
            out[-1] += f"（ほか{len(ev) - MAX_LINES}件はカルテで）"
        return out


# ─────────────────────────────── 文面
def karte(code):
    return f"{SITE}/stocks/{code}.html?{UTM}"


def compose_market(F, token):
    """銘柄を登録していない人の1通（2026-11-13）: 温度計の判定と今朝の AI朝刊の結論だけ。どちらも無い日は None"""
    if not F.regime and not F.ai:
        return None
    stop = f"{SITE}/api/board?a=nstop&t={token}"
    radar, ai = f"{SITE}/radar.html?{UTM}", f"{SITE}/ai.html?{UTM}"
    head = f"{mdw(F.today.isoformat())} の朝の、今日の地合いと AI朝刊の結論です。"
    rows = []
    if F.regime:
        reg, upd = F.regime
        rows.append(("今日の地合い（相場の温度計）", f"{REGIME_JP.get(reg, reg)}（{reg}）" + (f"・{md(upd)} {upd[11:16]}の判定" if len(upd) >= 16 else ""), radar,
                     "日経平均・S&P500・VIX から機械が判定。上げ下げの見立てではなく、戦略を選ぶための判定です"))
    if F.ai:
        st, hd, upd = F.ai
        rows.append((f"AI朝刊（{md(upd) + ' ' + upd[11:16] if len(upd) >= 16 else '今朝'}）", f"見立て「{AI_JP[st]}」" + (f"：{hd}" if hd else ""), ai,
                     "AI による一般的な市況の要約です"))
    tail = []
    if F.hakken:
        tail.append(("今日の発見", f"アーサーが気になった3銘柄（{md(F.hakken)}）", f"{SITE}/hakken/{F.hakken}.html?{UTM}"))
    tail.append(("保有ボード", "銘柄を入れて「通知の銘柄を更新」を押すと、その銘柄の出来事もこの1通に入ります", HOLD_URL))
    note = "事実と AI による一般的な市況の要約で、売買の推奨ではありません。"
    text = [head, ""]
    for a, b, u, n in rows:
        text += [f"■ {a}", f"・{b}", f"  {n}", f"  {u}", ""]
    text.append("―――")
    text += [f"{a}：{b}  {u}" for a, b, u in tail]
    text += ["", note, "このメールは株レーダーの「朝の1通」（地合いと AI朝刊の結論だけ）を申し込んだ方に送っています。",
             f"配信を止める（押すとすぐ止まり、メールアドレスを消します）：{stop}"]
    A = 'style="color:#0b6b3a"'
    h = ['<div style="max-width:600px;margin:0 auto;padding:0 16px;font-size:15px;line-height:1.7;color:#1f2933">',
         f'<p style="margin:16px 0 10px">{escape(head)}</p>']
    for a, b, u, n in rows:
        h.append(f'<div style="margin:14px 0;padding:10px 12px;border:1px solid #dde3ea;border-radius:8px">'
                 f'<div style="font-size:16px;font-weight:bold">{escape(a)}</div>'
                 f'<div style="margin-top:4px">{escape(b)}</div>'
                 f'<div style="margin-top:4px;color:#4a5560;font-size:14px">{escape(n)}</div>'
                 f'<div style="margin-top:6px"><a href="{u}" {A}>開く →</a></div></div>')
    h.append('<hr style="border:none;border-top:1px solid #dde3ea;margin:16px 0">')
    h += [f'<div style="margin:4px 0">{escape(a)}：{escape(b)} <a href="{u}" {A}>開く →</a></div>' for a, b, u in tail]
    h.append(f'<p style="color:#666;font-size:13px;margin-top:16px">{escape(note)}<br>このメールは株レーダーの「朝の1通」（地合いと AI朝刊の結論だけ）を申し込んだ方に送っています。<br>'
             f'<a href="{stop}" style="color:#666">配信を止める</a>（押すとすぐ止まり、メールアドレスを消します）</p>'
             '<p style="color:#666;font-size:13px">株レーダー https://kaburadar.jp</p></div>')
    parts = []
    if F.regime:
        parts.append(f"地合い {REGIME_JP.get(F.regime[0], F.regime[0])}")
    if F.ai:
        parts.append(f"AI朝刊 {AI_JP[F.ai[0]]}")
    subject = f"株レーダー 朝の1通（{md(F.today.isoformat())}）" + "・".join(parts)
    return {"subject": subject, "text": "\n".join(text), "html": "\n".join(h), "n": 0, "stop": stop}


def compose(F, codes, token):
    """codes のうち出来事のある銘柄だけで文面を作る。無ければ None。codes が空（地合いだけの申込み）は compose_market"""
    if not codes:
        return compose_market(F, token)
    blocks = [(c, F.names.get(c) or c, F.lines(c)) for c in codes]
    blocks = [b for b in blocks if b[2]]
    blocks.sort(key=lambda b: F.weight(b[0]))   # 重い出来事の銘柄から（同じ段はボードの順）
    if not blocks:
        return None
    stop = f"{SITE}/api/board?a=nstop&t={token}"
    head = f"前営業日 {mdw(F.p1.isoformat())} までの、保有ボードの銘柄の出来事です（{len(blocks)}銘柄）。"
    tail = []
    if F.regime:
        reg, upd = F.regime
        tail.append(("相場の温度計", f"{REGIME_JP.get(reg, reg)}（{reg}）" + (f"・{md(upd)} {upd[11:16]}の判定" if len(upd) >= 16 else ""), f"{SITE}/radar.html?{UTM}"))
    tail.append(("AI朝刊", "今朝の相場の読み", f"{SITE}/ai.html?{UTM}"))
    tail.append(("AIに聞く", "保有ボードから、数字つきで自分の AI に", HOLD_URL))
    if F.hakken:
        tail.append(("今日の発見", f"アーサーが気になった3銘柄（{md(F.hakken)}）", f"{SITE}/hakken/{F.hakken}.html?{UTM}"))
    note = "事実の一覧で、売買の推奨ではありません。株価は前営業日の終値です。大量保有の報告は1営業日遅れで載ります。"
    one = {c: F.one[c] for c, _, _ in blocks if c in F.one}
    if one:
        note += "「アーサー」の行は株レーダー専属AIエコノミスト（AI）の文です。"

    text = [head, f"保有ボードで損益を見る → {HOLD_URL}", ""]
    for c, n, ls in blocks:
        text.append(f"■ {n}（{c}）")
        text += [f"・{x}" for x in ls]
        if c in one:
            text.append(f"・アーサー（{md(one[c][1])}）：{one[c][0]}")
        text.append(f"  {karte(c)}")
        text.append("")
    text.append("―――")
    text += [f"{a}：{b}  {u}" for a, b, u in tail]
    text += ["", note, "このメールは株レーダーの保有ボードで「朝の1通」を申し込んだ方に送っています。",
             f"配信を止める（押すとすぐ止まり、メールアドレスと銘柄コードを消します）：{stop}"]

    A = 'style="color:#0b6b3a"'
    # スマホの Gmail で読みやすく: 本文 15px・行間 1.7・最大 600px・左右の余白 16px・銘柄名 16px 太字
    h = ['<div style="max-width:600px;margin:0 auto;padding:0 16px;font-size:15px;line-height:1.7;color:#1f2933">',
         f'<p style="margin:16px 0 10px">{escape(head)}</p>',
         f'<p style="margin:0 0 18px"><a href="{HOLD_URL}" style="display:inline-block;padding:10px 18px;background:#0b6b3a;'
         'color:#ffffff;text-decoration:none;border-radius:8px;font-weight:bold">保有ボードで損益を見る →</a></p>']
    for c, n, ls in blocks:
        h.append(f'<div style="margin:14px 0;padding:10px 12px;border:1px solid #dde3ea;border-radius:8px">'
                 f'<div style="font-size:16px;font-weight:bold"><a href="{karte(c)}" style="color:#0b6b3a;text-decoration:none">{escape(n)}（{c}）</a></div>'
                 + "".join(f'<div style="margin-top:4px">・{escape(x)}</div>' for x in ls)
                 + (f'<div style="margin-top:6px;color:#4a5560;font-size:14px">アーサー（{md(one[c][1])}）：{escape(one[c][0])}</div>'
                    if c in one else "")
                 + f'<div style="margin-top:6px"><a href="{karte(c)}" {A}>カルテを見る →</a></div></div>')
    h.append('<hr style="border:none;border-top:1px solid #dde3ea;margin:16px 0">')
    h += [f'<div style="margin:4px 0">{escape(a)}：{escape(b)} <a href="{u}" {A}>開く →</a></div>' for a, b, u in tail]
    h.append(f'<p style="color:#666;font-size:13px;margin-top:16px">{escape(note)}<br>このメールは株レーダーの保有ボードで「朝の1通」を申し込んだ方に送っています。<br>'
             f'<a href="{stop}" style="color:#666">配信を止める</a>（押すとすぐ止まり、メールアドレスと銘柄コードを消します）</p>'
             '<p style="color:#666;font-size:13px">株レーダー https://kaburadar.jp</p></div>')
    names = "・".join(n for _, n, _ in blocks[:2]) + (f" ほか{len(blocks) - 2}銘柄" if len(blocks) > 2 else "")
    subject = f"株レーダー 朝の1通（{md(F.today.isoformat())}）{names}"
    return {"subject": subject, "text": "\n".join(text), "html": "\n".join(h), "n": len(blocks), "stop": stop}


# ─────────────────────────────── Supabase・Resend
def sb_env():
    base = (os.environ.get("SUPABASE_URL") or "").strip()
    m = re.search(r"https://[a-z0-9-]+\.supabase\.co", base)
    base = m.group(0) if m else base.rstrip("/")
    raw = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    m = re.search(r"sb_secret_[A-Za-z0-9_-]+|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", raw)
    key = m.group(0) if m else raw
    if not base or not key:
        raise SystemExit("::error::SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY が無い")
    return base, key


class NoTable(Exception):
    pass


def sb(path, method="GET", body=None, prefer=None):
    base, key = sb_env()
    h = {"apikey": key, "Authorization": "Bearer " + key, "Content-Type": "application/json"}
    if prefer:
        h["Prefer"] = prefer
    req = urllib.request.Request(f"{base}/rest/v1/{path}", method=method, headers=h,
                                 data=json.dumps(body).encode("utf-8") if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            t = r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        msg = e.read().decode("utf-8")[:300]
        if e.code == 404 or "PGRST205" in msg or "42P01" in msg:
            raise NoTable(msg)
        raise SystemExit(f"::error::supabase {method} {path.split('?')[0]} {e.code}: {msg}")
    return json.loads(t) if t.strip() else None


def send_mail(api_key, to, m):
    body = {"from": FROM, "to": [to], "subject": m["subject"], "text": m["text"], "html": m["html"],
            "headers": {"List-Unsubscribe": f"<{m['stop']}>", "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}}
    req = urllib.request.Request("https://api.resend.com/emails", method="POST",
                                 headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json",
                                          "User-Agent": "kaburadar-notify"},
                                 data=json.dumps(body, ensure_ascii=False).encode("utf-8"))
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return 200 <= r.status < 300, ""
    except urllib.error.HTTPError as e:
        return False, f"{e.code} {e.read().decode('utf-8')[:200]}"
    except Exception as e:
        return False, str(e)[:200]


def who(r):
    """ログに出す名前（公開リポジトリの Actions のログは誰でも見られるので、メールは出さず行の番号だけ）"""
    return f"#{r['id']}"


# ─────────────────────────────── 本体
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", default="")
    ap.add_argument("--preview", default="")
    ap.add_argument("--today", default="")
    ap.add_argument("--html", default="", help="--preview の HTML をこのファイルに書く（手元の見た目の確認用）")
    a = ap.parse_args()

    today = date.fromisoformat(a.today) if a.today else datetime.now(JST).date()
    if not is_bizday(today):
        print(f"{today} は東証の休場日なので送らない")
        return 0
    F = Facts(today)
    F.build()
    print(f"今日 {today}・前営業日 {F.p1}（その前 {F.p2}・{F.p3}）・株価の日付 {F.price_date}・出来事のある銘柄 {len(F.ev)}")

    if a.preview:
        codes = [c.strip().upper() for c in a.preview.split(",") if c.strip()]
        m = compose(F, codes, "0" * 64)
        print(m["subject"] + "\n\n" + m["text"] if m else "（出来事が無いので送らない日）")
        if m and a.html:
            Path(a.html).write_text('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
                                    f'<title>{escape(m["subject"])}</title><body style="margin:0;background:#fff">' + m["html"],
                                    encoding="utf-8")
        return 0

    if F.price_date != F.p1.isoformat():
        # 古いデータで届けない（前営業日の株価が出来ていない＝夕方の収集が遅れた・落ちた）
        print(f"::error::prices.json の日付が {F.price_date}（前営業日 {F.p1} ではない）。今日は誰にも送らない")
        return 1

    try:
        rows = sb("kr_notify?select=id,email,codes,token,last_sent&confirmed=eq.true&order=id.asc&limit=1000")
    except NoTable:
        print("kr_notify がまだ無い（SQL を流す前）。何もしない")
        return 0
    if a.only:
        # "me" は GitHub の Secrets の NOTIFY_TO（本人のアドレス）。入力欄にメールを書くと実行の記録に残るので me を使う
        only = (os.environ.get("NOTIFY_TO") or "").strip() if a.only == "me" else a.only
        rows = [r for r in rows if r["email"] == only.strip().lower()]
        if not rows:
            print("::warning::--only のメールは確認済みの登録に無い")
            return 0
    tstr = today.isoformat()
    todo = []
    for r in rows:
        if r.get("last_sent") and datetime.fromisoformat(r["last_sent"].replace("Z", "+00:00")).astimezone(JST).date().isoformat() == tstr:
            continue                                   # 今日はもう送った（二重に送らない）
        m = compose(F, r.get("codes") or [], r["token"])
        if m:
            todo.append((r, m))
    print(f"確認済み {len(rows)} 人（うち地合いだけ {sum(1 for r in rows if not r.get('codes'))} 人）・今日送る {len(todo)} 人（出来事のない人には送らない）")
    if len(todo) > WARN_AT:
        print(f"::warning::今日送るのは {len(todo)} 通（{WARN_AT} 通超え）。上限 {DAILY_CAP} 通・Resend の無料枠 100通/日はルールトレードの通知・ログインメールと共用")
    if len(todo) > DAILY_CAP:
        print(f"::warning::今日送る {len(todo)} 人のうち {len(todo) - DAILY_CAP} 人は上限で送らない")
        todo = todo[:DAILY_CAP]

    if a.dry_run:
        # 文面（保有の銘柄）と停止のリンクの token はログに出さない。--only のときだけ本人の文面を token を伏せて出す
        for r, m in todo:
            print(f"{who(r)}: " + (f"{m['n']}銘柄に出来事" if m["n"] else "地合いと AI朝刊の結論だけ"))
            if a.only:
                print(m["subject"] + "\n" + m["text"].replace(r["token"], "<token>"))
        return 0

    api_key = (os.environ.get("RESEND_API_KEY") or "").strip()
    if not api_key:
        print("::warning::RESEND_API_KEY が無い（GitHub の Secrets に未登録）。送らずに終わる")
        return 0
    sent = failed = 0
    for r, m in todo:
        ok, why = send_mail(api_key, r["email"], m)
        if ok:
            sent += 1
            sb(f"kr_notify?id=eq.{r['id']}", "PATCH", {"last_sent": datetime.now(timezone.utc).isoformat()}, "return=minimal")
        else:
            failed += 1
            print(f"::warning::送れなかった {who(r)}: {why}")
        time.sleep(0.6)                                # Resend の 2通/秒 に合わせる
    print(f"送った {sent} 通・失敗 {failed} 通")

    # 確認されないまま7日たった申込みを消す
    if not a.only:
        old = (datetime.now(timezone.utc) - timedelta(days=UNCONFIRMED_DAYS)).isoformat()
        sb(f"kr_notify?confirmed=eq.false&created_at=lt.{urllib.parse.quote(old)}", "DELETE", prefer="return=minimal")
    return 1 if failed and not sent else 0


if __name__ == "__main__":
    sys.exit(main())
