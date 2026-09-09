# ruletrade-cron — GitHub Actions を時間どおりに起こす Worker

## これは何のためか

GitHub Actions の `schedule` は**常時2〜5時間遅れて起動する**。
2026-09-09 に schedule で動く19本すべてを調べた結果（直近6回の中央値）:

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

## GitHub 側の schedule は消さない

この Worker が落ちたときの保険として残す。
二重に走っても各ワークフローの**冪等ガード**（当日ぶんが既にあればスキップ）が効くので害はない。

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

### 3. トークンを Worker に渡す

```bash
npx wrangler secret put GH_PAT
```

`Enter a secret value:` と出たら、**1で発行した `github_pat_...` を貼って Enter**。
画面には表示されない。これで Cloudflare 側に暗号化して保存される。

---

## 動いているか確かめる

デプロイ時に表示された URL（`https://ruletrade-cron.<アカウント名>.workers.dev`）を開くと、
今の JST と設定内容が JSON で返る。**開くだけでは何も起動しない。**

```json
{
  "now_jst": "2026-09-10 08:00",
  "weekday": true,
  "has_token": true,      ← false ならステップ3ができていない
  "repo": "oo12takemaru-create/stock-trading-",
  "plan": { "08:00": ["daily-signal.yml"], ... }
}
```

実際の起動ログは Cloudflare のダッシュボード → Workers & Pages → ruletrade-cron → Logs で見る。

翌営業日の朝、GitHub の Actions 一覧で **`workflow_dispatch` 起動のものが予定時刻ちょうどに並んでいれば成功**。

---

## 起動する時刻（JST・平日のみ）

| 時刻 | ワークフロー | |
|---|---|---|
| 08:00 | daily-signal | 朝 |
| 12:00 | daily-signal | 昼 |
| 18:00 | daily-signal | 夕（終値ベース。radar-publish がこの完了で連鎖する） |
| 18:30 | precompute-daily | 前計算 → 公開数字 → score3_lite → キャッシュ温め |
| 19:30 | free-scanner | 無料版スキャナー |
| 20:30 | free-scanner | 予備 |
| 22:30 | precompute-daily | 予備 |

時刻を足す・変えるときは `src/index.js` の `PLAN` を直して `npx wrangler deploy` するだけ。
`wrangler.toml` の Cron Trigger（毎時0分・30分）は触らない。

> Cron Trigger を1本にしてあるのは、無料プランが1 Worker あたり3本までのため。
> 毎時起こして Worker の中で JST を見て振り分けている。

## 費用

無料枠の範囲。1日48回起動 × 平日のみで月1,000回程度、
無料枠は10万リクエスト/日なので桁がいくつも違う。
