from app.agents.agent5_script_generator import (
    _post_process_block,
    _script_validation_errors,
)


def test_post_process_removes_networkidle_waits_generically():
    code = """
test("Submit form", async ({ page }, testInfo) => {
  const monitor = new NetworkMonitor(page);
  await page.locator('[data-test="submit"]').click();
  await page.waitForLoadState('networkidle');
  await expect(page.locator('[data-test="result"]')).toBeVisible();
  await testInfo.attach('network_logs', { body: JSON.stringify(monitor.failures, null, 2), contentType: 'application/json' });
  expect(monitor.failures, JSON.stringify(monitor.failures, null, 2)).toEqual([]);
});
"""

    patched = _post_process_block(code, "Submit form", is_grouped=False)

    assert "waitForLoadState('networkidle')" not in patched
    assert "toBeVisible()" in patched


def test_post_process_adds_initial_navigation_for_single_test_scripts():
    code = """
test("Catalog action", async ({ page }, testInfo) => {
  const monitor = new NetworkMonitor(page);
  await page.locator('[data-test="item-link"]').click();
  await expect(page.locator('[data-test="details"]')).toBeVisible();
  await testInfo.attach('network_logs', { body: JSON.stringify(monitor.failures, null, 2), contentType: 'application/json' });
  expect(monitor.failures, JSON.stringify(monitor.failures, null, 2)).toEqual([]);
});
"""

    patched = _post_process_block(
        code,
        "Catalog action",
        is_grouped=False,
        target_page="/inventory.html",
    )

    assert "await page.goto(env('BASE_URL') + '/inventory.html');" in patched
    assert patched.index("new NetworkMonitor(page)") < patched.index("page.goto")
    assert patched.index("page.goto") < patched.index("page.locator")


def test_post_process_removes_storage_state_use():
    code = """
test.use({ storageState: { cookies: [], origins: [] } });

test("Login", async ({ page }, testInfo) => {
  const monitor = new NetworkMonitor(page);
  await page.locator('[data-test="username"]').fill(env('TEST_USERNAME'));
  await page.locator('[data-test="password"]').fill(env('TEST_PASSWORD'));
  await expect(page.locator('[data-test="login-button"]')).toBeVisible();
  await testInfo.attach('network_logs', { body: JSON.stringify(monitor.failures, null, 2), contentType: 'application/json' });
  expect(monitor.failures, JSON.stringify(monitor.failures, null, 2)).toEqual([]);
});
"""

    patched = _post_process_block(code, "Login", is_grouped=False, target_page="/")

    assert "storageState" not in patched
    assert "await page.goto(env('BASE_URL') + '/');" in patched


def test_post_process_uses_test_credential_env_names_for_raw_credential_fills():
    code = """
test("Login", async ({ page }, testInfo) => {
  const monitor = new NetworkMonitor(page);
  await page.locator('[data-test="username"]').fill('valid-email@example.test');
  await page.locator('[data-test="password"]').fill('password');
  await expect(page.locator('[data-test="login-button"]')).toBeVisible();
  await testInfo.attach('network_logs', { body: JSON.stringify(monitor.failures, null, 2), contentType: 'application/json' });
  expect(monitor.failures, JSON.stringify(monitor.failures, null, 2)).toEqual([]);
});
"""

    patched = _post_process_block(code, "Login", is_grouped=False, target_page="/")

    assert "env('TEST_USERNAME')" in patched
    assert "env('TEST_PASSWORD')" in patched
    assert "USER_EMAIL" not in patched
    assert "USER_PASSWORD" not in patched


def test_validation_rejects_networkidle_if_it_survives_post_process():
    code = """
test("Submit form", async ({ page }, testInfo) => {
  const monitor = new NetworkMonitor(page);
  await page.locator('[data-test="submit"]').click();
  await page.waitForLoadState('networkidle');
  await expect(page.locator('[data-test="result"]')).toBeVisible();
  await testInfo.attach('network_logs', { body: JSON.stringify(monitor.failures, null, 2), contentType: 'application/json' });
  expect(monitor.failures, JSON.stringify(monitor.failures, null, 2)).toEqual([]);
});
"""

    assert (
        "networkidle wait; wait for concrete URL or UI outcome"
        in _script_validation_errors(code, {})
    )


def test_post_process_rewrites_uppercase_textcontent_contains_to_case_insensitive_locator_assertion():
    code = """
test("Completion", async ({ page }, testInfo) => {
  const monitor = new NetworkMonitor(page);
  await page.waitForURL('**/complete');
  const message = await page.locator('[data-test="complete-message"]').textContent();
  expect(message).toContain('THANK YOU FOR YOUR ORDER');
  await testInfo.attach('network_logs', { body: JSON.stringify(monitor.failures, null, 2), contentType: 'application/json' });
  expect(monitor.failures, JSON.stringify(monitor.failures, null, 2)).toEqual([]);
});
"""

    patched = _post_process_block(code, "Completion", is_grouped=False)

    assert "textContent()" not in patched
    assert (
        "await expect(page.locator('[data-test=\"complete-message\"]')).toContainText(/THANK\\ YOU\\ FOR\\ YOUR\\ ORDER/i);"
        in patched
    )


def test_post_process_keeps_non_uppercase_textcontent_assertion_unchanged():
    code = """
test("Status copy", async ({ page }, testInfo) => {
  const monitor = new NetworkMonitor(page);
  const message = await page.locator('[data-test="status"]').textContent();
  expect(message).toContain('Ready for pickup');
  await testInfo.attach('network_logs', { body: JSON.stringify(monitor.failures, null, 2), contentType: 'application/json' });
  expect(monitor.failures, JSON.stringify(monitor.failures, null, 2)).toEqual([]);
});
"""

    patched = _post_process_block(code, "Status copy", is_grouped=False)

    assert "textContent()" in patched
    assert "toContain('Ready for pickup')" in patched
