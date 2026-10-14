// Odwrotny proxy do e2e i lokalnej pracy: odtwarza układ produkcyjny (Caddy) w jednym procesie Node bez zależności.
//   /api/*, /mcp(/*), /authorize /token /register /revoke /.well-known/oauth-*  -> API  (E2E_API_ORIGIN,  domyślnie http://127.0.0.1:8000)
//   reszta (w tym /health i /ready)                                             -> kokpit (E2E_WEB_ORIGIN,  domyślnie http://127.0.0.1:3000)
// Zestaw tras kierowanych do API jest wierny `deploy/Caddyfile` (bloki @mcp i @oauth), żeby stos e2e testował serwer MCP
// i konektor OAuth 2.1 tak jak produkcja. `/health` i `/ready` idą do kokpitu (jak catch-all `handle {}` w Caddy): twarde
// wejście na `/health` ma pokazać ekran „Zdrowie danych”, nie surowy JSON API — liveness API sprawdza się przez
// `/api/v1/health` albo bezpośrednio na porcie API (stack.sh czeka właśnie na porcie API, nie przez proxy).
// Słucha na E2E_PROXY_PORT (domyślnie 3100). Strumienie SSE przechodzą bez buforowania.
// Gdy ustawione E2E_TLS_CERT i E2E_TLS_KEY (ścieżki PEM), słucha po https (stack.sh z E2E_TLS=1) i dokłada do żądań
// upstream `x-forwarded-proto: https` (jak Caddy w produkcji; wartość od klienta jest nadpisywana).
// Bez nich zachowanie jest dotychczasowe: http, nagłówki bez zmian.
import { readFileSync } from "node:fs";
import http from "node:http";
import https from "node:https";

const api = new URL(process.env.E2E_API_ORIGIN ?? "http://127.0.0.1:8000");
const web = new URL(process.env.E2E_WEB_ORIGIN ?? "http://127.0.0.1:3000");
const port = Number(process.env.E2E_PROXY_PORT ?? 3100);
const certPath = process.env.E2E_TLS_CERT;
const keyPath = process.env.E2E_TLS_KEY;
const tls = Boolean(certPath && keyPath);

const handler = (req, res) => {
  // Ścieżka bez query (OAuth `/authorize` zawsze niesie query; `path` matcher Caddy też patrzy tylko na ścieżkę).
  const path = (req.url ?? "").split("?")[0];
  const toApi =
    path === "/api" ||
    path.startsWith("/api/") ||
    path === "/mcp" ||
    path.startsWith("/mcp/") ||
    path === "/authorize" ||
    path === "/token" ||
    path === "/register" ||
    path === "/revoke" ||
    path.startsWith("/.well-known/oauth-");
  const target = toApi ? api : web;
  const upstream = http.request(
    {
      hostname: target.hostname,
      port: target.port,
      path: req.url,
      method: req.method,
      headers: {
        ...req.headers,
        host: req.headers.host ?? "",
        ...(tls ? { "x-forwarded-proto": "https" } : {}),
      },
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
};

const server = tls
  ? https.createServer({ cert: readFileSync(certPath), key: readFileSync(keyPath) }, handler)
  : http.createServer(handler);

server.listen(port, "127.0.0.1", () => {
  console.log(
    `e2e proxy: ${tls ? "https" : "http"}://127.0.0.1:${port} (/api -> ${api.origin}, reszta -> ${web.origin})`,
  );
});
