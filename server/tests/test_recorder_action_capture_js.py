from pathlib import Path


RECORDER_JS = Path(__file__).resolve().parents[1] / "recorder_action_capture.js"


def _source() -> str:
    return RECORDER_JS.read_text(encoding="utf-8")


def test_click_listener_records_actionable_target_not_raw_child() -> None:
    src = _source()

    assert "function actionableTarget(el)" in src
    assert "const el = actionableTarget(e.target);" in src
    assert "'svg'" in src
    assert "'[role=\"menuitem\"]'" in src
    assert "'[role=\"option\"]'" in src


def test_href_hash_and_ad_iframes_are_not_treated_as_stable_evidence() -> None:
    src = _source()

    assert "function meaningfulHref(el)" in src
    assert "href !== '#'" in src
    assert "href !== 'javascript:void(0)'" in src
    assert 'iframe[aria-label*="Advertisement" i]' in src
    assert 'iframe[src*="doubleclick"]' in src
