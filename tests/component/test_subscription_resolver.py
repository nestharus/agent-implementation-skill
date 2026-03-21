from __future__ import annotations

import json
from pathlib import Path

import pytest

from containers import ArtifactIOService
from src.flow.engine.subscription_resolver import SubscriptionResolver
from src.flow.service.task_db_client import (
    init_db,
    request_task,
    subscribe_to_task,
    task_db,
)
from src.flow.types.result_envelope import TaskResultEnvelope
from src.flow.types.routing import Task
from src.orchestrator.path_registry import PathRegistry


def _make_task(
    *,
    task_type: str = "test.task",
    submitted_by: str = "tester",
    concern_scope: str | None = "scope-a",
    payload_path: str | None = "payload.json",
    depends_on_tasks: list[int] | None = None,
) -> Task:
    return Task(
        task_type=task_type,
        submitted_by=submitted_by,
        concern_scope=concern_scope,
        payload_path=payload_path,
        priority="normal",
        depends_on_tasks=depends_on_tasks or [],
    )


def _mark_complete(db_path: Path, task_id: int, *, result_envelope_path: str | None = None) -> None:
    with task_db(db_path) as conn:
        conn.execute(
            """UPDATE tasks
               SET status='complete',
                   result_envelope_path=?,
                   completed_at=datetime('now')
               WHERE id=?""",
            (result_envelope_path, task_id),
        )
        conn.commit()


def _seed_impl_feedback_context(
    planspace: Path,
    section_number: str,
    *,
    problem_alignment: str = "# Alignment\n",
    surface_registry: dict | None = None,
) -> None:
    paths = PathRegistry(planspace)
    intent_dir = paths.intent_section_dir(section_number)
    intent_dir.mkdir(parents=True, exist_ok=True)
    (intent_dir / "problem-alignment.md").write_text(problem_alignment, encoding="utf-8")
    registry_payload = surface_registry or {"section": section_number, "next_id": 1, "surfaces": []}
    paths.section_spec(section_number).parent.mkdir(parents=True, exist_ok=True)
    paths.section_spec(section_number).write_text(f"# Section {section_number}\n", encoding="utf-8")
    (intent_dir / "surface-registry.json").write_text(
        json.dumps(registry_payload, indent=2) + "\n",
        encoding="utf-8",
    )


def test_resolver_satisfies_dependencies_and_promotes_blocked_tasks(tmp_path: Path) -> None:
    db_path = tmp_path / "run.db"
    init_db(db_path)
    upstream = request_task(db_path, _make_task(task_type="upstream"))
    downstream = request_task(
        db_path,
        _make_task(task_type="downstream", depends_on_tasks=[upstream]),
    )
    _mark_complete(db_path, upstream)

    resolver = SubscriptionResolver(ArtifactIOService())
    resolver.resolve(
        db_path,
        upstream,
        tmp_path,
        TaskResultEnvelope(
            task_id=upstream,
            task_type="upstream",
            status="complete",
            output_path="done.txt",
        ),
    )

    with task_db(db_path) as conn:
        dep_row = conn.execute(
            """SELECT satisfied
               FROM task_dependencies
               WHERE task_id=? AND depends_on_task_id=?""",
            (downstream, upstream),
        ).fetchone()
        task_row = conn.execute(
            "SELECT status FROM tasks WHERE id=?",
            (downstream,),
        ).fetchone()

    assert dep_row == (1,)
    assert task_row == ("pending",)


def test_resolver_creates_callback_tasks_for_active_subscriptions(tmp_path: Path) -> None:
    db_path = tmp_path / "run.db"
    init_db(db_path)
    task_id = request_task(db_path, _make_task(task_type="producer"))
    subscribe_to_task(
        db_path,
        task_id,
        "section-01",
        callback_task_type="callback.task",
    )
    _mark_complete(db_path, task_id)

    resolver = SubscriptionResolver(ArtifactIOService())
    resolver.resolve(
        db_path,
        task_id,
        tmp_path,
        TaskResultEnvelope(
            task_id=task_id,
            task_type="producer",
            status="complete",
            output_path="producer.out",
        ),
    )

    with task_db(db_path) as conn:
        callback_rows = conn.execute(
            "SELECT task_type, concern_scope FROM tasks WHERE task_type='callback.task'",
        ).fetchall()
        subscription_row = conn.execute(
            "SELECT status FROM task_subscriptions WHERE task_id=?",
            (task_id,),
        ).fetchone()

    assert callback_rows == [("callback.task", "section-01")]
    assert subscription_row == ("consumed",)


def test_resolver_marks_subscription_failed_on_callback_submission_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "run.db"
    init_db(db_path)
    task_id = request_task(db_path, _make_task(task_type="producer"))
    subscribe_to_task(
        db_path,
        task_id,
        "section-01",
        callback_task_type="callback.task",
    )
    _mark_complete(db_path, task_id)

    def _boom(*args, **kwargs):
        raise RuntimeError("submission failed")

    monkeypatch.setattr(
        "flow.service.task_db_client._request_task_in_txn",
        _boom,
    )

    resolver = SubscriptionResolver(ArtifactIOService())
    resolver.resolve(
        db_path,
        task_id,
        tmp_path,
        TaskResultEnvelope(
            task_id=task_id,
            task_type="producer",
            status="complete",
            output_path="producer.out",
        ),
    )

    with task_db(db_path) as conn:
        row = conn.execute(
            "SELECT status, last_error FROM task_subscriptions WHERE task_id=?",
            (task_id,),
        ).fetchone()

    assert row == ("failed", "submission failed")


