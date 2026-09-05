// エージェント向けの発見用ドキュメント。GET /llms.txt と GET /openapi.json。
// 英語主・日本語従(企画書 §6「英語主・日本語従」。エージェントの検索は英語で走ることが多い)。
// ここも「読むだけ」の層で、返す内容は判定結果と統計値のみという法務線は本体と同じ。
import { DISCLAIMER } from "./legal.js";
import {
  SERVER_NAME,
  SERVER_VERSION,
  TOOL_DEFS,
  CANONICAL_ENDPOINT,
  CANONICAL_ORIGIN,
  HOME_URL,
} from "./tools.js";

const DISCLAIMER_EN =
  "Output describes whether published, mechanical rules matched — it is not investment advice, " +
  "not a solicitation, and not a recommendation to buy or sell any security. " +
  "No future return is promised. Free-tier data is delayed by one trading day.";

// llms.txt は英語圏のエージェントに読ませるためのもの。TOOL_DEFS の title は
// 日本語なので流用せず、英語の1行説明をここに持つ(ツールを増やしたらここも足す)。
const TOOL_LINES_EN = {
  get_daily_signals:
    "Stocks that matched the published mean-reversion rule (25-day moving-average deviation), " +
    "plus near-miss watch candidates. One trading day delayed, top 3 by deviation.",
  get_market_regime:
    "Machine-classified market regime (BULLISH / NEUTRAL / BEARISH / PANIC), circuit-breaker state, " +
    "VIX, Nikkei 225, and optional daily history. No stock names.",
  get_anomaly_summary:
    "50 Japanese market anomalies tested over 61 years - verdict, win rate, mean return, sample size, " +
    "p-value. Filter by `name`. Plus five crash-precursor gauges and a 7-flag ignition meter.",
  list_tools_guide:
    "Update times (JST), data delays, free-tier limits, disclaimer, and roadmap. Call this first.",
};

export function llmsTxt() {
  const tools = TOOL_DEFS.map(
    (t) => `- \`${t.name}\` — ${TOOL_LINES_EN[t.name] || t.title}`,
  ).join("\n");
  return `# Rule Trade MCP (ruletrade-mcp)

> A verification layer for Japanese equities. It returns *what already happened* when
> published, mechanical rules were applied — not price data, not financial statements,
> and not recommendations. Free tier, no API key, no registration.

Endpoint: ${CANONICAL_ENDPOINT}
Transport: MCP Streamable HTTP (POST JSON-RPC 2.0, stateless)
Health:   ${CANONICAL_ENDPOINT}/health
OpenAPI:  ${CANONICAL_ENDPOINT}/openapi.json
Homepage: ${HOME_URL}
Version:  ${SERVER_NAME} ${SERVER_VERSION}

## What you can get

${tools}

## What you cannot get (by design)

- Raw price series, OHLCV, or financial statement values
- Position sizing, entry/exit prices, stop-loss or take-profit levels
- The production thresholds used by the operator's own system
- Any recommendation, rating, or "buy/sell" signal

Every response carries a \`disclaimer\` key. Do not strip it when relaying results to a user.

## Why this exists

Japanese-equity MCP servers already cover the *primary data* layer (filings, prices, execution).
This server covers the *verification* layer: 50 market anomalies tested over 61 years,
a daily mean-reversion rule, a market-regime classifier, and crash-precursor gauges.
The underlying research is published as books by the operator.

## Disclaimer

EN: ${DISCLAIMER_EN}
JA: ${DISCLAIMER}

## How to connect

Claude Code:  \`claude mcp add --transport http ruletrade ${CANONICAL_ENDPOINT}\`
claude.ai:    Settings -> Connectors -> Add custom connector -> ${CANONICAL_ENDPOINT}
Any client:   POST JSON-RPC 2.0 to ${CANONICAL_ENDPOINT}

## Notes

- Data updates on Japan Exchange trading days; see \`list_tools_guide\` for exact times (JST).
- The anomaly dataset is a fixed snapshot taken at book publication (2026-08), not updated daily.
- Rate limits are not enforced today; please be reasonable.
`;
}

