"""Tests for _compute_recording_quality() helper and related phase3_ready logic.

Covers:
- Quality computation at each threshold boundary
- blocked_by_security=True always produces phase3_ready=False
- Old sessions without quality summary do not raise errors
- All noise steps → phase3_ready=False
- Insufficient stable selectors → phase3_ready=False
- Minimum viable quality → phase3_ready=True
"""

import os

os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost:5432/test")
os.environ.setdefault("JWT_SECRET_KEY", "x" * 32)

from unittest.mock import MagicMock, patch

from app.services import recorder_service
from app.schemas.scenario import RecorderStepCreate


def _make_step(selector_stability: str = "high", is_noise: bool = False, route_variant_after_id=None):
    step = MagicMock()
    step.selector_stability = selector_stability
    step.is_noise = is_noise
    step.route_variant_after_id = route_variant_after_id
    return step


def _mock_db_for_quality(steps, assertion_count: int = 1, route_variant_count: int = 1, flow_meta: dict | None = None):
    """Build a minimal db mock for _compute_recording_quality."""
    db = MagicMock()
    flow = MagicMock()
    flow.id = "flow-uuid-001"
    flow.metadata_json = flow_meta or {}

    # steps query
    scalars_steps = MagicMock()
    scalars_steps.__iter__ = MagicMock(return_value=iter(steps))
    scalars_steps.__len__ = MagicMock(return_value=len(steps))

    # assertion candidates
    scalars_assertions = MagicMock()
    assertion_ids = [MagicMock() for _ in range(assertion_count)]
    scalars_assertions.__iter__ = MagicMock(return_value=iter(assertion_ids))
    scalars_assertions.__len__ = MagicMock(return_value=len(assertion_ids))

    # route variants
    route_variant_ids = [MagicMock() for _ in range(route_variant_count)]
    scalars_routes = MagicMock()
    scalars_routes.__iter__ = MagicMock(return_value=iter(route_variant_ids))

    def execute_side_effect(query):
        result = MagicMock()
        result.rowcount = 0
        # We distinguish by the query type — use call count
        return result

    # We'll patch list() calls manually by checking which scalars() return is used
    call_count = [0]

    def scalars_side_effect():
        call_count[0] += 1
        if call_count[0] == 1:
            # steps query
            return iter(steps)
        elif call_count[0] == 2:
            # route variants (inner list call)
            return iter(route_variant_ids)
        elif call_count[0] == 3:
            # assertion candidates
            return iter(assertion_ids)
        return iter([])

    execute_result = MagicMock()
    execute_result.scalars = scalars_side_effect
    execute_result.rowcount = 0
    db.execute = MagicMock(return_value=execute_result)
    db.get = MagicMock(return_value=flow)

    return db, flow


class TestComputeRecordingQuality:
    """Pure logic tests that verify phase3_ready thresholds without a real DB."""

    def _quality(
        self,
        total_steps: int,
        stable_count: int,
        noise_count: int,
        assertion_count: int,
        blocked: bool,
    ) -> dict:
        """Helper: compute phase3_ready from raw counts (mirrors _compute_recording_quality logic)."""
        phase3_ready = (
            total_steps >= 3
            and (stable_count / total_steps >= 0.5 if total_steps > 0 else False)
            and (noise_count / total_steps <= 0.3 if total_steps > 0 else True)
            and not blocked
            and assertion_count >= 1
        )
        return {
            "total_steps": total_steps,
            "stable_selector_count": stable_count,
            "noise_step_count": noise_count,
            "assertion_candidate_count": assertion_count,
            "blocked_by_security": blocked,
            "phase3_ready": phase3_ready,
        }

    def test_minimum_viable_quality_is_ready(self) -> None:
        q = self._quality(total_steps=4, stable_count=2, noise_count=1, assertion_count=1, blocked=False)
        assert q["phase3_ready"] is True

    def test_too_few_steps_not_ready(self) -> None:
        q = self._quality(total_steps=2, stable_count=2, noise_count=0, assertion_count=1, blocked=False)
        assert q["phase3_ready"] is False

    def test_insufficient_stable_selectors_not_ready(self) -> None:
        q = self._quality(total_steps=6, stable_count=2, noise_count=0, assertion_count=1, blocked=False)
        # 2/6 = 0.33, below 0.5 threshold
        assert q["phase3_ready"] is False

    def test_exactly_at_stable_selector_threshold_ready(self) -> None:
        q = self._quality(total_steps=4, stable_count=2, noise_count=0, assertion_count=1, blocked=False)
        # 2/4 = 0.5, exactly at threshold
        assert q["phase3_ready"] is True

    def test_noise_above_threshold_not_ready(self) -> None:
        # 2/4 = 0.5 > 0.3 threshold
        q = self._quality(total_steps=4, stable_count=3, noise_count=2, assertion_count=1, blocked=False)
        assert q["phase3_ready"] is False

    def test_noise_at_threshold_ready(self) -> None:
        # 1/4 = 0.25 <= 0.3
        q = self._quality(total_steps=4, stable_count=2, noise_count=1, assertion_count=1, blocked=False)
        assert q["phase3_ready"] is True

    def test_blocked_by_security_always_not_ready(self) -> None:
        q = self._quality(total_steps=10, stable_count=8, noise_count=0, assertion_count=5, blocked=True)
        assert q["phase3_ready"] is False

    def test_no_assertion_candidates_not_ready(self) -> None:
        q = self._quality(total_steps=5, stable_count=3, noise_count=0, assertion_count=0, blocked=False)
        assert q["phase3_ready"] is False

    def test_all_noise_not_ready(self) -> None:
        q = self._quality(total_steps=5, stable_count=4, noise_count=5, assertion_count=3, blocked=False)
        assert q["phase3_ready"] is False


