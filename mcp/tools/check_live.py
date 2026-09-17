# -*- coding: utf-8 -*-
"""**本番**の MCP を叩いて、返ってくるものを機械で確かめる。

■ なぜ本番を叩くか
`npm test` はリポジトリの `docs/*.json` をフィクスチャにして動く。
本番は GitHub raw から読むので、**デプロイ漏れ・配信漏れ・rewrite の壊れ**は
ローカルのテストでは捕まらない。レジストリに「8本」と載せる前に、
実体が8本で動いていることをここで確かめる。

■ 何を見るか
  1. /health が ok
  2. tools/list が8本そろっている
  3. 各ツールが応答する（isError=false）
  4. 推奨語ゼロ（`src/legal.js` の NG_WORDS をそのまま使う）
  5. 銘柄コードが混ざっていない
     ★例外は2つだけ★
       - `get_daily_signals` は該当銘柄を1件だけ返す（設計どおり）
       - `get_etf_decay` は検証対象のETF（1357/1570/1360）を識別子として返す
  6. 「有料」「プラン」など区分を示す語がゼロ（2026-09-14 の方針）
  7. 応答サイズが 1ツール 50KB 以内
  8. llms.txt / openapi.json に8本ぶん載っている
  9. 案内している URL が正規URL（workers.dev を出していない）

    python mcp/tools/check_live.py
    python mcp/tools/check_live.py --base http://localhost:8787   # ローカル確認
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from legal_check import ng_words, find_codes, walk_strings  # noqa: E402

DEFAULT_BASE = "https://ruletrade.jp/mcp"
EXPECTED = [
    "get_daily_signals", "get_market_regime", "get_anomaly_summary",
    "get_candlestick_verdict", "get_event_reaction", "get_indicator_verdict",
    "get_etf_decay", "list_tools_guide",
]
# 実際にエージェントが投げそうな組み合わせ。detail=true も通しておく
CALLS = [
    ("get_daily_signals", {}),
    ("get_market_regime", {"history_days": 5}),
    ("get_anomaly_summary", {}),
    ("get_anomaly_summary", {"name": "セルインメイ"}),
    ("get_candlestick_verdict", {}),
    ("get_candlestick_verdict", {"pattern": "三空叩き込み", "detail": True}),
    ("get_event_reaction", {}),
    ("get_event_reaction", {"event": "地震"}),
    ("get_event_reaction", {"event": "決算", "detail": True}),
    ("get_event_reaction", {"event": "TOPIX"}),
    ("get_indicator_verdict", {}),
    ("get_indicator_verdict", {"name": "RSI", "detail": True}),
    ("get_etf_decay", {"detail": True}),
    ("list_tools_guide", {}),
]
ETF_CODES = ("1357", "1570", "1360")
BANNED = ["有料", "プラン", "free tier", "free-tier", "無料版",
          "paid", "premium", "upgrade", "subscription", "課金"]
LIMIT = 51200

fails = []
ok = lambda m: print("OK   %s" % m)


def ng(m):
    print("NG   %s" % m)
    fails.append(m)


# ★User-Agent を必ず名乗る★
# Python の既定 UA（Python-urllib/3.x）は配信側で 403 にされる。
# 素性の分かる名前を出す（呼び出しログの client 欄にもこれが残る）。
UA = "ruletrade-mcp-check/0.2 (+https://ruletrade.jp/)"


def post(base, body, timeout=30):
    req = urllib.request.Request(
        base, data=json.dumps(body).encode("utf-8"),
        headers={"content-type": "application/json",
                 "accept": "application/json",
                 "user-agent": UA,
                 "x-mcp-client": "check_live.py/0.2"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def get(url, timeout=30, fresh=False, with_headers=False):
    """fresh=True でキャッシュを迂回する。

    ★llms.txt / openapi.json は Vercel のエッジで最大1時間キャッシュされる★
    デプロイ直後は「Worker は新しいのに配信は古い」状態になる。
    どちらを見ているかを取り違えると、直っているものを不合格にしたり、
    その逆をやったりする。両方を見て区別する。
    """
    if fresh:
        url += ("&" if "?" in url else "?") + "cb=%d" % int(time.time())
    req = urllib.request.Request(url, headers={"user-agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read().decode("utf-8")
        return (body, dict(r.headers)) if with_headers else body


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=DEFAULT_BASE)
    args = ap.parse_args()
    base = args.base.rstrip("/")
    print("対象: %s" % base)
    print("")

    # ---- 1. health
    try:
        h = json.loads(get(base + "/health", fresh=True))
        if h.get("ok"):
            ok("health ok / version %s" % h.get("version"))
        else:
            ng("health が ok でない: %s" % json.dumps(h)[:300])
        if h.get("version") == "0.2.0":
            ok("version 0.2.0 が本番に出ている")
        else:
            ng("version が %s（0.2.0 のはず。デプロイ漏れ?）" % h.get("version"))
        bad = [k for k, v in (h.get("checks") or {}).items() if not v.get("ok")]
        if bad:
            ng("読めていないデータがある: %s" % bad)
        else:
            ok("元データ %d 本すべて読めている" % len(h.get("checks") or {}))
    except Exception as e:
        ng("health に到達できない: %s" % e)
        print("\n判定: 不合格（本番に届いていない）")
        return 1

    # ---- 2. tools/list
    try:
        tl = post(base, {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
        names = [t["name"] for t in tl["result"]["tools"]]
        if names == EXPECTED:
            ok("tools/list が8本・並びも想定どおり")
        else:
            ng("tools/list が違う: %s" % names)
        no_schema = [t["name"] for t in tl["result"]["tools"]
                     if t.get("inputSchema", {}).get("type") != "object"]
        if no_schema:
            ng("inputSchema が無いツール: %s" % no_schema)
        else:
            ok("8本すべてに inputSchema がある")
    except Exception as e:
        ng("tools/list に失敗: %s" % e)
        names = []

    # ---- 3〜7. 各ツールを実際に叩く
    words = ng_words()
    called = set()
    for name, a in CALLS:
        label = "%s%s" % (name, json.dumps(a, ensure_ascii=False) if a else "")
        try:
            r = post(base, {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                            "params": {"name": name, "arguments": a}})
        except Exception as e:
            ng("%s: 呼び出しに失敗 %s" % (label, e))
            continue
        res = r.get("result")
        if not res:
            ng("%s: result が無い %s" % (label, json.dumps(r)[:200]))
            continue
        if res.get("isError"):
            ng("%s: isError=true %s" % (label, json.dumps(res)[:200]))
            continue
        called.add(name)
        payload = res.get("structuredContent") or {}
        text = res["content"][0]["text"]

        if not payload.get("disclaimer"):
            ng("%s: disclaimer が無い" % label)
        size = len(text.encode("utf-8"))
        if size > LIMIT:
            ng("%s: %s バイト（50KB 超過）" % (label, format(size, ",")))

        joined = " ".join(t for _, t in walk_strings(payload))
        hit = [w for w in words if w.lower() in joined.lower()]
        if hit:
            ng("%s: 推奨語がある %s" % (label, hit))
        banned = [w for w in BANNED if w.lower() in joined.lower()]
        if banned:
            ng("%s: 区分・課金を示す語がある %s" % (label, banned))

        # ★銘柄コード★ 例外は3つだけ
        # list_tools_guide は get_etf_decay の説明を載せるので、そこに 1357 が出る
        allowed = ETF_CODES if name in ("get_etf_decay", "list_tools_guide") else ()
        codes = find_codes(payload, allowed=allowed)
        if name == "get_daily_signals":
            # 設計どおり1件だけ返る。その1件のコード以外が出たら混入
            top = payload.get("top_hit") or {}
            sod = (payload.get("full_system_today") or {}).get("published_one") or {}
            allowed = tuple(str(x) for x in (top.get("code"), sod.get("code")) if x)
            codes = find_codes(payload, allowed=allowed)
            if len(allowed) > 1:
                ng("%s: 銘柄が %d 件返っている（1件のはず）" % (label, len(allowed)))
        if codes:
            ng("%s: 想定外の銘柄コードがある %s" % (label, codes[:8]))

    missing = [n for n in EXPECTED if n not in called]
    if missing:
        ng("応答を確認できなかったツール: %s" % missing)
    else:
        ok("8本すべてが応答・免責あり・推奨語ゼロ・50KB 以内")
        ok("銘柄コードは get_daily_signals の1件と検証対象ETFのみ")
        ok("区分・課金を示す語がゼロ")

    # ---- 8. エージェント向けの2ファイル
    #     ★Worker が返す版を「正」として検査し、配信されている版が古い場合は
    #       不合格ではなく「キャッシュが切れるのを待つ」として知らせる★
    stale = []
    try:
        txt = get(base + "/llms.txt", fresh=True)
        miss = [n for n in EXPECTED if n not in txt]
        if miss:
            ng("llms.txt に載っていないツール: %s" % miss)
        else:
            ok("llms.txt に8本すべて載っている")
        if "workers.dev" in txt:
            ng("llms.txt が workers.dev を案内している")
        b = [w for w in BANNED if w.lower() in txt.lower()]
        if b:
            ng("llms.txt に区分・課金を示す語がある: %s" % b)

        cached, hdr = get(base + "/llms.txt", with_headers=True)
        if cached != txt:
            stale.append(("llms.txt", hdr.get("Age"), hdr.get("Cache-Control")))
    except Exception as e:
        ng("llms.txt を取得できない: %s" % e)

    try:
        raw, hdr = get(base + "/openapi.json", with_headers=True)
        fresh_raw = get(base + "/openapi.json", fresh=True)
        if raw != fresh_raw:
            stale.append(("openapi.json", hdr.get("Age"), hdr.get("Cache-Control")))
        spec = json.loads(fresh_raw)
        miss = [n for n in EXPECTED
                if "%s_arguments" % n not in spec.get("components", {}).get("schemas", {})]
        if miss:
            ng("openapi.json にスキーマが無いツール: %s" % miss)
        else:
            ok("openapi.json に8本ぶんのスキーマがある")
        if spec.get("info", {}).get("version") != "0.2.0":
            ng("openapi.json の version が %s" % spec.get("info", {}).get("version"))
        blob = json.dumps(spec, ensure_ascii=False)
        b = [w for w in BANNED if w.lower() in blob.lower()]
        if b:
            ng("openapi.json に区分・課金を示す語がある: %s" % b)
    except Exception as e:
        ng("openapi.json を取得できない: %s" % e)

    # ---- 9. 案内している URL
    try:
        root = json.loads(get(base.replace("/mcp", "") + "/mcp" if base.endswith("/mcp") else base))
    except Exception:
        root = None
    if root and root.get("endpoint") and "workers.dev" in json.dumps(root):
        ng("ルートが workers.dev を案内している")

    if stale:
        print("")
        print("※ 配信側（Vercel のエッジ）に古い版が残っています。"
              "Worker は新しいものを返しています。")
        for name, age, cc in stale:
            print("   %s  Age=%s  %s → 自然に切れるのを待つか、"
                  "本人が Vercel でパージ" % (name, age, cc))

    print("")
    print("判定: %s" % ("合格" if not fails else "不合格（%d 件）" % len(fails)))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
