// 法務線(人向けサイト kaburadar.jp / ruletrade.jp と同一)
//  ・投資助言と受け取られうる「推奨語」は出力に一切含めない
//  ・すべてのレスポンスに免責キー(disclaimer)を常設する
//
// 出力に出る文字列は scrub() を通す。JSONの中身(外部データ由来の文言)も再帰的に通す。

export const DISCLAIMER =
  "本ツールの出力は公開ルール(機械的な条件)への該当状況を示すデータであり、投資助言・売買の勧誘ではありません。" +
  "特定銘柄の売買を勧めるものではなく、将来の利益を保証しません。最終判断はご自身の責任で行ってください。" +
  "無料版データは1営業日遅れです。";

// 推奨語(見つけたら伏せ字にする)。人向けサイトの運用と同じ線引き。
export const NG_WORDS = [
  "推奨", "おすすめ", "オススメ", "お勧め", "買い推奨", "売り推奨",
  "買うべき", "売るべき", "買い時", "売り時", "今が買い", "今が売り",
  "必ず儲かる", "確実に儲かる", "絶対に上がる", "絶対に下がる", "勝てる", "儲かる",
  "買え", "売れ", "仕込め", "利確せよ", "損切せよ", "損切りせよ",
  "強く買い", "強く売り", "strong buy", "strong sell", "buy now", "sell now",
];

const MASK = "［表現調整］";

// NG語を伏せ字に置換。副作用として置換件数を返す(ログ用)。
export function scrubText(s, hit) {
  if (typeof s !== "string" || !s) return s;
  let out = s;
  for (const w of NG_WORDS) {
    const re = new RegExp(w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "gi");
    if (re.test(out)) {
      out = out.replace(re, MASK);
      if (hit) hit.count = (hit.count || 0) + 1;
    }
  }
  return out;
}

// JSONを再帰的に scrub する(キーは触らず、文字列値だけ)。
export function scrub(value, hit) {
  if (typeof value === "string") return scrubText(value, hit);
  if (Array.isArray(value)) return value.map((v) => scrub(v, hit));
  if (value && typeof value === "object") {
    const o = {};
    for (const [k, v] of Object.entries(value)) o[k] = scrub(v, hit);
    return o;
  }
  return value;
}
