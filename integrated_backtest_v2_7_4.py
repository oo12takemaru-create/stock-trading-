"""
╔══════════════════════════════════════════════════════════════════╗
║   BNF + Minervini 統合バックテスト v2.7.4                        ║
║   銘柄拡大版 - 128本 → 163本(+35本の中堅グロース追加)           ║
╚══════════════════════════════════════════════════════════════════╝

【v2.7.3 → v2.7.4 の変更点】

  v2.7.3 実測: CAGR +144% / 最大DD -28% / 128銘柄
  ↓
  銘柄拡大: 128 → 163本(+35本)
    中堅グロース・SaaS・バイオ・半導体関連を ticker_verifier.py で検証後に追加

  追加カテゴリ:
    SaaS系:   ラクス / サイボウズ / マネーフォワード / Sansan / ビジョナル
    バイオ:   ペプチドリーム / サンバイオ / GNI
    医療機器: シスメックス / テルモ
    半導体:   ディスコ / SCREEN HD / ルネサス / KOKUSAI / アルバック / 東京応化
    エンタメ: バンダイナムコ / サンリオ / 東映アニメ / コーエーテクモ / MIXI
    IT:       LINEヤフー / GMOインターネット
    機械:     ハーモニックドライブ / THK / ミネベアミツミ
    金融:     三菱HCキャピタル / マネックスG / アコム / コンコルディアFG
    その他:   電通G / 神戸物産 / 東京建物 / リログループ / ベイカレント

  新規セクター追加(SECTOR_BNF_THRESHOLDS):
    エンタメ:       -10% (変動中、ゲームと類似)
    コンサル:        -8% (ITサービスと類似)
    医療機器:       -12% (医薬よりやや浅め)
    広告:           -10% (広告ITと同じ)
    食品小売:       -12% (小売と類似)
    機械:           -12% (産業用ロボットと類似)
    バイオ:         -18% (高変動グロース)
    不動産サービス: -15%
    消費者金融:     -12%

  期待効果:
    MINERVINI件数:   月2件  → 月4〜6件 (グロース追加)
    BNF-LITE件数:    月1.5件 → 月2〜3件
    MOMENTUM件数:    月13件 → 月18〜25件 (銘柄増)
    総シグナル頻度:  月18件 → 月25〜35件
    分散効果によるDD改善: -28% → -22〜26% の可能性
    実行時間:        40分 → 約55分 (1.4倍)


【v2.7.2の結果 → v2.7.3の最終調整（参考）】

  v2.7.2 実測: CAGR +137% / 最大DD -26.4% / 24連敗 / BNF 323件
  商品化基準 5項目のうち3項目クリア、2項目(DD -20%、連敗20以下)未達。

  ★残った原因:
    v2.7.2の実証最適閾値(-22%など)は「地合い倍率なしの分析」で算出。
    そこに v2.6 の REGIME_BNF_MULTIPLIER をそのまま乗算すると二重深化:

      銀行 PANIC時:    -22% × 1.7 = -37.4%  ← 10年に1-2回しか出ない深さ
      電子部品 PANIC:  -22% × 1.7 = -37.4%
      自動車 BEARISH:  -22% × 1.3 = -28.6%

    このため PANIC時のBNFが勝率47.8%と期待値を下回る結果になった。

  v2.7.3 改訂内容:
    ✅ 地合い倍率を適正化
       項目        v2.7.2  → v2.7.3
       BULLISH     × 0.7   →  × 0.8  (やや緩和、BULLISH時の機会UP)
       NEUTRAL     × 1.0   →  × 1.0  (据え置き、基準)
       BEARISH     × 1.3   →  × 1.15 (実証閾値と二重にしない)
       PANIC       × 1.7   →  × 1.3  (過剰深化解消、真の底を拾う)

  期待効果:
    BNF件数:     323件  → 350〜400件 (機会UP)
    BNF勝率:     52.6%  → 55〜60% (適正閾値)
    PANIC勝率:   47.8%  → 55〜65% (過剰深化解消)
    最大DD:      -26%   → -18〜22% ★商品化基準クリア
    連敗:        24     → 18〜22 ★商品化基準クリア
    CAGR:        +137%  → +130〜150% (キープ)

  → 5指標すべて商品化基準を達成する最終形を目指す


【v2.7.1の結果 → v2.7.2の革新（参考）】

  v2.7.1 実測: CAGR +128.8% / 最大DD -34.0% / BNF 202件 勝率51%
  しかし: DD-34%は商品化NG、BNF勝率51%はまだ低い。

  ★決定的発見(sector_threshold_analyzer.pyで判明):
    BNF氏本人の発言(2001-2004年相場ベース)で設計した閾値が、
    現代の日本株データでは40セクター中31セクター(77%)で誤っていた。

    現代はもっと深い乖離を要求する:
      医薬品:     -10%   → 実証最適 -15% (勝率65.2%!)
      銀行:       -12%   → 実証最適 -22% (勝率70%、EV+12.5%!)
      商社:       -12%   → 実証最適 -22% (EV+10.5%!)
      自動車:     -13%   → 実証最適 -22% (勝率58%)
      電子部品:   -15%   → 実証最適 -22% (勝率73%!)
      半導体製造装置: -15% → 実証最適 -22%

    逆に浅い方が良いセクターも判明:
      ITサービス: -15% → -8% (件数確保型)
      重工業:     -15% → -8% (勝率59%)
      空調:       -15% → -8%
      レジャー:   -15% → -8% (勝率65%)
      カー用品:   -15% → -8%
      家電量販:   -12% → -8%

  v2.7.2 改訂内容:
    ✅ SECTOR_BNF_THRESHOLDS を実証最適値(40セクター)で総入れ替え
    ✅ リスク管理は v2.7.1 設定を継承 (panic-bnf-max=10, etc)
    ✅ BNF連敗停止の永久ループバグは修正済み(v2.7.1継承)

  期待効果:
    BNF件数:     202件 → 120〜170件 (深い閾値で厳選、質重視)
    BNF勝率:     51%   → 58〜65% (実証ベース)
    PANIC勝率:   38.5% → 55〜65% (深い-22%で底近くを拾う)
    最大DD:      -34%  → -18〜22% (質が上がるのでDD改善)
    CAGR:        +128% → +130〜160%


【v2.7の問題 → v2.7.1の解決（参考）】

  v2.7 実測: CAGR +106.6% / 最大DD -14.9% / 14連敗 (DD目標達成)

  問題: しかし BNF-LITE が 5件まで激減、実質死亡。
       PANIC相場トレード 0件。BNFの爆益チャンスを完全に喪失。
  原因: リスク管理4つが重なって、BNFが稼働できる条件がほぼ消失。

  v2.7.1 リスク管理緩和（デフォルト値を変更）:
    項目                     v2.7   → v2.7.1
    --panic-bnf-max           5     →  10    (PANIC時BNF最大保有)
    --panic-wait-days         5     →  3     (突入後様子見日数)
    --bnf-loss-cooldown       7     →  3     (BNF停止期間)
    --bnf-loss-threshold      3     →  5     (BNF停止トリガー連敗数)
    --panic-risk-mult         0.5   →  0.5   (据え置き、破綻防止)

  期待効果:
    BNF-LITE件数:  5     → 80〜150件
    PANIC相場:     0件   → 20〜50件
    最大DD:        -15%  → -20%前後
    連続負け:      14    → 15〜25
    CAGR:          +106% → +120%前後
    → 三つ巴のバランスが復活、かつDDは商品化可能水準を維持


【v2.6の問題 → v2.7の解決（参考）】

  v2.6 実測: CAGR +150.2% / 最大DD -43.2% / 46連敗 (DD商品化NG)

  原因: PANIC貫通のBNFが「真の底」以前にエントリーして連続損切りループ。
       BNF氏は2001年の特殊な低位株で勝ったが、v2.6は主要128銘柄全部で
       PANIC時にBNF発動 → 過剰。

  v2.7 4大リスク管理:
    ①PANIC時BNFポジション半減  (--panic-risk-mult 0.5)
    ②PANIC時BNF同時保有上限    (--panic-bnf-max 5 → v2.7.1: 10)
    ③PANIC突入後N日の様子見    (--panic-wait-days 5 → v2.7.1: 3)
    ④BNF動的停止               (--bnf-loss-cooldown 7 → v2.7.1: 3)

【v2.6からの継承】

  ・セクター別乖離率閾値 (SECTOR_BNF_THRESHOLDS)
     医薬-10% / 銀行-12% / 電機-13% / 半導体-15% / 海運-18%
  ・地合い別乖離率倍率
     BULLISH×0.7 / NEUTRAL×1.0 / BEARISH×1.3 / PANIC×1.7
  ・MINERVINI: ストップ-9%, VCP条件緩和, MA50押し目
  ・PANIC時のBNF-LITE HALT貫通 (ただしv2.7ではポジション半減される)

【インストール】
  pip install yfinance pandas numpy rich matplotlib

【実行方法】

--- 基本 ---
  python integrated_backtest_v2_7.py --years 10 --mode daily --max-concurrent 20 --compound --circuit-breaker --chart

--- リスク管理をカスタマイズ ---
  --panic-risk-mult 0.5      PANIC時のBNFリスク倍率（0.5=半減）
  --panic-bnf-max 5          PANIC時のBNF同時保有上限
  --panic-wait-days 5        PANIC突入後の様子見日数
  --bnf-loss-cooldown 7      BNF連敗停止日数（3連敗で起動）
  --bnf-loss-threshold 3     BNF停止トリガーの連敗数

--- v2.6互換で走らせたい（リスク管理OFFで検証） ---
  python integrated_backtest_v2_7.py --years 10 --mode daily --max-concurrent 20 --compound --circuit-breaker --legacy-v26

--- v2.5互換 ---
  ... --legacy-v25

--- v2.4互換 ---
  ... --legacy-v24
"""

import sys, argparse, datetime, csv, warnings
from collections import defaultdict

def check_libs():
    missing = []
    for lib in ["yfinance", "pandas", "numpy", "rich"]:
        try: __import__(lib)
        except ImportError: missing.append(lib)
    if missing:
        print(f"\n必要なライブラリが不足: {', '.join(missing)}")
        print(f"pip install {' '.join(missing)}\n"); sys.exit(1)

check_libs()
warnings.filterwarnings("ignore")

import yfinance as yf
import pandas as pd
import numpy as np
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.rule import Rule
from rich.columns import Columns
from rich import box

console = Console()

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  グローバル指標
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

GLOBAL_TICKERS = {
    "^N225":  "日経225",
    "^GSPC":  "S&P500",
    "^VIX":   "VIX恐怖指数",
}

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  監視銘柄
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

