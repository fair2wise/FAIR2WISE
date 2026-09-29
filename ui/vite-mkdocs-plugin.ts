import fs from 'node:fs'
import path from 'node:path'
import { spawnSync } from 'node:child_process'
import type { Connect, Plugin, PreviewServer, ViteDevServer } from 'vite'

const MIME: Record<string, string> = {
  '.css': 'text/css; charset=utf-8',
  '.html': 'text/html; charset=utf-8',
  '.ico': 'image/x-icon',
  '.jpg': 'image/jpeg',
  '.js': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.png': 'image/png',
  '.svg': 'image/svg+xml',
  '.txt': 'text/plain; charset=utf-8',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
}

const STUB_HTML = `<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>FAIR2WISE Documentation</title>
    <style>
      body { font-family: system-ui, sans-serif; margin: 2rem; color: #0f172a; }
      code { background: #f1f5f9; padding: 0.1em 0.35em; border-radius: 4px; }
    </style>
  </head>
  <body>
    <h1>FAIR2WISE Documentation</h1>
    <p>The MkDocs site is not built yet. From the repository root run:</p>
    <p><code>mkdocs build -f mkdocs/.mkdocs.ui.yml</code></p>
    <p>Vite will generate that config and retry the build the next time the UI starts.</p>
  </body>
</html>
`

function repoRootFromUi(uiDir: string) {
  return path.resolve(uiDir, '..')
}

function ensureUiMkdocsConfig(mkdocsDir: string) {
  const source = fs.readFileSync(path.join(mkdocsDir, 'mkdocs.yml'), 'utf8')
  const patched = source.replace(
    /^site_url:.*$/m,
    'site_url: http://127.0.0.1:5175/docs/',
  )
  const dest = path.join(mkdocsDir, '.mkdocs.ui.yml')
  fs.writeFileSync(dest, patched)
  return dest
}

function ensureMkdocsSite(uiDir: string) {
  const root = repoRootFromUi(uiDir)
  const mkdocsDir = path.join(root, 'mkdocs')
  const siteDir = path.join(mkdocsDir, 'site')
  const index = path.join(siteDir, 'index.html')
  if (fs.existsSync(index)) return siteDir

  const config = ensureUiMkdocsConfig(mkdocsDir)
  const result = spawnSync('mkdocs', ['build', '-f', config], {
    cwd: root,
    encoding: 'utf8',
  })
  if (result.status !== 0) {
    console.warn('[docs] mkdocs build failed; serving a stub page')
    if (result.stderr) console.warn(result.stderr.trim())
    fs.mkdirSync(siteDir, { recursive: true })
    fs.writeFileSync(index, STUB_HTML)
  }
  return siteDir
}

function docsMiddleware(siteDir: string): Connect.NextHandleFunction {
  const root = path.resolve(siteDir)
  return (req, res, next) => {
    const raw = req.url || ''
    if (!raw.startsWith('/docs')) return next()

    const url = new URL(raw, 'http://127.0.0.1')
    let rel = decodeURIComponent(url.pathname.slice('/docs'.length) || '/')
    if (!rel.startsWith('/')) rel = `/${rel}`
    if (rel === '/') rel = '/index.html'
    if (rel.endsWith('/')) rel += 'index.html'

    const file = path.resolve(root, `.${rel}`)
    if (file !== root && !file.startsWith(`${root}${path.sep}`)) {
      res.statusCode = 403
      res.end()
      return
    }

    let target = file
    if (fs.existsSync(target) && fs.statSync(target).isDirectory()) {
      target = path.join(target, 'index.html')
    }
    if (!fs.existsSync(target) || fs.statSync(target).isDirectory()) {
      res.statusCode = 404
      res.setHeader('Content-Type', 'text/html; charset=utf-8')
      res.end(STUB_HTML)
      return
    }

    const type = MIME[path.extname(target).toLowerCase()] || 'application/octet-stream'
    res.setHeader('Content-Type', type)
    fs.createReadStream(target).pipe(res)
  }
}

export function serveMkdocs(uiDir: string): Plugin {
  return {
    name: 'serve-mkdocs',
    configureServer(server: ViteDevServer) {
      const siteDir = ensureMkdocsSite(uiDir)
      server.middlewares.use(docsMiddleware(siteDir))
    },
    configurePreviewServer(server: PreviewServer) {
      const siteDir = ensureMkdocsSite(uiDir)
      server.middlewares.use(docsMiddleware(siteDir))
    },
  }
}
