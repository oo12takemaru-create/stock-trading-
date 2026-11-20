/**
 * 相場ボードの取引時間中の自動更新（2026-11-20・株レーダー）— 組み立て（純粋な関数だけ。fetch はしない）
 *
 * 収集側 market_board_1d.py（docs/market_board_1d.json）と同じ形の items を、Yahoo!ファイナンスの
 * spark（v7・複数の記号を1回で）から作る。サイトの market_board.js / topboard.js は、この形をそのまま 1日の線として使う。
 *
 * 違い（market_board_1d.py との差）:
 *   ・spark は終値の並びだけで高値・安値の並びが無い。取引所の本はその日の高値・安値（meta.regularMarketDayHigh/Low）を使い、
 *     無いとき（^TNX は 0 で来る）・先物・為替・ビットコインは線の値から計算する（5分足の高値・安値より少し狭くなることがある）
 *   ・前日終値は日足ではなく meta から（取引所=chartPreviousClose（range=1d のとき前日終値）、先物=previousClose（前日の清算値））
 *   ・途中（fin=0）の時刻 tj は「取得元の値の時刻」（meta.regularMarketTime）。足の終わりではない
 *   ・ts（取得元の値の時刻・日本時間）と live:1 を足す
 * 日本10年（財務省・日次だけ）は対象外（サイトは今のまま日足）。
 */

// [id, 記号, 種類 k, 取引時間の表 ck]（market_board.py の ITEMS と同じ並び・日本10年を除く）
export const ITEMS = [
  ["n225", "^N225", "idx", "tse"],
  ["topix", "1306.T", "idx", "tse"],
  ["g250", "2516.T", "idx", "tse"],
  ["nkf", "NIY=F", "fut", "cme"],
  ["dji", "^DJI", "idx", "nyse"],
  ["spx", "^GSPC", "idx", "nyse"],
  ["ixic", "^IXIC", "idx", "nyse"],
  ["sox", "^SOX", "idx", "nyse"],
  ["vix", "^VIX", "vix", "cboe"],
  ["dax", "^GDAXI", "idx", "xetra"],
  ["ftse", "^FTSE", "idx", "lse"],
  ["sse", "000001.SS", "idx", "sse"],
  ["hsi", "^HSI", "idx", "hkex"],
  ["kospi", "^KS11", "idx", "krx"],
  ["sensex", "^BSESN", "idx", "bse"],
  ["usdjpy", "JPY=X", "fx", "live"],
  ["eurjpy", "EURJPY=X", "fx", "live"],
  ["eurusd", "EURUSD=X", "fx", "live"],
  ["us10", "^TNX", "rate", "ust"],
  ["wti", "CL=F", "cmd", "cme"],
  ["gold", "GC=F", "cmd", "cme"],
  ["copper", "HG=F", "cmd", "cme"],
  ["btc", "BTC-USD", "crypto", "live"],
];

// 取引時間（現地・分）。market_board_1d.py の SESS と同じ
export const SESS = {
  tse: ["Asia/Tokyo", 540, 930],
  nyse: ["America/New_York", 570, 960],
  cboe: ["America/New_York", 570, 975],
  ust: ["America/New_York", 480, 900],
  xetra: ["Europe/Berlin", 540, 1050],
  lse: ["Europe/London", 480, 990],
  sse: ["Asia/Shanghai", 570, 900],
  hkex: ["Asia/Hong_Kong", 570, 970],
  krx: ["Asia/Seoul", 540, 930],
  bse: ["Asia/Kolkata", 555, 930],
};
const STALE_DAYS = 6;
const JST = 540;

/** 2つの spark の呼び方（取引所=その日の5分足／先物・為替・暗号資産=2日分の15分足） */
export function groups() {
  const ex = [], wide = [];
  for (const [, t, , ck] of ITEMS) (ck === "cme" || ck === "live" ? wide : ex).push(t);
  return [
    { symbols: ex, range: "1d", interval: "5m", iv: 5 },
    { symbols: wide, range: "2d", interval: "15m", iv: 15 },
  ];
}

