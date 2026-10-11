# -*- coding: utf-8 -*-
"""株レーダー掲示板の口（kaburadar.jp/api/board）を本番で試す（2026-10-11 起動文 掲示板 の確認2・4・5）

一時的なテスト用アカウントを3つ作り（パスワードでログインして本物のログインの鍵を得る）、
上限・NGワード・URL禁止・ニックネームの重複・通報3件で非表示・管理の口が本人以外に閉じていることを確かめて、
最後にアカウントごと消す（kr_profiles / kr_predictions / kr_posts / kr_reports も on delete cascade で消える）。

上限を試すための「出している予想」は service role で **hidden=true** で入れる（画面に出ない・件数には数える）。
画面に出るのは「通報テスト用の雑談」1件だけで、通報3件で数秒のうちに非表示になる。
使い方: python -X utf8 board_apitest.py（SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY が要る）
"""
import json
import secrets
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import board_score as B   # Supabase の呼び方（service role）を共有する

SITE = "https://kaburadar.jp"
PUBLISHABLE = "sb_publishable_0O6qMsj7OkgW0UiIBIKIBQ_WGUsWXlp"   # 公開用（kaburadar.jp/board.js と同じ）


def http(method, url, body=None, headers=None):
    h = {"Content-Type": "application/json", "User-Agent": "kaburadar-board-apitest", "Origin": SITE}
    h.update(headers or {})
    req = urllib.request.Request(url, method=method, headers=h, data=json.dumps(body).encode("utf-8") if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode("utf-8") or "null")
    except urllib.error.HTTPError as e:
        t = e.read().decode("utf-8")
        try:
            return e.code, json.loads(t)
        except Exception:
            return e.code, {"raw": t[:200]}


def make_user(tag):
    email = f"board-apitest-{tag}-{int(time.time())}@kaburadar.jp"
    pw = secrets.token_urlsafe(24)
    u = B.call("admin/users", "POST", {"email": email, "password": pw, "email_confirm": True,
                                       "user_metadata": {"kaburadar_selftest": True}}, auth=True)
    base, _ = B.env()
    st, tok = http("POST", f"{base}/auth/v1/token?grant_type=password", {"email": email, "password": pw},
                   {"apikey": PUBLISHABLE})
    assert st == 200 and tok.get("access_token"), f"ログインできない {st} {tok}"
    return u["id"], tok["access_token"]


def board(a, tok=None, body=None, q=""):
    h = {"Authorization": "Bearer " + tok} if tok else {}
    return http("POST" if body is not None else "GET", f"{SITE}/api/board?a={a}{q}", body, h)


