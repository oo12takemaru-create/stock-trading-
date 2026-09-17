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
  "No future return is promised. Daily data is delayed by one trading day; " +
  "the book-derived studies are fixed snapshots taken at publication.";

// llms.txt は英語圏のエージェントに読ませるためのもの。TOOL_DEFS の title は
// 日本語なので流用せず、英語の1行説明をここに持つ(ツールを増やしたらここも足す)。
const TOOL_LINES_EN = {
  get_daily_signals:
    "The single stock that matched the published mean-reversion rule (25-day moving-average deviation of " +
    "-15% or lower), one trading day delayed. Counts are returned; the full list of matches is not distributed.",
  get_market_regime:
    "Machine-classified market regime (BULLISH / NEUTRAL / BEARISH / PANIC), circuit-breaker state, VIX, " +
    "Nikkei 225, and a three-axis score (trend, short-term risk, supply and demand) with the reasoning " +
    "behind each axis. Optional daily history. No stock names.",
  get_anomaly_summary:
    "50 Japanese market anomalies tested over 61 years - verdict, win rate, mean return, sample size, " +
    "p-value. Filter by `name`. Plus five crash-precursor gauges and a 7-flag ignition meter.",
  get_candlestick_verdict:
    "All 12 classic Japanese candlestick patterns (three soldiers, head-and-shoulders, three gaps and the rest) "
    + "tested on 1,550 TSE Prime stocks over 10.5 years. None of them cleared the adoption bar - "
    + "the reason each one failed is the part worth reading.",
  get_event_reaction:
    "What followed an event, from three separate studies: 27 event types (earthquakes, rate decisions, tariffs, "
    + "pandemics), post-earnings drift across ~26,000 announcements, and index additions and deletions. All three "
    + "point the same way - the move happens before the event clears, not after. Sector baskets and per-stock rows "
    + "are deliberately excluded.",
  get_indicator_verdict:
    "26 technical indicators (RSI, MACD, moving-average crossover, Bollinger and the rest) traded exactly as the "
    + "textbooks describe, across TOPIX500 over 10 years and roughly 500,000 trades. Nine cleared every regime; "
    + "thirteen did not, and those thirteen are mostly the ones beginners learn first.",
  get_etf_decay:
    "Twelve years of measured decay in leveraged and inverse ETFs on the Nikkei 225 - yearly theory against actual, "
    + "win rate by holding period, profit factor by market condition, and what happened across 305 attempts to "
    + "hold and wait for a position to come back.",
  list_tools_guide:
    "Update times (JST), data delays, what is deliberately not returned, disclaimer, and roadmap. Call this first.",
};

export function llmsTxt() {
  const tools = TOOL_DEFS.map(
    (t) => `- \`${t.name}\` — ${TOOL_LINES_EN[t.name] || t.title}`,
  ).join("\n");
  return `# Rule Trade MCP (ruletrade-mcp)

> A verification layer for Japanese equities. It returns *what already happened* when
> published, mechanical rules were applied — not price data, not financial statements,
> and not recommendations. No API key, no registration, no charge.

Endpoint: ${CANONICAL_ENDPOINT}
Transport: MCP Streamable HTTP (POST JSON-RPC 2.0, stateless)
Health:   ${CANONICAL_ENDPOINT}/health
OpenAPI:  ${CANONICAL_ENDPOINT}/openapi.json
Homepage: ${HOME_URL}
Version:  ${SERVER_NAME} ${SERVER_VERSION}

## What you can get

${tools}

## What you cannot get (by design)

- The full list of stocks matching a rule on a given day (one stock is returned; counts are returned)
- Raw price series, OHLCV, or financial statement values
- Position sizing, entry/exit prices, stop-loss or take-profit levels
- The production thresholds used by the operator's own system
- Any recommendation, rating, or "buy/sell" signal

Every response carries a \`disclaimer\` key. Do not strip it when relaying results to a user.

## Why this exists

Japanese-equity MCP servers already cover the *primary data* layer (filings, prices, execution).
This server covers the *verification* layer: 50 market anomalies tested over 61 years,
26 technical indicators traded by the book, 12 candlestick patterns tested on TSE Prime,
27 event types and what followed them, post-earnings drift, index rebalancing,
twelve years of leveraged-ETF decay,
a daily mean-reversion rule, a market-regime classifier, and crash-precursor gauges.
The underlying research is published as books by the operator.

It also returns what did **not** work. All 12 candlestick patterns were rejected, including
the one with the highest profit factor (3.04) - it flipped sign between the first half of the
sample and the second, which is what an artifact of hindsight looks like. Results that failed
are kept and returned with the same weight as results that passed.

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
- The candlestick, event, indicator and ETF datasets are fixed snapshots from the same research, not updated daily.
- Results that failed are returned with their caveats attached. Read them: the ETF figures come from
  twelve years in which the Nikkei roughly quadrupled, and that context changes what they mean.
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
      title: "Rule Trade MCP",
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
