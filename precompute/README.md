# precompute ― ルールビルダーの前計算バッチ

ルールトレード（ruletrade.jp）会員アプリの「ルールビルダー」が使う前計算テーブルを作る。
既存のスキャナー・バックテストエンジンには**一切手を入れない**。読むだけ。

- 企画の正本: `集客サイト企画/08_企画書v2_ルールビルダー型.md`
- 画面・仕様の正本: `集客サイト企画/10_画面設計v2/ruletrade_handoff_v2.md`
- この工程の指示書: `集客サイト企画/起動文_実務_ビルダーPhase2-1_前計算バッチと統計API_2026-09-05.md`

---

## 何を作るか

| テーブル | 中身 | 行数の目安 |
|---|---|---|
| `daily_metrics` | 銘柄×日付の前計算列（乖離率・出来高倍率・N日高値比・移動平均の並び・真偽値の判定 …） | 約 81.0 万行 |
| `market_condition` | 日付ごとの相場環境（BULLISH / NEUTRAL / BEARISH / PANIC）と根拠指標 | 約 2,462 行 |
| `sectors` | 銘柄マスタ（340銘柄） | 340 行 |
| `sector_thresholds` / `regime_multipliers` | BNF のセクター別閾値と相場環境の倍率（**会員には見せない**） | 89 / 4 行 |
| `portfolio_results` | 規定値ポートフォリオの成績（＝サイトの公開数字） | 1 行 |

実測 **260.6MB ＝ 無料枠500MBの52.1%**（相談ライン70%）。投入先は Supabase Postgres（会員基盤と同じプロジェクト）。
テーブル定義は `ruletrade-app/supabase/schema.sql` にある。

**列は25列に絞ってある**（2026-09-05 Fable判断・案③）。他の列から復元できる中間値
（`vol_avg_*`）と、比率列に置き換わった実値（`ma_*` / `high_20` / `bb_lower_*`）は
DBに入れない。ローカルの CSV/Parquet には従来どおり全39列を出す
（`engine_rules.py` の照合に要るため）。境目は `metrics.DB_COLUMNS`。

**ユニバースは 340 銘柄**（`daily_scanner_v2_8_0.py` の `STOCKS`。`universe_version = jp340_v2_8_0`）。
ソース上のキー出現は341個だが `4592.T` はコメントアウト済み。さらに `9719.T` / `4974.T` /
`6201.T` は yfinance にデータが無いので、実際に計算に使えるのは **337銘柄**。
`integrated_backtest_v2_8_0.py` の `JAPAN_STOCKS`（238銘柄）はその完全な部分集合。

なぜ 341 か（`引継ぎ.md` §16-4 判断2・2026-09-05 Fable）:
ビルダーは「過去データで検証して毎日動かす」道具なので、毎日動く側（341）と
検証側を揃えないと、会員は毎日15%「検証に無い銘柄」を見ることになる。
サイトの公開数字（10年・1,826トレード／勝率52.8%／PF1.55／最大DD−27.3%）も
この 341銘柄・scanner版regime・終値・v2.8.0 が基準。

---

## 使い方

```bash
pip install -r ../requirements.txt      # yfinance / pandas / numpy
```

| コマンド | 何をするか |
|---|---|
| `python precompute/precompute_metrics.py` | ローカルに CSV / Parquet を出すだけ（Supabase に触らない） |
| `python precompute/build_metrics.py --env-file ../ruletrade-app/.env.local` | 全期間を計算して Supabase に投入 |
| `python precompute/update_metrics.py --env-file ../ruletrade-app/.env.local` | 直近5営業日ぶんだけ投入 |
| `python precompute/build_portfolio.py --env-file ../ruletrade-app/.env.local` | 規定値ポートフォリオの成績を計算して保存 |

`--dry-run` を付けると件数だけ出して投入しない。`--limit 5` で先頭5銘柄だけ。

### GitHub Actions

| ワークフロー | いつ | 何を |
|---|---|---|
| `precompute-daily.yml` | 平日 18:30 JST | `update_metrics.py`（直近5営業日）→ `build_portfolio.py` |
| `precompute-monthly.yml` | 毎月1日 UTC20:00（＝2日 05:00 JST） | `build_metrics.py`（全期間の作り直し）→ `build_portfolio.py` |

必要な Secrets: `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY`。

月初に全部作り直すのは、yfinance の調整済み株価が分割・配当で**過去にさかのぼって変わる**ため。
日次差分だけでは古い行がずれたまま残る。

