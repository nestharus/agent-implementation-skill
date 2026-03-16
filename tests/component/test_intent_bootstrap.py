from __future__ import annotations

import json
from pathlib import Path

import pytest

from containers import ArtifactIOService, Services
from src.intent.engine import intent_initializer as bootstrap
from src.intent.engine.intent_initializer import IntentInitializer
from orchestrator.types import Section


def _make_section(planspace: Path) -> Section:
    section_path = planspace / "artifacts" / "sections" / "section-01.md"
    section_path.write_text("# Section 01\n", encoding="utf-8")
    problem_frame = (
        planspace / "artifacts" / "sections" / "section-01-problem-frame.md"
    )
    problem_frame.write_text("Problem frame summary", encoding="utf-8")
    return Section(number="01", path=section_path, related_files=["src/main.py"])


class _StubTriager:
    """Minimal stub for IntentTriager — run_intent_triage returns injected value."""

    def __init__(self, result: dict) -> None:
        self._result = result

    def run_intent_triage(self, *_args, **_kwargs) -> dict:
        return self._result

    def load_triage_result(self, *_args, **_kwargs):
        return self._result


class _StubGovernance:
    """Minimal stub for GovernancePacketBuilder."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def build_section_governance_packet(self, sec_num, planspace, summary=""):
        self.calls.append((sec_num, planspace, summary))


class _StubIntentPack:
    """Minimal stub for IntentPackGenerator."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def generate_intent_pack(self, _section, _planspace, _codespace, *, incoming_notes):
        self.calls.append(incoming_notes)


class _StubBootstrapper:
    """Minimal stub for PhilosophyBootstrapper."""

    def __init__(self, result: dict) -> None:
        self._result = result

    def ensure_global_philosophy(self, *_args, **_kwargs):
        return self._result


class _StubPipelineControl:
    """Minimal stub for PipelineControlService."""

    def __init__(self, *, alignment_values=None):
        self._iter = iter(alignment_values or [False])

    def alignment_changed_pending(self, _planspace):
        return next(self._iter, False)

    def pause_for_parent(self, *_args, **_kwargs):
        pass


def _make_initializer(
    triage_result: dict,
    governance: _StubGovernance | None = None,
    intent_pack: _StubIntentPack | None = None,
    philosophy_result: dict | None = None,
    pipeline_control: _StubPipelineControl | None = None,
) -> tuple[IntentInitializer, _StubGovernance, _StubIntentPack]:
    gov = governance or _StubGovernance()
    pack = intent_pack or _StubIntentPack()
    phi = _StubBootstrapper(philosophy_result or {
        "status": "ready", "blocking_state": None,
        "philosophy_path": None, "detail": "ready",
    })
    initializer = IntentInitializer(
        artifact_io=ArtifactIOService(),
        communicator=Services.communicator(),
        governance_packet_builder=gov,
        intent_pack_generator=pack,
        intent_triager=_StubTriager(triage_result),
        logger=Services.logger(),
        philosophy_bootstrapper=phi,
        pipeline_control=pipeline_control or _StubPipelineControl(),
        policies=Services.policies(),
    )
    return initializer, gov, pack


def test_run_intent_bootstrap_full_mode_generates_pack_and_merges_budget(
    planspace: Path,
    codespace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capturing_communicator,
    noop_pipeline_control,
) -> None:
    section = _make_section(planspace)
    cycle_budget_path = (
        planspace / "artifacts" / "signals" / "section-01-cycle-budget.json"
    )
    cycle_budget_path.write_text(
        json.dumps({"proposal_max": 1, "implementation_max": 1}),
        encoding="utf-8",
    )

    triage_result = {
        "intent_mode": "full",
        "budgets": {
            "proposal_max": 6,
            "implementation_max": 7,
            "intent_expansion_max": 2,
            "max_new_surfaces_per_cycle": 3,
            "ignored": 99,
        },
    }

    initializer, governance, intent_pack = _make_initializer(
        triage_result,
        philosophy_result={
            "status": "ready",
            "blocking_state": None,
            "philosophy_path": (
                planspace / "artifacts" / "intent" / "global" / "philosophy.md"
            ),
            "detail": "ready",
        },
    )

    monkeypatch.setattr(
        bootstrap,
        "extract_todos_from_files",
        lambda *_args, **_kwargs: "- TODO: preserve invariant\n",
    )

    cycle_budget = initializer.run_intent_bootstrap(
        section,
        planspace,
        codespace,
        "incoming note",
    )

    assert cycle_budget == {
        "proposal_max": 6,
        "implementation_max": 7,
        "intent_expansion_max": 2,
        "max_new_surfaces_per_cycle": 3,
    }
    assert capturing_communicator.traceability_calls
    assert governance.calls == [("01", planspace, "Problem frame summary")]
    assert intent_pack.calls == ["incoming note"]
    assert (
        planspace / "artifacts" / "todos" / "section-01-todos.md"
    ).read_text(encoding="utf-8") == "- TODO: preserve invariant\n"


