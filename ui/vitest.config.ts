import path from 'path';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  // Automatic JSX runtime so .tsx tests and components need no React import.
  esbuild: { jsx: 'automatic' },
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.{ts,tsx}'],
    // Finch imports react-router itself; inline it so it shares our router context.
    server: { deps: { inline: ['@blueskyproject/finch'] } },
    setupFiles: ['./src/test/setup.ts'],
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
});
