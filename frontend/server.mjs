import { createReadStream, statSync } from "node:fs";
import { createServer, request as proxyRequest } from "node:http";
import { extname, join, normalize } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(fileURLToPath(new URL(".", import.meta.url)), "dist");
const port = Number(process.env.PORT || 5173);
const host = process.env.HOST || "0.0.0.0";
const backendHost = process.env.BACKEND_HOST || "127.0.0.1";
const backendPort = Number(process.env.BACKEND_PORT || 8000);
const appToken = process.env.APP_TOKEN || "";

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
  // SSE(text/event-stream) 스트리밍이 끊기지 않게 하는 게 핵심이다.
  //  1) 응답 소켓의 Nagle 알고리즘을 꺼서(setNoDelay) 작은 청크(백엔드가 2초마다
  //     보내는 ': keepalive')를 즉시 클라이언트로 흘린다. 버퍼링되면 소켓이 노는
  //     것처럼 보여 keep-alive 타임아웃(기본 5초)에 걸려 진단 도중 끊긴다.
  //  2) 이 응답에는 소켓 타임아웃을 걸지 않는다(setTimeout(0)).
  //  3) 헤더를 즉시 내보내고(flushHeaders) 청크가 올 때마다 바로 write 한다.
  res.socket?.setNoDelay?.(true);
  res.setTimeout?.(0);

  const upstream = proxyRequest(
    {
      hostname: backendHost,
      port: backendPort,
      method: req.method,
      path: req.url,
      // APP_TOKEN은 브라우저 번들에 넣지 않는다. 같은 서버의 프록시만 비밀값을
      // 보유하고 백엔드로 전달해, 외부 사용자는 토큰을 볼 수 없게 한다.
      headers: {
        ...req.headers,
        host: `${backendHost}:${backendPort}`,
        ...(appToken ? { "x-app-token": appToken } : {}),
      },
    },
    (upstreamResponse) => {
      res.writeHead(upstreamResponse.statusCode || 502, upstreamResponse.headers);
      res.flushHeaders?.();
      upstreamResponse.on("data", (chunk) => res.write(chunk));
      upstreamResponse.on("end", () => res.end());
      upstreamResponse.on("error", () => res.end());
    },
  );
  upstream.on("socket", (socket) => socket.setNoDelay?.(true));
  upstream.setTimeout?.(0);
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

const server = createServer((req, res) => {
  if (req.url === "/api" || req.url?.startsWith("/api/")) proxyApi(req, res);
  else serveFile(req, res);
});
// 긴 SSE 진단(9초+)이 keep-alive 유휴 타임아웃에 걸려 끊기지 않도록 넉넉히 둔다.
server.keepAliveTimeout = 120000;
server.headersTimeout = 125000;
server.requestTimeout = 0;
server.listen(port, host, () => {
  console.info(`Production frontend listening on http://${host}:${port}`);
});
