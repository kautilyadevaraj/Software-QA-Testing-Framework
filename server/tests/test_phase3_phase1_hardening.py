import uuid
from types import SimpleNamespace

from app.agents.agent3_planner import (
    _auth_mode_for_item,
    _deterministic_xray_metadata_from_chunks,
    _infer_auth_mode,
    _merge_xray_metadata_fallback,
    _valid_planned_items,
)
from app.agents.agent4_context_builder import _resolve_target_page
import app.agents.agent5_script_generator as agent5_script_generator
import app.agents.agent7_retry as agent7_retry
from app.agents.agent5_script_generator import _script_validation_errors
from app.agents.agent5_script_generator import _deterministic_test_block, _post_process_block
from app.agents.agent6_classifier import (
    _is_negative_intent,
    _looks_like_infra_error,
    _looks_repairable,
)
from app.services.phase3_jobs import build_single_test_job
from app.services.credential_service import normalize_auth_strategy, read_credential_rows


def _valid_network_tail() -> str:
    return """
  await testInfo.attach('network_logs', { body: JSON.stringify(monitor.failures, null, 2), contentType: 'application/json' });
  expect(monitor.failures, JSON.stringify(monitor.failures, null, 2)).toEqual([]);
"""


def test_a5_accepts_semantic_selector_instead_of_recorded_css_selector():
    context = {
        "steps": ["click the Submit button"],
        "recorded_steps": [
            {
                "action": "click",
                "selector": '[data-test="submit"]',
                "element_text": "Submit",
            }
        ],
        "acceptance_criteria": ["Submission confirmation is visible"],
    }
    code = f"""
test("Submit form", async ({{ page }}, testInfo) => {{
  const monitor = new NetworkMonitor(page);
  await page.getByRole('button', {{ name: 'Submit' }}).click();
  await expect(page.getByText('Submission confirmation')).toBeVisible();
{_valid_network_tail()}}});
"""

    errors = _script_validation_errors(code, context)

    assert not [error for error in errors if "missing approved step actions" in error]
    assert not [error for error in errors if "script missing required action types" in error]
    assert not [error for error in errors if "unexpected duplicate generated actions" in error]


def test_a5_exact_sequence_replay_validator_is_removed():
    assert not hasattr(agent5_script_generator, "_sequence_coverage_errors")
    assert not hasattr(agent5_script_generator, "_expected_action_sequence")


def test_a5_duplicate_check_rejects_clear_action_inflation():
    context = {
        "steps": ["click the Submit button"],
        "acceptance_criteria": ["Submission confirmation is visible"],
    }
    code = f"""
test("Submit form", async ({{ page }}, testInfo) => {{
  const monitor = new NetworkMonitor(page);
  await page.getByRole('button', {{ name: 'Submit' }}).click();
  await page.getByRole('button', {{ name: 'Submit' }}).click();
  await page.getByRole('button', {{ name: 'Submit' }}).click();
  await expect(page.getByText('Submission confirmation')).toBeVisible();
{_valid_network_tail()}}});
"""

    assert any(
        "unexpected duplicate generated actions" in error
        for error in _script_validation_errors(code, context)
    )


def test_a5_still_rejects_missing_required_action_type():
    context = {
        "steps": ["click the Submit button"],
        "acceptance_criteria": ["Submission confirmation is visible"],
    }
    code = f"""
test("Submit form", async ({{ page }}, testInfo) => {{
  const monitor = new NetworkMonitor(page);
  await expect(page.getByText('Submission confirmation')).toBeVisible();
{_valid_network_tail()}}});
"""

    assert any(
        "script missing required action types" in error
        for error in _script_validation_errors(code, context)
    )


