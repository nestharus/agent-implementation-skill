from __future__ import annotations

from pathlib import Path

from src.flow.service.task_db_client import (
    answer_user_input,
    claim_runnable_task,
    complete_task_with_result,
    detect_dependency_starvation,
    detect_value_expansion,
    fail_task_with_result,
    get_active_subscriptions,
    get_value_axes,
    init_db,
    query_tasks,
    record_value_axis,
    request_task,
    request_user_input,
    subscribe_to_task,
    task_db,
    update_value_axis_status,
)
from src.flow.types.routing import Task
from src.flow.types.result_envelope import TaskResultEnvelope


def _make_task(
    *,
    task_type: str = "test.task",
    submitted_by: str = "tester",
    concern_scope: str | None = "scope-a",
    payload_path: str | None = "payload.json",
    priority: str = "normal",
    problem_id: str | None = "PRB-0001",
    depends_on_tasks: list[int] | None = None,
) -> Task:
    return Task(
        task_type=task_type,
        submitted_by=submitted_by,
        concern_scope=concern_scope,
        payload_path=payload_path,
        priority=priority,
        problem_id=problem_id,
        depends_on_tasks=depends_on_tasks or [],
    )


def test_init_db_creates_piece1_tables_columns_and_indexes(tmp_path: Path) -> None:
    db_path = tmp_path / "run.db"
    init_db(db_path)

    with task_db(db_path) as conn:
        task_columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(tasks)")
        }
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        indexes = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }

    assert {
        "updated_at",
        "dedupe_key",
        "status_reason",
        "superseded_by_task_id",
        "result_envelope_path",
    }.issubset(task_columns)
    assert {
        "task_dependencies",
        "task_subscriptions",
        "task_events",
        "task_claims",
        "user_input_requests",
        "value_axes",
    }.issubset(tables)
    assert {
        "idx_tasks_dedupe_active",
        "idx_tasks_updated",
        "idx_task_deps_task",
        "idx_task_deps_depends",
        "idx_task_subs_task",
        "idx_task_subs_scope",
        "idx_task_events_task",
        "idx_user_input_task",
        "idx_value_axes_scope_status",
    }.issubset(indexes)


def test_request_task_creates_task_and_returns_task_id(tmp_path: Path) -> None:
    db_path = tmp_path / "run.db"
    init_db(db_path)

    task_id = request_task(db_path, _make_task())

    assert task_id > 0
    rows = query_tasks(db_path)
    assert [row["id"] for row in rows] == [task_id]
    assert rows[0]["task_type"] == "test.task"


def test_request_task_deduplicates_active_tasks_by_exact_key(tmp_path: Path) -> None:
    db_path = tmp_path / "run.db"
    init_db(db_path)

    first = request_task(db_path, _make_task(payload_path="one.json"), dedupe_key="same")
    second = request_task(db_path, _make_task(payload_path="two.json"), dedupe_key="same")

    assert second == first
    assert len(query_tasks(db_path)) == 1


def test_request_task_writes_dependency_edges_and_blocks_until_satisfied(tmp_path: Path) -> None:
    db_path = tmp_path / "run.db"
    init_db(db_path)
    upstream_a = request_task(db_path, _make_task(task_type="upstream.a"))
    upstream_b = request_task(db_path, _make_task(task_type="upstream.b"))

    multi_dep = request_task(
        db_path,
        _make_task(task_type="downstream.multi"),
        depends_on_tasks=[upstream_a, upstream_b],
    )
    single_dep = request_task(
        db_path,
        _make_task(task_type="downstream.single"),
        depends_on_tasks=[upstream_a],
    )

    with task_db(db_path) as conn:
        edges = conn.execute(
            "SELECT task_id, depends_on_task_id FROM task_dependencies ORDER BY task_id, depends_on_task_id"
        ).fetchall()
        multi_status = conn.execute(
            "SELECT status FROM tasks WHERE id=?",
            (multi_dep,),
        ).fetchone()[0]
        single_status = conn.execute(
            "SELECT status FROM tasks WHERE id=?",
            (single_dep,),
        ).fetchone()[0]

    assert edges == [
        (multi_dep, upstream_a),
        (multi_dep, upstream_b),
        (single_dep, upstream_a),
    ]
    assert multi_status == "blocked"
    assert single_status == "blocked"


