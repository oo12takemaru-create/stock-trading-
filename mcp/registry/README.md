# レジストリ登録の要件と登録内容の案

作成: 2026-09-05（起動文 `起動文_実務_MCP公開後仕上げ_2026-09-05.md` Step 5）
更新: 2026-09-17（v0.2.0・ツール8本への再publish 準備）

## 状態

| | |
|---|---|
| MCP 公式レジストリ | **掲載済み**。`jp.ruletrade/mcp` **v0.1.0** `status: active`（2026-09-05） |
| 認証方式 | ドメイン認証（`ruletrade.jp` の apex に TXT 1本） |
| Smithery | **掲載済み** |
| いまの `server.*.json` | **v0.2.0・ツール8本**（このPRで更新。**まだ publish していない**） |

確認: `https://registry.modelcontextprotocol.io/v0/servers?search=ruletrade`

### ★再publish の順序（2026-09-17 Fable）★

**publish は Worker が8本で動いてから。** 順番を飛ばさないこと。

1. PR をマージ（このPR＝`server.*.json` の 0.2.0 化）
2. **本人が `wrangler deploy`**（`cd stock-trading\mcp; npx wrangler deploy`）
3. **本番が8本になっているか機械検査**（レジストリの記載と実体を合わせるため）
4. **本人が publish**（下の B-2。`server.dns.json` を使う）
5. Smithery の説明文を更新（下の「Smithery 用の説明文」）
6. 記録（`引継ぎ.md` §17・`mcp/HANDOFF.md`）

レジストリに「8本」と書いてある状態で本番が4本だと、エージェントが空振りする。
**先に実体を合わせる。**

---

---

## 1. 調べた結果（2026-09-05 時点）

### MCP 公式レジストリ（registry.modelcontextprotocol.io）

現在 **preview 版**（破壊的変更やデータリセットがあり得ると明記されている）。

| 項目 | 結果 |
|---|---|
| リモートHTTPサーバーの登録 | **可能**。`remotes` 配列に `{"type":"streamable-http","url":"..."}` を書く。「URL で公開アクセス可能であること」が必須条件で、うちは満たしている |
| npm 等へのパッケージ公開 | **不要**。パッケージ所有権の検証は `packages` を書いた場合の話。リモートのみの構成には該当しない |
| 必要なファイル | `server.json` 1枚のみ |
| 登録ツール | `mcp-publisher` CLI |
| 審査 | 人手の審査は無し。名前空間の認証が通れば登録される |
| OSSライセンスの公開義務 | **無し**（リポジトリは既に公開だが、要件ではない） |
| ライセンス表記 | 任意 |

**名前空間の認証は2択で、どちらを選ぶかで名前が決まる。**

| 認証方式 | 名前の形 | うちの場合 | DNS変更 |
|---|---|---|---|
| GitHub | `io.github.<username>/*` | `io.github.oo12takemaru-create/ruletrade` | **不要** |
| ドメイン | `<逆引きドメイン>/*` | `jp.ruletrade/mcp` | **必要**（`ruletrade.jp` の**apex**に TXT レコード） |

- **`remotes` の URL は名前空間と一致しなくてよい。** GitHub 認証を選んでも、公開URLは `https://ruletrade.jp/mcp` のままで登録できる。
- DNS 認証の TXT レコードは **apex（`ruletrade.jp` 直下）に置く必要がある**。`_mcp-auth.ruletrade.jp` のようなセレクタ配下では認証が通らないと明記されている。鍵をローテートしたら古いレコードの削除も必要。

### Smithery（smithery.ai）

| 項目 | 結果 |
|---|---|
| 既に他所でホストしているサーバーの掲載 | **可能**。`smithery.ai/new` で公開HTTPS URL を入力するだけ |
| GitHub リポジトリ | **不要** |
| `smithery.yaml` | リモートURL登録の場合は不要 |
| 審査 | 人手の審査は無し。**自動スキャン**が走る（公開サーバーは自動で完了） |
| 認証 | 認証不要のサーバーはそのままスキャンされる。うちは認証不要 |
| 任意 | 登録後に Settings → Verification で公式ベンダー認証のチェックリストを進められる |

---

## 2. 名前空間の決定（2026-09-05 Fable）

**`jp.ruletrade/mcp`（ドメイン認証）に決定。** apex への TXT 追加は許可された。
理由: 起動文の「DNS変更禁止」は 08-31 のネームサーバー移管を指しており、DNSレコード設定画面で TXT を1本足す操作は別物（Resend の DKIM で実績あり）。
以下は判断に使った材料（記録として残す）。