def test_a5_treats_select_first_available_item_as_click_intent():
    context = {
        "steps": ["Select the first visible available item, without depending on its exact name or URL."],
        "acceptance_criteria": ["The selected item can be added to the cart."],
    }
    code = f"""
test("Select dynamic item", async ({{ page }}, testInfo) => {{
  const monitor = new NetworkMonitor(page);
  await page.getByRole('link').first().click();
  await expect(page.getByRole('button', {{ name: /add/i }})).toBeVisible();
{_valid_network_tail()}}});
"""

    errors = _script_validation_errors(code, context)

    assert not [error for error in errors if "select expected" in error]
    assert not [error for error in errors if "script missing required action types" in error]


def test_a5_numbered_steps_are_normalized_for_action_coverage():
    context = {
        "steps": [
            "3. Select the first visible available item, without depending on its exact name or URL.",
            "4. Add that item to the cart.",
            "5. Open the cart and start checkout.",
            "8. Fill the required checkout fields with valid test data.",
        ],
        "acceptance_criteria": ["The selected item can be added to the cart."],
    }
    code = f"""
test("Numbered checkout steps", async ({{ page }}, testInfo) => {{
  const monitor = new NetworkMonitor(page);
  await page.getByRole('link').first().click();
  await page.getByRole('button', {{ name: /add/i }}).click();
  await page.getByRole('link', {{ name: /cart/i }}).click();
  await page.getByLabel(/first name/i).fill('Test User');
  await expect(page.getByText(/cart/i)).toBeVisible();
{_valid_network_tail()}}});
"""

    errors = _script_validation_errors(code, context)

    assert not [error for error in errors if "select expected" in error]
    assert not [error for error in errors if "unexpected duplicate generated actions" in error]
    assert not [error for error in errors if "script missing required action types" in error]


def test_a5_deterministic_fallback_generates_inline_login_from_recording():
    context = {
        "title": "Authenticated checkout",
        "auth_mode": "authenticated",
        "auth_login_path": "/",
        "target_page": "/inventory.html",
        "steps": [
            '1. Login using the project credential profile for role "standard_user".',
            "2. Add that item to the cart.",
        ],
        "acceptance_criteria": ["Cart badge is visible."],
        "recorded_steps": [
            {"action": "fill", "selector": '[data-test="username"]', "input_type": "text"},
            {"action": "fill", "selector": '[data-test="password"]', "input_type": "password"},
            {"action": "submit", "selector": '[data-test="login-button"]', "element_text": "Login"},
            {"action": "submit", "selector": '[data-test="add-to-cart"]', "element_text": "Add to cart"},
        ],
    }

    script = _post_process_block(
        _deterministic_test_block(context, is_grouped=False),
        context["title"],
        is_grouped=False,
        auth_mode=context["auth_mode"],
        auth_login_path=context["auth_login_path"],
        target_page=context["auth_login_path"],
    )

    assert "storageState" not in script
    assert "await page.goto(env('BASE_URL') + '/');" in script
    assert "env('TEST_USERNAME')" in script
    assert "env('TEST_PASSWORD')" in script
    assert '[data-test="login-button"]' in script


def test_a5_still_treats_dropdown_selection_as_select_intent():
    context = {
        "steps": ["Select the Price option from the sort dropdown."],
        "acceptance_criteria": ["Products are sorted by price."],
    }
    code = f"""
test("Sort products", async ({{ page }}, testInfo) => {{
  const monitor = new NetworkMonitor(page);
  await page.getByRole('button', {{ name: /sort/i }}).click();
  await expect(page.getByText('Products')).toBeVisible();
{_valid_network_tail()}}});
"""

    assert any(
        "select expected" in error
        for error in _script_validation_errors(code, context)
    )


def test_a5_low_confidence_assertion_evidence_does_not_block_when_assertion_exists():
    context = {
        "steps": ["assert the confirmation is visible"],
        "acceptance_criteria": ["Confirmation is visible"],
        "assertion_evidence": [
            {
                "kind": "ui_text",
                "outcome": "confirmation visible",
                "confidence": 0.3,
                "grounding": "inferred",
            }
        ],
    }
    code = f"""
test("Confirmation", async ({{ page }}, testInfo) => {{
  const monitor = new NetworkMonitor(page);
  await expect(page.getByText('Confirmation')).toBeVisible();
{_valid_network_tail()}}});
"""

    assert "assertion evidence is low confidence; human review required" not in _script_validation_errors(code, context)


