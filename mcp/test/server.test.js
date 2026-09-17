// 実際の docs/*.json をフィクスチャにして、ネットワーク無しで全ツールを通す。
import { test, before } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { handle } from "../src/server.js";
import { clearCache } from "../src/data.js";
import { NG_WORDS, scrubText } from "../src/legal.js";

const DOCS = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../docs");

// fetch をリポジトリ内ファイル読みに差し替える
before(() => {
  clearCache();
  globalThis.fetch = async (url) => {
    const name = new URL(url).pathname.split("/").pop();
    try {
      const txt = await readFile(path.join(DOCS, name), "utf8");
      return new Response(txt, { status: 200, headers: { "content-type": "application/json" } });
    } catch {
      return new Response("not found", { status: 404 });
    }
  };
});

const rpc = async (method, params, id = 1) => {
  const res = await handle(
    new Request("https://x.test/mcp", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ jsonrpc: "2.0", id, method, params }),
    }),
    {},
  );
  return { status: res.status, body: res.status === 202 ? null : await res.json() };
};

const call = async (name, args = {}) => {
  const { body } = await rpc("tools/call", { name, arguments: args });
  assert.equal(body.error, undefined, JSON.stringify(body.error));
  return body.result;
};

const hasNg = (s) => NG_WORDS.some((w) => s.toLowerCase().includes(w.toLowerCase()));

test("initialize が対応バージョンを返す", async () => {
  const { body } = await rpc("initialize", { protocolVersion: "2025-03-26", capabilities: {}, clientInfo: { name: "t", version: "1" } });
  assert.equal(body.result.protocolVersion, "2025-03-26");
  assert.ok(body.result.capabilities.tools);
  const r2 = await rpc("initialize", { protocolVersion: "1999-01-01" });
  assert.equal(r2.body.result.protocolVersion, "2025-06-18");
});

test("notifications は 202", async () => {
  const res = await handle(
    new Request("https://x.test/mcp", { method: "POST", body: JSON.stringify({ jsonrpc: "2.0", method: "notifications/initialized" }) }),
    {},
  );
  assert.equal(res.status, 202);
});

test("tools/list に8本", async () => {
  const { body } = await rpc("tools/list");
  assert.deepEqual(
    body.result.tools.map((t) => t.name),
    ["get_daily_signals", "get_market_regime", "get_anomaly_summary",
    "get_candlestick_verdict", "get_event_reaction", "get_indicator_verdict",
    "get_etf_decay", "list_tools_guide"],
  );
  for (const t of body.result.tools) assert.equal(t.inputSchema.type, "object");
});

test("get_daily_signals: free_scanner.json を変換し売買情報を含まない", async () => {
  const r = await call("get_daily_signals", { strategy: "bnf" });
  const p = r.structuredContent;
  assert.equal(r.isError, false);
  assert.equal(p.strategy, "bnf");
  assert.ok(p.disclaimer);
  assert.match(p.target_date, /^\d{4}-\d{2}-\d{2}$/);
  assert.ok(Array.isArray(p.items) && p.items.length <= 3);
  for (const it of p.items) {
    assert.ok(["rule_hit", "watch"].includes(it.status));
    assert.equal(typeof it.ma25_deviation_pct, "number");
    assert.equal(it.price, undefined);
    assert.equal(it.shares, undefined);
  }
  const onlyHit = await call("get_daily_signals", { include_watch: false });
  assert.ok(onlyHit.structuredContent.items.every((i) => i.status === "rule_hit"));
  assert.doesNotMatch(r.content[0].text, /[¥￥]\s*[\d,]+/); // 価格が漏れていない
});

test("get_market_regime: radar.json + 履歴", async () => {
  const p = (await call("get_market_regime", { history_days: 5 })).structuredContent;
  assert.ok(["BULLISH", "NEUTRAL", "BEARISH", "PANIC"].includes(p.regime));
  assert.equal(typeof p.is_halt, "boolean");
  assert.ok(p.disclaimer);
  assert.ok(Array.isArray(p.history) && p.history.length <= 5 && p.history.length > 0);
  assert.ok(p.history[0].date);
  const p0 = (await call("get_market_regime", {})).structuredContent;
  assert.equal(p0.history, undefined);
});

test("get_anomaly_summary: gauge.json + crash.json", async () => {
  const p = (await call("get_anomaly_summary", {})).structuredContent;
  assert.equal(p.precursor_gauges.items.length, 5);
  assert.equal(p.ignition_meter.flags.length, 7);
  assert.equal(typeof p.ignition_meter.historical_drop_rate_pct, "number");
  assert.equal(p.precursor_gauges.items[0].book, undefined);
  assert.equal(p.ignition_meter.stats, undefined);
  const d = (await call("get_anomaly_summary", { detail: true })).structuredContent;
  assert.ok(d.precursor_gauges.items[0].book);
  assert.ok(d.ignition_meter.stats.all);
  assert.ok(d.ignition_meter.history.length > 0);
});

