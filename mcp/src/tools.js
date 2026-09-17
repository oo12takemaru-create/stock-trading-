// Week 1 無料ツール4本。全部「既存の公開JSONを読んで整形する」だけ。
import { DISCLAIMER } from "./legal.js";

export const SERVER_NAME = "ruletrade-mcp";
export const SERVER_VERSION = "0.2.0";

// 正規URL。ruletrade.jp(Vercel)から Cloudflare Workers へ rewrite している。
// Worker 側から見た url.origin は workers.dev のままなので、定数で持つ。
// 配信先を変えるときは vercel.json の1行と、ここ(または環境変数 PUBLIC_ORIGIN)だけ直す。
export const CANONICAL_ORIGIN = "https://ruletrade.jp";
export const CANONICAL_ENDPOINT = `${CANONICAL_ORIGIN}/mcp`;
export const HOME_URL = "https://ruletrade.jp/";

const REGIME_LABEL = {
  BULLISH: "強気",
  NEUTRAL: "中立",
  BEARISH: "弱気",
  PANIC: "パニック",
};

const BNF_RULE =
  "終値の25日移動平均線からの乖離率が -15% 以下(書籍『BNFに学ぶ』掲載の基本ルール。終値ベースで未来情報は使わない)";

// ジンクス(アノマリー)50本の検証結果 docs/anomaly_results.json を読むための定数。
// 生成側は `株式投資開発/jinx_verification/`(このリポジトリの外)。MCP は読むだけ。
const JINX_LEGEND = {
  "◎": "今も有効(全期間・直近10年とも統計的に有意)",
  "○": "昔は有効(全期間では有意だが直近10年では消えた)",
  "▲": "最近になって現れた(全期間では有意でないが直近10年では有意)",
  "△": "誤差の範囲(方向は言い伝えどおりだが有意でない)",
  "×": "迷信(方向が逆、または無関係)",
  "?": "判定保留(標本が15未満で統計判定に耐えない)",
  "→": "本書の対象外(姉妹書で検証)",
};

const JINX_METHOD =
  "日経平均株価などの公開価格データ(61年8か月・15,154営業日)を用いた機械検証。" +
  "p値はウェルチのt検定(該当率の検証は1標本t検定)。「直近10年」は2016年8月〜2026年8月。" +
  "配当・手数料・税金は考慮していない。検証不能・代用データを使った項目は note に記載。";

const JINX_SOURCE =
  "書籍『株のジンクス・アノマリー全50本を61年で全検証』(ASIN B0HGRKBZP6)の検証データ";

// 小数の桁を落とす(統計値の見た目を揃え、返却量も減らす)
const r2 = (v, d = 2) => (typeof v === "number" && Number.isFinite(v) ? Number(v.toFixed(d)) : null);

// p値は 1e-8 のような極端に小さい値を取る。固定小数で丸めると 0 になり
// 「p=0」という有り得ない値をエージェントに読ませてしまうので、有効数字で丸める。
const rp = (v) => (typeof v === "number" && Number.isFinite(v) ? Number(v.toPrecision(3)) : null);

function jinxMetric(m, detail) {
  const o = {
    period: m.period, // full = 全期間 / recent10 = 直近10年
    n: m.n,
    win_rate: r2(m.win_rate),
    mean: r2(m.mean, 3),
    diff_vs_control: r2(m.diff, 3),
    p: rp(m.p),
    significant: typeof m.p === "number" && Number.isFinite(m.p) ? m.p < 0.05 : null,
  };
  // 元データ側で p が浮動小数の下限を下回って 0 になっている項目がある。
  // 「p=0」と読ませないよう、値は素通ししたうえで意味を添える。
  if (m.p === 0) o.p_note = "元データで 0。厳密な 0 ではなく、倍精度で表せないほど小さい値";
  if (detail) {
    o.median = r2(m.median, 3);
    o.std = r2(m.std, 3);
    o.control_n = m.ctrl_n ?? null;
    o.control_mean = r2(m.ctrl_mean, 3);
  }
  return o;
}

// 返すのは判定結果と統計値のみ。価格系列・財務値は返さない(企画書 §9)。
// saying(格言の本文)は推奨語を含むため出力に含めない。name と definition で同定できる。
function jinxItem(x, detail, compact) {
  // 一覧(全50本)は判定だけの軽い形にする。統計値は name で絞り込むか detail=true で返す。
  if (compact) {
    return {
      id: x.id,
      name: x.name,
      category: x.category,
      judgment: x.judgment,
      verified: Array.isArray(x.metrics) && x.metrics.length > 0,
    };
  }
  const o = {
    id: x.id,
    name: x.name,
    category: x.category,
    judgment: x.judgment,
    judgment_label: JINX_LEGEND[x.judgment] || "",
    definition: x.definition,
    unit: x.unit,
    expected_direction: x.expect > 0 ? "up" : x.expect < 0 ? "down" : "none",
    // データが取得できず検証できなかった項目・姉妹書送りの項目は metrics が空。理由は note にある
    verified: Array.isArray(x.metrics) && x.metrics.length > 0,
    metrics: (x.metrics || []).map((m) => jinxMetric(m, detail)),
  };
  if (x.note) o.note = x.note;
  if (detail && x.extra && Object.keys(x.extra).length) {
    o.extra = Object.fromEntries(
      Object.entries(x.extra).map(([k, v]) => [k, typeof v === "number" ? r2(v, 4) : v]),
    );
  }
  return o;
}