def test_a5_negative_validation_success_marker_is_scoped_to_expect_lines():
    context = {
        "title": "Required Field Validation",
        "steps": ["click the Continue button", "assert validation message is visible"],
        "acceptance_criteria": ["Missing required input is rejected"],
    }
    code = f"""
test("Required Field Validation", async ({{ page }}, testInfo) => {{
  const monitor = new NetworkMonitor(page);
  await page.getByRole('button', {{ name: 'Continue' }}).click();
  await page.waitForURL('**/success');
  await expect(page.getByText('Required')).toBeVisible();
{_valid_network_tail()}}});
"""

    assert "negative validation test asserts a success/completion outcome" not in _script_validation_errors(code, context)


class _FakeDb:
    def __init__(self, test_case):
        self.test_case = test_case

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def get(self, *_args):
        return self.test_case


class _FakeTestCase:
    title = "Save profile"
    steps = ["Fill all required fields"]
    acceptance_criteria = ["Profile details are saved successfully"]
    auth_mode = "authenticated"
    credential_role = "admin"


def test_a6_negative_intent_ignores_step_text(monkeypatch):
    import app.db.session as session_module

    monkeypatch.setattr(session_module, "SessionLocal", lambda: _FakeDb(_FakeTestCase()))

    assert _is_negative_intent(str(uuid.uuid4())) is False


def test_a6_repairable_regex_covers_modern_playwright_errors():
    assert _looks_repairable("page.waitForSelector: Timeout 30000ms exceeded")
    assert _looks_repairable("Execution context was destroyed, most likely because of a navigation")
    assert _looks_repairable("elementHandle.click: Element is detached from the DOM")
    assert _looks_repairable("Test timeout of 120000ms exceeded.")
    assert _looks_repairable("page.goto: net::ERR_ABORTED at https://example.test")


def test_a6_err_aborted_is_repairable_not_infra():
    error_log = "page.goto: net::ERR_ABORTED at https://example.test"

    assert _looks_like_infra_error(error_log) is False
    assert _looks_repairable(error_log) is True


def test_a3_authenticated_user_story_is_not_login_flow_setup():
    assert _infer_auth_mode(
        "As an authenticated user, I want to add any available item to the cart and complete checkout.",
        ['Login using the project credential profile for role "standard_user".'],
    ) == "authenticated"


def test_a3_explicit_auth_mode_wins_over_login_setup_step():
    item = {
        "title": "Complete checkout",
        "auth_mode": "authenticated",
        "steps": ['Login using the project credential profile for role "standard_user".'],
    }

    assert _auth_mode_for_item(item) == "authenticated"


def test_a3_real_login_validation_stays_login_flow():
    assert _infer_auth_mode(
        "Login rejects empty credentials",
        ["Open login page", "Click login without entering credentials"],
    ) == "login_flow"


def test_a3_authenticated_business_case_gets_inline_login_setup():
    items = [
        {
            "title": "Complete checkout",
            "auth_mode": "authenticated",
            "steps": ["add an available item to cart", "assert cart badge is visible"],
            "acceptance_criteria": ["Cart badge is visible"],
            "depends_on": [],
            "target_page": "/inventory.html",
        }
    ]

    valid, rejects = _valid_planned_items(
        items,
        ["/inventory.html"],
        "Checkout",
        "Authenticated user completes checkout",
    )

    assert rejects == 0
    assert valid[0]["auth_mode"] == "authenticated"
    assert valid[0]["steps"][0].startswith("login using the project credential profile")


def test_a3_rejects_unjustified_dynamic_recording_replay():
    items = [
        {
            "title": "Product Details",
            "auth_mode": "authenticated",
            "steps": ["navigate to /product_details/24", "assert product detail page is visible"],
            "acceptance_criteria": ["Product detail page is visible"],
            "depends_on": [],
            "target_page": "/inventory.html",
        }
    ]

    valid, rejects = _valid_planned_items(
        items,
        ["/inventory.html"],
        "Product details",
        "User can view a product details page",
        recorded_steps=[
            {
                "action_type": "click",
                "selector": 'a[href="/product_details/24"]',
                "url": "https://example.test/product_details/24",
            }
        ],
    )

    assert valid == []
    assert rejects == 1


