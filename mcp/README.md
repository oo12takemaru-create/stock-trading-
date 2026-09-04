# Rule Trade MCP — free tier / ルールトレード MCP(無料版)

**Endpoint: `https://ruletrade.jp/mcp`**
(`llms.txt`: `https://ruletrade.jp/mcp/llms.txt` · OpenAPI: `https://ruletrade.jp/mcp/openapi.json` · health: `https://ruletrade.jp/mcp/health`)

---

## English

A **verification layer** for Japanese equities, exposed over MCP.

Most Japanese-equity MCP servers cover the *primary data* layer — filings, prices, order execution.
This one covers what is missing: **what actually happened when published, mechanical rules were applied.**
It holds no engine of its own; it reads JSON that an existing pipeline publishes daily and reshapes it.
Zero dependencies, no API key, no registration.

### What you can get

| Tool | Returns |
|---|---|
| `get_daily_signals` | Stocks that matched a published mean-reversion rule (25-day moving-average deviation ≤ -15%), plus near-miss watch candidates. Delayed one trading day, top 3 by deviation. |
| `get_market_regime` | Machine-classified market regime (BULLISH / NEUTRAL / BEARISH / PANIC), circuit-breaker state, VIX, Nikkei 225, daily history. No stock names. |
| `get_anomaly_summary` | **50 Japanese market anomalies tested over 61 years** — verdict, win rate, mean return, sample size, p-value, control group. Filter by `name`. Plus five crash-precursor gauges and a 7-flag ignition meter verified over 26 years. |
| `list_tools_guide` | Update times (JST), delays, free-tier limits, disclaimer, roadmap. Call this first. |

### What you cannot get — by design

- Raw price series, OHLCV, or financial-statement values
- Position sizing, entry/exit prices, stop-loss or take-profit levels
- The production thresholds used by the operator's own system
- Any recommendation, rating, or buy/sell signal

Every response carries a `disclaimer` key. **Do not strip it when relaying results to a user.**
Output describes whether mechanical rules matched. It is not investment advice, not a solicitation,
and promises no future return.

### How to connect

```bash
# Claude Code
claude mcp add --transport http ruletrade https://ruletrade.jp/mcp
```

- **claude.ai**: Settings → Connectors → Add custom connector → `https://ruletrade.jp/mcp`
- **Any MCP client**: POST JSON-RPC 2.0 to `https://ruletrade.jp/mcp` (Streamable HTTP, stateless)

### Where the research comes from

The rules and verification data are published as books by the operator (Japanese, Kindle):
*Silent Investor BNF*, *Crashes Decay*, and *All 50 Japanese Market Anomalies Tested Over 61 Years*.
The anomaly dataset is a fixed snapshot taken at that book's publication (2026-08) and is not updated daily.

### Good to know

- Data updates on Japan Exchange trading days. Exact times are in `list_tools_guide`.
- Rate limits are not enforced today. Please be reasonable.
- Statistical values are rounded; p-values use 3 significant figures so very small values stay readable.

---

## 日本語

kaburadar.jp / ruletrade.jp が毎日公開している検証済みJSON(`docs/*.json`)を、
AIエージェント(Claude / Cursor / ChatGPT 等)から MCP で読めるようにする **変換層** です。
エンジンは持たず、GitHub raw の JSON を読んで整形するだけ。依存パッケージゼロ。

**正規URL は `https://ruletrade.jp/mcp`**。実体は Cloudflare Workers だが、
ruletrade.jp(Vercel)の `vercel.json` で rewrite しているため、配信先が変わっても利用者側の URL は変わらない。

| ツール | 読むJSON | 内容 |
|---|---|---|
| `get_daily_signals` | free_scanner.json | BNF 25日線乖離ルールの該当/監視(1営業日遅れ・上位3件) |
| `get_market_regime` | radar.json / market_jiai.json / radar_history.json | 地合い BULLISH〜PANIC・HALT・VIX・日経・履歴 |
| `get_anomaly_summary` | anomaly_results.json / gauge.json / crash.json | ジンクス50本の検証結果(61年・判定/勝率/p値。`name` で絞り込み)+ 『暴落は、減衰する』5つの前兆 + 着火メーター(26年検証) |
| `list_tools_guide` | (静的) | 使い方・更新時刻・遅延・免責・今後の予定 |

法務線は人向けサイトと同一:
- 推奨語(推奨/おすすめ/買うべき/儲かる 等 `src/legal.js` の `NG_WORDS`)は出力から伏せ字にする
- 全レスポンスに `disclaimer` キーを常設
- 価格・株数・利確/損切ライン・本番の調整済み閾値は出さない(free_scanner.json 生成時点で既に落ちている)
- ジンクス検証は判定結果と統計値(勝率・平均・標本数・p値)のみ。格言の本文(`saying`)は推奨語を含むため出力しない

