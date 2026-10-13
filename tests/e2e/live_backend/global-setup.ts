/**
 * T18B live-backend setup: start the real FastAPI app (fake inference), build
 * the frozen UI once against it (config-only: VITE_F2W_AGENT_API_URL), and
 * serve the built dist on 127.0.0.1. No route mocking anywhere.
 *
 * Inputs (env): F2W_LIVE_PYTHON (venv python), F2W_LIVE_ARTIFACTS (run dir,
 * outside the repo), optional F2W_RUNTIME (passed to the backend only),
 * optional F2W_LIVE_UI_PORT (default 5175; it must be a default CORS origin).
 */
import { spawn, spawnSync, type ChildProcess } from 'node:child_process';
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';

const REPO = path.resolve(__dirname, '..', '..', '..');
const UI_DIR = path.join(REPO, 'ui');

export interface LiveRun {
  apiPort: number;
  apiBase: string;
  uiBase: string;
  stateDir: string;
  workspace: string;
  runtime: string;
  artifacts: string;
}

export function runFile(artifacts: string) {
  return path.join(artifacts, 'run.json');
}

function required(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`${name} must be set for the live-backend suite`);
  return value;
}

async function waitFor(file: string, child: ChildProcess, timeoutMs: number) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    if (fs.existsSync(file)) return JSON.parse(fs.readFileSync(file, 'utf8'));
    if (child.exitCode !== null) throw new Error(`backend exited early (${child.exitCode})`);
    await new Promise(resolve => setTimeout(resolve, 200));
  }
  throw new Error(`timed out waiting for ${file}`);
}

const MIME: Record<string, string> = {
  '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8', '.svg': 'image/svg+xml', '.png': 'image/png',
  '.json': 'application/json', '.woff2': 'font/woff2', '.ico': 'image/x-icon', '.csv': 'text/csv',
};

function serveDist(dist: string, port: number): Promise<http.Server> {
  const root = path.resolve(dist);
  const server = http.createServer((req, res) => {
    const url = new URL(req.url || '/', 'http://127.0.0.1');
    let file = path.resolve(root, `.${decodeURIComponent(url.pathname)}`);
    if (!file.startsWith(root)) { res.statusCode = 403; res.end(); return; }
    if (!fs.existsSync(file) || fs.statSync(file).isDirectory()) file = path.join(root, 'index.html');
    res.setHeader('Content-Type', MIME[path.extname(file)] || 'application/octet-stream');
    fs.createReadStream(file).pipe(res);
  });
  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(port, '127.0.0.1', () => resolve(server));
  });
}

export default async function globalSetup() {
  const python = required('F2W_LIVE_PYTHON');
  const artifacts = path.resolve(required('F2W_LIVE_ARTIFACTS'));
  const runtime = process.env.F2W_RUNTIME || '';
  const uiPort = Number.parseInt(process.env.F2W_LIVE_UI_PORT || '5175', 10);
  fs.rmSync(path.join(artifacts, 'backend'), { recursive: true, force: true });
  fs.mkdirSync(path.join(artifacts, 'backend', 'home'), { recursive: true });
  fs.mkdirSync(path.join(artifacts, 'backend', 'tmp'), { recursive: true });

  const ready = path.join(artifacts, 'backend', 'ready.json');
  const backendEnv: Record<string, string> = {
    PATH: `${path.dirname(python)}:/usr/bin:/bin`,
    HOME: path.join(artifacts, 'backend', 'home'),
    TMPDIR: path.join(artifacts, 'backend', 'tmp'),
    LANG: 'en_US.UTF-8',
  };
  if (runtime) backendEnv.F2W_RUNTIME = runtime;
  const log = fs.openSync(path.join(artifacts, 'backend', 'server.log'), 'w');
  const backend = spawn(python, [
    path.join(REPO, 'tests/contracts/legacy_ui/runtime/launch_live_backend.py'),
    '--state-dir', path.join(artifacts, 'backend', 'state'),
    '--ready-file', ready,
  ], { cwd: REPO, env: backendEnv, stdio: ['ignore', log, log] });
  const info = await waitFor(ready, backend, 90_000);
  const apiBase = `http://127.0.0.1:${info.port}`;

  // Build the frozen UI once, configuration only, into the artifact dir.
  const dist = path.join(artifacts, 'dist');
  const build = spawnSync(process.execPath, [
    path.join(UI_DIR, 'node_modules/vite/bin/vite.js'), 'build', '--outDir', dist, '--emptyOutDir', '--logLevel', 'warn',
  ], {
    cwd: UI_DIR,
    env: { PATH: process.env.PATH || '', HOME: backendEnv.HOME, VITE_F2W_AGENT_API_URL: apiBase },
    encoding: 'utf8',
  });
  fs.writeFileSync(path.join(artifacts, 'vite-build.log'), `${build.stdout}\n${build.stderr}`);
  if (build.status !== 0) {
    backend.kill('SIGTERM');
    throw new Error(`vite build failed (${build.status}); see ${artifacts}/vite-build.log`);
  }
  const bundle = fs.readdirSync(path.join(dist, 'assets')).filter(f => f.endsWith('.js'))
    .map(f => fs.readFileSync(path.join(dist, 'assets', f), 'utf8')).join('\n');
  if (!bundle.includes(apiBase)) throw new Error('built bundle does not point at the live backend');

  const server = await serveDist(dist, uiPort);
  const run: LiveRun = {
    apiPort: info.port, apiBase, uiBase: `http://127.0.0.1:${uiPort}`,
    stateDir: info.state_dir, workspace: info.workspace, runtime: info.runtime, artifacts,
  };
  fs.writeFileSync(runFile(artifacts), JSON.stringify(run, null, 2));

  return async () => {
    await new Promise(resolve => server.close(resolve));
    backend.kill('SIGTERM');
    await new Promise(resolve => setTimeout(resolve, 500));
    if (backend.exitCode === null) backend.kill('SIGKILL');
  };
}
