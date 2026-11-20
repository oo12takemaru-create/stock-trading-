# kaburadar-board-live — 相場ボードの取引時間中の自動更新（2026-11-20）

kaburadar.jp の相場ボード（world.html のチャート表示・トップの6枚の帯）を、取引時間中は5分ごとに新しい値にする中継ぎ。
画面（サイト側 `boardlive.js`）が `GET https://kaburadar-board-live.oo12takemaru.workers.dev/board` を読む。
読めない・30分より古いときは、画面は今までどおり `docs/market_board_1d.json`／`market_board.json`（world.yml・1日3回）に戻る。

## しくみ
- 取得元: Yahoo!ファイナンスの spark（v7）。1回の取り直しで2回だけ呼ぶ
  - 取引所の15本: `range=1d&interval=5m`
  - 先物4本・為替3本・ビットコイン: `range=2d&interval=15m`（先物は米東部 18:00〜翌17:00、為替とビットコインは直近24時間）
- 前日終値は収集側の `docs/market_board_1d.json` を正にする（Yahoo の meta の前日終値は SENSEX・WTI でずれていた）
- 形は `market_board_1d.json` と同じ（`build.js` の先頭のコメント）。各行に `live:1`、`ts`（取得元の値の時刻・日本時間）
- 日本10年（財務省・日次）は対象外。VIX は米国の取引時間の前は 1日の線が取れない（ok=0）→ 画面は今までの値のまま
- **Cron Trigger は使わない**。`/board` が呼ばれたとき、KV の値が間隔より古ければ取り直す
  - 間隔（JST）: 平日 8:45〜15:45 = 5分／それ以外の平日の夜・朝 = 15分／土曜 7:00〜月曜 8:45 = 60分
  - 少し古い（間隔の2倍まで）なら今の値を返して裏で取り直す。それより古い・無いときは待って取る（1秒前後）
  - 誰も見ていない時間は取得元に行かない

## 無料枠
| 枠 | 上限（無料） | 見積もり |
|---|---|---|
| Workers のリクエスト | 10万/日（アカウント合計） | 画面は90秒おき（15分・60分の時間帯は次の更新の頃まで待つ）。1人が日本の取引時間に30分開いて約20回。ruletrade-mcp の約1,100/日と合わせても、1日 4,000人·回 程度まで余裕 |
| KV 書き込み | 1,000/日 | 間隔ごとに最大1回: 平日 85（5分）＋約70（15分）≒ 155/日、週末 約24/日（複数の拠点が同時に書くことがあっても数百） |
| KV 読み取り | 10万/日 | 同じ isolate は30秒手元に持つ・KV 側も cacheTtl 30秒 |
| CPU | 10ms/回 | 組み立ては 2ms 前後（手元の計測） |
| サブリクエスト | 50/回 | 取り直し1回で3（spark 2・market_board_1d.json 1） |

## 反映（本人の端末で）
```bash
cd board-live-worker
npx wrangler deploy
```
KV「BOARD_LIVE」（id は wrangler.toml）は 2026-11-20 に作成済み。秘密の値は無い。

## 確かめる
- `https://kaburadar-board-live.oo12takemaru.workers.dev/` → 今の JST・間隔・KV の値の古さ（開くだけでは取りに行かない）
- `.../board` → `ok` が 20 以上、`fetch_err` が無いこと（取得元に遮断されると `fetch_err: [429]` などが入る）
- `npm test`（組み立ての点検）、`npx wrangler dev --local` で手元実行

## 止めるとき
Cloudflare のダッシュボード → Workers & Pages → kaburadar-board-live → 削除（または `npx wrangler delete`）。
画面は読めなくなると自動で今までの1日3回の値に戻るので、サイト側は触らなくてよい。
