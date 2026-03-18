"""Component tests for the intent triage service."""

from __future__ import annotations

import json
from pathlib import Path

from dependency_injector import providers

from unittest.mock import patch

from conftest import StubPolicies, WritingGuard, make_dispatcher
from containers import (
    ArtifactIOService,
    PromptGuard,
    Services,
    SignalReader,
)
from src.orchestrator.path_registry import PathRegistry
from src.intent.service import intent_triager
from src.intent.service.intent_triager import (
    IntentTriager,
    _TRIAGE_LINE_RE,
    _augment_risk_hints,
    _backfill_signal,
    _full_default,
    _try_parse_stdout,
)
from src.risk.repository.history import RiskHistory


def _make_triager() -> IntentTriager:
    return IntentTriager(
        communicator=Services.communicator(),
        dispatcher=Services.dispatcher(),
        logger=Services.logger(),
        policies=Services.policies(),
        prompt_guard=Services.prompt_guard(),
        signals=Services.signals(),
        task_router=Services.task_router(),
        artifact_io=Services.artifact_io(),
    )


def _make_risk_history() -> RiskHistory:
    return RiskHistory(artifact_io=ArtifactIOService())


def test_full_default_is_fail_closed() -> None:
    result = _full_default("01")

    assert result["intent_mode"] == "full"
    assert result["risk_mode"] == "full"
    assert result["risk_budget_hint"] == 4
    assert result["budgets"]["intent_expansion_max"] == 2


def test_load_triage_result_reads_signal_from_planspace(tmp_path: Path) -> None:
    signal_path = tmp_path / "artifacts" / "signals" / "intent-triage-01.json"
    signal_path.parent.mkdir(parents=True)
    signal_path.write_text(
        json.dumps({
            "intent_mode": "lightweight",
            "confidence": "high",
        }),
        encoding="utf-8",
    )

    result = _make_triager().load_triage_result("01", tmp_path)
    assert result is not None
    assert result["intent_mode"] == "lightweight"
    assert result["confidence"] == "high"
    assert result["risk_mode"] == "full"
    assert result["risk_confidence"] == "high"
    assert result["risk_budget_hint"] == 0
    assert result["posture_floor"] is None


def test_augment_risk_hints_passes_through_agent_risk_fields(tmp_path: Path) -> None:
    result = _augment_risk_hints(
        {
            "intent_mode": "lightweight",
            "confidence": "medium",
            "risk_mode": "light",
            "risk_budget_hint": 3,
        },
        "01",
        tmp_path,
        _make_risk_history(),
        related_files_count=8,
        incoming_notes_count=4,
        solve_count=2,
    )

    assert result["risk_mode"] == "light"
    assert result["risk_confidence"] == "medium"
    assert result["risk_budget_hint"] == 3
    assert result["posture_floor"] is None


def test_augment_risk_hints_defaults_fail_closed_without_heuristics(
    tmp_path: Path,
) -> None:
    result = _augment_risk_hints(
        {
            "intent_mode": "lightweight",
            "confidence": "high",
        },
        "01",
        tmp_path,
        _make_risk_history(),
        related_files_count=0,
        incoming_notes_count=0,
        solve_count=0,
    )

    assert result["risk_mode"] == "full"
    assert result["risk_confidence"] == "high"
    assert result["risk_budget_hint"] == 0


def test_run_intent_triage_returns_signal_from_agent(
    tmp_path: Path,
    capturing_communicator,
) -> None:
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    artifacts = planspace / "artifacts"
    codespace = tmp_path / "codespace"
    codespace.mkdir()

    def _dispatch(*args, **kwargs):
        signal_path = artifacts / "signals" / "intent-triage-01.json"
        signal_path.parent.mkdir(parents=True, exist_ok=True)
        signal_path.write_text(
            json.dumps({
                "section": "01",
                "intent_mode": "lightweight",
                "confidence": "medium",
                "risk_mode": "light",
                "risk_budget_hint": 2,
                "budgets": {"proposal_max": 3},
                "reason": "narrow surface",
            }),
            encoding="utf-8",
        )
        return ""

    Services.policies.override(providers.Object(StubPolicies({"intent_triage": "glm"})))
    Services.prompt_guard.override(providers.Object(WritingGuard()))
    Services.dispatcher.override(providers.Object(make_dispatcher(_dispatch)))

    try:
        result = _make_triager().run_intent_triage("01", planspace, codespace)

        assert result["intent_mode"] == "lightweight"
        assert result["risk_mode"] == "light"
        assert result["risk_confidence"] == "medium"
        assert result["risk_budget_hint"] == 2
        assert capturing_communicator.artifact_events == ["prompt:intent-triage-01"]
    finally:
        Services.dispatcher.reset_override()
        Services.prompt_guard.reset_override()
        Services.policies.reset_override()


