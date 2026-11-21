/**
 * サイト（kaburadar.jp）の作り直し（site-rebuild.yml）を時刻どおりに起こす（2026-11-21・本人 OK）。
 *
 * ■ なぜ要るか
 *   サイト側リポジトリ（oo12takemaru-create/kaburadar）の Site Rebuild は GitHub の schedule で
 *   平日 20:30・23:37・6:50 などに置いてあるが、実測で**5〜8時間遅れて翌日に走る**
 *   （11/18〜11/20: 20:30 の回が翌 4:00〜5:00、6:50 の回が 11:40〜12:10）。
 *   夜にページが新しくなるのは誰かが手で流した日だけで、11/16 は今日の発見が翌朝 6:28 まで出なかった。
 *   収集側と同じく、この Worker から workflow_dispatch を叩けば時刻どおりに走る。
 *
 * ■ なぜ PLAN に書かないか
 *   PLAN・NO_DEDUP・alreadyRanToday() は**収集側のリポジトリ（REPO）**の前提で書いてある。
 *   相手のリポジトリもブランチ（master）も違うので、米国株（us_market.js）・世界の地図（world.js）と
 *   同じ「別の表」にした。既存の起動とは別の try の中で動き、失敗しても既存の処理を止めない。
 *
 * ■ 時刻（JST）と、その時刻に新しくなっているデータ（2026-11-13〜11-21 の収集側の実測）
 *   平日 06:45  AI朝刊の取り込み（ai-morning-ingest 06:17〜06:20 完了）・米国株ヒートマップの確定値（06:15→06:16）
 *               休場日も動かす（AI朝刊の prep は休場日も動く＝RUN_ON_HOLIDAYS と同じ扱い）
 *   平日 16:15  引け後のヒートマップ（heatmap 16:00→16:01）・世界の地図（16:00→16:01）・決算速報（15:30）
 *   平日 20:00  今日の発見（karte-ai-ingest 19:47→19:48、Pages 公開 19:48〜19:50）・台帳と予兆（ledger 19:30→19:39）
 *               宝探し（19:01）・値動きとストップ高（movers 18:30→18:35）
 *   平日 21:30  遅れた日の回（サイト側の 23:37 の役目）: 今日の発見の「その後」（2回目の取り込み 20:47）・
 *               パイプラインの予備（pipeline-daily 21:00→21:09〜21:18）・温度計（21:11〜21:14）
 *   土   08:30  週末版（土曜＝今週の振り返り。ai-weekly-ingest 08:11〜08:13）・世界の地図（07:00）・米国株（08:00）
 *   日   07:15  週末版（日曜＝来週の地図）。日曜 07:00 にこの Worker が ai-weekly-ingest を起こして公開させる
 *               （WEEKEND_PLAN の日曜 07:00。取り込みは1〜2分・Pages は30秒ほど）
 *   ※ 平日の休場日の 16:15・20:00・21:30 は起こさない（収集側の市場データが作られない）。
 *   ※ :00 :05 :15 :30 :35 :40 :45 しか書けない（cron の刻み。plan.test.mjs が止める）。
 *
 * ■ 起動が重なったとき（site-rebuild.yml の concurrency: group site-rebuild・cancel-in-progress: false）
 *   走っている回は止めない。後から来た回は順番待ちになる。順番待ちは1本までで、さらに来ると
 *   **待っていた古い方が取り消され（cancelled）、新しい方が残る**。どちらも同じ最新のデータを読むので害はない。
 *
 * ■ トークン
 *   env.GH_PAT_SITE があればそれを、無ければ env.GH_PAT を使う。
 *   既存の GH_PAT は「stock-trading- だけ」で発行してある（README）ので、そのままでは kaburadar を起動できない
 *   （GitHub は 404 Not Found を返す）。本人が GH_PAT の Repository access に kaburadar を足すか、
 *   kaburadar だけの PAT を GH_PAT_SITE として登録する。
 */

export const SITE_REPO = "oo12takemaru-create/kaburadar";
export const SITE_REF = "master"; // サイト側の既定ブランチ（workflow_dispatch は既定ブランチのものしか動かない）
export const SITE_WORKFLOW = "site-rebuild.yml";

/**
 * JST の "HH:MM" → { days: JST の曜日（0=日〜6=土）, holidays: 東証の休場日（平日）でも起こすか }
 * 土日は東証の休場日なので、土日に置く回は holidays: true にする（そうしないと動かない）。
 */
export const SITE_PLAN = {
  "06:45": { days: [1, 2, 3, 4, 5], holidays: true },  // AI朝刊・米国株の確定値のあと
  "16:15": { days: [1, 2, 3, 4, 5], holidays: false }, // 引け後のヒートマップ・世界の地図
  "20:00": { days: [1, 2, 3, 4, 5], holidays: false }, // 今日の発見・台帳・予兆のあと
  "21:30": { days: [1, 2, 3, 4, 5], holidays: false }, // 遅れた日の回（その後・パイプラインの予備）
  "08:30": { days: [6], holidays: true },              // 土: 週末版（今週の振り返り）
  "07:15": { days: [0], holidays: true },              // 日: 週末版（来週の地図）
};

const pad = (n) => String(n).padStart(2, "0");

/**
 * この時刻（UTC）にサイトの作り直しを起こすか。
 *   isTradingDay: holidays.js の isTradingDay（JST の壁時計を UTC として持つ Date を取る）
 * 返り値: { run: boolean, why: string, jst: "HH:MM" }
 */
export function siteTargetsFor(nowUtc, isTradingDay) {
  const jst = new Date(nowUtc.getTime() + 9 * 3600 * 1000);
  const key = `${pad(jst.getUTCHours())}:${pad(jst.getUTCMinutes())}`;
  const p = SITE_PLAN[key];
  if (!p) return { run: false, why: "", jst: key };
  const dow = jst.getUTCDay();
  if (!p.days.includes(dow)) return { run: false, why: `サイトの作り直しはこの曜日（${dow}）は無し`, jst: key };
  if (!p.holidays && !isTradingDay(jst)) {
    return { run: false, why: "休場日なのでサイトの作り直しは起こさない", jst: key };
  }
  return { run: true, why: `サイトの作り直し JST ${key}`, jst: key };
}
