import { cloudflareTest } from '@cloudflare/vitest-pool-workers';
import { defineConfig } from 'vitest/config';

// Tests run inside workerd (the Workers runtime) with an in-memory KV.
// wrangler.toml isn't loaded: its assets directory (web/dist) and KV id
// needn't exist for unit tests, so the bindings are declared here instead.
export default defineConfig({
  plugins: [
    cloudflareTest({
      main: './src/index.js',
      miniflare: {
        compatibilityDate: '2025-11-01',
        kvNamespaces: ['SHOW_KV'],
        bindings: {
          INGEST_KEY: 'test-ingest-key-0123456789abcdef',
          ADMIN_URL: 'https://lights.example.com/admin',
        },
      },
    }),
  ],
});
