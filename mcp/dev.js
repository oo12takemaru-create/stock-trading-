// ローカル確認用: node dev.js → http://localhost:8787/mcp
import http from "node:http";
import { handle } from "./src/server.js";

const port = Number(process.env.PORT || 8787);
http
  .createServer(async (req, res) => {
    const chunks = [];
    for await (const c of req) chunks.push(c);
    const body = chunks.length ? Buffer.concat(chunks) : undefined;
    const request = new Request(`http://localhost:${port}${req.url}`, {
      method: req.method,
      headers: req.headers,
      body: req.method === "GET" || req.method === "HEAD" ? undefined : body,
    });
    const r = await handle(request, process.env);
    res.writeHead(r.status, Object.fromEntries(r.headers));
    res.end(Buffer.from(await r.arrayBuffer()));
  })
  .listen(port, () => console.log(`ruletrade-mcp dev: http://localhost:${port}/mcp`));
