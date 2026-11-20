/**
 * kaburadar-board-live — 相場ボード（24本中23本）の取引時間中の自動更新の中継ぎ（2026-11-20）
 *
 *   GET /board  → market_board_1d.json と同じ形の JSON（items ごとに live:1・ts=取得元の値の時刻）
 *   GET /       → 状態（今の JST・間隔・KV の値の古さ）。開くだけでは取りに行かない
 *
 * ■ しくみ（Cron Trigger を使わない「見られたときに古ければ取り直す」形）
 *   KV に1つのキー（board）で最後の結果を持つ。/board が呼ばれたとき、その値が間隔（cadence: 平日 8:45〜15:45 は5分・
 *   夜と朝は15分・週末は60分）より古ければ Yahoo!ファイナンスの spark から取り直して KV に書く。
 *   ・誰も見ていない時間は何もしない（取得元に行かない・KV に書かない）
 *   ・書き込みは間隔で頭打ち（平日 最大 約160回/日。無料枠 1,000回/日）
 *   ・Cron Trigger の枠（無料はアカウントで5本・ruletrade-cron が3本、kaburadar-buyback も使用）を使わない
 *   ・少し古い（間隔の2倍まで）ときは手元の値をすぐ返して、裏で取り直す（waitUntil）。もっと古い・無いときは待って取る
 *   ・取れなかったときは前の値を返す（画面は updated を見て、30分より古ければ今までの1日3回の値に戻る）
 * ■ 費用: Workers 無料枠（10万リクエスト/日・CPU 10ms/回・サブリクエスト 50/回）と KV 無料枠（読み 10万/日・書き 1,000/日）の中。
 *   1回の取り直しは spark 2回（サブリクエスト2）。
 * 「リアルタイム」とは書かない（約20分遅れの配信値）。Vercel の関数は使わない。
 */
import { build, cadence, groups } from "./build.js";

const SPARK = "https://query1.finance.yahoo.com/v7/finance/spark";
const KEY = "board";
const UA = "Mozilla/5.0 (compatible; kaburadar-board/1.0)";
const ORIGINS = /^https:\/\/(www\.)?kaburadar\.jp$|^http:\/\/(localhost|127\.0\.0\.1)(:\d+)?$/;

let MEM = null;          // 同じ isolate の中の手元の値（KV を読む回数を減らす）
let INFLIGHT = null;     // 同じ isolate の中で取り直しを重ねない

async function sparkGet(g) {
  const u = `${SPARK}?symbols=${encodeURIComponent(g.symbols.join(","))}&range=${g.range}&interval=${g.interval}`;
  const ctl = new AbortController();
  const tm = setTimeout(() => ctl.abort(), 8000);
  try {
    const r = await fetch(u, { headers: { "User-Agent": UA, Accept: "application/json" }, signal: ctl.signal });
    if (!r.ok) return { err: r.status };
    return await r.json();
  } catch (e) {
    return { err: String(e && e.name || e) };
  } finally {
    clearTimeout(tm);
  }
}

// 前日終値の正（収集側の market_board_1d.json・1日3回）。同じ isolate で10分は持ち回す
const BASE_URL = "https://oo12takemaru-create.github.io/stock-trading-/market_board_1d.json";
let BASE = null;
async function base1d() {
  if (BASE && Date.now() - BASE.at < 600000) return BASE.d;
  try {
    const r = await fetch(BASE_URL, { cf: { cacheTtl: 300 } });
    if (r.ok) { BASE = { d: await r.json(), at: Date.now() }; return BASE.d; }
  } catch (e) { /* 読めなければ Yahoo の前日終値のまま */ }
  return BASE ? BASE.d : null;
}

async function refresh(env) {
  const now = Date.now();
  const [base, ...res] = await Promise.all([base1d(), ...groups().map(sparkGet)]);
  const out = build(res, now, base);
  out.at = now;
  const errs = res.map((r) => r && r.err).filter(Boolean);
  if (errs.length) out.fetch_err = errs;
  if (!out.ok) return null;                       // 1本も取れない → 書かない（前の値のまま）
  const body = JSON.stringify(out);
  await env.LIVE.put(KEY, body);
  MEM = { body, at: now };
  return MEM;
}

async function current(env) {
  if (MEM && Date.now() - MEM.at < 30000) return MEM;
  const body = await env.LIVE.get(KEY, { cacheTtl: 30 });
  if (!body) return null;
  let at = 0;
  try { at = JSON.parse(body).at || 0; } catch (e) { return null; }
  MEM = { body, at };
  return MEM;
}

function cors(req) {
  const o = req.headers.get("Origin") || "";
  const h = { "Vary": "Origin" };
  if (ORIGINS.test(o)) h["Access-Control-Allow-Origin"] = o;
  return h;
}

function json(body, req, status = 200, maxAge = 60) {
  return new Response(body, {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": `public, max-age=${maxAge}`,
      "X-Robots-Tag": "noindex",
      ...cors(req),
    },
  });
}

export default {
  async fetch(req, env, ctx) {
    const url = new URL(req.url);
    if (req.method === "OPTIONS") return new Response(null, { status: 204, headers: { ...cors(req), "Access-Control-Allow-Methods": "GET", "Access-Control-Max-Age": "86400" } });
    if (req.method !== "GET") return new Response("method", { status: 405 });

    if (url.pathname === "/board") {
      const now = Date.now(), every = cadence(now) * 60000;
      let cur = await current(env);
      const age = cur ? now - cur.at : Infinity;
      if (age >= every - 20000) {
        const go = () => (INFLIGHT = INFLIGHT || refresh(env).finally(() => { INFLIGHT = null; }));
        if (cur && age < every * 2) {
          ctx.waitUntil(go().catch(() => null));        // 少し古い → 今の値を返して裏で取り直す
        } else {
          const got = await go().catch(() => null);      // 無い・かなり古い → 待って取る
          if (got) cur = got;
        }
      }
      if (!cur) return json(JSON.stringify({ v: 1, live: 1, ok: 0, items: [] }), req, 503, 30);
      return json(cur.body, req);
    }

    if (url.pathname === "/") {
      const now = Date.now();
      const cur = await current(env);
      let ok = null, updated = null;
      if (cur) { try { const d = JSON.parse(cur.body); ok = d.ok; updated = d.updated; } catch (e) { /* */ } }
      return json(JSON.stringify({
        name: "kaburadar-board-live",
        now_jst: new Date(now + 9 * 3600e3).toISOString().slice(0, 16).replace("T", " "),
        every_min: cadence(now) / 1,
        kv: cur ? { updated, ok, age_sec: Math.round((now - cur.at) / 1000) } : null,
      }), req, 200, 0);
    }
    return new Response("not found", { status: 404 });
  },
};
