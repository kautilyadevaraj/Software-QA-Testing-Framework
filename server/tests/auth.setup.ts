/**
 * auth.setup.ts — runs ONCE before Phase 3 workers start.
 *
 * Logs in with the configured credentials and saves browser
 * cookies + localStorage to tests/auth.json.  Every subsequent
 * generated test loads that file via playwright.config.ts so it
 * starts in an already-authenticated state.
 *
 * Auth tests (login/logout/locked-out) override storageState in
 * their own test.use() call so they still start unauthenticated.
 */
import { test as setup } from "@playwright/test";

const AUTH_FILE = "tests/auth.json";

// Always start with a clean browser — no stored state from a
// previous run should affect the login attempt.
setup.use({ storageState: { cookies: [], origins: [] } });

setup("authenticate", async ({ page }) => {
  const base = process.env.BASE_URL ?? "http://localhost:3000";
  await page.goto(base);

  // ── Username / e-mail field ──────────────────────────────────
  const emailCandidates = [
    "#user-name",
    "input[name='username']",
    "input[name='email']",
    "input[type='email']",
    "#email",
    "#username",
  ];
  for (const sel of emailCandidates) {
    const el = page.locator(sel);
    if ((await el.count()) > 0) {
      await el.fill(process.env.USER_EMAIL ?? "");
      break;
    }
  }

  // ── Password field ───────────────────────────────────────────
  const passwordCandidates = [
    "#password",
    "input[type='password']",
    "input[name='password']",
  ];
  for (const sel of passwordCandidates) {
    const el = page.locator(sel);
    if ((await el.count()) > 0) {
      await el.fill(process.env.USER_PASSWORD ?? "");
      break;
    }
  }

  // ── Submit button ────────────────────────────────────────────
  const submitCandidates = [
    "#login-button",
    "button[type='submit']",
    "input[type='submit']",
    "button:has-text('Login')",
    "button:has-text('Sign in')",
    "button:has-text('Sign In')",
  ];
  for (const sel of submitCandidates) {
    const el = page.locator(sel);
    if ((await el.count()) > 0) {
      await el.click();
      break;
    }
  }

  // Wait for navigation to settle after login
  await page.waitForLoadState("networkidle", { timeout: 15000 }).catch(() => {});

  // Persist the authenticated session for all downstream tests
  await page.context().storageState({ path: AUTH_FILE });
});
