"""Flow submission helpers shared by ingestion and reconciliation."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

from flow.repository.catalog import resolve_chain_ref
from flow.repository.flow_context_store import (
    continuation_relpath,
    flow_context_relpath,
    result_manifest_relpath,
)
from flow.service.task_db_client import load_task, request_user_input
from flow.repository.gate_repository import insert_gate_record
from flow.types.context import (
    FlowEnvelope,
    FlowTask,
    new_chain_id,
    new_flow_id,
    new_gate_id,
    new_instance_id,
)
from flow.types.routing import Task, request_task, update_task_flow_paths
from flow.types.schema import BranchSpec, GateSpec, TaskSpec

if TYPE_CHECKING:
    from containers import FreshnessService
    from flow.repository.flow_context_store import FlowContextStore


class FlowSubmitter:
    def __init__(
        self,
        freshness: FreshnessService,
        flow_context_store: FlowContextStore,
    ) -> None:
        self._freshness = freshness
        self._flow_context_store = flow_context_store

    def _freshness_from_steps(self, steps: list[TaskSpec], planspace: Path) -> str | None:
        """Derive a freshness token from the first step with a section scope."""
        for step in steps:
            if not step.concern_scope:
                continue
            match = re.match(r"^section-(\d+)$", step.concern_scope)
            if match:
                return self._freshness.compute(planspace, match.group(1))
        return None

    @staticmethod
    def _user_input_spec_from_payload(payload_path: str) -> tuple[str, object | None]:
        prompt_path = Path(payload_path)
        if not prompt_path.name.endswith("-prompt.md"):
            raise ValueError(
                f"research.user_input payload must end with -prompt.md: {payload_path}"
            )
        spec_path = prompt_path.with_name(
            prompt_path.name.replace("-prompt.md", "-spec.json")
        )
        data = json.loads(spec_path.read_text(encoding="utf-8"))
        question_text = str(data.get("question_text") or data.get("question") or "").strip()
        if not question_text:
            raise ValueError(f"research.user_input spec missing question_text: {spec_path}")
        return question_text, data.get("response_schema_json")

    def submit_chain(
        self,
        env: FlowEnvelope,
        steps: list[TaskSpec],
        *,
        chain_id: str | None = None,
        dedup_key: tuple[str, str] | None = None,
        initial_dependency_task_id: int | None = None,
    ) -> list[int]:
        """Submit a linear chain of tasks.

        When *dedup_key* is ``(task_type, flow_id)``, the first step's
        INSERT is guarded by an atomic duplicate check inside the same
        SQLite connection.  If a matching active task already exists the
        entire chain is skipped and an empty list is returned.
        """
        if not steps:
            return []

        flow_id = env.flow_id or new_flow_id()
        chain_id = chain_id or new_chain_id()
        refs = list(env.origin_refs)

        task_ids: list[int] = []
        previous_task_id: int | None = initial_dependency_task_id

        for i, step in enumerate(steps):
            instance_id = new_instance_id()
            # Apply dedup_key only to the first step in the chain.
            step_dedup = dedup_key if i == 0 else None
            tid = request_task(
                env.db_path,
                Task.from_spec(
                    step, env.submitted_by,
                    depends_on_tasks=(
                        [previous_task_id] if previous_task_id is not None else []
                    ),
                    instance_id=instance_id,
                    flow_id=flow_id,
                    chain_id=chain_id,
                    declared_by_task_id=env.declared_by_task_id,
                    freshness_token=env.freshness_token,
                ),
                dedupe_key=step_dedup,
            )
            if i == 0 and step_dedup is not None:
                stored = load_task(env.db_path, tid)
                if stored is None or stored.get("instance_id") != instance_id:
                    return []

            if step.task_type == "research.user_input":
                question_text, response_schema_json = self._user_input_spec_from_payload(
                    step.payload_path
                )
                request_user_input(
                    env.db_path,
                    tid,
                    question_text,
                    response_schema_json=response_schema_json,
                )

            ctx_path = flow_context_relpath(tid)
            cont_path = continuation_relpath(tid)
            res_path = result_manifest_relpath(tid)

            update_task_flow_paths(env.db_path, tid, ctx_path, cont_path, res_path)

            if env.planspace is not None:
                self._flow_context_store.write_flow_context(
                    planspace=env.planspace,
                    task=FlowTask(
                        task_id=tid,
                        instance_id=instance_id,
                        flow_id=flow_id,
                        chain_id=chain_id,
                        task_type=step.task_type,
                        declared_by_task_id=env.declared_by_task_id,
                        trigger_gate_id=None,
                    ),
                    origin_refs=refs,
                    previous_task_id=previous_task_id,
                )

            task_ids.append(tid)
            previous_task_id = tid

        return task_ids

    def submit_fanout(
        self,
        env: FlowEnvelope,
        branches: list[BranchSpec],
        *,
        gate: GateSpec | None = None,
        dedup_flow_id: str | None = None,
    ) -> str | None:
        """Submit parallel branches, optionally under a convergence gate.

        When *dedup_flow_id* is set, each branch's first step is
        atomically dedup-checked against ``(task_type, dedup_flow_id)``
        so concurrent workers cannot insert duplicate branches.
        """
        if not branches:
            return None

        from dataclasses import replace as _replace

        flow_id = env.flow_id or new_flow_id()
        gate_id: str | None = None
        if gate is not None:
            gate_id = new_gate_id()

        branch_info: list[tuple[str, int, str]] = []

        for branch in branches:
            child_chain_id = new_chain_id()

            if branch.chain_ref:
                steps = resolve_chain_ref(branch.chain_ref, branch.args, list(env.origin_refs))
            else:
                steps = branch.steps

            # Build per-branch dedup_key from the first step's task_type.
            branch_dedup: tuple[str, str] | None = None
            if dedup_flow_id is not None and steps:
                branch_dedup = (steps[0].task_type, dedup_flow_id)

            branch_freshness = env.freshness_token
            if branch_freshness is None and env.planspace is not None:
                branch_freshness = self._freshness_from_steps(steps, env.planspace)

            branch_env = _replace(env, flow_id=flow_id, freshness_token=branch_freshness)
            task_ids = self.submit_chain(
                branch_env,
                steps,
                chain_id=child_chain_id,
                dedup_key=branch_dedup,
            )

            if task_ids:
                branch_info.append((child_chain_id, task_ids[-1], branch.label))

        if gate_id is not None and branch_info:
            insert_gate_record(
                env.db_path, gate_id, flow_id, env.declared_by_task_id,
                gate, branch_info,
            )

        return gate_id