def main():
    stamp = datetime.now(timezone(timedelta(hours=9))).strftime("%H%M%S")
    users, checks = [], {}
    try:
        ua, ta = make_user("a"); users.append(ua)
        ub, tb = make_user("b"); users.append(ub)
        uc, tc = make_user("c"); users.append(uc)

        # ── ログインなし（確認5）
        st, r = board("post", None, {"code": "7203", "body": "x"})
        checks["ログインなしの書き込みは 401"] = st == 401 and r.get("need") == "login"
        st, r = board("list", q="&code=7203")
        checks["ログインなしで読める"] = st == 200 and r.get("ok")

        # ── ニックネーム
        st, r = board("profile", ta, {"nickname": "ﾃｽﾄA" + stamp, "agree": False})
        checks["規約に同意しないと登録できない"] = st == 400 and "同意" in r.get("error", "")
        st, r = board("profile", ta, {"nickname": "イナゴ" + stamp[:4], "agree": True})
        checks["NGワード入りのニックネームは止まる"] = st == 400
        st, r = board("profile", ta, {"nickname": "x", "agree": True})
        checks["1字のニックネームは止まる"] = st == 400
        nick_a = "テストA" + stamp
        st, r = board("profile", ta, {"nickname": nick_a, "agree": True})
        checks["ニックネームを登録できる"] = st == 200 and r.get("ok")
        st, r = board("profile", tb, {"nickname": nick_a, "agree": True})
        checks["同じニックネームは止まる"] = st == 409
        for tok, n in ((tb, "テストB" + stamp), (tc, "テストC" + stamp)):
            st, r = board("profile", tok, {"nickname": n, "agree": True})
            assert st == 200, r

        # ── 予想の上限（確認2）。出している予想は hidden=true で直接入れる（画面に出ない）
        past = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        codes10 = ["1301", "1332", "1605", "1801", "1802", "1803", "1812", "1925", "1928", "1963"]
        B.call("kr_predictions", "POST", [{"user_id": ua, "code": c, "side": "up", "horizon": "3m", "reason": "需給",
                                           "created_at": past, "hidden": True} for c in codes10], prefer="return=minimal")
        st, r = board("predict", ta, {"code": "7203", "side": "up", "horizon": "1w", "reason": "需給"})
        checks["11件目の予想は止まる（同時に10件まで）"] = st == 429 and "10件" in r.get("error", "")
        B.call("kr_predictions", "POST", {"user_id": ub, "code": "7203", "side": "up", "horizon": "3m", "reason": "需給",
                                          "created_at": past, "hidden": True}, prefer="return=minimal")
        st, r = board("predict", tb, {"code": "7203", "side": "down", "horizon": "1w", "reason": "需給"})
        checks["同じ銘柄の2件目は止まる"] = st == 409
        B.call("kr_predictions", "POST", [{"user_id": uc, "code": c, "side": "up", "horizon": "3m", "reason": "需給",
                                           "hidden": True} for c in codes10[:5]], prefer="return=minimal")
        st, r = board("predict", tc, {"code": "7203", "side": "up", "horizon": "1w", "reason": "需給"})
        checks["1日6件目の予想は止まる（1日5件まで）"] = st == 429 and "1日" in r.get("error", "")
        st, r = board("predict", tb, {"code": "6758", "side": "up", "horizon": "1w", "reason": "需給", "note": "https://example.com 見て"})
        checks["URL入りのひとことは止まる"] = st == 400
        st, r = board("predict", tb, {"code": "6758", "side": "up", "horizon": "1w", "reason": "なんとなく"})
        checks["決まった根拠以外は止まる"] = st == 400

        # ── 雑談（確認2）
        st, r = board("post", tb, {"code": "7203", "body": "この株は絶対上がる"})
        checks["NGワード入りの雑談は止まる"] = st == 400
        st, r = board("post", tb, {"code": "7203", "body": "詳しくは www.example.com で"})
        checks["URL入りの雑談は止まる"] = st == 400
        st, r = board("post", tb, {"code": "7203", "body": "あ" * 301})
        checks["301字の雑談は止まる"] = st == 400

        # ── 通報3件で非表示（確認4）。画面に出るのはこの1件だけ・数秒で消える
        st, r = board("post", tb, {"code": "9984", "body": "【自動テスト】通報の確認用の書き込みです。すぐ消えます"})
        assert st == 200, r
        pid = B.call(f"kr_posts?select=id&user_id=eq.{ub}&order=id.desc&limit=1")[0]["id"]
        st1, r1 = board("report", ta, {"type": "post", "id": pid})
        st1b, r1b = board("report", ta, {"type": "post", "id": pid})
        checks["同じ人の2回目の通報は数えない"] = r1.get("reports") == 1 and r1b.get("reports") == 1
        board("report", tc, {"type": "post", "id": pid})
        vis2 = any(p["id"] == pid for p in board("list", q="&code=9984")[1].get("posts", []))
        board("report", tb, {"type": "post", "id": pid})
        vis3 = any(p["id"] == pid for p in board("list", q="&code=9984")[1].get("posts", []))
        checks["通報2件ではまだ出ている"] = vis2
        checks["通報3件で非表示になった"] = not vis3
        hid = B.call(f"kr_posts?select=hidden,report_count&id=eq.{pid}")[0]
        checks["表の hidden=true・通報数3"] = hid["hidden"] is True and hid["report_count"] == 3

        # ── 管理の口は本人以外に閉じている（確認4）
        st, r = board("admin_list", ta)
        checks["管理の口は本人以外 403"] = st == 403
        st, r = board("admin_hide", ta, {"type": "post", "id": pid, "hidden": False})
        checks["本人以外は再表示できない"] = st == 403 and B.call(f"kr_posts?select=hidden&id=eq.{pid}")[0]["hidden"] is True
        st, r = board("admin_list")
        checks["ログインなしの管理の口は 401"] = st == 401
    finally:
        for u in users:
            B.call(f"admin/users/{u}", "DELETE", auth=True)
        left = sum(len(B.call(f"{t}?select=user_id&user_id=eq.{u}")) for u in users for t in ("kr_profiles", "kr_predictions", "kr_posts"))
        print(f"後片付け: テスト用アカウント{len(users)}つを削除（残り {left}行）")
        checks["後片付け（本番に残っていない）"] = left == 0
    for k, v in checks.items():
        print(f"  {'✓' if v else '✗'} {k}")
    if not all(checks.values()):
        raise SystemExit("::error::掲示板の口の試験に失敗")
    print("掲示板の口の試験 OK")


if __name__ == "__main__":
    main()
