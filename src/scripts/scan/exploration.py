"""Section exploration + validation + patching."""

from __future__ import annotations

from pathlib import Path

from lib.scan.scan_phase_logger import log_phase_failure
from lib.scan.scan_template_loader import load_scan_template
from lib.scan.scan_related_files import (
    apply_related_files_update,
    list_section_files,
    validate_existing_related_files,
)

from prompt_safety import validate_dynamic_content

from .dispatch import dispatch_agent, read_scan_model_policy


def run_section_exploration(
    *,
    sections_dir: Path,
    codemap_path: Path,
    codespace: Path,
    artifacts_dir: Path,
    scan_log_dir: Path,
    model_policy: dict[str, str] | None = None,
) -> None:
    """Dispatch agents per section to identify related files."""
    if model_policy is None:
        model_policy = read_scan_model_policy(artifacts_dir)
    section_files = list_section_files(sections_dir)
    corrections_file = artifacts_dir / "signals" / "codemap-corrections.json"

    for section_file in section_files:
        section_name = section_file.stem  # e.g. "section-01"

        # If section already has Related Files, run validation pass
        section_text = section_file.read_text()
        if "## Related Files" in section_text:
            validate_existing_related_files(
                section_file=section_file,
                section_name=section_name,
                codemap_path=codemap_path,
                codespace=codespace,
                artifacts_dir=artifacts_dir,
                scan_log_dir=scan_log_dir,
                corrections_file=corrections_file,
                model_policy=model_policy,
            )
            continue

        # Fresh exploration
        _explore_section(
            section_file=section_file,
            section_name=section_name,
            codemap_path=codemap_path,
            codespace=codespace,
            artifacts_dir=artifacts_dir,
            scan_log_dir=scan_log_dir,
            corrections_file=corrections_file,
            model_policy=model_policy,
        )


# ------------------------------------------------------------------
# Fresh exploration path
# ------------------------------------------------------------------


def _explore_section(
    *,
    section_file: Path,
    section_name: str,
    codemap_path: Path,
    codespace: Path,
    artifacts_dir: Path,
    scan_log_dir: Path,
    corrections_file: Path,
    model_policy: dict[str, str],
) -> None:
    """Dispatch agent to identify related files for a new section."""
    section_log = scan_log_dir / section_name
    section_log.mkdir(parents=True, exist_ok=True)
    prompt_file = section_log / "explore-prompt.md"
    response_file = section_log / "explore-response.md"
    stderr_file = section_log / "explore.stderr.log"

    corrections_signal = (
        artifacts_dir / "signals" / "codemap-corrections.json"
    )

    prompt = load_scan_template("explore_section.md").format(
        codemap_path=codemap_path,
        section_file=section_file,
        corrections_signal=corrections_signal,
    )
    violations = validate_dynamic_content(prompt)
    if violations:
        log_phase_failure(
            "quick-explore",
            section_name,
            f"prompt blocked — safety violations: {violations}",
            scan_log_dir,
        )
        return
    prompt_file.write_text(prompt)

    result = dispatch_agent(
        model=model_policy.get("exploration", "claude-opus"),
        project=codespace,
        prompt_file=prompt_file,
        agent_file="scan-related-files-explorer.md",
        stdout_file=response_file,
        stderr_file=stderr_file,
    )

    if result.returncode != 0:
        log_phase_failure(
            "quick-explore",
            section_name,
            f"exploration agent failed (see {stderr_file})",
            scan_log_dir,
        )
        return

    # Append only the Related Files block to section file
    if response_file.is_file():
        response_text = response_file.read_text()
        if "## Related Files" in response_text:
            # Extract only the ## Related Files block
            rf_idx = response_text.index("## Related Files")
            rf_block = response_text[rf_idx:]
            # Trim at next ## heading that isn't a ### sub-heading
            lines = rf_block.split("\n")
            end_idx = len(lines)
            for i, line in enumerate(lines[1:], start=1):
                if line.startswith("## ") and not line.startswith("### "):
                    end_idx = i
                    break
            rf_block = "\n".join(lines[:end_idx]).rstrip()

            with section_file.open("a") as f:
                f.write("\n")
                f.write(rf_block)
            print(f"[EXPLORE] {section_name} — related files identified")
        else:
            log_phase_failure(
                "quick-explore",
                section_name,
                "agent output missing Related Files block",
                scan_log_dir,
            )