def test_a4_authenticated_target_uses_first_post_auth_route():
    recorded_steps = [
        SimpleNamespace(
            url="https://example.test/login",
            url_before="https://example.test/login",
            url_after="https://example.test/login",
            selector="[name='username']",
            playwright_locator="page.getByLabel('Username')",
            element_text="",
            accessible_name="Username",
            role="textbox",
            label="Username",
            value="",
        ),
        SimpleNamespace(
            url="https://example.test/login",
            url_before="https://example.test/login",
            url_after="https://example.test/products",
            selector="[data-test='login-button']",
            playwright_locator="page.getByRole('button', { name: /Login/i })",
            element_text="Login",
            accessible_name="Login",
            role="button",
            label="",
            value="",
        ),
        SimpleNamespace(
            url="https://example.test/products",
            url_before="https://example.test/products",
            url_after="https://example.test/products",
            selector="[data-test='add-to-cart']",
            playwright_locator="page.getByRole('button', { name: /Add/i })",
            element_text="Add to cart",
            accessible_name="Add to cart",
            role="button",
            label="",
            value="",
        ),
    ]

    target, reason = _resolve_target_page("/", recorded_steps, "authenticated")

    assert target == "/products"
    assert "post-auth" in reason


def test_phase3_single_job_carries_plan_run_id():
    job = build_single_test_job(
        project_id=str(uuid.uuid4()),
        run_id=str(uuid.uuid4()),
        plan_run_id=str(uuid.uuid4()),
        test_id=str(uuid.uuid4()),
        script_path="generated/example.spec.ts",
        credential_id=str(uuid.uuid4()),
    )

    assert job["plan_run_id"]
    assert job["job_type"] == "single_test"


def test_phase3_run_spec_forwards_plan_run_id_to_env(monkeypatch, tmp_path):
    import app.services.phase3_worker as worker

    captured = {}

    def fake_build_env(*args, **kwargs):
        captured.update(kwargs)
        return {}

    class FakeProc:
        returncode = 0
        stdout = "{}"
        stderr = ""

    monkeypatch.setattr(worker, "_build_env", fake_build_env)
    monkeypatch.setattr(worker.subprocess, "run", lambda *args, **kwargs: FakeProc())

    script = tmp_path / "sample.spec.ts"
    script.write_text("test('x', async () => {})")

    worker._run_spec(
        script,
        str(uuid.uuid4()),
        test_id=str(uuid.uuid4()),
        credential_id=str(uuid.uuid4()),
        plan_run_id="plan-run-123",
    )

    assert captured["plan_run_id"] == "plan-run-123"


def test_phase3_grouped_missing_results_are_marked_human_review(monkeypatch, tmp_path):
    import app.services.phase3_worker as worker

    saved_results = []
    state_updates = []
    reviews = []

    script = tmp_path / "group.spec.ts"
    script.write_text("test('x', async () => {})")

    monkeypatch.setattr(
        worker,
        "_run_spec",
        lambda *args, **kwargs: {"exit_code": 1, "stderr": "compile failed", "report": {}},
    )
    monkeypatch.setattr(
        worker,
        "_walk_specs",
        lambda _report: iter([("first", "passed", "", [], None)]),
    )
    monkeypatch.setattr(
        worker.mcp_server,
        "save_test_result",
        lambda **kwargs: saved_results.append(kwargs),
    )
    monkeypatch.setattr(
        worker.mcp_server,
        "mark_complete",
        lambda _test_id: None,
    )
    monkeypatch.setattr(
        worker.state_store,
        "update_state",
        lambda *args, **kwargs: state_updates.append((args, kwargs)),
    )
    monkeypatch.setattr(
        worker,
        "_write_worker_review_queue",
        lambda *args, **kwargs: reviews.append((args, kwargs)),
    )

    missing_id = str(uuid.uuid4())
    worker._run_grouped_spec(
        hls_id=str(uuid.uuid4()),
        run_id=str(uuid.uuid4()),
        script_path=script,
        ordered_test_ids=[str(uuid.uuid4()), missing_id],
        plan_run_id=str(uuid.uuid4()),
    )

    assert any(result["test_id"] == missing_id and result["status"] == "HUMAN_REVIEW" for result in saved_results)
    assert any(update[0][0] == missing_id and update[0][1] == "HUMAN_REVIEW" for update in state_updates)
    assert reviews and reviews[0][1]["category"] == "GROUPED_RESULT_MISSING"