def test_claim_runnable_task_skips_unsatisfied_dependency_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "run.db"
    init_db(db_path)
    blocked_upstream = request_task(
        db_path,
        _make_task(task_type="upstream", priority="low"),
    )
    blocked = request_task(
        db_path,
        _make_task(task_type="blocked", priority="high"),
        depends_on_tasks=[blocked_upstream],
    )
    runnable = request_task(
        db_path,
        _make_task(task_type="runnable", priority="normal"),
    )

    claimed = claim_runnable_task(db_path, "dispatcher")

    assert claimed is not None
    assert claimed["id"] == str(runnable)
    with task_db(db_path) as conn:
        blocked_status = conn.execute(
            "SELECT status FROM tasks WHERE id=?",
            (blocked,),
        ).fetchone()[0]
    assert blocked_status == "blocked"


def test_claim_runnable_task_claims_when_all_dependencies_satisfied(tmp_path: Path) -> None:
    db_path = tmp_path / "run.db"
    init_db(db_path)
    upstream = request_task(db_path, _make_task(task_type="upstream"))
    downstream = request_task(
        db_path,
        _make_task(task_type="downstream"),
        depends_on_tasks=[upstream],
    )

    first = claim_runnable_task(db_path, "dispatcher")
    assert first is not None
    assert first["id"] == str(upstream)

    complete_task_with_result(db_path, upstream, output_path="upstream.out")

    second = claim_runnable_task(db_path, "dispatcher")
    assert second is not None
    assert second["id"] == str(downstream)


def test_complete_task_with_result_satisfies_downstream_dependencies(tmp_path: Path) -> None:
    db_path = tmp_path / "run.db"
    init_db(db_path)
    upstream = request_task(db_path, _make_task(task_type="upstream"))
    downstream = request_task(
        db_path,
        _make_task(task_type="downstream"),
        depends_on_tasks=[upstream],
    )

    claim_runnable_task(db_path, "dispatcher")
    complete_task_with_result(db_path, upstream, output_path="result.txt")

    with task_db(db_path) as conn:
        satisfied, satisfied_at = conn.execute(
            "SELECT satisfied, satisfied_at FROM task_dependencies "
            "WHERE task_id=? AND depends_on_task_id=?",
            (downstream, upstream),
        ).fetchone()
        downstream_status = conn.execute(
            "SELECT status FROM tasks WHERE id=?",
            (downstream,),
        ).fetchone()[0]

    assert satisfied == 1
    assert satisfied_at is not None
    assert downstream_status == "pending"


def test_complete_task_with_result_logs_event(tmp_path: Path) -> None:
    db_path = tmp_path / "run.db"
    init_db(db_path)
    task_id = request_task(db_path, _make_task())

    claim_runnable_task(db_path, "dispatcher")
    complete_task_with_result(db_path, task_id, output_path="result.txt")

    with task_db(db_path) as conn:
        event = conn.execute(
            "SELECT event_type, detail FROM task_events WHERE task_id=?",
            (task_id,),
        ).fetchone()

    assert event == ("completed", "result.txt")


