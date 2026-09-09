# ruletrade-cron — GitHub Actions を時間どおりに起こす Worker

## これは何のためか

GitHub Actions の `schedule` は**常時2〜5時間遅れて起動する**。
2026-09-09 に schedule で動く24本すべてを調べた結果（直近6回の中央値）:

| 分の指定 | ワークフロー | 遅れ中央値 | 最大 |
|---|---|---|---|
| :05 | cot-weekly | 30分 | 297分 |
| :47 | crash-daily | 174分 | 278分 |
| :30 | free-scanner | 192分 | 268分 |
| :00,:30 | daily-signal | 241分 | 296分 |
| :30 | precompute-daily | 277分 | 277分 |

「毎時0分・30分は混むので分をずらす」は**効かない**（`:05` でも `:47` でも同じように遅れる）。
2026-09-05 には free-scanner が丸ごと起動せず、公開JSONの対象日が4日間止まった。
GitHub は「起動しなかった」を失敗として記録しないので誰も気づけなかった。

Cloudflare の Cron Trigger は数秒〜数十秒のずれで動く。
ここから GitHub API の `workflow_dispatch` を叩けば、**狙った時刻に確実に走る**。

## GitHub 側の schedule は切替が済むまで残す

Worker が落ちたときの保険として、**並行運転の間だけ**残す。
二重に走っても各ワークフローの冪等ガード（当日ぶんが既にあればスキップ）が効くので害はない。
ザラ場の realtime-signal も `concurrency` で直列化され、
`signals_log.csv` の「同日・同銘柄・同戦略」判定で再通知されない。

3営業日安定したら `schedule:` を全部消す（起動文 Step 4）。
**残したままにしない**——遅延の原因が混ざって、どちらが起こしたのか分からなくなる。

---

## 本人にやってもらうこと（3ステップ）

### 1. GitHub の PAT を発行する

https://github.com/settings/personal-access-tokens/new

| 項目 | 値 |
|---|---|
| Token name | `ruletrade-cron-worker` |
| Expiration | 1年（切れたら作り直す。期限なしにしない） |
| Repository access | **Only select repositories** → `stock-trading-` **だけ** |
| Permissions → Repository permissions → **Actions** | **Read and write** |

Actions 以外は触らない（既定の Metadata: Read-only はそのまま残る）。

> **このトークンはチャットに貼らないでください。** 次の手順で端末に直接入力します。
> 漏れるとこのリポジトリのワークフローを誰でも起動できます。

発行すると `github_pat_...` が1度だけ表示される。**その画面を閉じる前に次へ進む。**

### 2. Cloudflare にデプロイする

このフォルダ（`cron-worker/`）で実行する。

```bash
npx wrangler login
```

ブラウザが開くので Cloudflare にログインして許可する。続けて:

```bash
npx wrangler deploy
```

### 3. 秘密を Worker に渡す（3つ）

```bash
npx wrangler secret put GH_PAT
```

`Enter a secret value:` と出たら、**1で発行した `github_pat_...` を貼って Enter**。
画面には表示されない。続けて残り2つ:

```bash
npx wrangler secret put RESEND_API_KEY
```

```bash
npx wrangler secret put NOTIFY_TO
```

```bash
npx wrangler secret put CRON_SECRET
```

| Secret | 中身 | 無いとどうなるか |
|---|---|---|
| `GH_PAT` | 1で発行した PAT | **何も起動しない**（ログとメールに残す） |
| `RESEND_API_KEY` | Resend の API キー | 起動に失敗してもメールが来ない |
| `NOTIFY_TO` | 通知を受けるメールアドレス | 同上 |
| `CRON_SECRET` | 会員アプリ（`ruletrade-app`）側と**同じ値** | 朝の会員通知が送られない |

> `CRON_SECRET` は会員アプリ側の環境変数と1文字でも違うと 401 になる。
> 値は `ruletrade-app` の設定を正とする（引継ぎ.md §19 相談⑨）。

---

## 出す前に机上で確かめる

GitHub にも Cloudflare にも触らずに、1日ぶんの起動を再現できる。

```bash
node cron-worker/test/simulate.mjs 2026-09-09
```

```
06:30  ai-analysis.yml
07:00  pipeline-morning.yml
07:30  [HTTP] POST /api/cron/morning-notify
...
18:00  pipeline-daily.yml
23:30  data-healthcheck.yml
ワークフロー起動 45 回 / 13 種類
```

その日に起動したものは**成功したことにする**ので、これが「ふつうの日」。
`fail=` を付けると失敗した日を再現でき、予備と関門の動きを確かめられる。

```bash
node cron-worker/test/simulate.mjs 2026-09-12            # 土 → pipeline-weekly だけ
node cron-worker/test/simulate.mjs 2026-09-21            # 敬老の日 → 何も起動しない
node cron-worker/test/simulate.mjs 2026-09-09 fail=pipeline-daily.yml
  # → 21:00 の予備が {"backup":"true"} 付きで走る
node cron-worker/test/simulate.mjs 2026-09-09 fail=pipeline-morning.yml
  # → 7:30 の会員通知を送らず、本人にメールが飛ぶ
```

