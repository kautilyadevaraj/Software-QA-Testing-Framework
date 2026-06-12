"""Parametrized noise filter tests covering all known noise host patterns.

Verifies:
- Ad networks (googleads, doubleclick, pagead, adsystem, taboola, outbrain, analytics, tracking)
- Captcha/bot-check vendors (captcha, recaptcha, hcaptcha, security-check, bot-check, challenge)
- Cookie/consent platforms (cookiebot, onetrust, cookie-consent, trustarc)
- Legitimate same-domain clicks are NOT incorrectly flagged
"""

import os

os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost:5432/test")
os.environ.setdefault("JWT_SECRET_KEY", "x" * 32)

import pytest

from app.schemas.scenario import RecorderStepCreate
from app.services import recorder_service


SESSION_ORIGIN = "https://example.test"


@pytest.mark.parametrize("host", [
    "googleads.g.doubleclick.net",
    "pagead2.googlesyndication.com",
    "ad.doubleclick.net",
    "adsystem.google.com",
    "taboola.com",
    "outbrain.com",
    "analytics.google.com",
    "tracking.myapp.com",
])
def test_noise_ad_tracker_host(host: str) -> None:
    payload = RecorderStepCreate(
        step_index=0,
        action_type="click",
        selector="div",
        url_before=f"https://{host}/ads/click",
        url=f"https://{host}/ads/click",
    )
    is_noise, reason = recorder_service._is_noise_step(payload, SESSION_ORIGIN)
    assert is_noise is True, f"Expected noise for host: {host}"
    assert reason is not None


@pytest.mark.parametrize("url", [
    "https://www.google.com/recaptcha/api2/demo",
    "https://hcaptcha.com/challenge",
    "https://example.test/security-check",
    "https://example.test/bot-check/verify",
    "https://example.test/captcha",
])
def test_noise_captcha_urls(url: str) -> None:
    payload = RecorderStepCreate(
        step_index=1,
        action_type="click",
        selector="button",
        url_before=url,
        url=url,
    )
    is_noise, reason = recorder_service._is_noise_step(payload, SESSION_ORIGIN)
    assert is_noise is True, f"Expected noise for captcha URL: {url}"
    assert reason is not None


@pytest.mark.parametrize("host", [
    "consent.cookiebot.com",
    "cdn.onetrust.com",
    "cookie-consent.example.com",
    "trustarc.com",
])
def test_noise_consent_overlay_host(host: str) -> None:
    payload = RecorderStepCreate(
        step_index=2,
        action_type="click",
        selector="button",
        url_before=f"https://{host}/accept",
        url=f"https://{host}/accept",
    )
    is_noise, reason = recorder_service._is_noise_step(payload, SESSION_ORIGIN)
    assert is_noise is True, f"Expected noise for consent host: {host}"
    assert reason is not None


@pytest.mark.parametrize("url", [
    "https://example.test/login",
    "https://example.test/dashboard",
    "https://example.test/cart.html",
    "https://example.test/web/index.php/auth/login",
    "https://example.test/web/index.php/pim/search",
])
def test_legitimate_same_domain_not_noise(url: str) -> None:
    payload = RecorderStepCreate(
        step_index=3,
        action_type="click",
        selector='[data-test="some-btn"]',
        url_before=url,
        url=url,
    )
    is_noise, reason = recorder_service._is_noise_step(payload, SESSION_ORIGIN)
    assert is_noise is False, f"Incorrectly flagged as noise: {url}"
    assert reason is None


def test_none_url_not_noise() -> None:
    payload = RecorderStepCreate(
        step_index=4,
        action_type="click",
        selector='[data-test="btn"]',
        url_before=None,
        url=None,
    )
    is_noise, reason = recorder_service._is_noise_step(payload, SESSION_ORIGIN)
    assert is_noise is False
    assert reason is None


def test_normalize_payload_marks_ad_step_as_noise() -> None:
    """When _normalize_step_payload is called, noise fields are set on the payload."""
    payload = RecorderStepCreate(
        step_index=10,
        action_type="click",
        selector="div",
        url_before="https://doubleclick.net/ads/click",
        url="https://doubleclick.net/ads/click",
    )
    normalized = recorder_service._normalize_step_payload(payload, session_origin=SESSION_ORIGIN)
    assert normalized.is_noise is True
    assert normalized.noise_reason is not None


def test_normalize_payload_keeps_non_noise_step_clean() -> None:
    """Legitimate steps must not be marked as noise even after normalization."""
    payload = RecorderStepCreate(
        step_index=11,
        action_type="click",
        selector='[data-testid="login"]',
        url_before="https://example.test/login",
        url="https://example.test/login",
    )
    normalized = recorder_service._normalize_step_payload(payload, session_origin=SESSION_ORIGIN)
    assert normalized.is_noise is False
    assert normalized.noise_reason is None
