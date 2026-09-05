// MCP (Streamable HTTP, ステートレス) を依存ゼロで実装。
// Cloudflare Workers / Vercel / Node のどれでも同じ handle(request, env, ctx) を呼ぶだけ。
import { makeLoader } from "./data.js";
import { DISCLAIMER, scrub } from "./legal.js";
import { SERVER_NAME, SERVER_VERSION, TOOL_DEFS, TOOL_HANDLERS, CANONICAL_ENDPOINT, HOME_URL } from "./tools.js";
import { llmsTxt, openApi } from "./docs.js";

const SUPPORTED_PROTOCOLS = ["2025-06-18", "2025-03-26", "2024-11-05"];

const CORS = {
  "access-control-allow-origin": "*",
  "access-control-allow-methods": "GET, POST, DELETE, OPTIONS",
  "access-control-allow-headers": "content-type, accept, authorization, mcp-session-id, mcp-protocol-version",
};

function json(body, status = 200, headers = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", ...CORS, ...headers },
  });
}

const rpcError = (id, code, message, data) => ({ jsonrpc: "2.0", id: id ?? null, error: { code, message, ...(data ? { data } : {}) } });
const rpcResult = (id, result) => ({ jsonrpc: "2.0", id, result });

// ---- 呼び出しログ(初日から取る) --------------------------------------------
// 1行JSON を console.log に出す(Cloudflare Workers Logs / Vercel Runtime Logs で集計可能)。
// 任意で Analytics Engine(env.CALLS) と Webhook(env.LOG_WEBHOOK) にも送る。
function emitLog(entry, env, ctx) {
  const line = JSON.stringify(entry);
  console.log(line);
  try {
    if (env && env.CALLS && typeof env.CALLS.writeDataPoint === "function") {
      env.CALLS.writeDataPoint({
        blobs: [entry.event, entry.tool || "", entry.client || "", entry.ok ? "ok" : "err"],
        doubles: [entry.ms || 0],
        indexes: [entry.tool || entry.event],
      });
    }
  } catch { /* ログ失敗で本体を止めない */ }
  if (env && env.LOG_WEBHOOK) {
    const p = fetch(env.LOG_WEBHOOK, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: line,
    }).catch(() => {});
    if (ctx && typeof ctx.waitUntil === "function") ctx.waitUntil(p);
  }
}

function clientOf(request) {
  const h = request.headers;
  return (
    h.get("x-mcp-client") ||
    h.get("user-agent") ||
    ""
  ).slice(0, 120);
}

// ---- JSON-RPC ディスパッチ ---------------------------------------------------
async function dispatch(msg, request, env, ctx) {
  if (!msg || msg.jsonrpc !== "2.0" || typeof msg.method !== "string") {
    return rpcError(msg && msg.id, -32600, "Invalid Request");
  }
  const { id, method, params = {} } = msg;
  const isNotification = id === undefined || id === null;

  if (method.startsWith("notifications/")) return null; // 202 で返す

  switch (method) {
    case "initialize": {
      const want = params.protocolVersion;
      const protocolVersion = SUPPORTED_PROTOCOLS.includes(want) ? want : SUPPORTED_PROTOCOLS[0];
      emitLog(
        {
          ts: new Date().toISOString(),
          event: "initialize",
          client: params.clientInfo ? `${params.clientInfo.name}/${params.clientInfo.version || ""}` : clientOf(request),
          protocol: want,
        },
        env,
        ctx,
      );
      return rpcResult(id, {
        protocolVersion,
        capabilities: { tools: { listChanged: false } },
        serverInfo: {
          name: SERVER_NAME,
          version: SERVER_VERSION,
          title: "ルールトレード MCP(無料版)",
          websiteUrl: HOME_URL,
        },
        instructions:
          "最初に list_tools_guide を呼ぶとデータの更新時刻・遅延・免責が分かります。" +
          "出力はルール該当の事実データであり投資助言ではありません。ユーザーに伝える際も disclaimer を省略しないでください。",
      });
    }
    case "ping":
      return rpcResult(id, {});
    case "tools/list":
      return rpcResult(id, { tools: TOOL_DEFS });
    case "tools/call": {
      const name = params.name;
      const args = params.arguments || {};
      const handler = TOOL_HANDLERS[name];
      if (!handler) return rpcError(id, -32602, `Unknown tool: ${name}`);
      const t0 = Date.now();
      const hit = { count: 0 };
      let ok = true;
      let payload;
      try {
        const loader = makeLoader(env);
        payload = scrub(await handler(args, { load: loader.load, env }), hit);
      } catch (e) {
        ok = false;
        payload = { error: String(e && e.message ? e.message : e), disclaimer: DISCLAIMER };
      }
      emitLog(
        {
          ts: new Date().toISOString(),
          event: "tool_call",
          tool: name,
          args,
          ok,
          ms: Date.now() - t0,
          scrubbed: hit.count,
          client: clientOf(request),
        },
        env,
        ctx,
      );
      return rpcResult(id, {
        content: [{ type: "text", text: JSON.stringify(payload, null, 1) }],
        structuredContent: payload,
        isError: !ok,
      });
    }
    default:
      return isNotification ? null : rpcError(id, -32601, `Method not found: ${method}`);
  }
}