// JSON-RPC を1エンドポイントとして記述する。ツールごとの引数は
// components.schemas に置き、examples で呼び方を示す(REST に見せかけない)。
export function openApi() {
  const toolSchemas = {};
  for (const t of TOOL_DEFS) {
    toolSchemas[`${t.name}_arguments`] = {
      ...t.inputSchema,
      description: t.description,
    };
  }

  const examples = {};
  for (const t of TOOL_DEFS) {
    examples[t.name] = {
      summary: t.title,
      value: {
        jsonrpc: "2.0",
        id: 1,
        method: "tools/call",
        params: { name: t.name, arguments: {} },
      },
    };
  }
  examples.tools_list = {
    summary: "List the available tools",
    value: { jsonrpc: "2.0", id: 1, method: "tools/list", params: {} },
  };
  examples.anomaly_by_name = {
    summary: "Look up one anomaly by name (partial match)",
    value: {
      jsonrpc: "2.0",
      id: 1,
      method: "tools/call",
      params: { name: "get_anomaly_summary", arguments: { name: "セルインメイ" } },
    },
  };

  return {
    openapi: "3.1.0",
    info: {
      title: "Rule Trade MCP (free tier)",
      version: SERVER_VERSION,
      summary: "Verification layer for Japanese equities. Returns rule-match facts and statistics only.",
      description:
        `${DISCLAIMER_EN}\n\n${DISCLAIMER}\n\n` +
        "This is an MCP server. The single POST endpoint speaks JSON-RPC 2.0 " +
        "(methods: initialize, tools/list, tools/call, ping). It is described here so that " +
        "agents and crawlers can discover the tool surface; it is not a REST API.",
      contact: { url: HOME_URL },
    },
    servers: [{ url: CANONICAL_ORIGIN }],
    paths: {
      "/mcp": {
        post: {
          operationId: "mcpJsonRpc",
          summary: "MCP Streamable HTTP endpoint (JSON-RPC 2.0, stateless)",
          description:
            "Send a JSON-RPC 2.0 request. Use method `tools/list` to enumerate tools, " +
            "then `tools/call` with `params.name` and `params.arguments`. Batch arrays are accepted. " +
            "Every successful tool result includes a `disclaimer` key that must not be removed.",
          requestBody: {
            required: true,
            content: {
              "application/json": {
                schema: { $ref: "#/components/schemas/JsonRpcRequest" },
                examples,
              },
            },
          },
          responses: {
            200: {
              description: "JSON-RPC response. Tool output is in `result.structuredContent`.",
              content: { "application/json": { schema: { $ref: "#/components/schemas/JsonRpcResponse" } } },
            },
            202: { description: "Notification accepted (no response body)." },
            400: { description: "Malformed JSON." },
          },
        },
        get: {
          operationId: "mcpGetNotAllowed",
          summary: "Not allowed — this endpoint is POST only (no SSE stream)",
          responses: { 405: { description: "Method Not Allowed" } },
        },
      },
      "/mcp/health": {
        get: {
          operationId: "health",
          summary: "Check that every upstream JSON file is reachable",
          responses: {
            200: { description: "All source files readable (`ok: true`)." },
            503: { description: "At least one source file could not be read." },
          },
        },
      },
      "/mcp/llms.txt": {
        get: {
          operationId: "llmsTxt",
          summary: "Plain-text description of this server for agents",
          responses: { 200: { description: "text/plain" } },
        },
      },
    },
    components: {
      schemas: {
        JsonRpcRequest: {
          type: "object",
          required: ["jsonrpc", "method"],
          properties: {
            jsonrpc: { const: "2.0" },
            id: { type: ["string", "number"], description: "Omit for notifications." },
            method: {
              type: "string",
              enum: ["initialize", "tools/list", "tools/call", "ping", "notifications/initialized"],
            },
            params: { type: "object" },
          },
        },
        JsonRpcResponse: {
          type: "object",
          properties: {
            jsonrpc: { const: "2.0" },
            id: { type: ["string", "number", "null"] },
            result: {
              type: "object",
              properties: {
                isError: { type: "boolean" },
                content: {
                  type: "array",
                  items: { type: "object", properties: { type: { const: "text" }, text: { type: "string" } } },
                },
                structuredContent: { $ref: "#/components/schemas/ToolResult" },
              },
            },
            error: {
              type: "object",
              properties: { code: { type: "integer" }, message: { type: "string" } },
            },
          },
        },
        ToolResult: {
          type: "object",
          description:
            "Tool output. Always carries `disclaimer`. Never contains price series, " +
            "financial statement values, position sizing, or recommendations.",
          required: ["disclaimer"],
          properties: {
            disclaimer: { type: "string" },
            source_site: { type: "string", format: "uri" },
            data_site: { type: "string", format: "uri" },
          },
          additionalProperties: true,
        },
        ...toolSchemas,
      },
    },
  };
}