// ---- ツール定義(tools/list で返す) ----------------------------------------
export const TOOL_DEFS = [
  {
    name: "get_daily_signals",
    title: "公開ルールに該当した銘柄(1件・1営業日遅れ)",
    description:
      "公開ルール(BNF 25日線乖離が -15% 以下)に該当した銘柄を返す。" +
      "★返すのは1件だけ★(該当した全銘柄の一覧は配布しない)。件数は返すので「今日は何件あったか」は分かる。" +
      "1営業日遅れ(前営業日の終値で判定)。価格・株数・利確/損切ラインは含まない。" +
      "該当は機械的な条件への一致であって、売買の判断ではない。",
    inputSchema: {
      type: "object",
      properties: {
        strategy: {
          type: "string",
          enum: ["bnf"],
          default: "bnf",
          description: "ルール名。現在は bnf(25日線乖離の逆張りルール)のみ",
        },
      },
      additionalProperties: false,
    },
  },
  {
    name: "get_market_regime",
    title: "地合い判定(BULLISH/NEUTRAL/BEARISH/PANIC)と3軸スコア",
    description:
      "本番システムが日中に更新している相場環境の判定、サーキットブレーカー(HALT)状態、VIX、日経平均、当日シグナル件数を返す。" +
      "あわせて3軸スコア(トレンド・短期リスク・需給をそれぞれ -2〜+2 で採点し、合計 -6〜+6 を5段階に分けたもの)と、" +
      "各軸がその点数になった理由・5営業日以内の大型イベントを返す。" +
      "銘柄名は含まない。オプションで日次履歴も返す。相場の予想ではない。",
    inputSchema: {
      type: "object",
      properties: {
        history_days: {
          type: "integer",
          minimum: 0,
          maximum: 60,
          default: 0,
          description: "直近N日分の地合い履歴を含める(0=含めない、最大60)",
        },
      },
      additionalProperties: false,
    },
  },
  {
    name: "get_anomaly_summary",
    title: "アノマリー検証結果(ジンクス50本)+暴落前兆(傾斜計5・着火メーター7)",
    description:
      "日本株の言い伝え(アノマリー/ジンクス)50本を61年分の日経平均データで検証した結果(判定・勝率・平均リターン・標本数・p値)を返す。" +
      "name を渡すとその名前を含むものだけに絞り込める(例: セルインメイ, 節分天井, 干支)。" +
      "あわせて、5つの暴落前兆(逆イールド・CAPE・信用膨張・過熱・引き締め)の点灯状況と、着火メーター(7フラグ・20営業日以内に-10%が起きた過去の割合)の現在値も返す。" +
      "detail=true で年代別の内訳・統計表・書籍上の根拠を含める。返すのは判定結果と統計値のみで、価格系列や財務値は含まない。",
    inputSchema: {
      type: "object",
      properties: {
        name: {
          type: "string",
          description:
            "アノマリー名またはカテゴリの部分一致で絞り込む(例: セルインメイ / 節分 / 曜日 / 都市伝説)。省略すると全50本の要約を返す",
        },
        detail: {
          type: "boolean",
          default: false,
          description:
            "年代別の内訳(extra)・中央値/標準偏差/対照群・書籍上の根拠(book)・着火メーターの統計表と履歴を含めるか",
        },
      },
      additionalProperties: false,
    },
  },
  {
    name: "get_candlestick_verdict",
    title: "酒田五法12本の検証結果(採用ゼロ本)",
    description:
      "三尊天井・赤三兵・三空叩き込みなど酒田五法12本を東証プライム1,550銘柄・10年半で検証した結果を返す。" +
      "取引回数・勝率・PF・平均超過収益・p値・負け年・5つの採用基準のどれで落ちたか・不採用の理由を含む。" +
      "12本すべてが採用基準を満たさなかった(採用ゼロ本)。" +
      "最も PF の高い三空叩き込み(PF 3.04)も、期間を前半と後半に分けると符号が反転するため不採用としている。" +
      "pattern を渡すとその形だけに絞り込める(日本語・英語の別名可。例: 三尊, head and shoulders, 赤三兵)。" +
      "detail=true で保有日数別・相場環境別の内訳を含める。銘柄名・銘柄コード・価格は含まない。",
    inputSchema: {
      type: "object",
      properties: {
        pattern: {
          type: "string",
          description:
            "形の名前で絞り込む(部分一致・日英の別名可。例: 三尊 / 逆三尊 / 三空 / 赤三兵 / morning star)。省略すると12本の一覧を返す",
        },
        detail: {
          type: "boolean",
          default: false,
          description:
            "保有日数別(5/10/20営業日)の内訳・75日移動平均で分けた相場環境別の内訳・検証に使った規定値を含めるか",
        },
      },
      additionalProperties: false,
    },
  },
  {
    name: "get_event_reaction",
    title: "出来事のあとに何が起きたか(3つの検証)",
    description:
      "ある出来事のあとに何が起きたかを、3つの検証からまとめて返す。" +
      "(1)出来事27種 — 地震・利上げ・円安・関税・パンデミックなどのあと、市場全体を上回る動きがあったか。" +
      "判定は 持続型/初動型/不発/逆行/市場全体のみ の5種類と、5つの窓(当日/翌日/5営業日後/1か月後/3か月後)の超過収益・勝率・p値。" +
      "(2)決算後のドリフト — 決算発表のあと値動きが続くか。約26,000件の決算で検証。" +
      "(3)指数の入替 — TOPIX の採用・除外でいつ・どちら向きに動くか。" +
      "3つは別々のデータから同じ形を示している: 反応は出来事を通過する前か、ごく初期に終わる。" +
      "出来事27種の「初動型」は翌朝の寄り付きで終わり、決算後のドリフトは+5日で統計的に消え、指数の入替は実施日で止まる。" +
      "event を渡すと絞り込める(例: 地震, earthquake, 利上げ, 決算, PEAD, TOPIX, 日経225)。省略すると3つの見出しを返す。" +
      "銘柄名・銘柄コード・銘柄バスケットは含まない。返すのは分類と統計だけ。",
    inputSchema: {
      type: "object",
      properties: {
        event: {
          type: "string",
          description:
            "出来事の名前で絞り込む(部分一致・日英の別名可)。" +
            "出来事27種: 地震 / earthquake / 利上げ / 円安 / 関税 / パンデミック など。" +
            "決算後のドリフト: 決算 / earnings / PEAD。" +
            "指数の入替: TOPIX / 日経225 / 除外 / 採用。" +
            "省略すると3つの検証の見出しと、出来事27種の一覧を返す",
        },
        detail: {
          type: "boolean",
          default: false,
          description:
            "絞り込んだときに、分類前の全標本数・各窓での市場全体の動き・" +
            "決算の区分別内訳(発表時刻/四半期/規模/期間)とルール別の成績まで含めるか。" +
            "event を省略したときは効かない(全件の詳細は 50KB を超えるため)",
        },
      },
      additionalProperties: false,
    },
  },
  {
    name: "get_indicator_verdict",
    title: "テクニカル指標26種の検証結果(使える9 / 条件付き4 / 捨てろ13)",
    description:
      "RSI・MACD・移動平均クロス・ボリンジャーバンドなどテクニカル指標26種を、教科書どおりの買い方で TOPIX500・10年・約50万回の売買として検証した結果を返す。" +
      "勝率・期待値・PF・ランダムな売買との超過収益を、出口2種類(20営業日で手仕舞い / 8%トレーリング)で返す。" +
      "判定は 使える9個 / 条件付き4個 / 捨てろ13個 の3段。「使える」の基準は8区分(出口2 × 上昇/下落/前半/後半)すべてで超過収益が +0.3% を超えること。" +
      "捨てろ13個には入門書の最初に出てくる指標が並んでいる。" +
      "name を渡すとその指標だけに絞り込める(日本語・英語の別名可。例: RSI, 一目, ichimoku, 乖離率)。" +
      "detail=true で8区分すべての内訳を含める。銘柄名・銘柄コード・価格は含まない。",
    inputSchema: {
      type: "object",
      properties: {
        name: {
          type: "string",
          description:
            "指標名またはカテゴリで絞り込む(部分一致・日英の別名可。例: RSI / MACD / ボリンジャー / オシレーター / volume)。省略すると26種の一覧を返す",
        },
        detail: {
          type: "boolean",
          default: false,
          description: "8区分(出口A・B × 上昇/下落/前半/後半)それぞれの超過収益を含めるか",
        },
      },
      additionalProperties: false,
    },
  },
  {
    name: "get_etf_decay",
    title: "レバレッジ・インバースETFの減価(12年の実測)",
    description:
      "日経ダブルインバース(1357)などレバレッジ・インバース型ETFを12年・2,964営業日ぶん実測した結果を返す。" +
      "年別の理論値と実績・保有日数別の勝率と理論との乖離・地合い条件別の PF と最大ドローダウン・「戻るまで待つ」を305回試した結果を含む。" +
      "日々の値動きを2倍にする商品なので、上下を往復するだけで元に戻らない(日経が+10%のあと-9.1%で元の水準に戻る2日間で、100→80→94.5)。" +
      "検証結果には各節の「解釈で注意すべき点」を必ず添えて返す(日経が12年で約4倍になった上昇相場のデータであるため)。" +
      "detail=true で地合い条件別の72通りを含める。日足の価格系列と個別株の銘柄情報は含まない。",
    inputSchema: {
      type: "object",
      properties: {
        code: {
          type: "string",
          description:
            "検証対象のETF(1357=ダブルインバース / 1570=レバレッジ)。省略すると両方を含む全体を返す",
        },
        detail: {
          type: "boolean",
          default: false,
          description:
            "地合い条件別(75日線割れ初日・200日線割れ初日など8条件 × 保有日数4 × コスト2 = 72通り)の PF と最大ドローダウンを含めるか",
        },
      },
      additionalProperties: false,
    },
  },
  {
    name: "list_tools_guide",
    title: "使い方ガイド・データ更新時刻・免責",
    description:
      "このサーバーの全ツール(8本)の説明、データの更新タイミングと遅延、返さないものの一覧、免責事項、今後の予定を返す。最初に一度呼ぶことを想定。",
    inputSchema: { type: "object", properties: {}, additionalProperties: false },
  },
];

