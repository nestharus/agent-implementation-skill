"""Projection of raw task output into structured result envelopes."""

from __future__ import annotations

import json
from pathlib import Path

from flow.types.result_envelope import TaskResultEnvelope


class TaskResultProjector:
    """Projects raw task output into structured TaskResultEnvelope."""

    def __init__(self, artifact_io) -> None:
        self._artifact_io = artifact_io

    def project(
        self,
        task: dict,
        output_path: str | None,
        planspace: Path,
    ) -> TaskResultEnvelope:
        """Read task output and build a structured envelope."""
        task_id = int(task.get("id") or task.get("task_id") or 0)
        task_type = str(task.get("task_type") or "")
        status = str(task.get("status") or "")
        task_error = self._normalize_optional_string(task.get("error"))
        resolved_output = output_path or self._normalize_optional_string(
            task.get("output_path"),
        )
        output_file = self._resolve_output_file(planspace, resolved_output)
        if output_file is None:
            return self._empty_envelope(
                task_id=task_id,
                task_type=task_type,
                status=status,
                output_path=resolved_output,
                error=task_error,
            )

        output_exists = output_file.exists()
        projected = self._project_json_output(
            task_id=task_id,
            task_type=task_type,
            status=status,
            output_path=resolved_output,
            output_file=output_file,
            task_error=task_error,
        )
        if projected is not None:
            return projected

        if output_exists and output_file.suffix.lower() == ".json":
            return self._empty_envelope(
                task_id=task_id,
                task_type=task_type,
                status=status,
                output_path=resolved_output,
                error=task_error or "malformed task output",
            )

        raw_output = self._artifact_io.read_if_exists(output_file)
        if task_type == "section.assess" and raw_output:
            return self._project_alignment_verdict(
                task_id=task_id,
                task_type=task_type,
                status=status,
                output_path=resolved_output,
                raw_output=raw_output,
                task_error=task_error,
            )

        return self._empty_envelope(
            task_id=task_id,
            task_type=task_type,
            status=status,
            output_path=resolved_output,
            error=task_error,
        )

    def _project_json_output(
        self,
        *,
        task_id: int,
        task_type: str,
        status: str,
        output_path: str | None,
        output_file: Path,
        task_error: str | None,
    ) -> TaskResultEnvelope | None:
        if output_file.suffix.lower() != ".json":
            return None
        data = self._artifact_io.read_json(output_file)
        if not isinstance(data, dict):
            return None
        unresolved = self._normalize_string_list(data.get("unresolved_problems"))
        new_value_axes = self._normalize_string_list(data.get("new_value_axes"))
        scope_expansions = self._normalize_string_list(data.get("scope_expansions"))
        partial_solutions = self._normalize_partial_solutions(
            data.get("partial_solutions"),
        )
        return TaskResultEnvelope(
            task_id=task_id,
            task_type=task_type,
            status=status,
            output_path=output_path,
            unresolved_problems=unresolved,
            new_value_axes=new_value_axes,
            partial_solutions=partial_solutions,
            scope_expansions=scope_expansions,
            error=(
                self._normalize_optional_string(data.get("error"))
                or task_error
            ),
        )

    def _project_alignment_verdict(
        self,
        *,
        task_id: int,
        task_type: str,
        status: str,
        output_path: str | None,
        raw_output: str,
        task_error: str | None,
    ) -> TaskResultEnvelope:
        from staleness.helpers.verdict_parsers import parse_alignment_verdict

        verdict = parse_alignment_verdict(raw_output)
        unresolved: list[str] = []
        if isinstance(verdict, dict):
            problems = verdict.get("problems")
            if isinstance(problems, list):
                unresolved = [
                    str(problem).strip()
                    for problem in problems
                    if str(problem).strip()
                ]
            elif isinstance(problems, str) and problems.strip():
                unresolved = [problems.strip()]
        return TaskResultEnvelope(
            task_id=task_id,
            task_type=task_type,
            status=status,
            output_path=output_path,
            unresolved_problems=unresolved,
            new_value_axes=[],
            partial_solutions=[],
            scope_expansions=[],
            error=task_error,
        )

    @staticmethod
    def _resolve_output_file(planspace: Path, output_path: str | None) -> Path | None:
        if not output_path:
            return None
        output_file = Path(output_path)
        if not output_file.is_absolute():
            output_file = planspace / output_file
        return output_file

    @staticmethod
    def _normalize_optional_string(value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _normalize_string_list(value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

    @staticmethod
    def _normalize_partial_solutions(value: object) -> list[dict]:
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, dict)]

    @staticmethod
    def _empty_envelope(
        *,
        task_id: int,
        task_type: str,
        status: str,
        output_path: str | None,
        error: str | None,
    ) -> TaskResultEnvelope:
        return TaskResultEnvelope(
            task_id=task_id,
            task_type=task_type,
            status=status,
            output_path=output_path,
            unresolved_problems=[],
            new_value_axes=[],
            partial_solutions=[],
            scope_expansions=[],
            error=error,
        )
