"""Scope-delta aggregation and adjudication helpers."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from coordination.repository.scope_deltas import list_scope_delta_files
from orchestrator.repository.decisions import Decision, Decisions
from orchestrator.path_registry import PathRegistry
from orchestrator.engine.section_state_machine import SectionState, set_section_state
from implementation.service.scope_delta_parser import (
    normalize_section_id,
    parse_scope_delta_adjudication,
)
from reconciliation.service.detectors import consolidate_new_section_candidates
from dispatch.types import ALIGNMENT_CHANGED_PENDING
from signals.types import TRUNCATE_REASON

if TYPE_CHECKING:
    from containers import (
        AgentDispatcher,
        ArtifactIOService,
        Communicator,
        LogService,
        ModelPolicyService,
        PromptGuard,
        TaskRouterService,
    )


class ScopeDeltaAggregationExit(Exception):
    """Raised when scope-delta adjudication must fail closed."""


def _compose_adjudication_text(pending_deltas_path: Path) -> str:
    """Return the full prompt text for scope-delta adjudication."""
    return f"""# Task: Adjudicate Scope Deltas

## Pending Scope Deltas

Read the pending scope deltas from: `{pending_deltas_path}`

Each delta has a unique `delta_id`. Use it as the primary key in your
decisions so the system can apply each decision back to the exact
originating artifact.

## Instructions

Each scope delta represents a section discovering work outside its
designated scope. For each delta, decide:

1. **accept**: Create new section(s) to handle the out-of-scope work
2. **reject**: The work is not needed or can be deferred
3. **absorb**: Expand an existing section's scope to include it

Each delta also includes `requires_root_reframing`:
- `true`: this concern changes the parent framing and should not be
  treated as a routine local section split
- `false`: this can be handled as an ordinary local scope adjustment

Reply with a JSON block:

```json
{{"decisions": [
  {{"delta_id": "delta-03-proposal-oos", "action": "accept", "reason": "New section needed for auth module", "new_sections": [{{"title": "Authentication Middleware", "scope": "Authentication middleware setup and integration"}}]}},
  {{"delta_id": "delta-05-scan-deep", "action": "reject", "reason": "Optimization can be deferred to next round"}},
  {{"delta_id": "delta-07-candidate-a1b2c3d4", "action": "absorb", "reason": "Small addition fits existing scope", "absorb_into_section": "02", "scope_addition": "Include config validation"}}
]}}
```