// ---- 各ツールの実装 ------------------------------------------------------
function meta(extra) {
  return {
    disclaimer: DISCLAIMER,
    source_site: HOME_URL,          // ルール・検証の公開元
    data_site: "https://kaburadar.jp", // 日次データ(地合い・傾斜計・着火メーター)の公開元
    ...extra,
  };
}

/**
 * 公開ルールに該当した銘柄。docs/free_scanner.json を読むだけ。
 *
 * ★返す銘柄は1件だけ（2026-09-17 に上位3件から縮小）★
 * 引継ぎ §12-5「rule_hits.json は全銘柄の該当リストを公開しない。by_code はその日1件のみ」
 * §19-9「登録なしで出すのは銘柄名1件＋地合いラベルまで」。
 * サイトの無料面と同じ線に揃えた。**銘柄リストを配る道具にしない。**
 *
 * 件数は返す。「今日は何件あったか」は銘柄を明かさずに答えられる事実で、
 * エージェントが「今日は静かか」を判断するのに要る。
 */
async function getDailySignals(args, { load }) {
  const d = await load("free_scanner.json");
  const rows = Array.isArray(d.rows) ? d.rows : [];
  const hits = rows.filter((r) => r.judge === "signal");
  const watch = rows.length - hits.length;

  // ★1件だけ★ 乖離が最も大きいルール該当。該当が無ければ null
  const top = hits[0]
    ? {
        code: String(hits[0].code),
        name: hits[0].name,
        sector: hits[0].sector,
        ma25_deviation_pct: hits[0].kairi,
        status: "rule_hit",
        status_label: "ルール該当(-15%以下)",
      }
    : null;

  // 本番システム(3戦略統合)で当日初出の1件。free_scanner 側で既に1件に絞ってある
  const sod = d.signal_of_day
    ? {
        code: String(d.signal_of_day.code),
        name: d.signal_of_day.name,
        strategy_label: d.signal_of_day.strategy_label,
        reason: d.signal_of_day.reason,
        carried_over: !!d.signal_of_day.carried_over,
        note: "本番システム(3戦略統合)で当日初出の銘柄のうち最も早い1件。価格・株数は含まない",
      }
    : null;

  return meta({
    strategy: "bnf",
    rule: BNF_RULE,
    target_date: d.target_date,
    generated_at: d.generated_at,
    data_delay: "1営業日(前営業日の終値で判定)",
    market_condition: d.jiai,
    is_halt: !!d.is_halt,
    // ★銘柄は1件だけ★
    top_hit: top,
    top_hit_note: top
      ? "公開ルールに該当した銘柄のうち、乖離が最も大きい1件。該当した全銘柄の一覧は配布しない"
      : "この対象日に公開ルールへ該当した銘柄は、観測した範囲では無かった",
    counts: {
      rule_hit: hits.length,
      watch: watch,
      scope: "乖離率の上位3件の中での内訳。全上場銘柄での該当数ではない",
    },
    full_system_today: {
      date: d.signals_today_date ?? null,
      signal_count: d.signals_today_count ?? null,
      published_one: sod,
      note:
        "本番システムは3つの戦略を回している。signal_count はその当日の合計で、" +
        "公開するのは最も早い1件だけ。target_date(1営業日前)とは別の日付になりうる",
    },
    limits:
      "銘柄は1件のみ。該当した全銘柄の一覧・利確/損切ライン・株数・" +
      "本番システムが使っている調整済みの閾値は含まない",
    how_to_read: [
      "rule_hit は「公開ルールの条件に機械的に該当した」という事実で、売買の判断ではない",
      "counts は上位3件の中での内訳。市場全体で何件あったかではない",
      "データは1営業日遅れ。前営業日の終値で判定している",
    ],
  });
}

