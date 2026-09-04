// 公開JSON(GitHub raw)を読むだけ。エンジンは持たない。
// キャッシュは同一インスタンス内で短時間(既定60秒)。プラットフォームのキャッシュに依存しない。

export const DEFAULT_BASE =
  "https://raw.githubusercontent.com/oo12takemaru-create/stock-trading-/main/docs";

const cache = new Map(); // path -> { at, data }

export function makeLoader(env = {}, fetchImpl = globalThis.fetch) {
  const base = (env.DATA_BASE || DEFAULT_BASE).replace(/\/+$/, "");
  const ttl = Number(env.CACHE_TTL_MS || 60_000);

  async function load(path) {
    const now = Date.now();
    const c = cache.get(path);
    if (c && now - c.at < ttl) return c.data;
    const url = `${base}/${path}?_=${Math.floor(now / ttl)}`;
    const res = await fetchImpl(url, { headers: { "user-agent": "ruletrade-mcp/0.1" } });
    if (!res.ok) throw new Error(`データ取得に失敗しました: ${path} (HTTP ${res.status})`);
    const data = await res.json();
    cache.set(path, { at: now, data });
    return data;
  }

  return { load, base };
}

export function clearCache() {
  cache.clear();
}
