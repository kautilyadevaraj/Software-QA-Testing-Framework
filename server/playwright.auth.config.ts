import { defineConfig, devices } from "@playwright/test";

const headed = process.env.PLAYWRIGHT_HEADED === "true";
const slowMoMs = headed
  ? parseInt(process.env.PLAYWRIGHT_SLOW_MO_MS ?? "3000", 10)
  : 0;

export default defineConfig({
  testDir: "./tests",
  testMatch: ["auth.setup.ts"],
  timeout: 90000,
  retries: 0,
  workers: 1,
  reporter: "dot",

  use: {
    headless: !headed,
    baseURL: process.env.BASE_URL ?? "http://localhost:3000",
    screenshot: "only-on-failure",
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
