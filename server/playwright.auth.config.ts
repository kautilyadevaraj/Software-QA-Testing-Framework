import { defineConfig, devices } from "@playwright/test";

const headed = process.env.PLAYWRIGHT_HEADED === "true";

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
