#!/usr/bin/env python3
"""Dispatch per-section strategic exploration for quick scan.

Usage:
  uv run python scripts/section_explore.py <planspace> <codespace> \
    <codemap_path> <sections_dir> <scan_log_dir> <workflow_home>
"""

from __future__ import annotations

import concurrent.futures
import subprocess
import sys
from datetime import datetime
from pathlib import Path


def log_phase_failure(scan_log_dir: Path, phase: str, context: str, message: str) -> None:
    """Log a phase failure to the scan log directory."""
    failure_log = scan_log_dir / "failures.log"
    timestamp = datetime.now().astimezone().isoformat()
    failure_log.parent.mkdir(parents=True, exist_ok=True)
    with failure_log.open("a", encoding="utf-8") as f:
        f.write(f"{timestamp} phase={phase} context={context} message={message}\n")
    print(f"[FAIL] phase={phase} context={context} message={message}", file=sys.stderr)


def run_cmd(
    cmd: list[str],
    stdout_path: Path | None = None,
    stderr_path: Path | None = None,
) -> int:
    """Run a command and return the exit code."""
    stdout_handle = stdout_path.open("w", encoding="utf-8") if stdout_path else None
    stderr_handle = stderr_path.open("w", encoding="utf-8") if stderr_path else None
    try:
        proc = subprocess.run(  # noqa: S603
            cmd, stdout=stdout_handle, stderr=stderr_handle, check=False,
        )
        return proc.returncode
    finally:
        if stdout_handle:
            stdout_handle.close()
        if stderr_handle:
            stderr_handle.close()


def section_has_related_entries(section_file: Path) -> bool:
    """Check if a section file already has related-file entries."""
    in_related = False
    for line in section_file.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.strip() == "## Related Files":
            in_related = True
            continue
        if in_related and line.startswith("## "):
            in_related = False
        if in_related and line.startswith("### "):
            return True
    return False