test("get_anomaly_summary: ジンクス50本(anomaly_results.json)", async () => {
  const p = (await call("get_anomaly_summary", {})).structuredContent;
  const j = p.jinx_verification;
  assert.equal(j.total, 50, "50本読めていない");
  assert.equal(j.matched, 50);
  assert.equal(j.items.length, 50);
  assert.ok(j.judgment_legend["○"], "判定記号の凡例がない");
  assert.equal(Object.values(j.judgment_counts).reduce((a, b) => a + b, 0), 50);

  // 引数なし = 一覧モード(判定のみ・軽い)
  for (const it of j.items) {
    assert.ok(it.name && it.category, `欠けた項目: ${it.id}`);
    assert.ok(it.judgment in j.judgment_legend, `未知の判定記号: ${it.judgment}`);
    assert.equal(typeof it.verified, "boolean");
    assert.equal(it.metrics, undefined, "一覧モードで統計値が出ている");
    assert.equal(it.saying, undefined);
  }
  assert.ok(j.note.includes("name"), "一覧モードの案内がない");

  // 絞り込むと統計値が付く
  const one = (await call("get_anomaly_summary", { name: "セルインメイ" })).structuredContent.jinx_verification.items[0];
  assert.ok(Array.isArray(one.metrics) && one.metrics.length === 2);
  assert.equal(one.verified, true);
  assert.ok(one.definition);
  for (const m of one.metrics) {
    assert.ok(["full", "recent10"].includes(m.period));
    assert.equal(typeof m.n, "number");
    assert.ok(m.win_rate === null || typeof m.win_rate === "number");
  }

  // p値は有効数字で丸める。固定小数だと 1e-8 が 0 になり「p=0」を読ませてしまう
  const all = (await call("get_anomaly_summary", { detail: true })).structuredContent.jinx_verification.items;
  for (const it of all) {
    for (const m of it.metrics) {
      // p=0 を許すのは、元データ側でアンダーフローしていて注記を付けた場合だけ
      if (m.p === 0) assert.ok(m.p_note, `${it.name}(${m.period}) の p が注記なしで 0`);
      else assert.ok(m.p === null || (m.p > 0 && m.p <= 1), `${it.name}(${m.period}) の p が範囲外: ${m.p}`);
    }
  }
  // 生データの素通し禁止(企画書 §9)。格言本文・図のパス・年代別の内訳は出さない
  assert.equal(one.saying, undefined);
  assert.equal(one.chart, undefined);
  assert.equal(one.extra, undefined);
  assert.equal(one.metrics[0].std, undefined);

  // detail=true で年代別・対照群が付く
  const d = (await call("get_anomaly_summary", { detail: true })).structuredContent.jinx_verification;
  assert.ok(d.items.some((x) => x.extra && Object.keys(x.extra).length > 0));
  assert.ok(d.items.every((x) => Array.isArray(x.metrics)));
  assert.equal(typeof d.items[0].metrics[0].control_mean, "number");
  // 検証できなかった4本も落とさずに返す
  assert.equal(d.items.filter((x) => !x.verified).length, 4);
});

test("get_anomaly_summary: name で絞り込める", async () => {
  const one = (await call("get_anomaly_summary", { name: "セルインメイ" })).structuredContent.jinx_verification;
  assert.equal(one.matched, 1);
  assert.equal(one.items[0].name, "セルインメイ");
  assert.equal(one.total, 50);

  const cat = (await call("get_anomaly_summary", { name: "曜日" })).structuredContent.jinx_verification;
  assert.ok(cat.matched > 1, "カテゴリ部分一致が効いていない");

  const none = (await call("get_anomaly_summary", { name: "存在しないジンクス" })).structuredContent.jinx_verification;
  assert.equal(none.matched, 0);
  assert.equal(none.available_names.length, 50);
});

test("ジンクス検証データに推奨語が残らない(saying は出力しない)", async () => {
  // 元データの saying(書籍の格言本文)には「儲かる」「売れ」等が含まれる。
  // 出力に含めていないこと + 万一含めても scrub が効くことの両方を確認する。
  const raw = JSON.parse(await readFile(path.join(DOCS, "anomaly_results.json"), "utf8"));
  assert.equal(raw.length, 50);
  assert.ok(raw.some((x) => hasNg(String(x.saying || ""))), "テスト前提の変化: 元データにNG語が無い");

  for (const args of [{}, { detail: true }, { name: "ハロウィン" }]) {
    const r = await call("get_anomaly_summary", args);
    assert.equal(hasNg(r.content[0].text), false, `推奨語が残っている: ${JSON.stringify(args)}`);
    assert.ok(r.structuredContent.disclaimer.length > 20);
  }
  assert.equal(hasNg(scrubText(raw.find((x) => hasNg(String(x.saying || ""))).saying)), false);
});

