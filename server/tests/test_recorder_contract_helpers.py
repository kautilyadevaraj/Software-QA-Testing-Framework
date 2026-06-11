import os

os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost:5432/test")
os.environ.setdefault("JWT_SECRET_KEY", "x" * 32)

from app.schemas.scenario import RecorderStepCreate
from app.models.scenario import ScenarioStep
from app.agents import agent3_planner, agent4_context_builder, agent5_script_generator
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


# ── Phase 2 quality tests ──────────────────────────────────────────────────

def test_phase3_a4_filters_noise_steps_before_context() -> None:
    steps = [
        ScenarioStep(
            step_index=0,
            action_type="click",
            selector='[data-test="real-action"]',
            is_noise=False,
        ),
        ScenarioStep(
            step_index=1,
            action_type="click",
            selector="div#ad-close",
            is_noise=True,
            noise_reason="ad_or_tracker_domain:googleads.g.doubleclick.net",
        ),
    ]

    clean = agent4_context_builder._non_noise_steps(steps)

    assert [s.step_index for s in clean] == [0]


def test_phase3_a4_serializes_selector_quality_and_field_identity() -> None:
    step = ScenarioStep(
        step_index=3,
        action_type="fill",
        selector='input[placeholder="Type for hints..."]',
        selector_candidates=['input[placeholder="Type for hints..."]'],
        selector_quality_reason="placeholder",
        semantic_context={
            "field_identity": {
                "route_path": "/web/index.php/pim/viewEmployeeList",
                "field_label": "Employee Name",
                "placeholder": "Type for hints...",
            }
        },
    )

    payload = agent4_context_builder._serialize_recorded_steps([step])

    assert payload[0]["selector_quality_reason"] == "placeholder"
    assert payload[0]["field_identity"]["route_path"] == "/web/index.php/pim/viewEmployeeList"
    assert payload[0]["field_identity"]["field_label"] == "Employee Name"


def test_phase3_a4_adds_abstract_selector_and_intent_hints() -> None:
    step = ScenarioStep(
        step_index=4,
        action_type="click",
        selector='a[href="/product_details/24"]',
        element_type="a",
    )

    payload = agent4_context_builder._serialize_recorded_steps([step])

    assert payload[0]["selector_hint"] == 'a[href*="/product_details/"]'
    assert payload[0]["intent_hint"] == "product details link"


def test_phase3_a4_builds_dynamic_route_patterns() -> None:
    patterns = agent4_context_builder._build_route_patterns({
        "/product_details/24": "View Product",
        "/cart": "Cart",
    })

    assert patterns == {"/product_details/{id}": "View Product"}


def test_phase3_a4_few_shot_abstracts_dynamic_selector_and_value() -> None:
    step = ScenarioStep(
        step_index=5,
        action_type="fill",
        selector='input[name="quantity"]',
        value="2",
    )

    rendered = agent4_context_builder._render_few_shot_step(step)

    assert rendered == "  await page.locator('input[name=\"quantity\"]').fill('1');"


def test_phase3_a4_few_shot_does_not_replay_specific_product_detail_id() -> None:
    step = ScenarioStep(
        step_index=6,
        action_type="click",
        selector='a[href="/product_details/2"]',
        element_type="a",
    )

    rendered = agent4_context_builder._render_few_shot_step(step)

    assert rendered == '  await page.locator(\'a[href*="/product_details/"]\').click();'
    assert "/product_details/2" not in rendered


def test_phase3_a4_test_id_detection_ignores_noise_steps() -> None:
    steps = [
        ScenarioStep(
            step_index=0,
            action_type="click",
            selector='[data-test="login-button"]',
            is_noise=False,
        ),
        ScenarioStep(
            step_index=1,
            action_type="click",
            selector='[data-cy="ad-close"]',
            is_noise=True,
        ),
        ScenarioStep(
            step_index=2,
            action_type="click",
            selector='[data-cy="tracker-close"]',
            is_noise=True,
        ),
    ]

    assert agent4_context_builder._detect_test_id_attribute(steps) == "data-test"


def test_phase3_a3_warns_on_recorded_specific_generic_route_leakage() -> None:
    item = {
        "title": "Product Details",
        "steps": ["click product 24 details link", "navigate to /product_details/24"],
        "acceptance_criteria": ["Product detail page is visible"],
    }
    warnings = agent3_planner._recording_leakage_warnings(
        item,
        [{"selector": 'a[href="/product_details/24"]', "url": "https://example.test/product_details/24"}],
        "User can view a product details page",
    )

    assert "recorded-specific route '/product_details/24'" in warnings


