# レジストリ登録の要件と登録内容の案

作成: 2026-09-05（起動文 `起動文_実務_MCP公開後仕上げ_2026-09-05.md` Step 5）
状態: **未登録。名前空間は `jp.ruletrade/mcp`（ドメイン認証）に決定（2026-09-05 Fable）。鍵は生成済み。TXT追加・認証・登録は本人が行う。**

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

※ レジストリは preview 版なので、手順が変わっていないか実行前に確認すること。

### C. 登録後にやること

- `mcp/HANDOFF.md` と `引継ぎ.md` §17 に、登録先・登録名・掲載URLを追記
- 呼び出しログ（`event:"tool_call"` の `client`）を見て、レジストリ経由の流入があるか確認する

---

## 5. 注意

- **公式レジストリは preview 版**。「破壊的変更やデータリセットがあり得る」と明記されている。登録が消えることもあり得るので、`server.json` はこのフォルダに残しておく（再登録に使う）。
- `key.pem`（ドメイン認証を選んだ場合の秘密鍵）は **絶対にコミットしない**。ルート `.gitignore` の `*.pem` を確認すること。