// ---- v0.2: 書籍の検証結果を返す2本 -----------------------------------------
// このツールの値打ちは「効いたものを並べる」ことではなく
// 「効かなかったことも同じ重みで返す」ことにある。そこを test で固定する。

test("get_candlestick_verdict: 12本すべて不採用(採用ゼロ本)", async () => {
  const p = (await call("get_candlestick_verdict", {})).structuredContent;
  assert.equal(p.matched, 12, "12本読めていない");
  assert.equal(p.items.length, 12);
  assert.equal(p.summary.adopted, 0, "採用ゼロ本のはず");
  assert.equal(p.summary.not_adopted, 12);
  for (const it of p.items) {
    assert.equal(it.verdict, "not_adopted", `${it.name} の verdict が ${it.verdict}`);
    assert.ok(Array.isArray(it.checks_failed) && it.checks_failed.length > 0,
      `${it.name}: どの基準で落ちたかが無い`);
  }
  // 一覧モードは軽い形(統計の全部は出さない)
  assert.equal(p.items[0].primary, undefined, "一覧モードで統計値が出ている");
  assert.ok(p.items_note.includes("pattern"), "一覧モードの案内がない");
  assert.ok(p.headline.includes("1本も無かった"), "結論の1行が無い");
  assert.ok(p.criteria.min_pf, "採用基準が無い");
});

test("get_candlestick_verdict: 高PFでも不採用の理由を要約せずに返す", async () => {
  // ★このツールで最も価値のある答え★
  // PF 3.04 は12本で最高。それでも落ちている理由が全文で出ること。
  const p = (await call("get_candlestick_verdict", { pattern: "三空叩き込み" })).structuredContent;
  assert.equal(p.matched, 1);
  const it = p.items[0];
  assert.equal(it.primary.pf, 3.036, "PF が元データと違う");
  assert.equal(it.verdict, "not_adopted");
  for (const w of ["発見期", "確認期", "符号が反転", "全期間の平均だけが良く見える"]) {
    assert.ok(it.rejection_reason.includes(w), `不採用理由から「${w}」が落ちている`);
  }
  // key_finding にも同じ趣旨が入っている(一覧だけ見る相手にも届くように)
  assert.ok(p.key_finding.includes("3.04"), "key_finding に高PFの例が無い");
  // 絞り込むと統計が付く
  assert.equal(typeof it.primary.win_rate_pct, "number");
  assert.equal(typeof it.primary.p_value, "number");
  assert.equal(it.by_horizon, undefined, "detail なしで窓別が出ている");

  const d = (await call("get_candlestick_verdict", { pattern: "三空叩き込み", detail: true }))
    .structuredContent.items[0];
  assert.equal(d.by_horizon.length, 3, "5/10/20営業日の3窓が無い");
  assert.ok(d.regime.above_75ma, "相場環境別が無い");
  // 別基盤の参考値は★注記ごと★返す。数字だけ渡すと採用判定に見える
  assert.ok(d.size_reference.note.includes("採用判定には使わない"), "参考値の注記が落ちている");
});

test("get_candlestick_verdict: 別枠は逆三尊のみ・社内向けの語を出さない", async () => {
  const p = (await call("get_candlestick_verdict", { detail: true })).structuredContent;
  const filters = p.items.filter((x) => x.filter_candidate);
  assert.equal(filters.length, 1);
  assert.equal(filters[0].name, "逆三尊");
  assert.equal(filters[0].verdict, "not_adopted", "別枠でも採用ではない");
  // 元データの理由には「回避フィルターの候補」「買わない」という検証者の設計メモが
  // 入っている。そのまま出すと行動の指示に読めるので、事実までに留める
  const text = (await call("get_candlestick_verdict", { pattern: "逆三尊" })).content[0].text;
  for (const w of ["回避フィルター", "買わない", "ビルダー側"]) {
    assert.equal(text.includes(w), false, `社内向けの語が出ている: ${w}`);
  }
  assert.ok(filters[0].filter_note, "別枠である旨の説明が無い");
});

test("get_candlestick_verdict: 日英の別名で引ける・外したときは候補を返す", async () => {
  for (const q of ["三尊", "head and shoulders", "赤三兵", "three white soldiers", "sanku"]) {
    const r = (await call("get_candlestick_verdict", { pattern: q })).structuredContent;
    assert.ok(r.matched > 0, `別名で引けない: ${q}`);
  }
  const none = (await call("get_candlestick_verdict", { pattern: "存在しない形" })).structuredContent;
  assert.equal(none.matched, 0);
  assert.equal(none.available_patterns.length, 12);
});

