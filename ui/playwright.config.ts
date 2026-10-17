import { defineConfig } from '@playwright/test';

// The API must already serve the index on :8120 (make up, or make serve). The Vite dev server proxies /api.
export default defineConfig({
  testDir: './e2e',
  timeout: 180_000,
  expect: { timeout: 20_000 },
  retries: 0,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: { baseURL: process.env['UI_BASE'] ?? 'http://localhost:5173', trace: 'retain-on-failure' },
  webServer: process.env['UI_BASE']
    ? undefined
    : { command: 'npx vite --port 5173 --strictPort', url: 'http://localhost:5173', reuseExistingServer: true, timeout: 120_000 },
});
