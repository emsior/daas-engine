// Odwrotny proxy do e2e i lokalnej pracy: odtwarza układ produkcyjny (Caddy) w jednym procesie Node bez zależności.
//   /api/*  -> API   (E2E_API_ORIGIN,  domyślnie http://127.0.0.1:8000)
//   reszta  -> kokpit (E2E_WEB_ORIGIN,  domyślnie http://127.0.0.1:3000)
// Słucha na E2E_PROXY_PORT (domyślnie 3100). Strumienie SSE przechodzą bez buforowania.
import http from "node:http";

const api = new URL(process.env.E2E_API_ORIGIN ?? "http://127.0.0.1:8000");
const web = new URL(process.env.E2E_WEB_ORIGIN ?? "http://127.0.0.1:3000");
const port = Number(process.env.E2E_PROXY_PORT ?? 3100);

const server = http.createServer((req, res) => {
  const target =
    req.url?.startsWith("/api/") ||
    req.url === "/api" ||
    req.url === "/health" ||
    req.url === "/ready"
      ? api
      : web;
  const upstream = http.request(
    {
      hostname: target.hostname,
      port: target.port,
      path: req.url,
      method: req.method,
      headers: { ...req.headers, host: req.headers.host ?? "" },
    },
    (response) => {
      res.writeHead(response.statusCode ?? 502, response.headers);
      response.pipe(res);
    },
  );
  upstream.on("error", () => {
    if (!res.headersSent) res.writeHead(502, { "content-type": "text/plain" });
    res.end("bad gateway");
  });
  req.pipe(upstream);
  res.on("close", () => upstream.destroy());
});

server.listen(port, "127.0.0.1", () => {
  console.log(
    `e2e proxy: http://127.0.0.1:${port} (/api -> ${api.origin}, reszta -> ${web.origin})`,
  );
});