test("get_event_reaction: 3つの検証をまとめて引ける", async () => {
  // 引数なし＝何が引けるかの見出しだけ。中身まで返すと 26KB になる
  const p = (await call("get_event_reaction", {})).structuredContent;
  assert.equal(p.datasets.length, 3, "3つの検証が揃っていない");
  assert.deepEqual(p.datasets.map((d) => d.id),
    ["market_events", "earnings_drift", "supply_demand"]);
  assert.equal(p.matched, null, "絞り込んでいないのに matched が出ている");
  assert.ok(p.matched_note.includes("見出し"));
  // ★3つが同じ形を示している、がこのツールの答え★
  for (const w of ["初動型", "+5日", "実施日"]) {
    assert.ok(p.key_finding.includes(w), `key_finding に「${w}」が無い`);
  }
  // 中身は返さない（見出しだけ）
  assert.equal(p.earnings_drift.by_quantile, undefined, "省略時に決算の中身が出ている");
  assert.equal(p.supply_demand.events[0].measurements, undefined, "省略時に需給の中身が出ている");
  assert.ok(p.earnings_drift.headline && p.earnings_drift.key_finding);
  assert.ok(p.supply_demand.headline && p.supply_demand.key_finding);
});

test("get_event_reaction: 出来事27種(書籍36)", async () => {
  const p = (await call("get_event_reaction", {})).structuredContent.market_events;
  assert.equal(p.total, 27, "27種読めていない");
  assert.equal(p.items.length, 27);
  assert.ok(p.key_finding.includes("初動型"), "初動型の注意が無い");
  // 判定の意味は27件ぶん繰り返さず、legend に1回
  assert.equal(p.items[0].verdict_note, undefined);
  assert.ok(p.verdict_legend["初動型"], "判定の凡例が無い");
  assert.equal(Object.keys(p.verdict_legend).length, Object.keys(p.verdict_counts).length,
    "凡例に説明の無い判定がある");
  assert.equal(p.items[0].windows, undefined, "一覧モードで窓が出ている");

  const one = (await call("get_event_reaction", { event: "地震" })).structuredContent;
  assert.equal(one.matched.market_events, 1);
  const it = one.market_events.items[0];
  assert.equal(it.windows.length, 5, "当日/翌日/5日/1か月/3か月の5窓が無い");
  assert.equal(it.windows[0].market_pct, undefined, "detail なしで市場全体の値が出ている");
  const d = (await call("get_event_reaction", { event: "地震", detail: true }))
    .structuredContent.market_events.items[0];
  assert.equal(typeof d.windows[0].market_pct, "number");
  assert.equal(typeof d.samples_all, "number");
  // 英語の別名
  assert.ok((await call("get_event_reaction", { event: "earthquake" }))
    .structuredContent.matched.market_events > 0);
});

test("get_event_reaction: 決算後ドリフト(書籍31)は好反応を買っても取れない", async () => {
  const p = (await call("get_event_reaction", { event: "決算" })).structuredContent;
  assert.equal(p.matched.earnings_drift, 1, "決算で引けていない");
  const e = p.earnings_drift;
  assert.equal(e.by_quantile.length, 5);
  assert.equal(e.long_short.length, 5);

  // ★ここが消えると「好決算を買えば取れる」と読まれる★
  const q5 = e.by_quantile.find((q) => q.quantile === 5);
  const w5 = q5.windows.find((w) => w.days === 5);
  assert.equal(w5.significant, false, "分位5の+5日が有意になっている");
  assert.ok(Math.abs(w5.car_pct) < 0.2, `分位5の+5日が ${w5.car_pct}%`);
  assert.ok(e.key_finding.includes("ゼロと区別できない"), "好反応側の注意が無い");

  // +5日は有意、+20日は消える
  const ls5 = e.long_short.find((x) => x.window.startsWith("+1〜+5"));
  const ls20 = e.long_short.find((x) => x.window.startsWith("+1〜+20"));
  assert.equal(ls5.significant, true);
  assert.equal(ls20.significant, false, "+20日まで有意なら「5日で消える」が言えない");

  // 区分別とルール別は detail
  assert.equal(e.robustness, undefined, "detail なしで区分別が出ている");
  const d = (await call("get_event_reaction", { event: "決算", detail: true }))
    .structuredContent.earnings_drift;
  assert.equal(d.robustness.length, 12);
  assert.equal(d.rule_backtest.length, 9);
  // 英語でも引ける
  assert.equal((await call("get_event_reaction", { event: "PEAD" }))
    .structuredContent.matched.earnings_drift, 1);
});

