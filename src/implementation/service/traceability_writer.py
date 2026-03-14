from datetime import datetime, timezone
from pathlib import Path

from containers import Services
from orchestrator.path_registry import PathRegistry

from orchestrator.types import Section


def _file_sha256(path: Path) -> str:
    """Return hex SHA-256 of a file, or empty string if missing."""
    return Services.hasher().file_hash(path)


def _proposal_governance_ids(planspace: Path, section_number: str) -> dict:
    """Extract governance identity from proposal-state if available."""
    from proposal.repository.state import load_proposal_state

    paths = PathRegistry(planspace)
    state_path = paths.proposal_state(section_number)
    state = load_proposal_state(state_path)
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


def _collect_alignment_verdicts(artifacts: Path, sec: str) -> list[dict]:
    """Collect alignment verdicts from output files using structured JSON."""
    verdicts: list[dict] = []
    for stage, prefix in (("proposal", "intg-align"),
                          ("implementation", "impl-align")):
        output_path = artifacts / f"{prefix}-{sec}-output.md"
        if not output_path.exists():
            continue
        text = output_path.read_text(encoding="utf-8")
        verdict = Services.section_alignment().parse_alignment_verdict(text)
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


def _optional_artifact(path: Path) -> dict | None:
    """Return path+hash dict for an artifact if it exists, else None."""
    if not path.exists():
        return None
    return {"path": str(path), "hash": _file_sha256(path)}


def write_traceability_index(
    planspace: Path, section: Section,
    modified_files: list[str],
) -> None:
    """Write a traceability index for a completed section."""
    paths = PathRegistry(planspace)
    trace_dir = paths.trace_dir()
    trace_dir.mkdir(parents=True, exist_ok=True)
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
            "proposal": _file_sha256(proposal_excerpt),
            "alignment": _file_sha256(alignment_excerpt),
        },
        "integration_proposal": {
            "path": str(integration_proposal),
            "hash": _file_sha256(integration_proposal),
        },
        "microstrategy": _optional_artifact(paths.microstrategy(sec)),
        "todos_extraction": _optional_artifact(paths.todos(sec)),
        "alignment_surface": _optional_artifact(paths.alignment_surface(sec)),
        "problem_frame": _optional_artifact(paths.problem_frame(sec)),
        "modified_files": modified_files,
        "alignment_verdicts": _collect_alignment_verdicts(artifacts, sec),
        "governance": {
            "packet_path": str(paths.governance_packet(sec)),
            "packet_hash": Services.hasher().file_hash(paths.governance_packet(sec)),
            **_proposal_governance_ids(planspace, sec),
        },
    }

    trace_path = paths.trace_index(sec)
    Services.artifact_io().write_json(trace_path, index)
    Services.logger().log(f"Section {sec}: traceability index written to {trace_path}")


def update_trace_governance(
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
    data = Services.artifact_io().read_json(trace_path)
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
    governance["packet_hash"] = Services.hasher().file_hash(paths.governance_packet(section_number))
    governance["problem_ids"] = merged_problem_ids
    governance["pattern_ids"] = merged_pattern_ids
    if profile_id is not None:
        governance["profile_id"] = profile_id
    else:
        governance.setdefault("profile_id", "")

    data["governance"] = governance
    Services.artifact_io().write_json(trace_path, data)
    return True