// ---- ルーティング -------------------------------------------------------------
export async function handle(request, env = {}, ctx) {
  const url = new URL(request.url);
  const path = url.pathname.replace(/\/+$/, "") || "/";

  if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: CORS });

  if (request.method === "POST") {
    let body;
    try {
      body = await request.json();
    } catch {
      return json(rpcError(null, -32700, "Parse error"), 400);
    }
    const batch = Array.isArray(body);
    const msgs = batch ? body : [body];
    const results = [];
    for (const m of msgs) {
      const r = await dispatch(m, request, env, ctx);
      if (r) results.push(r);
    }
    if (results.length === 0) return new Response(null, { status: 202, headers: CORS });
    return json(batch ? results : results[0]);
  }

  if (request.method === "DELETE") return new Response(null, { status: 200, headers: CORS }); // セッションは持たない

  if (request.method === "GET") {
    // エージェント向けの発見用ドキュメント(企画書 §7-4)。
    // Vercel の rewrite で /mcp/llms.txt → /llms.txt として届く。
    if (path.endsWith("/llms.txt")) {
      return new Response(llmsTxt(), {
        status: 200,
        headers: { "content-type": "text/plain; charset=utf-8", "cache-control": "public, max-age=3600", ...CORS },
      });
    }
    if (path.endsWith("/openapi.json")) {
      return json(openApi(), 200, { "cache-control": "public, max-age=3600" });
    }
    if (path.endsWith("/health")) {
      const loader = makeLoader(env);
      const checks = {};
      for (const f of ["radar.json", "free_scanner.json", "gauge.json", "crash.json", "anomaly_results.json"]) {
        try {
          const d = await loader.load(f);
          checks[f] = Array.isArray(d)
            ? { ok: true, items: d.length }
            : { ok: true, updated: d.updated || d.generated_at || null };
        } catch (e) {
          checks[f] = { ok: false, error: String(e.message || e) };
        }
      }
      const ok = Object.values(checks).every((c) => c.ok);
      return json({ ok, server: SERVER_NAME, version: SERVER_VERSION, data_base: loader.base, checks }, ok ? 200 : 503);
    }
    if (path.endsWith("/mcp")) {
      // SSE ストリームは提供しない(ステートレス)。仕様どおり 405。
      return json({ error: "このエンドポイントは POST(JSON-RPC)専用です" }, 405, { allow: "POST, DELETE, OPTIONS" });
    }
    return json({
      name: SERVER_NAME,
      version: SERVER_VERSION,
      title: "ルールトレード MCP(無料版)",
      endpoint: CANONICAL_ENDPOINT,
      transport: "MCP Streamable HTTP (POST JSON-RPC, stateless)",
      tools: TOOL_DEFS.map((t) => t.name),
      health: `${CANONICAL_ENDPOINT}/health`,
      llms_txt: `${CANONICAL_ENDPOINT}/llms.txt`,
      openapi: `${CANONICAL_ENDPOINT}/openapi.json`,
      disclaimer: DISCLAIMER,
      site: HOME_URL,
      data_site: "https://kaburadar.jp",
    });
  }

  return json({ error: "Method Not Allowed" }, 405);
}