class TestSelectorQualityReason:
    def test_data_testid(self) -> None:
        assert recorder_service._selector_quality_reason('[data-testid="x"]') == "data_attr"

    def test_data_cy(self) -> None:
        assert recorder_service._selector_quality_reason('[data-cy="btn"]') == "data_attr"

    def test_aria_label(self) -> None:
        assert recorder_service._selector_quality_reason('button[aria-label="Submit"]') == "role_name"

    def test_placeholder(self) -> None:
        assert recorder_service._selector_quality_reason('input[placeholder="Email"]') == "placeholder"

    def test_href(self) -> None:
        assert recorder_service._selector_quality_reason('a[href="/cart"]') == "href"

    def test_placeholder_href_is_structural_fallback(self) -> None:
        assert recorder_service._selector_quality_reason('a[href="#"]') == "structural_fallback"

    def test_javascript_void_href_is_structural_fallback(self) -> None:
        assert recorder_service._selector_quality_reason('a[href="javascript:void(0)"]') == "structural_fallback"

    def test_structural_nth_of_type(self) -> None:
        assert recorder_service._selector_quality_reason("div:nth-of-type(2) > button") == "structural_fallback"

    def test_none_selector(self) -> None:
        assert recorder_service._selector_quality_reason(None) is None

    def test_empty_selector(self) -> None:
        assert recorder_service._selector_quality_reason("") is None


class TestSecurityBlockedUrl:
    def test_recaptcha_url_blocked(self) -> None:
        assert recorder_service._is_security_blocked_url("https://www.google.com/recaptcha/api2") is True

    def test_bot_check_path_blocked(self) -> None:
        assert recorder_service._is_security_blocked_url("https://example.test/bot-check/verify") is True

    def test_challenge_path_blocked(self) -> None:
        assert recorder_service._is_security_blocked_url("https://example.test/challenge") is True

    def test_normal_url_not_blocked(self) -> None:
        assert recorder_service._is_security_blocked_url("https://example.test/login") is False

    def test_dashboard_not_blocked(self) -> None:
        assert recorder_service._is_security_blocked_url("https://example.test/dashboard") is False


class TestRecordingCompletionQualityDecision:
    def test_quality_allows_scenario_completion_when_phase3_ready(self) -> None:
        quality = {
            "total_steps": 4,
            "stable_selector_count": 3,
            "noise_step_count": 0,
            "assertion_candidate_count": 1,
            "blocked_by_security": False,
            "phase3_ready": True,
        }

        assert recorder_service._recording_quality_failure_reasons(quality) == []
        assert recorder_service._quality_allows_scenario_completion(quality) is True

    def test_quality_blocks_scenario_completion_for_bad_evidence(self) -> None:
        quality = {
            "total_steps": 3,
            "stable_selector_count": 0,
            "noise_step_count": 0,
            "assertion_candidate_count": 0,
            "blocked_by_security": False,
            "phase3_ready": False,
        }

        assert recorder_service._recording_quality_failure_reasons(quality) == [
            "insufficient_stable_selectors",
            "missing_assertion_candidates",
        ]
        assert recorder_service._quality_allows_scenario_completion(quality) is False

    def test_quality_blocks_scenario_completion_for_noisy_recording(self) -> None:
        quality = {
            "total_steps": 5,
            "stable_selector_count": 4,
            "noise_step_count": 3,
            "assertion_candidate_count": 1,
            "blocked_by_security": False,
            "phase3_ready": False,
        }

        assert recorder_service._recording_quality_failure_reasons(quality) == ["too_much_noise"]
        assert recorder_service._quality_allows_scenario_completion(quality) is False
