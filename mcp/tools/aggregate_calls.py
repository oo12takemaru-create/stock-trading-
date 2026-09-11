# -*- coding: utf-8 -*-
"""MCP の呼び出しログを集計する（ツール別 × client 別・日別の推移）。

■ 何を見るためか
`ruletrade-mcp` は1回の呼び出しごとに1行の JSON を console.log している
（`mcp/src/server.js` の emitLog）。

    {"ts":"...","event":"tool_call","tool":"get_daily_signals","args":{...},
     "ok":true,"ms":123,"scrubbed":0,"client":"claude-ai/1.0"}

この行を集めて「どのツールが・どのクライアントから・何回呼ばれたか」を数える。
レジストリ掲載（2026-09-05）の前後で変化があるかを見るのが最初の用途。

■ ログがどこにあるか（ここが肝心）
`console.log` の行き先は **Workers Logs（Observability）** で、
**保持期間が短い**（無料枠では数日）。90日ぶん貯めたいなら
`wrangler.toml` の Analytics Engine を有効にする必要がある（既定では
コメントアウトされている）。このスクリプトは両方に対応していて、
使えるほうから取る。

    1. Analytics Engine（あれば最優先。保持90日・SQL で集計できる）
    2. Workers Logs の Observability API（保持が短い）
    3. どちらも駄目なら、手で落とした JSONL を読む（--file）

■ 使い方
    python mcp/tools/aggregate_calls.py --from 2026-09-04 --to 2026-09-11

  認証は `wrangler login` 済みならそのトークンを自動で使う（設定は要らない）。
  別の API Token を使いたいときだけ CF_API_TOKEN を設定する。

    # ダッシュボードから落とした JSONL を集計する場合（トークン不要）
    python mcp/tools/aggregate_calls.py --file logs.jsonl

■ 必要な API Token の権限
    Account → Workers Observability : Read      （Workers Logs を読む）
    Account → Account Analytics     : Read      （Analytics Engine を読む）
  `wrangler login` の OAuth トークンには Observability の権限が**含まれない**
  ので、ダッシュボードで API Token を別に発行する必要がある。
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
import re
import sys
import urllib.error
import urllib.request

ACCOUNT_ID = os.environ.get("CF_ACCOUNT_ID", "c3616de29952aefefc294797cbd5383b")
SCRIPT = "ruletrade-mcp"
DATASET = "ruletrade_mcp_calls"      # wrangler.toml の analytics_engine_datasets と合わせる
API = "https://api.cloudflare.com/client/v4"

# レジストリ掲載日。この前後で分けて数える
REGISTRY_DATE = dt.date(2026, 9, 5)


# ── クライアント名の正規化 ────────────────────────────────────
# client は x-mcp-client か User-Agent の生値なので、そのままだと
# バージョン違いが別物として数えられてしまう。代表的なものをまとめる。
CLIENT_PATTERNS = [
    (re.compile(r"claude[-_ ]?ai|anthropic", re.I), "claude.ai"),
    (re.compile(r"claude[-_ ]?code", re.I), "Claude Code"),
    (re.compile(r"claude[-_ ]?desktop", re.I), "Claude Desktop"),
    (re.compile(r"cursor", re.I), "Cursor"),
    (re.compile(r"smithery", re.I), "Smithery"),
    (re.compile(r"windsurf|codeium", re.I), "Windsurf"),
    (re.compile(r"cline", re.I), "Cline"),
    (re.compile(r"vscode|visual studio", re.I), "VS Code"),
    (re.compile(r"python-requests|httpx|curl|wget|node-fetch|undici|axios", re.I), "スクリプト/手動"),
    (re.compile(r"bot|crawler|spider|scan", re.I), "ボット/巡回"),
]


def normalize_client(raw):
    s = (raw or "").strip()
    if not s:
        return "(不明)"
    for pat, name in CLIENT_PATTERNS:
        if pat.search(s):
            return name
    # 見覚えの無いものは頭だけ残す（生の UA を全部出すと表が読めない）
    return s.split("/")[0][:28] or "(不明)"


def wrangler_token():
    """`wrangler login` が置いた OAuth トークンを探して返す（無ければ None）。

    ★毎回 CF_API_TOKEN を設定させないため★
    wrangler でデプロイできる人は既にログイン済みなので、そのトークンを使えば
    そのまま動く。実測で Analytics Engine の SQL も GraphQL もこれで通った
    （Workers Logs の Observability だけは権限が足りず 403 になる）。

    置き場所は OS と版で違うので、ありそうな所を順に見る。
    """
    home = os.path.expanduser("~")
    candidates = [
        os.path.join(os.environ.get("APPDATA", ""), "xdg.config", ".wrangler", "config", "default.toml"),
        os.path.join(home, ".wrangler", "config", "default.toml"),
        os.path.join(home, ".config", ".wrangler", "config", "default.toml"),
        os.path.join(os.environ.get("XDG_CONFIG_HOME", ""), ".wrangler", "config", "default.toml"),
    ]
    for path in candidates:
        if not path or not os.path.exists(path):
            continue
        try:
            m = re.search(r'oauth_token\s*=\s*"([^"]+)"', open(path, encoding="utf-8").read())
        except OSError:
            continue
        if m:
            return m.group(1)
    return None


def _req(url, token, body=None, method="GET", timeout=90):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:                              # 通信そのものの失敗
        return 0, str(e)


# ══════════════════════════════════════════════════════════
#  取り口1: Analytics Engine（保持90日・SQL）
# ══════════════════════════════════════════════════════════
def from_analytics_engine(token, d_from, d_to, log=print):
    """emitLog の writeDataPoint を SQL で集計する。

    blobs = [event, tool, client, ok/err] / doubles = [ms] / indexes = [tool]
    （mcp/src/server.js の emitLog と対応。並びを変えたらここも直す）
    """
    sql = (
        "SELECT toDate(timestamp) AS d, blob2 AS tool, blob3 AS client, "
        "blob4 AS status, SUM(_sample_interval) AS n "
        "FROM %s "
        "WHERE timestamp >= toDateTime('%s 00:00:00') "
        "  AND timestamp <  toDateTime('%s 00:00:00') "
        "  AND blob1 = 'tool_call' "
        "GROUP BY d, tool, client, status "
        "ORDER BY d, n DESC "
        "FORMAT JSON"
        % (DATASET, d_from, d_to + dt.timedelta(days=1))
    )
    url = "%s/accounts/%s/analytics_engine/sql" % (API, ACCOUNT_ID)
    req = urllib.request.Request(url, data=sql.encode("utf-8"), method="POST",
                                 headers={"Authorization": "Bearer " + token})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            body = json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:200]
        log("  Analytics Engine: 使えません（HTTP %s %s）" % (e.code, detail))
        return None
    except Exception as e:
        log("  Analytics Engine: 使えません（%s）" % e)
        return None

    rows = []
    for r in body.get("data", []):
        rows.append({
            "date": r["d"][:10],
            "tool": r.get("tool") or "(不明)",
            "client": r.get("client") or "",
            "ok": (r.get("status") == "ok"),
            "n": int(float(r.get("n") or 0)),
        })
    log("  Analytics Engine: %d 行" % len(rows))
    return rows


# ══════════════════════════════════════════════════════════
#  取り口2: Workers Logs（Observability API・保持が短い）
# ══════════════════════════════════════════════════════════
def from_observability(token, d_from, d_to, log=print):
    """Workers Logs を問い合わせる。

    ★API の形が変わりやすい★
    公式ドキュメントに載っている形が版によって違うので、いくつか試して
    通ったものを使う。全部落ちたら None を返して次の取り口に渡す。
    """
    t_from = int(dt.datetime.combine(d_from, dt.time.min).timestamp() * 1000)
    t_to = int(dt.datetime.combine(d_to + dt.timedelta(days=1), dt.time.min).timestamp() * 1000)
    url = "%s/accounts/%s/workers/observability/telemetry/query" % (API, ACCOUNT_ID)

    shapes = [
        {  # 版1
            "queryId": "ruletrade-mcp-tool-calls",
            "timeframe": {"from": t_from, "to": t_to},
            "parameters": {
                "datasets": ["cloudflare-workers"],
                "filters": [{"key": "$metadata.service", "operation": "eq", "value": SCRIPT}],
            },
            "limit": 10000,
            "view": "events",
        },
        {  # 版2（parameters を平らに持つ形）
            "timeframe": {"from": t_from, "to": t_to},
            "datasets": ["cloudflare-workers"],
            "filters": [{"key": "$metadata.service", "operation": "eq", "value": SCRIPT}],
            "limit": 10000,
        },
    ]
    for i, body in enumerate(shapes, 1):
        st, text = _req(url, token, body, "POST")
        if st == 200:
            try:
                data = json.loads(text)
            except ValueError:
                continue
            events = (data.get("result") or {}).get("events") or data.get("result") or []
            if isinstance(events, dict):
                events = events.get("events") or []
            rows = _parse_events(events)
            log("  Workers Logs: %d 件（形%d）" % (len(rows), i))
            return rows
        if st in (401, 403):
            log("  Workers Logs: 権限がありません（HTTP %s）。"
                "API Token に Workers Observability: Read が要ります" % st)
            return None
        log("  Workers Logs: 形%d は HTTP %s" % (i, st))
    return None


def _parse_events(events):
    """Observability の生イベントから tool_call の行を取り出す。"""
    rows = []
    for ev in events or []:
        # console.log の中身は版によって置き場所が違う
        raw = None
        for k in ("message", "$workers.message", "line", "log"):
            v = ev.get(k) if isinstance(ev, dict) else None
            if isinstance(v, str) and v.strip().startswith("{"):
                raw = v
                break
        if raw is None:
            src = ev.get("source") if isinstance(ev, dict) else None
            if isinstance(src, dict):
                msg = src.get("message")
                if isinstance(msg, list) and msg and isinstance(msg[0], str):
                    raw = msg[0]
                elif isinstance(msg, str):
                    raw = msg
        if not raw:
            continue
        try:
            entry = json.loads(raw)
        except ValueError:
            continue
        if entry.get("event") != "tool_call":
            continue
        rows.append({
            "date": str(entry.get("ts", ""))[:10],
            "tool": entry.get("tool") or "(不明)",
            "client": entry.get("client") or "",
            "ok": bool(entry.get("ok")),
            "n": 1,
        })
    return rows


# ══════════════════════════════════════════════════════════
#  取り口3: GraphQL Analytics API（総量だけ・ただし確実に取れる）
# ══════════════════════════════════════════════════════════
def from_graphql(token, d_from, d_to, log=print):
    """Worker への**リクエスト数**を日別に取る。

    ★これは tool_call の数ではない★
    MCP は JSON-RPC なので、1回の会話で initialize / tools/list / tools/call が
    それぞれ HTTP リクエストになる。ヘルスチェックも混ざる。
    つまり「呼び出しの総量」であって「どのツールが何回」ではない。

    それでも取る理由は、**Workers Logs と違って保持が長く、権限も通りやすい**から。
    ツール別・クライアント別が要るなら Analytics Engine を有効にすること
    （wrangler.toml のコメントを外す。有効にした日から貯まる）。
    """
    q = """
    query($acc:String!,$from:Time!,$to:Time!){
      viewer{ accounts(filter:{accountTag:$acc}){
        workersInvocationsAdaptive(limit:1000, filter:{
            datetime_geq:$from, datetime_leq:$to, scriptName:"%s"}){
          dimensions{ date }
          sum{ requests errors }
        }
      }}
    }""" % SCRIPT
    body = {"query": q, "variables": {
        "acc": ACCOUNT_ID,
        "from": dt.datetime.combine(d_from, dt.time.min).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "to": dt.datetime.combine(d_to, dt.time.max).strftime("%Y-%m-%dT%H:%M:%SZ")}}
    st, text = _req(API + "/graphql", token, body, "POST")
    if st != 200:
        log("  GraphQL: HTTP %s" % st)
        return None
    try:
        res = json.loads(text)
    except ValueError:
        return None
    if res.get("errors"):
        log("  GraphQL: %s" % json.dumps(res["errors"], ensure_ascii=False)[:160])
        return None
    accs = (res.get("data") or {}).get("viewer", {}).get("accounts") or []
    if not accs:
        return None
    rows = []
    for r in accs[0].get("workersInvocationsAdaptive") or []:
        rows.append({
            "date": r["dimensions"]["date"][:10],
            "tool": "(合計のみ)",          # ツール名は console.log にしかない
            "client": "",
            "ok": True,
            "n": int(r["sum"]["requests"]),
            "errors": int(r["sum"].get("errors") or 0),
        })
    log("  GraphQL: %d 日ぶん" % len(rows))
    return rows


# ══════════════════════════════════════════════════════════
#  取り口4: 手で落とした JSONL
# ══════════════════════════════════════════════════════════
def from_file(path, log=print):
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                # ダッシュボードの書き出しは {"message": "{...}"} で包まれることがある
                continue
            if isinstance(entry.get("message"), str):
                try:
                    entry = json.loads(entry["message"])
                except ValueError:
                    continue
            if entry.get("event") != "tool_call":
                continue
            rows.append({
                "date": str(entry.get("ts", ""))[:10],
                "tool": entry.get("tool") or "(不明)",
                "client": entry.get("client") or "",
                "ok": bool(entry.get("ok")),
                "n": 1,
            })
    log("  ファイル: %d 件" % len(rows))
    return rows


# ══════════════════════════════════════════════════════════
def report(rows, d_from, d_to, out=sys.stdout):
    p = lambda s="": print(s, file=out)
    if not rows:
        p("集計できる呼び出しがありませんでした。")
        p("")
        p("考えられること:")
        p("  ・Workers Logs の保持期間を過ぎている（無料枠は数日ぶんしか残らない）")
        p("  ・その期間に呼び出しが1件も無かった")
        p("  ・API Token に Workers Observability: Read が付いていない")
        p("")
        p("→ 長い期間を見たいなら wrangler.toml の Analytics Engine を有効にする")
        p("  （保持90日・無料枠あり）。有効にした日から貯まる。")
        return

    total = sum(r["n"] for r in rows)
    dates = sorted({r["date"] for r in rows if r["date"]})
    p("MCP 呼び出しログ集計  %s 〜 %s" % (d_from, d_to))
    p("=" * 58)
    p("総呼び出し数: %s 件" % f"{total:,}")
    if dates:
        p("実データの範囲: %s 〜 %s（%d 日）" % (dates[0], dates[-1], len(dates)))
    err = sum(r["n"] for r in rows if not r["ok"])
    p("失敗: %d 件 (%.1f%%)" % (err, err / total * 100 if total else 0))
    p("")

    only_total = all(r["tool"] == "(合計のみ)" for r in rows)
    if only_total:
        p("■ 内訳（ツール別・クライアント別）")
        p("  取れませんでした。GraphQL は Worker への**リクエスト総数**しか返しません。")
        p("  内訳は console.log の中にあり、それを読むには")
        p("    ・Workers Logs（保持が短い／API Token に Observability: Read が要る）")
        p("    ・Analytics Engine（保持90日／wrangler.toml のコメントを外す）")
        p("  のどちらかが要ります。")
        p("")
        p("  ※ MCP は JSON-RPC なので、1回の会話で initialize / tools/list /")
        p("     tools/call がそれぞれ1リクエストになります。ヘルスチェックも混ざるため、")
        p("     下の数字は**会話の回数でもツール実行の回数でもない**ことに注意。")
        p("")

    # ── ツール別 × client 別 ──
    cross = collections.defaultdict(int)
    tools = collections.Counter()
    clients = collections.Counter()
    for r in rows:
        c = normalize_client(r["client"])
        cross[(r["tool"], c)] += r["n"]
        tools[r["tool"]] += r["n"]
        clients[c] += r["n"]

    cl_order = [c for c, _ in clients.most_common()]
    w = max([len(t) for t in tools] + [10])
    if not only_total:
      p("■ ツール別 × クライアント別")
      p("%-*s %s %8s" % (w, "ツール", " ".join("%12s" % c[:12] for c in cl_order), "合計"))
      p("-" * (w + 13 * len(cl_order) + 9))
      for tool, n in tools.most_common():
          cells = " ".join("%12s" % (cross.get((tool, c), 0) or "-") for c in cl_order)
          p("%-*s %s %8d" % (w, tool, cells, n))
      p("%-*s %s %8d" % (w, "合計", " ".join("%12d" % clients[c] for c in cl_order), total))
      p("")

    # ── 日別（レジストリ掲載の前後）──
    by_day = collections.Counter()
    for r in rows:
        if r["date"]:
            by_day[r["date"]] += r["n"]
    p("■ 日別の推移")
    # バーは最大値を40桁に合わせる（件数をそのまま桁数にすると全部振り切れる）
    mx = max(by_day.values()) if by_day else 1
    for d in sorted(by_day):
        mark = ""
        try:
            if dt.date.fromisoformat(d) == REGISTRY_DATE:
                mark = "  ← レジストリ掲載"
        except ValueError:
            pass
        bar = "▇" * max(1, round(by_day[d] / mx * 40))
        p("  %s  %5d 件 %-40s%s" % (d, by_day[d], bar, mark))
    p("")

    before = [d for d in by_day if d and dt.date.fromisoformat(d) < REGISTRY_DATE]
    after = [d for d in by_day if d and dt.date.fromisoformat(d) >= REGISTRY_DATE]
    if before and after:
        b = sum(by_day[d] for d in before) / len(before)
        a = sum(by_day[d] for d in after) / len(after)
        p("■ レジストリ掲載（%s）の前後" % REGISTRY_DATE)
        p("  前: %d 日で 1日あたり %.1f 件" % (len(before), b))
        p("  後: %d 日で 1日あたり %.1f 件" % (len(after), a))
        if b > 0:
            p("  変化: %+.0f%%" % ((a - b) / b * 100))
        p("")
        p("  ※ 日数が少ないうちは増減の判断材料になりません。")
    else:
        p("■ レジストリ掲載（%s）の前後" % REGISTRY_DATE)
        p("  片側のデータしかないため比較できません"
          "（掲載前 %d 日 / 掲載後 %d 日）。" % (len(before), len(after)))


def main():
    ap = argparse.ArgumentParser(description="MCP の呼び出しログを集計する")
    ap.add_argument("--from", dest="d_from", default=None, help="開始日 YYYY-MM-DD")
    ap.add_argument("--to", dest="d_to", default=None, help="終了日 YYYY-MM-DD（含む）")
    ap.add_argument("--file", default=None, help="ダッシュボードから落とした JSONL を読む")
    ap.add_argument("--json", default=None, help="集計結果をこのパスに JSON でも書く")
    args = ap.parse_args()

    today = dt.date.today()
    d_to = dt.date.fromisoformat(args.d_to) if args.d_to else today
    d_from = dt.date.fromisoformat(args.d_from) if args.d_from else d_to - dt.timedelta(days=7)

    if args.file:
        rows = from_file(args.file)
    else:
        token = os.environ.get("CF_API_TOKEN")
        if token:
            print("認証: 環境変数 CF_API_TOKEN")
        else:
            token = wrangler_token()
            if token:
                print("認証: wrangler login のトークンを使います")
        if not token:
            print("Cloudflare の認証情報が見つかりません。", file=sys.stderr)
            print("", file=sys.stderr)
            print("いちばん簡単なのは wrangler にログインすることです:", file=sys.stderr)
            print("    cd mcp; npx wrangler login", file=sys.stderr)
            print("", file=sys.stderr)
            print("API Token を使う場合は CF_API_TOKEN に入れてください。", file=sys.stderr)
            print("  権限: Account → Account Analytics : Read", file=sys.stderr)
            print("        Account → Workers Observability : Read（Workers Logs を見るとき）", file=sys.stderr)
            print("", file=sys.stderr)
            print("ダッシュボードから JSONL を落とした場合は --file で渡せます。", file=sys.stderr)
            return 2
        print("ログの取り口を順に試します（%s 〜 %s）" % (d_from, d_to))
        # 細かいほうから順に試す。取れたところで止める
        rows = from_analytics_engine(token, d_from, d_to)
        if rows is None:
            rows = from_observability(token, d_from, d_to)
        if rows is None:
            # 最後の手。ツール別・クライアント別は出ないが総量は必ず取れる
            rows = from_graphql(token, d_from, d_to)
        if rows is None:
            print("どの取り口も使えませんでした。", file=sys.stderr)
            rows = []
        print("")

    report(rows, d_from, d_to)

    if args.json:
        agg = collections.defaultdict(int)
        for r in rows:
            agg["%s|%s|%s" % (r["date"], r["tool"], normalize_client(r["client"]))] += r["n"]
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"from": str(d_from), "to": str(d_to),
                       "total": sum(r["n"] for r in rows),
                       "counts": agg}, f, ensure_ascii=False, indent=1)
        print("→ %s に書きました" % args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