def test_resolver_records_value_axes_and_triggers_realignment(tmp_path: Path) -> None:
    db_path = tmp_path / "run.db"
    init_db(db_path)
    section_spec = tmp_path / "artifacts" / "sections" / "section-01.md"
    section_spec.parent.mkdir(parents=True, exist_ok=True)
    section_spec.write_text("# Section 01\n", encoding="utf-8")
    task_id = request_task(
        db_path,
        _make_task(task_type="research.synthesis", concern_scope="section-01"),
    )
    _mark_complete(db_path, task_id)

    resolver = SubscriptionResolver(ArtifactIOService())
    resolver.resolve(
        db_path,
        task_id,
        tmp_path,
        TaskResultEnvelope(
            task_id=task_id,
            task_type="research.synthesis",
            status="complete",
            output_path="producer.out",
            new_value_axes=["Latency"],
        ),
    )

    with task_db(db_path) as conn:
        axes = conn.execute(
            "SELECT axis_name FROM value_axes WHERE section_scope='section-01'",
        ).fetchall()
        assess_rows = conn.execute(
            "SELECT task_type FROM tasks WHERE concern_scope='section-01' AND task_type='section.assess'",
        ).fetchall()

    assert axes == [("Latency",)]
    assert assess_rows == [("section.assess",)]


def test_resolver_writes_impl_feedback_surfaces_for_novel_value_axes(tmp_path: Path) -> None:
    db_path = tmp_path / "run.db"
    init_db(db_path)
    _seed_impl_feedback_context(tmp_path, "01")
    task_id = request_task(
        db_path,
        _make_task(task_type="section.implement", concern_scope="section-01"),
    )
    _mark_complete(db_path, task_id)

    resolver = SubscriptionResolver(ArtifactIOService())
    detected = resolver.resolve(
        db_path,
        task_id,
        tmp_path,
        TaskResultEnvelope(
            task_id=task_id,
            task_type="section.implement",
            status="complete",
            output_path="producer.out",
            new_value_axes=["Latency"],
        ),
    )

    assert detected is True
    impl_feedback_path = PathRegistry(tmp_path).impl_feedback_surfaces("01")
    payload = json.loads(impl_feedback_path.read_text(encoding="utf-8"))
    assert payload["problem_surfaces"][0]["kind"] == "new_axis"
    assert payload["problem_surfaces"][0]["title"] == "Latency"
    assert "task" in payload["problem_surfaces"][0]["evidence"]


def test_resolver_skips_impl_feedback_for_already_covered_value_axes(tmp_path: Path) -> None:
    db_path = tmp_path / "run.db"
    init_db(db_path)
    _seed_impl_feedback_context(
        tmp_path,
        "01",
        problem_alignment="# Alignment\n## Latency\n",
    )
    task_id = request_task(
        db_path,
        _make_task(task_type="section.implement", concern_scope="section-01"),
    )
    _mark_complete(db_path, task_id)

    resolver = SubscriptionResolver(ArtifactIOService())
    detected = resolver.resolve(
        db_path,
        task_id,
        tmp_path,
        TaskResultEnvelope(
            task_id=task_id,
            task_type="section.implement",
            status="complete",
            output_path="producer.out",
            new_value_axes=["Latency"],
        ),
    )

    assert detected is False
    assert not PathRegistry(tmp_path).impl_feedback_surfaces("01").exists()


def test_novelty_check_reads_problem_alignment_and_surface_registry(tmp_path: Path) -> None:
    db_path = tmp_path / "run.db"
    init_db(db_path)
    _seed_impl_feedback_context(
        tmp_path,
        "01",
        problem_alignment="# Alignment\n## Throughput\n",
        surface_registry={
            "section": "01",
            "next_id": 2,
            "surfaces": [
                {
                    "id": "P-01-0001",
                    "notes": "Latency",
                    "description": "Already pending",
                },
            ],
        },
    )
    task_id = request_task(
        db_path,
        _make_task(task_type="section.implement", concern_scope="section-01"),
    )
    _mark_complete(db_path, task_id)

    resolver = SubscriptionResolver(ArtifactIOService())
    detected = resolver.resolve(
        db_path,
        task_id,
        tmp_path,
        TaskResultEnvelope(
            task_id=task_id,
            task_type="section.implement",
            status="complete",
            output_path="producer.out",
            new_value_axes=["Latency", "Throughput", "Security"],
        ),
    )

    assert detected is True
    payload = json.loads(
        PathRegistry(tmp_path).impl_feedback_surfaces("01").read_text(encoding="utf-8"),
    )
    assert [surface["title"] for surface in payload["problem_surfaces"]] == ["Security"]