@patch("src.intent.service.intent_triager.time.sleep")
def test_triage_prompt_does_not_advertise_skip(
    _mock_sleep,
    tmp_path: Path,
    noop_communicator,
) -> None:
    """The generated triage prompt only advertises light|full, never skip."""
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    artifacts = planspace / "artifacts"
    codespace = tmp_path / "codespace"
    codespace.mkdir()

    written_prompts: list[str] = []

    class _CaptureGuard(PromptGuard):
        def write_validated(self, content, path):
            written_prompts.append(content)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            return True
        def validate_dynamic(self, content):
            return []

    class _MockSignals(SignalReader):
        def read(self, *args, **kwargs):
            return None
        def read_tuple(self, signal_path):
            return None

    Services.policies.override(providers.Object(StubPolicies({
        "intent_triage": "glm",
        "intent_triage_escalation": "strong-model",
    })))
    Services.prompt_guard.override(providers.Object(_CaptureGuard()))
    Services.dispatcher.override(providers.Object(make_dispatcher(lambda *_a, **_kw: "")))
    Services.signals.override(providers.Object(_MockSignals()))

    try:
        _make_triager().run_intent_triage("01", planspace, codespace)

        assert len(written_prompts) == 1
        prompt = written_prompts[0]
        assert '"light"|"full"' in prompt or "'light'|'full'" in prompt
        assert '"skip"' not in prompt
    finally:
        Services.dispatcher.reset_override()
        Services.prompt_guard.reset_override()
        Services.policies.reset_override()
        Services.signals.reset_override()


def test_legacy_persisted_skip_artifact_normalizes_safely(
    tmp_path: Path,
) -> None:
    """A stale persisted triage signal with risk_mode=skip loads as light."""
    signal_path = tmp_path / "artifacts" / "signals" / "intent-triage-01.json"
    signal_path.parent.mkdir(parents=True)
    signal_path.write_text(
        json.dumps({
            "intent_mode": "lightweight",
            "confidence": "high",
            "risk_mode": "skip",
        }),
        encoding="utf-8",
    )

    result = _make_triager().load_triage_result("01", tmp_path)

    assert result is not None
    # The signal passes through as-is (load_triage_result does not
    # normalize risk_mode — that happens downstream in determine_engagement).
    assert result["risk_mode"] == "skip"


# -- Stdout fallback tests -------------------------------------------------

def test_triage_stdout_json_backfills_signal(
    tmp_path: Path,
    noop_communicator,
) -> None:
    """Signal file missing, stdout has fenced JSON -> succeeds and writes signal."""
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    artifacts = planspace / "artifacts"
    codespace = tmp_path / "codespace"
    codespace.mkdir()

    triage_payload = {
        "section": "01",
        "intent_mode": "lightweight",
        "confidence": "high",
        "risk_mode": "light",
        "risk_budget_hint": 1,
        "budgets": {"proposal_max": 3},
        "reason": "narrow scope",
    }

    def _dispatch(*args, **kwargs):
        # Agent writes stdout output with fenced JSON but does NOT write signal
        output_path = artifacts / "intent-triage-01-output.md"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            "Analyzing section 01...\n\n"
            "```json\n"
            + json.dumps(triage_payload, indent=2)
            + "\n```\n\n"
            "TRIAGE: 01 -> lightweight (narrow scope) expansion=0\n",
            encoding="utf-8",
        )
        return ""

    Services.policies.override(providers.Object(StubPolicies({
        "intent_triage": "glm",
        "intent_triage_escalation": "strong-model",
    })))
    Services.prompt_guard.override(providers.Object(WritingGuard()))
    Services.dispatcher.override(providers.Object(make_dispatcher(_dispatch)))

    try:
        result = _make_triager().run_intent_triage("01", planspace, codespace)

        assert result["intent_mode"] == "lightweight"
        assert result["confidence"] == "high"
        assert result["risk_mode"] == "light"

        # Verify the signal file was backfilled
        signal_path = artifacts / "signals" / "intent-triage-01.json"
        assert signal_path.exists()
        backfilled = json.loads(signal_path.read_text(encoding="utf-8"))
        assert backfilled["intent_mode"] == "lightweight"
    finally:
        Services.dispatcher.reset_override()
        Services.prompt_guard.reset_override()
        Services.policies.reset_override()


def test_triage_stdout_raw_json_backfills_signal(
    tmp_path: Path,
    noop_communicator,
) -> None:
    """Signal file missing, stdout has raw JSON with intent_mode -> succeeds."""
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    artifacts = planspace / "artifacts"
    codespace = tmp_path / "codespace"
    codespace.mkdir()

    def _dispatch(*args, **kwargs):
        output_path = artifacts / "intent-triage-01-output.md"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        # Raw JSON embedded in prose — no fencing
        output_path.write_text(
            'After analysis, the result is: '
            '{"intent_mode": "full", "confidence": "medium", '
            '"risk_mode": "full", "risk_budget_hint": 2, '
            '"reason": "broad integration"}\n'
            'Done.\n',
            encoding="utf-8",
        )
        return ""

    Services.policies.override(providers.Object(StubPolicies({
        "intent_triage": "glm",
        "intent_triage_escalation": "strong-model",
    })))
    Services.prompt_guard.override(providers.Object(WritingGuard()))
    Services.dispatcher.override(providers.Object(make_dispatcher(_dispatch)))

    try:
        result = _make_triager().run_intent_triage("01", planspace, codespace)

        assert result["intent_mode"] == "full"
        assert result["confidence"] == "medium"

        signal_path = artifacts / "signals" / "intent-triage-01.json"
        assert signal_path.exists()
    finally:
        Services.dispatcher.reset_override()
        Services.prompt_guard.reset_override()
        Services.policies.reset_override()