**Required fields by action:**
- ALL: `delta_id`, `action`, `reason`
- accept: `new_sections` (array of `{{title, scope}}`)
- absorb: `absorb_into_section`, `scope_addition`
"""


class ScopeDeltaAggregator:
    """Adjudicate pending scope deltas and return decisions.

    All cross-cutting services are received via constructor injection.
    """

    def __init__(
        self,
        artifact_io: ArtifactIOService,
        communicator: Communicator,
        dispatcher: AgentDispatcher,
        logger: LogService,
        policies: ModelPolicyService,
        prompt_guard: PromptGuard,
        task_router: TaskRouterService,
    ) -> None:
        self._artifact_io = artifact_io
        self._communicator = communicator
        self._dispatcher = dispatcher
        self._logger = logger
        self._policies = policies
        self._prompt_guard = prompt_guard
        self._task_router = task_router
        self._decisions = Decisions(artifact_io=artifact_io)

    def _load_pending_deltas(self, scope_deltas_dir: Path) -> tuple[list[Path], list[dict]]:
        delta_files = list_scope_delta_files(scope_deltas_dir)
        pending_deltas: list[dict] = []
        for delta_file in delta_files:
            delta = self._artifact_io.read_json(delta_file)
            if delta is not None:
                if delta.get("adjudicated"):
                    continue
                pending_deltas.append(delta)
            else:
                self._logger.log(
                    f"  coordinator: WARNING — malformed scope-delta "
                    f"{delta_file.name}, preserving as .malformed.json",
                )
        return delta_files, pending_deltas

    def _write_adjudication_prompt(
        self,
        coord_dir: Path,
        pending_deltas: list[dict],
    ) -> tuple[Path, Path]:
        adjudication_prompt = coord_dir / "scope-delta-prompt.md"
        adjudication_output = coord_dir / "scope-delta-output.md"
        pending_deltas_path = coord_dir / "scope-deltas-pending.json"
        prompt_deltas = []
        for delta in pending_deltas:
            prompt_delta = dict(delta)
            prompt_delta["requires_root_reframing"] = bool(
                delta.get("requires_root_reframing", False),
            )
            prompt_deltas.append(prompt_delta)
        self._artifact_io.write_json(pending_deltas_path, prompt_deltas)

        prompt_text = _compose_adjudication_text(pending_deltas_path)
        if not self._prompt_guard.write_validated(prompt_text, adjudication_prompt):
            raise ScopeDeltaAggregationExit

        return adjudication_prompt, adjudication_output

    def _dispatch_adjudication(
        self,
        planspace: Path,
        adjudication_prompt: Path,
        adjudication_output: Path,
    ) -> dict | None:
        policy = self._policies.load(planspace)
        self._communicator.log_artifact(planspace, "prompt:scope-delta-adjudication")

        adjudication_result = self._dispatcher.dispatch(
            self._policies.resolve(policy, "coordination_plan"),
            adjudication_prompt,
            adjudication_output,
            planspace,
            agent_file=self._task_router.agent_for("coordination.plan"),
        )
        if adjudication_result == ALIGNMENT_CHANGED_PENDING:
            raise ScopeDeltaAggregationExit

        adj_data = parse_scope_delta_adjudication(adjudication_result.output)
        if adj_data is not None:
            return adj_data

        self._logger.log("  coordinator: scope-delta adjudication parse "
            "failed — retrying with escalation model")
        retry_prompt = adjudication_prompt.with_name("scope-delta-prompt-retry.md")
        retry_prompt.write_text(
            adjudication_prompt.read_text(encoding="utf-8")
            + "\n\nOutput ONLY the JSON object, no prose.\n",
            encoding="utf-8",
        )
        retry_output = adjudication_output.with_name("scope-delta-output-retry.md")
        retry_result = self._dispatcher.dispatch(
            self._policies.resolve(policy, "escalation_model"),
            retry_prompt,
            retry_output,
            planspace,
            agent_file=self._task_router.agent_for("coordination.plan"),
        )
        if retry_result == ALIGNMENT_CHANGED_PENDING:
            raise ScopeDeltaAggregationExit

        return parse_scope_delta_adjudication(retry_result.output)

    def _build_delta_id_map(self, delta_files: list[Path]) -> dict[str, Path]:
        delta_id_to_path: dict[str, Path] = {}
        for delta_file in delta_files:
            delta = self._artifact_io.read_json(delta_file)
            if isinstance(delta, dict):
                delta_id = delta.get("delta_id")
                if delta_id:
                    delta_id_to_path[str(delta_id)] = delta_file
        return delta_id_to_path

    def _apply_adjudication(
        self,
        decision: dict,
        *,
        paths: PathRegistry,
        delta_id_to_path: dict[str, Path],
    ) -> None:
        delta_id = str(decision.get("delta_id", ""))
        action = decision.get("action", "")

        if delta_id and delta_id in delta_id_to_path:
            delta_path = delta_id_to_path[delta_id]
        else:
            section = normalize_section_id(str(decision.get("section", "")), paths)
            delta_path = paths.scope_delta_section(section)

        if delta_path.exists():
            delta = self._artifact_io.read_json(delta_path)
            if delta is None:
                self._logger.log(
                    f"  coordinator: WARNING — malformed scope-delta "
                    f"{delta_path.name} during adjudication application, "
                    "preserving as .malformed.json",
                )
                malformed = delta_path.with_suffix(".malformed.json")
                self._artifact_io.rename_malformed(delta_path)
                self._artifact_io.write_json(
                    delta_path,
                    {
                        "delta_id": delta_id,
                        "section": decision.get("section", ""),
                        "origin": "unknown",
                        "adjudicated": True,
                        "adjudication": decision,
                        "error": (
                            "original scope-delta malformed during "
                            "adjudication application"
                        ),
                        "preserved_path": str(malformed),
                    },
                )
                self._logger.log(f"  coordinator: scope delta {delta_id or delta_path.name} → {action}")
                return

            delta["adjudicated"] = True
            delta["adjudication"] = decision
            self._artifact_io.write_json(delta_path, delta)

        self._logger.log(f"  coordinator: scope delta {delta_id or delta_path.name} → {action}")

    def _consolidate_accepted_sections(
        self,
        decisions: list[dict],
        paths: PathRegistry,
    ) -> list[dict]:
        """Deduplicate new-section proposals across accepted deltas.

        When multiple deltas propose sections with the same title, only
        one section file should be created.  Uses the reconciliation
        detector ``consolidate_new_section_candidates`` for exact-match
        grouping, then merges duplicate proposals into a single entry.
        """
        from proposal.repository.state import ProposalState

        # Build pseudo proposal states from accepted delta new_sections
        pseudo_states: dict[str, ProposalState] = {}
        for decision in decisions:
            if decision.get("action") != "accept":
                continue
            new_sections = decision.get("new_sections")
            if not isinstance(new_sections, list):
                continue
            delta_id = str(decision.get("delta_id", ""))
            pseudo_states[delta_id] = ProposalState(
                new_section_candidates=new_sections,
            )

        if len(pseudo_states) < 2:
            return decisions

        consolidated, _ungrouped = consolidate_new_section_candidates(pseudo_states)
        if not consolidated:
            return decisions

        # Build set of duplicate titles (those appearing in >1 delta)
        seen_titles: set[str] = set()
        for entry in consolidated:
            seen_titles.add(entry["title"])

        # Remove duplicate new_sections from decisions, keeping only the
        # first occurrence of each consolidated title
        emitted: set[str] = set()
        updated_decisions: list[dict] = []
        for decision in decisions:
            if decision.get("action") != "accept":
                updated_decisions.append(decision)
                continue
            new_sections = decision.get("new_sections")
            if not isinstance(new_sections, list):
                updated_decisions.append(decision)
                continue
            filtered = []
            for ns in new_sections:
                title = str(ns.get("title", "")).strip().lower() if isinstance(ns, dict) else ""
                if title in seen_titles:
                    if title not in emitted:
                        emitted.add(title)
                        filtered.append(ns)
                    else:
                        self._logger.log(
                            f"  coordinator: deduplicated new-section "
                            f"'{title}' (already proposed by another delta)"
                        )
                else:
                    filtered.append(ns)
            decision = dict(decision, new_sections=filtered)
            updated_decisions.append(decision)

        return updated_decisions

    def _next_section_number(self, sections_dir: Path) -> str:
        """Determine the next available section number in the sections dir."""
        existing = sorted(sections_dir.glob("section-*.md"))
        max_num = 0
        for p in existing:
            m = re.match(r"^section-(\d+)\.md$", p.name)
            if m:
                max_num = max(max_num, int(m.group(1)))
        return f"{max_num + 1:02d}"

    def _create_new_sections(
        self,
        decisions: list[dict],
        paths: PathRegistry,
    ) -> list[str]:
        """Create section files and state rows for accepted deltas with new_sections.

        Returns a list of newly created section numbers.
        """
        created: list[str] = []
        sections_dir = paths.sections_dir()
        sections_dir.mkdir(parents=True, exist_ok=True)
        db_path = paths.run_db()

        for decision in decisions:
            if decision.get("action") != "accept":
                continue
            new_sections = decision.get("new_sections")
            if not isinstance(new_sections, list) or not new_sections:
                continue

            for ns in new_sections:
                if not isinstance(ns, dict):
                    continue
                title = ns.get("title", "Untitled Section")
                scope = ns.get("scope", title)

                sec_num = self._next_section_number(sections_dir)
                section_path = sections_dir / f"section-{sec_num}.md"
                section_content = (
                    f"# Section {sec_num}: {title}\n\n"
                    f"{scope}\n\n"
                    f"## Related Files\n\n"
                    f"(To be populated by scan or re-explorer)\n"
                )
                section_path.write_text(section_content, encoding="utf-8")

                # Register in the state machine as PENDING
                if db_path.exists():
                    set_section_state(db_path, sec_num, SectionState.PENDING)

                created.append(sec_num)
                self._logger.log(
                    f"  coordinator: created section-{sec_num}.md "
                    f"for accepted delta '{decision.get('delta_id', '?')}' "
                    f"(title: {title})"
                )

        return created

    def _record_decisions(
        self,
        planspace: Path,
        decisions: list[dict],
    ) -> None:
        paths = PathRegistry(planspace)
        decisions_rollup_path = paths.coordination_dir() / "scope-delta-decisions.json"
        self._artifact_io.write_json(decisions_rollup_path, {"decisions": decisions})
        self._communicator.log_artifact(planspace, "coordination:scope-delta-decisions")

        decisions_dir = paths.decisions_dir()
        for decision in decisions:
            delta_id = str(decision.get("delta_id", ""))
            section = normalize_section_id(str(decision.get("section", "")), paths)
            action = decision.get("action", "")
            reason = decision.get("reason", "")
            label = delta_id or section
            self._communicator.send_to_parent(
                planspace,
                f"summary:scope-delta:{label}:{action}:{reason[:TRUNCATE_REASON]}",
            )

            existing = self._decisions.load_decisions(decisions_dir, section=section)
            next_num = len(existing) + 1
            self._decisions.record_decision(
                decisions_dir,
                Decision(
                    id=f"d-{delta_id or section}-{next_num:03d}",
                    scope="section",
                    section=section,
                    problem_id=None,
                    parent_problem_id=None,
                    concern_scope="scope-delta",
                    proposal_summary=f"{action}: {reason}",
                    alignment_to_parent=None,
                    status="decided",
                ),
            )

    def aggregate_scope_deltas(
        self,
        planspace: Path,
    ) -> list[dict]:
        """Adjudicate any pending scope deltas and return the decisions."""
        paths = PathRegistry(planspace)
        scope_deltas_dir = paths.scope_deltas_dir()
        if not scope_deltas_dir.exists():
            return []

        delta_files, pending_deltas = self._load_pending_deltas(scope_deltas_dir)
        if not pending_deltas:
            return []

        self._logger.log(
            f"  coordinator: {len(pending_deltas)} pending scope "
            f"deltas — dispatching adjudicator",
        )
        adjudication_prompt, adjudication_output = self._write_adjudication_prompt(
            paths.coordination_dir(),
            pending_deltas,
        )
        adj_data = self._dispatch_adjudication(
            planspace,
            adjudication_prompt,
            adjudication_output,
        )
        if adj_data is None:
            self._logger.log("  coordinator: scope-delta adjudication parse "
                "failed after retry — fail closed")
            self._artifact_io.write_json(
                paths.coordination_dir() / "scope-delta-adjudication-failure.json",
                {
                    "error": "unparseable_adjudication_json",
                    "prompt_path": str(adjudication_prompt),
                    "output_path": str(adjudication_output),
                    "attempts": 2,
                },
            )
            self._communicator.send_to_parent(
                planspace,
                "fail:coordination:unparseable_scope_delta_adjudication",
            )
            raise ScopeDeltaAggregationExit

        delta_id_to_path = self._build_delta_id_map(delta_files)
        decisions = list(adj_data.get("decisions", []))
        for decision in decisions:
            self._apply_adjudication(
                decision,
                paths=paths,
                delta_id_to_path=delta_id_to_path,
            )

        # Consolidate new-section proposals across deltas before creating.
        # Uses the reconciliation detector to deduplicate candidates that
        # share the same title across multiple source sections.
        decisions = self._consolidate_accepted_sections(decisions, paths)

        # Create section files for accepted deltas with new_sections data
        created_sections = self._create_new_sections(decisions, paths)
        if created_sections:
            self._logger.log(
                f"  coordinator: {len(created_sections)} new section(s) "
                f"created from accepted scope deltas: {created_sections}"
            )

        self._record_decisions(
            planspace,
            decisions,
        )
        return decisions
