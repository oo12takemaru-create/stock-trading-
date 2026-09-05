# レジストリ登録の要件と登録内容の案

作成: 2026-09-05（起動文 `起動文_実務_MCP公開後仕上げ_2026-09-05.md` Step 5）
状態: **未登録。実際の登録操作は本人が行う。**

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

## 2. 判断が必要な点（Fable に相談）

**名前空間をどちらにするか。**

起動文 §3 の「やらないこと」に **`Cloudflare カスタムドメイン・DNS 変更`** があり、§4 の止める条件に
**「レジストリの要件が『独自ドメインの所有確認』を含む」** がある。ドメイン認証はまさにこれに当たる。

| | `io.github.oo12takemaru-create/ruletrade` | `jp.ruletrade/mcp` |
|---|---|---|
| DNS変更 | 不要（起動文の禁止事項に触れない） | **必要**（apex に TXT 1本） |
| 見え方 | GitHubのユーザー名が名前に出る。ブランド名として弱い | ブランドとして自然。ドメイン所有の裏付けが付く |
| 後から変更 | **名前は識別子なので、後で変えると別サーバー扱いになる**。利用者の設定は URL 基準なので実害は小さいが、登録は取り直し |
| リスク | 無し | DNS操作（2026-08-31 に移管失敗の経験あり）。ただし**TXTレコードの追加は移管とは別物で、既存のA/CNAMEには触れない** |

**実務側の所感**: `jp.ruletrade/mcp` の方が資産として良い。TXT レコードの追加は既存レコードを触らないので 08-31 の失敗とは性質が違う。
ただし DNS を触るかどうかは起動文が明示的に禁じているので、**ここは判断を仰ぐ**。

**GitHub 認証で先に登録して後から変える**のは勧めない。名前が識別子なので、登録し直しになる。

---

## 3. 登録内容の案

`server.github.json` / `server.dns.json` の2案を用意した。**`name` 以外は同一**。

- **name**: 上記の2択
- **title**: `Rule Trade — Japanese equity verification layer`
- **description**（英語。エージェントの検索は英語で走る）:
  > Returns what happened when published, mechanical rules were applied to Japanese equities:
  > daily rule matches, market regime, and 50 market anomalies tested over 61 years.
  > Statistics and verdicts only — no price data, no financials, no recommendations.
- **日本語説明**（Smithery の説明欄など、日本語が入る場所用）:
  > 日本株の「検証層」。公開ルールに機械的に該当した事実、地合いの機械判定、
  > ジンクス50本を61年分で検証した結果（判定・勝率・標本数・p値）を返します。
  > 返すのは判定結果と統計値のみで、価格データ・財務値・売買推奨は含みません。投資助言ではありません。
- **version**: `0.1.0`
- **websiteUrl**: `https://ruletrade.jp/`
- **repository**: `https://github.com/oo12takemaru-create/stock-trading-`（subfolder `mcp`）
- **remotes**: `streamable-http` → `https://ruletrade.jp/mcp`
- **カテゴリ/タグ**（入力欄がある場合）: `finance`, `research`, `japan`, `stocks`, `backtesting`

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

**B-2. ドメイン認証にする場合（DNS の TXT を1本足す）**

先に鍵を作って TXT レコードの文字列を出す（**`key.pem` は秘密鍵。git に入れないこと**）:
```
cd "D:\マイドキュメント\Claude\Projects\stock-trading\mcp\registry"
openssl genpkey -algorithm Ed25519 -out key.pem
```
その後、出てきた公開鍵から作った `v=MCPv1; k=ed25519; p=...` という値を
**`ruletrade.jp` の apex に TXT レコードとして追加**する（サブドメインやセレクタ配下ではダメ）。
既存の A / CNAME / MX には触らない。反映を待ってから:
```
copy server.dns.json server.json
npx @modelcontextprotocol/mcp-publisher login dns --domain ruletrade.jp --private-key-file key.pem
npx @modelcontextprotocol/mcp-publisher publish
```

※ 鍵生成と TXT 文字列の組み立てのコマンドは、その時点の公式手順を実務チャットで再確認してから実行すること
（レジストリは preview 版で、手順が変わる可能性がある）。

### C. 登録後にやること

- `mcp/HANDOFF.md` と `引継ぎ.md` §17 に、登録先・登録名・掲載URLを追記
- 呼び出しログ（`event:"tool_call"` の `client`）を見て、レジストリ経由の流入があるか確認する

---

## 5. 注意

- **公式レジストリは preview 版**。「破壊的変更やデータリセットがあり得る」と明記されている。登録が消えることもあり得るので、`server.json` はこのフォルダに残しておく（再登録に使う）。
- `key.pem`（ドメイン認証を選んだ場合の秘密鍵）は **絶対にコミットしない**。ルート `.gitignore` の `*.pem` を確認すること。
