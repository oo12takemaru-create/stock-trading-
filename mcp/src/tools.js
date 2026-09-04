// Week 1 無料ツール4本。全部「既存の公開JSONを読んで整形する」だけ。
import { DISCLAIMER } from "./legal.js";

export const SERVER_NAME = "ruletrade-mcp";
export const SERVER_VERSION = "0.1.0";

const REGIME_LABEL = {
  BULLISH: "強気",
  NEUTRAL: "中立",
  BEARISH: "弱気",
  PANIC: "パニック",
};

const BNF_RULE =
  "終値の25日移動平均線からの乖離率が -15% 以下(書籍『BNFに学ぶ』掲載の基本ルール。終値ベースで未来情報は使わない)";

// ---- ツール定義(tools/list で返す) ----------------------------------------
export const TOOL_DEFS = [
  {
    name: "get_daily_signals",
    title: "日次ルール該当リスト(無料版・1営業日遅れ)",
    description:
      "公開ルール(BNF 25日線乖離)に該当した銘柄と監視候補を返す。無料版の制限: 1営業日遅れ・乖離率上位3件のみ・価格や株数などの売買情報は含まない。投資助言ではない。",
    inputSchema: {
      type: "object",
      properties: {
        strategy: {
          type: "string",
          enum: ["bnf"],
          default: "bnf",
          description: "ルール名。現在は bnf(25日線乖離の逆張りルール)のみ",
        },
        include_watch: {
          type: "boolean",
          default: true,
          description: "ルール未達だが乖離が大きい監視候補(judge=watch)も含めるか",
        },
      },
      additionalProperties: false,
    },
  },
  {
    name: "get_market_regime",
    title: "地合い判定(BULLISH/NEUTRAL/BEARISH/PANIC)",
    description:
      "本番システムが日中に更新している相場環境の判定、サーキットブレーカー(HALT)状態、VIX、日経平均、当日シグナル件数を返す。銘柄名は含まない。オプションで日次履歴も返す。",
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
    title: "暴落前兆の検証結果(傾斜計5項目+着火メーター7項目)",
    description:
      "書籍『暴落は、減衰する』の5つの前兆(逆イールド・CAPE・信用膨張・過熱・引き締め)の点灯状況と、過去26年のデータで検証した着火メーター(7フラグ・20営業日以内に-10%が起きた割合)を返す。detail=true で各項目の書籍上の根拠と統計表も含める。",
    inputSchema: {
      type: "object",
      properties: {
        detail: {
          type: "boolean",
          default: false,
          description: "各項目の書籍上の根拠(book)と着火メーターの統計表・履歴を含めるか",
        },
      },
      additionalProperties: false,
    },
  },
  {
    name: "list_tools_guide",
    title: "使い方ガイド・データ更新時刻・免責",
    description:
      "このサーバーの全ツールの説明、データの更新タイミングと遅延、無料版の制限、免責事項、今後の予定を返す。最初に一度呼ぶことを想定。",
    inputSchema: { type: "object", properties: {}, additionalProperties: false },
  },
];

// ---- 各ツールの実装 ------------------------------------------------------
function meta(extra) {
  return { disclaimer: DISCLAIMER, source_site: "https://kaburadar.jp", ...extra };
}

