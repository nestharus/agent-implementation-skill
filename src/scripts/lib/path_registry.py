"""PathRegistry: Centralized artifact path construction.

Foundational service (Tier 1). No domain knowledge beyond directory layout.
Initialized with a planspace Path, provides typed accessors for all known
artifact locations. Replaces 142+ ad-hoc path constructions.
"""

from __future__ import annotations

from pathlib import Path


class PathRegistry:
    """Single source of truth for artifact directory layout."""

    def __init__(self, planspace: Path) -> None:
        self._planspace = planspace
        self._artifacts = planspace / "artifacts"

    @property
    def planspace(self) -> Path:
        return self._planspace

    @property
    def artifacts(self) -> Path:
        return self._artifacts

    # --- Directory accessors ---

    def sections_dir(self) -> Path:
        return self._artifacts / "sections"

    def proposals_dir(self) -> Path:
        return self._artifacts / "proposals"

    def signals_dir(self) -> Path:
        return self._artifacts / "signals"

    def notes_dir(self) -> Path:
        return self._artifacts / "notes"

    def decisions_dir(self) -> Path:
        return self._artifacts / "decisions"

    def todos_dir(self) -> Path:
        return self._artifacts / "todos"

    def coordination_dir(self) -> Path:
        return self._artifacts / "coordination"

    def reconciliation_dir(self) -> Path:
        return self._artifacts / "reconciliation"

    def scope_deltas_dir(self) -> Path:
        return self._artifacts / "scope-deltas"

    # --- Section-scoped file accessors ---

    def section_spec(self, num: str) -> Path:
        return self.sections_dir() / f"section-{num}.md"

    def proposal(self, num: str) -> Path:
        return self.proposals_dir() / f"section-{num}-integration-proposal.md"

    def proposal_excerpt(self, num: str) -> Path:
        return self.proposals_dir() / f"section-{num}-proposal-excerpt.md"

    def alignment_excerpt(self, num: str) -> Path:
        return self._artifacts / f"alignment-excerpt-{num}.md"

    def microstrategy(self, num: str) -> Path:
        return self.proposals_dir() / f"section-{num}-microstrategy.md"

    def problem_frame(self, num: str) -> Path:
        return self.signals_dir() / f"section-{num}-problem-frame.json"

    def cycle_budget(self, num: str) -> Path:
        return self.signals_dir() / f"section-{num}-cycle-budget.json"

    def mode_signal(self, num: str) -> Path:
        return self.signals_dir() / f"section-{num}-mode.json"

    def blocker_signal(self, num: str) -> Path:
        return self.signals_dir() / f"section-{num}-blocker.json"

    def microstrategy_signal(self, num: str) -> Path:
        return self.signals_dir() / f"proposal-{num}-microstrategy.json"

    def todos(self, num: str) -> Path:
        return self.todos_dir() / f"section-{num}-todos.md"

    def trace_map(self, num: str) -> Path:
        return self._artifacts / f"trace-map-{num}.json"

    def impl_modified(self, num: str) -> Path:
        return self._artifacts / f"impl-{num}-modified.txt"

    # --- Global file accessors ---

    def codemap(self) -> Path:
        return self._artifacts / "codemap.json"

    def corrections(self) -> Path:
        return self._artifacts / "codemap-corrections.json"

    def tool_registry(self) -> Path:
        return self._artifacts / "tool-registry.json"

    def project_mode_json(self) -> Path:
        return self.signals_dir() / "project-mode.json"

    def project_mode_txt(self) -> Path:
        return self._artifacts / "project-mode.txt"

    def mode_contract(self) -> Path:
        return self._artifacts / "mode-contract.json"

    def model_policy(self) -> Path:
        return self._artifacts / "model-policy.json"

    def strategic_state(self) -> Path:
        return self._artifacts / "strategic-state.json"

    def run_db(self) -> Path:
        return self._planspace / "run.db"