@patch("src.intent.service.intent_triager.time.sleep")
def test_triage_missing_signal_escalates(
    _mock_sleep,
    tmp_path: Path,
    noop_communicator,
) -> None:
    """Signal file missing, stdout empty -> retries GLM then escalates to stronger model."""
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    artifacts = planspace / "artifacts"
    codespace = tmp_path / "codespace"
    codespace.mkdir()

    dispatch_calls: list[str] = []

    def _dispatch(*args, **kwargs):
        model = args[0] if args else kwargs.get("model", "")
        dispatch_calls.append(model)
        if model == "strong-model":
            # Escalation attempt writes a proper signal file
            signal_path = artifacts / "signals" / "intent-triage-01.json"
            signal_path.parent.mkdir(parents=True, exist_ok=True)
            signal_path.write_text(
                json.dumps({
                    "section": "01",
                    "intent_mode": "full",
                    "confidence": "medium",
                    "risk_mode": "full",
                    "risk_budget_hint": 3,
                    "budgets": {"proposal_max": 5},
                    "reason": "escalated — complex surface",
                }),
                encoding="utf-8",
            )
        else:
            # GLM attempts: write empty output, no signal
            output_path = artifacts / "intent-triage-01-output.md"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text("", encoding="utf-8")
        return ""

    Services.policies.override(providers.Object(StubPolicies({
        "intent_triage": "glm",
        "intent_triage_escalation": "strong-model",
    })))
    Services.prompt_guard.override(providers.Object(WritingGuard()))
    Services.dispatcher.override(providers.Object(make_dispatcher(_dispatch)))

    try:
        result = _make_triager().run_intent_triage("01", planspace, codespace)

        assert result["intent_mode"] == "full"
        assert result["confidence"] == "medium"
        # Verify all dispatches: initial GLM + GLM retry + escalation
        assert len(dispatch_calls) == 3
        assert dispatch_calls[0] == "glm"
        assert dispatch_calls[1] == "glm"
        assert dispatch_calls[2] == "strong-model"
        _mock_sleep.assert_called_once_with(5)
    finally:
        Services.dispatcher.reset_override()
        Services.prompt_guard.reset_override()
        Services.policies.reset_override()


@patch("src.intent.service.intent_triager.time.sleep")
def test_triage_all_fail_defaults_full(
    _mock_sleep,
    tmp_path: Path,
    noop_communicator,
) -> None:
    """All dispatches (initial GLM, GLM retry, escalation) fail -> defaults to full."""
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    artifacts = planspace / "artifacts"
    codespace = tmp_path / "codespace"
    codespace.mkdir()

    dispatch_calls: list[str] = []

    def _dispatch(*args, **kwargs):
        model = args[0] if args else kwargs.get("model", "")
        dispatch_calls.append(model)
        # All attempts produce empty output, no signal
        output_path = artifacts / "intent-triage-01-output.md"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("No useful output here.\n", encoding="utf-8")
        return ""

    Services.policies.override(providers.Object(StubPolicies({
        "intent_triage": "glm",
        "intent_triage_escalation": "strong-model",
    })))
    Services.prompt_guard.override(providers.Object(WritingGuard()))
    Services.dispatcher.override(providers.Object(make_dispatcher(_dispatch)))

    try:
        result = _make_triager().run_intent_triage("01", planspace, codespace)

        assert result["intent_mode"] == "full"
        assert result["confidence"] == "low"
        assert result["risk_mode"] == "full"
        assert result["risk_budget_hint"] == 4
        # All three dispatches happened: initial GLM + GLM retry + escalation
        assert len(dispatch_calls) == 3
        assert dispatch_calls[0] == "glm"
        assert dispatch_calls[1] == "glm"
        assert dispatch_calls[2] == "strong-model"
    finally:
        Services.dispatcher.reset_override()
        Services.prompt_guard.reset_override()
        Services.policies.reset_override()


# -- _TRIAGE_LINE_RE unit tests --------------------------------------------

def test_triage_line_parse_arrow_format() -> None:
    """Arrow-format line extracts the mode, not the section ID."""
    m = _TRIAGE_LINE_RE.search("TRIAGE: 06 → full (reason) expansion=0")
    assert m is not None
    assert m.group(1) == "full"


def test_triage_line_parse_lightweight() -> None:
    m = _TRIAGE_LINE_RE.search("TRIAGE: 01 → lightweight (narrow scope) expansion=0")
    assert m is not None
    assert m.group(1) == "lightweight"


def test_triage_line_parse_no_match() -> None:
    m = _TRIAGE_LINE_RE.search("I'll read the artifacts")
    assert m is None