test("get_event_reaction: 指数の入替(書籍29)は通過前に動く", async () => {
  const p = (await call("get_event_reaction", { event: "TOPIX" })).structuredContent;
  assert.ok(p.matched.supply_demand >= 2, "TOPIX で除外と採用の両方が引けていない");
  const sd = p.supply_demand;
  const ex = sd.events.find((e) => e.event_id === "topix_exclusion");
  const add = sd.events.find((e) => e.event_id === "topix_addition");
  assert.ok(ex.measurements.length > 0 && add.measurements.length > 0);

  // 実施日の直前は動き、通過後は動かない
  const before = ex.measurements.find((m) => m.window.includes("(-3,-1)") && m.target.includes("低減10回"));
  const after = ex.measurements.find((m) => m.window.includes("(0,+20)"));
  assert.ok(before.mean_car_pct < 0 && before.significant, "実施前の下げが出ていない");
  assert.equal(after.significant, false, "実施後が有意になっている");

  // ★自社株買いが無いことを黙って落とさない★
  assert.ok(sd.not_included.includes("自社株買い"), "自社株買いが無い理由が書かれていない");

  // 日経225 でも引ける
  assert.ok((await call("get_event_reaction", { event: "日経225" }))
    .structuredContent.matched.supply_demand >= 1);
  // どれにも当たらないときは案内を返す
  const none = (await call("get_event_reaction", { event: "存在しない出来事" })).structuredContent;
  assert.deepEqual(none.matched, { market_events: 0, earnings_drift: 0, supply_demand: 0 });
  assert.ok(none.hint.includes("決算"), "引き方の案内が無い");
});

test("★法務の線★ 書籍2本の出力に銘柄コード・銘柄名が1つも無い", async () => {
  // 連想の元データには銘柄バスケット(499行・code と name)が併存している。
  // 取り違えると「出来事 → 買う銘柄」を返す道具になる。生成側で読み込んでいないが、
  // 万一混ざったらここで落とす。
  for (const [name, args] of [
    ["get_candlestick_verdict", { detail: true }],
    ["get_event_reaction", { detail: true }],
    ["get_candlestick_verdict", { pattern: "三空叩き込み", detail: true }],
    ["get_event_reaction", { event: "地震", detail: true }],
  ]) {
    const r = await call(name, args);
    // ★数値の中は見ない★ p値 0.0135 の小数部が銘柄コードに見えるため、
    // 文字列の値とキーだけを集めて調べる
    const texts = [];
    const walk = (v) => {
      if (typeof v === "string") texts.push(v);
      else if (Array.isArray(v)) v.forEach(walk);
      else if (v && typeof v === "object") {
        for (const [k, x] of Object.entries(v)) { texts.push(k); walk(x); }
      }
    };
    walk(r.structuredContent);
    const codes = (texts.join(" ").match(/(?<![\d.])\d{4}(?![\d.])/g) || [])
      .filter((c) => !(Number(c) >= 1990 && Number(c) <= 2100)); // 年号は除く
    assert.deepEqual(codes, [], `${name}: 銘柄コードらしき4桁がある`);
    assert.equal(hasNg(r.content[0].text), false, `${name} に推奨語がある`);
    assert.ok(r.structuredContent.disclaimer.length > 20, `${name} に免責が無い`);
    // 元データ側の注意。get_event_reaction は3冊ぶんを notes にまとめている
    const sc = r.structuredContent;
    assert.ok(sc.note || Object.keys(sc.notes || {}).length >= 3,
      `${name} に元データ側の注意が無い`);
  }
});

test("v0.2: 応答サイズが 1ツール 50KB を超えない", async () => {
  // 上限を超えると一部のクライアントで切り詰められ、免責ごと落ちる。
  // 一覧は軽い形、詳細は1件ずつ、という設計が効いているかを数字で見る。
  for (const [name, args] of [
    ["get_candlestick_verdict", {}],
    ["get_candlestick_verdict", { detail: true }],
    ["get_event_reaction", {}],
    ["get_event_reaction", { detail: true }],
  ]) {
    const r = await call(name, args);
    const bytes = Buffer.byteLength(r.content[0].text, "utf8");
    assert.ok(bytes <= 51200, `${name}(${JSON.stringify(args)}) が ${bytes} バイト`);
  }
});

// ---- v0.2 Step2: 指標26種 + レバレッジ・インバースETF ------------------------

test("get_indicator_verdict: 26種・使える9 / 条件付き4 / 捨てろ13", async () => {
  const p = (await call("get_indicator_verdict", {})).structuredContent;
  assert.equal(p.matched, 26, "26種読めていない");
  assert.equal(p.summary.usable, 9);
  assert.equal(p.summary.conditional, 4);
  assert.equal(p.summary.not_usable, 13);
  // ★「使える」は8区分すべてを通ったもの＝ below_bar が空★
  for (const it of p.items) {
    if (it.verdict === "usable") {
      assert.equal(it.below_bar_count, 0, `${it.indicator}: 使えるなのに未達区分がある`);
    } else {
      assert.ok(it.below_bar_count > 0, `${it.indicator}: 未達が無いのに使えるでない`);
    }
  }
  assert.ok(p.criteria.usable.includes("0.3"), "判定の基準が書かれていない");
  assert.ok(p.benchmark.exit_b_pf, "ランダムとの比較基準が無い");
  // 一覧モードは軽い形
  assert.equal(p.items[0].regimes, undefined, "一覧モードで8区分が出ている");
  assert.equal(p.items[0].exit_a, undefined);
  assert.ok(p.items_note.includes("name"), "一覧モードの案内が無い");
});

