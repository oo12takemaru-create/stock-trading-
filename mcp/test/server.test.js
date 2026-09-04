// 実際の docs/*.json をフィクスチャにして、ネットワーク無しで全ツールを通す。
import { test, before } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { handle } from "../src/server.js";
import { clearCache } from "../src/data.js";
import { NG_WORDS, scrubText } from "../src/legal.js";

const DOCS = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../docs");

// fetch をリポジトリ内ファイル読みに差し替える
before(() => {
  clearCache();
  globalThis.fetch = async (url) => {
    const name = new URL(url).pathname.split("/").pop();
    try {
      const txt = await readFile(path.join(DOCS, name), "utf8");
      return new Response(txt, { status: 200, headers: { "content-type": "application/json" } });
    } catch {
      return new Response("not found", { status: 404 });
    }
  };
});

const rpc = async (method, params, id = 1) => {
  const res = await handle(
    new Request("https://x.test/mcp", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ jsonrpc: "2.0", id, method, params }),
    }),
    {},
  );
  return { status: res.status, body: res.status === 202 ? null : await res.json() };
};

const call = async (name, args = {}) => {
  const { body } = await rpc("tools/call", { name, arguments: args });
  assert.equal(body.error, undefined, JSON.stringify(body.error));
  return body.result;
};

const hasNg = (s) => NG_WORDS.some((w) => s.toLowerCase().includes(w.toLowerCase()));

test("initialize が対応バージョンを返す", async () => {
  const { body } = await rpc("initialize", { protocolVersion: "2025-03-26", capabilities: {}, clientInfo: { name: "t", version: "1" } });
  assert.equal(body.result.protocolVersion, "2025-03-26");
  assert.ok(body.result.capabilities.tools);
  const r2 = await rpc("initialize", { protocolVersion: "1999-01-01" });
  assert.equal(r2.body.result.protocolVersion, "2025-06-18");
});

test("notifications は 202", async () => {
  const res = await handle(
    new Request("https://x.test/mcp", { method: "POST", body: JSON.stringify({ jsonrpc: "2.0", method: "notifications/initialized" }) }),
    {},
  );
  assert.equal(res.status, 202);
});

test("tools/list に4本", async () => {
  const { body } = await rpc("tools/list");
  assert.deepEqual(
    body.result.tools.map((t) => t.name),
    ["get_daily_signals", "get_market_regime", "get_anomaly_summary", "list_tools_guide"],
  );
  for (const t of body.result.tools) assert.equal(t.inputSchema.type, "object");
});

test("get_daily_signals: free_scanner.json を変換し売買情報を含まない", async () => {
  const r = await call("get_daily_signals", { strategy: "bnf" });
  const p = r.structuredContent;
  assert.equal(r.isError, false);
  assert.equal(p.strategy, "bnf");
  assert.ok(p.disclaimer);
  assert.match(p.target_date, /^\d{4}-\d{2}-\d{2}$/);
  assert.ok(Array.isArray(p.items) && p.items.length <= 3);
  for (const it of p.items) {
    assert.ok(["rule_hit", "watch"].includes(it.status));
    assert.equal(typeof it.ma25_deviation_pct, "number");
    assert.equal(it.price, undefined);
    assert.equal(it.shares, undefined);
  }
  const onlyHit = await call("get_daily_signals", { include_watch: false });
  assert.ok(onlyHit.structuredContent.items.every((i) => i.status === "rule_hit"));
  assert.doesNotMatch(r.content[0].text, /[¥￥]\s*[\d,]+/); // 価格が漏れていない
});

test("get_market_regime: radar.json + 履歴", async () => {
  const p = (await call("get_market_regime", { history_days: 5 })).structuredContent;
  assert.ok(["BULLISH", "NEUTRAL", "BEARISH", "PANIC"].includes(p.regime));
  assert.equal(typeof p.is_halt, "boolean");
  assert.ok(p.disclaimer);
  assert.ok(Array.isArray(p.history) && p.history.length <= 5 && p.history.length > 0);
  assert.ok(p.history[0].date);
  const p0 = (await call("get_market_regime", {})).structuredContent;
  assert.equal(p0.history, undefined);
});

test("get_anomaly_summary: gauge.json + crash.json", async () => {
  const p = (await call("get_anomaly_summary", {})).structuredContent;
  assert.equal(p.precursor_gauges.items.length, 5);
  assert.equal(p.ignition_meter.flags.length, 7);
  assert.equal(typeof p.ignition_meter.historical_drop_rate_pct, "number");
  assert.equal(p.precursor_gauges.items[0].book, undefined);
  assert.equal(p.ignition_meter.stats, undefined);
  const d = (await call("get_anomaly_summary", { detail: true })).structuredContent;
  assert.ok(d.precursor_gauges.items[0].book);
  assert.ok(d.ignition_meter.stats.all);
  assert.ok(d.ignition_meter.history.length > 0);
});

test("list_tools_guide", async () => {
  const p = (await call("list_tools_guide")).structuredContent;
  assert.equal(p.tools.length, 4);
  assert.ok(p.legal.disclaimer);
  assert.ok(p.data_schedule_jst.get_daily_signals);
});

test("法務線: 全ツール出力に推奨語が含まれず、免責キーが常設", async () => {
  for (const [name, args] of [
    ["get_daily_signals", {}],
    ["get_market_regime", { history_days: 10 }],
    ["get_anomaly_summary", { detail: true }],
    ["list_tools_guide", {}],
  ]) {
    const r = await call(name, args);
    assert.equal(r.structuredContent.disclaimer.length > 20, true, name);
    assert.equal(hasNg(r.content[0].text), false, `${name} に推奨語が残っている`);
  }
});

test("scrub は外部データ由来の推奨語も伏せる", () => {
  const hit = { count: 0 };
  const s = scrubText("この銘柄は買い推奨、Strong Buy です", hit);
  assert.equal(hasNg(s), false);
  assert.ok(hit.count >= 1);
});

test("不明メソッド / 不明ツール / パースエラー", async () => {
  assert.equal((await rpc("nope")).body.error.code, -32601);
  const { body } = await rpc("tools/call", { name: "nope", arguments: {} });
  assert.equal(body.error.code, -32602);
  const res = await handle(new Request("https://x.test/mcp", { method: "POST", body: "{" }), {});
  assert.equal(res.status, 400);
});

test("データ取得失敗は isError=true で返す(サーバーは落ちない)", async () => {
  const saved = globalThis.fetch;
  clearCache();
  globalThis.fetch = async () => new Response("x", { status: 500 });
  const { body } = await rpc("tools/call", { name: "get_market_regime", arguments: {} });
  assert.equal(body.result.isError, true);
  assert.ok(body.result.structuredContent.disclaimer);
  globalThis.fetch = saved;
  clearCache();
});

test("GET / と /health と /mcp(405)", async () => {
  const root = await handle(new Request("https://x.test/"), {});
  assert.equal(root.status, 200);
  assert.equal((await root.json()).endpoint, "https://x.test/mcp");
  const h = await handle(new Request("https://x.test/health"), {});
  assert.equal(h.status, 200);
  assert.equal((await h.json()).ok, true);
  const m = await handle(new Request("https://x.test/mcp"), {});
  assert.equal(m.status, 405);
});

test("バッチ要求", async () => {
  const res = await handle(
    new Request("https://x.test/mcp", {
      method: "POST",
      body: JSON.stringify([
        { jsonrpc: "2.0", id: 1, method: "ping" },
        { jsonrpc: "2.0", id: 2, method: "tools/list" },
      ]),
    }),
    {},
  );
  const b = await res.json();
  assert.equal(b.length, 2);
});