def test_phase4_a5_prompts_assert_business_outcomes_not_every_step():
    prompt_text = "\n".join([
        agent5_script_generator._SCRIPT_PROMPT,
        agent5_script_generator._GROUPED_TEST_BLOCK_PROMPT,
    ])

    assert "at least one expect() per meaningful step" not in prompt_text
    assert "prove acceptance criteria and important user-visible state changes" in prompt_text
    assert "do not assert every" in prompt_text


def test_phase4_a7_prompts_use_recorded_evidence_not_verbatim_replay():
    prompt_text = "\n".join([
        agent7_retry._REPAIR_PROMPT,
        agent7_retry._GROUPED_REPAIR_PROMPT,
    ])

    forbidden = [
        "Phase-2 ground truth",
        "prefer these verbatim",
        "Prefer RECORDED SELECTORS below verbatim",
        "Add to cart",
        "Thank you",
    ]

    for phrase in forbidden:
        assert phrase not in prompt_text
    assert "Phase-2 evidence" in prompt_text
    assert "Use recorded selectors below as evidence" in prompt_text


def test_phase4_a4_synthesized_few_shot_uses_current_network_evidence():
    from app.agents.agent4_context_builder import _synthesize_few_shot

    class _Scalars:
        def __init__(self, rows):
            self.rows = rows

        def scalars(self):
            return self

        def __iter__(self):
            return iter(self.rows)

    class _Db:
        def __init__(self, rows):
            self.rows = rows

        def execute(self, *_args, **_kwargs):
            return _Scalars(self.rows)

    rows = [
        SimpleNamespace(
            action_type="navigate",
            url="https://example.test/start",
            selector="",
            value="",
            input_value_kind="",
            input_type="",
            label="",
            element_text="",
            accessible_name="",
            semantic_context={},
        ),
        SimpleNamespace(
            action_type="click",
            url="",
            selector='[data-testid="submit"]',
            value="",
            input_value_kind="",
            input_type="",
            label="",
            element_text="Submit",
            accessible_name="Submit",
            semantic_context={},
        ),
    ]

    few_shot = _synthesize_few_shot(uuid.uuid4(), _Db(rows))

    assert few_shot is not None
    assert "testInfo" in few_shot
    assert "monitor.hasFailures()" not in few_shot
    assert "monitor.failures" in few_shot


def test_phase5_auth_strategy_normalization_defaults_to_inline_login():
    assert normalize_auth_strategy("") == "inline_login"
    assert normalize_auth_strategy("form login") == "inline_login"
    assert normalize_auth_strategy("storage") == "storage_state"
    assert normalize_auth_strategy("", auth_type="storage_state") == "storage_state"
    assert normalize_auth_strategy("unknown") == "inline_login"


def test_phase5_credential_csv_reads_auth_strategy_and_script(tmp_path):
    csv_path = tmp_path / "credentials.csv"
    csv_path.write_text(
        "username,password,role,auth_strategy,auth_script,endpoint\n"
        "admin@example.test,secret,admin,storage_state,admin.auth.setup.ts,https://app.example.test\n",
        encoding="utf-8",
    )

    rows = read_credential_rows(csv_path)

    assert rows == [
        {
            "username": "admin@example.test",
            "password": "secret",
            "role": "admin",
            "auth_type": "",
            "auth_strategy": "storage_state",
            "auth_script": "admin.auth.setup.ts",
            "endpoint": "https://app.example.test",
        }
    ]


