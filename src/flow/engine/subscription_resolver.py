"""Dependency and subscription resolution for completed tasks."""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

from orchestrator.path_registry import PathRegistry


class SubscriptionResolver:
    """Resolves subscriptions and dependencies on task completion."""

    def __init__(self, artifact_io) -> None:
        self._artifact_io = artifact_io

    def resolve(
        self,
        db_path,
        task_id: int,
        planspace: Path,
        result_envelope,
    ) -> bool:
        from flow.service.task_db_client import (
            _append_task_event,
            _detect_value_expansion_in_txn,
            _record_value_axis_in_txn,
            _request_task_in_txn,
            _section_number_from_scope,
            _write_value_axes_artifact,
            task_db,
        )

        tid = int(task_id)
        implementation_feedback_detected = False
        with task_db(db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._satisfy_dependencies(conn, tid, _append_task_event)
            self._resolve_subscriptions(
                conn,
                tid,
                planspace,
                result_envelope,
                _append_task_event,
                _request_task_in_txn,
            )
            implementation_feedback_detected = self._record_value_axes(
                conn,
                tid,
                planspace,
                result_envelope,
                _append_task_event,
                _record_value_axis_in_txn,
                _write_value_axes_artifact,
                _detect_value_expansion_in_txn,
                _request_task_in_txn,
                _section_number_from_scope,
            )
            conn.commit()
        return implementation_feedback_detected

    def _satisfy_dependencies(self, conn, task_id: int, append_task_event) -> None:
        rows = conn.execute(
            """SELECT id, task_id
               FROM task_dependencies
               WHERE depends_on_task_id=? AND satisfied=0
               ORDER BY id ASC""",
            (task_id,),
        ).fetchall()
        for dependency_row_id, downstream_task_id in rows:
            conn.execute(
                """UPDATE task_dependencies
                   SET satisfied=1, satisfied_at=datetime('now')
                   WHERE id=?""",
                (int(dependency_row_id),),
            )
            append_task_event(
                conn,
                int(downstream_task_id),
                "dependency_satisfied",
                f"depends_on:{task_id}",
            )
            waiting = conn.execute(
                """SELECT 1
                   FROM task_dependencies
                   WHERE task_id=? AND satisfied=0
                   LIMIT 1""",
                (int(downstream_task_id),),
            ).fetchone()
            if waiting is None:
                conn.execute(
                    """UPDATE tasks
                       SET status='pending',
                           status_reason=NULL,
                           updated_at=datetime('now')
                       WHERE id=? AND status='blocked'""",
                    (int(downstream_task_id),),
                )

    def _resolve_subscriptions(
        self,
        conn,
        task_id: int,
        planspace: Path,
        result_envelope,
        append_task_event,
        request_task_in_txn,
    ) -> None:
        task_row = conn.execute(
            "SELECT result_envelope_path FROM tasks WHERE id=?",
            (task_id,),
        ).fetchone()
        result_envelope_path = (
            str(PathRegistry(planspace).task_result_envelope(task_id))
            if task_row is None or not task_row[0]
            else str(task_row[0])
        )
        subscriptions = conn.execute(
            """SELECT id, subscriber_scope, callback_task_type,
                      callback_payload_path, verification_mode
               FROM task_subscriptions
               WHERE task_id=? AND status='active'
               ORDER BY id ASC""",
            (task_id,),
        ).fetchall()
        for (
            subscription_id,
            subscriber_scope,
            callback_task_type,
            callback_payload_path,
            verification_mode,
        ) in subscriptions:
            if str(verification_mode) == "validated_user_input":
                continue
            if callback_task_type:
                try:
                    payload_path = (
                        str(callback_payload_path)
                        if callback_payload_path
                        else result_envelope_path
                    )
                    callback_task_id = request_task_in_txn(
                        conn,
                        SimpleNamespace(
                            task_type=str(callback_task_type),
                            submitted_by=f"task-subscription:{task_id}",
                            concern_scope=str(subscriber_scope),
                            payload_path=payload_path,
                            priority="normal",
                        ),
                    )
                except Exception as exc:  # noqa: BLE001
                    conn.execute(
                        """UPDATE task_subscriptions
                           SET status='failed',
                               last_error=?,
                               notified_at=datetime('now')
                           WHERE id=?""",
                        (str(exc), int(subscription_id)),
                    )
                    append_task_event(
                        conn,
                        task_id,
                        "subscription_notification_failed",
                        f"{subscription_id}:{exc}",
                    )
                    continue
                detail = f"{subscription_id}:{callback_task_id}"
            else:
                detail = str(subscription_id)

            conn.execute(
                """UPDATE task_subscriptions
                   SET status='consumed',
                       last_error=NULL,
                       notified_at=datetime('now'),
                       consumed_at=datetime('now')
                   WHERE id=?""",
                (int(subscription_id),),
            )
            append_task_event(conn, task_id, "subscription_notified", detail)

    def _record_value_axes(
        self,
        conn,
        task_id: int,
        planspace: Path,
        result_envelope,
        append_task_event,
        record_value_axis_in_txn,
        write_value_axes_artifact,
        detect_value_expansion_in_txn,
        request_task_in_txn,
        section_number_from_scope,
    ) -> bool:
        task_row = conn.execute(
            "SELECT concern_scope FROM tasks WHERE id=?",
            (task_id,),
        ).fetchone()
        concern_scope = (
            str(task_row[0]) if task_row is not None and task_row[0] is not None else ""
        )
        if not concern_scope:
            return False

        new_value_axes = [
            str(axis).strip()
            for axis in getattr(result_envelope, "new_value_axes", [])
            if str(axis).strip()
        ]
        if not new_value_axes:
            return False

        recorded_axes: list[str] = []
        for axis_name in new_value_axes:
            _, created = record_value_axis_in_txn(
                conn,
                concern_scope,
                axis_name,
                source_task_id=task_id,
            )
            if created:
                recorded_axes.append(axis_name)
                append_task_event(conn, task_id, "value_axis_added", axis_name)

        if not recorded_axes:
            return False

        write_value_axes_artifact(
            conn,
            planspace,
            concern_scope,
            triggered_axes=recorded_axes,
        )
        section_number = section_number_from_scope(concern_scope)
        impl_feedback_written = False
        if section_number is not None:
            novel_axes = self._find_novel_axes(
                planspace,
                section_number,
                recorded_axes,
            )
            if novel_axes:
                value_axes_path = PathRegistry(planspace).value_axes_artifact(section_number)
                self._artifact_io.write_json(
                    PathRegistry(planspace).impl_feedback_surfaces(section_number),
                    {
                        "problem_surfaces": [
                            {
                                "kind": "new_axis",
                                "title": axis_name,
                                "description": (
                                    "New value axis discovered during implementation "
                                    f"of task {task_id}"
                                ),
                                "evidence": (
                                    f"Discovered via task result envelope from task {task_id}. "
                                    f"See value-axes artifact at {value_axes_path}."
                                ),
                            }
                            for axis_name in novel_axes
                        ],
                        "philosophy_surfaces": [],
                    },
                )
                append_task_event(
                    conn,
                    task_id,
                    "impl_feedback_surface_written",
                    json.dumps(novel_axes, sort_keys=True),
                )
                impl_feedback_written = True
        expanded_axes = detect_value_expansion_in_txn(conn, concern_scope)
        if not expanded_axes:
            return impl_feedback_written
        if section_number is None:
            return impl_feedback_written
        assess_task_id = request_task_in_txn(
            conn,
            SimpleNamespace(
                task_type="section.assess",
                submitted_by=f"value-axis:{section_number}",
                concern_scope=concern_scope,
                payload_path=str(PathRegistry(planspace).section_spec(section_number)),
                priority="normal",
                problem_id=f"value-expansion-{section_number}",
            ),
            dedupe_key=json.dumps(
                {
                    "task_type": "section.assess",
                    "concern_scope": concern_scope,
                    "reason": "value_expansion",
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
        append_task_event(
            conn,
            assess_task_id,
            "value_expansion_assess_requested",
            json.dumps(recorded_axes, sort_keys=True),
        )
        return impl_feedback_written

    def _find_novel_axes(
        self,
        planspace: Path,
        section_number: str,
        recorded_axes: list[str],
    ) -> list[str]:
        covered_titles = self._covered_axis_titles(planspace, section_number)
        novel_axes: list[str] = []
        for axis_name in recorded_axes:
            if self._normalize_title(axis_name) in covered_titles:
                continue
            novel_axes.append(axis_name)
        return novel_axes

    def _covered_axis_titles(
        self,
        planspace: Path,
        section_number: str,
    ) -> set[str]:
        paths = PathRegistry(planspace)
        intent_dir = paths.intent_section_dir(section_number)
        covered = self._problem_alignment_titles(intent_dir / "problem-alignment.md")
        covered.update(
            self._surface_registry_titles(intent_dir / "surface-registry.json"),
        )
        return covered

    def _problem_alignment_titles(self, alignment_path: Path) -> set[str]:
        text = self._artifact_io.read_if_exists(alignment_path)
        titles: set[str] = set()
        for match in re.finditer(r"^\s{0,3}#{1,6}\s+(.+?)\s*$", text, re.MULTILINE):
            normalized = self._normalize_title(match.group(1))
            if normalized:
                titles.add(normalized)
        return titles

    def _surface_registry_titles(self, registry_path: Path) -> set[str]:
        data = self._artifact_io.read_json(registry_path)
        if not isinstance(data, dict):
            return set()
        return self._extract_named_titles(data)

    def _extract_named_titles(self, value: object) -> set[str]:
        titles: set[str] = set()
        if isinstance(value, dict):
            for key, nested in value.items():
                if key in {"title", "notes"}:
                    normalized = self._normalize_title(nested)
                    if normalized:
                        titles.add(normalized)
                titles.update(self._extract_named_titles(nested))
        elif isinstance(value, list):
            for item in value:
                titles.update(self._extract_named_titles(item))
        return titles

    @staticmethod
    def _normalize_title(value: object) -> str:
        text = str(value).strip().lower()
        return re.sub(r"\s+", " ", text)
