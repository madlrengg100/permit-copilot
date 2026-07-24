import { createReadStream, statSync } from "node:fs";
import { createServer, request as proxyRequest } from "node:http";
import { extname, join, normalize } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(fileURLToPath(new URL(".", import.meta.url)), "dist");
const port = Number(process.env.PORT || 5173);
const host = process.env.HOST || "0.0.0.0";
const backendHost = process.env.BACKEND_HOST || "127.0.0.1";
const backendPort = Number(process.env.BACKEND_PORT || 8000);

const mimeTypes = {
  ".css": "text/css; charset=utf-8",
  ".html": "text/html; charset=utf-8",
  ".ico": "image/x-icon",
  ".js": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".png": "image/png",
  ".svg": "image/svg+xml",
  ".webp": "image/webp",
};

function proxyApi(req, res) {
  const upstream = proxyRequest(
    {
      hostname: backendHost,
      port: backendPort,
      method: req.method,
      path: req.url,
      headers: { ...req.headers, host: `${backendHost}:${backendPort}` },
    },
    (upstreamResponse) => {
      res.writeHead(upstreamResponse.statusCode || 502, upstreamResponse.headers);
      upstreamResponse.pipe(res);
    },
  );
  upstream.on("error", (error) => {
    if (!res.headersSent) res.writeHead(502, { "content-type": "text/plain; charset=utf-8" });
    res.end(`백엔드 연결 실패: ${error.message}`);
  });
  req.pipe(upstream);
}

function serveFile(req, res) {
  const pathname = decodeURIComponent(new URL(req.url || "/", "http://localhost").pathname);
  const relative = normalize(pathname).replace(/^(\.\.(\/|\\|$))+/, "").replace(/^[/\\]+/, "");
  let file = join(root, relative || "index.html");
  try {
    if (statSync(file).isDirectory()) file = join(file, "index.html");
    if (!statSync(file).isFile()) throw new Error("not a file");
  } catch {
    // React의 클라이언트 라우팅을 위해 존재하지 않는 경로는 index.html로 보낸다.
    file = join(root, "index.html");
  }
  res.writeHead(200, {
    "content-type": mimeTypes[extname(file)] || "application/octet-stream",
    "cache-control": file.endsWith("index.html") ? "no-cache" : "public, max-age=31536000, immutable",
  });
  createReadStream(file).pipe(res);
}

createServer((req, res) => {
  if (req.url === "/api" || req.url?.startsWith("/api/")) proxyApi(req, res);
  else serveFile(req, res);
}).listen(port, host, () => {
  console.info(`Production frontend listening on http://${host}:${port}`);
});
