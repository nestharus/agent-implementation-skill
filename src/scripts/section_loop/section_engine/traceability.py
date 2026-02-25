import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from ..alignment import _parse_alignment_verdict
from ..communication import log
from ..types import Section


def _file_sha256(path: Path) -> str:
    """Return hex SHA-256 of a file, or empty string if missing."""
    if not path.exists():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_traceability_index(
    planspace: Path, section: Section, codespace: Path,
    modified_files: list[str],
) -> None:
    """Write a traceability index for a completed section.

    Creates artifacts/trace/section-<n>.json containing hashes of all
    authoritative artifacts, the list of modified files, and alignment
    verdicts extracted from alignment output files.
    """
    artifacts = planspace / "artifacts"
    trace_dir = artifacts / "trace"
    trace_dir.mkdir(parents=True, exist_ok=True)
    sec = section.number

    # Artifact paths
    proposal_excerpt = (artifacts / "sections"
                        / f"section-{sec}-proposal-excerpt.md")
    alignment_excerpt = (artifacts / "sections"
                         / f"section-{sec}-alignment-excerpt.md")
    integration_proposal = (artifacts / "proposals"
                            / f"section-{sec}-integration-proposal.md")
    microstrategy = (artifacts / "proposals"
                     / f"section-{sec}-microstrategy.md")
    todos_extraction = (artifacts / "todos"
                        / f"section-{sec}-todos.md")
    alignment_surface = (artifacts / "sections"
                         / f"section-{sec}-alignment-surface.md")
    problem_frame = (artifacts / "sections"
                     / f"section-{sec}-problem-frame.md")

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
    }

    trace_path = trace_dir / f"section-{sec}.json"
    trace_path.write_text(
        json.dumps(index, indent=2), encoding="utf-8",
    )
    log(f"Section {sec}: traceability index written to {trace_path}")


def _verify_traceability(planspace: Path, section_number: str) -> list[str]:
    """Verify traceability index for a section.

    Checks:
    - Required artifacts exist
    - Hashes match current file contents
    - Alignment verdicts exist for each boundary

    Returns a list of violations (empty = pass).
    """
    trace_path = (planspace / "artifacts" / "trace"
                  / f"section-{section_number}.json")
    violations: list[str] = []

    if not trace_path.exists():
        violations.append(f"Traceability index missing: {trace_path}")
        return violations

    try:
        index = json.loads(trace_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        violations.append(f"Traceability index unreadable: {exc}")
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