test("get_indicator_verdict: 絞り込みと8区分・書籍の一言は出さない", async () => {
  const one = (await call("get_indicator_verdict", { name: "RSI" })).structuredContent;
  assert.ok(one.matched >= 1);
  const it = one.items.find((x) => x.indicator === "RSI");
  assert.equal(it.verdict, "usable");
  assert.equal(it.below_bar.length, 0);
  assert.equal(typeof it.exit_a.win_rate_pct, "number");
  assert.equal(typeof it.exit_b.pf, "number");
  assert.equal(it.regimes, undefined, "detail なしで8区分が出ている");

  const d = (await call("get_indicator_verdict", { name: "RSI", detail: true }))
    .structuredContent.items.find((x) => x.indicator === "RSI");
  assert.equal(d.regimes.length, 8, "8区分そろっていない");
  assert.ok(d.regimes.every((r) => typeof r.excess_pct === "number"));

  // 条件付きは「どこで落ちたか」が出る
  const w = (await call("get_indicator_verdict", { name: "ウィリアムズ" }))
    .structuredContent.items[0];
  assert.equal(w.verdict, "conditional");
  assert.ok(w.below_bar.length > 0, "条件付きなのに未達区分が出ていない");

  // ★書籍本文の「一言」を出さない★
  // 『新規学習は非推奨』のように推奨語を含み、統計でもない
  const all = (await call("get_indicator_verdict", { detail: true })).content[0].text;
  for (const phrase of ["新規学習", "死にかけ", "墓に眠る", "せっかちな弟"]) {
    assert.equal(all.includes(phrase), false, `書籍の一言が出ている: ${phrase}`);
  }

  // 英語の別名でも引ける
  for (const q of ["ichimoku", "bollinger", "momentum"]) {
    assert.ok((await call("get_indicator_verdict", { name: q })).structuredContent.matched > 0,
      `別名で引けない: ${q}`);
  }
  const none = (await call("get_indicator_verdict", { name: "存在しない指標" })).structuredContent;
  assert.equal(none.matched, 0);
  assert.equal(none.available_indicators.length, 26);
});

test("get_etf_decay: 12年の実測・戻り待ち・注意書きを必ず返す", async () => {
  const p = (await call("get_etf_decay", {})).structuredContent;
  assert.equal(p.yearly.length, 13, "年別13年ぶんが無い");
  assert.equal(p.by_holding_days.length, 6);
  assert.equal(p.wait_for_recovery.results.length, 2);
  assert.ok(p.mechanism.why_it_decays.includes("94.5"), "減価の仕組みの数字が無い");

  // ★起動文が名指しした知見★ 回数の多さと損益の大きさが釣り合わない
  assert.ok(p.key_finding.includes("99.3"), "戻り待ちの成功率が無い");
  assert.ok(p.key_finding.includes("32.5"), "戻らなかった場合の数字が無い");

  // ★注意書きを落とさない★
  // 「日経が12年で約4倍になった上昇相場のデータ」という前提が抜けると、
  // この数字はまるごと誤読される
  assert.ok(p.verification_caveats, "解釈の注意が無い");
  assert.ok(p.verification_caveats.note.includes("上昇相場"), "最重要の前提が無い");
  assert.ok(Array.isArray(p.verification_caveats.wait_for_recovery));
  // 著者の執筆メモは混ぜない（酒田の逆三尊と同じ線）
  const text = (await call("get_etf_decay", { detail: true })).content[0].text;
  assert.equal(text.includes("章立てへの示唆"), false, "著者の執筆メモが出ている");

  // 条件別は detail のときだけ
  assert.equal(p.by_condition, undefined, "detail なしで72通りが出ている");
  assert.ok(p.by_condition_note.includes("detail"));
  const d = (await call("get_etf_decay", { detail: true })).structuredContent;
  assert.equal(d.by_condition.length, 72, "条件別72通りが無い");
  assert.ok(d.by_condition.every((x) => typeof x.pf === "number"));

  // code で対象を指定できる / 未知のコードは候補を返す
  const one = (await call("get_etf_decay", { code: "1357" })).structuredContent;
  assert.equal(one.focus.code, "1357");
  const ja = (await call("get_etf_decay", { code: "ダブルインバース" })).structuredContent;
  assert.equal(ja.focus.code, "1357");
  const none = (await call("get_etf_decay", { code: "9999" })).structuredContent;
  assert.equal(none.matched, 0);
  assert.ok(none.available_products.length >= 2);
});

