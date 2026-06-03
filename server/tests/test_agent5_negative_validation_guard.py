from app.agents.agent5_script_generator import _script_validation_errors


def test_required_field_validation_cannot_assert_success_completion():
    context = {
        "title": "Required Checkout Fields Validation",
        "steps": [
            "leave one or more required fields empty",
            "submit the Continue button",
            "assert validation message is visible",
        ],
        "acceptance_criteria": [
            "Missing required input is rejected",
            "Clear validation feedback is shown",
        ],
    }
    code = """
test("Required Checkout Fields Validation", async ({ page }, testInfo) => {
  const monitor = new NetworkMonitor(page);
  await page.locator('[data-test="firstName"]').fill('xyz');
  await page.locator('[data-test="lastName"]').fill('xyz');
  await page.locator('[data-test="postalCode"]').fill('560080');
  await page.locator('[data-test="continue"]').click();
  await page.locator('[data-test="finish"]').click();
  await expect(page.locator('[data-test="complete-header"]')).toContainText('Thank you for your order!');
  await testInfo.attach('network_logs', { body: JSON.stringify(monitor.failures, null, 2), contentType: 'application/json' });
  expect(monitor.failures, JSON.stringify(monitor.failures, null, 2)).toEqual([]);
});
"""

    assert "negative validation test asserts a success/completion outcome" in _script_validation_errors(code, context)


def test_required_field_validation_accepts_error_feedback_assertion():
    context = {
        "title": "Required Checkout Fields Validation",
        "steps": [
            "leave one or more required fields empty",
            "submit the Continue button",
            "assert validation message is visible",
        ],
        "acceptance_criteria": ["Clear validation feedback is shown"],
    }
    code = """
test("Required Checkout Fields Validation", async ({ page }, testInfo) => {
  const monitor = new NetworkMonitor(page);
  await page.locator('[data-test="continue"]').click();
  await expect(page.locator('[data-test="error"]')).toContainText('required');
  await testInfo.attach('network_logs', { body: JSON.stringify(monitor.failures, null, 2), contentType: 'application/json' });
  expect(monitor.failures, JSON.stringify(monitor.failures, null, 2)).toEqual([]);
});
"""

    assert "negative validation test asserts a success/completion outcome" not in _script_validation_errors(code, context)