async function getMarketRegime(args, { load }) {
  const n = Math.max(0, Math.min(60, Number(args.history_days) || 0));
  const [radar, jiai, s3] = await Promise.all([
    load("radar.json"),
    load("market_jiai.json").catch(() => null),
    // 3軸スコア。まだ配信されていない日もあるので任意データ扱い
    load("score3.json").catch(() => null),
  ]);
  const out = meta({
    updated: radar.updated,
    scanner_timestamp: radar.scanner_timestamp,
    regime: radar.regime,
    regime_label: REGIME_LABEL[radar.regime] || radar.regime,
    regime_meaning: {
      BULLISH: "順張り(ブレイク)+成長株が主力",
      NEUTRAL: "逆張り(乖離)+成長株",
      BEARISH: "逆張り(乖離)のみ",
      PANIC: "逆張り(乖離)のみ・リスク半減",
    },
    is_halt: !!radar.is_halt,
    halt_reason: radar.halt_reason || "",
    halt_rules: "VIX>35 / 日経1ヶ月変化率<-15% / 5連敗 のいずれかでHALT(順張り・成長株を停止)。クールダウン5営業日",
    vix: radar.vix ?? null,
    n225: radar.n225 ?? null,
    signal_count_today: radar.signal_count ?? 0,
    daily_close_basis: jiai
      ? { target_date: jiai.target_date, jiai: jiai.jiai, nikkei_close: jiai.nikkei_close, generated_at: jiai.generated_at }
      : null,
  });
  // ★3軸スコア（トレンド・短期リスク・需給）★
  // regime(BULLISH〜PANIC)は戦略の切り替えに使う分類、こちらは「今どちらに傾いているか」を
  // 軸ごとに分けて見るもの。別物なので混ぜずに並べる。
  out.score3 = s3
    ? {
        updated: s3.updated,
        trade_date: s3.trade_date,
        total: s3.total,
        range: s3.range,
        stance: s3.stance,
        stance_label: s3.stance_jp,
        stages: s3.stages,
        axes: (s3.axes || []).map((a) => ({
          key: a.key,
          label: a.label,
          score: a.score,
          // 何を見てその点数になったか。数字だけ渡すと根拠が分からない
          reasons: a.notes,
        })),
        events_within_5days: s3.events_5d || [],
        inputs_ok: s3.inputs_ok !== false,
        note:
          "3つの軸をそれぞれ -2〜+2 で採点し、合計(-6〜+6)を5段階に分けたもの。" +
          "相場の予想ではなく、公開データが今どちらに傾いているかの整理",
        // ★食い違うことがある。それを黙っていると誤読される★
        // regime は本番システムがザラ場の値も見て戦略を切り替えるための分類、
        // 3軸スコアは前営業日の終値をもとにした整理。基準も更新時刻も違う。
        vs_regime:
          "上の regime とは基準が違うので、食い違うことがある。" +
          "regime は本番システムが戦略を切り替えるための分類でザラ場の値も反映する。" +
          "3軸スコアは前営業日の終値をもとにした整理で、日次更新。" +
          "どちらかが間違っているのではなく、見ているものが違う",
      }
    : {
        available: false,
        reason: "3軸スコア(score3.json)を取得できなかった。他の項目は返している",
      };

  out.how_to_read = [
    "regime は本番システムが戦略を切り替えるための分類で、相場の予想ではない",
    "regime と score3 は基準が違うので食い違うことがある(score3.vs_regime を参照)",
    "score3 の各軸は reasons に根拠が入っている。点数だけで読まない",
    "VIX などの値は regime と score3 で更新時刻が違うため、わずかにずれることがある",
    "銘柄名は含まない。件数と市場全体の数字だけを返す",
  ];

  if (n > 0) {
    const h = await load("radar_history.json").catch(() => ({ items: [] }));
    out.history = (h.items || []).slice(-n).map((x) => ({
      date: x.d,
      regime: x.regime,
      vix: x.vix,
      n225: x.n225,
      signal_count: x.sig,
      is_halt: !!x.halt,
    }));
  }
  return out;
}

async function getAnomalySummary(args, { load, env }) {
  const detail = !!args.detail;
  const q = typeof args.name === "string" ? args.name.trim() : "";
  const [g, c, jinxRaw] = await Promise.all([
    load("gauge.json"),
    load("crash.json"),
    // ジンクス50本。無くても他の2つは返せるようにする(任意データ扱い)
    load("anomaly_results.json").catch(() => null),
  ]);

  const gauges = (g.gauges || []).map((x) => {
    const o = {
      no: x.no,
      key: x.key,
      label: x.label,
      on: !!x.on,
      value: x.value,
      criterion: x.criterion,
      detail: x.detail,
      asof: x.asof,
      stale: !!x.stale,
      source: x.source,
    };
    if (detail) o.book = x.book;
    return o;
  });

  const flags = (c.flags || []).map((f) => ({
    key: f.key,
    label: f.label,
    on: !!f.on,
    value: f.value,
    threshold: f.threshold,
    distance: f.distance,
    why: f.why,
  }));

  const out = meta({
    book: g.book || null,
    precursor_gauges: {
      updated: g.updated,
      lit: g.lit,
      total: g.total,
      stage: g.stage,
      stage_key: g.stage_key,
      message: g.message,
      items: gauges,
    },
    aftershock_phase: g.phase
      ? {
          stage: g.phase.stage,
          stage_key: g.phase.stage_key,
          vix: g.phase.vix,
          vix_band: g.phase.vix_band,
          adr: g.phase.adr,
          drawdown_from_peak_pct: g.phase.drawdown,
          peak_date: g.phase.peak_date,
          shock_date: g.phase.shock_date,
          message: g.phase.message,
          book_note: g.phase.action,
        }
      : null,
    ignition_meter: {
      updated: c.updated,
      trade_date: c.trade_date,
      n225: c.n225,
      definition: `フラグ点灯数に応じて、${c.horizon}営業日以内に日経平均が${Math.round((c.crash_def || -0.1) * 100)}%以上下落した過去の割合(${c.period?.all || ""})`,
      score: c.score,
      flag_total: c.flag_total,
      stage: c.stage,
      stage_key: c.stage_key,
      historical_drop_rate_pct: c.prob,
      historical_drop_rate_recent_pct: c.prob_recent,
      ratio_vs_base: c.ratio,
      flags,
    },
    note: "点灯は「過去にそうだった割合」であり、将来の下落を予測・保証するものではない",
  });
  if (detail) {
    out.ignition_meter.stats = c.stats;
    out.ignition_meter.history = (c.history || []).slice(-30).map((x) => ({ date: x.d, score: x.s }));
  }
  // ジンクス(アノマリー)50本の検証結果。docs/anomaly_results.json を読むだけ。
  const all = Array.isArray(jinxRaw) ? jinxRaw : null;
  if (all) {
    const key = q.toLowerCase();
    const hits = key
      ? all.filter(
          (x) =>
            String(x.name || "").toLowerCase().includes(key) ||
            String(x.category || "").toLowerCase().includes(key),
        )
      : all;
    const compact = !q && !detail;
    const counts = {};
    for (const x of all) counts[x.judgment] = (counts[x.judgment] || 0) + 1;
    out.jinx_verification = {
      source: JINX_SOURCE,
      method: JINX_METHOD,
      total: all.length,
      judgment_legend: JINX_LEGEND,
      judgment_counts: counts,
      categories: [...new Set(all.map((x) => x.category))],
      query: q || null,
      matched: hits.length,
      items: hits.map((x) => jinxItem(x, detail, compact)),
    };
    if (compact) {
      out.jinx_verification.note =
        "一覧のため判定のみ。勝率・平均リターン・標本数・p値が要るときは name に名前かカテゴリを渡す(部分一致)か、detail=true を指定する";
    }
    if (q && hits.length === 0) {
      out.jinx_verification.available_names = all.map((x) => x.name);
      out.jinx_verification.hint = "name は部分一致。上の available_names から選び直すか、name を省略して全件を取得する";
    }
  } else {
    out.jinx_verification = { available: false, reason: "検証データ(anomaly_results.json)を取得できなかった" };
  }
  // 別配信先で差し替えたい場合は ANOMALY_URL(構造はそのまま返す)
  if (env && env.ANOMALY_URL) {
    try {
      const r = await fetch(env.ANOMALY_URL, { headers: { "user-agent": "ruletrade-mcp/0.1" } });
      if (r.ok) out.jinx_verification_external = await r.json();
    } catch {
      /* 任意データなので失敗は無視 */
    }
  }
  return out;
}