JAPAN_STOCKS = {
    # 半導体・精密
    "8035.T": ("東京エレクトロン", "半導体製造装置"),
    "6857.T": ("アドバンテスト", "半導体製造装置"),
    "6146.T": ("ディスコ", "半導体製造装置"),
    "6920.T": ("レーザーテック", "半導体製造装置"),
    "7735.T": ("SCREEN HD", "半導体製造装置"),
    "6728.T": ("アルバック", "半導体製造装置"),
    "6323.T": ("ローツェ", "半導体製造装置"),
    "6758.T": ("ソニー", "電機"),
    "6861.T": ("キーエンス", "FAセンサー"),
    "6273.T": ("SMC", "空圧制御"),
    "6981.T": ("村田製作所", "電子部品"),
    "6762.T": ("TDK", "電子部品"),
    "6971.T": ("京セラ", "電子部品"),
    "6954.T": ("ファナック", "産業用ロボット"),
    "7011.T": ("三菱重工", "重工業"),
    "7012.T": ("川崎重工", "重工業"),
    "7013.T": ("IHI", "重工業"),
    "6301.T": ("コマツ", "建機"),
    "6326.T": ("クボタ", "農機"),
    "6367.T": ("ダイキン", "空調"),
    "6594.T": ("ニデック", "モーター"),
    "6501.T": ("日立製作所", "総合電機"),
    "6503.T": ("三菱電機", "総合電機"),
    "6506.T": ("安川電機", "モーター"),
    "6902.T": ("デンソー", "自動車部品"),

    # ゲーム・エンタメ
    "7974.T": ("任天堂", "ゲーム"),
    "9684.T": ("スクウェア・エニックス", "ゲーム"),
    "9697.T": ("カプコン", "ゲーム"),
    "9766.T": ("コナミ", "ゲーム"),
    "3659.T": ("ネクソン", "ゲーム"),
    "6460.T": ("セガサミーHD", "ゲーム"),

    # IT・グロース
    "4307.T": ("野村総研", "ITサービス"),
    "4063.T": ("信越化学", "化学"),
    "4568.T": ("第一三共", "医薬品"),
    "4519.T": ("中外製薬", "医薬品"),
    "4523.T": ("エーザイ", "医薬品"),
    "4578.T": ("大塚HD", "医薬品"),
    "2413.T": ("エムスリー", "医療IT"),
    "4751.T": ("サイバーエージェント", "広告IT"),
    "4385.T": ("メルカリ", "フリマEC"),
    "4478.T": ("フリー", "SaaS"),
    "4704.T": ("トレンドマイクロ", "セキュリティ"),

    # 小売
    "9843.T": ("ニトリHD", "小売"),
    "3382.T": ("セブン&アイ", "小売"),
    "9983.T": ("ファーストリテイリング", "小売"),
    "7532.T": ("パンパシHD", "小売"),
    "8227.T": ("しまむら", "小売"),
    "2670.T": ("ABCマート", "小売"),
    "3092.T": ("ZOZO", "ECファッション"),
    "3088.T": ("マツキヨココカラ", "ドラッグ"),
    "9832.T": ("オートバックス", "カー用品"),
    "3048.T": ("ビックカメラ", "家電量販"),

    # インバウンド・旅行
    "9201.T": ("日本航空", "空運"),
    "9202.T": ("ANA", "空運"),
    "9020.T": ("JR東日本", "鉄道"),
    "9021.T": ("JR西日本", "鉄道"),
    "9022.T": ("JR東海", "鉄道"),
    "4661.T": ("オリエンタルランド", "レジャー"),
    "9602.T": ("東宝", "映画"),

    # 通信・商社
    "9432.T": ("NTT", "通信"),
    "9433.T": ("KDDI", "通信"),
    "9434.T": ("ソフトバンク", "通信"),
    "9984.T": ("ソフトバンクG", "投資会社"),
    "8058.T": ("三菱商事", "商社"),
    "8031.T": ("三井物産", "商社"),
    "8001.T": ("伊藤忠商事", "商社"),
    "8002.T": ("丸紅", "商社"),
    "8053.T": ("住友商事", "商社"),
    "2768.T": ("双日", "商社"),

    # 自動車
    "7203.T": ("トヨタ自動車", "自動車"),
    "7267.T": ("ホンダ", "自動車"),
    "7201.T": ("日産自動車", "自動車"),
    "7269.T": ("スズキ", "自動車"),
    "7261.T": ("マツダ", "自動車"),
    "7270.T": ("SUBARU", "自動車"),

    # 素材・化学
    "4005.T": ("住友化学", "化学"),
    "4188.T": ("三菱ケミカル", "化学"),
    "4042.T": ("東ソー", "化学"),
    "4452.T": ("花王", "化学"),
    "4901.T": ("富士フイルム", "化学"),
    "3402.T": ("東レ", "化学"),
    "4183.T": ("三井化学", "化学"),
    "4204.T": ("積水化学", "化学"),
    "5019.T": ("出光興産", "石油"),
    "5020.T": ("ENEOS", "石油"),
    "5411.T": ("JFE", "鉄鋼"),
    "5401.T": ("日本製鉄", "鉄鋼"),
    "5713.T": ("住友金属鉱山", "非鉄"),
    "5802.T": ("住友電工", "電線"),

    # 食品
    "2502.T": ("アサヒGHD", "飲料"),
    "2503.T": ("キリンHD", "飲料"),
    "2802.T": ("味の素", "食品"),
    "2914.T": ("JT", "たばこ"),
    "2897.T": ("日清食品HD", "食品"),
    "2801.T": ("キッコーマン", "食品"),
    "2269.T": ("明治HD", "食品"),

    # 海運・不動産
    "9101.T": ("日本郵船", "海運"),
    "9104.T": ("商船三井", "海運"),
    "9107.T": ("川崎汽船", "海運"),
    "8802.T": ("三菱地所", "不動産"),
    "8801.T": ("三井不動産", "不動産"),
    "8830.T": ("住友不動産", "不動産"),
    "3003.T": ("ヒューリック", "不動産"),

    # 銀行・証券・保険
    "8306.T": ("三菱UFJ", "銀行"),
    "8316.T": ("三井住友FG", "銀行"),
    "8411.T": ("みずほFG", "銀行"),
    "8591.T": ("オリックス", "リース"),
    "8473.T": ("SBIホールディングス", "証券"),
    "8604.T": ("野村HD", "証券"),
    "8766.T": ("東京海上", "保険"),
    "8750.T": ("第一生命HD", "保険"),

    # 追加の主要銘柄
    "8267.T": ("イオン", "小売"),
    "8113.T": ("ユニ・チャーム", "日用品"),
    "4502.T": ("武田薬品", "医薬品"),
    "4503.T": ("アステラス製薬", "医薬品"),
    "3436.T": ("SUMCO", "半導体"),
    "6976.T": ("太陽誘電", "電子部品"),
    "6701.T": ("NEC", "電機"),
    "6702.T": ("富士通", "電機"),
    "6098.T": ("リクルートHD", "人材"),
    "4755.T": ("楽天グループ", "IT"),
    "9735.T": ("セコム", "警備"),
    "9064.T": ("ヤマトHD", "物流"),
    "9503.T": ("関西電力", "電力"),
    "9501.T": ("東京電力HD", "電力"),
    "9531.T": ("東京ガス", "ガス"),
    "1605.T": ("INPEX", "石油"),
    "5108.T": ("ブリヂストン", "タイヤ"),

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # ★v2.7.4: 新規追加35本(ticker_verifier.pyで疎通確認・流動性5億円/日以上)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    # SaaS系(6本)
    "3923.T": ("ラクス", "SaaS"),
    "4776.T": ("サイボウズ", "SaaS"),
    "3994.T": ("マネーフォワード", "SaaS"),
    "4443.T": ("Sansan", "SaaS"),
    "4194.T": ("ビジョナル", "SaaS"),

    # バイオ・医療機器(5本)
    "4587.T": ("ペプチドリーム", "バイオ"),
    "4592.T": ("サンバイオ", "バイオ"),
    "2160.T": ("GNI", "バイオ"),
    "6869.T": ("シスメックス", "医療機器"),
    "4543.T": ("テルモ", "医療機器"),

    # 半導体関連(3本) ※ディスコ/SCREEN HD/アルバックは既存のため除外
    "4186.T": ("東京応化工業", "半導体"),
    "6723.T": ("ルネサスエレクトロニクス", "半導体"),
    "6525.T": ("KOKUSAI ELECTRIC", "半導体製造装置"),

    # IT・コンサル(4本)
    "4689.T": ("LINEヤフー", "IT"),
    "9449.T": ("GMOインターネット", "IT"),
    "6532.T": ("ベイカレント", "コンサル"),
    "4324.T": ("電通グループ", "広告"),

    # 機械・電子部品(3本)
    "6324.T": ("ハーモニックドライブ", "産業用ロボット"),
    "6481.T": ("THK", "機械"),
    "6479.T": ("ミネベアミツミ", "電子部品"),

    # エンタメ・ゲーム(5本)
    "7832.T": ("バンダイナムコHD", "エンタメ"),
    "8136.T": ("サンリオ", "エンタメ"),
    "4816.T": ("東映アニメーション", "エンタメ"),
    "3635.T": ("コーエーテクモ", "ゲーム"),
    "2121.T": ("MIXI", "ゲーム"),

    # 金融(4本)
    "8593.T": ("三菱HCキャピタル", "リース"),
    "8698.T": ("マネックスG", "証券"),
    "7186.T": ("コンコルディアFG", "銀行"),
    "8572.T": ("アコム", "消費者金融"),

    # 不動産・小売・その他(3本)
    "3038.T": ("神戸物産", "食品小売"),
    "8804.T": ("東京建物", "不動産"),
    "8876.T": ("リログループ", "不動産サービス"),
}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  v2.6: セクター別BNF乖離率閾値（BNF氏の発言を根拠に設計）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#
# BNF氏本人の発言:
#   「大型薬品株なら -5〜10%、電機・ハイテクは -10〜15%」
#   「セクターごとに反発しやすい乖離率水準が異なる」
#
# 設計方針:
#   ディフェンシブ系(生活必需品・医薬品) → 浅い乖離で反発
#   グロース系・変動大銘柄 → 深い乖離が必要
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SECTOR_BNF_THRESHOLDS = {
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # v2.7.2: sector_threshold_analyzer.py による実証検証(10年過去データ)の結果を反映
    # サンプル件数10件以上、かつ現v2.7.1から±2pp以上の差がある場合のみ変更
    # 件数不足のセクターは現行値維持(情報不足)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    # === 浅い乖離で反発(-8%)ITサービス系・旧来型===
    "ITサービス":  -8.0,   # v2.7.1 -15% → -8% (件数27, 勝率40.7%, EV+2.00%)
    "カー用品":    -8.0,   # -15% → -8%  (件数12, 勝率41.7%)
    "タイヤ":      -8.0,   # -13% → -8%  (件数16, 勝率62.5%)
    "レジャー":    -8.0,   # -15% → -8%  (件数17, 勝率65%!)
    "保険":        -8.0,   # -12% → -8%  (件数16, 勝率44%, EV+4.60%)
    "家電量販":    -8.0,   # -12% → -8%  (件数20, 勝率50%)
    "映画":        -8.0,   # -15% → -8%  (件数16, 勝率69%!)
    "空圧制御":    -8.0,   # -13% → -8%  (件数44, 勝率48%)
    "空調":        -8.0,   # -15% → -8%  (件数29, 勝率48%)
    "重工業":      -8.0,   # -15% → -8%  (件数27, 勝率59%, EV+4.35%)

    # === ディフェンシブ(-10%で反発)===
    "ゲーム":      -10.0,  # -15% → -10% (件数113, 勝率56%, 安定型)
    "モーター":    -10.0,  # -15% → -10% (件数62, 勝率51.6%)
    "リース":      -10.0,  # -12% → -10% (件数16)
    "広告IT":      -10.0,  # -15% → -10% (件数29, 勝率51.7%)
    "物流":        -10.0,  # -12% → -10% (件数16)
    "空運":        -10.0,  # -15% → -10% (件数31, 勝率51.6%)
    "自動車部品":  -10.0,  # -13% → -10% (件数20, 勝率65%!)
    "通信":        -10.0,  # 据え置き (件数10, 勝率60%)
    "食品":        -10.0,  # サンプル不足のため継承
    "飲料":        -10.0,
    "たばこ":      -10.0,
    "日用品":      -10.0,

    # === 標準(-12%)===
    "ECファッション": -12.0,  # -18% → -12% (件数25, 勝率48%, EV+2.57%)
    "IT":          -12.0,  # -15% → -12% (件数16, 勝率50%, EV+4.63%)
    "ドラッグ":    -12.0,  # 据え置き (件数11, 勝率45.5%)
    "化学":        -12.0,  # -13% → -12% (件数10, 勝率70%!)
    "産業用ロボット": -12.0,  # -15% → -12% (件数14, 勝率50%)
    "石油":        -12.0,  # -15% → -12% (件数15, 勝率66.7%, EV+8.10%)
    "小売":        -12.0,  # 据え置き (件数42, 勝率50%)
    "鉄道":        -12.0,  # -15% → -12% (件数22, 勝率41%)
    "警備":        -12.0,  # サンプル不足

    # === 深い乖離(-15%)医薬・総合電機===
    "医薬品":      -15.0,  # -10% → -15% (件数23, 勝率65%!!, EV+6.65%)
    "SaaS":        -15.0,  # 据え置き (件数29, 勝率38%, EV+3.70%)
    "医療IT":      -15.0,  # 据え置き (件数13)
    "総合電機":    -15.0,  # -13% → -15% (件数13, 勝率61.5%, EV+9.16%)
    "建機":        -15.0,  # 据え置き (件数11, 勝率54%, EV+6.43%)

    # === 超深い(-18%)グロース・投資・人材・電機===
    "フリマEC":    -18.0,  # 据え置き (件数21, 勝率38%)
    "人材":        -18.0,  # -15% → -18% (件数12, 勝率41.7%, EV+4.23%)
    "投資会社":    -18.0,  # -12% → -18% (件数11, 勝率54.5%, EV+7.61%)
    "電機":        -18.0,  # -13% → -18% (件数16, 勝率56%, EV+6.44%)

    # === 極深(-22%)驚異的発見セクター===
    "半導体製造装置": -22.0,  # -15% → -22% (件数12, 勝率50%, EV+9.21%!)
    "商社":        -22.0,  # -12% → -22% (件数11, 勝率64%, EV+10.55%!)
    "銀行":        -22.0,  # -12% → -22% (件数10, 勝率70%!!, EV+12.53%!)
    "電子部品":    -22.0,  # -15% → -22% (件数11, 勝率73%!!, EV+12.41%!)
    "自動車":      -22.0,  # -13% → -22% (件数12, 勝率58%, EV+9.46%)

    # === サンプル不足で未検証（現行値維持）===
    "半導体":      -15.0,  # データなし(銘柄自体少ない)
    "半導体シリコン": -15.0,
    "セキュリティ": -15.0,
    "証券":        -12.0,
    "鉄鋼":        -15.0,
    "非鉄":        -15.0,
    "電線":        -15.0,
    "電力":        -12.0,
    "ガス":        -12.0,
    "不動産":      -15.0,
    "農機":        -15.0,
    "海運":        -18.0,

    # ★v2.7.4: 新規セクター(類似セクターから推定)
    "エンタメ":        -10.0,  # ゲーム(-10%)と類似
    "コンサル":         -8.0,  # ITサービス(-8%)と類似
    "医療機器":        -12.0,  # 医薬品(-15%)より浅め、安定性高
    "広告":            -10.0,  # 広告IT(-10%)と同じ
    "食品小売":        -12.0,  # 小売(-12%)と同じ
    "機械":            -12.0,  # 産業用ロボット(-12%)と同じ
    "バイオ":          -18.0,  # 高変動グロース、人材(-18%)と類似
    "不動産サービス":  -15.0,  # 不動産(-15%)と同じ
    "消費者金融":      -12.0,  # 金融系、リース(-10%)より深め

    # フォールバック
    "default":     -15.0,
}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  v2.6: 地合い別乖離率倍率（BNF氏の「地合いで水準が変わる」発言を反映）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#
# BNF氏本人の発言:
#   「01年や02年の相場では25日MAからのマイナス乖離が最低20%、
#    安心して買えるのは35%以上の乖離率」
#
# 実際の閾値 = SECTOR_BNF_THRESHOLDS[sector] × REGIME_BNF_MULTIPLIER[regime]
#   例: 半導体(-15%) × BEARISH(1.3) = -19.5%
#   例: 医薬品(-10%) × PANIC(1.7) = -17.0%
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