def test_request_user_input_creates_request_row_and_sets_task_to_awaiting_input(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "run.db"
    init_db(db_path)
    task_id = request_task(db_path, _make_task(task_type="research.user_input"))

    request_user_input(
        db_path,
        task_id,
        "Need user confirmation",
        response_schema_json={"type": "object", "required": ["answer"]},
    )

    with task_db(db_path) as conn:
        request_row = conn.execute(
            "SELECT question, status FROM user_input_requests WHERE task_id=?",
            (task_id,),
        ).fetchone()
        task_status = conn.execute(
            "SELECT status FROM tasks WHERE id=?",
            (task_id,),
        ).fetchone()[0]

    assert request_row == ("Need user confirmation", "awaiting_input")
    assert task_status == "awaiting_input"


def test_answer_user_input_with_valid_response_unblocks_task(tmp_path: Path) -> None:
    db_path = tmp_path / "run.db"
    init_db(db_path)
    task_id = request_task(db_path, _make_task(task_type="research.user_input"))
    request_user_input(
        db_path,
        task_id,
        "Need user confirmation",
        response_schema_json={
            "type": "object",
            "required": ["answer"],
            "properties": {"answer": {"type": "string"}},
        },
    )

    assert answer_user_input(db_path, task_id, {"answer": "yes"}) is True

    with task_db(db_path) as conn:
        request_row = conn.execute(
            "SELECT status, response_json, answered_at FROM user_input_requests WHERE task_id=?",
            (task_id,),
        ).fetchone()
        task_status = conn.execute(
            "SELECT status FROM tasks WHERE id=?",
            (task_id,),
        ).fetchone()[0]

    assert request_row[0] == "answered"
    assert request_row[1] == '{"answer":"yes"}'
    assert request_row[2] is not None
    assert task_status == "pending"


def test_answer_user_input_with_invalid_response_keeps_task_blocked(tmp_path: Path) -> None:
    db_path = tmp_path / "run.db"
    init_db(db_path)
    task_id = request_task(db_path, _make_task(task_type="research.user_input"))
    request_user_input(
        db_path,
        task_id,
        "Need user confirmation",
        response_schema_json={
            "type": "object",
            "required": ["answer"],
            "properties": {"answer": {"type": "string"}},
        },
    )

    assert answer_user_input(db_path, task_id, {"answer": 7}) is False

    with task_db(db_path) as conn:
        request_status = conn.execute(
            "SELECT status FROM user_input_requests WHERE task_id=?",
            (task_id,),
        ).fetchone()[0]
        task_status = conn.execute(
            "SELECT status FROM tasks WHERE id=?",
            (task_id,),
        ).fetchone()[0]

    assert request_status == "awaiting_input"
    assert task_status == "awaiting_input"


def test_claim_runnable_task_does_not_claim_awaiting_input_tasks(tmp_path: Path) -> None:
    db_path = tmp_path / "run.db"
    init_db(db_path)
    task_id = request_task(db_path, _make_task(task_type="research.user_input"))
    request_user_input(db_path, task_id, "Need user confirmation")

    assert claim_runnable_task(db_path, "dispatcher") is None


def test_subscribe_to_task_creates_subscription_row(tmp_path: Path) -> None:
    db_path = tmp_path / "run.db"
    init_db(db_path)
    task_id = request_task(db_path, _make_task())

    subscription_id = subscribe_to_task(
        db_path,
        task_id,
        "scope.subscriber",
        callback_task_type="callback.task",
        callback_payload_path="callback.json",
    )

    assert subscription_id > 0
    with task_db(db_path) as conn:
        row = conn.execute(
            "SELECT subscriber_scope, callback_task_type, callback_payload_path "
            "FROM task_subscriptions WHERE id=?",
            (subscription_id,),
        ).fetchone()
    assert row == ("scope.subscriber", "callback.task", "callback.json")


def test_get_active_subscriptions_returns_active_subs_for_completed_task(tmp_path: Path) -> None:
    db_path = tmp_path / "run.db"
    init_db(db_path)
    task_id = request_task(db_path, _make_task())
    subscribe_to_task(db_path, task_id, "scope.one")
    subscribe_to_task(db_path, task_id, "scope.two")

    claim_runnable_task(db_path, "dispatcher")
    complete_task_with_result(db_path, task_id, output_path="done.txt")

    subscriptions = get_active_subscriptions(db_path, task_id)

    assert subscriptions == []


def test_query_tasks_filters_by_status_scope_and_type(tmp_path: Path) -> None:
    db_path = tmp_path / "run.db"
    init_db(db_path)
    running = request_task(
        db_path,
        _make_task(task_type="type.alpha", concern_scope="scope.keep"),
    )
    request_task(
        db_path,
        _make_task(task_type="type.beta", concern_scope="scope.keep"),
    )
    request_task(
        db_path,
        _make_task(task_type="type.alpha", concern_scope="scope.other"),
    )

    claim_runnable_task(db_path, "dispatcher")

    by_status = query_tasks(db_path, status="running")
    by_scope = query_tasks(db_path, concern_scope="scope.keep")
    by_type = query_tasks(db_path, task_type="type.alpha")

    assert [row["id"] for row in by_status] == [running]
    assert [row["concern_scope"] for row in by_scope] == [
        "scope.keep",
        "scope.keep",
    ]
    assert [row["task_type"] for row in by_type] == [
        "type.alpha",
        "type.alpha",
    ]


def test_claim_runnable_task_fails_closed_on_dependency_cycle(tmp_path: Path) -> None:
    db_path = tmp_path / "run.db"
    init_db(db_path)
    task_a = request_task(db_path, _make_task(task_type="task.a"))
    task_b = request_task(db_path, _make_task(task_type="task.b"))

    with task_db(db_path) as conn:
        conn.execute(
            "INSERT INTO task_dependencies(task_id, depends_on_task_id) VALUES(?, ?)",
            (task_a, task_b),
        )
        conn.execute(
            "INSERT INTO task_dependencies(task_id, depends_on_task_id) VALUES(?, ?)",
            (task_b, task_a),
        )
        conn.commit()

    assert claim_runnable_task(db_path, "dispatcher") is None


def test_fail_task_with_result_cascades_dependency_failures(tmp_path: Path) -> None:
    db_path = tmp_path / "run.db"
    init_db(db_path)
    first = request_task(db_path, _make_task(task_type="first", priority="high"))
    second = request_task(
        db_path,
        _make_task(
            task_type="second",
            priority="normal",
            depends_on_tasks=[first],
        ),
    )

    claim_runnable_task(db_path, "dispatcher")
    fail_task_with_result(db_path, first, error="boom")

    with task_db(db_path) as conn:
        status, reason, error = conn.execute(
            "SELECT status, status_reason, error FROM tasks WHERE id=?",
            (second,),
        ).fetchone()

    assert status == "failed"
    assert reason == "dependency_failed"
    assert error == f"dependency_failed:{first}"


def test_detect_dependency_starvation_logs_event_for_old_blocked_tasks(tmp_path: Path) -> None:
    db_path = tmp_path / "run.db"
    init_db(db_path)
    upstream = request_task(db_path, _make_task(task_type="upstream"))
    blocked = request_task(
        db_path,
        _make_task(task_type="blocked", depends_on_tasks=[upstream]),
    )

    with task_db(db_path) as conn:
        conn.execute(
            "UPDATE tasks SET updated_at='2000-01-01 00:00:00' WHERE id IN (?, ?)",
            (upstream, blocked),
        )
        conn.commit()

    starved = detect_dependency_starvation(db_path, threshold_seconds=1)

    assert starved == [blocked]
    with task_db(db_path) as conn:
        event = conn.execute(
            "SELECT event_type FROM task_events WHERE task_id=? ORDER BY id DESC LIMIT 1",
            (blocked,),
        ).fetchone()
    assert event == ("dependency_starvation",)


def test_record_value_axis_deduplicates_by_scope_and_name(tmp_path: Path) -> None:
    db_path = tmp_path / "run.db"
    init_db(db_path)

    first = record_value_axis(db_path, "section-01", "Latency")
    second = record_value_axis(db_path, "section-01", "Latency")

    assert second == first
    with task_db(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM value_axes").fetchone()[0]
    assert count == 1


def test_get_value_axes_filters_by_status(tmp_path: Path) -> None:
    db_path = tmp_path / "run.db"
    init_db(db_path)
    active_id = record_value_axis(db_path, "section-01", "Latency")
    superseded_id = record_value_axis(db_path, "section-01", "Cost")
    update_value_axis_status(db_path, superseded_id, "superseded")

    active_axes = get_value_axes(db_path, "section-01", status="active")
    superseded_axes = get_value_axes(db_path, "section-01", status="superseded")

    assert [axis["id"] for axis in active_axes] == [active_id]
    assert [axis["id"] for axis in superseded_axes] == [superseded_id]


def test_complete_task_with_result_persists_new_value_axes(tmp_path: Path) -> None:
    db_path = tmp_path / "run.db"
    init_db(db_path)
    task_id = request_task(
        db_path,
        _make_task(task_type="research.synthesis", concern_scope="section-01"),
    )

    claim_runnable_task(db_path, "dispatcher")
    complete_task_with_result(
        db_path,
        task_id,
        result_envelope=TaskResultEnvelope(
            task_id=task_id,
            task_type="research.synthesis",
            status="complete",
            output_path="result.json",
            new_value_axes=["Latency", "Reliability"],
        ),
    )

    axes = get_value_axes(db_path, "section-01", status="active")

    assert [axis["axis_name"] for axis in axes] == ["Latency", "Reliability"]


def test_complete_task_with_new_value_axis_submits_section_assess_once(tmp_path: Path) -> None:
    db_path = tmp_path / "run.db"
    init_db(db_path)
    task_id = request_task(
        db_path,
        _make_task(task_type="research.synthesis", concern_scope="section-01"),
    )

    claim_runnable_task(db_path, "dispatcher")
    complete_task_with_result(
        db_path,
        task_id,
        result_envelope=TaskResultEnvelope(
            task_id=task_id,
            task_type="research.synthesis",
            status="complete",
            output_path="result.json",
            new_value_axes=["Latency"],
        ),
    )

    assess_tasks = query_tasks(
        db_path,
        concern_scope="section-01",
        task_type="section.assess",
    )
    assert len(assess_tasks) == 1
    assert detect_value_expansion(db_path, "section-01") == []

    task_id_2 = request_task(
        db_path,
        _make_task(task_type="research.synthesis", concern_scope="section-01"),
    )
    with task_db(db_path) as conn:
        conn.execute(
            "UPDATE tasks SET status='running', claimed_by='dispatcher' WHERE id=?",
            (task_id_2,),
        )
        conn.commit()
    complete_task_with_result(
        db_path,
        task_id_2,
        result_envelope=TaskResultEnvelope(
            task_id=task_id_2,
            task_type="research.synthesis",
            status="complete",
            output_path="result-2.json",
            new_value_axes=["Security"],
        ),
    )

    assess_tasks = query_tasks(
        db_path,
        concern_scope="section-01",
        task_type="section.assess",
    )
    assert len(assess_tasks) == 1
