# 引き継ぎ資料 — ルールトレードMCP 無料版(Week 1)

作成: 2026-09-04(スマホからのリモートセッション)
ブランチ: `claude/rule-trade-mcp-free-launch-136is2`(push済み・main 未マージ)
次の作業者: 家のパソコンの Claude Code(このファイルを読ませて再開する)

---

## 1. 目的と方針(決定済み・再検討不要)

- 今週中に **無料の MCP ツール4本を公開**する。Claude主導、本人作業はデプロイ確認のみ。
- **既存の公開JSON(`docs/*.json`)を読むだけの変換層**。エンジンは持たない。二重実装しない。
- **呼び出しログを初日から取る**。需要を数字で見てから有料化(ビルダーと同じ型)。
- 有料側 `run_rule_backtest` は、ビルダー Phase 2 の `backtest_grid` ができた時点で追加。
- **法務線は人向けサイト(kaburadar.jp / ruletrade.jp)と同一**: 推奨語禁止・免責キー常設。

## 2. いまの状態

| 項目 | 状態 |
|---|---|
| コード | `mcp/` 配下に完成。依存パッケージゼロ(Node 20+ の標準機能のみ) |
| テスト | `cd mcp && npm test` → 13本すべて合格(実際の docs/*.json をフィクスチャに使用) |
| 本番データでの動作 | ローカル dev サーバーから GitHub raw の JSON を読んで4本とも正常応答を確認済み |
| CI | `.github/workflows/mcp-test.yml` を追加(mcp/ 変更時に自動テスト) |
| デプロイ | **未実施**(本人作業) |
| main マージ | **未実施** |

## 3. ファイル構成

```
mcp/
├── src/
│   ├── server.js      MCP Streamable HTTP(JSON-RPC・ステートレス)の実装、ルーティング、呼び出しログ
│   ├── tools.js       ツール4本の定義(tools/list 用スキーマ)と実装
│   ├── data.js        GitHub raw から JSON を取得(60秒キャッシュ)。DATA_BASE で差し替え可
│   └── legal.js       免責文 DISCLAIMER・推奨語リスト NG_WORDS・伏せ字化 scrub()
├── worker.js          Cloudflare Workers エントリ
├── api/[[...path]].js Vercel Functions エントリ(全パスを rewrite で集約)
├── vercel.json        Vercel 用 rewrite
├── wrangler.toml      Cloudflare 用(observability=有効。Analytics Engine はコメントアウト)
├── dev.js             ローカル確認用 http サーバー(node dev.js → :8787)
├── test/server.test.js 13テスト
├── package.json
├── README.md          デプロイ手順・接続方法・環境変数
└── HANDOFF.md         このファイル
```

ルート `.gitignore` は `*.json` を無視する設定なので、`!mcp/package.json` `!mcp/vercel.json` の例外を追加済み。
**mcp/ に新しい JSON を足すときは .gitignore に例外を追加すること。**

## 4. ツール4本と読んでいるJSON

| ツール | 読むJSON | 引数 | 備考 |
|---|---|---|---|
| `get_daily_signals` | `docs/free_scanner.json` | `strategy`(bnfのみ), `include_watch` | judge=signal→`rule_hit`、watch→`watch` に言い換え。価格・株数は元JSONに無い |
| `get_market_regime` | `docs/radar.json`, `docs/market_jiai.json`, `docs/radar_history.json` | `history_days`(0〜60) | 銘柄名なし・件数のみ |
| `get_anomaly_summary` | `docs/gauge.json`, `docs/crash.json` | `detail` | 『暴落は、減衰する』5前兆 + 着火メーター7フラグ |
| `list_tools_guide` | なし(静的) | なし | 更新時刻・遅延・制限・免責・ロードマップ |

エンドポイント: `POST /mcp`(MCP本体)、`GET /health`(4JSONの疎通)、`GET /`(概要)。`GET /mcp` は仕様どおり 405。

## 5. 法務線の実装

- `legal.js` の `NG_WORDS`(推奨・おすすめ・買うべき・儲かる・strong buy 等)を、**外部JSON由来の文言も含めて**再帰的に伏せ字 `［表現調整］` に置換。置換件数はログの `scrubbed` に出る。
- 全レスポンスの先頭に `disclaimer` キー。エラー時も付く。
- `initialize` の `instructions` で「ユーザーに伝える際も disclaimer を省略しない」とクライアントAIに指示。
- テスト「法務線」で4本全出力に NG語ゼロ・免責あり を機械検査している。**NG語を増やしたら `npm test` で既存の出力(gauge.json の書籍解説文など)が引っかからないか確認する。**

## 6. 呼び出しログ

- 形式(1行JSON, console.log): `{"ts","event":"tool_call","tool","args","ok","ms","scrubbed","client"}`。`initialize` 時は `clientInfo` を記録。
- Cloudflare: Workers Logs(wrangler.toml で有効化済み)。集計したければ `[[analytics_engine_datasets]]` のコメントを外し binding `CALLS` を使う(コードは対応済み)。
- Vercel: Runtime Logs(Hobby は保持1時間)。長期集計は環境変数 `LOG_WEBHOOK` に GAS/Slack 等の URL を入れる(ログを POST する)。

## 7. 未決事項(家で確認が必要)

1. **「ジンクス本の検証結果」の所在。** リポジトリ内に該当JSONは無い。現状 `get_anomaly_summary` は gauge.json + crash.json で代替している。
   - 検証JSONが見つかったら: (a) `docs/` に置いて `.gitignore` に例外を追加し tools.js で読む、または (b) 別URLで公開して環境変数 `ANOMALY_URL` を設定(そのまま `jinx_verification` キーに同梱される)。
2. **デプロイ先の選択。** 推奨は Cloudflare(ログ保持・無料枠)。手順は README.md の「デプロイ」。
3. **リポジトリを非公開化する予定の有無。** 非公開化すると raw URL が読めなくなるので、その時は `DATA_BASE` を別配信先(Pages 等)に向ける。

## 8. 家での再開手順

```bash
git fetch origin claude/rule-trade-mcp-free-launch-136is2
git checkout claude/rule-trade-mcp-free-launch-136is2
cd mcp && npm test            # 13 pass を確認
node dev.js                   # 別ターミナルで
curl -s localhost:8787/health
curl -s -X POST localhost:8787/mcp -H 'content-type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"list_tools_guide","arguments":{}}}'
```

Claude Code に渡す一言:
> `mcp/HANDOFF.md` を読んで、未決事項の1(ジンクス検証JSON)から進めて。場所は ○○ にある。

## 9. 公開までの残タスク(順番どおり)

- [ ] ジンクス検証JSONを特定して `get_anomaly_summary` を正式対応(または ANOMALY_URL で差し込み)
- [ ] Cloudflare or Vercel にデプロイ(Root directory = `mcp`)→ `/health` が `"ok": true`
- [ ] Claude Code から接続して4本の出力文言を目視: `claude mcp add --transport http ruletrade https://<host>/mcp`
- [ ] claude.ai のカスタムコネクタでも接続確認(設定 → コネクタ → URL に `/mcp`)
- [ ] main へマージ(PR)
- [ ] 人向けサイトに MCP の案内を1行追加(URL と「投資助言ではない」旨)
- [ ] 1週間後に呼び出しログを集計(ツール別回数・クライアント別)→ 有料化判断の材料

## 10. 触らないもの

- `daily_scanner_*.py`, `scanner_free.py`, `make_radar_json.py` などの既存生成側。MCP は読むだけ。
- 既存ワークフロー(`.github/workflows/` の mcp-test.yml 以外)。
- `docs/index.html`(人向けサイト)。
