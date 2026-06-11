from types import SimpleNamespace

from app.agents.agent4_context_builder import _first_authenticated_route_path
from app.agents.agent5_script_generator import (
    _deterministic_test_block,
    _post_process_block,
    _script_validation_errors,
)
from app.agents.agent7_retry import _extract_repaired_test_block


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


def test_validation_rejects_llm_explanation_before_test_block():
    code = """
The recorded selectors show that this should click the details link first.

test("Back to list", async ({ page }, testInfo) => {
  const monitor = new NetworkMonitor(page);
  await page.goto(env('BASE_URL') + '/items');
  await page.locator('[data-test="item-link"]').click();
  await expect(page.locator('[data-test="back"]')).toBeVisible();
  await testInfo.attach('network_logs', { body: JSON.stringify(monitor.failures, null, 2), contentType: 'application/json' });
  expect(monitor.failures, JSON.stringify(monitor.failures, null, 2)).toEqual([]);
});
"""

    assert "non-code text before Playwright test() block" in _script_validation_errors(
        code,
        {"target_page": "/items"},
    )


def test_validation_rejects_route_map_as_direct_workflow_jumps():
    context = {
        "target_page": "/",
        "steps": [
            "navigate to {BASE_URL}/",
            "fill [data-test='username'] with {TEST_USERNAME}",
            "fill [data-test='password'] with {TEST_PASSWORD}",
            "click [data-test='login-button']",
            "click [data-test='cart-link'] to navigate to /cart",
            "assert cart page lists the selected item",
        ],
    }
    code = """
test("Cart flow", async ({ page }, testInfo) => {
  const monitor = new NetworkMonitor(page);
  await page.goto(env('BASE_URL') + '/');
  await page.locator('[data-test="username"]').fill(env('TEST_USERNAME'));
  await page.locator('[data-test="password"]').fill(env('TEST_PASSWORD'));
  await page.locator('[data-test="login-button"]').click();
  await page.goto(env('BASE_URL') + '/cart');
  await expect(page.locator('[data-test="cart-item"]')).toBeVisible();
  await testInfo.attach('network_logs', { body: JSON.stringify(monitor.failures, null, 2), contentType: 'application/json' });
  expect(monitor.failures, JSON.stringify(monitor.failures, null, 2)).toEqual([]);
});
"""

    errors = _script_validation_errors(code, context)

    assert any(error.startswith("direct route jumps after initial navigation") for error in errors)


def test_validation_allows_explicit_direct_navigation_step():
    context = {
        "target_page": "/reports",
        "steps": [
            "navigate directly to {BASE_URL}/reports",
            "assert report list is visible",
        ],
    }
    code = """
test("Direct reports access", async ({ page }, testInfo) => {
  const monitor = new NetworkMonitor(page);
  await page.goto(env('BASE_URL') + '/reports');
  await expect(page.locator('[data-test="report-list"]')).toBeVisible();
  await testInfo.attach('network_logs', { body: JSON.stringify(monitor.failures, null, 2), contentType: 'application/json' });
  expect(monitor.failures, JSON.stringify(monitor.failures, null, 2)).toEqual([]);
});
"""

    errors = _script_validation_errors(code, context)

    assert not any("goto" in error or "route jumps" in error for error in errors)


def test_deterministic_fallback_uses_recorded_transition_for_workflow_route():
    context = {
        "title": "Open record details",
        "target_page": "/records",
        "steps": [
            "navigate to {BASE_URL}/records",
            "click the first record to navigate to /records/123",
            "assert record details panel is visible",
        ],
        "recorded_steps": [
            {
                "step_index": 1,
                "action": "click",
                "selector": "a[href='/records/123']",
                "selector_hint": "a[href*=\"/records/\"]",
                "element_text": "Record 123",
                "from_url": "https://example.test/records",
                "to_url": "https://example.test/records/123",
            }
        ],
        "recorded_route_transitions": [
            {
                "step_index": 1,
                "action_type": "click",
                "selector": "a[href='/records/123']",
                "element_text": "Record 123",
                "from_path": "/records",
                "to_path": "/records/123",
                "confidence": 1.0,
            }
        ],
        "assertion_evidence": [
            {
                "kind": "element_visible",
                "confidence": 0.9,
                "observable_hint": "[data-test='record-details']",
                "outcome": "record details panel is visible",
            }
        ],
    }

    block = _deterministic_test_block(context, is_grouped=False)

    assert "page.goto(env('BASE_URL') + '/records/123')" not in block
    assert "page.locator('a[href*=\"/records/\"]').click()" in block
    assert "page.waitForURL('**/records/123**')" in block
    assert _script_validation_errors(block, context) == []


def test_deterministic_fallback_keeps_empty_field_empty_in_validation_flow():
    context = {
        "title": "Missing required field validation",
        "target_page": "/login",
        "steps": [
            "navigate to {BASE_URL}/login",
            "fill [data-test=\"username\"] with {TEST_USERNAME}",
            "leave [data-test='username'] empty",
            "click [data-test='submit']",
            "assert validation feedback is visible for missing [data-test='username']",
        ],
        "recorded_steps": [
            {"step_index": 1, "action": "fill", "selector": "[data-test='username']"},
            {"step_index": 2, "action": "click", "selector": "[data-test='submit']"},
        ],
        "assertion_evidence": [
            {
                "kind": "error_message",
                "confidence": 0.95,
                "observable_hint": "[data-test='error']",
                "outcome": "validation error is visible for missing username",
            }
        ],
    }

    block = _deterministic_test_block(context, is_grouped=False)

    assert "fill(env('TEST_USERNAME'))" not in block
    assert "page.locator('[data-test=\\'username\\']').fill('')" in block
    assert "page.locator('[data-test=\\'submit\\']').click()" in block
    assert "page.locator('[data-test=\\'error\\']')" in block
    assert _script_validation_errors(block, context) == []


