import { createReadStream, statSync } from "node:fs";
import { createServer, request as httpRequest } from "node:http";
import { extname, join, normalize, resolve } from "node:path";

const root = resolve("dist");
const port = Number(process.env.PORT || 80);
const backend = new URL(process.env.BACKEND_URL || "http://backend:8000");

const mimeTypes = {
  ".css": "text/css; charset=utf-8",
  ".html": "text/html; charset=utf-8",
  ".ico": "image/x-icon",
  ".js": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".map": "application/json; charset=utf-8",
  ".png": "image/png",
  ".svg": "image/svg+xml",
  ".webp": "image/webp",
  ".woff": "font/woff",
  ".woff2": "font/woff2",
};

function sendFile(res, filePath) {
  const type = mimeTypes[extname(filePath)] || "application/octet-stream";
  res.writeHead(200, {
    "content-type": type,
    "cache-control": filePath.includes(`${root}\\assets`) || filePath.includes(`${root}/assets`)
      ? "public, max-age=31536000, immutable"
      : "no-cache",
  });
  createReadStream(filePath).pipe(res);
}

function staticPath(pathname) {
  const decoded = decodeURIComponent(pathname.split("?")[0]);
  const normalized = normalize(decoded).replace(/^(\.\.[/\\])+/, "");
  const candidate = join(root, normalized);
  return candidate.startsWith(root) ? candidate : join(root, "index.html");
}

function proxyApi(req, res) {
  const target = new URL(req.url || "/", backend);
  const proxy = httpRequest(
    {
      hostname: target.hostname,
      port: target.port || 80,
      path: target.pathname + target.search,
      method: req.method,
      headers: {
        ...req.headers,
        host: backend.host,
      },
    },
    (upstream) => {
      res.writeHead(upstream.statusCode || 502, upstream.headers);
      upstream.pipe(res);
    },
  );

  proxy.on("error", () => {
    res.writeHead(502, { "content-type": "text/plain; charset=utf-8" });
    res.end("后端服务暂不可用");
  });

  req.pipe(proxy);
}

createServer((req, res) => {
  const url = req.url || "/";
  if (url.startsWith("/api/")) {
    proxyApi(req, res);
    return;
  }

  let filePath = staticPath(url);
  try {
    const stat = statSync(filePath);
    if (stat.isDirectory()) {
      filePath = join(filePath, "index.html");
    }
    sendFile(res, filePath);
  } catch {
    sendFile(res, join(root, "index.html"));
  }
}).listen(port, "0.0.0.0");