test("★法務の線★ Step2 の2本に個別株の銘柄情報が無い", async () => {
  // ETF のコード(1357/1570/1360)は「何を検証したか」の識別子なので許す。
  // それ以外の4桁が出たら個別株の混入。
  const ALLOWED = new Set(["1357", "1570", "1360"]);
  for (const [name, args] of [
    ["get_indicator_verdict", { detail: true }],
    ["get_indicator_verdict", { name: "RSI", detail: true }],
    ["get_etf_decay", { detail: true }],
    ["get_etf_decay", { code: "1357", detail: true }],
  ]) {
    const r = await call(name, args);
    // ★数値の中は見ない★（期待値 0.0045 の小数部が4桁コードに見える）
    const texts = [];
    const walk = (v) => {
      if (typeof v === "string") texts.push(v);
      else if (Array.isArray(v)) v.forEach(walk);
      else if (v && typeof v === "object") {
        for (const [k, x] of Object.entries(v)) { texts.push(k); walk(x); }
      }
    };
    walk(r.structuredContent);
    const codes = (texts.join(" ").match(/(?<![\d.])\d{4}(?![\d.])/g) || [])
      .filter((c) => !(Number(c) >= 1990 && Number(c) <= 2100))
      .filter((c) => !ALLOWED.has(c));
    assert.deepEqual(codes, [], `${name}: 個別株らしき4桁がある`);
    assert.equal(hasNg(r.content[0].text), false, `${name} に推奨語がある`);
    assert.ok(r.structuredContent.disclaimer.length > 20, `${name} に免責が無い`);
    assert.ok(r.structuredContent.note, `${name} に元データ側の注意が無い`);
  }
});

test("Step2: 応答サイズが 1ツール 50KB を超えない", async () => {
  for (const [name, args] of [
    ["get_indicator_verdict", {}],
    ["get_indicator_verdict", { detail: true }],
    ["get_etf_decay", {}],
    ["get_etf_decay", { detail: true }],
  ]) {
    const r = await call(name, args);
    const bytes = Buffer.byteLength(r.content[0].text, "utf8");
    assert.ok(bytes <= 51200, `${name}(${JSON.stringify(args)}) が ${bytes} バイト`);
  }
});

test("list_tools_guide", async () => {
  const p = (await call("list_tools_guide")).structuredContent;
  assert.equal(p.tools.length, 8);
  assert.ok(p.legal.disclaimer);
  assert.ok(p.data_schedule_jst.get_daily_signals);
});

test("法務線: 全ツール出力に推奨語が含まれず、免責キーが常設", async () => {
  for (const [name, args] of [
    ["get_daily_signals", {}],
    ["get_market_regime", { history_days: 10 }],
    ["get_anomaly_summary", { detail: true }],
    ["get_candlestick_verdict", { detail: true }],
    ["get_event_reaction", { detail: true }],
    ["get_indicator_verdict", { detail: true }],
    ["get_etf_decay", { detail: true }],
    ["list_tools_guide", {}],
  ]) {
    const r = await call(name, args);
    assert.equal(r.structuredContent.disclaimer.length > 20, true, name);
    assert.equal(hasNg(r.content[0].text), false, `${name} に推奨語が残っている`);
  }
});

test("scrub は外部データ由来の推奨語も伏せる", () => {
  const hit = { count: 0 };
  const s = scrubText("この銘柄は買い推奨、Strong Buy です", hit);
  assert.equal(hasNg(s), false);
  assert.ok(hit.count >= 1);
});

test("不明メソッド / 不明ツール / パースエラー", async () => {
  assert.equal((await rpc("nope")).body.error.code, -32601);
  const { body } = await rpc("tools/call", { name: "nope", arguments: {} });
  assert.equal(body.error.code, -32602);
  const res = await handle(new Request("https://x.test/mcp", { method: "POST", body: "{" }), {});
  assert.equal(res.status, 400);
});

test("データ取得失敗は isError=true で返す(サーバーは落ちない)", async () => {
  const saved = globalThis.fetch;
  clearCache();
  globalThis.fetch = async () => new Response("x", { status: 500 });
  const { body } = await rpc("tools/call", { name: "get_market_regime", arguments: {} });
  assert.equal(body.result.isError, true);
  assert.ok(body.result.structuredContent.disclaimer);
  globalThis.fetch = saved;
  clearCache();
});

test("GET / と /health と /mcp(405)", async () => {
  const root = await handle(new Request("https://x.test/"), {});
  assert.equal(root.status, 200);
  // 案内する URL は常に正規URL。Vercel の rewrite 経由だと Worker から見た
  // オリジンは workers.dev のままなので、リクエストのホストを混ぜてはいけない
  const rootBody = await root.json();
  assert.equal(rootBody.endpoint, "https://ruletrade.jp/mcp");
  assert.equal(rootBody.health, "https://ruletrade.jp/mcp/health");
  assert.equal(rootBody.site, "https://ruletrade.jp/");
  const h = await handle(new Request("https://x.test/health"), {});
  assert.equal(h.status, 200);
  assert.equal((await h.json()).ok, true);
  const m = await handle(new Request("https://x.test/mcp"), {});
  assert.equal(m.status, 405);
});