// ---- 書籍の検証結果を返す2本(v0.2) ------------------------------------------
// どちらも「検証したが採用しなかった」を含めて返す。
// 効いたものだけを並べると、当てはめで生まれた数字を選り分けたことが伝わらない。

// 名前・別名・IDのどれかに部分一致するか。エージェントは日本語でも英語でも聞いてくる
function nameHit(x, key, extraFields = []) {
  const pool = [x.name, ...(x.aliases || []), ...extraFields.map((f) => x[f])];
  return pool.some((s) => String(s || "").toLowerCase().includes(key));
}

/**
 * 酒田五法12本の検証結果。docs/candlestick_verdict.json を読むだけ。
 * 生成側は mcp/tools/build_candlestick_verdict.py(書籍32の検証出力から機械変換)。
 *
 * ★このツールで最も価値があるのは「PF が高くても採用できない理由」★
 * 三空叩き込みは PF 3.04 と最も見栄えのする数字が出たが、期間を発見期と確認期に
 * 分けると符号が反転するため不採用になっている。rejection_reason を要約しないこと。
 */
async function getCandlestickVerdict(args, { load }) {
  const q = typeof args.pattern === "string" ? args.pattern.trim() : "";
  const detail = !!args.detail;
  const d = await load("candlestick_verdict.json");
  const all = Array.isArray(d.patterns) ? d.patterns : [];
  const key = q.toLowerCase();
  const hits = key ? all.filter((p) => nameHit(p, key, ["pattern_id"])) : all;
  // 一覧(12本)は判定と落ちた基準だけの軽い形に。統計値は pattern で絞るか detail=true
  const compact = !q && !detail;

  const items = hits.map((p) => {
    if (compact) {
      return {
        pattern_id: p.pattern_id,
        name: p.name,
        direction: p.direction,
        verdict: p.verdict,
        pf: p.primary.pf,
        trades: p.primary.trades,
        // なぜ落ちたかは一覧でも出す。「不採用」だけだと理由が伝わらない
        checks_failed: p.primary.checks_failed,
        filter_candidate: !!p.filter_candidate,
      };
    }
    const o = {
      pattern_id: p.pattern_id,
      name: p.name,
      aliases: p.aliases,
      direction: p.direction,
      direction_note: p.direction_note,
      verdict: p.verdict,
      filter_candidate: !!p.filter_candidate,
      universe: p.universe,
      period: p.period,
      entry: p.entry,
      exit: p.exit,
      primary: p.primary,
      rejection_reason: p.rejection_reason,
    };
    if (p.filter_note) o.filter_note = p.filter_note;
    if (detail) {
      o.by_horizon = p.by_horizon;
      o.regime = p.regime;
      o.params_tested = p.params_tested;
      // 別基盤の参考値。★注記ごと返す★ 数字だけ渡すと採用判定に見える
      if (p.size_reference) o.size_reference = p.size_reference;
    }
    return o;
  });

  const out = meta({
    source: d.source_book,
    universe: d.universe,
    period: d.period,
    method: d.method,
    criteria: d.criteria,
    policy: d.policy,
    // ★結論を先に置く★ 一覧を全部読まなくても分かるように
    headline: d.headline,
    summary: d.summary,
    key_finding: d.key_finding,
    verdict_values: d.verdict_values,
    not_included: d.not_included,
    query: q || null,
    matched: hits.length,
    items,
    how_to_read: [
      "verdict=not_adopted は「著者の採用基準(5つ)を満たさなかった」という事実で、形が必ず外れるという意味ではない",
      "checks_failed にどの基準で落ちたかが入る。PF だけを見て判断しないこと",
      "mean_excess_pct は同じ期間の市場全体を引いた超過収益。単体のリターンではない",
      "sensitivity_same_sign は「規定値をずらしても符号が変わらなかった回数 / 試した回数」",
      "filter_candidate=true は、トレードルールとしては不採用だが別枠で保持しているもの",
    ],
    note: d.disclaimer,
  });
  if (compact) {
    out.items_note =
      "一覧のため判定と落ちた基準のみ。勝率・平均超過・p値・不採用の理由が要るときは pattern に形の名前を渡す(部分一致)か、detail=true を指定する";
  }
  if (q && hits.length === 0) {
    out.available_patterns = all.map((p) => p.name);
    out.hint = "pattern は部分一致(日英の別名可)。上の available_patterns から選び直すか、pattern を省略して全件を取得する";
  }
  return out;
}

// 決算の検証を引き当てるための言葉。単一のデータセットなので名前を持たない
const EARNINGS_KEYS = [
  "決算", "earnings", "pead", "ドリフト", "drift", "サプライズ", "surprise",
  "短信", "業績", "発表", "またぎ", "跨ぎ",
];

/**
 * 出来事のあとに何が起きたか。3つの検証をまとめて引く。
 *
 *   docs/event_reaction.json        … 出来事27種（書籍36）地震・利上げ・関税ほか
 *   docs/earnings_drift.json        … 決算後ドリフト（書籍31）
 *   docs/supply_demand_events.json  … 指数の入替（書籍29）
 *
 * ★3つが同じ形を示しているので1つのツールにまとめる★
 * 連想の「初動型」、決算の「+5日で消える」、需給の「通過したら終わり」は
 * 別々のデータから出た同じ結論。並べて返すことに意味がある。
 *
 * ★銘柄バスケットと明細は元データ側で開いていない★
 * 連想の baskets.csv（499行）、決算の events_raw.csv、需給の buyback_*.csv は
 * いずれも銘柄コード・企業名つき。混ぜた瞬間に「出来事 → 買う銘柄」になる。
 */
