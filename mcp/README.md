# ルールトレード MCP(無料版)

kaburadar.jp / ruletrade.jp が毎日公開している検証済みJSON(`docs/*.json`)を、
AIエージェント(Claude / Cursor / ChatGPT 等)から MCP で読めるようにする **変換層** です。
エンジンは持たず、GitHub raw の JSON を読んで整形するだけ。依存パッケージゼロ。

| ツール | 読むJSON | 内容 |
|---|---|---|
| `get_daily_signals` | free_scanner.json | BNF 25日線乖離ルールの該当/監視(1営業日遅れ・上位3件) |
| `get_market_regime` | radar.json / market_jiai.json / radar_history.json | 地合い BULLISH〜PANIC・HALT・VIX・日経・履歴 |
| `get_anomaly_summary` | gauge.json / crash.json | 『暴落は、減衰する』5つの前兆 + 着火メーター(26年検証) |
| `list_tools_guide` | (静的) | 使い方・更新時刻・遅延・免責・今後の予定 |

法務線は人向けサイトと同一:
- 推奨語(推奨/おすすめ/買うべき/儲かる 等 `src/legal.js` の `NG_WORDS`)は出力から伏せ字にする
- 全レスポンスに `disclaimer` キーを常設
- 価格・株数・利確/損切ライン・本番の調整済み閾値は出さない(free_scanner.json 生成時点で既に落ちている)

## エンドポイント

| パス | 内容 |
|---|---|
| `POST /mcp` | MCP Streamable HTTP(JSON-RPC・ステートレス) |
| `GET /health` | 4つのJSONが読めるか確認 |
| `GET /` | 概要 |

## デプロイ(どちらか一方でOK・スマホのブラウザから完結)

### A. Cloudflare Workers(推奨: ログ保持が長い・無料枠 10万req/日)
1. Cloudflare ダッシュボード → **Workers & Pages** → **Create** → **Import a repository**
2. リポジトリ `stock-trading-` を選ぶ → **Root directory** に `mcp` を入れる
3. Build command は空、Deploy command は `npx wrangler deploy`(既定のまま)
4. Deploy → `https://ruletrade-mcp.<account>.workers.dev/mcp` がエンドポイント
5. ログ: Worker → **Logs**(`wrangler.toml` の observability で有効化済み)。`event:"tool_call"` で絞ると呼び出し数が見える

### B. Vercel
1. vercel.com → **Add New Project** → リポジトリ `stock-trading-` を Import
2. **Root Directory** を `mcp` に変更、Framework Preset は **Other**
3. Deploy → `https://<project>.vercel.app/mcp` がエンドポイント
4. ログ: Project → **Logs**(Hobby は保持1時間なので、集計したい場合は下の `LOG_WEBHOOK` を使う)

### 動作確認(デプロイ後にブラウザで開くだけ)
- `https://<host>/health` → `"ok": true` なら完了

### 接続
- **Claude(claude.ai)**: 設定 → コネクタ → カスタムコネクタを追加 → URL に `https://<host>/mcp`
- **Claude Code**: `claude mcp add --transport http ruletrade https://<host>/mcp`
- 最初に `list_tools_guide` を呼ぶよう `initialize` の instructions で案内している

## 環境変数(すべて任意)

| 変数 | 用途 |
|---|---|
| `DATA_BASE` | JSONの取得元(既定: GitHub raw の `docs/`)。リポジトリ非公開化時に差し替え |
| `ANOMALY_URL` | ジンクス/アノマリー検証JSONを別途公開したら指定。`get_anomaly_summary` の `jinx_verification` にそのまま入る |
| `LOG_WEBHOOK` | 呼び出しログ(1行JSON)をPOSTする先。GAS/Slack/Sheets 等で集計したい場合 |
| `CACHE_TTL_MS` | インスタンス内キャッシュ(既定 60000) |

呼び出しログの形: `{"ts","event":"tool_call","tool","args","ok","ms","scrubbed","client"}`
(`initialize` 時は `clientInfo` も記録。有料化判断はこの数字を見てから)

## ローカル

```bash
cd mcp
npm test        # 実際の docs/*.json をフィクスチャに13テスト(推奨語ゼロ検査を含む)
node dev.js     # http://localhost:8787/mcp
curl -s localhost:8787/health
curl -s -X POST localhost:8787/mcp -H 'content-type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"get_market_regime","arguments":{"history_days":5}}}'
```

## 今後

- `run_rule_backtest`(有料)は、バックテスト基盤(backtest_grid)ができた時点で追加。エンジンは二重に作らない
- Week 1 は「読むだけ」。書き込み・発注・個別助言に相当する機能は入れない
