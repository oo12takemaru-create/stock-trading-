# レジストリへ publish する（本人が実行する）
#
#   cd "D:\マイドキュメント\Claude\Projects\stock-trading\mcp\registry"
#   .\publish.ps1
#
# ★秘密鍵はこのファイルにも、画面にも出ない★
# 鍵は git 管理外の _secrets\ から読み、変数のまま mcp-publisher に渡す。
#
# やること
#   1. server.dns.json（このリポジトリの正）を _secrets\server.json に置く
#      （mcp-publisher.exe は同じフォルダの server.json を読む仕様）
#   2. ドメイン認証で login
#   3. publish
#   4. レジストリを叩いて、載った version を確認する
#
# 先に Worker を deploy しておくこと。レジストリに「8本」と書いてある状態で
# 本番が4本だと、エージェントが空振りする。

$ErrorActionPreference = "Stop"

$SecretsDir  = "D:\マイドキュメント\Claude\Projects\集客サイト企画\_secrets"
$RegistryDir = $PSScriptRoot
$Exe         = Join-Path $SecretsDir "mcp-publisher.exe"
$KeyFile     = Join-Path $SecretsDir "mcp_registry_private_hex.txt"
$Src         = Join-Path $RegistryDir "server.dns.json"
$Dst         = Join-Path $SecretsDir "server.json"

# ---- 1. 要るものが揃っているか
foreach ($p in @($Exe, $KeyFile, $Src)) {
  if (-not (Test-Path $p)) { Write-Host "見つかりません: $p" -ForegroundColor Red; exit 1 }
}

# ---- 2. 何を publish するかを見せる（人が見て止められるように）
$new = Get-Content $Src -Raw | ConvertFrom-Json
Write-Host ""
Write-Host "=== これから publish する内容 ===" -ForegroundColor Cyan
Write-Host ("  name       : " + $new.name)
Write-Host ("  version    : " + $new.version)
Write-Host ("  description: " + $new.description)
Write-Host ("  endpoint   : " + $new.remotes[0].url)

if (Test-Path $Dst) {
  $old = Get-Content $Dst -Raw | ConvertFrom-Json
  Write-Host ("  （前回 publish した version: " + $old.version + "）")
  if ($old.name -ne $new.name) {
    Write-Host ""
    Write-Host "name が前回と違います。名前は識別子なので、変えると別サーバー扱いになります。" -ForegroundColor Red
    Write-Host ("  前回: " + $old.name)
    Write-Host ("  今回: " + $new.name)
    exit 1
  }
}
Write-Host ""
$ans = Read-Host "この内容で publish しますか (y/N)"
if ($ans -ne "y") { Write-Host "やめました。"; exit 0 }

# ---- 3. mcp-publisher は同じフォルダの server.json を読む
Copy-Item $Src $Dst -Force
Write-Host "server.json を更新しました。" -ForegroundColor Green

# ---- 4. 鍵を読む（★画面には出さない★）
$key = (Get-Content $KeyFile -Raw).Trim()
if ($key.Length -ne 64) {
  Write-Host "鍵の形が想定と違います（64桁の hex のはず・今: $($key.Length) 文字）" -ForegroundColor Red
  exit 1
}

Push-Location $SecretsDir
try {
  Write-Host ""
  Write-Host "--- login（ドメイン認証） ---" -ForegroundColor Cyan
  & $Exe login dns --domain=ruletrade.jp --private-key=$key
  if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "login に失敗しました。TXT レコードが消えている可能性があります。" -ForegroundColor Red
    Write-Host "確認: nslookup -type=TXT ruletrade.jp 01.dnsv.jp"
    Write-Host "（apex に v=MCPv1; k=ed25519; p=... が1本あるはず）"
    exit 1
  }

  Write-Host ""
  Write-Host "--- publish ---" -ForegroundColor Cyan
  & $Exe publish
  if ($LASTEXITCODE -ne 0) { Write-Host "publish に失敗しました。" -ForegroundColor Red; exit 1 }
}
finally {
  Pop-Location
  # 鍵を変数から消す（この後の履歴に残さない）
  Remove-Variable key -ErrorAction SilentlyContinue
}

# ---- 5. 載ったかを確認する
Write-Host ""
Write-Host "--- レジストリを確認 ---" -ForegroundColor Cyan
Start-Sleep -Seconds 3
try {
  $r = Invoke-RestMethod -Uri "https://registry.modelcontextprotocol.io/v0/servers?search=ruletrade" `
                         -Headers @{ "user-agent" = "ruletrade-publish/0.2" } -TimeoutSec 30
  # ★レスポンスは { servers: [ { server: {...}, _meta: {...} } ] } とネストしている★
  # 直下から name を読むと必ず空振りして「まだ出ていません」と出る（2026-09-18 に実際に出した）
  $hit = $r.servers |
         Where-Object { $_.server.name -eq $new.name } |
         Sort-Object { $_._meta.'io.modelcontextprotocol.registry/official'.updatedAt } -Descending |
         Select-Object -First 1
  if ($null -eq $hit) {
    Write-Host "レジストリにまだ出ていません（反映待ちのことがあります。数分後に再確認してください）" -ForegroundColor Yellow
  } else {
    $meta = $hit._meta.'io.modelcontextprotocol.registry/official'
    Write-Host ("  name    : " + $hit.server.name)
    Write-Host ("  version : " + $hit.server.version)
    Write-Host ("  status  : " + $meta.status)
    Write-Host ("  isLatest: " + $meta.isLatest)
    Write-Host ("  updated : " + $meta.updatedAt)
    if ($hit.server.version -eq $new.version) {
      Write-Host ""
      Write-Host "OK  レジストリが $($new.version) になりました。" -ForegroundColor Green
    } else {
      Write-Host ""
      Write-Host "まだ $($hit.server.version) です。反映に少しかかることがあります。" -ForegroundColor Yellow
    }
  }
} catch {
  Write-Host "確認に失敗しました（publish 自体は成功している可能性があります）: $_" -ForegroundColor Yellow
  Write-Host "手で確認: https://registry.modelcontextprotocol.io/v0/servers?search=ruletrade"
}

Write-Host ""
Write-Host "次: Smithery の説明文を更新（文案は registry\README.md）"
