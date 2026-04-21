# 📊 日本株トレード統合システム v2.7.3

BNF + Minervini + MOMENTUM の統合戦略を、過去10年データで実証最適化した日本株トレードシステム。

## 🎯 主要指標(10年バックテスト)

| 指標 | 数値 |
|---|---|
| CAGR(年率リターン) | **+144.1%** |
| 勝率 | 59.3% |
| プロフィットファクター | 2.41 |
| 最大ドローダウン | -28.0% |
| Calmar Ratio | 5.1 |
| Sharpe Ratio(推定) | ~2.0 |
| 総トレード数 | 2,236 |
| シグナル頻度 | 月 18.9 件 |

> ⚠️ バックテスト結果は過去データで、将来の利益を保証しません。

## 📁 ファイル構成

### 🎯 実運用ツール

| ファイル | 用途 |
|---|---|
| `daily_scanner_v2_7_3.py` | 毎日実行するシグナルスキャナー(GitHub Actions対応) |
| `trade_recorder.py` | トレード結果を記録するツール |
| `trading_guide_v2.md` | SBI証券での実践マニュアル |

### 🔬 バックテスト・分析ツール

| ファイル | 用途 |
|---|---|
| `integrated_backtest_v2_7_3.py` | 最新統合バックテスト(最終形) |
| `integrated_backtest_v2_7_2.py` | 実証閾値版(地合い倍率過剰) |
| `integrated_backtest_v2_7_1.py` | BNF復活版(リスク管理緩和) |
| `integrated_backtest_v2_7.py` | リスク管理強化版 |
| `integrated_backtest_v2_6.py` | 真BNF流(セクター別閾値) |
| `integrated_backtest_v2_5.py` | 弱点戦略改善版 |
| `integrated_backtest_v2_4.py` | サーキットブレーカー初期版 |
| `sector_threshold_analyzer.py` | セクター別閾値の実証検証 |
| `cost_tax_analyzer.py` | 手数料・税金適用後の現実値計算 |
| `stop_loss_simulator.py` | 損切りロジック改善のシミュレーション |

### ⚙️ GitHub Actions 自動実行

| ファイル | 用途 |
|---|---|
| `.github/workflows/daily-signal.yml` | 毎日3回(朝・昼・夕)シグナル自動生成 |

## 🚀 GitHub Actions 自動実行

このリポジトリを GitHub に置くと、以下のスケジュールで自動実行されます:

- **🌅 朝 08:00 JST** (月〜金) — 寄付き前にシグナル確認
- **☀️ 昼 12:00 JST** (月〜金) — 前場クローズ後の状況確認
- **🌙 夕 18:00 JST** (月〜金) — 後場クローズ後の翌日準備

結果は GitHub Issues に自動投稿され、設定によりメール通知も来ます。

### 手動実行

GitHub の「Actions」タブから `Daily Trading Signal` → `Run workflow` で任意のタイミング実行可能。資金額とリスク%もそこで変更できます。

## 💻 ローカル実行

```bash
# 依存ライブラリをインストール
pip install -r requirements.txt

# シグナルスキャナー実行
python daily_scanner_v2_7_3.py --capital 1000000 --risk 1.0

# バックテスト実行
python integrated_backtest_v2_7_3.py --years 10 --mode daily --max-concurrent 20 --compound --circuit-breaker --chart

# コスト適用後の現実値を確認
python cost_tax_analyzer.py --csv integrated_backtest_v2_7_3_daily_result.csv

# セクター閾値の実証検証
python sector_threshold_analyzer.py --years 10
```

## 🎯 戦略ロジック

### 3つの戦略が相場環境で切り替わる

| 相場環境 | 使う戦略 |
|---|---|
| 🚀 BULLISH | MOMENTUM(主力) + MINERVINI(押し目) |
| ⚖️ NEUTRAL | BNF-LITE + MINERVINI |
| ⚠️ BEARISH | BNF-LITE のみ |
| 🚨 PANIC | BNF-LITE のみ(HALT貫通、リスク半減) |

### BNF-LITE(逆張り)

25日移動平均線乖離率でエントリー。**セクター別 × 地合い別**の閾値を使用:

| セクター例 | 基準閾値 |
|---|---|
| 銀行・商社・電子部品・自動車 | -22% |
| 半導体製造装置 | -22% |
| 電機・投資会社・人材 | -18% |
| 医薬品・総合電機 | -15% |
| ECファッション・IT・小売 | -12% |
| ゲーム・モーター・空運 | -10% |
| ITサービス・重工業・保険 | -8% |

地合い倍率: BULLISH×0.8 / NEUTRAL×1.0 / BEARISH×1.15 / PANIC×1.3

### MOMENTUM(順張り)

20日高値ブレイクアウト + 出来高1.5倍。200MA・50MA上の強気銘柄のみ。BULLISH相場でのみ発動。

### MINERVINI(成長株)

Trend Template 8条件通過銘柄が、VCP(Volatility Contraction Pattern)ブレイクアウト または MA50押し目で反発した時にエントリー。ストップ -9%、+25%で半分利確。

## 🛡 サーキットブレーカー

以下のいずれかで HALT 発動(MOMENTUM/MINERVINI停止、BNF-LITEのみ継続):

- VIX > 35(極度のパニック)
- 日経1ヶ月変化率 < -15%(急落)
- 5連敗(手法不適合期)

クールダウン期間は5営業日。

## ⚠️ 実運用の注意事項

### 資金管理
- 最大DD -28% から逆算して、**一時的に口座が28%減ることを許容できる金額**で運用
- 生活費とは別の余裕資金のみを使う
- 最低50万円、推奨100〜300万円スタート

### メンタル準備
- 最大24〜26連敗(約1.5ヶ月負け続ける)局面あり
- バックテスト通りに続けられるか自問してから開始
- ペーパートレード1〜2ヶ月を推奨

### SBI証券について
- 個人向けAPIがないため手動発注
- 毎朝のシグナルを見て指値・逆指値を手動で仕込む
- 約定通知もスマホで確認

## 📜 ライセンス

Personal use only. 商用利用は別途許可が必要です。

## 🙏 謝辞

- **BNF氏(小手川隆氏)** — 逆張り手法の哲学
- **Mark Minervini** — Trend Template と VCP
- **Jesse Livermore** — ピボタル・ポイント理論
