# -*- coding: utf-8 -*-
"""株レーダー掲示板の予想を採点する（2026-10-11 起動文 掲示板）→ Supabase の kr_predictions と docs/board_activity.json

■ 起点と終点（台帳 ledger_score.py・空売りの答え合わせと同じ考え方）
  起点 = 投稿日（JST）の**翌営業日の始値**。引け後のニュースを見てから投稿する有利を消す。
  終点 = 起点日から h 営業日後の終値（1週間=5・1か月=20・3か月=60）。営業日は、その銘柄の株価がある日。
  上の予想で 終点>起点 なら win、終点<起点 なら lose。下の予想はその逆。同じ値は draw（成績に入れない）。
  ※ 投稿の時点で api/board.js が土日だけを飛ばした仮の起点日・期限日を入れている。ここで株価の暦で確定し直す
    （祝日をまたぐと期限日が後ろにずれる。画面の期限は「ごろ」と書いてある）

■ 採点が付いた予想は、銘柄ページ・新着からは消え（api/board.js が result is null だけを出す）、投稿者の成績に残る

■ 永久に埋まらない予想（上場廃止など）: 仮の期限日から30暦日たっても終値が取れなければ draw で閉じる
  （同時に10件までの枠をふさぎ続けないように。draw は成績に入らない）

■ docs/board_activity.json（サイトには出さない）: 銘柄ごとの雑談の件数（直近7日・30日）と、出ている予想の数。
  11月の「注目度の高い銘柄」の材料

■ 毎日の一問（2026-10-29 追加）: 日経平均の終値が前の営業日より上か下かを採点して kr_daily_q に書く（下の daily()）。
  表がまだ無い（SQL 未適用）ときは警告だけ出して、予想の採点は止めない

平日 19:30 JST に cron-worker から dispatch（ledger-daily と同じ枠）。
使い方:
  python -X utf8 board_score.py             採点して書き込む
  python -X utf8 board_score.py --dry-run  書き込まずに、付く予定の結果を出すだけ
  python -X utf8 board_score.py --selftest 一時的なテスト用アカウントで、期限を過去にした予想を1件作って採点し、
                                           公開の口（kaburadar.jp/api/board）で「銘柄ページから消えた・成績に入った」を
                                           確かめてから、アカウントごと消す（本番データに残さない）
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DOCS = HERE / "docs"
JST = timezone(timedelta(hours=9))
H = {"1w": 5, "1m": 20, "3m": 60}
GIVE_UP_DAYS = 30
SITE = "https://kaburadar.jp"


# ─────────────────────────────── Supabase（service role・GitHub Secrets。kr_export.py と同じ）
def env():
    base = (os.environ.get("SUPABASE_URL") or "").strip()
    m = re.search(r"https://[a-z0-9-]+\.supabase\.co", base)
    base = m.group(0) if m else base.rstrip("/")
    raw = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    m = re.search(r"sb_secret_[A-Za-z0-9_-]+|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", raw)
    key = m.group(0) if m else raw
    if not base or not key:
        raise SystemExit("::error::SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY が無い")
    return base, key


def call(path, method="GET", body=None, prefer=None, auth=False):
    base, key = env()
    url = f"{base}/{'auth/v1' if auth else 'rest/v1'}/{path}"
    h = {"apikey": key, "Authorization": "Bearer " + key, "Content-Type": "application/json"}
    if prefer:
        h["Prefer"] = prefer
    req = urllib.request.Request(url, method=method, headers=h,
                                 data=json.dumps(body).encode("utf-8") if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            t = r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        raise SystemExit(f"::error::supabase {method} {path.split('?')[0]} {e.code}: {e.read().decode('utf-8')[:300]}")
    return json.loads(t) if t.strip() else None


def get_all(table, query):
    rows, off = [], 0
    while True:
        part = call(f"{table}?{query}&limit=1000&offset={off}")
        rows += part
        if len(part) < 1000:
            return rows
        off += 1000


# ─────────────────────────────── 株価（ledger_score.py と同じ取り方）
def tick(code):
    c = code.strip()
    return (c[:4] if len(c) == 5 and c.endswith("0") else c) + ".T"


def fetch(codes, start):
    import pandas as pd
    import yfinance as yf
    px = {}
    tks = sorted({tick(c) for c in codes})
    for i in range(0, len(tks), 60):
        chunk = tks[i:i + 60]
        try:
            df = yf.download(" ".join(chunk), start=start, progress=False, auto_adjust=False,
                             threads=False, group_by="ticker")
        except Exception as e:
            print(f"  価格取得失敗: {e}", file=sys.stderr)
            continue
        for t in chunk:
            try:
                sub = df[t] if isinstance(df.columns, pd.MultiIndex) else df
                o, cl = sub["Open"].dropna(), sub["Close"].dropna()
                if len(cl) < 1:
                    continue
                px[t] = ({k.strftime("%Y-%m-%d"): float(v) for k, v in o.items()},
                         {k.strftime("%Y-%m-%d"): float(v) for k, v in cl.items()})
            except Exception:
                continue
        time.sleep(1.0)
    return px


def last_settled_day(now=None):
    """終値が確定している最後の日。15:45 JST より前に手で走らせたときは、今日の足（途中の値）を使わない"""
    now = now or datetime.now(JST)
    d = now.date()
    return d if (now.hour, now.minute) >= (15, 45) else d - timedelta(days=1)


def add_weekdays(d, n):
    k = 0
    while k < n:
        d += timedelta(days=1)
        if d.weekday() < 5:
            k += 1
    return d


def judge(side, start, end):
    if end == start:
        return "draw"
    return "win" if (end > start) == (side == "up") else "lose"


def score_one(p, px, settled, today):
    """1件を採点して、書き込む欄（変わらなければ空の dict）を返す"""
    d0 = datetime.fromisoformat(p["created_at"].replace("Z", "+00:00")).astimezone(JST).date().isoformat()
    h = H[p["horizon"]]
    upd = {}
    pr = px.get(tick(p["code"]))
    if pr:
        opens, closes = pr
        after = sorted(d for d in closes if d0 < d <= settled.isoformat())
        if after and opens.get(after[0]):
            s_day, s_px = after[0], round(opens[after[0]], 2)
            if p.get("start_date") != s_day:
                upd["start_date"] = s_day
            if p.get("start_price") is None or float(p["start_price"]) != s_px:
                upd["start_price"] = s_px
            if len(after) > h:
                e_day, e_px = after[h], round(closes[after[h]], 2)
                upd.update(due_date=e_day, end_price=e_px, result=judge(p["side"], s_px, e_px),
                           scored_at=datetime.now(timezone.utc).isoformat())
            else:
                # まだ期限前。仮の期限日を、確定した起点日から（土日だけ飛ばして）引き直す
                due = add_weekdays(date.fromisoformat(s_day), h).isoformat()
                if p.get("due_date") != due:
                    upd["due_date"] = due
    if "result" not in upd and p.get("due_date"):
        if (today - date.fromisoformat(p["due_date"])).days > GIVE_UP_DAYS:
            upd.update(result="draw", scored_at=datetime.now(timezone.utc).isoformat())
    return upd


def run(dry=False, only_ids=None):
    q = "select=id,user_id,code,side,horizon,created_at,start_date,start_price,due_date&result=is.null&order=id"
    if only_ids:
        q += f"&id=in.({','.join(str(i) for i in only_ids)})"
    preds = get_all("kr_predictions", q)
    print(f"採点待ち {len(preds)}件")
    if not preds:
        return []
    oldest = min(datetime.fromisoformat(p["created_at"].replace("Z", "+00:00")).date() for p in preds)
    px = fetch({p["code"] for p in preds}, (oldest - timedelta(days=7)).isoformat())
    settled, today = last_settled_day(), datetime.now(JST).date()
    done = []
    for p in preds:
        upd = score_one(p, px, settled, today)
        if not upd:
            continue
        tag = upd.get("result", "（期限前）")
        print(f"  #{p['id']} {p['code']} {p['side']} {p['horizon']} → {tag} {json.dumps(upd, ensure_ascii=False)}")
        if not dry:
            call(f"kr_predictions?id=eq.{p['id']}", "PATCH", upd, prefer="return=minimal")
        if "result" in upd:
            done.append((p["id"], upd["result"]))
    print(f"採点 {len(done)}件" + ("（dry-run・書き込みなし）" if dry else ""))
    return done


def activity():
    """銘柄ごとの雑談の件数と、出ている予想の数（サイトには出さない・11月の注目度の材料）"""
    now = datetime.now(JST)
    since30 = (now - timedelta(days=30)).isoformat()
    since7 = (now - timedelta(days=7)).isoformat()
    posts = get_all("kr_posts", f"select=code,created_at&hidden=eq.false&created_at=gte.{urllib.parse.quote(since30)}")
    preds = get_all("kr_predictions", "select=code&hidden=eq.false&result=is.null")
    out = {}
    for r in posts:
        o = out.setdefault(r["code"], {"posts_7d": 0, "posts_30d": 0, "open_preds": 0})
        o["posts_30d"] += 1
        if r["created_at"] >= since7:
            o["posts_7d"] += 1
    for r in preds:
        out.setdefault(r["code"], {"posts_7d": 0, "posts_30d": 0, "open_preds": 0})["open_preds"] += 1
    DOCS.mkdir(exist_ok=True)
    (DOCS / "board_activity.json").write_text(json.dumps(
        {"updated": now.isoformat(timespec="seconds"), "note": "掲示板の銘柄ごとの雑談の件数と出ている予想の数。サイトには出さない",
         "items": dict(sorted(out.items(), key=lambda kv: -kv[1]["posts_30d"]))},
        ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"board_activity.json {len(out)}銘柄")


# ─────────────────────────────── 毎日の一問「今日の日経平均は上がる？下がる？」（2026-10-29 起動文 第1波 担当A）
# 問題の日 d の日経平均の終値が、前の営業日の終値より上なら up・下なら down・同じなら draw。
# 暦は jp_bizday（サイトの api/board.js に同じ計算を写してある）。終値は ledger_score.py と同じ出所（yfinance の ^N225）。
# 臨時休場などで d の終値が5日たっても無ければ void（成績に入れない）。票数は締切時点のものを kr_daily_q に残す。
DAILY_START = date(2026, 10, 30)   # #1（api/board.js の DAILY_START と同じ）
DAILY_VOID_DAYS = 5


def daily_no(d):
    import jp_bizday
    n, cur = 0, DAILY_START
    while cur <= d:
        n += jp_bizday.is_bizday(cur)
        cur += timedelta(days=1)
    return n


def prev_bizday(d):
    import jp_bizday
    cur = d - timedelta(days=1)
    while not jp_bizday.is_bizday(cur):
        cur -= timedelta(days=1)
    return cur


def n225_closes(start):
    import yfinance as yf
    df = yf.download("^N225", start=start, progress=False, auto_adjust=False, threads=False)
    cl = df["Close"]
    if hasattr(cl, "columns"):      # 1銘柄でも列が2段になる版がある
        cl = cl.iloc[:, 0]
    return {k.strftime("%Y-%m-%d"): float(v) for k, v in cl.dropna().items()}


def daily_count(d, side):
    base, key = env()
    req = urllib.request.Request(f"{base}/rest/v1/kr_daily_votes?select=id&d=eq.{d}&side=eq.{side}&limit=1",
                                 headers={"apikey": key, "Authorization": "Bearer " + key, "Prefer": "count=exact"})
    with urllib.request.urlopen(req, timeout=60) as r:
        m = re.search(r"/(\d+)$", r.headers.get("Content-Range") or "")
    return int(m.group(1)) if m else 0


def daily_score_days(closes, days, today):
    """採点する日ごとに kr_daily_q に書く行を返す（取得と分けてある＝手元で試せる）"""
    rows, keys = [], sorted(closes)
    for d in days:
        ds = d.isoformat()
        if ds in closes:
            prev = [k for k in keys if k < ds]
            if not prev:
                continue
            p, c = round(closes[prev[-1]], 2), round(closes[ds], 2)
            if prev[-1] != prev_bizday(d).isoformat():
                print(f"::warning::{ds} の前の終値が {prev[-1]} の値（暦の前営業日 {prev_bizday(d)} の値が配信に無い）")
            rows.append({"d": ds, "no": daily_no(d), "prev_close": p, "close": c,
                         "result": "draw" if c == p else ("up" if c > p else "down")})
        elif (today - d).days > DAILY_VOID_DAYS:
            rows.append({"d": ds, "no": daily_no(d), "prev_close": None, "close": None, "result": "void"})
    return rows


def daily(dry=False):
    import jp_bizday
    settled, today = last_settled_day(), datetime.now(JST).date()
    if settled < DAILY_START:
        print("毎日の一問: まだ始まっていない")
        return
    done = {r["d"] for r in get_all("kr_daily_q", "select=d&result=not.is.null")}
    days, cur = [], DAILY_START
    while cur <= settled:
        if jp_bizday.is_bizday(cur) and cur.isoformat() not in done:
            days.append(cur)
        cur += timedelta(days=1)
    print(f"毎日の一問: 採点待ち {len(days)}日")
    if not days:
        return
    closes = n225_closes((days[0] - timedelta(days=14)).isoformat())
    for row in daily_score_days(closes, days, today):
        row.update(up_n=daily_count(row["d"], "up"), down_n=daily_count(row["d"], "down"),
                   scored_at=datetime.now(timezone.utc).isoformat())
        print(f"  #{row['no']} {row['d']} 日経平均 {row['prev_close']} → {row['close']} = {row['result']}"
              f"（上{row['up_n']}・下{row['down_n']}）" + ("（dry-run・書き込みなし）" if dry else ""))
        if not dry:
            call("kr_daily_q?on_conflict=d", "POST", row, prefer="return=minimal,resolution=merge-duplicates")


def daily_safe(dry=False):
    # 表（kr_daily_votes / kr_daily_q）を本人が Supabase に入れる前は失敗する。掲示板の予想の採点は止めない
    try:
        daily(dry)
    except SystemExit as e:
        print(f"::warning::毎日の一問の採点をとばした: {e}")
    except Exception as e:
        print(f"::warning::毎日の一問の採点をとばした: {type(e).__name__}: {e}")


# ─────────────────────────────── 自己テスト（本番データに残さない）
def site(path):
    req = urllib.request.Request(SITE + path, headers={"User-Agent": "kaburadar-board-selftest", "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def selftest():
    code = "7203"
    nick = "テスト" + datetime.now(JST).strftime("%H%M%S")
    email = f"board-selftest+{int(time.time())}@kaburadar.jp"
    u = call("admin/users", "POST", {"email": email, "email_confirm": True,
                                       "user_metadata": {"kaburadar_selftest": True}}, auth=True)
    uid = u["id"]
    ok = True
    try:
        call("kr_profiles", "POST", {"user_id": uid, "nickname": nick, "pioneer": False}, prefer="return=minimal")
        # 期限を過去にした予想: 40日前に投稿・1週間（5営業日）。もう1件は今日の投稿（期限前のまま残るはず）
        past = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
        r1 = call("kr_predictions", "POST", {"user_id": uid, "code": code, "side": "up", "horizon": "1w", "reason": "チャート",
                                             "note": "自己テスト（すぐ消します）", "created_at": past}, prefer="return=representation")
        pid = r1[0]["id"]
        r2 = call("kr_predictions", "POST", {"user_id": uid, "code": "6758", "side": "down", "horizon": "3m", "reason": "需給",
                                             "note": "自己テスト（すぐ消します）"}, prefer="return=representation")
        pid2 = r2[0]["id"]
        before = site(f"/api/board?a=list&code={code}")
        seen_before = any(p["id"] == pid for p in before.get("preds", []))
        print(f"採点前: 銘柄ページの予想に出ている={seen_before}")
        done = run(only_ids=[pid, pid2])
        row = call(f"kr_predictions?select=start_date,start_price,due_date,end_price,result&id=eq.{pid}")[0]
        row2 = call(f"kr_predictions?select=result,start_date,due_date&id=eq.{pid2}")[0]
        print(f"採点後（過去の予想）: {json.dumps(row, ensure_ascii=False)}")
        print(f"採点後（今日の予想）: {json.dumps(row2, ensure_ascii=False)}")
        after = site(f"/api/board?a=list&code={code}")
        seen_after = any(p["id"] == pid for p in after.get("preds", []))
        up = site(f"/api/board?a=user&n={urllib.parse.quote(nick)}")
        rec = [p for p in up.get("preds", []) if p["id"] == pid]
        checks = {
            "採点前は銘柄ページに出ている": seen_before,
            "過去の予想に win/lose/draw が付いた": row["result"] in ("win", "lose", "draw") and dict(done).get(pid) == row["result"],
            "起点の始値・終点の終値が入った": row["start_price"] is not None and row["end_price"] is not None,
            "起点日 < 期限日": row["start_date"] < row["due_date"],
            "今日の予想は期限前のまま": row2["result"] is None,
            "採点後は銘柄ページから消えた": not seen_after,
            "投稿者ページの成績に入った": bool(rec) and rec[0]["result"] == row["result"],
        }
        for k, v in checks.items():
            print(f"  {'✓' if v else '✗'} {k}")
        ok = all(checks.values())
    finally:
        # アカウントを消すと kr_profiles / kr_predictions も消える（on delete cascade）
        call(f"admin/users/{uid}", "DELETE", auth=True)
        left = call(f"kr_profiles?select=user_id&user_id=eq.{uid}")
        left2 = call(f"kr_predictions?select=id&user_id=eq.{uid}")
        print(f"後片付け: テスト用アカウントを削除（残り profiles={len(left)} predictions={len(left2)}）")
        ok = ok and not left and not left2
    if not ok:
        raise SystemExit("::error::自己テストに失敗")
    print("自己テスト OK")


if __name__ == "__main__":
    a = sys.argv[1:]
    if "--selftest" in a:
        selftest()
    else:
        run(dry="--dry-run" in a)
        if "--dry-run" not in a:
            activity()
        daily_safe(dry="--dry-run" in a)
