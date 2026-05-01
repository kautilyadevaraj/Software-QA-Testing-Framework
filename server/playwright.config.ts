import { defineConfig, devices } from "@playwright/test";
import { existsSync } from "fs";

const headed = process.env.PLAYWRIGHT_HEADED === "true";
const AUTH_FILE = "tests/auth.json";

export default defineConfig({
  testDir: "./tests/generated",
  timeout: parseInt(process.env.TEST_TIMEOUT_MS ?? "180000", 10),
  retries: 1,   // allows trace capture on first retry
  workers: 1,
  reporter: "json",

  use: {
    headless: !headed,
    baseURL: process.env.BASE_URL ?? "http://localhost:3000",
    storageState: existsSync(AUTH_FILE) ? AUTH_FILE : undefined,
    screenshot: "only-on-failure",
    video: headed ? "on" : "off",
    trace: "on-first-retry",   // worker reads trace path from JSON reporter attachments
    launchOptions: {
      slowMo: headed ? 500 : 0,
    },
  },

  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