def test_phase3_a3_preserves_explicit_recorded_route_when_requirement_names_it() -> None:
    item = {
        "title": "Specific Product Details",
        "steps": ["navigate to /product_details/24"],
        "acceptance_criteria": ["Specific product detail page is visible"],
    }
    warnings = agent3_planner._recording_leakage_warnings(
        item,
        [{"selector": 'a[href="/product_details/24"]', "url": "https://example.test/product_details/24"}],
        "Requirement: verify /product_details/24 opens successfully",
    )

    assert warnings == []


def test_phase3_a5_uses_selector_hint_for_generic_business_object() -> None:
    selector, index = agent5_script_generator._recorded_selector_for_action(
        [
            {
                "action": "click",
                "selector": 'a[href="/product_details/24"]',
                "selector_hint": 'a[href*="/product_details/"]',
                "element_type": "a",
            }
        ],
        "click",
        set(),
        "click a product details link",
    )

    assert selector == 'a[href*="/product_details/"]'
    assert index == 0


def test_phase3_a5_keeps_stable_control_selector() -> None:
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


def test_phase3_a5_uses_configured_valid_fill_values(monkeypatch) -> None:
    monkeypatch.setattr(agent5_script_generator.settings, "phase3_test_data_name", "QA Person")
    monkeypatch.setattr(agent5_script_generator.settings, "phase3_test_data_postal_code", "560001")

    assert agent5_script_generator._valid_runtime_value_for_fill("a valid name", "#first-name", "fill first name with a valid name") == "'QA Person'"
    assert agent5_script_generator._valid_runtime_value_for_fill("a valid code", "#postal-code", "fill postal code with a valid code") == "'560001'"
    assert agent5_script_generator._valid_runtime_value_for_fill("a valid quantity", "#quantity", "fill quantity with a valid quantity") == "'1'"


def test_phase3_a5_does_not_match_add_record_to_records_link() -> None:
    context = {
        "dom": {
            "interactive_elements": [
                {"selector": 'a[href="/records"]', "text": "Records", "role": "a", "tag": "a"},
                {"selector": 'button.add-record', "text": "Add record", "role": "button", "tag": "button"},
            ]
        },
        "route_snapshots": {},
    }

    selector = agent5_script_generator._element_selector_for_step(
        context,
        "click",
        "click the Add record button",
    )

    assert selector == "button.add-record"


def test_phase3_a5_deterministic_fallback_uses_grounded_assertion_not_body() -> None:
    context = {
        "title": "Guest User Product Search",
        "steps": ["assert the search results page displays a list of matching products"],
        "recorded_steps": [
            {
                "action": "click",
                "selector": 'a[href="/product_details/2"]',
                "selector_hint": 'a[href*="/product_details/"]',
            }
        ],
        "recording_flow": {"assertion_candidates_by_snapshot": {}},
        "dom": {"interactive_elements": []},
    }

    lines = agent5_script_generator._deterministic_lines_from_steps(context, page_var="page")

    assert "page.locator('body')" not in "\n".join(lines)
    assert "a[href*=\"/product_details/\"]" in "\n".join(lines)


def test_phase3_a5_inserts_recorded_navigation_bridge_before_later_control() -> None:
    context = {
        "title": "Add Item to Cart",
        "steps": ["click the Add to Cart button"],
        "recorded_steps": [
            {
                "action": "click",
                "selector": 'a[href="/product_details/2"]',
                "selector_hint": 'a[href*="/product_details/"]',
                "element_text": "View Product",
                "url": "https://example.test/products",
                "from_url": "https://example.test/products",
                "to_url": "https://example.test/product_details/2",
            },
            {
                "action": "click",
                "selector": "button.cart",
                "element_text": "Add to cart",
                "url": "https://example.test/product_details/2",
                "from_url": "https://example.test/product_details/2",
                "to_url": "https://example.test/product_details/2",
            },
        ],
        "recording_flow": {"assertion_candidates_by_snapshot": {}},
        "dom": {"interactive_elements": []},
    }

    lines = agent5_script_generator._deterministic_lines_from_steps(context, page_var="page")
    text = "\n".join(lines)

    assert "a[href*=\"/product_details/\"]" in text
    assert "button.cart" in text


def test_noise_ad_domain_marked_as_noise() -> None:
    payload = RecorderStepCreate(
        step_index=5,
        action_type="click",
        selector='iframe[src]',
        url_before="https://googleads.g.doubleclick.net/pagead/ads",
        url="https://googleads.g.doubleclick.net/pagead/ads",
    )
    is_noise, reason = recorder_service._is_noise_step(payload, "https://example.test")
    assert is_noise is True
    assert reason is not None


def test_noise_ad_iframe_with_advertisement_label_marked_as_noise() -> None:
    payload = RecorderStepCreate(
        step_index=5,
        action_type="click",
        selector="iframe",
        element_type="iframe",
        accessible_name="Advertisement",
        url_before="https://automationexercise.com/product_details/2",
        url="https://automationexercise.com/product_details/2",
    )

    is_noise, reason = recorder_service._is_noise_step(payload, "https://automationexercise.com")

    assert is_noise is True
    assert reason == "ad_iframe_element:advertisement"