def extract_summary(tool_path: Path, section_file: Path) -> str:
    """Extract a summary from a section file using the summary tool."""
    proc = subprocess.run(  # noqa: S603
        [str(tool_path), str(section_file)],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return "(no summary available)"
    for line in proc.stdout.splitlines():
        if line.startswith("summary: "):
            summary = line[len("summary: ") :].strip()
            return summary if summary else "(no summary available)"
    return "(no summary available)"


def build_prompt(
    section_file: Path,
    section_summary: str,
    codemap_path: Path,
    codespace: Path,
    workflow_home: Path,
) -> str:
    """Build the exploration prompt for a single section."""
    return "\n".join(
        [
            "# Task: Strategic Section Exploration",
            "",
            "Goal:",
            "Identify repository files related to this section"
            " and append canonical related-file entries.",
            "",
            "Inputs:",
            f"- Section file: {section_file}",
            f"- Section summary: {section_summary}",
            f"- Codemap file: {codemap_path}",
            f"- Codespace root: {codespace}",
            "",
            "Strategy (execute all steps):",
            "1. Form hypotheses from codemap + section:",
            "   - Read the section file and codemap.",
            "   - Form explicit hypotheses for candidate"
            " files/directories likely related to this section and why.",
            "2. Verify candidates with GLM:",
            "   - Verify candidate files using single-compare GLM calls, one file per call.",
            "   - Dispatch GLM using:",
            "     uv run --frozen agents --model glm --project <codespace> --file <prompt-file>",
            f"   - For this run, <codespace> is: {codespace}",
            "   - Write this exact template to a prompt file for each GLM check:",
            "```",
            "# Task: File-Section Relevance Check",
            "",
            "Is this source file related to this proposal section?",
            "",
            "## Section Summary",
            "{section_summary}",
            "",
            "## File: {filepath}",
            "{file_content_or_docstring}",
            "",
            "## Instructions",
            "Reply with exactly one line:",
            "RELATED: <brief reason>",
            "or",
            "NOT_RELATED",
            "",
            "Nothing else.",
            "```",
            "   - Replace placeholders before dispatching each GLM check:",
            "     - {section_summary}: the section summary above",
            "     - {filepath}: the candidate file's repo-relative path",
            "     - {file_content_or_docstring}: module docstring or full file content",
            "   - Parse each GLM response by first non-blank line:",
            "     - Starts with `RELATED:`: confirmed related;"
            " capture reason text after the colon.",
            "     - Starts with `NOT_RELATED`: skip candidate.",
            "     - Empty or unrecognized: treat as"
            " `NOT_RELATED` and log anomaly in your summary.",
            "   - For Python files, you may screen with module docstrings first:",
            f"     - `python3 {workflow_home}/tools/extract-docstring-py <filepath>`",
            "     - Use full file content when docstring is absent or insufficient.",
            "3. Explore adjacencies for confirmed matches:",
            "   - For confirmed RELATED files, explore neighboring"
            " files/directories and related modules/imports.",
            "   - Verify additional candidates with the same GLM loop.",
            "4. Discover beyond codemap when gaps are suspected:",
            "   - If gaps remain, inspect directories, grep for"
            " relevant symbols/patterns, and evaluate candidates not listed in codemap.",
            "   - Verify new candidates with GLM.",
            "5. Append canonical related-file entries:",
            "   - Append ONLY new entries to this section file under `## Related Files`:",
            "     ### <repo-relative filepath>",
            "     - Relevance: <brief reason>",
            "   - Resume rule: skip files already present under `## Related Files`.",
            "   - Do not write absolute paths.",
            "   - Ensure each listed file exists under the codespace.",
            "",
            "Exploration bounds:",
            "- Verify at most 20-30 candidate files for this section.",
            "- Prioritize high-signal candidates first.",
            "",
            "Output requirements:",
            "- Update section file directly with canonical entries.",
            "- Provide a short completion summary in stdout.",
        ]
    )


def process_section(
    section_file: Path,
    codespace: Path,
    codemap_path: Path,
    scan_log_dir: Path,
    summary_tool: Path,
    workflow_home: Path,
) -> tuple[str, bool, str]:
    """Process a single section through the exploration pipeline."""
    section_name = section_file.stem
    section_log_dir = scan_log_dir / section_name
    section_log_dir.mkdir(parents=True, exist_ok=True)

    prompt_file = section_log_dir / "quick-explore-prompt.md"
    response_file = section_log_dir / "quick-explore-response.md"
    stderr_file = section_log_dir / "quick-explore.stderr.log"

    if section_has_related_entries(section_file):
        print(f"[EXPLORE] {section_name}: skip (already has related files)")
        return section_name, True, "skip"

    section_summary = extract_summary(summary_tool, section_file)

    prompt_file.write_text(
        build_prompt(section_file, section_summary, codemap_path, codespace, workflow_home),
        encoding="utf-8",
    )

    rc = run_cmd(
        [
            "uv",
            "run",
            "--frozen",
            "agents",
            "--model",
            "claude-opus",
            "--project",
            str(codespace),
            "--file",
            str(prompt_file),
        ],
        stdout_path=response_file,
        stderr_path=stderr_file,
    )
    if rc != 0:
        return section_name, False, f"section exploration failed (see {stderr_file})"

    if not section_has_related_entries(section_file):
        return section_name, False, "agent completed without producing related file entries"

    return section_name, True, "ok"


def main(argv: list[str]) -> int:
    """CLI entry point for section exploration."""
    if len(argv) != 7:
        print(
            "Usage: section_explore.py <planspace> <codespace>"
            " <codemap_path> <sections_dir> <scan_log_dir>"
            " <workflow_home>",
            file=sys.stderr,
        )
        return 1

    _planspace = Path(argv[1])
    codespace = Path(argv[2])
    codemap_path = Path(argv[3])
    sections_dir = Path(argv[4])
    scan_log_dir = Path(argv[5])
    workflow_home = Path(argv[6])

    summary_tool = workflow_home / "tools" / "extract-summary-md"
    section_files = sorted(sections_dir.glob("section-*.md"))

    if not section_files:
        log_phase_failure(scan_log_dir, "quick-explore", "global", "no section files found")
        return 1

    failed_sections: list[str] = []
    successful_sections = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [
            executor.submit(
                process_section,
                section_file,
                codespace,
                codemap_path,
                scan_log_dir,
                summary_tool,
                workflow_home,
            )
            for section_file in section_files
        ]
        for future in concurrent.futures.as_completed(futures):
            section_name, ok, detail = future.result()
            if not ok:
                failed_sections.append(section_name)
                log_phase_failure(scan_log_dir, "quick-explore", section_name, detail)
            else:
                successful_sections += 1

    if failed_sections:
        print(f"[EXPLORE] Failed sections: {' '.join(failed_sections)}")
        if successful_sections > 0:
            print("[EXPLORE] Continuing with partial section exploration success.")
            return 0
    if successful_sections == 0:
        log_phase_failure(
            scan_log_dir, "quick-explore", "global",
            "no sections explored successfully",
        )
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
