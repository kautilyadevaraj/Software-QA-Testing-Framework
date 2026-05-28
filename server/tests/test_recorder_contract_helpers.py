import os

os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost:5432/test")
os.environ.setdefault("JWT_SECRET_KEY", "x" * 32)

from app.schemas.scenario import RecorderStepCreate
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