async function getEventReaction(args, { load }) {
  const q = typeof args.event === "string" ? args.event.trim() : "";
  const detail = !!args.detail;
  const key = q.toLowerCase();

  // 1つでも欠けたら他を返せない、という作りにはしない（任意データ扱い）
  const [me, ed, sd] = await Promise.all([
    load("event_reaction.json").catch(() => null),
    load("earnings_drift.json").catch(() => null),
    load("supply_demand_events.json").catch(() => null),
  ]);

  // ---- 出来事27種
  const meAll = me && Array.isArray(me.events) ? me.events : [];
  const meHits = key ? meAll.filter((e) => nameHit(e, key, ["event_type"])) : meAll;
  // ★event を省略したときは常に軽い形★
  // 27件すべての詳細に決算・需給を足すと 61KB になり、1ツール 50KB を超える。
  // detail は「絞り込んだときにどこまで出すか」の指定として使う。
  const meCompact = !q;

  // ---- 決算（単一の検証なので、言葉で引き当てる）
  // ★event を省略したときは中身を返さない★
  // 3つの検証の中身をすべて足すと 26KB になる。省略時は見出しだけにして、
  // 「何が引けるか」が分かる状態にとどめる。
  const edHit = !!ed && !!q && EARNINGS_KEYS.some((k) => key.includes(k) || k.includes(key));

  // ---- 需給（イベント3種）
  const sdAll = sd && Array.isArray(sd.events) ? sd.events : [];
  const sdHits = key ? sdAll.filter((e) => nameHit(e, key, ["event_id"])) : [];

  const datasets = [];
  if (me) {
    datasets.push({
      id: "market_events",
      name: "出来事27種のあとの動き",
      answers: "地震・利上げ・円安・関税・パンデミックなどのあと、何が市場全体を上回って動いたか",
      source_book: me.source_book,
      count: meAll.length,
    });
  }
  if (ed) {
    datasets.push({
      id: "earnings_drift",
      name: "決算後のドリフト",
      answers: "決算発表のあと、値動きが続くか。好反応を翌日買って取れるか",
      source_book: ed.source_book,
      count: ed.by_quantile ? ed.by_quantile.length : null,
    });
  }
  if (sd) {
    datasets.push({
      id: "supply_demand",
      name: "指数の入替（需給イベント）",
      answers: "TOPIX の採用・除外で、いつ・どちら向きに動くか",
      source_book: sd.source_book,
      count: sdAll.length,
    });
  }

  const out = meta({
    what_this_answers:
      "ある出来事のあとに何が起きたかを、3つの検証から返す。返すのは分類ごとの統計で、銘柄ではない。",
    datasets,
    // ★3つが同じ形を示している。これがこのツールの答え★
    key_finding:
      "3つの検証は別々のデータから同じ形を示している。" +
      "出来事への反応は、その出来事が起きる前か、ごく初期に終わっていた。" +
      "出来事27種の「初動型」は前日引けから翌朝の寄り付きで終わり、" +
      "決算後のドリフトは+5日で統計的に消え、" +
      "指数の入替では実施日を通過した時点で動きが止まっている。" +
      "ニュースを見てから動くのでは間に合わないものが多い。",
    query: q || null,
    matched: q
      ? {
          market_events: meHits.length,
          earnings_drift: edHit ? 1 : 0,
          supply_demand: sdHits.length,
        }
      : null,
    matched_note: q ? null : "event を指定していないため、3つの検証の見出しだけを返している",
  });

  // ---- 出来事27種
  if (me) {
    const counts = {};
    const legend = {};
    for (const e of meAll) {
      counts[e.verdict] = (counts[e.verdict] || 0) + 1;
      if (e.verdict_note) legend[e.verdict] = e.verdict_note;
    }
    out.market_events = {
      source: me.source_book,
      universe: me.universe,
      method: me.method,
      key_finding: me.key_finding,
      total: meAll.length,
      verdict_counts: counts,
      verdict_legend: legend,
      matched: meHits.length,
      items: meHits.map((e) => {
        if (meCompact) {
          // 判定の意味は verdict_legend に1回だけ置く（27件ぶん繰り返さない）
          return {
            event_type: e.event_type,
            name: e.name,
            verdict: e.verdict,
            samples: e.samples,
          };
        }
        const o = {
          event_type: e.event_type,
          name: e.name,
          aliases: e.aliases,
          samples: e.samples,
          period: e.period,
          verdict: e.verdict,
          verdict_note: e.verdict_note,
          verdict_secondary: e.verdict_secondary,
          peak_day: e.peak_day,
          half_life_day: e.half_life_day,
          windows: e.windows.map((w) => {
            const x = {
              days: w.days,
              label: w.label,
              excess_pct: w.excess_pct,
              win_rate_pct: w.win_rate_pct,
              p_value: w.p_value,
              significant: w.significant,
            };
            if (detail) x.market_pct = w.market_pct;
            return x;
          }),
        };
        if (detail) o.samples_all = e.samples_all;
        return o;
      }),
    };
    if (meCompact) {
      out.market_events.items_note = detail
        ? "一覧のため判定のみ。全27件の詳細は 50KB を超えるため返せない。event に出来事の名前を渡すと窓別の超過収益・勝率・p値を返す(部分一致)"
        : "一覧のため判定のみ。窓別の超過収益・勝率・p値が要るときは event に出来事の名前を渡す(部分一致)";
    }
    if (q && meHits.length === 0) out.market_events.available_events = meAll.map((e) => e.name);
  }

  // ---- 決算後のドリフト
  if (ed) {
    const summary = {
      source: ed.source_book,
      universe: ed.universe,
      period: ed.period,
      data_source: ed.data_source,
      headline: ed.headline,
      // ★好反応を買っても取れない、が最も大事な点★
      key_finding: ed.key_finding,
    };
    if (!edHit) {
      // 引き当たっていないときも見出しだけは出す（あることを知らせる）
      out.earnings_drift = {
        ...summary,
        hint: "event に「決算」を渡すと、分位別のドリフトとロングショートの数字を返す",
      };
    } else {
      out.earnings_drift = {
        ...summary,
        method: ed.method,
        by_quantile: ed.by_quantile,
        long_short: ed.long_short,
        how_to_read: ed.how_to_read,
        not_included: ed.not_included,
      };
      if (detail) {
        out.earnings_drift.robustness = ed.robustness;
        out.earnings_drift.rule_backtest = ed.rule_backtest;
      } else {
        out.earnings_drift.detail_note =
          "区分別(発表時刻・四半期・規模・期間)の内訳とルール別の成績は detail=true で返す";
      }
    }
  }

  // ---- 指数の入替
  if (sd) {
    const summary = {
      source: sd.source_book,
      data_source: sd.data_source,
      headline: sd.headline,
      key_finding: sd.key_finding,
    };
    if (sdHits.length === 0) {
      out.supply_demand = {
        ...summary,
        events: sdAll.map((e) => ({ event_id: e.event_id, name: e.name, pressure: e.pressure })),
        hint: q
          ? "この言葉では引けなかった。event に「TOPIX」「日経225」などを渡すと測定値を返す"
          : "event に「TOPIX」「日経225」などを渡すと、区間ごとの平均CARとp値を返す",
      };
    } else {
      out.supply_demand = {
        ...summary,
        method: sd.method,
        matched: sdHits.length,
        events: sdHits.map((e) => ({
          event_id: e.event_id,
          name: e.name,
          aliases: e.aliases,
          pressure: e.pressure,
          what_happened: e.what_happened,
          measurements: e.measurements,
        })),
        how_to_read: sd.how_to_read,
        // ★自社株買いが無い理由もここに出る★ 黙って落とさない
        not_included: sd.not_included,
      };
    }
  }

  // ★元データ側の注意はトップにまとめる★
  // 絞り込んでも省略しても、3冊ぶんの免責が必ず目に入るようにする
  out.notes = {};
  if (me) out.notes.market_events = me.disclaimer;
  if (ed) out.notes.earnings_drift = ed.disclaimer;
  if (sd) out.notes.supply_demand = sd.disclaimer;

  out.how_to_read = [
    "超過収益はいずれも市場全体を引いたもの。分類や区分の平均で、個別銘柄の値動きではない",
    "「有意でない」は「動かない」ではなく「動きを統計的に確認できなかった」という意味",
    "3つの検証は測り方が違う(分類バスケット / 決算の分位 / イベント前後の累積)。数字を直接くらべない",
    "判定が「不発」「逆行」のもの、基準に届かなかったものも落とさずに返す",
  ];
  out.not_included =
    "銘柄名・銘柄コード・銘柄バスケットは含まない。" +
    "元データの明細(連想のバスケット・決算の銘柄別・自社株買いの企業別)は生成側でも開いていない。";
  if (q && meHits.length === 0 && !edHit && sdHits.length === 0) {
    out.hint =
      "どの検証にも当たらなかった。出来事の名前(地震 / 利上げ / 関税 …)、「決算」、" +
      "「TOPIX」「日経225」のいずれかで引ける。event を省略すると3つすべての要約を返す";
  }
  return out;
}

