/**
 * 世界の値動きの地図（world.yml）を JST の時刻表で起こす（2026-10-30）。
 *
 * ■ なぜ PLAN に書かないか
 *   PLAN は東証の営業日で動く（東証の休場日は市場データのものを起こさない）。世界の地図は
 *   東証が休みでも海外は動くので、東証の休場日と無関係に JST の曜日だけで判定する。
 *   米国株ヒートマップ（us_market.js）と同じ「別の表」の形。
 *
 * ■ 時刻（JST）
 *   07:00 確定版（昨夜の米国・欧州の引け、前日のアジアの引け）… 火〜土（月〜金の米国の引け後）
 *   16:00 日本とアジアの引け後 … 月〜金
 *   23:00 欧州の途中・米国の寄り後 … 月〜金
 *   world.yml は1日に何度も走るのが正常（NO_DEDUP に入れてある）。
 *   日付アーカイブは収集側（world_fetch.py）が「S&P500 の取引日が前の記録と同じなら積まない」で冪等にしている
 *   （米国の休場明けでない朝は積まれない）。
 */

export const WORLD_WORKFLOW = "world.yml";

/** JST の曜日（0=日〜6=土）→ 時刻 → ワークフロー */
export const WORLD_PLAN = {
  "07:00": { days: [2, 3, 4, 5, 6], targets: [WORLD_WORKFLOW] },
  "16:00": { days: [1, 2, 3, 4, 5], targets: [WORLD_WORKFLOW] },
  "23:00": { days: [1, 2, 3, 4, 5], targets: [WORLD_WORKFLOW] },
};

const pad = (n) => String(n).padStart(2, "0");

/** この時刻（UTC）に起こす世界の地図のワークフロー。東証の休場日は見ない */
export function worldTargetsFor(nowUtc) {
  const jst = new Date(nowUtc.getTime() + 9 * 3600 * 1000);
  const key = `${pad(jst.getUTCHours())}:${pad(jst.getUTCMinutes())}`;
  const p = WORLD_PLAN[key];
  if (!p) return { targets: [], why: "", jst: key };
  if (!p.days.includes(jst.getUTCDay())) return { targets: [], why: `世界の地図はこの曜日（${jst.getUTCDay()}）は休み`, jst: key };
  return { targets: p.targets, why: `世界の地図 JST ${key}`, jst: key };
}