時刻表そのものの点検（cron と食い違っていないか・重複確認の入れ忘れ）は:

```bash
cd cron-worker; npm test
```

デプロイ直前に、Cloudflare のローカル実行でも1回叩いておく:

```bash
npx wrangler dev --test-scheduled
```

別の端末から `curl "http://localhost:8787/__scheduled?cron=0,30+*+*+*+*"` を叩くと
`scheduled()` が動く。`GH_PAT` はローカルには無いので、
**「GH_PAT が設定されていません」とログに出れば配線は正しい**（実際の起動はしない）。

---

## 動いているか確かめる

デプロイ時に表示された URL（`https://ruletrade-cron.<アカウント名>.workers.dev`）を開くと、
今の JST と設定内容が JSON で返る。**開くだけでは何も起動しない。**

```json
{
  "now_jst": "2026-09-10 08:00",
  "trading_day": true,
  "holiday_table_covers_year": true,
  "secrets": { "GH_PAT": true, "RESEND_API_KEY": true, "NOTIFY_TO": true, "CRON_SECRET": true },
  "this_minute": { "why": "平日の予定", "targets": ["daily-signal.yml"] },
  "intraday": { "workflow": "realtime-signal.yml", "from": "09:00", "to": "15:45" }
}
```

`secrets` が `false` のものがあればステップ3ができていない。
`holiday_table_covers_year` が `false` なら休場日表の年が切れている（`src/holidays.js` に足す）。

実際の起動ログは Cloudflare のダッシュボード → Workers & Pages → ruletrade-cron → Logs で見る。
翌営業日の朝、GitHub の Actions 一覧で
**`workflow_dispatch` 起動のものが予定時刻ちょうどに並んでいれば成功**。

---

## 起動する時刻（JST）

### 平日

| 時刻 | 起動するもの | |
|---|---|---|
| 06:30 | ai-analysis | 朝のAI分析 |
| 07:00 | **pipeline-morning** | 前営業日ぶんが無ければ**失敗させる**関門 |
| 07:30 | **会員向けの朝の通知**（HTTP） | `ruletrade-app` の `/api/cron/morning-notify` を叩く。**07:00 の関門が成功したときだけ** |
| 07:30 | karauri-daily / kessan-daily | |
| 08:00 | daily-signal | 朝 |
| 08:30 | morning-digest / kessan-react-daily | 朝の逆指値メール |
| 09:00〜15:45 | **realtime-signal** | ザラ場15分毎（1日28回） |
| 09:00 / 10:30 / 12:00 / 14:30 / 16:00 | heatmap | 値動き |
| 12:00 | daily-signal | 昼 |
| 16:30 | investor-flow | **木曜だけ**（JPX の公表が木曜） |
| 18:00 | **pipeline-daily** | 夕シグナル→free-scanner→radar→派生→前計算→点検 |
| 19:00 | buyback-daily / ai-record | |
| 21:00 | **pipeline-daily**（予備） | 18:00 が成功していれば起動しない |
| 23:30 | data-healthcheck | 止まっていれば Issue |

### 土曜

| 時刻 | 起動するもの |
|---|---|
| 09:30 | **pipeline-weekly**（cot→信用残・建玉スコア→十倍株→週次AI） |

休場日（土日＋JPX の休場日）は市場データを作るものを起こさない。
週末バッチだけは土曜に動かす。休場日表は `src/holidays.js`（年1回の手入れ）。

### 会員向けの朝の通知だけ、GitHub を経由しない

JST 7:30 に `https://ruletrade-app.vercel.app/api/cron/morning-notify` を
`Authorization: Bearer <CRON_SECRET>` で POST する（Vercel Pro を買わずに時刻どおり叩くため）。

**07:00 の `pipeline-morning` が成功していなければ叩かない。**
あれは「前営業日ぶんのデータが揃っているか」の関門で、落ちている日に通知を送ると
**古いデータで会員に直接届く**。判定できないとき（まだ実行中・API が答えない）も含めて
送らない側に倒し、代わりに本人へメールを出す。

> ここだけ「判定できなければ止める」向き。ワークフローの二重起動防止は逆に
> 「判定できなければ走らせる」（二重に走っても害がないため）。**会員に届くかどうか**で分けている。

### 変えたいとき

`src/index.js` の `PLAN` を直して `npx wrangler deploy` するだけ。
ただし**書ける時刻は :00 と :30 だけ**（それ以外を書いても発火しない）。`npm test` が止める。

1日に複数回走るものを足すときは `NO_DEDUP` にも入れる。
入れ忘れると2回目以降が**黙って消える**（失敗として記録されないので気づけない）。これも `npm test` が止める。

> Cron Trigger は2本だけ。無料プランはアカウントあたり5本まで。
> 1本目（毎時 :00 :30）で PLAN を振り分け、2本目（ザラ場の :15 :45）は
> realtime-signal を15分間隔にするための補い。

## 費用

無料枠の範囲。cron の発火が1日76回、そこから起動する dispatch が平日45回。
無料枠は10万リクエスト/日なので桁がいくつも違う。
