import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright config for the browser smoke tests.
 *
 * These are DELIBERATELY separate from the vitest suite (`npm test`). vitest runs under
 * happy-dom, which cannot execute igv.js's canvas renderer — so the component tests MOCK
 * igv and only cover the pure coordinate/config adapters. This Playwright project drives a
 * REAL Chromium against the standalone `/showcase.html` page (no backend, no network: the
 * guide viewer renders the submitted sequence as an inline-FASTA reference), which is the
 * only way to verify that igv.js actually paints the guide track in a browser.
 *
 * One-time setup: `npx playwright install chromium`. Run with `npm run test:e2e`.
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? "dot" : "list",
  use: {
    baseURL: "http://localhost:5173",
    trace: "on-first-retry",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  // Boot the Vite dev server for the duration of the run (reused if already up).
  webServer: {
    command: "npm run dev",
    url: "http://localhost:5173/showcase.html",
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
