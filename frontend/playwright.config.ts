import { defineConfig } from '@playwright/test'
import path from 'node:path'

const reportDir = path.resolve(process.env.PLAYWRIGHT_REPORT_DIR || 'playwright-report')
const outputDir = path.resolve(process.env.PLAYWRIGHT_OUTPUT_DIR || 'test-results')

export default defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: [
    ['list'],
    ['html', { outputFolder: reportDir, open: 'never' }],
  ],
  outputDir,
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL || 'http://127.0.0.1:5173',
    trace: 'off',
    screenshot: 'only-on-failure',
    video: 'off',
    viewport: { width: 1440, height: 900 },
    ignoreHTTPSErrors: true,
    actionTimeout: 15_000,
    navigationTimeout: 30_000,
  },
  projects: [
    {
      name: 'acceptance',
      use: { browserName: 'chromium' },
    },
  ],
})
