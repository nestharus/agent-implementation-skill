from __future__ import annotations

import json
from pathlib import Path

from containers import ArtifactIOService
from src.flow.engine.bootstrap_coordinator import BootstrapCoordinator
from src.flow.service.task_db_client import init_db
from src.orchestrator.path_registry import PathRegistry


class _FakeFlowSubmitter:
    def __init__(self) -> None:
        self.chain_calls: list[tuple] = []
        self.fanout_calls: list[tuple] = []

    def submit_chain(self, env, steps, **kwargs):
        self.chain_calls.append((env, steps, kwargs))
        return [len(self.chain_calls)]

    def submit_fanout(self, env, branches, **kwargs):
        self.fanout_calls.append((env, branches, kwargs))
        return "gate-1"


def test_bootstrap_coordinator_routes_confirm_understanding_to_interpret_response(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "run.db"
    init_db(db_path)
    planspace = tmp_path
    PathRegistry(planspace).ensure_artifacts_tree()
    signal_path = planspace / "artifacts" / "signals" / "confirm-understanding-signal.json"
    signal_path.write_text(
        '{"state":"NEED_DECISION","detail":"Need answer"}',
        encoding="utf-8",
    )
    submitter = _FakeFlowSubmitter()
    coordinator = BootstrapCoordinator(ArtifactIOService(), submitter)

    handled = coordinator.handle_completion(
        {
            "id": 1,
            "task_type": "bootstrap.confirm_understanding",
            "flow_id": "flow-1",
            "payload_path": str(planspace / "spec.md"),
        },
        db_path,
        planspace,
    )

    assert handled is True
    assert submitter.chain_calls
    _, steps, _ = submitter.chain_calls[0]
    assert [step.task_type for step in steps] == ["bootstrap.interpret_response"]


def test_bootstrap_coordinator_fail_closes_invalid_user_response(tmp_path: Path) -> None:
    db_path = tmp_path / "run.db"
    init_db(db_path)
    planspace = tmp_path
    PathRegistry(planspace).ensure_artifacts_tree()
    response_path = planspace / "artifacts" / "global" / "user-response.json"
    response_path.parent.mkdir(parents=True, exist_ok=True)
    response_path.write_text('{"missing":"keys"}', encoding="utf-8")
    submitter = _FakeFlowSubmitter()
    coordinator = BootstrapCoordinator(ArtifactIOService(), submitter)

    handled = coordinator.handle_completion(
        {
            "id": 2,
            "task_type": "bootstrap.interpret_response",
            "flow_id": "flow-1",
            "payload_path": str(planspace / "spec.md"),
        },
        db_path,
        planspace,
    )

    assert handled is True
    assert submitter.chain_calls == []
    assert not response_path.exists()
    assert (response_path.parent / "user-response.malformed.json").exists()


def test_align_proposal_builds_codemap_when_expansion_log_is_empty(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "run.db"
    init_db(db_path)
    planspace = tmp_path
    paths = PathRegistry(planspace)
    paths.ensure_artifacts_tree()
    (planspace / "artifacts" / "global").mkdir(parents=True, exist_ok=True)
    (planspace / "artifacts" / "global" / "proposal-alignment.json").write_text(
        json.dumps({"aligned": False}),
        encoding="utf-8",
    )
    (planspace / "artifacts" / "global" / "expansion-log.json").write_text(
        json.dumps({"expansions": []}),
        encoding="utf-8",
    )
    paths.global_proposal().write_text("# Proposal\n", encoding="utf-8")
    submitter = _FakeFlowSubmitter()
    coordinator = BootstrapCoordinator(ArtifactIOService(), submitter)

    handled = coordinator.handle_completion(
        {
            "id": 3,
            "task_type": "bootstrap.align_proposal",
            "flow_id": "flow-1",
            "payload_path": str(planspace / "spec.md"),
        },
        db_path,
        planspace,
    )

    assert handled is True
    _, steps, _ = submitter.chain_calls[0]
    assert [step.task_type for step in steps] == ["bootstrap.build_codemap"]


def test_align_proposal_builds_codemap_when_proposal_hash_is_unchanged(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "run.db"
    init_db(db_path)
    planspace = tmp_path
    paths = PathRegistry(planspace)
    paths.ensure_artifacts_tree()
    global_dir = planspace / "artifacts" / "global"
    global_dir.mkdir(parents=True, exist_ok=True)
    paths.global_proposal().write_text("# Proposal\nStable\n", encoding="utf-8")
    (global_dir / "proposal-alignment.json").write_text(
        json.dumps({"aligned": False}),
        encoding="utf-8",
    )
    submitter = _FakeFlowSubmitter()
    coordinator = BootstrapCoordinator(ArtifactIOService(), submitter)
    task = {
        "id": 4,
        "task_type": "bootstrap.align_proposal",
        "flow_id": "flow-1",
        "payload_path": str(planspace / "spec.md"),
    }

    first = coordinator.handle_completion(task, db_path, planspace)
    (global_dir / "expansion-log.json").write_text(
        json.dumps({"expansions": [{"type": "problem_coverage"}]}),
        encoding="utf-8",
    )
    second = coordinator.handle_completion(task, db_path, planspace)

    assert first is True
    assert second is True
    _, first_steps, _ = submitter.chain_calls[0]
    _, second_steps, _ = submitter.chain_calls[1]
    assert [step.task_type for step in first_steps] == ["bootstrap.expand_proposal"]
    assert [step.task_type for step in second_steps] == ["bootstrap.build_codemap"]
