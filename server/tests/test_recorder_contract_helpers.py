import os

os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost:5432/test")
os.environ.setdefault("JWT_SECRET_KEY", "x" * 32)

from app.schemas.scenario import RecorderStepCreate
from app.models.scenario import ScenarioStep
from app.agents import agent4_context_builder, agent5_script_generator
from app.services import recorder_service


def test_transition_type_detects_url_change() -> None:
    payload = RecorderStepCreate(
        step_index=0,
        action_type="click",
        selector='[data-test="shopping-cart-link"]',
        url_before="https://example.test/inventory.html",
        url_after="https://example.test/cart.html",
    )

    transition_type, confidence = recorder_service._transition_type(payload, None, None)

    assert transition_type == "url_change"
    assert confidence == 1.0


def test_transition_type_detects_dom_change_with_snapshot_ids() -> None:
    payload = RecorderStepCreate(
        step_index=1,
        action_type="click",
        selector='[data-test="add-to-cart"]',
        url_before="https://example.test/inventory.html",
        url_after="https://example.test/inventory.html",
    )

    transition_type, confidence = recorder_service._transition_type(
        payload,
        "00000000-0000-0000-0000-000000000001",
        "00000000-0000-0000-0000-000000000002",
    )

    assert transition_type == "dom_change"
    assert confidence == 0.9


def test_input_value_kind_marks_password_as_credential() -> None:
    payload = RecorderStepCreate(
        step_index=2,
        action_type="fill",
        selector='[data-test="password"]',
        value=None,
        input_type="password",
    )

    assert recorder_service._classify_input_value(payload) == "empty"

    payload = payload.model_copy(update={"value": "secret"})

    assert recorder_service._classify_input_value(payload) == "credential"


def test_normalize_fill_does_not_own_later_navigation() -> None:
    payload = RecorderStepCreate(
        step_index=1,
        action_type="fill",
        selector='[data-test="password"]',
        input_type="password",
        role="textbox",
        url_before="https://example.test/login",
        url_after="https://example.test/dashboard",
        caused_navigation=True,
        semantic_context={
            "navigation": {
                "from": "https://example.test/login",
                "to": "https://example.test/dashboard",
                "caused_navigation": True,
            },
            "page": {
                "url_before": "https://example.test/login",
                "url_after": "https://example.test/dashboard",
            },
        },
    )

    normalized = recorder_service._normalize_step_payload(payload)

    assert normalized.url_after == "https://example.test/login"
    assert normalized.caused_navigation is False
    assert normalized.semantic_context["navigation"]["caused_navigation"] is False
    assert normalized.semantic_context["navigation"]["to"] is None


def test_normalize_keeps_submit_navigation() -> None:
    payload = RecorderStepCreate(
        step_index=2,
        action_type="submit",
        selector='[data-test="login-button"]',
        role="button",
        input_type="submit",
        url_before="https://example.test/login",
        url_after="https://example.test/dashboard",
        caused_navigation=True,
    )

    normalized = recorder_service._normalize_step_payload(payload)

    assert normalized.url_after == "https://example.test/dashboard"
    assert normalized.caused_navigation is True


def test_normalize_child_click_uses_actionable_parent_selector() -> None:
    payload = RecorderStepCreate(
        step_index=4,
        action_type="click",
        selector='[data-test="shopping-cart-badge"]',
        selector_candidates=['[data-test="shopping-cart-badge"]'],
        element_type="span",
        role="span",
        element_text="1",
        accessible_name="1",
        playwright_locator="page.getByRole('span', { name: /1/i })",
        url_before="https://example.test/inventory.html",
        url_after="https://example.test/cart.html",
        caused_navigation=True,
        semantic_context={
            "parent_context": {
                "tag": "a",
                "role": "a",
                "text": "1",
                "label": "1",
                "selector": '[data-test="shopping-cart-link"]',
            }
        },
    )

    normalized = recorder_service._normalize_step_payload(payload)

    assert normalized.selector == '[data-test="shopping-cart-link"]'
    assert normalized.selector_candidates[0] == '[data-test="shopping-cart-link"]'
    assert normalized.element_type == "a"
    assert normalized.role == "link"
    assert normalized.playwright_locator == "page.getByRole('link', { name: /1/i })"


def test_normalize_tag_roles_to_playwright_roles() -> None:
    link_payload = RecorderStepCreate(
        step_index=0,
        action_type="click",
        selector="a[href='/cart']",
        element_type="a",
        role="a",
        playwright_locator="page.getByRole('a', { name: /Cart/i })",
    )
    input_payload = RecorderStepCreate(
        step_index=1,
        action_type="fill",
        selector="input[name='email']",
        element_type="input",
        role="input",
        input_type="email",
        playwright_locator="page.getByRole('input', { name: /Email/i })",
    )

    normalized_link = recorder_service._normalize_step_payload(link_payload)
    normalized_input = recorder_service._normalize_step_payload(input_payload)

    assert normalized_link.role == "link"
    assert normalized_link.playwright_locator == "page.getByRole('link', { name: /Cart/i })"
    assert normalized_input.role == "textbox"
    assert normalized_input.playwright_locator == "page.getByRole('textbox', { name: /Email/i })"


def test_phase3_derives_start_path_from_first_recorded_url_before() -> None:
    steps = [
        ScenarioStep(
            step_index=0,
            action_type="fill",
            selector='[data-test="username"]',
            url_before="https://example.test/login",
            url_after="https://example.test/login",
        )
    ]

    target, reason = agent4_context_builder._resolve_target_page("/dashboard", steps)

    assert target == "/login"
    assert "trusting recording" in reason


def test_phase3_few_shot_renders_recorded_submit_as_click() -> None:
    step = ScenarioStep(
        step_index=2,
        action_type="submit",
        selector='[data-test="continue"]',
        element_type="input",
        input_type="submit",
    )

    rendered = agent4_context_builder._render_few_shot_step(step)

    assert rendered == "  await page.locator('[data-test=\"continue\"]').click();"


def test_phase3_matches_recorded_submit_for_click_intent() -> None:
    selector, index = agent5_script_generator._recorded_selector_for_action(
        [
            {
                "action": "submit",
                "selector": '[data-test="checkout"]',
                "accessible_name": "Checkout",
                "element_type": "button",
            }
        ],
        "click",
        set(),
        "click the Checkout button",
    )

    assert selector == '[data-test="checkout"]'
    assert index == 0
