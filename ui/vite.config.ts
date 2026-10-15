import { defineConfig, loadEnv } from 'vite'
import type { Plugin } from 'vite'
import path from 'path'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { serveMkdocs } from './vite-mkdocs-plugin'


function figmaAssetResolver() {
  return {
    name: 'figma-asset-resolver',
    resolveId(id) {
      if (id.startsWith('figma:asset/')) {
        const filename = id.replace('figma:asset/', '')
        return path.resolve(__dirname, 'src/assets', filename)
      }
    },
  }
}

/**
 * Public URL prefix (T24C, D26). Mirrors app/modules/deployment/gateway.py:
 * F2W_PUBLIC_PREFIX unset or "/" keeps the unprefixed developer layout
 * (base "/", API http://127.0.0.1:8090, docs /docs/). With a prefix such as
 * /fair2wise the build uses base /fair2wise/, same-origin API /fair2wise/api and
 * docs /fair2wise/docs/, and a cross-origin or differently prefixed API URL is
 * refused so a bundle cannot silently disagree with the gateway.
 */
const SEGMENT = /^[A-Za-z0-9][A-Za-z0-9._-]*$/

export function normalizePrefix(raw: string | undefined): string {
  const value = (raw ?? '').trim()
  if (value === '' || value === '/') return ''
  if (!value.startsWith('/')) {
    throw new Error(`F2W_PUBLIC_PREFIX must be a rooted path like /fair2wise, got ${JSON.stringify(raw)}`)
  }
  const trimmed = value.replace(/\/+$/, '')
  for (const segment of trimmed.slice(1).split('/')) {
    if (!SEGMENT.test(segment)) {
      throw new Error(`F2W_PUBLIC_PREFIX has an invalid path segment ${JSON.stringify(segment)}`)
    }
  }
  return trimmed
}

export interface PublicLayout {
  public_prefix: string
  ui_base: string
  api_base: string
  docs_url: string
}

export function publicLayout(env: Record<string, string | undefined>): PublicLayout {
  const prefix = normalizePrefix(env.F2W_PUBLIC_PREFIX)
  if (!prefix) {
    return {
      public_prefix: '',
      ui_base: '/',
      api_base: '', // not same-origin: liveAgent.ts uses VITE_F2W_AGENT_API_URL or its default
      docs_url: '/docs/',
    }
  }
  const sameOriginApi = `${prefix}/api`
  const api = (env.VITE_F2W_AGENT_API_URL || sameOriginApi).replace(/\/$/, '')
  if (api !== sameOriginApi) {
    throw new Error(
      `VITE_F2W_AGENT_API_URL=${api} disagrees with F2W_PUBLIC_PREFIX=${prefix}: ` +
        `prefixed builds use the same-origin ${sameOriginApi} (unset it)`,
    )
  }
  const docs = (env.VITE_F2W_DOCS_URL || `${prefix}/docs/`).replace(/\/?$/, '/')
  if (!docs.startsWith(`${prefix}/`)) {
    throw new Error(`VITE_F2W_DOCS_URL=${docs} is outside F2W_PUBLIC_PREFIX=${prefix}`)
  }
  return { public_prefix: prefix, ui_base: `${prefix}/`, api_base: api, docs_url: docs }
}

/** Write dist/f2w-build.json so the image can refuse a runtime prefix the bundle was not built for. */
function buildManifest(layout: PublicLayout): Plugin {
  return {
    name: 'f2w-build-manifest',
    apply: 'build',
    generateBundle() {
      this.emitFile({
        type: 'asset',
        fileName: 'f2w-build.json',
        source: `${JSON.stringify(layout, null, 2)}\n`,
      })
    },
  }
}

export default defineConfig(({ mode }) => {
  const env = { ...loadEnv(mode, __dirname, 'VITE_'), ...process.env }
  const layout = publicLayout(env)
  // Only prefixed builds pin the browser settings; unprefixed builds keep the
  // existing VITE_F2W_AGENT_API_URL / VITE_F2W_DOCS_URL behaviour (and defaults).
  const define = layout.public_prefix
    ? {
        'import.meta.env.VITE_F2W_AGENT_API_URL': JSON.stringify(layout.api_base),
        'import.meta.env.VITE_F2W_DOCS_URL': JSON.stringify(layout.docs_url),
      }
    : {}

  return {
    base: layout.ui_base,
    define,
    plugins: [
      figmaAssetResolver(),
      serveMkdocs(__dirname),
      // The React and Tailwind plugins are both required for Make, even if
      // Tailwind is not being actively used – do not remove them
      react(),
      tailwindcss(),
      buildManifest(layout),
    ],
    resolve: {
      alias: {
        // Alias @ to the src directory
        '@': path.resolve(__dirname, './src'),
      },
    },

    // File types to support raw imports. Never add .css, .tsx, or .ts files to this.
    assetsInclude: ['**/*.svg', '**/*.csv'],
  }
})