起動文 §3 の「やらないこと」に **`Cloudflare カスタムドメイン・DNS 変更`** があり、§4 の止める条件に
**「レジストリの要件が『独自ドメインの所有確認』を含む」** がある。ドメイン認証はまさにこれに当たる。

| | `io.github.oo12takemaru-create/ruletrade` | `jp.ruletrade/mcp` |
|---|---|---|
| DNS変更 | 不要（起動文の禁止事項に触れない） | **必要**（apex に TXT 1本） |
| 見え方 | GitHubのユーザー名が名前に出る。ブランド名として弱い | ブランドとして自然。ドメイン所有の裏付けが付く |
| 後から変更 | **名前は識別子なので、後で変えると別サーバー扱いになる**。利用者の設定は URL 基準なので実害は小さいが、登録は取り直し |
| リスク | 無し | DNS操作（2026-08-31 に移管失敗の経験あり）。ただし**TXTレコードの追加は移管とは別物で、既存のA/CNAMEには触れない** |

**採用しなかった方（`server.github.json`）は残してある。** ドメイン認証が通らなかった場合の退避先。
ただし**名前は識別子なので、後から切り替えると登録し直しになる**（利用者側は URL 基準なので実害は小さい）。

---

## 3. 登録内容（v0.2.0）

`server.github.json` / `server.dns.json` の2つがある。**`name` 以外は同一**。
実際に publish するのは **`server.dns.json`**（ドメイン認証で登録済みのため）。
`server.github.json` は認証が通らなくなったときの退避先。

- **name**: `jp.ruletrade/mcp`（登録済み・**変えない**。名前は識別子で、変えると別サーバー扱いになる）
- **title**: `Rule Trade - Japanese equity verification layer`
- **description**（英語・**100文字以内**。エージェントの検索は英語で走る）:
  > Verified Japanese equity studies: 50 anomalies, 26 indicators, 12 candlesticks, events, ETF decay.

  98文字。旧版（`Rule matches, market regime, 50 anomalies…`）はツール4本時代のもの。
- **version**: `0.2.0`
- **websiteUrl**: `https://ruletrade.jp/`
- **repository**: `https://github.com/oo12takemaru-create/stock-trading-`（subfolder `mcp`）
- **remotes**: `streamable-http` → `https://ruletrade.jp/mcp`
- **カテゴリ/タグ**（入力欄がある場合）: `finance`, `research`, `japan`, `stocks`, `backtesting`

### Smithery 用の説明文（長い欄がある場所用・v0.2.0）

**英語**

> Returns what happened when published, mechanical rules were applied to Japanese equities.
>
> Eight tools: daily rule matches (one stock, one trading day delayed), market regime with a
> three-axis score, 50 market anomalies tested over 61 years, all 12 classic candlestick patterns
> tested on 1,550 TSE Prime stocks, 26 technical indicators traded exactly as the textbooks
> describe, 27 event types with post-earnings drift and index rebalancing, and twelve years of
> measured decay in leveraged and inverse ETFs.
>
> Results that failed are returned with the same weight as results that passed. All 12 candlestick
> patterns were rejected, including the one with the highest profit factor (3.04) — it flipped sign
> between the first half of the sample and the second.
>
> Statistics and verdicts only: no price series, no financial statements, no stock lists, no
> recommendations. Every response carries a disclaimer. Not investment advice.

**日本語**

> 日本株の「検証層」。公開ルールに機械的に該当した事実と、書籍で行った検証の結果を返します。
>
> ツール8本: 公開ルールの該当銘柄（1件・1営業日遅れ）／地合いの機械判定と3軸スコア／
> ジンクス50本を61年分で検証した結果／酒田五法12本（東証プライム・10年半）／
> テクニカル指標26種（TOPIX500・約50万回の売買）／出来事27種と決算後ドリフト・指数の入替／
> レバレッジ・インバースETFの減価12年。
>
> **効かなかったものも同じ重みで返します。** 酒田五法は12本すべてが採用基準を満たしませんでした
> （最もPFの高い三空叩き込み 3.04 も、期間を前半と後半に分けると符号が反転するため不採用）。
>
> 返すのは判定結果と統計値のみで、価格系列・財務値・銘柄リスト・売買推奨は含みません。
> 全レスポンスに免責を付けています。投資助言ではありません。

---

## 4. 本人向け手順（名前空間が決まってから）

### A. Smithery（先にこちらを勧める。DNS不要・5分・取り消しも容易）

1. https://smithery.ai/new を開く
2. URL 欄に `https://ruletrade.jp/mcp` を入力
3. 表示される公開フローを進める（自動スキャンが走る。認証不要のサーバーなのでそのまま完了する）
4. 説明欄には上の英語 description、日本語欄があれば日本語版を貼る
5. 完了後、掲載ページのURLを控える