def test_phase5_agent7_retry_attempts_come_from_settings(monkeypatch):
    monkeypatch.setattr(agent7_retry.settings, "phase3_agent_retry_attempts", 5)

    assert agent7_retry._max_retry_attempts() == 5


def test_phase5_storage_state_is_explicit_strategy():
    from app.services.auth_state_service import profile_requires_storage_state

    inline_profile = SimpleNamespace(auth_strategy="inline_login", auth_type="")
    storage_profile = SimpleNamespace(auth_strategy="storage_state", auth_type="")
    legacy_profile = SimpleNamespace(auth_strategy="", auth_type="storage_state")

    assert profile_requires_storage_state(inline_profile) is False
    assert profile_requires_storage_state(storage_profile) is True
    assert profile_requires_storage_state(legacy_profile) is True


def test_phase6_a5_valid_fill_values_are_configurable(monkeypatch):
    monkeypatch.setattr(agent5_script_generator.settings, "phase3_test_data_name", "Alex QA")
    monkeypatch.setattr(agent5_script_generator.settings, "phase3_test_data_postal_code", "SW1A 1AA")
    monkeypatch.setattr(agent5_script_generator.settings, "phase3_test_data_phone", "+15551234567")
    monkeypatch.setattr(agent5_script_generator.settings, "phase3_test_data_search", "reference")
    monkeypatch.setattr(agent5_script_generator.settings, "phase3_test_data_email", "qa@example.test")

    assert agent5_script_generator._valid_runtime_value_for_fill("a valid name", "#name", "fill name") == "'Alex QA'"
    assert agent5_script_generator._valid_runtime_value_for_fill("a valid code", "#postal", "fill postal code") == "'SW1A 1AA'"
    assert agent5_script_generator._valid_runtime_value_for_fill("a valid phone", "#phone", "fill phone") == "'+15551234567'"
    assert agent5_script_generator._valid_runtime_value_for_fill("a valid email", "#email", "fill email") == "'qa@example.test'"
    assert agent5_script_generator._valid_runtime_value_for_fill("a valid search", "#search", "fill search") == "'reference'"


def test_phase6_semantic_tokens_do_not_expand_retail_terms():
    tokens = agent5_script_generator._semantic_tokens("click the cart link")

    assert "cart" in tokens
    assert "basket" not in tokens
    assert "bag" not in tokens


def test_llm_provider_chain_is_anthropic_and_groq_only(monkeypatch):
    import app.utils.llm as llm

    monkeypatch.setattr(llm.settings, "llm_provider", "anthropic")
    monkeypatch.setattr(llm.settings, "llm_fallback_chain", "anthropic,groq,gemini,nim")

    assert llm._provider_chain() == ["anthropic", "groq"]

    llm._PROVIDER_FUNCS.clear()
    llm._register_providers()

    assert set(llm._PROVIDER_FUNCS) == {"anthropic", "groq"}


def test_xray_requirement_fallback_fills_all_cases_from_single_brd_requirement():
    chunks = [
        {
            "category": "brd",
            "text": (
                "FR-001 The application shall allow authenticated shoppers to "
                "login, browse products, manage cart contents, and complete checkout."
            ),
        }
    ]
    tc_rows = [
        {
            "title": "Standard user login lands on inventory page",
            "steps": ["login with valid credentials"],
            "acceptance_criteria": ["inventory is visible"],
        },
        {
            "title": "Complete full checkout flow to confirmation",
            "steps": ["add product to cart", "complete checkout"],
            "acceptance_criteria": ["confirmation is shown"],
        },
    ]

    metadata = _deterministic_xray_metadata_from_chunks(chunks, tc_rows)

    assert metadata["standard user login lands on inventory page"]["requirement"] == "FR-001"
    assert metadata["complete full checkout flow to confirmation"]["requirement"] == "FR-001"