/**
 * テクニカル指標26種の検証結果。docs/indicator_verdict.json を読むだけ。
 * 生成側は mcp/tools/build_indicator_verdict.py（書籍21の検証出力＋原稿の判定表）。
 *
 * ★「使える」は数字から機械的に決まる★
 * 8区分すべてで超過収益が +0.3% を超えたもの＝ちょうど9個。
 * どの区分で届かなかったかを below_bar に入れているので、
 * 「なぜ使えるでないか」が数字で見える（酒田の checks_failed と同じ）。
 */
async function getIndicatorVerdict(args, { load }) {
  const q = typeof args.name === "string" ? args.name.trim() : "";
  const detail = !!args.detail;
  const d = await load("indicator_verdict.json");
  const all = Array.isArray(d.items) ? d.items : [];
  const key = q.toLowerCase();
  const hits = key
    ? all.filter((x) => nameHit(x, key, ["indicator", "category"]))
    : all;
  const compact = !q && !detail;

  const items = hits.map((x) => {
    if (compact) {
      return {
        indicator: x.indicator,
        category: x.category,
        verdict: x.verdict,
        verdict_ja: x.verdict_ja,
        win_rate_pct: x.exit_a.win_rate_pct,
        excess_expectancy_pct: x.exit_a.excess_expectancy_pct,
        pf: x.exit_b.pf,
        // 8区分のうち基準に届かなかった数。0 なら「使える」
        below_bar_count: x.below_bar_count,
      };
    }
    const o = {
      indicator: x.indicator,
      category: x.category,
      aliases: x.aliases,
      verdict: x.verdict,
      verdict_ja: x.verdict_ja,
      exit_a: x.exit_a,
      exit_b: x.exit_b,
      // ★なぜその判定なのかが数字で見える★
      below_bar: x.below_bar,
      below_bar_count: x.below_bar_count,
    };
    if (detail) o.regimes = x.regimes;
    return o;
  });

  const out = meta({
    source: d.source_book,
    universe: d.universe,
    period: d.period,
    method: d.method,
    criteria: d.criteria,
    benchmark: d.benchmark,
    headline: d.headline,
    summary: d.summary,
    key_finding: d.key_finding,
    not_included: d.not_included,
    query: q || null,
    matched: hits.length,
    items,
    how_to_read: [
      "超過収益は「同じ区分でランダムに売買した場合」との差。単体のリターンではない",
      "below_bar は8区分のうち +0.3% に届かなかった区分。「使える」はここが空になる",
      "判定は上の基準に対するもので、指標そのものの優劣を決めるものではない",
      "出口Aは20営業日で手仕舞い、出口Bは8%トレーリングストップ。同じ指標でも出口で成績が変わる",
    ],
    note: d.disclaimer,
  });
  if (compact) {
    out.items_note =
      "一覧のため主要な数字のみ。8区分の内訳や届かなかった区分が要るときは name に指標名を渡す(部分一致)か、detail=true を指定する";
  }
  if (q && hits.length === 0) {
    out.available_indicators = all.map((x) => x.indicator);
    out.hint = "name は部分一致(日英の別名可)。上の available_indicators から選び直すか、name を省略して全件を取得する";
  }
  return out;
}

/**
 * レバレッジ・インバースETFの減価。docs/etf_decay.json を読むだけ。
 * 生成側は mcp/tools/build_etf_decay.py（書籍35の検証出力）。
 *
 * ★「解釈で注意すべき点」を必ず一緒に返す★
 * 「日経が12年で約4倍になった上昇相場のデータ」という前提を落とすと、
 * この数字はまるごと誤読される。数字だけ返してはいけない。
 */
