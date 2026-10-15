// Static stand-in for docker/nginx.conf (T24C) used only by playwright.prefix.config.ts.
//
// Serves <root>/fair2wise/ with the gateway's public routes so the unchanged e2e
// specs run against a prefixed production build: / -> 302 /fair2wise/,
// /fair2wise -> 301 /fair2wise/, SPA fallback only inside /fair2wise/, a missing
// file-like path (assets) is 404, /fair2wise/api/* and /fair2wise/model-api are
// 404 here (Playwright mocks the API in the page), nothing outside the prefix.
// The real gateway is exercised by tests/gateway (Python) against nginx.conf.
//
// Usage: node e2e/prefix-gateway.mjs <root> <port>
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';

const [rootArg, portArg] = process.argv.slice(2);
const ROOT = path.resolve(rootArg || 'dist/prefix-root');
const PORT = Number.parseInt(portArg || '0', 10);
const PREFIX = '/fair2wise';
const TYPES = {
  '.css': 'text/css; charset=utf-8',
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.woff2': 'font/woff2',
};

function send(res, status, body = '', headers = {}) {
  res.writeHead(status, { 'Content-Type': 'text/plain; charset=utf-8', ...headers });
  res.end(body);
}

function file(res, rel) {
  const target = path.resolve(ROOT, `.${rel}`);
  if (!target.startsWith(`${ROOT}${path.sep}`) || !fs.existsSync(target) || !fs.statSync(target).isFile()) {
    return false;
  }
  res.writeHead(200, { 'Content-Type': TYPES[path.extname(target)] || 'application/octet-stream' });
  fs.createReadStream(target).pipe(res);
  return true;
}

const server = http.createServer((req, res) => {
  const url = new URL(req.url || '/', 'http://gateway.invalid');
  const p = decodeURIComponent(url.pathname);
  const qs = url.search;
  if (p === '/healthz' || p === `${PREFIX}/healthz`) return send(res, 200, 'ok\n');
  if (p === '/') return send(res, 302, '', { Location: `${PREFIX}/` });
  if (p === PREFIX) return send(res, 301, '', { Location: `${PREFIX}/${qs}` });
  if (!p.startsWith(`${PREFIX}/`)) return send(res, 404, 'not found');
  if (p === `${PREFIX}/api` || p.startsWith(`${PREFIX}/api/`) || p.startsWith(`${PREFIX}/model-api`)) {
    return send(res, 404, 'not found');
  }
  if (p.startsWith(`${PREFIX}/docs/`)) {
    const rel = p.endsWith('/') ? `${p}index.html` : p;
    if (!file(res, rel)) send(res, 404, 'not found');
    return undefined;
  }
  if (file(res, p)) return undefined;
  if (p.startsWith(`${PREFIX}/assets/`) || /\.[A-Za-z0-9]{1,8}$/.test(p)) return send(res, 404, 'not found');
  if (!file(res, `${PREFIX}/index.html`)) send(res, 500, 'UI build missing');
  return undefined;
});

server.listen(PORT, '127.0.0.1', () => {
  console.log(`[prefix-gateway] http://127.0.0.1:${server.address().port}${PREFIX}/ from ${ROOT}`);
});
for (const sig of ['SIGINT', 'SIGTERM']) process.on(sig, () => server.close(() => process.exit(0)));