---

## ファイルの役割

| ファイル | 役割 |
|---|---|
| `universe.py` | 銘柄マスタ（`daily_scanner_v2_8_0.py` の `STOCKS` の写し。手で編集しない） |
| `thresholds.json` | 既存エンジンの閾値を切り出したもの |
| `config.py` | 設定の読み込みとパス |
| `fetching.py` | yfinance からの取得とローカルキャッシュ |
| `metrics.py` | **前計算列の定義**（どの列がエンジンのどこに対応するかを全部コメントに書いてある） |
| `engine_rules.py` | 既存エンジンのシグナル条件を前計算列で書き直したもの（照合用） |
| `pipeline.py` | 取得 → 指標 → 相場環境 の一本道。3つの実行スクリプトが全部これを通る |
| `preset_signals.py` | 条件ブロックでは書けない判定（ナイフガード・ミネルヴィニの入り口）を真偽値の列にする |
| `preset_rules.py` | 規定値ルール3本を前計算列だけで書いたもの |
| `backtest_engine.py` | **1ルールの約定・集計の正本**。書籍（BNF2検証）照合用＝翌営業日の**始値**で約定 |
| `portfolio_engine.py` | **ポートフォリオの約定・集計の正本**。本番v2.8.0＝翌営業日の**終値**で約定 |
| `portfolio_run.py` | 規定値3本を1回まわす。受け入れ試験と本番バッチが同じここを通る |
| `supabase_io.py` | PostgREST への upsert（標準ライブラリだけ）。時間切れなら塊を半分に割って入れ直す |

---

## 2つの数字を混ぜないこと

| 呼び名 | 何か | どこが計算するか |
|---|---|---|
| **ポートフォリオ合算** | 規定値3本を1つの資金で回した成績。**サイトの公開数字と同じもの**。分割利確・サーキットブレーカー・同セクター3本上限・同時保有10を含む | Python（`portfolio_engine.py`）→ `portfolio_results` 表 → `GET /api/backtest/portfolio` |
| **ルール単独** | そのルールだけを、ポートフォリオ制約なしで回した成績。会員が値を動かしたときに即時再計算される | SQL（`backtest_trades()`）→ `POST /api/backtest` |

**同時保有数に上限が無いと、最大DD・累積損益・年率は額面どおり読めない**
（ピーク時に資金を超える建玉になる）。API はその場合これらを `null` で返す
（`money_metrics_available: false`）。勝率・PF・平均損益は金額に依存しないので常に出る。

---

## 受け入れ試験

| 段 | 確かめること | 実行 |
|---|---|---|
| 1 | 移植したエンジンが正しい（BNF2検証のキャッシュ株価をそのまま使う） | `python precompute/tests/verify_bnf2_exp2.py` |
| 2 | 自前の取得・指標計算でも同じ数字になる（＝元データ差が無い） | `python precompute/tests/verify_bnf2_own_data.py` |
| 3 | TypeScript 側の集計が Python と同じ | `cd ruletrade-app && npm run test:backtest` |
| 4 | SQL 関数 `backtest_trades()` が Python と同じ約定を返す | `npm run test:backtest-sql` |
| 5 | **公開数字（1,826件/52.8%/PF1.55）を ±5% で再現できる** | `python precompute/tests/verify_published_numbers.py` |
| 6 | 応答に価格系列・銘柄明細・推奨語が入らない | `npm run test:backtest-response` |
| 7 | RLS（匿名・他会員から書けない） | `npm run test:rls` |

3・4 の材料は `tests/dump_candidates_exp2.py` / `tests/dump_candidates_parity.py` が作る。
**材料は Supabase に投入したのと同じデータから作ること。**
yfinance の調整済み株価は再取得のたびに 1e-8 ほど動くので、
`high_20_ratio >= 100` のような「ちょうど境界」の条件では判定が入れ替わる日が出る
（実測で9,339件中1件）。統計値には影響しない。

**同じ計算が3箇所（Python・SQL・TypeScript）にある。**
片方だけ直すと静かにズレるので、約定ルールを変えるときは必ず3つとも直し、
上の試験を全部回すこと。

---

## 触らないもの

- 既存の18本のワークフロー、`daily_scanner_v2_8_0.py`、`integrated_backtest_v2_8_0.py`
- `signals_log.csv`、`docs/*.json`（無料公開している成果物）
- `mcp/`（MCPサーバー）