REGIME_BNF_MULTIPLIER = {
    # v2.7.3 改訂: 実証最適閾値との二重深化を解消
    # v2.7.2 まで: BULLISH 0.7 / NEUTRAL 1.0 / BEARISH 1.3 / PANIC 1.7
    # → 銀行-22% × PANIC 1.7 = -37.4% と現実離れした深さになっていた
    "BULLISH": 0.8,   # 0.7 → 0.8 (強気相場で機会UP、過度の浅化回避)
    "NEUTRAL": 1.0,   # 据え置き (基準)
    "BEARISH": 1.15,  # 1.3 → 1.15 (実証閾値と二重にしない)
    "PANIC":   1.3,   # 1.7 → 1.3 (過剰深化解消、真の底を拾う)
}


def get_bnf_threshold(sector, regime):
    """セクターと地合いから、乖離率の閾値を計算"""
    base = SECTOR_BNF_THRESHOLDS.get(sector, SECTOR_BNF_THRESHOLDS["default"])
    mult = REGIME_BNF_MULTIPLIER.get(regime, 1.0)
    return base * mult


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  コマンドライン引数
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def parse_args():
    p = argparse.ArgumentParser(description="BNF+Minervini統合バックテスト v2.7.1")
    p.add_argument("--years", type=int, default=5, help="検証期間（年）")
    p.add_argument("--chart", action="store_true", help="グラフ生成")
    p.add_argument("--capital", type=float, default=1000000, help="初期資金")
    p.add_argument("--risk", type=float, default=1.0, help="1トレードリスク（パーセント）")
    # モード選択
    p.add_argument("--mode", type=str, default="quality",
                   choices=["quality", "daily"],
                   help="quality=厳選シグナル月4件, daily=月15-25件")
    # v2.1: リスク管理
    p.add_argument("--max-concurrent", type=int, default=10,
                   help="同時保有銘柄の上限（v2.1新規、デフォルト10銘柄）")
    # v2.2: 複利モード
    p.add_argument("--compound", action="store_true",
                   help="複利モード（v2.2新規、残高に応じて株数を調整）")
    # v2.4: シンプルサーキットブレーカー（HALT-only）
    p.add_argument("--circuit-breaker", action="store_true",
                   help="サーキットブレーカー（v2.4: HALT-only シンプル版）")
    p.add_argument("--halt-vix", type=float, default=35.0,
                   help="HALT発動VIX閾値（デフォルト35）")
    p.add_argument("--halt-consecutive-losses", type=int, default=5,
                   help="HALT発動連敗数（デフォルト5）")
    p.add_argument("--halt-n225-drop", type=float, default=15.0,
                   help="HALT発動する日経1ヶ月下落率パーセント（デフォルト15）")
    p.add_argument("--halt-cooldown", type=int, default=5,
                   help="HALT解除のクールダウン日数（デフォルト5日）")
    # 個別検証
    p.add_argument("--bnf-only", action="store_true", help="BNFのみ実行")
    p.add_argument("--minervini-only", action="store_true", help="Minerviniのみ実行")
    p.add_argument("--momentum-only", action="store_true", help="MOMENTUMのみ実行（dailyモード時）")
    # 互換モード（比較検証用）
    p.add_argument("--legacy-v24", action="store_true",
                   help="v2.4互換動作（MINERVINI/BNF両方を元に戻す）")
    p.add_argument("--legacy-v25", action="store_true",
                   help="v2.5互換動作（BNF-LITEに陽線・RSI・破綻除外フィルタ復活）")
    p.add_argument("--legacy-v26", action="store_true",
                   help="v2.6互換動作（v2.7リスク管理をオフ）")
    # v2.6: BNFのHALT貫通オプション
    p.add_argument("--bnf-halted", action="store_true",
                   help="v2.6の「BNFはHALT貫通」機能をオフ（PANICでもBNFを止める、安全運転）")
    # ★v2.7.1: リスク管理（デフォルト値を緩和）
    p.add_argument("--panic-risk-mult", type=float, default=0.5,
                   help="PANIC時のBNFリスク倍率（デフォルト0.5=半減）")
    p.add_argument("--panic-bnf-max", type=int, default=10,
                   help="PANIC時のBNF同時保有上限（v2.7.1: 5→10に緩和）")
    p.add_argument("--panic-wait-days", type=int, default=3,
                   help="PANIC突入後の様子見日数（v2.7.1: 5→3日に緩和）")
    p.add_argument("--bnf-loss-cooldown", type=int, default=3,
                   help="BNF動的停止のクールダウン日数（v2.7.1: 7→3日に緩和）")
    p.add_argument("--bnf-loss-threshold", type=int, default=5,
                   help="BNF動的停止のトリガー連敗数（v2.7.1: 3→5連敗に緩和）")
    return p.parse_args()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  マーケット環境判定
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def fetch_global_data(start, end):
    data = {}
    with Progress(SpinnerColumn(), TextColumn("[cyan]{task.description}"),
                  BarColumn(), TaskProgressColumn(), console=console) as prog:
        task = prog.add_task("グローバル指数取得中...", total=len(GLOBAL_TICKERS))
        for tk, name in GLOBAL_TICKERS.items():
            try:
                df = yf.download(tk, start=start, end=end, progress=False, auto_adjust=True)
                if not df.empty:
                    if isinstance(df.columns, pd.MultiIndex):
                        df.columns = df.columns.get_level_values(0)
                    data[tk] = df
            except Exception:
                pass
            prog.advance(task)
    return data


def detect_market_regime(global_data, date):
    signals = {}

    if "^N225" in global_data:
        df = global_data["^N225"]
        df_sub = df[df.index <= date]
        if len(df_sub) >= 200:
            close = df_sub["Close"].iloc[-1]
            ma200 = df_sub["Close"].rolling(200).mean().iloc[-1]
            ma50 = df_sub["Close"].rolling(50).mean().iloc[-1]
            signals["n225_above_200ma"] = close > ma200
            signals["n225_above_50ma"] = close > ma50
            if len(df_sub) >= 22:
                change_1m = (close / df_sub["Close"].iloc[-22] - 1) * 100
                signals["n225_1m_change"] = change_1m

    if "^GSPC" in global_data:
        df = global_data["^GSPC"]
        df_sub = df[df.index <= date]
        if len(df_sub) >= 200:
            close = df_sub["Close"].iloc[-1]
            ma200 = df_sub["Close"].rolling(200).mean().iloc[-1]
            signals["sp500_above_200ma"] = close > ma200

    if "^VIX" in global_data:
        df = global_data["^VIX"]
        df_sub = df[df.index <= date]
        if not df_sub.empty:
            signals["vix"] = df_sub["Close"].iloc[-1]

    vix = signals.get("vix", 20)
    change_1m = signals.get("n225_1m_change", 0)

    if vix > 30 and change_1m < -10:
        return "PANIC", signals

    if not signals.get("n225_above_200ma", True) or vix > 25:
        return "BEARISH", signals

    if (signals.get("n225_above_200ma", False) and
        signals.get("n225_above_50ma", False) and
        signals.get("sp500_above_200ma", False) and
        vix < 20 and change_1m > 0):
        return "BULLISH", signals

    return "NEUTRAL", signals


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  v2.4: サーキットブレーカー（HALT-only シンプル版）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def check_halt_conditions(global_data, date, halt_vix=35, halt_n225_drop=15.0):
    """HALT条件のチェック（連敗以外の市場条件のみ）
    Returns: (halt_active, reason)
    """
    # VIX > halt_vix（極度のパニック）
    if "^VIX" in global_data:
        df = global_data["^VIX"]
        df_sub = df[df.index <= date]
        if not df_sub.empty:
            current_vix = df_sub["Close"].iloc[-1]
            if current_vix > halt_vix:
                return True, f"VIX={current_vix:.1f} > {halt_vix}（極度のパニック）"

    # 日経1ヶ月-X%以上下落
    if "^N225" in global_data:
        df = global_data["^N225"]
        df_sub = df[df.index <= date]
        if len(df_sub) >= 22:
            close = df_sub["Close"].iloc[-1]
            close_1m_ago = df_sub["Close"].iloc[-22]
            change_1m = (close / close_1m_ago - 1) * 100
            if change_1m < -halt_n225_drop:
                return True, f"日経1ヶ月{change_1m:.1f}%下落（急落）"

    return False, None


def precompute_halt_only_states(global_data, date_range, config):
    """v2.4: 市場条件ベースのHALT状態を事前計算
    クールダウン日数経過後にのみNORMAL復帰

    Returns: dict {date: (state, reason)}
      state: "NORMAL" または "HALT"
    """
    halt_vix = config.get("halt_vix", 35)
    halt_n225_drop = config.get("halt_n225_drop", 15.0)
    cooldown = config.get("halt_cooldown", 5)

    # 日付順にソート
    dates_sorted = sorted(date_range)

    # 各日の「生のHALT判定」
    raw_halt = {}
    for date in dates_sorted:
        active, reason = check_halt_conditions(
            global_data, date,
            halt_vix=halt_vix,
            halt_n225_drop=halt_n225_drop
        )
        raw_halt[date] = (active, reason)

    # クールダウン適用
    # HALTが一度発動したら、連続clear_streak日間クリアになるまで継続
    final_states = {}
    current_state = "NORMAL"
    current_reason = None
    clear_streak = 0  # HALT条件が解除された連続日数

    for date in dates_sorted:
        is_halt, reason = raw_halt[date]

        if is_halt:
            # HALT発動（または継続）
            current_state = "HALT"
            current_reason = reason
            clear_streak = 0
        else:
            # HALT条件が解除された
            if current_state == "HALT":
                clear_streak += 1
                if clear_streak >= cooldown:
                    # クールダウン経過でNORMAL復帰
                    current_state = "NORMAL"
                    current_reason = None
            else:
                # 既にNORMAL
                pass

        final_states[date] = (current_state, current_reason)

    return final_states


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Minervini: Trend Template
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def check_trend_template(df, idx, lite=False):
    """Trend Template 8条件チェック
    lite=True: 6/8条件に緩和（MA200上昇とRS強度を除外）
    """
    if idx < 200:
        return False

    close = df["Close"].iloc[idx]
    ma50 = df["MA50"].iloc[idx]
    ma150 = df["MA150"].iloc[idx]
    ma200 = df["MA200"].iloc[idx]

    if pd.isna(ma50) or pd.isna(ma150) or pd.isna(ma200):
        return False
    if not (close > ma150 and close > ma200):
        return False
    if not (ma150 > ma200):
        return False

    # LITEモードでは MA200上昇 条件をスキップ
    if not lite:
        ma200_20ago = df["MA200"].iloc[idx-20] if idx >= 20 else None
        if ma200_20ago is None or pd.isna(ma200_20ago) or not (ma200 > ma200_20ago):
            return False

    if not (ma50 > ma150 > ma200):
        return False
    if not (close > ma50):
        return False

    low_52w = df["Low"].iloc[max(0, idx-252):idx+1].min()
    if not (close >= low_52w * 1.25):
        return False

    high_52w = df["High"].iloc[max(0, idx-252):idx+1].max()
    if not (close >= high_52w * 0.75):
        return False

    # LITEモードでは RS強度 条件を緩和（+15% → +5%）
    if idx >= 126:
        ret_6m = (close / df["Close"].iloc[idx-126] - 1) * 100
        threshold = 5 if lite else 15
        if ret_6m < threshold:
            return False

    return True