## データの出どころ

`docs/*.json` は既存の生成側(Python)が毎日書き出しているもの。MCP は読むだけで、生成側は一切触らない。

`docs/anomaly_results.json` だけは日次ではなく **書籍刊行時点(2026-08)の固定データ**。
元は `株式投資開発/jinx_verification/results/results.json`(このリポジトリの外)で、
JSON 非対応の `NaN` を `null` に置換したコピー。更新するときは同じ手順でコピーし直す。

## エンドポイント

| パス | 内容 |
|---|---|
| `POST /mcp` | MCP Streamable HTTP(JSON-RPC・ステートレス) |
| `GET /health` | 5つのJSONが読めるか確認 |
| `GET /llms.txt` | エージェント向けの説明(英語主・日本語従) |
| `GET /openapi.json` | OpenAPI 3.1。JSON-RPC を1エンドポイントとして記述 |
| `GET /` | 概要 |

## デプロイ(どちらか一方でOK・スマホのブラウザから完結)

### A. Cloudflare Workers(推奨: ログ保持が長い・無料枠 10万req/日)
1. Cloudflare ダッシュボード → **Workers & Pages** → **Create** → **Import a repository**
2. リポジトリ `stock-trading-` を選ぶ → **Root directory** に `mcp` を入れる
3. Build command は空、Deploy command は `npx wrangler deploy`(既定のまま)
4. Deploy → Worker の実体は `https://ruletrade-mcp.<account>.workers.dev/mcp`。
   **利用者に案内するのは正規URL `https://ruletrade.jp/mcp`**(ruletrade.jp の `site/vercel.json` が rewrite する)。
   Worker の URL が変わったときは `site/vercel.json` の2行と `src/tools.js` の `CANONICAL_*` を直す
5. ログ: Worker → **Logs**(`wrangler.toml` の observability で有効化済み)。`event:"tool_call"` で絞ると呼び出し数が見える

### B. Vercel
1. vercel.com → **Add New Project** → リポジトリ `stock-trading-` を Import
2. **Root Directory** を `mcp` に変更、Framework Preset は **Other**
3. Deploy → `https://<project>.vercel.app/mcp` がエンドポイント
4. ログ: Project → **Logs**(Hobby は保持1時間なので、集計したい場合は下の `LOG_WEBHOOK` を使う)

### 動作確認(デプロイ後にブラウザで開くだけ)
- `https://ruletrade.jp/mcp/health` → `"ok": true` なら完了
- Worker 直の `https://ruletrade-mcp.<account>.workers.dev/health` でも同じものが見える(切り分け用)

### 接続(正規URLを使う。`*.workers.dev` は案内しない)
- **Claude(claude.ai)**: 設定 → コネクタ → カスタムコネクタを追加 → URL に `https://ruletrade.jp/mcp`
- **Claude Code**: `claude mcp add --transport http ruletrade https://ruletrade.jp/mcp`
- 最初に `list_tools_guide` を呼ぶよう `initialize` の instructions で案内している

## 環境変数(すべて任意)

| 変数 | 用途 |
|---|---|
| `DATA_BASE` | JSONの取得元(既定: GitHub raw の `docs/`)。リポジトリ非公開化時に差し替え |
| `ANOMALY_URL` | 検証JSONを別配信先に置く場合に指定。`get_anomaly_summary` の `jinx_verification_external` にそのまま入る(通常は不要。既定は `docs/anomaly_results.json`) |
| `LOG_WEBHOOK` | 呼び出しログ(1行JSON)をPOSTする先。GAS/Slack/Sheets 等で集計したい場合 |
| `CACHE_TTL_MS` | インスタンス内キャッシュ(既定 60000) |

呼び出しログの形: `{"ts","event":"tool_call","tool","args","ok","ms","scrubbed","client"}`
(`initialize` 時は `clientInfo` も記録。有料化判断はこの数字を見てから)

## ローカル

```bash
cd mcp
npm test        # 実際の docs/*.json をフィクスチャに18テスト(推奨語ゼロ検査を含む)
node dev.js     # http://localhost:8787/mcp
curl -s localhost:8787/health
curl -s -X POST localhost:8787/mcp -H 'content-type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"get_market_regime","arguments":{"history_days":5}}}'
```

## 今後

- `run_rule_backtest`(有料)は、バックテスト基盤(backtest_grid)ができた時点で追加。エンジンは二重に作らない
- Week 1 は「読むだけ」。書き込み・発注・個別助言に相当する機能は入れない
