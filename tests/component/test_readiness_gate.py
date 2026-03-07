from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.scripts.lib.readiness_gate import (
    publish_discoveries,
    resolve_and_route,
    route_blockers,
)
from src.scripts.section_loop.types import Section


def _section(planspace: Path) -> Section:
    section = Section(
        number="03",
        path=planspace / "artifacts" / "sections" / "section-03.md",
    )
    section.path.parent.mkdir(parents=True, exist_ok=True)
    section.path.write_text("# Section 03\n", encoding="utf-8")
    return section


def test_publish_discoveries_writes_scope_delta_and_research_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    planspace = tmp_path / "planspace"
    (planspace / "artifacts" / "scope-deltas").mkdir(parents=True, exist_ok=True)
    (planspace / "artifacts" / "open-problems").mkdir(parents=True, exist_ok=True)
    appended: list[str] = []
    monkeypatch.setattr(
        "src.scripts.lib.readiness_gate._append_open_problem",
        lambda _planspace, _section, detail, _source: appended.append(detail),
    )

    publish_discoveries(
        "03",
        {
            "new_section_candidates": ["Create retry worker"],
            "research_questions": ["Need API quota behavior"],
        },
        planspace,
    )

    scope_files = list((planspace / "artifacts" / "scope-deltas").glob("*.json"))
    assert len(scope_files) == 1
    assert appended == ["Need API quota behavior"]
    rq_path = planspace / "artifacts" / "open-problems" / "section-03-research-questions.json"
    assert json.loads(rq_path.read_text(encoding="utf-8"))["research_questions"] == [
        "Need API quota behavior"
    ]


def test_route_blockers_writes_signals_and_queues_reconciliation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    planspace = tmp_path / "planspace"
    (planspace / "artifacts" / "signals").mkdir(parents=True, exist_ok=True)
    queued: list[tuple[list[str], list[str]]] = []

    monkeypatch.setattr(
        "src.scripts.lib.readiness_gate.queue_reconciliation_request",
        lambda _artifacts, _section, contracts, anchors: queued.append(
            (contracts, anchors)
        ),
    )
    monkeypatch.setattr(
        "src.scripts.lib.readiness_gate._update_blocker_rollup",
        lambda *_args, **_kwargs: None,
    )

    route_blockers(
        "03",
        {
            "user_root_questions": ["Choose retry policy"],
            "shared_seam_candidates": ["shared client cache"],
            "unresolved_contracts": ["CacheProtocol"],
            "unresolved_anchors": ["client.cache"],
        },
        planspace,
        "parent",
    )

    assert (
        planspace / "artifacts" / "signals" / "section-03-proposal-q0-signal.json"
    ).exists()
    assert (
        planspace / "artifacts" / "signals" / "substrate-trigger-03-00.json"
    ).exists()
    assert (
        planspace / "artifacts" / "signals" / "section-03-seam-0-signal.json"
    ).exists()
    assert queued == [(["CacheProtocol"], ["client.cache"])]


def test_resolve_and_route_returns_blocked_proposal_pass_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    planspace = tmp_path / "planspace"
    artifacts = planspace / "artifacts"
    (artifacts / "proposals").mkdir(parents=True, exist_ok=True)
    (artifacts / "signals").mkdir(parents=True, exist_ok=True)
    section = _section(planspace)
    proposal_state_path = artifacts / "proposals" / "section-03-proposal-state.json"
    proposal_state_path.write_text(
        json.dumps(
            {
                "resolved_anchors": [],
                "unresolved_contracts": ["CacheProtocol"],
                "resolved_contracts": [],
                "unresolved_anchors": [],
                "user_root_questions": ["Choose retry policy"],
                "shared_seam_candidates": [],
                "new_section_candidates": [],
                "research_questions": [],
                "blocking_research_questions": [],
                "execution_ready": False,
                "readiness_rationale": "blocked",
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "src.scripts.lib.readiness_gate.resolve_readiness",
        lambda *_args, **_kwargs: {
            "ready": False,
            "blockers": [{"type": "user_root_questions", "description": "Choose retry policy"}],
            "rationale": "blocked",
        },
    )
    monkeypatch.setattr(
        "src.scripts.lib.readiness_gate.mailbox_send",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "src.scripts.lib.readiness_gate._append_open_problem",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "src.scripts.lib.readiness_gate.queue_reconciliation_request",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "src.scripts.lib.readiness_gate._update_blocker_rollup",
        lambda *_args, **_kwargs: None,
    )

    result = resolve_and_route(section, planspace, "parent", "proposal")

    assert result.ready is False
    assert result.blockers == [
        {"type": "user_root_questions", "description": "Choose retry policy"}
    ]
    assert result.proposal_pass_result is not None
    assert result.proposal_pass_result.execution_ready is False
    assert result.proposal_pass_result.needs_reconciliation is True