def test_deterministic_fallback_prefers_remove_control_over_item_text_link():
    context = {
        "title": "Remove product from cart and verify cart updates",
        "target_page": "/cart",
        "steps": [
            "navigate to {BASE_URL}/cart",
            "click the Remove control for Bolt T-Shirt",
            "assert the empty cart message is visible",
        ],
        "recorded_steps": [
            {
                "step_index": 1,
                "action": "click",
                "selector": "[data-test='item-0-title-link']",
                "element_text": "Bolt T-Shirt",
                "element_type": "a",
            },
            {
                "step_index": 2,
                "action": "click",
                "selector": "[data-test='remove-bolt-t-shirt']",
                "element_text": "Remove",
                "accessible_name": "Remove Bolt T-Shirt",
                "element_type": "button",
            },
        ],
        "assertion_evidence": [
            {
                "kind": "element_visible",
                "confidence": 0.9,
                "observable_hint": "[data-test='empty-cart-message']",
                "outcome": "empty cart message is visible",
            }
        ],
    }

    block = _deterministic_test_block(context, is_grouped=False)

    assert "page.locator('[data-test=\\'remove-bolt-t-shirt\\']').click()" in block
    assert "item-0-title-link" not in block
    assert _script_validation_errors(block, context) == []


def test_post_process_rewrites_duplicate_get_by_text_assertion_to_grounded_selector():
    context = {
        "recording_flow": {
            "assertion_candidates_by_snapshot": {
                "/cart": [
                    {
                        "selector": "[data-test='item-name']",
                        "text": "Bolt T-Shirt",
                        "kind": "stable_ui_text",
                        "confidence": 0.9,
                    },
                    {
                        "selector": "[data-test='item-description']",
                        "text": "Bolt T-Shirt is a lightweight cotton shirt",
                        "kind": "stable_ui_text",
                        "confidence": 0.9,
                    },
                ]
            }
        }
    }
    code = """
test("Cart content", async ({ page }, testInfo) => {
  const monitor = new NetworkMonitor(page);
  await expect(page.getByText('Bolt T-Shirt')).toBeVisible();
  await testInfo.attach('network_logs', { body: JSON.stringify(monitor.failures, null, 2), contentType: 'application/json' });
  expect(monitor.failures, JSON.stringify(monitor.failures, null, 2)).toEqual([]);
});
"""

    patched = _post_process_block(code, "Cart content", is_grouped=False, context=context)

    assert "getByText('Bolt T-Shirt')" not in patched
    assert "page.locator('[data-test=\\'item-name\\']').first()" in patched
    assert _script_validation_errors(patched, context) == []


def test_validation_rejects_ambiguous_get_by_text_assertion_without_unique_selector():
    context = {
        "recording_flow": {
            "assertion_candidates_by_snapshot": {
                "/settings": [
                    {"selector": "[data-test='primary-save']", "text": "Save", "kind": "stable_ui_text"},
                    {"selector": "[data-test='footer-save']", "text": "Save", "kind": "stable_ui_text"},
                ]
            }
        }
    }
    code = """
test("Save controls", async ({ page }, testInfo) => {
  const monitor = new NetworkMonitor(page);
  await expect(page.getByText('Save')).toBeVisible();
  await testInfo.attach('network_logs', { body: JSON.stringify(monitor.failures, null, 2), contentType: 'application/json' });
  expect(monitor.failures, JSON.stringify(monitor.failures, null, 2)).toEqual([]);
});
"""

    errors = _script_validation_errors(code, context)

    assert any(error.startswith("ambiguous getByText assertion") for error in errors)


def test_agent7_extracts_test_block_from_llm_explanation():
    raw = """
The repaired script should use the recorded selector.

test("Cart content", async ({ page }, testInfo) => {
  const monitor = new NetworkMonitor(page);
  await expect(page.locator('[data-test="cart-item"]')).toBeVisible();
  await testInfo.attach('network_logs', { body: JSON.stringify(monitor.failures, null, 2), contentType: 'application/json' });
  expect(monitor.failures, JSON.stringify(monitor.failures, null, 2)).toEqual([]);
});
"""

    extracted = _extract_repaired_test_block(raw)

    assert extracted.startswith('test("Cart content"')
    assert "The repaired script" not in extracted


def test_authenticated_start_prefers_first_business_action_source_route():
    steps = [
        SimpleNamespace(
            selector="[data-test='username']",
            playwright_locator="",
            element_text="",
            accessible_name="",
            role="",
            label="",
            value="",
            url="https://example.test/",
            url_before="https://example.test/",
            url_after="https://example.test/details/123",
        ),
        SimpleNamespace(
            selector="[data-test='record-link']",
            playwright_locator="",
            element_text="Record 123",
            accessible_name="",
            role="link",
            label="",
            value="",
            url="https://example.test/records",
            url_before="https://example.test/records",
            url_after="https://example.test/details/123",
        ),
    ]

    assert _first_authenticated_route_path(steps) == "/records"