test("案内する URL が正規URL(ruletrade.jp)に統一されている", async () => {
  // initialize の serverInfo
  const { body } = await rpc("initialize", { protocolVersion: "2025-06-18", capabilities: {}, clientInfo: { name: "t", version: "1" } });
  assert.equal(body.result.serverInfo.websiteUrl, "https://ruletrade.jp/");

  // list_tools_guide
  const g = (await call("list_tools_guide")).structuredContent;
  assert.equal(g.server.endpoint, "https://ruletrade.jp/mcp");
  assert.equal(g.server.homepage, "https://ruletrade.jp/");
  assert.equal(g.contact, "https://ruletrade.jp/");

  // 全ツールに source_site / data_site が付く
  for (const name of ["get_daily_signals", "get_market_regime", "get_anomaly_summary",
    "get_candlestick_verdict", "get_event_reaction", "get_indicator_verdict",
    "get_etf_decay", "list_tools_guide"]) {
    const p = (await call(name)).structuredContent;
    assert.equal(p.source_site, "https://ruletrade.jp/", name);
    assert.equal(p.data_site, "https://kaburadar.jp", name);
  }

  // workers.dev を案内していない(配信先が変わっても利用者側が壊れないようにするため)
  for (const name of ["list_tools_guide", "get_daily_signals"]) {
    const t = (await call(name)).content[0].text;
    assert.equal(t.includes("workers.dev"), false, `${name} が workers.dev を案内している`);
  }
});

test("llms.txt と openapi.json(エージェント向けの発見用)", async () => {
  const t = await handle(new Request("https://x.test/llms.txt"), {});
  assert.equal(t.status, 200);
  assert.match(t.headers.get("content-type"), /^text\/plain/);
  const txt = await t.text();
  assert.ok(txt.includes("https://ruletrade.jp/mcp"), "正規URLが書かれていない");
  assert.equal(txt.includes("workers.dev"), false, "workers.dev を案内している");
  for (const name of ["get_daily_signals", "get_market_regime", "get_anomaly_summary",
    "get_candlestick_verdict", "get_event_reaction", "get_indicator_verdict",
    "get_etf_decay", "list_tools_guide"]) {
    assert.ok(txt.includes(name), `${name} が載っていない`);
  }
  assert.ok(/not investment advice/i.test(txt), "英語の免責がない");
  // ツール一覧は英語で書く(英語圏のエージェントに読ませるためのファイルなので、
  // TOOL_DEFS の日本語 title を流用しない)
  const toolBlock = txt.split("## What you can get")[1].split("##")[0];
  assert.equal(/[ぁ-んァ-ン一-龥]/.test(toolBlock), false, "ツール一覧に日本語が混ざっている");
  for (const name of ["get_daily_signals", "get_market_regime", "get_anomaly_summary",
    "get_candlestick_verdict", "get_event_reaction", "get_indicator_verdict",
    "get_etf_decay", "list_tools_guide"]) {
    const line = toolBlock.split("\n").find((l) => l.startsWith("- " + `\`${name}\`` + " — "));
    assert.ok(line, `${name} の行がない`);
    const desc = line.split(" — ")[1] || "";
    assert.ok(desc.trim().length > 30, `${name} の英語説明が短すぎる: ${desc}`);
    assert.equal(/[ぁ-んァ-ン一-龥]/.test(desc), false, `${name} の説明に日本語`);
  }
  assert.equal(hasNg(txt), false, "llms.txt に推奨語がある");

  const o = await handle(new Request("https://x.test/openapi.json"), {});
  assert.equal(o.status, 200);
  assert.match(o.headers.get("content-type"), /^application\/json/);
  const spec = await o.json();
  assert.equal(spec.openapi, "3.1.0");
  assert.equal(spec.servers[0].url, "https://ruletrade.jp");
  assert.ok(spec.paths["/mcp"].post, "/mcp の POST が記述されていない");
  assert.equal(spec.paths["/mcp"].get.responses["405"] !== undefined, true);
  // ツール4本ぶんの引数スキーマが載っている
  for (const name of ["get_daily_signals", "get_market_regime", "get_anomaly_summary",
    "get_candlestick_verdict", "get_event_reaction", "get_indicator_verdict",
    "get_etf_decay", "list_tools_guide"]) {
    assert.ok(spec.components.schemas[`${name}_arguments`], `${name} のスキーマがない`);
  }
  assert.ok(spec.components.schemas.ToolResult.required.includes("disclaimer"));
  assert.equal(hasNg(JSON.stringify(spec)), false, "openapi.json に推奨語がある");
});

test("バッチ要求", async () => {
  const res = await handle(
    new Request("https://x.test/mcp", {
      method: "POST",
      body: JSON.stringify([
        { jsonrpc: "2.0", id: 1, method: "ping" },
        { jsonrpc: "2.0", id: 2, method: "tools/list" },
      ]),
    }),
    {},
  );
  const b = await res.json();
  assert.equal(b.length, 2);
});