def test_xray_requirement_fallback_only_replaces_missing_requirement():
    primary = {
        "login": {"requirement": "TBD", "labels": "Positive,Functional"},
        "checkout": {"requirement": "FR-999", "labels": "Regression"},
    }
    fallback = {
        "login": {"requirement": "FR-001", "priority": "High"},
        "checkout": {"requirement": "FR-002", "priority": "Medium"},
    }

    merged = _merge_xray_metadata_fallback(primary, fallback)

    assert merged["login"]["requirement"] == "FR-001"
    assert merged["checkout"]["requirement"] == "FR-999"


def test_xray_requirement_fallback_overrides_wrong_llm_requirement_when_brd_match_is_strong():
    primary = {
        "remove product from cart updates cart state": {"requirement": "FR-007", "labels": "Positive,Functional"},
    }
    fallback = {
        "remove product from cart updates cart state": {
            "requirement": "FR-008",
            "_requirement_score": "54",
        },
    }

    merged = _merge_xray_metadata_fallback(primary, fallback)

    assert merged["remove product from cart updates cart state"]["requirement"] == "FR-008"


def test_xray_requirement_mapper_uses_primary_intent_not_setup_steps():
    chunks = [
        {
            "category": "brd",
            "text": """
4. Functional Requirements
ID
Requirement
Acceptance Criteria
FR-006
Product detail page is reachable from inventory.
Opening a product displays its detail page and Back to products returns to inventory.
FR-007
Adding a product updates cart state.
Add to Cart changes to Remove and cart badge increments.
FR-008
Removing a product updates cart state.
Remove changes product state back and cart badge/count updates.
FR-011
Checkout customer information is required.
First Name, Last Name, and Zip/Postal Code are required before continuing.
FR-014
Checkout completion confirms the order.
Finish navigates to checkout complete and shows a confirmation message.
FR-015
Logout ends the session.
Logout returns to login page and login controls are visible.
5. Required QA Test Coverage
Remove product from cart should not be swallowed into the previous requirement.
""",
        }
    ]
    tc_rows = [
        {
            "title": "Remove product from cart updates cart state",
            "steps": ["login", "add product to cart as setup", "remove product from cart"],
            "acceptance_criteria": ["removed item no longer appears"],
        },
        {
            "title": "Checkout required fields validation prevents continuation",
            "steps": ["start checkout", "leave first name last name and postal code empty"],
            "acceptance_criteria": ["required field validation prevents continuing"],
        },
        {
            "title": "Back to products returns to inventory page",
            "steps": ["open product detail", "click Back to products"],
            "acceptance_criteria": ["inventory page is shown"],
        },
        {
            "title": "Logout returns user to login page",
            "steps": ["open menu", "click logout"],
            "acceptance_criteria": ["login controls are visible"],
        },
    ]

    metadata = _deterministic_xray_metadata_from_chunks(chunks, tc_rows)

    assert metadata["remove product from cart updates cart state"]["requirement"] == "FR-008"
    assert metadata["checkout required fields validation prevents continuation"]["requirement"] == "FR-011"
    assert metadata["back to products returns to inventory page"]["requirement"] == "FR-006"
    assert metadata["logout returns user to login page"]["requirement"] == "FR-015"


def test_llm_anthropic_call_parses_text(monkeypatch):
    import app.utils.llm as llm

    captured = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"content": [{"type": "text", "text": "generated script"}]}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return FakeResponse()

    monkeypatch.setattr(llm.settings, "anthropic_api_key", "test-key")
    monkeypatch.setattr(llm.settings, "anthropic_model", "claude-sonnet-4-6")
    monkeypatch.setattr(llm.requests, "post", fake_post)

    assert llm._call_anthropic("prompt", max_tokens=123) == "generated script"
    assert captured["json"]["model"] == "claude-sonnet-4-6"
    assert captured["json"]["max_tokens"] == 123
    assert captured["headers"]["x-api-key"] == "test-key"
