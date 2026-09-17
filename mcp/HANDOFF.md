# 引き継ぎ資料 — ルールトレードMCP

作成: 2026-09-04(スマホからのリモートセッション)
ブランチ: `claude/rule-trade-mcp-free-launch-136is2`(push済み・main 未マージ)
次の作業者: 家のパソコンの Claude Code(このファイルを読ませて再開する)

---

## 1. 目的と方針(決定済み・再検討不要)

- 今週中に **無料の MCP ツール4本を公開**する。Claude主導、本人作業はデプロイ確認のみ。
- **既存の公開JSON(`docs/*.json`)を読むだけの変換層**。エンジンは持たない。二重実装しない。
- **呼び出しログを初日から取る**。実際にどう使われているかを数字で見る。
- `run_rule_backtest` は、ビルダー Phase 2 の `backtest_grid` ができた時点で検討。
- **法務線は人向けサイト(kaburadar.jp / ruletrade.jp)と同一**: 推奨語禁止・免責キー常設。

## 2. いまの状態

| 項目 | 状態 |
|---|---|
| コード | `mcp/` 配下に完成。依存パッケージゼロ(Node 20+ の標準機能のみ) |
| テスト | `cd mcp && npm test` → 18本すべて合格(実際の docs/*.json をフィクスチャに使用) |
| 本番データでの動作 | ローカル dev サーバーから GitHub raw の JSON を読んで4本とも正常応答を確認済み |
| CI | `.github/workflows/mcp-test.yml` を追加(mcp/ 変更時に自動テスト) |
| デプロイ | **公開済み**(2026-09-04 Cloudflare Workers)。**正規URL = `https://ruletrade.jp/mcp`** |
| main マージ | 済(PR #352 ほか) |

## 3. ファイル構成

```
mcp/
├── src/
│   ├── server.js      MCP Streamable HTTP(JSON-RPC・ステートレス)の実装、ルーティング、呼び出しログ
│   ├── tools.js       ツール4本の定義(tools/list 用スキーマ)と実装
│   ├── data.js        GitHub raw から JSON を取得(60秒キャッシュ)。DATA_BASE で差し替え可
│   ├── legal.js       免責文 DISCLAIMER・推奨語リスト NG_WORDS・伏せ字化 scrub()
│   └── docs.js        GET /llms.txt と GET /openapi.json(エージェント向けの発見用・英語主)
├── worker.js          Cloudflare Workers エントリ
├── api/[[...path]].js Vercel Functions エントリ(全パスを rewrite で集約)
├── vercel.json        Vercel 用 rewrite
├── wrangler.toml      Cloudflare 用(observability=有効。Analytics Engine はコメントアウト)
├── dev.js             ローカル確認用 http サーバー(node dev.js → :8787)
├── test/server.test.js 18テスト
├── package.json
├── README.md          デプロイ手順・接続方法・環境変数
└── HANDOFF.md         このファイル
```

ルート `.gitignore` は `*.json` を無視する設定なので、`!mcp/package.json` `!mcp/vercel.json` の例外を追加済み。
**mcp/ に新しい JSON を足すときは .gitignore に例外を追加すること。**

### 3-9. 課金はしない(2026-09-14 本人決定)

初期の資料には「需要を見てから有料化」「有料側 `run_rule_backtest`」と書いてあったが、
**2026-09-14 に Stripe を閉じ、有料は作らないと決めた**。収益は書籍・note・広告で立てる。

MCP 側でやること:

- **「有料」「プラン」「無料版」「free tier」の語を新しく書かない。**
  区分を示す語は「他に区分がある」と読める。APIキーも登録も要らない、という事実だけ書く
- `list_tools_guide` の `tier: "free"` → `access: "APIキー・登録・利用料は不要"` に置き換えた
- `free_tier_limits` → `limits`(返さないものの一覧)に改名
- **免責文(`legal.js` の DISCLAIMER)にも「無料版データは1営業日遅れ」と書いてあった。**
  全応答に入るので影響が大きい。「日次データは1営業日遅れ(書籍の検証結果は刊行時点の固定データ)」に直した
- `test("★方針★ 区分や課金を示す語を1つも書かない")` が、8本の応答・tools/list・
  llms.txt・openapi.json すべてを機械検査している

### 3-10. レジストリの再publish（v0.1.0 → v0.2.0）

**掲載済み**: MCP 公式レジストリ `jp.ruletrade/mcp` v0.1.0 `status: active`（2026-09-05・
ドメイン認証）／Smithery 掲載済み。

**★順序を飛ばさない（2026-09-17 Fable）★**

1. `server.*.json` の 0.2.0 化（PR）
2. **本人が `wrangler deploy`**
3. **本番が8本になっているか機械検査**
4. **本人が publish**（`server.dns.json`・`_secrets/` の鍵）
5. Smithery の説明文を更新
6. 記録

レジストリに「8本」と書いてある状態で本番が4本だと、エージェントが空振りする。
**先に実体を合わせる。** 手順と説明文の文案は `mcp/registry/README.md` に置いた。

## 4. ツール8本と読んでいるJSON

| ツール | 読むJSON | 引数 | 備考 |
|---|---|---|---|
| `get_daily_signals` | `docs/free_scanner.json` | `strategy`(bnfのみ), `include_watch` | judge=signal→`rule_hit`、watch→`watch` に言い換え。価格・株数は元JSONに無い |
| `get_market_regime` | `docs/radar.json`, `docs/market_jiai.json`, `docs/radar_history.json` | `history_days`(0〜60) | 銘柄名なし・件数のみ |
| `get_anomaly_summary` | `docs/anomaly_results.json`, `docs/gauge.json`, `docs/crash.json` | `name`, `detail` | ジンクス50本の検証結果 + 『暴落は、減衰する』5前兆 + 着火メーター7フラグ |
| `get_candlestick_verdict` | `docs/candlestick_verdict.json` | `pattern`, `detail` | 酒田五法12本。**採用ゼロ本**。落ちた基準と不採用理由を返す |
| `get_event_reaction` | `docs/event_reaction.json`, `earnings_drift.json`, `supply_demand_events.json` | `event`, `detail` | **3つの検証を統合**。出来事27種 / 決算後ドリフト / 指数の入替 |
| `get_indicator_verdict` | `docs/indicator_verdict.json` | `name`, `detail` | テクニカル指標26種。**使える9 / 条件付き4 / 捨てろ13** |
| `get_etf_decay` | `docs/etf_decay.json` | `code`, `detail` | レバレッジ・インバースETFの減価12年。戻り待ち305回 |
| `list_tools_guide` | なし(静的) | なし | 更新時刻・遅延・制限・免責・ロードマップ |

エンドポイント: `POST /mcp`(MCP本体)、`GET /health`(5JSONの疎通)、`GET /llms.txt`、`GET /openapi.json`、`GET /`(概要)。`GET /mcp` は仕様どおり 405。

### 4-0. 書籍の検証結果を返す2本(2026-09-17 追加・v0.2)

- 実体: `docs/candlestick_verdict.json`(12本・49KB)、`docs/event_reaction.json`(27件・36KB)。
  どちらも **日次更新ではなく書籍刊行時点の固定データ**。
- 生成: `mcp/tools/build_candlestick_verdict.py` / `build_event_reaction.py`。
  検証の生出力(CSV・JSON)から**機械変換**する。**数字を手で書き写さない**。
- 検証: `mcp/tools/verify_candlestick_verdict.py` / `verify_event_reaction.py`。
  **元CSVまで遡って**1つずつ突き合わせる(中間ファイルがずれていても効く)。
  元データが変わったら気づけるよう、`adoption` が全部 `rejected` かも見ている。

**★酒田は採用ゼロ本★**(2026-09-17 Fable 判断)

- 元データ12本すべてが `adoption: "rejected"`。`verdict` は `not_adopted` の1値だけを使う。
  **`adopted` は使わない**。起動文には「採用2本」とあったが、実データが正。
- 逆三尊のみ `filter_candidate: true` を併記(元データの `status` が `candidate`)。
  ただし verdict は `not_adopted`。採用ではない。
- **三空叩き込み(PF 3.04)の不採用理由は要約せず全文で返す。**
  「発見期と確認期に分けると符号が反転する」— PF が高くても採用できない理由は
  他のどこにも書いていない。このツールで最も価値のある答えなので、test で固定している。
- **逆三尊の不採用理由だけは元の文を使わない。** 元には「『この形が出た直後は買わない』という
  回避フィルターの候補」という検証者の設計メモが入っており、そのまま出すと行動の指示に読める。
  `build_candlestick_verdict.py` の `REASON_OVERRIDE` で差し替え、verify が差し替え済みかを確かめる。

**★連想は銘柄バスケットを読み込まない★**

- 元データの同じフォルダに `baskets.csv`(499行・code と name)がある。混ぜた瞬間に
  「出来事 → 買う銘柄」を返す道具になり、法務の線を越える。**生成側で開かない**。
- verify が「バスケットの銘柄名が1つも入っていないか」を機械で確かめている。

**★4桁の検査で数値の中を見ないこと★**
p値 `0.0045` を正規表現で拾うと `0045` が銘柄コードに見える。銘柄コードが紛れ込むとしたら
**文字列の値**(名前・別名・説明)なので、JSON を walk して文字列だけを集めて調べる。

### 4-0-4. 既存2本の修正(2026-09-17・v0.2 Step4)

**`get_daily_signals`: 上位3件 → 1件に縮小**

引継ぎ §12-5「rule_hits.json は全銘柄の該当リストを公開しない。by_code はその日1件のみ」
§19-9「登録なしで出すのは銘柄名1件＋地合いラベルまで」に揃えた。

| 変えたこと | |
|---|---|
| `items`(3件の配列) | **削除**。`top_hit`(1件・無ければ null)に |
| `include_watch` 引数 | **削除**(監視候補を返さなくなったため) |
| `rule_hit_count` / `watch_count` | **削除**。`counts: null` + `counts_note` |

**★件数は返せない（2026-09-17 Fable 指摘で修正）★**

いったんは「上位3件の中での内訳」を `counts` として返したが、これは
**「今日は何件あったか」の答えにならない**。生成側 `scanner_free.py` は

```python
rows.sort(key=lambda r: r["kairi"])   # 全銘柄を判定して
shown = rows[:top_n]                   # 上位3件に絞る（全体の該当数は捨てる）
```

としており、**全銘柄ベースの該当数はどこにも出力されていない**
（`docs/*.json` を全部あたって確認済み）。無い数字を、あるように見える形で
返さない。`counts: null` と理由を返す。

全銘柄ベースで返せる件数は `full_system_today.signal_count`（本番システム
3戦略の当日合計）だけ。これは BNF 単独の数ではないので note で区別している。

生成側に1行足せば出せるが、起動文 §8「生成側 Python には触らない」。

test は **配列で銘柄を返すキーが1つも無いこと** と **counts が null であること**
を機械検査している。

**`get_market_regime`: 3軸スコアを追加**

起動文には「`radar.json` に無い間は null」とあるが、**実際は `docs/score3.json` で
配信済み**だった。そこから読む(5段階・軸ごとの点数と根拠・5営業日以内のイベント)。

★`regime` と `score3` は食い違うことがある★
観測時点で regime=BULLISH・score3=lean_defense(やや守り)だった。基準が違うため。

- `regime` … 本番システムが戦略を切り替えるための分類。ザラ場の値も反映
- `score3` … 前営業日の終値をもとにした整理。日次更新

**黙っていると誤読される**ので `score3.vs_regime` と `how_to_read` に明記し、test で固定した。
`metrics`(日経の終値・移動平均・ドル円の生値)は返さない。軸の `reasons` に言葉で入っている。

### 4-0-3. get_event_reaction に3つの検証を統合(2026-09-17・v0.2 Step3)

`get_event_reaction` は**3つの JSON を読む**。ツールは増やしていない。

| データセット | JSON | 書籍 | 引く言葉 |
|---|---|---|---|
| 出来事27種 | `event_reaction.json` | 36 | 地震 / 利上げ / 関税 / earthquake … |
| 決算後ドリフト | `earnings_drift.json` | 31 | 決算 / earnings / PEAD |
| 指数の入替 | `supply_demand_events.json` | 29 | TOPIX / 日経225 / 除外 / 採用 |

**★なぜ1本にまとめるか★**
3つは別々のデータから**同じ形**を示している。

- 出来事27種の「初動型」= 前日引けから翌朝の寄り付きで終わる
- 決算後ドリフト = +5日で統計的に消える
- 指数の入替 = 実施日を通過した時点で動きが止まる

並べて返すことに意味がある。`key_finding` にこの3つを1文でまとめてある。

**★返却の構造(Step1 から変えた)★**
`items` をトップに置くのをやめ、`market_events` / `earnings_drift` / `supply_demand` の
3つに分けた。測り方が違う(分類バスケットの超過収益 / 決算の分位CAR / イベント前後のCAR)ので、
同じ配列に入れると比較できない数字が並ぶ。免責は `notes` にまとめてトップへ。

**★サイズの設計(実測して決めた)★**

| 呼び方 | バイト |
|---|---|
| 引数なし | 11,113 |
| `{detail:true}` | 11,165 |
| `{event:"地震"}` | 8,955 |
| `{event:"決算", detail:true}` | 18,424 |
| `{event:"TOPIX"}` | 15,630 |

- **event を省略したら中身を返さない**(見出しだけ)。3つの中身を全部足すと 26KB
- **detail は絞り込んだときだけ効く**。27件すべての詳細を返すと **61KB で上限超過**
- 判定の意味は `verdict_legend` に1回だけ(27件ぶん繰り返さない)

**★決算で落としてはいけない数字★**
分位5(最も好反応)の+5日は **+0.04%(p=0.91)** で、統計的にゼロと区別できない。
ここが消えると「好決算を買えば取れる」と読まれる。test で有意でないことを固定している。
差が出たのは下位側が動き続けた分。**README の「儲けの源泉は買いではなく売りだった」という
一文は取り込まない**(行動の含意が強い)。同じ内容を数字で返す。

**★自社株買いは入れていない★**
起動文 §2 は「自社株買いで株は上がるか」も挙げているが、`00_マスター検定結果.csv` に
自社株買いの行が無い。あるのは `buyback_*.csv`(5,523行・3,658行・5,681行)で、
**いずれも銘柄コード・企業名つきの明細**。起動文 §6「明細は絶対に入れない」に当たる。
明細から集計を作るのは検証のやり直しで、実務の範囲を超える。
→ `not_included` に理由を書いて返す。**黙って落とさない。**

**★「p=0」を注記なしで返さない★**
元CSVに `0.0` の行がある(TOPIX採用 指定日 CAR(-20,-1))。厳密なゼロではないので
`p_note` を添える。ジンクス50本で同じ手当てをしたのと同じ。

**★4桁コードの検査を共通化した(2回同じ罠を踏んだため)★**
`mcp/tools/legal_check.py` の `find_codes()` に寄せ、**verify 5本すべてが使う**。

1. 1回目: JSON 全体を文字列にして探し、p値 `0.0045` の小数部が `0045` に見えた
   → 文字列の値だけを walk するようにした
2. 2回目: 文章に「p=0.0007」と書いたため、**文字列の中に本当に `0007` があった**
   → 直前・直後に数字か小数点が無い4桁だけを拾うようにした(`(?<![\d.])\d{4}(?![\d.])`)

**test/server.test.js の JS 側も同じ正規表現に直してある。**

### 4-0-2. 指標26種とETF減価(2026-09-17 追加・v0.2 Step2)

- 実体: `docs/indicator_verdict.json`(26種・43KB)、`docs/etf_decay.json`(26KB)。固定データ。
- 生成: `build_indicator_verdict.py` / `build_etf_decay.py`。検証: `verify_*.py`。

**★指標26種は「判定の正本」が2つある★**

| | 出どころ | 中身 |
|---|---|---|
| 統計 | `physics_dex/results/summary.csv` | 勝率・期待値・PF・8区分の超過 |
| **3段判定** | **書籍21の原稿 `結合原稿_第3稿.md` の「全判定早見表」** | 使える9 / 条件付き4 / 捨てろ13 |

CSV にも `判定(下書き)` 列があるが**古い**(下書きでは「使える」が17個)。
起動文の指示どおり**書籍の判定を正**とする。

**ただし「使える9個」は数字から再現できる。**
8区分すべてで超過が +0.3% 超＝ちょうど9個で、書籍の「使える」と完全一致する。
`verify_indicator_verdict.py` が毎回検算しており、基準と判定がずれたら落ちる。
「条件付き」と「捨てろ」の境目は数字だけでは決まらない(同じ2区分落ちでも両方に分かれる)ので、
ここは原稿を正本として読む。

**書籍本文の「一言」は取り込まない。**
『衰弱進行中。新規学習は非推奨』のように推奨語を含み、そもそも統計ではなく著者の評価。
代わりに `below_bar`(+0.3% に届かなかった区分)を計算で出し、
**なぜその判定なのかを数字で示す**(酒田の `checks_failed` と同じ)。

**★ETFのコードは返す★**
`1357` / `1570` は起動文 §4-4 が `code` 引数で明示している。
**何を検証したかを示す識別子**で、個別株を名指しして売買を促すのとは性質が違う。
`verify_etf_decay.py` は **この3つ以外の4桁が出たら落とす**(個別株の混入)。

**★書籍の文章を取り込むときの2つの罠(2026-09-17 に実際に踏んだ)★**

1. **著者の執筆メモが混ざる。**
   「章立てへの示唆: 6章を…に書き換える」は検証結果ではない。`DROP_PREFIXES` で落とす。
   酒田の逆三尊で「回避フィルターの候補」を落としたのと同じ線。
2. **NG語を含む普通の日本語がある。**
   「各買い時点の日経平均」は `legal.js` の NG_WORDS(買い時)に当たり、本番の scrub で
   「各［表現調整］点の日経平均」になって**意味が壊れる**。`SAFE_REWRITE` で言い換える
   (「購入時点」。「買った時点」だと「各買った時点」になり不自然なので、前に付く語を選ばない形にする)。

→ **`mcp/tools/legal_check.py` が JSON を書き出す前に止める。**
   NG_WORDS は `src/legal.js` から読む(書き写すと二重管理になり片方だけ増える)。
   **5本の build すべてがこれを通している。** 伏せ字は最後の砦であって、そこに頼らない。

**★「解釈で注意すべき点」を必ず返す★**
ETF の数字は「日経が12年で約4倍になった上昇相場」のもの。この前提を落とすと
結果をまるごと読み違える。元 md の注意書きを節ごとに載せ、test で存在を固定している。

### 4-1. ジンクス検証データ(2026-09-04 追加)

- 実体: `docs/anomaly_results.json`(50本の配列・52KB)。**日次更新ではなく書籍刊行時点の固定データ**。
- 生成側は `株式投資開発/jinx_verification/`(別フォルダ・git管理外)。**MCP はコピーを読むだけで生成側は触らない**。
  コピー時に JSON 非対応の `NaN`(5箇所)を `null` に置換している。
- 返すのは判定結果と統計値のみ(`name/category/judgment/definition/win_rate/mean/n/p` 等)。
  **`saying`(格言の本文)は「儲かる」「売れ」等の推奨語を含むため出力しない**(伏せ字だらけになるのを避けるため)。
  `chart`(図のパス)・`expect` の生値も出さない。
- `name` 引数で名前/カテゴリの部分一致絞り込み。引数なしの一覧は判定のみの軽い形(全件+統計値だと約38K文字になるため)。
- 検証不能・姉妹書送りの4本(決算またぎ / 優待の新設 / オリンピック / サザエさん効果)は `metrics` が空。
  落とさずに `verified: false` + `note`(理由)を付けて返す。

### 4-2. 正規URLと配信経路(2026-09-05)

```
利用者 → https://ruletrade.jp/mcp  (Vercel 静的サイト・site/vercel.json の rewrite)
                 ↓ プロキシ
         https://ruletrade-mcp.oo12takemaru.workers.dev/mcp  (Cloudflare Workers 実体)
```

- **案内する URL は常に `https://ruletrade.jp/mcp`。`*.workers.dev` は案内しない**(テストで機械検査している)。
  Worker を引っ越しても `site/vercel.json` の2行を直すだけで、レジストリ登録も利用者側も変わらない。
- **DNS は変更していない**(2026-08-31 の DNS 移管失敗を繰り返さないため)。Cloudflare のカスタムドメインも使わない。
- Vercel の rewrite 経由だと Worker から見た `url.origin` は workers.dev のままなので、
  **URL をレスポンスに書くときは `src/tools.js` の `CANONICAL_ORIGIN` / `CANONICAL_ENDPOINT` / `HOME_URL` を使う**。
  リクエストのホストから組み立ててはいけない。
- `/mcp/health`・`/mcp/llms.txt`・`/mcp/openapi.json` は rewrite の2本目(`/mcp/(.*)` → Worker の `/$1`)で届く。
- 呼び出しログは Worker 側にそのまま残る(プロキシしてもクライアント情報は失われない)。

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

1. ~~「ジンクス本の検証結果」の所在。~~ **解決(2026-09-04)**。選択肢 (a) を採用。
   `株式投資開発/jinx_verification/results/results.json` を `docs/anomaly_results.json` としてコピーし、
   `.gitignore` に `!docs/anomaly_results.json` を追加、`tools.js` の `get_anomaly_summary` から読んでいる。
   `ANOMALY_URL` は別配信先に置きたい場合の予備として残置(キー名は `jinx_verification_external` に変更)。
2. **デプロイ先の選択。** → Cloudflare Workers に決定(2026-09-04・ログ保持のため)。手順は README.md の「デプロイ」。
   ⚠️ **デプロイ前に main へマージすること。** `DATA_BASE` の既定は GitHub raw の `main/docs` を指すので、
   `docs/anomaly_results.json` が main に無いと `/health` が 503 になる。
3. **リポジトリを非公開化する予定の有無。** → 当面は公開のまま(2026-09-04 決定)。
   非公開化すると raw URL が読めなくなるので、その時は `DATA_BASE` を別配信先(Pages 等)に向ける。

## 8. 家での再開手順

```bash
git fetch origin claude/rule-trade-mcp-free-launch-136is2
git checkout claude/rule-trade-mcp-free-launch-136is2
cd mcp && npm test            # 18 pass を確認
node dev.js                   # 別ターミナルで
curl -s localhost:8787/health
curl -s -X POST localhost:8787/mcp -H 'content-type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"list_tools_guide","arguments":{}}}'
```

Claude Code に渡す一言:
> `mcp/HANDOFF.md` を読んで、未決事項の1(ジンクス検証JSON)から進めて。場所は ○○ にある。

## 9. 公開までの残タスク(順番どおり)

- [x] ジンクス検証JSONを特定して `get_anomaly_summary` を正式対応(2026-09-04・`docs/anomaly_results.json` として同梱)
- [x] main へマージ(PR #352)
- [x] Cloudflare にデプロイ → `/health` が `"ok": true`(2026-09-04)
- [ ] Claude Code から接続して4本の出力文言を目視: `claude mcp add --transport http ruletrade https://<host>/mcp`
- [x] Claude Code から接続確認(2026-09-05・`https://ruletrade.jp/mcp` で ✔ Connected)
- [ ] claude.ai のカスタムコネクタでも接続確認(設定 → コネクタ → URL に `https://ruletrade.jp/mcp`)= **本人作業**
- [x] 人向けサイトに MCP の案内(`system.html`・`scanner.html`・`site/llms.txt`)
- [ ] MCP レジストリ登録 = **本人作業**(登録内容の案は `mcp/registry/` にある)
- [x] 呼び出しログを集計(ツール別回数・クライアント別)→ `mcp/tools/aggregate_calls.py`

## 10. 触らないもの

- `daily_scanner_*.py`, `scanner_free.py`, `make_radar_json.py` などの既存生成側。MCP は読むだけ。
- 既存ワークフロー(`.github/workflows/` の mcp-test.yml 以外)。
- `docs/index.html`(人向けサイト)。
