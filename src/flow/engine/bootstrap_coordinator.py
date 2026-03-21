"""Bootstrap-specific completion coordination."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from orchestrator.path_registry import PathRegistry
from flow.service.task_db_client import log_bootstrap_stage, log_event, task_db
from flow.types.context import FlowEnvelope
from staleness.helpers.content_hasher import file_hash

logger = logging.getLogger(__name__)

_JOIN = "__join__"


class BootstrapCoordinator:
    """Handles bootstrap task chain follow-on logic."""

    _GLOBAL_FOLLOW_ON: dict[str, str | list[str] | tuple[str, str]] = {
        "bootstrap.classify_entry": ["bootstrap.extract_problems", "bootstrap.extract_values"],
        "bootstrap.extract_problems": "bootstrap.explore_problems",
        "bootstrap.extract_values": "bootstrap.explore_values",
        "bootstrap.explore_problems": (_JOIN, "bootstrap.confirm_understanding"),
        "bootstrap.explore_values": (_JOIN, "bootstrap.confirm_understanding"),
        "bootstrap.decompose": "bootstrap.align_proposal",
        "bootstrap.expand_proposal": "bootstrap.align_proposal",
        "bootstrap.explore_factors": "bootstrap.align_proposal",
        "bootstrap.explore_sections": "bootstrap.discover_substrate",
    }

    _JOIN_SIBLINGS: dict[str, str] = {
        "bootstrap.explore_problems": "bootstrap.explore_values",
        "bootstrap.explore_values": "bootstrap.explore_problems",
    }

    _HIERARCHICAL_MODULE_THRESHOLD = 2
    _CONFIRM_UNDERSTANDING_SIGNAL_REL = "artifacts/signals/confirm-understanding-signal.json"
    _USER_RESPONSE_REL = "artifacts/global/user-response.json"
    _EXPANSION_LOG_REL = "artifacts/global/expansion-log.json"
    _EXPANSION_PROPOSAL_HASH_REL = "artifacts/global/expansion-proposal.hash"
    _USER_RESPONSE_REQUIRED_KEYS = frozenset({
        "confirmed_problems",
        "corrected_problems",
        "new_problems",
        "confirmed_values",
        "corrected_values",
        "new_context",
    })
    _BOOTSTRAP_ARTIFACT_PATHS: dict[str, str] = {
        "bootstrap.assess_reliability": "artifacts/global/reliability-assessment.json",
        "bootstrap.align_proposal": "artifacts/global/proposal-alignment.json",
    }

    def __init__(self, artifact_io, flow_submitter) -> None:
        self._artifact_io = artifact_io
        self._flow_submitter = flow_submitter

    def handle_completion(
        self,
        task: dict,
        db_path: Path,
        planspace: Path | None,
    ) -> bool:
        task_type = str(task.get("task_type") or "")
        if task_type == "scan.codemap_synthesize":
            self.handle_codemap_synthesize_complete(task, db_path, planspace)
            return True
        if not task_type.startswith("bootstrap."):
            return False

        flow_id = task.get("flow_id") or ""
        log_event(
            db_path,
            kind="global_task_complete",
            tag=task_type,
            body=json.dumps({"task_id": task.get("id"), "flow_id": flow_id}),
        )

        if task_type == "bootstrap.assess_reliability":
            recommendation = self._read_global_output_field(
                planspace, task_type, "recommendation",
            )
            follow_on = (
                "bootstrap.decompose"
                if recommendation == "decompose"
                else "bootstrap.align_proposal"
            )
            self._submit_global_follow_on(db_path, planspace, task, follow_on)
            return True

        if task_type == "bootstrap.align_proposal":
            aligned = self._read_global_output_field(planspace, task_type, "aligned")
            if aligned is True or aligned == "true":
                self._clear_expansion_proposal_hash(planspace)
                self._submit_global_follow_on(
                    db_path, planspace, task, "bootstrap.build_codemap",
                )
                return True
            follow_on = self._resolve_misaligned_follow_on(planspace)
            self._submit_global_follow_on(db_path, planspace, task, follow_on)
            return True

        if task_type == "bootstrap.confirm_understanding":
            if planspace is not None:
                signal_path = planspace / self._CONFIRM_UNDERSTANDING_SIGNAL_REL
                signal = self._artifact_io.read_json(signal_path)
                if isinstance(signal, dict) and signal.get("state") == "NEED_DECISION":
                    log_event(
                        db_path,
                        kind="bootstrap_gate",
                        tag="confirm_understanding",
                        body=json.dumps({
                            "gate": "awaiting_user_response",
                            "flow_id": flow_id,
                        }),
                    )
                    self._submit_global_follow_on(
                        db_path,
                        planspace,
                        task,
                        "bootstrap.interpret_response",
                    )
                    return True
            self._submit_global_follow_on(
                db_path,
                planspace,
                task,
                "bootstrap.assess_reliability",
            )
            return True

        if task_type == "bootstrap.interpret_response":
            if planspace is not None:
                response_path = planspace / self._USER_RESPONSE_REL
                if not self._is_valid_user_response(response_path):
                    malformed_dest = self._artifact_io.rename_malformed(response_path)
                    log_event(
                        db_path,
                        kind="interpret_response_malformed",
                        tag="bootstrap.interpret_response",
                        body=json.dumps({
                            "flow_id": flow_id,
                            "malformed_path": str(malformed_dest),
                        }),
                    )
                    logger.warning(
                        "bootstrap.interpret_response produced malformed "
                        "user-response.json (flow_id=%s); preserved at %s",
                        flow_id,
                        malformed_dest,
                    )
                    return True
            self._submit_global_follow_on(
                db_path,
                planspace,
                task,
                "bootstrap.assess_reliability",
            )
            return True

        if task_type == "bootstrap.build_codemap":
            self._handle_build_codemap_complete(db_path, planspace, task)
            return True

        if task_type == "bootstrap.discover_substrate":
            self._initialize_section_states_from_artifacts(db_path, planspace)
            self._seed_section_codemap_fragments(planspace)
            log_bootstrap_stage(db_path, "bootstrap", "completed")
            log_event(
                db_path,
                kind="global_bootstrap_complete",
                tag="discover_substrate",
                body=json.dumps({"flow_id": flow_id}),
            )
            return True

        follow_on = self._GLOBAL_FOLLOW_ON.get(task_type)
        if follow_on is None:
            return True
        if isinstance(follow_on, list):
            self._submit_global_fanout(db_path, planspace, task, follow_on)
            return True
        if isinstance(follow_on, tuple) and follow_on[0] == _JOIN:
            sibling_type = self._JOIN_SIBLINGS.get(task_type)
            target_type = follow_on[1]
            if sibling_type and not self._is_sibling_global_task_complete(
                db_path,
                sibling_type,
                flow_id,
            ):
                logger.info(
                    "bootstrap join: %s complete but sibling %s not yet done, deferring %s",
                    task_type,
                    sibling_type,
                    target_type,
                )
                return True
            self._submit_global_follow_on(db_path, planspace, task, target_type)
            return True
        if isinstance(follow_on, str):
            self._submit_global_follow_on(db_path, planspace, task, follow_on)
        return True

    def handle_codemap_synthesize_complete(
        self,
        task: dict,
        db_path: Path,
        planspace: Path | None,
    ) -> None:
        codemap_valid = False
        if planspace is not None:
            codemap_path = PathRegistry(planspace).codemap()
            codemap_valid = codemap_path.is_file() and codemap_path.stat().st_size > 0
        if codemap_valid:
            log_bootstrap_stage(db_path, "hierarchical_codemap", "completed")
        else:
            log_bootstrap_stage(
                db_path,
                "hierarchical_codemap_fallback",
                "completed",
                error="synthesis produced empty or missing codemap",
            )
        bootstrap_task = {
            "id": task.get("id"),
            "flow_id": task.get("flow_id") or "",
            "payload_path": task.get("payload_path") or "",
        }
        self._submit_global_follow_on(
            db_path,
            planspace,
            bootstrap_task,
            "bootstrap.explore_sections",
        )

    def handle_codemap_synthesize_failed(
        self,
        task: dict,
        db_path: Path,
        planspace: Path | None,
    ) -> None:
        log_bootstrap_stage(
            db_path,
            "hierarchical_codemap_fallback",
            "failed",
            error="codemap synthesis task failed",
        )
        bootstrap_task = {
            "id": task.get("id"),
            "flow_id": task.get("flow_id") or "",
            "payload_path": task.get("payload_path") or "",
        }
        self._submit_global_follow_on(
            db_path,
            planspace,
            bootstrap_task,
            "bootstrap.explore_sections",
        )

    @staticmethod
    def _is_sibling_global_task_complete(
        db_path: Path,
        sibling_task_type: str,
        flow_id: str,
    ) -> bool:
        with task_db(db_path) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM tasks "
                "WHERE task_type = ? AND flow_id = ? AND status = 'complete'",
                (sibling_task_type, flow_id),
            ).fetchone()
            return bool(row and row[0] > 0)

    def _is_valid_user_response(self, response_path: Path) -> bool:
        data = self._artifact_io.read_json(response_path)
        if not isinstance(data, dict):
            return False
        return self._USER_RESPONSE_REQUIRED_KEYS.issubset(data.keys())

    def _resolve_misaligned_follow_on(self, planspace: Path | None) -> str:
        if planspace is None:
            return "bootstrap.expand_proposal"

        if self._expansion_log_has_no_expansions(planspace):
            logger.info(
                "bootstrap.align_proposal: latest expansion produced no material "
                "changes; proceeding to build_codemap",
            )
            self._clear_expansion_proposal_hash(planspace)
            return "bootstrap.build_codemap"

        if self._proposal_hash_unchanged_since_last_expansion(planspace):
            logger.info(
                "bootstrap.align_proposal: proposal hash unchanged after expansion; "
                "proceeding to build_codemap",
            )
            self._clear_expansion_proposal_hash(planspace)
            return "bootstrap.build_codemap"

        self._record_expansion_proposal_hash(planspace)
        return "bootstrap.expand_proposal"

    def _expansion_log_has_no_expansions(self, planspace: Path) -> bool:
        data = self._artifact_io.read_json(planspace / self._EXPANSION_LOG_REL)
        if not isinstance(data, dict):
            return False
        expansions = data.get("expansions")
        return isinstance(expansions, list) and not expansions

    def _proposal_hash_unchanged_since_last_expansion(self, planspace: Path) -> bool:
        hash_path = planspace / self._EXPANSION_PROPOSAL_HASH_REL
        if not hash_path.exists():
            return False
        previous_hash = hash_path.read_text(encoding="utf-8").strip()
        current_hash = file_hash(PathRegistry(planspace).global_proposal())
        return bool(previous_hash) and previous_hash == current_hash

    def _record_expansion_proposal_hash(self, planspace: Path) -> None:
        hash_path = planspace / self._EXPANSION_PROPOSAL_HASH_REL
        hash_path.parent.mkdir(parents=True, exist_ok=True)
        hash_path.write_text(
            file_hash(PathRegistry(planspace).global_proposal()),
            encoding="utf-8",
        )

    @staticmethod
    def _clear_expansion_proposal_hash(planspace: Path | None) -> None:
        if planspace is None:
            return
        hash_path = planspace / BootstrapCoordinator._EXPANSION_PROPOSAL_HASH_REL
        if hash_path.exists():
            hash_path.unlink()

    def _submit_global_follow_on(
        self,
        db_path: Path,
        planspace: Path | None,
        task: dict,
        follow_on_type: str,
    ) -> None:
        from flow.types.schema import TaskSpec

        flow_id = task.get("flow_id") or ""
        env = FlowEnvelope(
            db_path=db_path,
            submitted_by="reconciler",
            flow_id=flow_id,
            declared_by_task_id=int(task["id"]),
            origin_refs=[],
            planspace=planspace,
        )
        payload = task.get("payload_path") or ""
        self._flow_submitter.submit_chain(
            env,
            [
                TaskSpec(
                    task_type=follow_on_type,
                    concern_scope="bootstrap",
                    payload_path=payload,
                    priority="normal",
                ),
            ],
            dedup_key=(follow_on_type, flow_id),
        )

    def _submit_global_fanout(
        self,
        db_path: Path,
        planspace: Path | None,
        task: dict,
        task_types: list[str],
    ) -> None:
        from flow.types.schema import BranchSpec, TaskSpec

        flow_id = task.get("flow_id") or ""
        env = FlowEnvelope(
            db_path=db_path,
            submitted_by="reconciler",
            flow_id=flow_id,
            declared_by_task_id=int(task["id"]),
            origin_refs=[],
            planspace=planspace,
        )
        payload = task.get("payload_path") or ""
        branches = [
            BranchSpec(
                label=task_type.split(".")[-1],
                steps=[
                    TaskSpec(
                        task_type=task_type,
                        concern_scope="bootstrap",
                        payload_path=payload,
                        priority="normal",
                    ),
                ],
            )
            for task_type in task_types
        ]
        self._flow_submitter.submit_fanout(env, branches, dedup_flow_id=flow_id)

    def _handle_build_codemap_complete(
        self,
        db_path: Path,
        planspace: Path | None,
        task: dict,
    ) -> None:
        if planspace is None:
            self._submit_global_follow_on(
                db_path,
                planspace,
                task,
                "bootstrap.explore_sections",
            )
            return

        codemap_path = PathRegistry(planspace).codemap()
        modules: list = []
        if codemap_path.is_file():
            try:
                from scan.codemap.skeleton_parser import parse_skeleton_modules

                codemap_text = codemap_path.read_text(encoding="utf-8")
                modules = parse_skeleton_modules(codemap_text)
            except Exception:  # noqa: BLE001
                log_bootstrap_stage(
                    db_path,
                    "hierarchical_codemap_fallback",
                    "completed",
                    error="skeleton parse failed",
                )
        if len(modules) < self._HIERARCHICAL_MODULE_THRESHOLD:
            self._submit_global_follow_on(
                db_path,
                planspace,
                task,
                "bootstrap.explore_sections",
            )
            return
        try:
            from scan.codemap.codemap_builder import build_module_fanout

            branches, gate = build_module_fanout(modules)
            flow_id = task.get("flow_id") or ""
            env = FlowEnvelope(
                db_path=db_path,
                submitted_by="reconciler",
                flow_id=flow_id,
                declared_by_task_id=int(task["id"]),
                origin_refs=[],
                planspace=planspace,
            )
            gate_id = self._flow_submitter.submit_fanout(
                env,
                branches,
                gate=gate,
                dedup_flow_id=flow_id,
            )
            if gate_id:
                log_event(
                    db_path,
                    kind="bootstrap_codemap_fanout",
                    tag="bootstrap.build_codemap",
                    body=json.dumps({
                        "flow_id": flow_id,
                        "gate_id": gate_id,
                        "module_count": len(modules),
                        "modules": [module.name for module in modules],
                    }),
                )
                return
            log_bootstrap_stage(
                db_path,
                "hierarchical_codemap_fallback",
                "completed",
                error="fanout submission returned no gate_id",
            )
        except Exception:  # noqa: BLE001
            log_bootstrap_stage(
                db_path,
                "hierarchical_codemap_fallback",
                "failed",
                error="hierarchical fanout submission raised exception",
            )
        self._submit_global_follow_on(
            db_path,
            planspace,
            task,
            "bootstrap.explore_sections",
        )

    def _read_global_output_field(
        self,
        planspace: Path | None,
        task_type: str,
        field: str,
    ) -> object:
        rel_path = self._BOOTSTRAP_ARTIFACT_PATHS.get(task_type)
        if not rel_path or planspace is None:
            return None
        data = self._artifact_io.read_json(planspace / rel_path)
        if isinstance(data, dict):
            return data.get(field)
        return None

    def _initialize_section_states_from_artifacts(
        self,
        db_path: Path,
        planspace: Path | None,
    ) -> None:
        from orchestrator.engine.section_state_machine import (
            SectionState,
            get_section_state,
            set_section_state,
        )

        if planspace is None:
            return
        sections_dir = PathRegistry(planspace).sections_dir()
        if not sections_dir.is_dir():
            return
        for section_file in sorted(sections_dir.glob("section-*.md")):
            stem = section_file.stem
            parts = stem.split("-", 1)
            if len(parts) < 2:
                continue
            section_number = parts[1]
            if get_section_state(db_path, section_number) is not None:
                continue
            set_section_state(db_path, section_number, SectionState.PENDING)

    @staticmethod
    def _seed_section_codemap_fragments(planspace: Path | None) -> None:
        if planspace is None:
            return
        try:
            from scan.codemap.codemap_builder import write_section_fragments

            write_section_fragments(PathRegistry(planspace))
        except Exception:  # noqa: BLE001
            logger.debug(
                "Section codemap fragment seeding failed — continuing",
                exc_info=True,
            )