def test_noise_captcha_url_marked_as_noise() -> None:
    payload = RecorderStepCreate(
        step_index=6,
        action_type="click",
        selector="div",
        url_before="https://example.test/recaptcha/challenge",
        url="https://example.test/recaptcha/challenge",
    )
    is_noise, reason = recorder_service._is_noise_step(payload, "https://example.test")
    assert is_noise is True
    assert reason is not None


def test_noise_consent_overlay_marked_as_noise() -> None:
    payload = RecorderStepCreate(
        step_index=7,
        action_type="click",
        selector="button",
        url_before="https://consent.cookiebot.com/accept",
        url="https://consent.cookiebot.com/accept",
    )
    is_noise, reason = recorder_service._is_noise_step(payload, "https://example.test")
    assert is_noise is True
    assert reason is not None


def test_legitimate_same_domain_click_not_noise() -> None:
    payload = RecorderStepCreate(
        step_index=8,
        action_type="click",
        selector='[data-test="login-button"]',
        url_before="https://example.test/login",
        url="https://example.test/login",
    )
    is_noise, reason = recorder_service._is_noise_step(payload, "https://example.test")
    assert is_noise is False
    assert reason is None


def test_selector_quality_reason_data_testid() -> None:
    reason = recorder_service._selector_quality_reason('[data-testid="login-btn"]')
    assert reason == "data_attr"


def test_selector_quality_reason_data_test() -> None:
    reason = recorder_service._selector_quality_reason('[data-test="submit"]')
    assert reason == "data_attr"


def test_selector_quality_reason_structural_fallback() -> None:
    reason = recorder_service._selector_quality_reason("div:nth-of-type(3) > span")
    assert reason == "structural_fallback"


def test_selector_quality_reason_stable_id() -> None:
    reason = recorder_service._selector_quality_reason("#login-form")
    assert reason == "stable_id"


def test_field_identity_login_vs_search() -> None:
    """Same placeholder on two different routes should produce different field_identity.route_path."""
    login_payload = RecorderStepCreate(
        step_index=0,
        action_type="fill",
        selector='input[placeholder="Username"]',
        url_before="https://example.test/web/index.php/auth/login",
    )
    search_payload = RecorderStepCreate(
        step_index=5,
        action_type="fill",
        selector='input[placeholder="Username"]',
        url_before="https://example.test/web/index.php/pim/search",
    )
    login_identity = recorder_service._build_field_identity(login_payload)
    search_identity = recorder_service._build_field_identity(search_payload)
    assert login_identity is not None
    assert search_identity is not None
    assert login_identity["route_path"] != search_identity["route_path"]


def test_field_identity_no_form_action_still_valid() -> None:
    """SPA forms with no form_action should still produce a valid field_identity."""
    payload = RecorderStepCreate(
        step_index=1,
        action_type="fill",
        selector='input[placeholder="Email"]',
        url_before="https://spa.example.test/login",
        semantic_context={},
    )
    identity = recorder_service._build_field_identity(payload)
    assert identity is not None
    assert identity["route_path"] is not None
    # form_action should be None (absent) — this is valid per spec
    assert identity.get("form_action") is None


def test_phase3_ready_false_for_all_noise() -> None:
    """A flow with all noise steps should compute phase3_ready=False."""
    quality = {
        "total_steps": 5,
        "stable_selector_count": 3,
        "structural_selector_count": 0,
        "noise_step_count": 5,  # all noise
        "assertion_candidate_count": 2,
        "blocked_by_security": False,
    }
    total = quality["total_steps"]
    noise_ratio = quality["noise_step_count"] / total if total > 0 else 0
    phase3_ready = (
        total >= 3
        and quality["stable_selector_count"] / total >= 0.5
        and noise_ratio <= 0.3
        and not quality["blocked_by_security"]
        and quality["assertion_candidate_count"] >= 1
    )
    assert phase3_ready is False


def test_phase3_ready_false_for_only_structural_selectors() -> None:
    """A flow with all low-stability selectors should compute phase3_ready=False."""
    quality = {
        "total_steps": 5,
        "stable_selector_count": 0,  # none stable
        "noise_step_count": 0,
        "assertion_candidate_count": 2,
        "blocked_by_security": False,
    }
    total = quality["total_steps"]
    phase3_ready = (
        total >= 3
        and quality["stable_selector_count"] / total >= 0.5
        and quality["noise_step_count"] / total <= 0.3
        and not quality["blocked_by_security"]
        and quality["assertion_candidate_count"] >= 1
    )
    assert phase3_ready is False