### B. MCP 公式レジストリ

**B-1. GitHub 認証にする場合（DNS変更なし）**

```
cd "D:\マイドキュメント\Claude\Projects\stock-trading\mcp\registry"
copy server.github.json server.json
npx @modelcontextprotocol/mcp-publisher login github
```
→ 表示される `https://github.com/login/device` を開き、ターミナルに出たコード（例 `ABCD-1234`）を入力して承認。
「Successfully authenticated!」が出たら:
```
npx @modelcontextprotocol/mcp-publisher publish
```

**B-0. いちばん簡単な方法（2026-09-17 追加）**

```powershell
cd "D:\マイドキュメント\Claude\Projects\stock-trading\mcpegistry"; .\publish.ps1
```

これだけ。スクリプトが

1. `server.dns.json` を `_secrets\server.json` に置く（exe は同じフォルダの `server.json` を読む）
2. publish する内容を表示して **y/N を聞く**（違っていたらここで止められる）
3. 鍵を `_secrets\mcp_registry_private_hex.txt` から読む（**画面には出さない**）
4. login → publish
5. レジストリを叩いて version が変わったか確認

までやる。`name` が前回と違う場合は**実行せずに止まる**（名前は識別子で、
変えると別サーバー扱いになるため）。

詳しい中身は `publish.ps1` を読むこと。下の B-1・B-2 は手で叩く場合の手順。

> ⚠️ **PowerShell 5.1 は BOM 無し UTF-8 を cp932 として読む。**
> `publish.ps1` を編集したら **BOM 付き UTF-8 で保存する**こと。
> BOM が無いと日本語が壊れて構文エラーになる（2026-09-17 に実際に踏んだ）。

**B-2. ドメイン認証（2026-09-05 Fable の決定でこちらを採用）**

**鍵は生成済み。手順は `集客サイト企画/TXT追加手順_MCPレジストリ_2026-09-05.md` にまとめてある。**

- 秘密鍵の置き場: `D:\マイドキュメント\Claude\Projects\集客サイト企画\_secrets\`
  (`mcp_registry_key.pem` と hex 版 `mcp_registry_private_hex.txt`)。
  **この公開リポジトリの中には置かない。** `集客サイト企画` は git 管理外のフォルダ。
- DNS に入れる公開鍵(apex の TXT・ホスト名は空欄): `_secrets/txt_value.txt`
- ⚠️ TXT は **apex に置く**。`_mcp-auth` などセレクタ配下では認証が通らない(SPF と同じ配置。DKIM とは違う)。
- CLI は Homebrew が Windows で使えないので **リリースバイナリ**を使う:
  `https://github.com/modelcontextprotocol/registry/releases/download/v1.8.1/mcp-publisher_windows_amd64.tar.gz`
- `--private-key` は**ファイルパスではなく 64桁の hex**(`--private-key-file` というフラグは存在しない)。

```
copy server.dns.json server.json
.\mcp-publisher.exe login dns --domain=ruletrade.jp --private-key=<64桁のhex>
.\mcp-publisher.exe publish
```

**再publish（v0.1.0 → v0.2.0）のとき**

- `name` は変えない。**同じ名前に新しい version を publish する**のが更新の形
- TXT レコードは 2026-09-05 に入れたものがそのまま使える（鍵をローテートしていなければ）
- publish 後、`https://registry.modelcontextprotocol.io/v0/servers?search=ruletrade` で
  **version が 0.2.0 になっているか**を確認する
- **先に Worker を deploy しておくこと**（上の「再publish の順序」）

※ レジストリは preview 版なので、手順が変わっていないか実行前に確認すること。

### C. 登録後にやること

- `mcp/HANDOFF.md` と `引継ぎ.md` §17 に、登録先・登録名・掲載URLを追記
- 呼び出しログ（`event:"tool_call"` の `client`）を見て、レジストリ経由の流入があるか確認する
- **v0.2.0 では「ツール別×client 別」が取れるようになっている**（2026-09-11 に
  Analytics Engine を有効化・`blob2=tool` / `blob3=client`）。
  集計は `python mcp/tools/aggregate_calls.py`。
  0.1.0 掲載時は 1日59件 → 1,071件（+1,715%）だった。8本化で何が変わるかを同じ尺度で見る

---

## 5. 注意

- **公式レジストリは preview 版**。「破壊的変更やデータリセットがあり得る」と明記されている。登録が消えることもあり得るので、`server.json` はこのフォルダに残しておく（再登録に使う）。
- `key.pem`（ドメイン認証を選んだ場合の秘密鍵）は **絶対にコミットしない**。ルート `.gitignore` の `*.pem` を確認すること。
