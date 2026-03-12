from datetime import datetime, timezone
from pathlib import Path

from containers import Services
from orchestrator.path_registry import PathRegistry

from staleness.service.section_alignment import _parse_alignment_verdict
from orchestrator.types import Section


def _file_sha256(path: Path) -> str:
    """Return hex SHA-256 of a file, or empty string if missing."""
    return Services.hasher().file_hash(path)


def _proposal_governance_ids(planspace: Path, section_number: str) -> dict:
    """Extract governance identity from proposal-state if available."""
    from proposal.repository.state import load_proposal_state

    paths = PathRegistry(planspace)
    state_path = (
        paths.proposals_dir()
        / f"section-{section_number}-proposal-state.json"
    )
    state = load_proposal_state(state_path)
    return {
        "problem_ids": [
            str(x) for x in state.get("problem_ids", [])
            if isinstance(x, str) and x.strip()
        ],
        "pattern_ids": [
            str(x) for x in state.get("pattern_ids", [])
            if isinstance(x, str) and x.strip()
        ],
        "profile_id": state.get("profile_id", "") or "",
    }


def _write_traceability_index(
    planspace: Path, section: Section, codespace: Path,
    modified_files: list[str],
) -> None:
    """Write a traceability index for a completed section.

    Creates artifacts/trace/section-<n>.json containing hashes of all
    authoritative artifacts, the list of modified files, and alignment
    verdicts extracted from alignment output files.
    """
    paths = PathRegistry(planspace)
    artifacts = paths.artifacts
    trace_dir = paths.trace_dir()
    trace_dir.mkdir(parents=True, exist_ok=True)
    sec = section.number

    # Artifact paths
    proposal_excerpt = paths.proposal_excerpt(sec)
    alignment_excerpt = paths.alignment_excerpt(sec)
    integration_proposal = paths.proposal(sec)
    microstrategy = paths.microstrategy(sec)
    todos_extraction = paths.todos(sec)
    alignment_surface = paths.sections_dir() / f"section-{sec}-alignment-surface.md"
    problem_frame = paths.problem_frame(sec)

    # Collect alignment verdicts from output files using structured JSON
    alignment_verdicts: list[dict] = []
    for stage, prefix in (("proposal", "intg-align"),
                          ("implementation", "impl-align")):
        output_path = artifacts / f"{prefix}-{sec}-output.md"
        if not output_path.exists():
            continue
        text = output_path.read_text(encoding="utf-8")
        verdict = _parse_alignment_verdict(text)
        if verdict is not None:
            problems = verdict.get("problems", [])
            problems_count = (len(problems) if isinstance(problems, list)
                              else (1 if problems else 0))
            alignment_verdicts.append({
                "stage": stage,
                "frame_ok": verdict.get("frame_ok", True),
                "aligned": verdict.get("aligned", False),
                "problems_count": problems_count,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        else:
            alignment_verdicts.append({
                "stage": stage,
                "result": "MISSING_JSON",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

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
        "microstrategy": {
            "path": str(microstrategy),
            "hash": _file_sha256(microstrategy),
        } if microstrategy.exists() else None,
        "todos_extraction": {
            "path": str(todos_extraction),
            "hash": _file_sha256(todos_extraction),
        } if todos_extraction.exists() else None,
        "alignment_surface": {
            "path": str(alignment_surface),
            "hash": _file_sha256(alignment_surface),
        } if alignment_surface.exists() else None,
        "problem_frame": {
            "path": str(problem_frame),
            "hash": _file_sha256(problem_frame),
        } if problem_frame.exists() else None,
        "modified_files": modified_files,
        "alignment_verdicts": alignment_verdicts,
        "governance": {
            "packet_path": str(paths.governance_packet(sec)),
            "packet_hash": Services.hasher().file_hash(paths.governance_packet(sec)),
            **_proposal_governance_ids(planspace, sec),
        },
    }

    trace_path = trace_dir / f"section-{sec}.json"
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
    trace_path = paths.trace_dir() / f"section-{section_number}.json"
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


def _verify_traceability(planspace: Path, section_number: str) -> list[str]:
    """Verify traceability index for a section.

    Checks:
    - Required artifacts exist
    - Hashes match current file contents
    - Alignment verdicts exist for each boundary

    Returns a list of violations (empty = pass).
    """
    trace_path = (
        PathRegistry(planspace).trace_dir() / f"section-{section_number}.json"
    )
    violations: list[str] = []

    if not trace_path.exists():
        violations.append(f"Traceability index missing: {trace_path}")
        return violations

    index = Services.artifact_io().read_json(trace_path)
    if index is None:
        violations.append(f"Traceability index unreadable: {trace_path}")
        return violations

    # Check required excerpt artifacts exist and hashes match
    for key in ("proposal", "alignment"):
        path_str = index.get("excerpt_paths", {}).get(key, "")
        expected_hash = index.get("excerpt_hashes", {}).get(key, "")
        if not path_str:
            violations.append(f"Missing excerpt path for {key}")
            continue
        path = Path(path_str)
        if not path.exists():
            violations.append(f"Excerpt file missing: {path}")
            continue
        actual_hash = _file_sha256(path)
        if actual_hash != expected_hash:
            violations.append(
                f"Hash mismatch for {key} excerpt: "
                f"expected {expected_hash[:12]}..., "
                f"got {actual_hash[:12]}..."
            )

    # Check integration proposal exists and hash matches
    ip_info = index.get("integration_proposal", {})
    if ip_info:
        ip_path = Path(ip_info.get("path", ""))
        if not ip_path.exists():
            violations.append(
                f"Integration proposal missing: {ip_path}")
        elif _file_sha256(ip_path) != ip_info.get("hash", ""):
            violations.append(
                "Hash mismatch for integration proposal")

    # Check microstrategy if recorded
    ms_info = index.get("microstrategy")
    if ms_info is not None:
        ms_path = Path(ms_info.get("path", ""))
        if not ms_path.exists():
            violations.append(f"Microstrategy missing: {ms_path}")
        elif _file_sha256(ms_path) != ms_info.get("hash", ""):
            violations.append("Hash mismatch for microstrategy")

    # Check alignment verdicts exist for each boundary
    verdicts = index.get("alignment_verdicts", [])
    verdict_stages = {v.get("stage") for v in verdicts}
    for required_stage in ("proposal", "implementation"):
        if required_stage not in verdict_stages:
            violations.append(
                f"Missing alignment verdict for {required_stage} boundary"
            )

    return violations