// ── 時刻 ──
// 夏時間（米国: 3月第2日曜 2:00 〜 11月第1日曜 2:00 現地／欧州: 3月最終日曜 1:00 UTC 〜 10月最終日曜 1:00 UTC）
function nthSunday(y, m, n) { const d = new Date(Date.UTC(y, m, 1)); const first = (7 - d.getUTCDay()) % 7 + 1; return first + (n - 1) * 7; }
function lastSunday(y, m) { const d = new Date(Date.UTC(y, m + 1, 0)); return d.getUTCDate() - d.getUTCDay(); }
function usDst(ms) {
  const y = new Date(ms).getUTCFullYear();
  const a = Date.UTC(y, 2, nthSunday(y, 2, 2), 7);   // 2:00 EST = 7:00 UTC
  const b = Date.UTC(y, 10, nthSunday(y, 10, 1), 6); // 2:00 EDT = 6:00 UTC
  return ms >= a && ms < b;
}
function euDst(ms) {
  const y = new Date(ms).getUTCFullYear();
  return ms >= Date.UTC(y, 2, lastSunday(y, 2), 1) && ms < Date.UTC(y, 9, lastSunday(y, 9), 1);
}
/** その時刻の現地の UTC からの差（分） */
export function tzOffset(tz, ms) {
  switch (tz) {
    case "Asia/Tokyo": case "Asia/Seoul": return 540;
    case "Asia/Shanghai": case "Asia/Hong_Kong": return 480;
    case "Asia/Kolkata": return 330;
    case "America/New_York": return usDst(ms) ? -240 : -300;
    case "Europe/Berlin": return euDst(ms) ? 120 : 60;
    case "Europe/London": return euDst(ms) ? 60 : 0;
    default: return 0;
  }
}
const pad = (n) => String(n).padStart(2, "0");
/** 現地の壁時計（UTC として持つ Date） */
const wall = (ms, off) => new Date(ms + off * 60000);
const ymd = (d) => `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`;
const hhmm = (d) => `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
function isoJst(ms) {
  const d = wall(ms, JST);
  return `${ymd(d)}T${hhmm(d)}+09:00`;
}
export function isoJstSec(ms) {
  const d = wall(ms, JST);
  return `${ymd(d)}T${hhmm(d)}:${pad(d.getUTCSeconds())}+09:00`;
}

// ── 数の丸め（market_board_1d.py の sig / rnd と同じ） ──
export function sig(v, n = 5) {
  if (!v) return 0;
  const p = n - 1 - Math.floor(Math.log10(Math.abs(v)));
  return roundTo(v, p);
}
function roundTo(v, d) {
  if (d >= 0) { const f = 10 ** d; return Math.round(v * f) / f; }
  const f = 10 ** -d; return Math.round(v / f) * f;
}
export function rnd(v, k, ref) {
  if (v == null || !isFinite(v)) return null;
  if (k === "rate") return roundTo(v, 3);
  const a = Math.abs(ref == null ? v : ref);
  return roundTo(v, a >= 100 ? 2 : a >= 10 ? 3 : a >= 1 ? 4 : 5);
}

/** spark の1本 → {ts:[秒], c:[値], meta}（null と 0 以下を除く） */
export function bars(res) {
  const r = res && res.response && res.response[0];
  if (!r || !r.meta) return null;
  const ts = r.timestamp || [], cl = (r.indicators && r.indicators.quote && r.indicators.quote[0] && r.indicators.quote[0].close) || [];
  const t = [], c = [];
  for (let i = 0; i < ts.length; i++) {
    const v = cl[i];
    if (v == null || !(v > 0)) continue;
    if (t.length && ts[i] <= t[t.length - 1]) { c[c.length - 1] = v; continue; }
    t.push(ts[i]); c.push(v);
  }
  return { t, c, meta: r.meta };
}

/**
 * 前日終値をそろえる。Yahoo の meta の前日終値は本によってずれる（2026-11-20: SENSEX の chartPreviousClose が
 * 1日前のまま・WTI の previousClose が日足の終値と違う）ので、収集側の market_board_1d.json（日足から作った値）を正にする。
 *   同じ取引日 → その pc／前の取引日の引けまで（fin=1）→ その last（＝前日終値）／それ以外 → Yahoo の値のまま
 * 為替・ビットコイン（直近24時間・roll）は基準が「24時間前の値」なので触らない。
 */
export function basePc(base, id, sday) {
  const s = base && base[id];
  if (!s || !s.ok || !s.d || s.roll) return null;
  if (s.d === sday && s.pc > 0) return s.pc;
  if (s.d < sday && s.fin && s.last > 0) return s.last;
  return null;
}

/** 1本を market_board_1d.json の1行の形に */
export function buildOne(spec, b, iv, nowMs, base) {
  const [id, tk, k, ck] = spec;
  const row = { id, t: tk, ok: 0, live: 1 };
  if (!b || b.t.length < 2) { row.note = "1日の線なし"; return row; }
  const m = b.meta;
  const mt = m.regularMarketTime ? m.regularMarketTime * 1000 : null;  // 取得元の値の時刻
  const mp = m.regularMarketPrice > 0 ? m.regularMarketPrice : null;
  let T = b.t.map((s) => s * 1000), C = b.c.slice();
  let sday, closeAt = null, tz, off, pc = null, roll = null, hi = null, lo = null;

  if (ck === "live") {
    tz = "Asia/Tokyo"; off = JST;
    const end = T[T.length - 1], from = end - 24 * 3600e3;
    let j = 0; while (j < T.length && T[j] <= from) j++;
    if (j > 0) pc = C[j - 1];
    T = T.slice(j); C = C.slice(j);
    roll = 24;
  } else if (ck === "cme") {
    tz = "America/New_York";
    off = tzOffset(tz, T[T.length - 1]);
    const sd = T.map((x) => ymd(wall(x + 6 * 3600e3, off)));     // 18:00 からが翌日の取引日
    const last = sd[sd.length - 1];
    const keep = sd.map((d) => d === last);
    // 前日の最後の値（清算値が meta に無いときの代わり）
    let prevLast = null; for (let i = 0; i < T.length; i++) if (!keep[i]) prevLast = C[i];
    T = T.filter((_, i) => keep[i]); C = C.filter((_, i) => keep[i]);
    sday = last;
    closeAt = Date.parse(`${last}T17:00:00Z`) - off * 60000;
    pc = m.previousClose > 0 ? m.previousClose : prevLast;
  } else {
    const s = SESS[ck];
    if (!s) { row.note = "1日の線なし"; return row; }
    tz = s[0];
    off = tzOffset(tz, T[T.length - 1]);
    const tt = [], cc = [];
    for (let i = 0; i < T.length; i++) {
      const w = wall(T[i], off), mm = w.getUTCHours() * 60 + w.getUTCMinutes();
      if (mm >= s[1] && mm < s[2]) { tt.push(T[i]); cc.push(C[i]); }
    }
    if (!tt.length) { row.note = "1日の線なし"; return row; }
    const days = tt.map((x) => ymd(wall(x, off)));
    sday = days[days.length - 1];
    T = tt.filter((_, i) => days[i] === sday); C = cc.filter((_, i) => days[i] === sday);
    closeAt = Date.parse(`${sday}T${pad(Math.floor(s[2] / 60))}:${pad(s[2] % 60)}:00Z`) - off * 60000;
    pc = m.chartPreviousClose > 0 ? m.chartPreviousClose : (m.previousClose > 0 ? m.previousClose : null);
    // その日の高値・安値（meta が同じ取引日を指しているときだけ）
    if (mt && ymd(wall(mt, off)) === sday && m.regularMarketDayHigh > 0 && m.regularMarketDayLow > 0) {
      hi = m.regularMarketDayHigh; lo = m.regularMarketDayLow;
    }
  }
  if (T.length < 2) { row.note = "1日の線なし（足が足りない）"; return row; }
  if (!roll && sday) { const bp = basePc(base, id, sday); if (bp != null) pc = bp; }
  if (pc == null) { row.note = "1日の線なし（前日終値なし）"; return row; }
  if (!sday) sday = ymd(wall(T[T.length - 1], JST));
  if ((nowMs - Date.parse(`${sday}T00:00:00Z`)) / 864e5 > STALE_DAYS + 1) { row.note = `1日の線なし（最終 ${sday}）`; return row; }

  const fin = closeAt == null ? 0 : (nowMs >= closeAt ? 1 : 0);
  const ivMs = iv * 60000;
  // 最後の値: 取得元の今の値（同じ取引日の値で、最後の足より後なら）
  let last = C[C.length - 1], lastAt = T[T.length - 1];
  if (mp && mt && mt >= T[T.length - 1] && (ck === "live" || mt <= (closeAt || Infinity) + 6 * 3600e3)) {
    last = mp; lastAt = mt;
    if (mt < T[T.length - 1] + ivMs) C[C.length - 1] = mp;     // 今の足の中 → その足の値を置きかえる
    else if (Math.abs(mp - C[C.length - 1]) > 1e-9) {            // 足より後（引けの値など）→ 1点足す
      T.push(Math.max(T[T.length - 1] + ivMs, fin && closeAt ? Math.min(mt, closeAt) : mt)); C.push(mp);
    }
  }
  if (hi == null) { hi = -Infinity; lo = Infinity; for (const v of C) { if (v > hi) hi = v; if (v < lo) lo = v; } }
  hi = Math.max(hi, last); lo = Math.min(lo, last);
  // 足の時刻（iv の何本分）
  const t0 = T[0];
  const steps = T.map((x) => Math.round((x - t0) / ivMs));
  const mv = steps.map((s, i) => s - (i ? steps[i - 1] : 0));
  const endMs = fin ? closeAt : Math.min(lastAt, nowMs);
  const endOff = tzOffset(tz, endMs);
  row.ok = 1;
  Object.assign(row, {
    d: sday, tz, fin,
    te: hhmm(wall(endMs, endOff)),
    tj: isoJst(endMs),
    ts: isoJst(lastAt),
    iv, t0: hhmm(wall(t0, tzOffset(tz, t0))), m: mv,
    c: C.map((v) => sig(v)),
    pc: rnd(pc, k), last: rnd(last, k),
    chg: rnd(last - pc, k, last),
    pct: k === "rate" ? null : Math.round((last / pc - 1) * 10000) / 100,
    hi: rnd(hi, k), lo: rnd(lo, k),
  });
  if (roll) row.roll = roll;
  return row;
}

/**
 * 更新の間隔（分）。JST で
 *   平日 8:45〜15:45 = 5分（日本の取引時間）
 *   土曜 7:00 〜 月曜 8:45 = 60分（週末は暗号資産・為替の週末だけ）
 *   それ以外（平日の夜〜翌朝・欧州と米国の時間） = 15分
 */
export function cadence(nowMs) {
  const d = wall(nowMs, JST), w = d.getUTCDay(), mm = d.getUTCHours() * 60 + d.getUTCMinutes();
  if (w >= 1 && w <= 5 && mm >= 525 && mm < 945) return 5;
  if ((w === 6 && mm >= 420) || w === 0 || (w === 1 && mm < 525)) return 60;
  return 15;
}

/** spark の結果（記号 → 1本）2つ分から、ファイル全体を作る */
export function build(results, nowMs, base1d) {
  const by = {}, base = {};
  for (const r of (base1d && base1d.items) || []) if (r && r.id) base[r.id] = r;
  const gs = groups();
  results.forEach((res, gi) => {
    for (const r of (res && res.spark && res.spark.result) || []) by[r.symbol] = { b: bars(r), iv: gs[gi].iv };
  });
  const items = ITEMS.map((spec) => {
    const g = by[spec[1]];
    try { return buildOne(spec, g && g.b, g ? g.iv : 5, nowMs, base); }
    catch (e) { return { id: spec[0], t: spec[1], ok: 0, live: 1, note: "1日の線なし" }; }
  });
  const every = cadence(nowMs);
  return {
    v: 1, live: 1,
    updated: isoJstSec(nowMs),
    every,
    next: isoJstSec(nowMs + every * 60000),
    ok: items.reduce((a, i) => a + i.ok, 0), count: items.length,
    source: "Yahoo!ファイナンスの配信値（5分足・約20分遅れて届く値）",
    items,
  };
}
