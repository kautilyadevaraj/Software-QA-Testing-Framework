import { defineConfig, devices } from "@playwright/test";

const headed = process.env.PLAYWRIGHT_HEADED === "true";
const slowMoMs = headed
  ? parseInt(process.env.PLAYWRIGHT_SLOW_MO_MS ?? "3000", 10)
  : 0;

// ── Timeout resolution ──────────────────────────────────────────────────────
// Per-test timeout (a single test() invocation). PLAYWRIGHT_TEST_TIMEOUT_MS is
// the new explicit setting; legacy TEST_TIMEOUT_MS is honored as a fallback so
// existing .env files keep working. Default 30 000 ms — tight enough that a
// hung selector in one test doesn't blow the whole suite's budget.
const PER_TEST_TIMEOUT_MS = parseInt(
  process.env.PLAYWRIGHT_TEST_TIMEOUT_MS
    ?? process.env.TEST_TIMEOUT_MS
    ?? "30000",
  10,
);

export default defineConfig({
  testDir: "./tests/generated",
  timeout: PER_TEST_TIMEOUT_MS,
  retries: 0,   // app uses its own Agent 7 retry — PW retries corrupt serial state
  workers: 1,
  reporter: "json",

  use: {
    headless: !headed,
    baseURL: process.env.BASE_URL ?? "http://localhost:3000",
    screenshot: "off",          // SQAT manages screenshots per outcome: assertion_screenshot.png (PASS) via spec injection; trace.zip (FAIL) via retain-on-failure.
    video: headed ? "on" : "off",
    // `on-first-retry` produces NOTHING when retries=0 (we disable Playwright
    // retries because A7 handles them at the agent level). `retain-on-failure`
    // keeps a trace for every failed test without needing a retry — the worker
    // reads the trace path from JSON reporter attachments and routes it to
    // the review queue UI.
    trace: "retain-on-failure",
    launchOptions: {
      slowMo: Number.isFinite(slowMoMs) ? slowMoMs : 3000,
    },
  },

  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