def test_run_intent_bootstrap_blocks_when_philosophy_is_unavailable(
    planspace: Path,
    codespace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capturing_pipeline_control,
    noop_communicator,
) -> None:
    section = _make_section(planspace)

    triage_result = {"intent_mode": "lightweight", "budgets": {}}

    initializer, _, _ = _make_initializer(
        triage_result,
        philosophy_result={
            "status": "needs_user_input",
            "blocking_state": "NEED_DECISION",
            "philosophy_path": None,
            "detail": "philosophy bootstrap needs user input",
        },
        pipeline_control=capturing_pipeline_control,
    )

    monkeypatch.setattr(
        bootstrap,
        "extract_todos_from_files",
        lambda *_args, **_kwargs: "",
    )
    blocker_rollups: list[Path] = []
    monkeypatch.setattr(
        bootstrap,
        "update_blocker_rollup",
        lambda current_planspace: blocker_rollups.append(current_planspace),
    )

    result = initializer.run_intent_bootstrap(
        section,
        planspace,
        codespace,
        None,
    )

    assert result is None
    assert blocker_rollups == [planspace]
    assert capturing_pipeline_control.pause_calls == [(
        planspace,
        "pause:need_decision:global:philosophy bootstrap requires user input",
    )]
    assert not (
        planspace / "artifacts" / "signals" / "philosophy-blocker-01.json"
    ).exists()


def test_run_intent_bootstrap_qa_mode_blocks_for_external_responder(
    planspace: Path,
    codespace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capturing_pipeline_control,
    noop_communicator,
) -> None:
    """With QA shortcuts removed, qa_mode=True still pauses for philosophy.

    The QA harness monitor responds externally via the message bus rather
    than bypassing the pause inline.
    """
    section = _make_section(planspace)

    # Write parameters.json with qa_mode enabled.
    params_path = planspace / "artifacts" / "parameters.json"
    params_path.write_text(json.dumps({"qa_mode": True}), encoding="utf-8")

    triage_result = {"intent_mode": "lightweight", "budgets": {}}

    initializer, _, _ = _make_initializer(
        triage_result,
        philosophy_result={
            "status": "needs_user_input",
            "blocking_state": "NEED_DECISION",
            "philosophy_path": None,
            "detail": "philosophy bootstrap needs user input",
        },
        pipeline_control=capturing_pipeline_control,
    )

    monkeypatch.setattr(
        bootstrap,
        "extract_todos_from_files",
        lambda *_args, **_kwargs: "",
    )
    blocker_rollups: list[Path] = []
    monkeypatch.setattr(
        bootstrap,
        "update_blocker_rollup",
        lambda current_planspace: blocker_rollups.append(current_planspace),
    )

    result = initializer.run_intent_bootstrap(
        section,
        planspace,
        codespace,
        None,
    )

    # Pipeline should halt — the QA harness responds externally.
    assert result is None
    assert blocker_rollups == [planspace]
    assert capturing_pipeline_control.pause_calls == [(
        planspace,
        "pause:need_decision:global:philosophy bootstrap requires user input",
    )]


def test_run_intent_bootstrap_qa_mode_false_still_blocks(
    planspace: Path,
    codespace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capturing_pipeline_control,
    noop_communicator,
) -> None:
    """When qa_mode is explicitly false, BLOCKING_NEED_DECISION should still halt."""
    section = _make_section(planspace)

    # Write parameters.json with qa_mode disabled.
    params_path = planspace / "artifacts" / "parameters.json"
    params_path.write_text(json.dumps({"qa_mode": False}), encoding="utf-8")

    triage_result = {"intent_mode": "lightweight", "budgets": {}}

    initializer, _, _ = _make_initializer(
        triage_result,
        philosophy_result={
            "status": "needs_user_input",
            "blocking_state": "NEED_DECISION",
            "philosophy_path": None,
            "detail": "philosophy bootstrap needs user input",
        },
        pipeline_control=capturing_pipeline_control,
    )

    monkeypatch.setattr(
        bootstrap,
        "extract_todos_from_files",
        lambda *_args, **_kwargs: "",
    )
    blocker_rollups: list[Path] = []
    monkeypatch.setattr(
        bootstrap,
        "update_blocker_rollup",
        lambda current_planspace: blocker_rollups.append(current_planspace),
    )

    result = initializer.run_intent_bootstrap(
        section,
        planspace,
        codespace,
        None,
    )

    # Pipeline should halt.
    assert result is None
    assert blocker_rollups == [planspace]
    assert capturing_pipeline_control.pause_calls == [(
        planspace,
        "pause:need_decision:global:philosophy bootstrap requires user input",
    )]


def test_run_intent_bootstrap_aborts_when_alignment_changes_after_philosophy(
    planspace: Path,
    codespace: Path,
    monkeypatch: pytest.MonkeyPatch,
    noop_pipeline_control,
    noop_communicator,
) -> None:
    section = _make_section(planspace)

    triage_result = {"intent_mode": "full", "budgets": {}}

    initializer, _, _ = _make_initializer(
        triage_result,
        philosophy_result={
            "status": "ready",
            "blocking_state": None,
            "philosophy_path": (
                planspace / "artifacts" / "intent" / "global" / "philosophy.md"
            ),
            "detail": "ready",
        },
        pipeline_control=_StubPipelineControl(alignment_values=[True]),
    )

    monkeypatch.setattr(
        bootstrap,
        "extract_todos_from_files",
        lambda *_args, **_kwargs: "",
    )

    result = initializer.run_intent_bootstrap(
        section,
        planspace,
        codespace,
        None,
    )

    assert result is None
