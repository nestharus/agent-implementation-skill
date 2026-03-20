"""Runtime readiness resolver for section execution."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from reconciliation.service.detectors import aggregate_shared_seams, detect_contract_conflicts
from flow.service.task_db_client import task_db

if TYPE_CHECKING:
    from containers import ArtifactIOService


class GovernanceBlockerState(str, Enum):
    """State code for governance readiness blockers."""

    DEVIATION = "governance_deviation"
    QUESTION = "governance_question"
    AMBIGUITY_UNRESOLVED = "governance_ambiguity_unresolved"
    IDENTITY_MISSING = "governance_identity_missing"
    PROFILE_MISMATCH = "governance_profile_mismatch"
    MEMBERSHIP = "governance_membership"
    PACKET_MISSING = "governance_packet_missing"

    def __str__(self) -> str:  # noqa: D105
        return self.value
from orchestrator.path_registry import PathRegistry
from proposal.repository.state import (
    ProposalState,
    State as ProposalStateRepo,
    extract_blockers,
    has_blocking_fields,
)


@dataclass(frozen=True)
class ReadinessResult:
    """Structured result from :func:`resolve_readiness`.

    Supports dict-style ``[]`` and ``.get()`` access for backward
    compatibility during migration.  Prefer attribute access
    (``.ready``, ``.blockers``, ``.rationale``, ``.artifact_path``).
    """

    ready: bool
    descent_required: bool = False
    blockers: list[dict] = field(default_factory=list)
    rationale: str = ""
    artifact_path: Path | None = None

    # -- backward-compat dict-style access ---------------------------------

    _FIELDS = frozenset({
        "ready", "descent_required", "blockers", "rationale", "artifact_path",
    })

    def __getitem__(self, key: str) -> Any:
        if key in self._FIELDS:
            return getattr(self, key)
        raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        if key in self._FIELDS:
            return getattr(self, key)
        return default

logger = logging.getLogger(__name__)

_DEFAULT_MAX_DEPTH = 3
_ABSOLUTE_MAX_DEPTH = 5
_MAX_DEPTH_POLICY_KEY = "fractal_max_depth"


def _check_pattern_deviations(state: ProposalState) -> list[dict]:
    """Return blockers for unresolved pattern deviations."""
    deviations = state.pattern_deviations
    if isinstance(deviations, list) and deviations:
        return [{
            "state": GovernanceBlockerState.DEVIATION,
            "detail": (
                f"{len(deviations)} unresolved pattern deviation(s) — "
                "pattern delta must be accepted before descent"
            ),
            "needs": "pattern delta resolution",
            "why_blocked": "PAT-0013: pattern change before code change",
            "source": "governance_identity",
        }]
    return []


def _check_governance_questions(state: ProposalState) -> list[dict]:
    """Return blockers for unresolved governance questions."""
    questions = state.governance_questions
    if isinstance(questions, list) and questions:
        return [{
            "state": GovernanceBlockerState.QUESTION,
            "detail": (
                f"{len(questions)} unresolved governance question(s)"
            ),
            "needs": "governance question resolution",
            "why_blocked": "PAT-0013: unresolved governance questions block descent",
            "source": "governance_identity",
        }]
    return []


@dataclass
class GovernanceIds:
    """Validated governance IDs extracted from proposal state."""

    problem_ids: list[str] = field(default_factory=list)
    pattern_ids: list[str] = field(default_factory=list)
    profile_id: str = ""

    def has_declared_ids(self) -> bool:
        return bool(self.problem_ids or self.pattern_ids or self.profile_id)


def _validate_declared_ids_types(
    state: ProposalState, section_number: str,
) -> GovernanceIds:
    """Extract and type-check declared governance IDs from *state*."""
    problem_ids = state.problem_ids
    pattern_ids = state.pattern_ids
    profile_id = state.profile_id
    if not isinstance(problem_ids, list):
        logger.warning(
            "Section %s: problem_ids has unexpected type %s, defaulting to []",
            section_number, type(problem_ids).__name__,
        )
        problem_ids = []
    if not isinstance(pattern_ids, list):
        logger.warning(
            "Section %s: pattern_ids has unexpected type %s, defaulting to []",
            section_number, type(pattern_ids).__name__,
        )
        pattern_ids = []
    if not isinstance(profile_id, str):
        logger.warning(
            "Section %s: profile_id has unexpected type %s, defaulting to ''",
            section_number, type(profile_id).__name__,
        )
        profile_id = ""
    return GovernanceIds(problem_ids, pattern_ids, profile_id)


def _check_packet_ambiguity(packet: dict, state: ProposalState) -> list[dict]:
    """CP-3 (R107): packet ambiguity must be carried in proposal-state."""
    packet_applicability = packet.get("applicability_state", "")
    packet_questions = packet.get("governance_questions", [])
    if not isinstance(packet_questions, list):
        packet_questions = []
    if packet_applicability == "ambiguous_applicability" and packet_questions:
        state_questions = state.governance_questions
        if not isinstance(state_questions, list):
            state_questions = []
        if not state_questions:
            return [{
                "state": GovernanceBlockerState.AMBIGUITY_UNRESOLVED,
                "detail": (
                    f"governance packet has {len(packet_questions)} "
                    "ambiguity question(s) but proposal-state does not "
                    "carry or resolve them"
                ),
                "needs": "governance question resolution or narrowed selection",
                "why_blocked": (
                    "PAT-0011: packet ambiguity must be resolved or "
                    "carried forward before descent"
                ),
                "source": "governance_identity",
            }]
    return []


def _check_empty_identity(packet: dict, has_declared_ids: bool) -> list[dict]:
    """PAT-0013 step 6: empty identity is illegal when packet has candidates."""
    packet_problems = packet.get("candidate_problems", [])
    packet_patterns = packet.get("candidate_patterns", [])
    governing_profile = packet.get("governing_profile", "")
    if not isinstance(packet_problems, list):
        packet_problems = []
    if not isinstance(packet_patterns, list):
        packet_patterns = []
    if not isinstance(governing_profile, str):
        governing_profile = ""
    has_governance_candidates = bool(
        packet_problems or packet_patterns or governing_profile
    )
    if has_governance_candidates and not has_declared_ids:
        return [{
            "state": GovernanceBlockerState.IDENTITY_MISSING,
            "detail": (
                "governance packet provides candidates but proposal "
                "declares no problem_ids, pattern_ids, or profile_id"
            ),
            "needs": "governance identity declaration",
            "why_blocked": "PAT-0013: non-empty identity required when governance applies",
            "source": "governance_identity",
        }]
    return []


def _check_profile_mismatch(
    profile_id: str, governing_profile: str,
) -> list[dict]:
    """Return blockers when profile_id does not match the governing profile."""
    if profile_id and governing_profile and profile_id != governing_profile:
        return [{
            "state": GovernanceBlockerState.PROFILE_MISMATCH,
            "detail": (
                f"profile_id '{profile_id}' does not match "
                f"governing_profile '{governing_profile}'"
            ),
            "needs": "profile_id correction",
            "why_blocked": "PAT-0013: profile_id must match governing profile",
            "source": "governance_identity",
        }]
    return []


def _check_packet_membership(
    problem_ids: list[str],
    pattern_ids: list[str],
    packet_problems: list,
    packet_patterns: list,
) -> list[dict]:
    """Validate that declared IDs reference records present in the packet."""
    if not (problem_ids or pattern_ids):
        return []
    packet_problem_ids = {
        str(p.get("problem_id", ""))
        for p in packet_problems
        if isinstance(p, dict)
    }
    packet_pattern_ids = {
        str(p.get("pattern_id", ""))
        for p in packet_patterns
        if isinstance(p, dict)
    }
    orphan_problems = [
        pid for pid in problem_ids
        if isinstance(pid, str) and pid and pid not in packet_problem_ids
    ]
    orphan_patterns = [
        pid for pid in pattern_ids
        if isinstance(pid, str) and pid and pid not in packet_pattern_ids
    ]
    if orphan_problems or orphan_patterns:
        details = []
        if orphan_problems:
            details.append(f"problem_ids {orphan_problems} not in packet")
        if orphan_patterns:
            details.append(f"pattern_ids {orphan_patterns} not in packet")
        return [{
            "state": GovernanceBlockerState.MEMBERSHIP,
            "detail": "; ".join(details),
            "needs": "governance ID correction",
            "why_blocked": "PAT-0013: IDs must reference packet records",
            "source": "governance_identity",
        }]
    return []


def _check_missing_packet(has_declared_ids: bool, packet: Any) -> list[dict]:
    """PAT-0013 step 6: declared IDs with missing/malformed packet -> block."""
    if has_declared_ids and not isinstance(packet, dict):
        return [{
            "state": GovernanceBlockerState.PACKET_MISSING,
            "detail": (
                "governance IDs declared but governance packet is "
                "missing or malformed"
            ),
            "needs": "governance packet rebuild",
            "why_blocked": "PAT-0013: packet required when IDs are declared",
            "source": "governance_identity",
        }]
    return []


def _collect_substrate_paths(
    seed_plan: dict | None,
    shard: dict | None,
) -> set[str]:
    """Collect file paths covered by substrate artifacts.

    Returns a set of file paths from:
    - seed plan ``anchors[*].path``
    - shard ``shared_seams[*].path_candidates``
    - shard ``provides[*].id`` (used for noun.verb matching)

    All values are lowercased for case-insensitive matching.
    """
    paths: set[str] = set()
    if isinstance(seed_plan, dict):
        for anchor in seed_plan.get("anchors", []):
            if isinstance(anchor, dict):
                p = anchor.get("path", "")
                if isinstance(p, str) and p:
                    paths.add(p.lower())
    if isinstance(shard, dict):
        for seam in shard.get("shared_seams", []):
            if isinstance(seam, dict):
                for pc in seam.get("path_candidates", []):
                    if isinstance(pc, str) and pc:
                        paths.add(pc.lower())
        for prov in shard.get("provides", []):
            if isinstance(prov, dict):
                pid = prov.get("id", "")
                if isinstance(pid, str) and pid:
                    paths.add(pid.lower())
    return paths


def _item_resolved_by_substrate(item: str, substrate_paths: set[str]) -> bool:
    """Return True if *item* (a free-text candidate) references a substrate path.

    Checks whether any substrate path appears as a substring of the
    lowercased item text.  This handles the common case where proposal-state
    candidates mention file paths or noun.verb IDs that the substrate system
    has already resolved.
    """
    lowered = item.lower()
    return any(sp in lowered for sp in substrate_paths)


def _count_distinct_problem_ids(state: ProposalState) -> int:
    """Count distinct non-empty problem IDs declared in proposal state."""
    problem_ids = state.problem_ids
    if not isinstance(problem_ids, list):
        return 0
    return len({
        str(pid).strip()
        for pid in problem_ids
        if isinstance(pid, str) and str(pid).strip()
    })


def _count_section_subconcern_headings(section_spec_path: Path) -> int:
    """Count H2 headings in the section spec as a complexity heuristic."""
    if not section_spec_path.is_file():
        return 0
    try:
        text = section_spec_path.read_text(encoding="utf-8")
    except OSError:
        return 0
    return len(re.findall(r"^##\s+\S", text, flags=re.MULTILINE))


class ReadinessResolver:
    def __init__(
        self,
        artifact_io: ArtifactIOService,
    ) -> None:
        self._artifact_io = artifact_io

    def effective_max_depth(self, planspace: Path) -> int:
        """Return the configured descent depth cap, clamped to safe bounds."""
        data = self._artifact_io.read_json(PathRegistry(planspace).model_policy())
        if isinstance(data, dict):
            value = data.get(_MAX_DEPTH_POLICY_KEY)
            if isinstance(value, int):
                if value < 1:
                    return _DEFAULT_MAX_DEPTH
                return min(value, _ABSOLUTE_MAX_DEPTH)
        return _DEFAULT_MAX_DEPTH

    def _current_section_depth(self, planspace: Path, section_number: str) -> int:
        """Return the stored recursion depth for a section, defaulting to 0."""
        db_path = PathRegistry(planspace).run_db()
        if not db_path.exists():
            return 0
        try:
            with task_db(db_path) as conn:
                row = conn.execute(
                    "SELECT depth FROM section_states WHERE section_number = ?",
                    (section_number,),
                ).fetchone()
        except Exception:
            logger.debug(
                "Section %s: could not read depth from section_states",
                section_number,
                exc_info=True,
            )
            return 0
        if not row or row[0] is None:
            return 0
        return int(row[0])

    def _descent_required(
        self,
        planspace: Path,
        section_number: str,
        state: ProposalState,
    ) -> bool:
        """Return True when the section should descend instead of advancing."""
        current_depth = self._current_section_depth(planspace, section_number)
        max_depth = self.effective_max_depth(planspace)
        if current_depth >= max_depth:
            return False

        problem_count = _count_distinct_problem_ids(state)
        if problem_count >= 3:
            logger.info(
                "Section %s: descent required (%d distinct problem_ids at depth %d/%d)",
                section_number, problem_count, current_depth, max_depth,
            )
            return True

        heading_count = _count_section_subconcern_headings(
            PathRegistry(planspace).section_spec(section_number),
        )
        if heading_count >= 3:
            logger.info(
                "Section %s: descent required (%d H2 headings at depth %d/%d)",
                section_number, heading_count, current_depth, max_depth,
            )
            return True

        return False

    def _apply_substrate_overlay(
        self,
        paths: PathRegistry,
        section_number: str,
    ) -> set[str]:
        """Return descriptions of proposal-state items resolved by substrate.

        Loads the substrate seed plan and section shard.  For each
        ``shared_seam_candidate`` or ``unresolved_anchor`` that references a
        file path or provides-ID already covered by the substrate, the item
        description is included in the returned set.

        Fail-open: if substrate files are missing or malformed, returns an
        empty set (no overlay applied).
        """
        try:
            seed_plan = self._artifact_io.read_json(paths.substrate_seed_plan())
        except Exception:
            seed_plan = None

        try:
            shard = self._artifact_io.read_json(
                paths.substrate_shard(section_number),
            )
        except Exception:
            shard = None

        return _collect_substrate_paths(seed_plan, shard)

    def _filter_substrate_resolved(
        self,
        items: list,
        substrate_paths: set[str],
        section_number: str,
        field_name: str,
    ) -> list:
        """Filter out items resolved by substrate, logging each removal."""
        if not substrate_paths or not items:
            return items
        filtered = []
        for item in items:
            desc = str(item)
            if _item_resolved_by_substrate(desc, substrate_paths):
                logger.info(
                    "Section %s: %s '%s' resolved by substrate",
                    section_number, field_name, desc,
                )
            else:
                filtered.append(item)
        return filtered

    def _apply_scaffold_overlay(
        self,
        paths: PathRegistry,
        section_number: str,
    ) -> set[str]:
        """Return file paths assigned to *section_number* by scaffold ownership.

        Reads the scaffold-assignment signal written by the coordination
        plan executor.  Returns a set of file path strings (lowercased)
        that this section is responsible for creating.  Unresolved anchors
        referencing these paths are reclassified as assigned-pending rather
        than blocking.

        Fail-open: missing or malformed signal returns an empty set.
        """
        try:
            data = self._artifact_io.read_json(paths.scaffold_assignments())
        except Exception:
            return set()
        if not isinstance(data, dict):
            return set()
        assignments = data.get("assignments", [])
        if not isinstance(assignments, list):
            return set()
        paths_set: set[str] = set()
        for entry in assignments:
            if not isinstance(entry, dict):
                continue
            if str(entry.get("section", "")) != section_number:
                continue
            for f in entry.get("files", []):
                if isinstance(f, str) and f:
                    paths_set.add(f.lower())
        return paths_set

    def _validate_governance_identity(
        self,
        state: ProposalState,
        planspace: Path,
        section_number: str,
    ) -> list[dict]:
        """Validate governance identity fields against the governance packet.

        *planspace* is the root planspace directory.  PathRegistry is used for
        all artifact path construction (PAT-0003).

        Returns a list of governance blockers (empty if valid).
        """
        blockers: list[dict] = []

        blockers.extend(_check_pattern_deviations(state))
        blockers.extend(_check_governance_questions(state))

        # Load governance packet for validation
        paths = PathRegistry(planspace)
        packet_path = paths.governance_packet(section_number)
        packet = self._artifact_io.read_json(packet_path)

        gov_ids = _validate_declared_ids_types(state, section_number)
        has_declared_ids = gov_ids.has_declared_ids()

        if isinstance(packet, dict):
            blockers.extend(_check_packet_ambiguity(packet, state))
            blockers.extend(_check_empty_identity(packet, has_declared_ids))

            governing_profile = packet.get("governing_profile", "")
            if not isinstance(governing_profile, str):
                governing_profile = ""
            blockers.extend(_check_profile_mismatch(gov_ids.profile_id, governing_profile))

            packet_problems = packet.get("candidate_problems", [])
            packet_patterns = packet.get("candidate_patterns", [])
            if not isinstance(packet_problems, list):
                packet_problems = []
            if not isinstance(packet_patterns, list):
                packet_patterns = []
            blockers.extend(_check_packet_membership(
                gov_ids.problem_ids, gov_ids.pattern_ids,
                packet_problems, packet_patterns,
            ))
        else:
            blockers.extend(_check_missing_packet(has_declared_ids, packet))

        return blockers

    def _load_seam_sharing_sections(
        self,
        paths: PathRegistry,
        section_number: str,
    ) -> list[str]:
        """Return section numbers that share substrate seams with *section_number*.

        Reads the substrate shard for *section_number* to find its
        ``provides`` and ``needs`` entries, then scans other shards for
        matching IDs.  Fail-open: missing/malformed shards return [].
        """
        try:
            shard = self._artifact_io.read_json(
                paths.substrate_shard(section_number),
            )
        except Exception:
            return []
        if not isinstance(shard, dict):
            return []

        own_provides = {
            str(p.get("id", "")).strip().lower()
            for p in shard.get("provides", [])
            if isinstance(p, dict) and str(p.get("id", "")).strip()
        }
        own_needs = {
            str(n.get("id", "")).strip().lower()
            for n in shard.get("needs", [])
            if isinstance(n, dict) and str(n.get("id", "")).strip()
        }
        if not own_provides and not own_needs:
            return []

        shards_dir = paths.substrate_dir() / "shards"
        if not shards_dir.is_dir():
            return []

        neighbors: list[str] = []
        for shard_path in sorted(shards_dir.glob("shard-*.json")):
            other_num = shard_path.stem.replace("shard-", "")
            if other_num == section_number:
                continue
            try:
                other = self._artifact_io.read_json(shard_path)
            except Exception:
                continue
            if not isinstance(other, dict):
                continue
            other_provides = {
                str(p.get("id", "")).strip().lower()
                for p in other.get("provides", [])
                if isinstance(p, dict) and str(p.get("id", "")).strip()
            }
            other_needs = {
                str(n.get("id", "")).strip().lower()
                for n in other.get("needs", [])
                if isinstance(n, dict) and str(n.get("id", "")).strip()
            }
            if own_provides & other_needs or own_needs & other_provides:
                neighbors.append(other_num)

        return neighbors

    def _check_contract_conflicts(
        self,
        paths: PathRegistry,
        section_number: str,
    ) -> list[dict]:
        """Check for contract conflicts between this section and seam-sharing neighbors.

        Loads proposal states for sections that share substrate
        provides/needs relationships, then invokes the pure
        ``detect_contract_conflicts()`` detector scoped to those sections.

        Returns a list of contract_conflict blockers (empty if none found).
        Fail-open: missing shards or proposal-states -> no blockers.
        """
        neighbor_sections = self._load_seam_sharing_sections(paths, section_number)
        if not neighbor_sections:
            return []

        repo = ProposalStateRepo(artifact_io=self._artifact_io)
        scoped_states: dict[str, ProposalState] = {}

        own_state_path = paths.proposal_state(section_number)
        if own_state_path.exists():
            scoped_states[section_number] = repo.load_proposal_state(own_state_path)

        for neighbor in neighbor_sections:
            neighbor_path = paths.proposal_state(neighbor)
            if neighbor_path.exists():
                scoped_states[neighbor] = repo.load_proposal_state(neighbor_path)

        if len(scoped_states) < 2:
            return []

        conflicts = detect_contract_conflicts(scoped_states)
        if not conflicts:
            return []

        blockers: list[dict] = []
        for conflict in conflicts:
            if section_number in conflict.get("sections", []):
                blockers.append({
                    "type": "contract_conflict",
                    "description": (
                        f"Contract '{conflict['contract']}' conflicts with "
                        f"section(s) {conflict['sections']} "
                        f"(resolved_in={conflict['resolved_in']}, "
                        f"unresolved_in={conflict['unresolved_in']})"
                    ),
                    "conflict": conflict,
                })
        return blockers

    def _check_shared_seam_conflicts(
        self,
        paths: PathRegistry,
        section_number: str,
    ) -> list[dict]:
        """Check for shared seams that span multiple sections and need substrate.

        Loads proposal states for this section and its seam-sharing neighbors,
        then runs ``aggregate_shared_seams()`` to find multi-section seams that
        have not yet been resolved by the substrate system.

        Returns a list of shared_seam blockers (empty if none found).
        Fail-open: missing shards or proposal-states -> no blockers.
        """
        neighbor_sections = self._load_seam_sharing_sections(paths, section_number)
        if not neighbor_sections:
            return []

        repo = ProposalStateRepo(artifact_io=self._artifact_io)
        scoped_states: dict[str, ProposalState] = {}

        own_state_path = paths.proposal_state(section_number)
        if own_state_path.exists():
            scoped_states[section_number] = repo.load_proposal_state(own_state_path)

        for neighbor in neighbor_sections:
            neighbor_path = paths.proposal_state(neighbor)
            if neighbor_path.exists():
                scoped_states[neighbor] = repo.load_proposal_state(neighbor_path)

        if len(scoped_states) < 2:
            return []

        aggregated, _ungrouped = aggregate_shared_seams(scoped_states)
        if not aggregated:
            return []

        # Filter to seams that involve this section and need substrate
        blockers: list[dict] = []
        for seam_entry in aggregated:
            if not seam_entry.get("needs_substrate", False):
                continue
            if section_number not in seam_entry.get("sections", []):
                continue
            blockers.append({
                "type": "shared_seam_conflict",
                "description": (
                    f"Shared seam '{seam_entry['seam']}' spans sections "
                    f"{seam_entry['sections']} and needs substrate resolution"
                ),
                "seam": seam_entry,
            })
        return blockers

    def resolve_readiness(self, planspace: Path, section_number: str) -> ReadinessResult:
        """Resolve whether *section_number* is ready for implementation.

        *planspace* is the root planspace directory (NOT the artifacts subdirectory).
        PathRegistry is used for all artifact path construction (PAT-0003).
        """
        paths = PathRegistry(planspace)
        proposal_state_path = paths.proposal_state(section_number)
        state = ProposalStateRepo(artifact_io=self._artifact_io).load_proposal_state(proposal_state_path)

        # Substrate overlay: filter out blocking items already resolved by
        # substrate artifacts (PRB-0006).  Fail-open — missing/malformed
        # substrate data means no filtering.  The gate itself remains
        # fail-closed.
        substrate_paths = self._apply_substrate_overlay(paths, section_number)
        if substrate_paths:
            state.shared_seam_candidates = self._filter_substrate_resolved(
                state.shared_seam_candidates, substrate_paths,
                section_number, "shared_seam_candidate",
            )
            state.unresolved_anchors = self._filter_substrate_resolved(
                state.unresolved_anchors, substrate_paths,
                section_number, "unresolved_anchor",
            )

        # Scaffold overlay: filter out unresolved_anchors assigned to this
        # section via scaffold ownership (foundational vacuum resolution).
        # Fail-open — missing signal means no filtering.
        scaffold_paths = self._apply_scaffold_overlay(paths, section_number)
        if scaffold_paths:
            state.unresolved_anchors = self._filter_substrate_resolved(
                state.unresolved_anchors, scaffold_paths,
                section_number, "unresolved_anchor (scaffold-assigned)",
            )

        ready = state.execution_ready is True and not has_blocking_fields(state)
        blockers = extract_blockers(state)

        # Validate governance identity (PAT-0013)
        governance_blockers = self._validate_governance_identity(
            state, planspace, section_number,
        )
        if governance_blockers:
            blockers.extend(governance_blockers)
            ready = False

        # Contract conflict detection (Gap 2): check for conflicting
        # contracts with seam-sharing sections.  Per-section reactive
        # check — not a global barrier.
        contract_blockers = self._check_contract_conflicts(paths, section_number)
        if contract_blockers:
            blockers.extend(contract_blockers)
            ready = False
            logger.info(
                "Section %s: %d contract conflict(s) with seam-sharing sections",
                section_number, len(contract_blockers),
            )

        # Shared seam detection: check for multi-section seams that need
        # substrate resolution before this section can proceed.
        seam_blockers = self._check_shared_seam_conflicts(paths, section_number)
        if seam_blockers:
            blockers.extend(seam_blockers)
            ready = False
            logger.info(
                "Section %s: %d shared seam conflict(s) with neighbor sections",
                section_number, len(seam_blockers),
            )

        rationale = state.readiness_rationale
        descent_required = False

        if not ready and not blockers:
            if not proposal_state_path.exists():
                rationale = rationale or "proposal-state artifact missing"
            elif not state.execution_ready:
                rationale = rationale or "execution_ready is false"
        elif ready:
            descent_required = self._descent_required(
                planspace, section_number, state,
            )

        serializable: dict = {
            "ready": ready,
            "blockers": blockers,
            "rationale": rationale,
        }
        if descent_required:
            serializable["descent_required"] = True

        readiness_dir = paths.readiness_dir()
        artifact_path = paths.execution_ready(section_number)
        try:
            self._artifact_io.write_json(artifact_path, serializable)
        except OSError:
            logger.warning("Could not write readiness artifact to %s", artifact_path)

        return ReadinessResult(
            ready=ready,
            descent_required=descent_required,
            blockers=blockers,
            rationale=rationale,
            artifact_path=artifact_path,
        )