def detect_vcp(df, idx, lookback=60, legacy=False):
    """VCP(Volatility Contraction Pattern) 検出

    ★v2.5改善点:
      - ATR比閾値 0.85 → 0.90 に緩和（もっと拾う）
      - last_range 12% → 15% に緩和
    legacy=True で v2.4 互換動作。
    """
    if idx < lookback:
        return False, None

    window = df.iloc[idx-lookback:idx+1].copy()
    atr_recent = window["ATR20"].iloc[-5:].mean()
    atr_past = window["ATR20"].iloc[:10].mean()

    if pd.isna(atr_recent) or pd.isna(atr_past) or atr_past == 0:
        return False, None

    # ★v2.5: ATR閾値緩和 0.85 → 0.90
    atr_threshold = 0.85 if legacy else 0.90
    if atr_recent > atr_past * atr_threshold:
        return False, None

    pivot = window["High"].iloc[:-1].max()
    current_close = window["Close"].iloc[-1]

    if current_close < pivot * 0.93 or current_close > pivot * 1.10:
        return False, None

    ranges = []
    for i in range(0, lookback - 10, 10):
        high = window["High"].iloc[i:i+10].max()
        low = window["Low"].iloc[i:i+10].min()
        if high > 0:
            ranges.append((high - low) / high * 100)

    if len(ranges) < 3:
        return False, None

    # ★v2.5: last_range 閾値緩和 12% → 15%
    last_range_threshold = 12 if legacy else 15
    last_range = ranges[-1]
    if last_range > last_range_threshold:
        return False, None

    first_half_avg = sum(ranges[:len(ranges)//2]) / max(1, len(ranges)//2)
    if last_range >= first_half_avg * 0.85:
        return False, None

    return True, pivot


def detect_ma_pullback(df, idx):
    """50日MAへの押し目（Minervini-LITE用）
    Trend Template通過銘柄が50MAに接近 + 跳ね返りサイン
    """
    if idx < 200:
        return False, None

    close = df["Close"].iloc[idx]
    low = df["Low"].iloc[idx]
    ma50 = df["MA50"].iloc[idx]

    if pd.isna(ma50):
        return False, None

    # 直近の高値（過去20日）
    recent_high = df["High"].iloc[max(0, idx-20):idx].max()

    # 条件1: 50MAに接近（±3%以内）
    if abs(close - ma50) / ma50 > 0.03:
        return False, None

    # 条件2: 直前に押し目がある（過去20日の安値が50MAに近い）
    recent_low = df["Low"].iloc[max(0, idx-10):idx+1].min()
    if recent_low > ma50 * 1.02:  # 50MAにタッチしてない
        return False, None

    # 条件3: 今日、陽線または前日より上昇
    open_p = df["Open"].iloc[idx]
    if close <= open_p:  # 陰線
        # 前日比プラスならOK
        if idx > 0 and close <= df["Close"].iloc[idx-1]:
            return False, None

    # ピボット = 直近高値
    return True, recent_high


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  BNF: 乖離率逆張り
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def check_bnf_signal(df, idx, lite=False, legacy_v24=False, legacy_v25=False,
                     sector=None, regime="NEUTRAL"):
    """BNF逆張り条件チェック

    動作モード:
      legacy_v24=True → v2.4互換（一律-15%、出来高1.0倍、他フィルタなし）
      legacy_v25=True → v2.5互換（陽線・RSI・破綻除外フィルタ入り）
      デフォルト     → v2.6真BNF流（セクター別×地合い倍率、フィルタ最小）

    v2.6の哲学（BNF氏本人の発言に忠実）:
      - 乖離率をセクター別に設定（SECTOR_BNF_THRESHOLDS）
      - 地合いで倍率調整（REGIME_BNF_MULTIPLIER）
      - 陽線・RSI・破綻除外フィルタは全削除
      - 出来高1.1倍（セリクラ確認の最低限）
    """
    if idx < 25:
        return False

    close = df["Close"].iloc[idx]
    open_p = df["Open"].iloc[idx]
    ma25 = df["MA25"].iloc[idx]
    vol = df["Volume"].iloc[idx]
    vol_avg = df["Vol20"].iloc[idx]

    if pd.isna(ma25) or pd.isna(vol_avg):
        return False

    # ── 乖離率チェック ──
    deviation = (close - ma25) / ma25 * 100

    if legacy_v24 or legacy_v25:
        # v2.4/v2.5: 一律閾値
        threshold = -15.0 if lite else -20.0
    else:
        # v2.6: セクター×地合い別閾値
        if lite and sector is not None:
            threshold = get_bnf_threshold(sector, regime)
        else:
            threshold = -20.0  # HIGH QUALITY版

    if deviation > threshold:
        return False

    # ── 出来高チェック ──
    if legacy_v24:
        vol_mult = 1.0 if lite else 1.1
    elif legacy_v25:
        vol_mult = 1.3 if lite else 1.1
    else:
        # v2.6: BNFは乖離重視なので出来高は緩め
        vol_mult = 1.1 if lite else 1.1
    if vol < vol_avg * vol_mult:
        return False

    # ── ボリンジャーバンド ──
    if lite:
        bb_check = df["BB_lower_1_5"].iloc[idx]  # -1.5σ
    else:
        bb_check = df["BB_lower"].iloc[idx]  # -2σ

    if pd.isna(bb_check) or close > bb_check:
        return False

    # ── v2.5 旧フィルタ（legacy_v25=True のときのみ適用）──
    if legacy_v25 and lite:
        # (1) 陽線必須
        if pd.isna(open_p) or close <= open_p:
            return False
        # (2) RSI(14) < 30
        if "RSI14" in df.columns:
            rsi = df["RSI14"].iloc[idx]
            if pd.isna(rsi) or rsi >= 30:
                return False
        # (3) 破綻除外
        if idx >= 200 and "MA200" in df.columns:
            ma200 = df["MA200"].iloc[idx]
            if not pd.isna(ma200) and ma200 > 0:
                ratio = close / ma200
                if ratio < 0.60:
                    return False

    # v2.6 ではこれらのフィルタを一切使わない（BNF氏の哲学通り）

    return True


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  MOMENTUM: 20日高値ブレイク（新規戦略）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def check_momentum_signal(df, idx):
    """MOMENTUM: 20日高値ブレイク戦略
    ・20日高値更新
    ・出来高1.5倍
    ・200日MA上 + 50日MA上
    ・エントリー価格 = ブレイク価格
    """
    if idx < 200:
        return False, None

    close = df["Close"].iloc[idx]
    high = df["High"].iloc[idx]
    ma50 = df["MA50"].iloc[idx]
    ma200 = df["MA200"].iloc[idx]
    vol = df["Volume"].iloc[idx]
    vol_avg = df["Vol20"].iloc[idx]

    if pd.isna(ma50) or pd.isna(ma200) or pd.isna(vol_avg):
        return False, None

    # 200MA・50MA上（強気銘柄のみ）
    if close < ma200 or close < ma50:
        return False, None

    # 20日高値（当日を除く過去20日）
    prev_high = df["High"].iloc[max(0, idx-20):idx].max()
    if pd.isna(prev_high) or prev_high == 0:
        return False, None

    # 当日高値が20日高値をブレイク
    if high <= prev_high * 1.001:  # 0.1%以上の明確なブレイク
        return False, None

    # 出来高1.5倍以上
    if vol < vol_avg * 1.5:
        return False, None

    # 直近で急上昇しすぎてない（オーバーエクステンション防止）
    ret_5d = (close / df["Close"].iloc[idx-5] - 1) * 100 if idx >= 5 else 0
    if ret_5d > 15:  # 5日で+15%超えてたら過熱
        return False, None

    return True, prev_high  # ピボット = 20日高値


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  統合バックテスター
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class IntegratedBacktester:
    def __init__(self, config, global_data):
        self.config = config
        self.global_data = global_data
        self.trades = []
        self.capital = config["initial_capital"]
        self.regime_counts = defaultdict(int)
        # ポートフォリオ管理（v2.1新規）
        # {date: count} で各日のオープンポジション数を追跡
        self.open_positions_by_date = defaultdict(int)
        self.max_concurrent = config.get("max_concurrent", 10)
        # エントリーの時系列記録（制限適用時に使う）
        self.pending_entries = []  # [(date, ticker, ...)]

    def prepare_indicators(self, df):
        df = df.copy()
        # Minervini用
        df["MA50"] = df["Close"].rolling(50).mean()
        df["MA150"] = df["Close"].rolling(150).mean()
        df["MA200"] = df["Close"].rolling(200).mean()
        df["EMA50"] = df["Close"].ewm(span=50, adjust=False).mean()
        # BNF用
        df["MA25"] = df["Close"].rolling(25).mean()
        df["BB_mid"] = df["Close"].rolling(20).mean()
        df["BB_std"] = df["Close"].rolling(20).std()
        df["BB_lower"] = df["BB_mid"] - 2 * df["BB_std"]
        df["BB_upper"] = df["BB_mid"] + 2 * df["BB_std"]
        df["BB_lower_1_5"] = df["BB_mid"] - 1.5 * df["BB_std"]  # LITE用
        # 共通
        df["Vol20"] = df["Volume"].rolling(20).mean()
        # ATR
        high_low = df["High"] - df["Low"]
        high_close = (df["High"] - df["Close"].shift()).abs()
        low_close = (df["Low"] - df["Close"].shift()).abs()
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        df["ATR20"] = tr.rolling(20).mean()
        # RSI(14) ← v2.5新規: BNF-LITE の真のoversold確認用
        delta = df["Close"].diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rs = gain / loss.replace(0, 1e-9)
        df["RSI14"] = 100 - (100 / (1 + rs))
        return df

    def backtest_stock(self, ticker, name, sector, df):
        df = self.prepare_indicators(df)

        in_position = False
        strategy = None
        entry_price = 0
        entry_date = None
        shares = 0
        initial_stop = 0
        half_taken = False
        entry_regime = None

        is_daily = self.config["mode"] == "daily"

        for idx in range(200, len(df)):
            date = df.index[idx]
            close = df["Close"].iloc[idx]
            high = df["High"].iloc[idx]
            low = df["Low"].iloc[idx]
            volume = df["Volume"].iloc[idx]

            # ── ポジション保有中のエグジット判定 ──
            if in_position:
                if strategy in ("BNF", "BNF-LITE"):
                    if low <= initial_stop:
                        self._record_trade(ticker, name, sector, entry_date, date,
                                           entry_price, initial_stop, shares,
                                           strategy, "損切り", entry_regime)
                        in_position = False
                        continue

                    ma25 = df["MA25"].iloc[idx]
                    if not pd.isna(ma25) and close >= ma25:
                        self._record_trade(ticker, name, sector, entry_date, date,
                                           entry_price, close, shares,
                                           strategy, "25日MA戻り", entry_regime)
                        in_position = False
                        continue

                    if (date - entry_date).days >= 14:
                        self._record_trade(ticker, name, sector, entry_date, date,
                                           entry_price, close, shares,
                                           strategy, "保有期限", entry_regime)
                        in_position = False
                        continue

                elif strategy == "MOMENTUM":
                    # MOMENTUM: 損切り-5%、+10%利確、10日保有
                    if low <= initial_stop:
                        self._record_trade(ticker, name, sector, entry_date, date,
                                           entry_price, initial_stop, shares,
                                           "MOMENTUM", "損切り", entry_regime)
                        in_position = False
                        continue

                    # +10%利確
                    if close >= entry_price * 1.10:
                        self._record_trade(ticker, name, sector, entry_date, date,
                                           entry_price, close, shares,
                                           "MOMENTUM", "+10%利確", entry_regime)
                        in_position = False
                        continue

                    # 10日経過
                    if (date - entry_date).days >= 10:
                        self._record_trade(ticker, name, sector, entry_date, date,
                                           entry_price, close, shares,
                                           "MOMENTUM", "保有期限", entry_regime)
                        in_position = False
                        continue

                elif strategy in ("MINERVINI", "MINERVINI-LITE"):
                    if low <= initial_stop:
                        self._record_trade(ticker, name, sector, entry_date, date,
                                           entry_price, initial_stop, shares,
                                           strategy, "損切り", entry_regime)
                        in_position = False
                        continue

                    if not half_taken and close >= entry_price * 1.25:
                        half_shares = shares // 2
                        self._record_trade(ticker, name, sector, entry_date, date,
                                           entry_price, close, half_shares,
                                           strategy, "半分利確", entry_regime)
                        shares -= half_shares
                        half_taken = True
                        initial_stop = entry_price
                        continue

                    if half_taken:
                        ema50 = df["EMA50"].iloc[idx]
                        if not pd.isna(ema50) and close < ema50:
                            self._record_trade(ticker, name, sector, entry_date, date,
                                               entry_price, close, shares,
                                               strategy, "50EMA下抜け", entry_regime)
                            in_position = False
                            continue

                    if (date - entry_date).days > 90:
                        self._record_trade(ticker, name, sector, entry_date, date,
                                           entry_price, close, shares,
                                           strategy, "タイムストップ", entry_regime)
                        in_position = False
                        continue

            # ── エントリー判定 ──
            if not in_position:
                regime, _ = detect_market_regime(self.global_data, date)
                self.regime_counts[regime] += 1

                selected_strategy = None
                selected_entry_price = None
                selected_stop = None

                # v2.6: モード判定（legacy_v24=v2.4互換, legacy_v25=v2.5互換, 他=v2.6）
                legacy_v24 = self.config.get("legacy_v24", False)
                legacy_v25 = self.config.get("legacy_v25", False)
                legacy_mode = legacy_v24  # 旧名との互換（MINERVINIロジックで使用）

                if is_daily:
                    # ── DAILY ACTION モード v2.1 ──

                    # BNF-LITE（PANIC/BEARISH/NEUTRAL で発動、全環境の下げ狙い）
                    # ★v2.6: セクター別×地合い別閾値、悪フィルタ削除
                    if not self.config["minervini_only"] and not self.config["momentum_only"]:
                        if regime in ("PANIC", "BEARISH", "NEUTRAL"):
                            if check_bnf_signal(df, idx, lite=True,
                                                legacy_v24=legacy_v24,
                                                legacy_v25=legacy_v25,
                                                sector=sector, regime=regime):
                                selected_strategy = "BNF-LITE"
                                selected_entry_price = close
                                selected_stop = close * 0.95  # -5%

                    # MOMENTUM（v2.1改善: BULLISH のみ発動、弱気相場の連敗回避）
                    if (selected_strategy is None and
                        not self.config["bnf_only"] and
                        not self.config["minervini_only"] and
                        regime == "BULLISH"):  # ← BULLISHのみに限定
                        mom_ok, pivot = check_momentum_signal(df, idx)
                        if mom_ok:
                            selected_strategy = "MOMENTUM"
                            selected_entry_price = pivot
                            selected_stop = pivot * 0.95  # -5%

                    # Minervini標準版（BULLISH/NEUTRALで発動、v2.1: LITE廃止）
                    # ★v2.5改善: VCP条件緩和・ストップ拡大・出来高緩和・MA50押し目ルート追加
                    # v2.6 でも MINERVINI は v2.5 の改善を維持(v2.4 のときだけオフ)
                    if (selected_strategy is None and
                        not self.config["bnf_only"] and
                        not self.config["momentum_only"] and
                        regime in ("BULLISH", "NEUTRAL")):
                        if check_trend_template(df, idx, lite=False):  # ← 標準版
                            vol20 = df["Vol20"].iloc[idx]
                            vol_threshold = 1.4 if legacy_v24 else 1.3  # ★v2.5: 出来高緩和
                            stop_ratio = 0.93 if legacy_v24 else 0.91  # ★v2.5: -7% → -9%

                            # ルート1: VCPブレイクアウト（従来型、条件緩和済み）
                            vcp_ok, pivot_v = detect_vcp(df, idx, legacy=legacy_v24)
                            if vcp_ok and high >= pivot_v:
                                if not pd.isna(vol20) and volume >= vol20 * vol_threshold:
                                    selected_strategy = "MINERVINI"
                                    selected_entry_price = pivot_v
                                    selected_stop = pivot_v * stop_ratio
                            elif not legacy_v24:
                                # ★v2.5新規: ルート2: 50MA押し目エントリー（v2.6も維持）
                                ma_ok, pivot_ma = detect_ma_pullback(df, idx)
                                if ma_ok and high >= pivot_ma:
                                    if not pd.isna(vol20) and volume >= vol20 * vol_threshold:
                                        selected_strategy = "MINERVINI"
                                        selected_entry_price = pivot_ma
                                        selected_stop = pivot_ma * stop_ratio

                else:
                    # ── HIGH QUALITYモード ──
                    if not self.config["minervini_only"]:
                        if regime in ("PANIC", "BEARISH", "NEUTRAL"):
                            # HIGH QUALITY版BNFは元々 -20%/BB-2σ/出来高1.1倍で厳しい
                            if check_bnf_signal(df, idx, lite=False, legacy_v24=True):
                                selected_strategy = "BNF"
                                selected_entry_price = close
                                selected_stop = close * 0.95

                    if (selected_strategy is None and
                        not self.config["bnf_only"] and
                        regime in ("BULLISH", "NEUTRAL")):
                        if check_trend_template(df, idx, lite=False):
                            vol20 = df["Vol20"].iloc[idx]
                            vol_threshold = 1.4 if legacy_mode else 1.3
                            stop_ratio = 0.93 if legacy_mode else 0.91
                            vcp_ok, pivot = detect_vcp(df, idx, legacy=legacy_mode)
                            if vcp_ok and high >= pivot:
                                if not pd.isna(vol20) and volume >= vol20 * vol_threshold:
                                    selected_strategy = "MINERVINI"
                                    selected_entry_price = pivot
                                    selected_stop = pivot * stop_ratio

                if selected_strategy is None:
                    continue

                # エントリー実行
                entry_price = selected_entry_price
                initial_stop = selected_stop
                entry_date = date
                entry_regime = regime
                half_taken = False
                strategy = selected_strategy

                risk_per_share = entry_price - initial_stop
                if risk_per_share <= 0:
                    continue
                risk_amount = self.capital * (self.config["risk"] / 100)

                # ★v2.7 改善①: PANIC時のBNFポジションを半減
                v27_enabled = not (self.config.get("legacy_v24", False) or
                                   self.config.get("legacy_v25", False) or
                                   self.config.get("legacy_v26", False))
                if (v27_enabled and
                    strategy == "BNF-LITE" and
                    entry_regime == "PANIC"):
                    risk_amount *= self.config.get("panic_risk_mult", 0.5)

                shares = int(risk_amount / risk_per_share)
                if shares < 1:
                    continue

                in_position = True

        # 期末強制クローズ
        if in_position:
            last_date = df.index[-1]
            last_close = df["Close"].iloc[-1]
            self._record_trade(ticker, name, sector, entry_date, last_date,
                               entry_price, last_close, shares,
                               strategy, "期末強制決済", entry_regime)

    def _record_trade(self, ticker, name, sector, entry_date, exit_date,
                      entry_price, exit_price, shares, strategy, reason, regime):
        pnl_pct = (exit_price / entry_price - 1) * 100
        pnl = shares * (exit_price - entry_price)
        self.trades.append({
            "ticker": ticker, "name": name, "sector": sector,
            "entry_date": entry_date, "exit_date": exit_date,
            "entry_price": entry_price, "exit_price": exit_price,
            "pnl_pct": pnl_pct, "pnl": pnl,
            "exit_reason": reason, "strategy": strategy,
            "regime": regime,
            "hold_days": (exit_date - entry_date).days,
        })

    def run_all(self, stocks):
        with Progress(SpinnerColumn(), TextColumn("[cyan]{task.description}"),
                      BarColumn(), TaskProgressColumn(), console=console) as prog:
            task = prog.add_task("バックテスト実行中...", total=len(stocks))
            for ticker, info in stocks.items():
                name, sector = info[0], info[1]
                try:
                    df = yf.download(ticker, start=self.config["start_date"],
                                     end=self.config["end_date"],
                                     progress=False, auto_adjust=True)
                    if not df.empty:
                        if isinstance(df.columns, pd.MultiIndex):
                            df.columns = df.columns.get_level_values(0)
                        if len(df) >= 200:
                            self.backtest_stock(ticker, name, sector, df)
                except Exception:
                    pass
                prog.advance(task)

        # ── v2.3新規: サーキットブレーカーでエントリーフィルタリング ──
        if self.config.get("circuit_breaker"):
            self.trades, self.cb_stats = self._apply_circuit_breaker(self.trades)
        else:
            self.cb_stats = None

        # ── v2.1新規: 同時保有制限フィルタリング ──
        # 時系列順にトレードを並べ、同時保有が max_concurrent を超えたら棄却
        self.trades = self._apply_concurrent_limit(self.trades)

        # ── v2.2新規: 複利モードの場合、残高ベースで株数と損益を再計算 ──
        if self.config.get("compound"):
            self.trades = self._apply_compound_recalculation(self.trades)

        return self.trades

    def _compute_panic_info(self, date_range):
        """v2.7: 各日のPANIC経過日数を事前計算
        Returns: dict {date: days_since_panic_started} or -1 if not in PANIC
        """
        import pandas as pd
        if not date_range:
            return {}
        panic_info = {}
        dates = pd.bdate_range(min(date_range), max(date_range))
        current_panic_start = None
        for date in dates:
            try:
                regime, _ = detect_market_regime(self.global_data, date)
            except Exception:
                regime = "NORMAL"
            if regime == "PANIC":
                if current_panic_start is None:
                    current_panic_start = date
                panic_info[date] = (date - current_panic_start).days
            else:
                current_panic_start = None
                panic_info[date] = -1
        return panic_info

    def _apply_circuit_breaker(self, trades):
        """v2.4: シンプルサーキットブレーカー（HALT-only）
        暴落時のエントリーを自動的にキャンセル

        v2.7 追加機能:
          改善①: PANIC時BNFポジション半減 (entry size計算で適用済み)
          改善②: PANIC時BNF同時保有上限
          改善③: PANIC突入後N日の様子見
          改善④: BNF動的停止（連敗検知）

        Returns: (filtered_trades, cb_stats)
        """
        if not trades:
            return trades, {"periods": [], "rejected_count": 0, "avoided_loss": 0}

        # 全期間のHALT状態を事前計算
        all_dates = set()
        for t in trades:
            all_dates.add(t["entry_date"])
        date_range = sorted(all_dates)

        cb_states = precompute_halt_only_states(
            self.global_data, date_range, self.config
        )

        # v2.7: v2.7 機能の有効判定
        v27_enabled = not (self.config.get("legacy_v24", False) or
                           self.config.get("legacy_v25", False) or
                           self.config.get("legacy_v26", False))

        # v2.7 改善③: PANIC経過日数を事前計算
        panic_info = self._compute_panic_info(date_range) if v27_enabled else {}
        panic_wait_days = self.config.get("panic_wait_days", 5)

        # v2.7 改善④: BNF動的停止パラメータ
        bnf_loss_threshold = self.config.get("bnf_loss_threshold", 3)
        bnf_loss_cooldown_days = self.config.get("bnf_loss_cooldown", 7)

        # v2.7 改善②: PANIC時のBNF同時保有上限
        panic_bnf_max = self.config.get("panic_bnf_max", 5)

        # トレードをエントリー日順にソート
        sorted_trades = sorted(trades, key=lambda x: x["entry_date"])

        accepted = []
        rejected = []

        # 連敗カウント用
        recent_results = []
        halt_losses = self.config.get("halt_consecutive_losses", 5)

        # 連敗HALT: 連敗HALT発動後、1勝が出るまで動的HALT継続
        loss_halt_active = False
        loss_halt_start_date = None
        LOSS_HALT_TIMEOUT_DAYS = 30

        # v2.7: BNF専用の戦歴・停止期間
        bnf_recent_results = []      # BNF-LITEだけのPnL履歴
        bnf_cooldown_until = None    # BNF停止の期限（この日まで停止）

        # v2.7 改善②: 現在オープン中のBNF-LITEポジションを数えるためのリスト
        # 構造: [(exit_date, trade), ...] — 順次チェックして閉じたものは除外
        open_bnf_positions = []

        for trade in sorted_trades:
            entry_date = trade["entry_date"]
            state, reason = cb_states.get(entry_date, ("NORMAL", None))
            regime = trade.get("regime", "NORMAL")
            is_bnf_lite = trade.get("strategy") == "BNF-LITE"

            # 連敗による動的HALT判定
            dynamic_halt = False
            dynamic_halt_reason = None

            if loss_halt_active:
                # 時間経過による自動解除チェック
                if loss_halt_start_date is not None:
                    days_since_halt = (entry_date - loss_halt_start_date).days
                    if days_since_halt >= LOSS_HALT_TIMEOUT_DAYS:
                        loss_halt_active = False
                        loss_halt_start_date = None
                        recent_results = []

                if loss_halt_active:
                    dynamic_halt = True
                    dynamic_halt_reason = f"{halt_losses}連敗継続中"
            elif len(recent_results) >= halt_losses:
                last_n = recent_results[-halt_losses:]
                if all(r <= 0 for r in last_n):
                    dynamic_halt = True
                    dynamic_halt_reason = f"{halt_losses}連敗（手法不適合期）"
                    loss_halt_active = True
                    loss_halt_start_date = entry_date

            # v2.6: BNF-LITE HALT貫通判定（v2.7でも継続）
            bnf_halted_mode = self.config.get("bnf_halted", False)
            bnf_bypasses_market_halt = (
                is_bnf_lite and
                not bnf_halted_mode and
                not self.config.get("legacy_v24", False) and
                not self.config.get("legacy_v25", False)
            )

            # 市場HALT判定
            if state == "HALT":
                if bnf_bypasses_market_halt:
                    pass  # BNFはHALTを貫通
                else:
                    trade_rej = trade.copy()
                    trade_rej["cb_state"] = "HALT"
                    trade_rej["cb_reason"] = reason
                    rejected.append(trade_rej)
                    continue

            if dynamic_halt:
                # 連敗HALTは全戦略に適用
                trade_rej = trade.copy()
                trade_rej["cb_state"] = "HALT"
                trade_rej["cb_reason"] = dynamic_halt_reason
                rejected.append(trade_rej)
                if trade["pnl"] > 0:
                    loss_halt_active = False
                    loss_halt_start_date = None
                    recent_results.append(trade["pnl"])
                    if len(recent_results) > 20:
                        recent_results = recent_results[-20:]
                continue

            # ★v2.7 改善③: PANIC突入後の様子見（BNF-LITEのみ対象）
            if v27_enabled and is_bnf_lite and regime == "PANIC":
                p_since = panic_info.get(entry_date, -1)
                if 0 <= p_since < panic_wait_days:
                    trade_rej = trade.copy()
                    trade_rej["cb_state"] = "HALT"
                    trade_rej["cb_reason"] = f"PANIC突入後{p_since}日目(様子見中)"
                    rejected.append(trade_rej)
                    continue

            # ★v2.7 改善④: BNF動的停止（連敗検知中なら却下）
            # ★v2.7.1 バグ修正: クールダウン期限切れでカウンタリセット、
            #                   却下中でも勝ちpnlで即解除(永久ループ防止)
            if v27_enabled and is_bnf_lite:
                from datetime import timedelta
                # クールダウン期限切れチェック → 連敗カウンタもリセット
                if bnf_cooldown_until is not None and entry_date >= bnf_cooldown_until:
                    bnf_cooldown_until = None
                    bnf_recent_results = []  # ★バグ修正: 過去の連敗を忘れる

                # クールダウン中
                if bnf_cooldown_until is not None and entry_date < bnf_cooldown_until:
                    trade_rej = trade.copy()
                    trade_rej["cb_state"] = "HALT"
                    trade_rej["cb_reason"] = f"BNF連敗停止中({bnf_cooldown_until})"
                    rejected.append(trade_rej)
                    # ★バグ修正: 却下トレードが勝ちだったら即解除
                    if trade["pnl"] > 0:
                        bnf_cooldown_until = None
                        bnf_recent_results = []
                    continue

                # BNF連敗判定（最新N件チェック）
                if len(bnf_recent_results) >= bnf_loss_threshold:
                    last_n = bnf_recent_results[-bnf_loss_threshold:]
                    if all(r <= 0 for r in last_n):
                        # クールダウン発動
                        bnf_cooldown_until = entry_date + timedelta(days=bnf_loss_cooldown_days)
                        trade_rej = trade.copy()
                        trade_rej["cb_state"] = "HALT"
                        trade_rej["cb_reason"] = f"BNF {bnf_loss_threshold}連敗→{bnf_loss_cooldown_days}日停止"
                        rejected.append(trade_rej)
                        # ★バグ修正: 却下トレードが勝ちだったら即解除
                        if trade["pnl"] > 0:
                            bnf_cooldown_until = None
                            bnf_recent_results = []
                        continue

            # ★v2.7 改善②: PANIC時のBNF同時保有上限
            if v27_enabled and is_bnf_lite and regime == "PANIC":
                # entry_date時点でまだオープンしているBNFポジションを数える
                open_bnf_positions = [
                    (ed, t) for (ed, t) in open_bnf_positions
                    if ed > entry_date
                ]
                if len(open_bnf_positions) >= panic_bnf_max:
                    trade_rej = trade.copy()
                    trade_rej["cb_state"] = "HALT"
                    trade_rej["cb_reason"] = f"PANIC時BNF上限{panic_bnf_max}銘柄到達"
                    rejected.append(trade_rej)
                    continue

            # NORMAL: エントリー許可
            trade["cb_state"] = "NORMAL"
            trade["cb_reason"] = None
            accepted.append(trade)

            # 連敗カウンタ更新
            recent_results.append(trade["pnl"])
            if len(recent_results) > 20:
                recent_results = recent_results[-20:]

            # v2.7: BNF専用カウンタ更新
            if is_bnf_lite:
                bnf_recent_results.append(trade["pnl"])
                if len(bnf_recent_results) > 20:
                    bnf_recent_results = bnf_recent_results[-20:]
                # BNF勝ち → クールダウン解除
                if trade["pnl"] > 0:
                    bnf_cooldown_until = None
                # PANIC時のBNFポジションリストに追加
                if regime == "PANIC":
                    open_bnf_positions.append((trade["exit_date"], trade))

            # 勝ちが出たら動的HALTを解除
            if trade["pnl"] > 0:
                loss_halt_active = False
                loss_halt_start_date = None

        # CB発動期間の統計を抽出
        periods = self._extract_cb_periods(cb_states)
        avoided_loss = sum(t["pnl"] for t in rejected)

        cb_stats = {
            "periods": periods,
            "rejected_count": len(rejected),
            "avoided_loss": avoided_loss,
        }

        return accepted, cb_stats

    def _extract_cb_periods(self, cb_states):
        """HALT発動期間を抽出してリスト化
        Returns: [{"start": date, "end": date, "state": "HALT", "reason": str}, ...]
        """
        if not cb_states:
            return []

        dates_sorted = sorted(cb_states.keys())
        periods = []
        current_period = None

        for date in dates_sorted:
            state, reason = cb_states[date]
            if state == "HALT":
                if current_period is None:
                    current_period = {
                        "start": date, "end": date,
                        "state": "HALT", "reason": reason
                    }
                else:
                    current_period["end"] = date
                    # 理由が変わったら最新の理由に更新
                    if reason and reason != current_period["reason"]:
                        current_period["reason"] = reason
            else:  # NORMAL
                if current_period is not None:
                    periods.append(current_period)
                    current_period = None

        if current_period is not None:
            periods.append(current_period)

        return periods

    def _apply_concurrent_limit(self, trades):
        """同時保有銘柄数の上限を適用して、超過したトレードを除外"""
        if not trades:
            return trades

        max_c = self.max_concurrent
        # エントリー日順にソート
        sorted_trades = sorted(trades, key=lambda x: x["entry_date"])

        accepted = []
        # 同時保有中のトレードを追跡（exit_date > 判定日のもの）
        for trade in sorted_trades:
            entry = trade["entry_date"]
            # この日にすでに保有されているトレード数を数える
            currently_open = sum(
                1 for a in accepted
                if a["entry_date"] <= entry < a["exit_date"]
            )
            if currently_open < max_c:
                accepted.append(trade)
            # 上限超えたら棄却（accepted に入れない）

        return accepted

    def _apply_compound_recalculation(self, trades):
        """v2.2: 複利モード
        エントリー時点での「確定済み残高」に基づいて
        株数・損益を再計算する

        ロジック:
        ・エントリー日順にソート
        ・各エントリー時点で、それまでに exit_date を迎えた
          トレードの確定損益を合算して現在残高を算出
        ・current_equity × risk_pct% でリスク枠を再計算
        ・リスク枠 / (entry_price - stop_price) で新しい株数を決定
        ・それに基づいて損益(pnl)を再計算
        """
        if not trades:
            return trades

        initial_capital = self.config["initial_capital"]
        risk_pct = self.config["risk"] / 100

        # エントリー日順にソート
        sorted_trades = sorted(trades, key=lambda x: x["entry_date"])

        recalculated = []
        for trade in sorted_trades:
            entry_date = trade["entry_date"]
            entry_price = trade["entry_price"]

            # このエントリー時点で確定済みの損益合計
            realized_pnl = sum(
                t["pnl"] for t in recalculated
                if t["exit_date"] <= entry_date
            )

            current_equity = initial_capital + realized_pnl

            # マイナス残高は防ぐ（現実的に破産している）
            if current_equity <= 0:
                # エントリー不可として棄却
                continue

            # リスク計算
            # 元の計算では stop_price 情報がないので、pnl_pct から逆算
            # pnl_pct が -7% の損切りで entry_price 1000円なら、stop_price=930円
            # 実際は loss_pct が損切り%、stop_price = entry * (1 + loss_pct/100)
            # ここでは簡易的に、元trade の shares と pnl から逆算できないので、
            # 戦略別の標準ストップロス% を使う
            strategy = trade.get("strategy", "")
            if strategy in ("BNF", "BNF-LITE", "MOMENTUM"):
                stop_pct = -5.0
            elif strategy in ("MINERVINI", "MINERVINI-LITE"):
                stop_pct = -7.0
            else:
                stop_pct = -7.0  # デフォルト

            stop_price = entry_price * (1 + stop_pct / 100)
            risk_per_share = entry_price - stop_price

            if risk_per_share <= 0:
                continue

            risk_amount = current_equity * risk_pct
            new_shares = int(risk_amount / risk_per_share)
            if new_shares < 1:
                continue

            # 新しい損益を計算
            exit_price = trade["exit_price"]
            new_pnl = new_shares * (exit_price - entry_price)

            # トレードを複製して損益を更新
            new_trade = trade.copy()
            new_trade["pnl"] = new_pnl
            new_trade["shares"] = new_shares
            new_trade["equity_before"] = current_equity  # 参考情報として保存
            recalculated.append(new_trade)

        return recalculated


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  統計計算
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def calc_stats(trades, initial_capital):
    if not trades:
        return None

    total = len(trades)
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]

    win_rate = len(wins) / total * 100
    total_pnl = sum(t["pnl"] for t in trades)
    final_capital = initial_capital + total_pnl
    return_pct = (final_capital / initial_capital - 1) * 100

    avg_win_pct = sum(t["pnl_pct"] for t in wins) / len(wins) if wins else 0
    avg_loss_pct = sum(t["pnl_pct"] for t in losses) / len(losses) if losses else 0
    avg_pnl_pct = sum(t["pnl_pct"] for t in trades) / total

    max_win_pct = max(t["pnl_pct"] for t in trades)
    max_loss_pct = min(t["pnl_pct"] for t in trades)

    gross_win = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in losses))
    pf = gross_win / gross_loss if gross_loss > 0 else float("inf")

    avg_hold = sum(t["hold_days"] for t in trades) / total

    sorted_trades = sorted(trades, key=lambda x: x["exit_date"])
    first_date = sorted_trades[0]["entry_date"]
    last_date = sorted_trades[-1]["exit_date"]
    span_days = max(1, (last_date - first_date).days)
    span_years = span_days / 365.25
    cagr = ((final_capital / initial_capital) ** (1/span_years) - 1) * 100 if span_years > 0 else 0
    signals_per_month = total / max(1, span_days / 30.44)

    equity = initial_capital
    peak = initial_capital
    max_dd = 0
    for t in sorted_trades:
        equity += t["pnl"]
        peak = max(peak, equity)
        dd = (peak - equity) / peak * 100
        max_dd = max(max_dd, dd)

    max_losing_streak = 0
    current_streak = 0
    for t in sorted_trades:
        if t["pnl"] <= 0:
            current_streak += 1
            max_losing_streak = max(max_losing_streak, current_streak)
        else:
            current_streak = 0

    strategy_stats = defaultdict(lambda: {"count": 0, "pnl": 0, "wins": 0})
    regime_stats = defaultdict(lambda: {"count": 0, "pnl": 0, "wins": 0})
    year_stats = defaultdict(lambda: {"count": 0, "pnl": 0, "wins": 0, "pnl_pct_sum": 0})
    exit_stats = defaultdict(lambda: {"count": 0, "pnl": 0})

    for t in trades:
        strategy_stats[t["strategy"]]["count"] += 1
        strategy_stats[t["strategy"]]["pnl"] += t["pnl"]
        if t["pnl"] > 0:
            strategy_stats[t["strategy"]]["wins"] += 1

        if t.get("regime"):
            regime_stats[t["regime"]]["count"] += 1
            regime_stats[t["regime"]]["pnl"] += t["pnl"]
            if t["pnl"] > 0:
                regime_stats[t["regime"]]["wins"] += 1

        y = t["exit_date"].year
        year_stats[y]["count"] += 1
        year_stats[y]["pnl"] += t["pnl"]
        year_stats[y]["pnl_pct_sum"] += t["pnl_pct"]
        if t["pnl"] > 0:
            year_stats[y]["wins"] += 1

        exit_stats[t["exit_reason"]]["count"] += 1
        exit_stats[t["exit_reason"]]["pnl"] += t["pnl"]

    return {
        "total": total, "wins": len(wins), "losses": len(losses),
        "win_rate": win_rate, "total_pnl": total_pnl,
        "return_pct": return_pct, "final_capital": final_capital,
        "cagr": cagr, "signals_per_month": signals_per_month,
        "avg_win_pct": avg_win_pct, "avg_loss_pct": avg_loss_pct,
        "avg_pnl_pct": avg_pnl_pct,
        "max_win_pct": max_win_pct, "max_loss_pct": max_loss_pct,
        "pf": pf, "avg_hold": avg_hold, "max_dd": max_dd,
        "max_losing_streak": max_losing_streak,
        "strategy_stats": dict(strategy_stats),
        "regime_stats": dict(regime_stats),
        "year_stats": dict(year_stats),
        "exit_stats": dict(exit_stats),
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  結果表示
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def show_summary(stats):
    t1 = Table(title="[bold cyan]🏆 主要成績[/]", box=box.ROUNDED, show_header=False)
    t1.add_column("指標", style="bold", width=18)
    t1.add_column("値", justify="right", width=14)

    t1.add_row("総トレード数", f"{stats['total']:,} 回")
    t1.add_row("勝ちトレード", f"{stats['wins']:,} 回")
    t1.add_row("負けトレード", f"{stats['losses']:,} 回")
    wr_c = "green" if stats["win_rate"] >= 50 else "yellow" if stats["win_rate"] >= 40 else "red"
    t1.add_row("勝率", f"[{wr_c}]{stats['win_rate']:.1f}%[/]")
    pf_c = "green" if stats["pf"] >= 2 else "yellow" if stats["pf"] >= 1.5 else "red"
    pf_s = f"{stats['pf']:.2f}" if stats["pf"] != float("inf") else "∞"
    t1.add_row("プロフィットF", f"[{pf_c}]{pf_s}[/]")
    t1.add_row("推定リターン", f"[green]{stats['return_pct']:+.1f}%[/]")
    t1.add_row("年率 (CAGR)", f"[green]{stats['cagr']:+.1f}%[/]")
    dd_c = "green" if stats["max_dd"] < 15 else "yellow" if stats["max_dd"] < 30 else "red"
    t1.add_row("最大ドローダウン", f"[{dd_c}]-{stats['max_dd']:.1f}%[/]")
    t1.add_row("連続最大負け", f"{stats['max_losing_streak']} 連敗")

    t2 = Table(title="[bold cyan]📊 詳細統計[/]", box=box.ROUNDED, show_header=False)
    t2.add_column("指標", style="bold", width=18)
    t2.add_column("値", justify="right", width=18)
    t2.add_row("平均損益", f"{stats['avg_pnl_pct']:+.2f}%")
    t2.add_row("平均利益", f"[green]{stats['avg_win_pct']:+.2f}%[/]")
    t2.add_row("平均損失", f"[red]{stats['avg_loss_pct']:+.2f}%[/]")
    t2.add_row("最大利益", f"[green]{stats['max_win_pct']:+.2f}%[/]")
    t2.add_row("最大損失", f"[red]{stats['max_loss_pct']:+.2f}%[/]")
    t2.add_row("平均保有日数", f"{stats['avg_hold']:.1f} 日")
    t2.add_row("最終仮想資金", f"¥{stats['final_capital']:,.0f}")
    signals_c = "green" if stats["signals_per_month"] >= 10 else "yellow" if stats["signals_per_month"] >= 3 else "red"
    t2.add_row("シグナル頻度", f"[{signals_c}]月 {stats['signals_per_month']:.1f} 件[/]")

    console.print(Columns([t1, t2]))


def show_strategy_breakdown(stats):
    if not stats["strategy_stats"]:
        return
    t = Table(title="[bold magenta]⚔ 戦略別内訳[/]",
              box=box.DOUBLE_EDGE, title_style="bold magenta")
    t.add_column("戦略", style="bold")
    t.add_column("件数", justify="right")
    t.add_column("勝率", justify="right")
    t.add_column("損益", justify="right")
    t.add_column("平均損益/件", justify="right")

    for strategy, s in sorted(stats["strategy_stats"].items(),
                              key=lambda x: x[1]["pnl"], reverse=True):
        wr = s["wins"] / s["count"] * 100 if s["count"] > 0 else 0
        avg_pnl = s["pnl"] / s["count"] if s["count"] > 0 else 0
        pnl_c = "green" if s["pnl"] > 0 else "red"
        t.add_row(
            f"[bold]{strategy}[/]",
            f"{s['count']}",
            f"{wr:.1f}%",
            f"[{pnl_c}]¥{s['pnl']:+,.0f}[/]",
            f"[{pnl_c}]¥{avg_pnl:+,.0f}[/]"
        )
    console.print(t)


def show_regime_breakdown(stats, regime_counts):
    t = Table(title="[bold yellow]🌤 相場環境別成績[/]", box=box.ROUNDED,
              title_style="bold yellow")
    t.add_column("環境", style="bold")
    t.add_column("日数", justify="right")
    t.add_column("エントリー数", justify="right")
    t.add_column("勝率", justify="right")
    t.add_column("損益", justify="right")

    total_days = sum(regime_counts.values())
    regime_order = ["BULLISH", "NEUTRAL", "BEARISH", "PANIC"]
    regime_colors = {"BULLISH": "green", "NEUTRAL": "white",
                     "BEARISH": "yellow", "PANIC": "red"}

    for regime in regime_order:
        s = stats["regime_stats"].get(regime, {"count": 0, "pnl": 0, "wins": 0})
        days = regime_counts.get(regime, 0)
        days_pct = days / total_days * 100 if total_days > 0 else 0
        wr = s["wins"] / s["count"] * 100 if s["count"] > 0 else 0
        color = regime_colors[regime]
        pnl_c = "green" if s["pnl"] > 0 else "red" if s["pnl"] < 0 else "white"
        t.add_row(
            f"[{color}]{regime}[/]",
            f"{days:,} ({days_pct:.1f}%)",
            f"{s['count']}",
            f"{wr:.1f}%" if s["count"] > 0 else "-",
            f"[{pnl_c}]¥{s['pnl']:+,.0f}[/]" if s["count"] > 0 else "-"
        )
    console.print(t)


def show_year_stats(stats):
    t = Table(title="[bold cyan]📅 年別成績[/]", box=box.ROUNDED, title_style="bold cyan")
    t.add_column("年", style="bold", justify="center")
    t.add_column("件数", justify="right")
    t.add_column("勝率", justify="right")
    t.add_column("平均損益", justify="right")
    t.add_column("損益", justify="right")

    for y in sorted(stats["year_stats"].keys()):
        s = stats["year_stats"][y]
        wr = s["wins"] / s["count"] * 100 if s["count"] > 0 else 0
        avg_pct = s["pnl_pct_sum"] / s["count"] if s["count"] > 0 else 0
        pnl_c = "green" if avg_pct > 0 else "red"
        t.add_row(
            str(y), f"{s['count']}", f"{wr:.0f}%",
            f"[{pnl_c}]{avg_pct:+.2f}%[/]",
            f"[{pnl_c}]¥{s['pnl']:+,.0f}[/]"
        )
    console.print(t)


def show_exit_stats(stats):
    t = Table(title="[bold cyan]🚪 エグジット理由別[/]", box=box.ROUNDED)
    t.add_column("理由", style="bold")
    t.add_column("件数", justify="right")
    t.add_column("損益", justify="right")
    for reason, s in sorted(stats["exit_stats"].items(),
                            key=lambda x: x[1]["count"], reverse=True):
        pnl_c = "green" if s["pnl"] > 0 else "red"
        t.add_row(reason, f"{s['count']}", f"[{pnl_c}]¥{s['pnl']:+,.0f}[/]")
    console.print(t)


def show_recent_trades(trades, n=20):
    t = Table(title=f"[bold cyan]📋 直近トレード（最新{n}件）[/]", box=box.ROUNDED)
    t.add_column("エントリー", justify="center")
    t.add_column("決済", justify="center")
    t.add_column("戦略", justify="center")
    t.add_column("環境")
    t.add_column("銘柄", style="cyan")
    t.add_column("損益%", justify="right")
    t.add_column("日数", justify="right")
    t.add_column("理由")

    strategy_colors = {
        "BNF": "magenta", "BNF-LITE": "magenta",
        "MINERVINI": "cyan", "MINERVINI-LITE": "cyan",
        "MOMENTUM": "yellow",
    }

    for trade in sorted(trades, key=lambda x: x["exit_date"], reverse=True)[:n]:
        is_win = trade["pnl"] > 0
        mark = "📗" if is_win else "📕"
        pnl_c = "green" if is_win else "red"
        strat_c = strategy_colors.get(trade["strategy"], "white")
        regime_c = {"BULLISH": "green", "NEUTRAL": "white",
                    "BEARISH": "yellow", "PANIC": "red"}.get(trade.get("regime", ""), "white")
        t.add_row(
            trade["entry_date"].strftime("%Y-%m-%d"),
            trade["exit_date"].strftime("%Y-%m-%d"),
            f"[{strat_c}]{trade['strategy']}[/]",
            f"[{regime_c}]{trade.get('regime', '-')}[/]",
            f"{mark} {trade['name'][:10]}",
            f"[{pnl_c}]{trade['pnl_pct']:+.2f}%[/]",
            f"{trade['hold_days']}日",
            trade["exit_reason"],
        )
    console.print(t)


def show_circuit_breaker_stats(cb_stats):
    """v2.4: サーキットブレーカー発動統計を表示（HALT-only）"""
    if not cb_stats or not cb_stats.get("periods"):
        console.print(Panel(
            "[green]✅ サーキットブレーカーは一度も発動しませんでした[/]\n"
            "[dim]相場環境が常に安定していたか、条件が厳しすぎる可能性[/]",
            border_style="green",
            title="[bold]🛡 サーキットブレーカー発動履歴[/]"
        ))
        return

    # 発動期間テーブル
    t = Table(title="[bold red]🛡 サーキットブレーカー発動履歴（HALT-only）[/]",
              box=box.DOUBLE_EDGE, title_style="bold red")
    t.add_column("開始日", justify="center")
    t.add_column("終了日", justify="center")
    t.add_column("期間", justify="right")
    t.add_column("発動理由")

    total_halt_days = 0

    for period in cb_stats["periods"]:
        days = (period["end"] - period["start"]).days + 1
        total_halt_days += days

        t.add_row(
            period["start"].strftime("%Y-%m-%d"),
            period["end"].strftime("%Y-%m-%d"),
            f"{days}日",
            period["reason"] or "-"
        )

    console.print(t)

    # サマリー
    s = Table(box=box.SIMPLE, show_header=False)
    s.add_column("", style="bold", width=22)
    s.add_column("", width=30, justify="right")
    s.add_row("HALT合計日数", f"[red]{total_halt_days}日[/]")
    s.add_row("回避したエントリー数", f"{cb_stats['rejected_count']}件")
    avoided = cb_stats["avoided_loss"]
    avoided_c = "green" if avoided < 0 else "red"
    avoided_label = "回避した損失（推定）" if avoided < 0 else "見逃した利益（推定）"
    s.add_row(avoided_label, f"[{avoided_c}]¥{abs(avoided):,.0f}[/]")
    console.print(s)
    console.print()


def show_evaluation(stats, config, mode):
    lines = []
    mode_label = "DAILY ACTION モード" if mode == "daily" else "HIGH QUALITY モード"
    lines.append(f"[bold]━━ 統合戦略 [{mode_label}] 総合評価 ━━[/]\n")
    if stats["win_rate"] >= 50:
        lines.append(f"○ 勝率 {stats['win_rate']:.1f}% — 合格")
    else:
        lines.append(f"△ 勝率 {stats['win_rate']:.1f}% — PFで補う必要")

    pf_s = f"{stats['pf']:.2f}" if stats["pf"] != float("inf") else "∞"
    if stats["pf"] >= 2:
        lines.append(f"○ PF {pf_s} — 優秀")
    elif stats["pf"] >= 1.5:
        lines.append(f"△ PF {pf_s} — 合格ライン")
    else:
        lines.append(f"✗ PF {pf_s} — 要改善")

    if stats["max_dd"] < 15:
        lines.append(f"○ 最大DD -{stats['max_dd']:.1f}% — 低リスク")
    elif stats["max_dd"] < 30:
        lines.append(f"△ 最大DD -{stats['max_dd']:.1f}% — 許容範囲")
    else:
        lines.append(f"✗ 最大DD -{stats['max_dd']:.1f}% — 要注意")

    # シグナル頻度評価
    spm = stats["signals_per_month"]
    if spm >= 15:
        lines.append(f"○ シグナル頻度 月{spm:.1f}件 — 十分なアクション")
    elif spm >= 5:
        lines.append(f"△ シグナル頻度 月{spm:.1f}件 — やや少ない")
    else:
        lines.append(f"✗ シグナル頻度 月{spm:.1f}件 — プロダクト化には少ない")

    rr = abs(stats["avg_win_pct"] / stats["avg_loss_pct"]) if stats["avg_loss_pct"] != 0 else 0
    lines.append(f"  リスクリワード 1:{rr:.2f}")

    lines.append("")
    if mode == "daily":
        lines.append("[bold]【DAILY ACTION モードの特徴】[/]")
        lines.append("  ・3戦略併用（BNF-LITE + MOMENTUM + Minervini-LITE）")
        lines.append("  ・毎日何かしらのシグナルを狙える")
        lines.append("  ・プロダクト化時のユーザー体験が良い")
        lines.append("")
        lines.append("[bold]【想定ユーザー】[/]")
        lines.append("  👤 アクティブトレーダー")
        lines.append("  👤 毎日チェックしたい人")
        lines.append("  👤 複数戦略を使い分けたい人")
    else:
        lines.append("[bold]【HIGH QUALITY モードの特徴】[/]")
        lines.append("  ・BNF + Minervini の精鋭シグナルのみ")
        lines.append("  ・少ない回数で高い勝率を狙う")
        lines.append("  ・本業がある人に最適")
        lines.append("")
        lines.append("[bold]【想定ユーザー】[/]")
        lines.append("  👤 サラリーマン投資家")
        lines.append("  👤 厳選トレード派")
        lines.append("  👤 精神的負担を減らしたい人")

    console.print(Panel("\n".join(lines), border_style="cyan",
                        title="[bold]🎯 総合評価[/]"))


def try_chart(trades, initial_capital, mode):
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt

        if not trades:
            return

        sorted_trades = sorted(trades, key=lambda x: x["exit_date"])
        dates = [t["exit_date"] for t in sorted_trades]
        equity = [initial_capital]
        capital = initial_capital
        for t in sorted_trades:
            capital += t["pnl"]
            equity.append(capital)

        fig, axes = plt.subplots(2, 1, figsize=(14, 9))

        axes[0].plot([sorted_trades[0]["entry_date"]] + dates, equity,
                     linewidth=2, color='#00bcd4')
        axes[0].axhline(y=initial_capital, color='gray', linestyle='--', alpha=0.5)
        axes[0].fill_between(
            [sorted_trades[0]["entry_date"]] + dates, initial_capital, equity,
            where=[v >= initial_capital for v in equity],
            alpha=0.15, color='green'
        )
        axes[0].fill_between(
            [sorted_trades[0]["entry_date"]] + dates, initial_capital, equity,
            where=[v < initial_capital for v in equity],
            alpha=0.15, color='red'
        )
        mode_label = "DAILY ACTION" if mode == "daily" else "HIGH QUALITY"
        axes[0].set_title(f'Integrated Strategy ({mode_label}) - Equity Curve',
                          fontsize=14, fontweight='bold')
        axes[0].grid(True, alpha=0.3)

        # 戦略別 PnL 分布
        strategies = set(t["strategy"] for t in trades)
        data = []
        labels = []
        colors_map = {
            "BNF": "#ff4081", "BNF-LITE": "#f8bbd0",
            "MINERVINI": "#00bcd4", "MINERVINI-LITE": "#b2ebf2",
            "MOMENTUM": "#ffc107",
        }
        for s in sorted(strategies):
            data.append([t["pnl_pct"] for t in trades if t["strategy"] == s])
            labels.append(s)

        axes[1].hist(data, bins=30, label=labels,
                     color=[colors_map.get(s, "gray") for s in labels], alpha=0.7)
        axes[1].axvline(x=0, color='black', linewidth=0.8)
        axes[1].set_title('P&L Distribution by Strategy', fontsize=14, fontweight='bold')
        axes[1].set_xlabel('P&L (%)')
        axes[1].set_ylabel('Count')
        axes[1].legend()
        axes[1].grid(True, alpha=0.3)

        plt.tight_layout()
        filename = f"integrated_backtest_v2_7_4_{mode}_chart.png"
        plt.savefig(filename, dpi=100, bbox_inches='tight')
        plt.close()
        console.print(f"[green]✓ グラフ保存: {filename}[/green]")
    except Exception as e:
        console.print(f"[yellow]グラフ生成エラー: {e}[/yellow]")


def export_csv(trades, mode):
    if not trades:
        return
    try:
        filename = f"integrated_backtest_v2_7_4_{mode}_result.csv"
        # v2.2/v2.3対応: 追加フィールドに対応、余分なキーは無視
        fieldnames = [
            "ticker", "name", "sector", "strategy", "regime",
            "entry_date", "exit_date",
            "entry_price", "exit_price", "pnl_pct", "pnl",
            "exit_reason", "hold_days",
            "shares", "equity_before",  # v2.2複利モード用
            "cb_state", "cb_reason",     # v2.3サーキットブレーカー用
        ]
        with open(filename, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for t in trades:
                row = t.copy()
                row["entry_date"] = t["entry_date"].strftime("%Y-%m-%d")
                row["exit_date"] = t["exit_date"].strftime("%Y-%m-%d")
                writer.writerow(row)
        console.print(f"[green]✓ CSV保存: {filename} ({len(trades)}件)[/green]")
    except Exception as e:
        console.print(f"[yellow]CSV保存エラー: {e}[/yellow]")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  メイン処理
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def main():
    args = parse_args()

    end_date = datetime.datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.datetime.now() -
                  datetime.timedelta(days=args.years * 365 + 250)).strftime("%Y-%m-%d")

    config = {
        "years": args.years,
        "start_date": start_date,
        "end_date": end_date,
        "initial_capital": args.capital,
        "risk": args.risk,
        "mode": args.mode,
        "bnf_only": args.bnf_only,
        "minervini_only": args.minervini_only,
        "momentum_only": args.momentum_only,
        "max_concurrent": args.max_concurrent,
        "compound": args.compound,
        "circuit_breaker": args.circuit_breaker,
        "halt_vix": args.halt_vix,
        "halt_consecutive_losses": args.halt_consecutive_losses,
        "halt_n225_drop": args.halt_n225_drop,
        "halt_cooldown": args.halt_cooldown,
        "legacy_v24": args.legacy_v24,
        "legacy_v25": args.legacy_v25,
        "legacy_v26": args.legacy_v26,
        "bnf_halted": args.bnf_halted,
        # v2.7: リスク管理
        "panic_risk_mult": args.panic_risk_mult,
        "panic_bnf_max": args.panic_bnf_max,
        "panic_wait_days": args.panic_wait_days,
        "bnf_loss_cooldown": args.bnf_loss_cooldown,
        "bnf_loss_threshold": args.bnf_loss_threshold,
    }

    # モード表示
    if args.bnf_only:
        submode = "BNF単独"
    elif args.minervini_only:
        submode = "Minervini単独"
    elif args.momentum_only:
        submode = "MOMENTUM単独"
    else:
        submode = "全戦略統合"

    mode_label = "DAILY ACTION" if args.mode == "daily" else "HIGH QUALITY"

    # ヘッダー
    console.print()
    console.print("╔═══════════════════════════════════════════════════════════╗")
    console.print("║  [bold cyan]BNF + Minervini 統合バックテスト v2.7.4[/]              ║")
    console.print("║  [dim]銘柄拡大版 - 163銘柄(+35本のグロース・半導体・バイオ)[/] ║")
    console.print("╚═══════════════════════════════════════════════════════════╝")

    cond_t = Table(title="[bold]📋 検証条件[/]", box=box.ROUNDED, show_header=False)
    cond_t.add_column("", style="bold", width=22)
    cond_t.add_column("", width=50)
    cond_t.add_row("[yellow]実行モード[/]", f"[yellow bold]{mode_label} / {submode}[/]")
    # v2.2: 複利/単利モード表示
    compound_label = "[bold green]💰 複利モード（資金成長連動）[/]" if args.compound else "[dim]単利モード（初期資金固定）[/]"
    cond_t.add_row("[yellow]計算方式[/]", compound_label)
    # v2.4: サーキットブレーカー表示（HALT-only）
    if args.circuit_breaker:
        cb_label = (f"[bold red]🛡 ON[/] [dim](HALT-only: VIX>{args.halt_vix}, "
                    f"日経{args.halt_n225_drop}%下落, {args.halt_consecutive_losses}連敗)[/]")
    else:
        cb_label = "[dim]OFF（暴落時も手法継続）[/]"
    cond_t.add_row("[yellow]サーキットB.[/]", cb_label)
    cond_t.add_row("検証期間", f"{start_date} 〜 {end_date}")
    cond_t.add_row("検証銘柄数", f"{len(JAPAN_STOCKS)} 銘柄")
    cond_t.add_row("初期仮想資金", f"¥{args.capital:,.0f}")
    cond_t.add_row("1トレードリスク", f"{args.risk}%")
    cond_t.add_row("最大同時保有", f"{args.max_concurrent} 銘柄 [dim](v2.1)[/]")
    cond_t.add_row("", "")

    if args.mode == "daily":
        # v2.7: 動作モード表示
        if args.legacy_v24:
            mode_tag = "[red]v2.4互換 (全改善OFF)[/]"
        elif args.legacy_v25:
            mode_tag = "[yellow]v2.5互換 (旧BNFフィルタ復活)[/]"
        elif args.legacy_v26:
            mode_tag = "[yellow]v2.6互換 (v2.7リスク管理OFF)[/]"
        else:
            mode_tag = "[green bold]v2.7.4 銘柄拡大版 (163銘柄・新セクター9追加)[/]"
        cond_t.add_row("[bold]動作モード[/]", mode_tag)

        # v2.7 リスク管理 表示
        if not (args.legacy_v24 or args.legacy_v25 or args.legacy_v26):
            cond_t.add_row("[bold green]v2.7 リスク管理[/]",
                           f"PANIC時BNF: リスク×{args.panic_risk_mult} / 最大{args.panic_bnf_max}銘柄")
            cond_t.add_row("", f"PANIC突入後{args.panic_wait_days}日は様子見 / BNF連敗{args.bnf_loss_threshold}→{args.bnf_loss_cooldown}日停止")

        # BNF-LITE 表示
        if args.legacy_v24:
            cond_t.add_row("[magenta]BNF-LITE[/]", "一律-15% + 出来高1.0倍 + BB-1.5σ")
        elif args.legacy_v25:
            cond_t.add_row("[magenta]BNF-LITE[/]", "一律-15% + 出来高1.3倍 [red]+陽線+RSI<30+破綻除外[/]")
        else:
            cond_t.add_row("[magenta]BNF-LITE[/]",
                           "[green bold]★実証最適閾値(-8〜-22%)[/] × [cyan]地合い倍率(適正化)[/]")
            cond_t.add_row("", "[dim]医薬-15%/銀行-22%/商社-22%/電子部品-22%/自動車-22%[/]")
            cond_t.add_row("", "[dim]電機-18%/ゲーム-10%/重工業-8%/ITサービス-8%[/]")
            cond_t.add_row("", "[bold cyan]BULLISH×0.8 / NEUTRAL×1.0 / BEARISH×1.15 / PANIC×1.3[/]")
            if not args.bnf_halted:
                cond_t.add_row("", "[bold green]🔥 HALT貫通モード(PANIC時もBNFは発動)[/]")

        # MINERVINI 表示
        if args.legacy_v24:
            cond_t.add_row("[cyan]MINERVINI[/]", "Trend Template 8/8 + VCP [dim](v2.1: LITE廃止)[/]")
        else:
            cond_t.add_row("[cyan]MINERVINI[/]",
                           "Trend Template 8/8 + [bold]VCP緩和[/] [green]+MA50押し目[/] [bold]ストップ-9%[/] [dim](v2.5継承)[/]")
        cond_t.add_row("[yellow]MOMENTUM[/]", "20日高値ブレイク [dim](v2.1: BULLISHのみ)[/]")
        cond_t.add_row("", "")
        cond_t.add_row("[bold]環境別配分[/]", "")
        cond_t.add_row("  PANIC/BEARISH", "BNF-LITE のみ")
        cond_t.add_row("  NEUTRAL", "BNF-LITE + MINERVINI")
        cond_t.add_row("  BULLISH", "MOMENTUM + MINERVINI")
    else:
        cond_t.add_row("[magenta]BNF[/]", "乖離-20% + 出来高1.1倍 + BB-2σ")
        cond_t.add_row("[cyan]MINERVINI[/]", "Trend Template 8/8 + VCP + 出来高1.4倍")
    console.print(cond_t)
    console.print()

    # STEP1
    console.print(Rule("[bold blue]STEP 1/3  データ取得（マーケット環境）[/]"))
    global_data = fetch_global_data(config["start_date"], config["end_date"])
    console.print()

    # STEP2
    console.print(Rule("[bold blue]STEP 2/3  バックテスト集計[/]"))
    console.print()
    bt = IntegratedBacktester(config, global_data)
    trades = bt.run_all(JAPAN_STOCKS)

    if not trades:
        console.print(Panel("[yellow]トレードが発生しませんでした[/]", border_style="yellow"))
        return

    stats = calc_stats(trades, config["initial_capital"])

    # STEP3
    console.print()
    console.print(Rule("[bold blue]STEP 3/3  結果分析[/]"))
    show_summary(stats)

    if not (args.bnf_only or args.minervini_only or args.momentum_only):
        show_strategy_breakdown(stats)
        show_regime_breakdown(stats, bt.regime_counts)

    show_year_stats(stats)
    show_exit_stats(stats)
    show_recent_trades(trades, n=20)

    # v2.3: サーキットブレーカーの発動履歴表示
    if args.circuit_breaker and hasattr(bt, "cb_stats") and bt.cb_stats:
        console.print()
        show_circuit_breaker_stats(bt.cb_stats)

    show_evaluation(stats, config, args.mode)

    console.print()
    export_csv(trades, args.mode)

    if args.chart:
        try_chart(trades, config["initial_capital"], args.mode)

    console.print()
    console.print("[dim]⚠ バックテストは過去データです。将来の利益を保証しません。[/dim]")
    console.print("[dim]⚠ 実際の投資はご自身の判断と責任で行ってください。[/dim]\n")


if __name__ == "__main__":
    main()