async function getDailySignals(args, { load }) {
  const includeWatch = args.include_watch !== false;
  const d = await load("free_scanner.json");
  const rows = Array.isArray(d.rows) ? d.rows : [];
  const items = rows
    .filter((r) => includeWatch || r.judge === "signal")
    .map((r) => ({
      code: String(r.code),
      name: r.name,
      sector: r.sector,
      ma25_deviation_pct: r.kairi,
      status: r.judge === "signal" ? "rule_hit" : "watch",
      status_label: r.judge === "signal" ? "ルール該当(-15%以下)" : "監視(ルール未達)",
    }));
  const hit = rows.filter((r) => r.judge === "signal").length;
  const sod = d.signal_of_day
    ? {
        code: String(d.signal_of_day.code),
        name: d.signal_of_day.name,
        strategy_label: d.signal_of_day.strategy_label,
        reason: d.signal_of_day.reason,
        note: "本番システム(3戦略統合)で当日初出の銘柄のうち最も早い1件。価格・株数は含まない",
      }
    : null;

  return meta({
    strategy: "bnf",
    rule: BNF_RULE,
    target_date: d.target_date,
    generated_at: d.generated_at,
    data_delay: "1営業日(前営業日の終値で判定)",
    limits: "乖離率上位3件のみ。利確/損切ライン・株数などの売買情報は含まない",
    market_condition: d.jiai,
    is_halt: !!d.is_halt,
    rule_hit_count: hit,
    watch_count: rows.length - hit,
    items,
    full_system_today: {
      date: d.signals_today_date ?? null,
      signal_count: d.signals_today_count ?? null,
      published_one: sod,
    },
  });
}

async function getMarketRegime(args, { load }) {
  const n = Math.max(0, Math.min(60, Number(args.history_days) || 0));
  const [radar, jiai] = await Promise.all([
    load("radar.json"),
    load("market_jiai.json").catch(() => null),
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
  const [g, c] = await Promise.all([load("gauge.json"), load("crash.json")]);

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
  // 別途ジンクス/アノマリー検証JSONがある場合は ANOMALY_URL で差し込める(構造はそのまま返す)
  if (env && env.ANOMALY_URL) {
    try {
      const r = await fetch(env.ANOMALY_URL, { headers: { "user-agent": "ruletrade-mcp/0.1" } });
      if (r.ok) out.jinx_verification = await r.json();
    } catch {
      /* 任意データなので失敗は無視 */
    }
  }
  return out;
}

async function listToolsGuide() {
  return meta({
    server: { name: SERVER_NAME, version: SERVER_VERSION, tier: "free" },
    what_this_is:
      "kaburadar.jp / ruletrade.jp が公開している検証済みルールの実行結果(JSON)を、AIエージェントから読める形に変換するだけのサーバー。売買エンジンや発注機能は持たない。",
    tools: TOOL_DEFS.map((t) => ({ name: t.name, title: t.title, description: t.description })),
    data_schedule_jst: {
      get_daily_signals: "平日 19:30 頃更新。前営業日の終値で判定(1営業日遅れ)",
      get_market_regime: "平日 08:00 / 12:00 / 18:00 の本番スキャン後に更新。ザラ場中は15分ごとに現在値を反映",
      get_anomaly_summary: "傾斜計: 平日 17:23 / 21:23。着火メーター: 平日 16:47 / 19:47 / 22:47",
      note: "祝日・データ取得失敗時は前回値が残る(各レスポンスの updated / asof / stale を確認)",
    },
    free_tier_limits: [
      "日次ルール該当は乖離率上位3件のみ・1営業日遅れ",
      "価格・株数・利確/損切ライン・本番の調整済み閾値は含まない",
      "地合い判定に銘柄名は含まない(件数のみ)",
    ],
    how_to_read: [
      "status=rule_hit は「公開ルールの条件に機械的に該当した」という事実で、売買の判断ではない",
      "regime は本番システムが戦略を切り替えるための分類で、相場予想ではない",
      "着火メーターの割合は過去データでの発生率。将来の予測ではない",
    ],
    roadmap: {
      planned: "run_rule_backtest(ルールの過去検証を実行)。バックテスト基盤の整備後に有料枠として追加予定",
      status: "未提供",
    },
    legal: {
      disclaimer: DISCLAIMER,
      policy: "推奨・おすすめ等の表現は出力から除外する。免責は全レスポンスに常設する。",
      license: "個人利用向け。再配布・商用利用は要相談",
    },
    contact: "https://kaburadar.jp",
  });
}

export const TOOL_HANDLERS = {
  get_daily_signals: getDailySignals,
  get_market_regime: getMarketRegime,
  get_anomaly_summary: getAnomalySummary,
  list_tools_guide: listToolsGuide,
};