async function getEtfDecay(args, { load }) {
  const code = typeof args.code === "string" ? args.code.trim() : "";
  const detail = !!args.detail;
  const d = await load("etf_decay.json");
  const products = d.products || {};

  // code は「何について聞いているか」の確認。データは両方を含む形で持っている
  let product = null;
  if (code) {
    const key = code.toLowerCase();
    const found = Object.entries(products).find(
      ([c, p]) =>
        c === code ||
        String(p.name || "").toLowerCase().includes(key) ||
        (p.aliases || []).some((a) => String(a).toLowerCase().includes(key)),
    );
    if (!found) {
      return meta({
        source: d.source_book,
        query: code,
        matched: 0,
        available_products: Object.entries(products).map(([c, p]) => ({
          code: c,
          name: p.name,
        })),
        hint: "code は 1357(ダブルインバース) または 1570(レバレッジ)。省略すると全体を返す",
        note: d.disclaimer,
      });
    }
    product = { code: found[0], ...found[1] };
  }

  const out = meta({
    source: d.source_book,
    period: d.period,
    products,
    query: code || null,
    focus: product,
    mechanism: d.mechanism,
    headline: d.headline,
    // ★起動文が名指しした知見。回数の多さと損益の大きさが釣り合わない★
    key_finding: d.key_finding,
    yearly: d.yearly,
    by_holding_days: d.by_holding_days,
    wait_for_recovery: d.wait_for_recovery,
    // ★前提を落とさない★
    verification_caveats: d.verification_caveats,
    not_included: d.not_included,
    how_to_read: [
      "「理論値」は期間の-2倍。日々-2倍の商品に期間-2倍を期待するのが誤りだが、そう期待して買う人が多いので比較対象にしている",
      "減価(実績-理論)は相場の方向に関係なく起きる。上下を往復するだけで減る",
      "勝率と損益の大きさは別。「戻るまで待つ」は99%戻るが、戻らなかった回の損失がそれまでの利益を上回る",
      "条件別の件数が数十回のものは、1〜2回の暴落で数字が大きく動く",
    ],
    note: d.disclaimer,
  });
  if (detail) {
    out.by_condition = d.by_condition;
  } else {
    out.by_condition_note =
      "地合い条件別(8条件 × 保有日数4 × コスト2 = 72通り)の PF と最大ドローダウンは detail=true で返す";
  }
  return out;
}

async function listToolsGuide() {
  return meta({
    server: {
      name: SERVER_NAME,
      version: SERVER_VERSION,
      // ★区分を示す語を置かない★ 他に区分がある前提に読めるため。
      //    APIキーも登録も要らない、という事実だけを書く（2026-09-14 の方針）
      access: "APIキー・登録・利用料は不要",
      endpoint: CANONICAL_ENDPOINT,
      homepage: HOME_URL,
    },
    what_this_is:
      "kaburadar.jp / ruletrade.jp が公開している検証済みルールの実行結果(JSON)を、AIエージェントから読める形に変換するだけのサーバー。売買エンジンや発注機能は持たない。",
    tools: TOOL_DEFS.map((t) => ({ name: t.name, title: t.title, description: t.description })),
    data_schedule_jst: {
      get_daily_signals: "平日 19:30 頃更新。前営業日の終値で判定(1営業日遅れ)。返すのは1件のみ",
      get_market_regime:
        "平日 08:00 / 12:00 / 18:00 の本番スキャン後に更新。ザラ場中は15分ごとに現在値を反映。" +
        "3軸スコアは前営業日の終値で日次更新",
      get_anomaly_summary:
        "傾斜計: 平日 17:23 / 21:23。着火メーター: 平日 16:47 / 19:47 / 22:47。" +
        "ジンクス50本の検証結果は書籍刊行時点(2026-08)の固定データで、日次更新はしない",
      get_candlestick_verdict:
        "書籍刊行時点の固定データ(東証プライム1,550銘柄・2016-01〜2026-08で検証)。日次更新はしない",
      get_event_reaction:
        "書籍刊行時点の固定データ(出来事27種・決算後ドリフト・指数の入替の3つ)。日次更新はしない",
      get_indicator_verdict:
        "書籍刊行時点の固定データ(TOPIX500・2016-2025で検証)。日次更新はしない",
      get_etf_decay:
        "書籍刊行時点の固定データ(2014-07〜2026-09の実測)。日次更新はしない",
      note: "祝日・データ取得失敗時は前回値が残る(各レスポンスの updated / asof / stale を確認)",
    },
    limits: [
      "★銘柄を返すのは1件だけ★ 公開ルールに該当した全銘柄の一覧は配布しない(件数は返す)",
      "日次データは1営業日遅れ(前営業日の終値で判定)",
      "価格・株数・利確/損切ライン・本番システムが使う調整済みの閾値は含まない",
      "地合い判定・3軸スコアに銘柄名は含まない(市場全体の数字のみ)",
      "書籍の検証結果に銘柄名・銘柄コード・銘柄バスケットは含まない",
    ],
    how_to_read: [
      "status=rule_hit は「公開ルールの条件に機械的に該当した」という事実で、売買の判断ではない",
      "銘柄を返すのは1件だけ。該当した全銘柄の一覧は配布しない。件数は返すので「今日は何件あったか」には答えられる",
      "3軸スコアは「今どちらに傾いているか」の整理で、相場の予想ではない。各軸の reasons に根拠が入っている",
      "regime は本番システムが戦略を切り替えるための分類で、相場予想ではない",
      "着火メーターの割合は過去データでの発生率。将来の予測ではない",
      "ジンクスの judgment(◎○▲△×?)は過去データでの統計的有意性の分類で、将来そうなるという意味ではない。judgment_legend に定義がある",
      "判定が × や ? のものも含めて全50本を返す。「効かなかったこと」の検証結果も同じ重みで扱う",
      "酒田五法12本はいずれも著者の採用基準を満たしていない(採用ゼロ本)。not_adopted は検証の結果であって、その形が必ず外れるという意味ではない",
      "出来事の判定が「初動型」のものは、前日引けから翌朝の寄り付きで反応が終わる。ニュースを見てから動くのでは間に合わないものが多い",
      "出来事・決算・指数の入替の3つは、いずれも「通過する前に動き、通過したら終わる」同じ形を示している。ニュースを見てから動くのでは間に合わないものが多い",
      "決算後のドリフトで差が出たのは好反応側ではない。最も反応の良かった分位を翌日買っても+5日の超過収益は統計的にゼロと区別できなかった",
      "テクニカル指標の「使える9個」は、8区分すべてで超過収益が +0.3% を超えたもの。判定は基準に対するもので、指標そのものの優劣ではない",
      "レバレッジ・インバースETFの数字は、日経が12年で約4倍になった上昇相場のもの。verification_caveats を必ず一緒に読むこと",
    ],
    roadmap: {
      planned: "run_rule_backtest(ルールの過去検証をその場で実行する)。検証基盤の整備後に検討",
      status: "未提供",
    },
    legal: {
      disclaimer: DISCLAIMER,
      policy: "推奨・おすすめ等の表現は出力から除外する。免責は全レスポンスに常設する。",
      license: "個人利用向け。再配布・商用利用は要相談",
    },
    contact: HOME_URL,
  });
}

export const TOOL_HANDLERS = {
  get_daily_signals: getDailySignals,
  get_market_regime: getMarketRegime,
  get_anomaly_summary: getAnomalySummary,
  get_candlestick_verdict: getCandlestickVerdict,
  get_event_reaction: getEventReaction,
  get_indicator_verdict: getIndicatorVerdict,
  get_etf_decay: getEtfDecay,
  list_tools_guide: listToolsGuide,
};
