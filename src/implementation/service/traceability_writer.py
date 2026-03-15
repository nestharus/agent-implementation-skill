from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from orchestrator.path_registry import PathRegistry
from orchestrator.types import Section

if TYPE_CHECKING:
    from containers import (
        ArtifactIOService,
        HasherService,
        LogService,
        SectionAlignmentService,
    )


def _proposal_governance_ids(planspace: Path, section_number: str, artifact_io: ArtifactIOService) -> dict:
    """Extract governance identity from proposal-state if available."""
    from proposal.repository.state import State

    paths = PathRegistry(planspace)
    state_path = paths.proposal_state(section_number)
    state = State(artifact_io=artifact_io).load_proposal_state(state_path)
    return {
        "problem_ids": [
            str(x) for x in state.problem_ids
            if isinstance(x, str) and x.strip()
        ],
        "pattern_ids": [
            str(x) for x in state.pattern_ids
            if isinstance(x, str) and x.strip()
        ],
        "profile_id": state.profile_id or "",
    }


def _optional_artifact(path: Path, hasher: HasherService) -> dict | None:
    """Return path+hash dict for an artifact if it exists, else None."""
    if not path.exists():
        return None
    return {"path": str(path), "hash": hasher.file_hash(path)}


class TraceabilityWriter:
    """Write and update traceability indexes for completed sections.

    All cross-cutting services are received via constructor injection.
    """

    def __init__(
        self,
        artifact_io: ArtifactIOService,
        hasher: HasherService,
        logger: LogService,
        section_alignment: SectionAlignmentService,
    ) -> None:
        self._artifact_io = artifact_io
        self._hasher = hasher
        self._logger = logger
        self._section_alignment = section_alignment

    def _file_sha256(self, path: Path) -> str:
        """Return hex SHA-256 of a file, or empty string if missing."""
        return self._hasher.file_hash(path)

    def _collect_alignment_verdicts(self, artifacts: Path, sec: str) -> list[dict]:
        """Collect alignment verdicts from output files using structured JSON."""
        verdicts: list[dict] = []
        for stage, prefix in (("proposal", "intg-align"),
                              ("implementation", "impl-align")):
            output_path = artifacts / f"{prefix}-{sec}-output.md"
            if not output_path.exists():
                continue
            text = output_path.read_text(encoding="utf-8")
            verdict = self._section_alignment.parse_alignment_verdict(text)
            ts = datetime.now(timezone.utc).isoformat()
            if verdict is not None:
                problems = verdict.get("problems", [])
                problems_count = (len(problems) if isinstance(problems, list)
                                  else (1 if problems else 0))
                verdicts.append({
                    "stage": stage,
                    "frame_ok": verdict.get("frame_ok", True),
                    "aligned": verdict.get("aligned", False),
                    "problems_count": problems_count,
                    "timestamp": ts,
                })
            else:
                verdicts.append({
                    "stage": stage,
                    "result": "MISSING_JSON",
                    "timestamp": ts,
                })
        return verdicts

    def write_traceability_index(
        self,
        planspace: Path, section: Section,
        modified_files: list[str],
    ) -> None:
        """Write a traceability index for a completed section."""
        paths = PathRegistry(planspace)
        sec = section.number

        proposal_excerpt = paths.proposal_excerpt(sec)
        alignment_excerpt = paths.alignment_excerpt(sec)
        integration_proposal = paths.proposal(sec)
        artifacts = paths.artifacts

        index = {
            "section": sec,
            "excerpt_paths": {
                "proposal": str(proposal_excerpt),
                "alignment": str(alignment_excerpt),
            },
            "excerpt_hashes": {
                "proposal": self._file_sha256(proposal_excerpt),
                "alignment": self._file_sha256(alignment_excerpt),
            },
            "integration_proposal": {
                "path": str(integration_proposal),
                "hash": self._file_sha256(integration_proposal),
            },
            "microstrategy": _optional_artifact(paths.microstrategy(sec), self._hasher),
            "todos_extraction": _optional_artifact(paths.todos(sec), self._hasher),
            "alignment_surface": _optional_artifact(paths.alignment_surface(sec), self._hasher),
            "problem_frame": _optional_artifact(paths.problem_frame(sec), self._hasher),
            "modified_files": modified_files,
            "alignment_verdicts": self._collect_alignment_verdicts(artifacts, sec),
            "governance": {
                "packet_path": str(paths.governance_packet(sec)),
                "packet_hash": self._hasher.file_hash(paths.governance_packet(sec)),
                **_proposal_governance_ids(planspace, sec, self._artifact_io),
            },
        }

        trace_path = paths.trace_index(sec)
        self._artifact_io.write_json(trace_path, index)
        self._logger.log(f"Section {sec}: traceability index written to {trace_path}")

    def update_trace_governance(
        self,
        planspace: Path,
        section_number: str,
        *,
        problem_ids: list[str] | None = None,
        pattern_ids: list[str] | None = None,
        profile_id: str | None = None,
    ) -> bool:
        """Update governance fields in an existing trace index."""
        paths = PathRegistry(planspace)
        trace_path = paths.trace_index(section_number)
        data = self._artifact_io.read_json(trace_path)
        if not isinstance(data, dict):
            return False

        governance = data.get("governance", {})
        if not isinstance(governance, dict):
            governance = {}

        merged_problem_ids = list(governance.get("problem_ids", []))
        if not isinstance(merged_problem_ids, list):
            merged_problem_ids = []
        merged_pattern_ids = list(governance.get("pattern_ids", []))
        if not isinstance(merged_pattern_ids, list):
            merged_pattern_ids = []

        if problem_ids:
            for problem_id in problem_ids:
                value = str(problem_id).strip()
                if value and value not in merged_problem_ids:
                    merged_problem_ids.append(value)

        if pattern_ids:
            for pattern_id in pattern_ids:
                value = str(pattern_id).strip()
                if value and value not in merged_pattern_ids:
                    merged_pattern_ids.append(value)

        governance["packet_path"] = str(paths.governance_packet(section_number))
        governance["packet_hash"] = self._hasher.file_hash(paths.governance_packet(section_number))
        governance["problem_ids"] = merged_problem_ids
        governance["pattern_ids"] = merged_pattern_ids
        if profile_id is not None:
            governance["profile_id"] = profile_id
        else:
            governance.setdefault("profile_id", "")

        data["governance"] = governance
        self._artifact_io.write_json(trace_path, data)
        return True
