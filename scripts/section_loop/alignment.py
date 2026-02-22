from pathlib import Path

from .communication import log
from .dispatch import dispatch_agent
from .pipeline_control import poll_control_messages
from .types import Section


def collect_modified_files(
    planspace: Path, section: Section, codespace: Path,
) -> list[str]:
    """Collect modified file paths from the implementation report.

    Normalizes all paths to safe relative paths under ``codespace``.
    Absolute paths are converted to relative (if under codespace) or
    rejected. Paths containing ``..`` that escape codespace are rejected.
    """
    artifacts = planspace / "artifacts"
    modified_report = artifacts / f"impl-{section.number}-modified.txt"
    codespace_resolved = codespace.resolve()
    modified = set()
    if modified_report.exists():
        for line in modified_report.read_text(encoding="utf-8").strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            pp = Path(line)
            if pp.is_absolute():
                # Convert absolute to relative if under codespace
                try:
                    rel = pp.resolve().relative_to(codespace_resolved)
                except ValueError:
                    log(f"  WARNING: reported path outside codespace, "
                        f"skipping: {line}")
                    continue
            else:
                # Resolve relative path and ensure it stays under codespace
                full = (codespace / pp).resolve()
                try:
                    rel = full.relative_to(codespace_resolved)
                except ValueError:
                    log(f"  WARNING: reported path escapes codespace, "
                        f"skipping: {line}")
                    continue
            modified.add(str(rel))
    return list(modified)


def _extract_problems(result: str) -> str | None:
    """Extract problem list from an alignment check result.

    Returns the problems text if PROBLEMS: found, None if ALIGNED.
    Uses first non-empty line for exact-match classification to avoid
    misclassifying outputs containing substrings like "MISALIGNED".
    """
    # Find the first non-empty line for exact classification
    first_line = ""
    for line in result.split("\n"):
        stripped = line.strip()
        if stripped:
            first_line = stripped
            break

    # Exact match ALIGNED on first line (not a substring of MISALIGNED etc.)
    if first_line == "ALIGNED" and "PROBLEMS:" not in result \
            and "UNDERSPECIFIED" not in result:
        return None
    # Extract everything after PROBLEMS:
    idx = result.find("PROBLEMS:")
    if idx != -1:
        return result[idx + len("PROBLEMS:"):].strip()
    # Fallback: return the whole result as problems if not ALIGNED
    return result.strip()


def _check_alignment_frame(result: str) -> str | None:
    """Detect if alignment output uses invalid feature-audit framing.

    Returns a warning string if feature-counting language detected,
    None if the output uses proper alignment framing.
    """
    # Simple heuristic: count checklist-like patterns
    checklist_patterns = 0
    for line in result.split("\n"):
        stripped = line.strip()
        # Detect "Feature X: implemented/done/complete/missing" patterns
        if any(stripped.lower().endswith(suffix)
               for suffix in (": implemented", ": done", ": complete",
                               ": missing", ": not implemented",
                               ": partially implemented")):
            checklist_patterns += 1
    if checklist_patterns >= 3:
        return (f"Alignment output contains {checklist_patterns} "
                f"feature-checklist lines — this is audit framing, "
                f"not alignment. Requesting re-check.")
    return None


def _run_alignment_check_with_retries(
    section: Section, planspace: Path, codespace: Path, parent: str,
    sec_num: str,
    output_prefix: str = "align",
    max_retries: int = 2,
) -> str | None:
    """Run an alignment check with TIMEOUT retry logic.

    Dispatches Opus for an implementation alignment check. If the agent
    times out, retries up to max_retries times. Returns the alignment
    result text, or None if all retries exhausted.
    """
    from .prompts import write_impl_alignment_prompt

    artifacts = planspace / "artifacts"
    for attempt in range(1, max_retries + 2):  # 1 initial + max_retries
        # Poll for control messages before each dispatch attempt
        ctrl = poll_control_messages(planspace, parent,
                                     current_section=sec_num)
        if ctrl == "alignment_changed":
            return "ALIGNMENT_CHANGED_PENDING"
        align_prompt = write_impl_alignment_prompt(
            section, planspace, codespace,
        )
        align_output = artifacts / f"{output_prefix}-{sec_num}-output.md"
        result = dispatch_agent(
            "claude-opus", align_prompt, align_output,
            planspace, parent, codespace=codespace,
            section_number=sec_num,
            agent_file="alignment-judge.md",
        )
        if result == "ALIGNMENT_CHANGED_PENDING":
            return result  # Caller must handle
        if not result.startswith("TIMEOUT:"):
            # Gate: reject feature-audit framing
            frame_warning = _check_alignment_frame(result)
            if frame_warning:
                log(f"  alignment frame check: {frame_warning}")
                # Don't count as a retry — it's a framing issue
                continue
            return result
        log(f"  alignment check for section {sec_num} timed out "
            f"(attempt {attempt}/{max_retries + 1})")
    return None
