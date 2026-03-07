"""Component tests for ROAL engagement mode selection."""

from __future__ import annotations

from lib.risk.engagement import determine_engagement
from lib.risk.types import RiskMode


def test_single_bounded_step_with_high_confidence_returns_skip() -> None:
    mode = determine_engagement(
        step_count=1,
        file_count=1,
        has_shared_seams=False,
        has_consequence_notes=False,
        has_stale_inputs=False,
        has_recent_failures=False,
        has_tool_changes=False,
        triage_confidence="high",
        freshness_changed=False,
    )

    assert mode == RiskMode.SKIP


def test_multi_step_package_returns_full() -> None:
    mode = determine_engagement(
        step_count=2,
        file_count=1,
        has_shared_seams=False,
        has_consequence_notes=False,
        has_stale_inputs=False,
        has_recent_failures=False,
        has_tool_changes=False,
        triage_confidence="high",
        freshness_changed=False,
    )

    assert mode == RiskMode.FULL


def test_shared_seams_trigger_full() -> None:
    mode = determine_engagement(
        step_count=1,
        file_count=1,
        has_shared_seams=True,
        has_consequence_notes=False,
        has_stale_inputs=False,
        has_recent_failures=False,
        has_tool_changes=False,
        triage_confidence="high",
        freshness_changed=False,
    )

    assert mode == RiskMode.FULL


def test_stale_inputs_trigger_full() -> None:
    mode = determine_engagement(
        step_count=1,
        file_count=1,
        has_shared_seams=False,
        has_consequence_notes=False,
        has_stale_inputs=True,
        has_recent_failures=False,
        has_tool_changes=False,
        triage_confidence="high",
        freshness_changed=False,
    )

    assert mode == RiskMode.FULL
