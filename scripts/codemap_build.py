#!/usr/bin/env python3
"""Build or reuse codemap artifact for quick scan orchestration.

Usage::

    uv run python scripts/codemap_build.py <planspace> <codespace>
        <structural_scan_path> <codemap_path> <scan_log_dir>
"""

from __future__ import annotations

import concurrent.futures
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path


def log_phase_failure(
    scan_log_dir: Path, phase: str, context: str, message: str,
) -> None:
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
            cmd,
            stdout=stdout_handle,
            stderr=stderr_handle,
            check=False,
        )
        return proc.returncode
    finally:
        if stdout_handle:
            stdout_handle.close()
        if stderr_handle:
            stderr_handle.close()


def validate_codemap_format(codemap_text: str) -> None:
    """Validate that codemap text contains required headings."""
    required_headings = (
        "## Project Shape",
        "## Directory Map",
        "## Cross-Cutting Patterns",
    )
    normalized = codemap_text.lower()
    for heading in required_headings:
        if heading.lower() not in normalized:
            msg = f"codemap format missing required heading: {heading}"
            print(f"[WARN] {msg}", file=sys.stderr)


def determine_project_size(
    structural_scan_path: Path, codespace: Path,
) -> str:
    """Determine project size class from structural scan."""
    total_files: int | None = None

    scan_exists = (
        structural_scan_path.is_file()
        and structural_scan_path.stat().st_size > 0
    )
    if scan_exists:
        in_distribution = False
        sum_counts = 0
        found_counts = False
        scan_text = structural_scan_path.read_text(
            encoding="utf-8", errors="ignore",
        )
        for line in scan_text.splitlines():
            if re.match(r"^##\s+.*File Type Distribution", line):
                in_distribution = True
                continue
            if in_distribution and re.match(r"^##\s+", line):
                in_distribution = False
            if in_distribution and line.startswith("|"):
                parts = [part.strip() for part in line.split("|") if part.strip()]
                for token in reversed(parts):
                    if token.isdigit():
                        sum_counts += int(token)
                        found_counts = True
                        break
        if found_counts:
            total_files = sum_counts

    if total_files is None or total_files <= 0:
        total_files = sum(
            1 for p in codespace.rglob("*")
            if p.is_file() and ".git" not in p.parts
        )

    if total_files < 50:
        return "small"
    if total_files <= 500:
        return "medium"
    return "large"


def identify_regions(
    structural_scan_path: Path,
    codespace: Path,
    size_class: str,
) -> list[str]:
    """Identify top-level regions for tiered codemap building."""
    if size_class == "small":
        return ["."]

    regions: list[str] = []
    scan_exists = (
        structural_scan_path.is_file()
        and structural_scan_path.stat().st_size > 0
    )
    if scan_exists:
        in_tree = False
        scan_text = structural_scan_path.read_text(
            encoding="utf-8", errors="ignore",
        )
        for raw_line in scan_text.splitlines():
            if re.match(r"^##\s+.*Directory Tree", raw_line):
                in_tree = True
                continue
            if in_tree and re.match(r"^##\s+", raw_line):
                in_tree = False
            if not in_tree:
                continue

            line = raw_line.strip()
            if not line:
                continue
            line = re.sub(r"^[-*+]\s+", "", line)
            line = re.sub(r"^[|`\s]+", "", line)
            line = re.sub(r"^[^\w./-]*[├└]──\s*", "", line)
            line = line.split()[0] if line.split() else ""
            line = line.rstrip("/").lstrip("./")
            if not line:
                continue
            head = line.split("/")[0]
            if head in {"", ".", ".git", ".gitignore", "...", "-"}:
                continue
            regions.append(head)

    if not regions:
        regions = [
            p.name for p in sorted(codespace.iterdir())
            if p.is_dir() and p.name != ".git"
        ]

    # de-duplicate preserving order
    seen: set[str] = set()
    unique_regions: list[str] = []
    for region in regions:
        if region not in seen:
            seen.add(region)
            unique_regions.append(region)
    return unique_regions


def build_codemap_small(
    codespace: Path,
    structural_scan_path: Path,
    codemap_path: Path,
    scan_log_dir: Path,
) -> int:
    """Build a single-pass codemap for small projects."""
    codemap_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_file = scan_log_dir / "codemap-build-prompt.md"
    stderr_file = scan_log_dir / "codemap-build.stderr.log"
    tmp_codemap = scan_log_dir / "codemap-build-output.md"

    prompt_file.write_text(
        "\n".join(
            [
                "# Task: Build Repository Codemap (Small Project)",
                "",
                "Generate a concise codemap markdown document for this repository.",
                "",
                "Inputs:",
                f"- Project root: {codespace}",
                f"- Structural scan artifact: {structural_scan_path}",
                "",
                "Read the structural scan artifact first, then produce"
                " codemap output in exactly this format:",
                "",
                "# Codemap: <project-name>",
                "## Project Shape",
                "## Directory Map",
                "### <directory>/",
                "Purpose: / Key files: / File types: / Relationships:",
                "## Cross-Cutting Patterns",
                "",
                "Requirements:",
                "- Output markdown only; no code fences.",
                "- Keep entries concise (3-5 lines per section).",
                "- Total output should be under 5KB.",
                "- Prefer concrete repo-relative paths.",
            ]
        ),
        encoding="utf-8",
    )

    agent_cmd = [
        "uv", "run", "--frozen", "agents",
        "--model", "claude-opus",
        "--project", str(codespace),
        "--file", str(prompt_file),
    ]
    rc = run_cmd(
        agent_cmd,
        stdout_path=tmp_codemap,
        stderr_path=stderr_file,
    )
    if rc != 0:
        log_phase_failure(
            scan_log_dir, "quick-codemap", codemap_path.name,
            f"codemap build command failed (see {stderr_file})",
        )
        return 1
    tmp_text = tmp_codemap.read_text(
        encoding="utf-8", errors="ignore",
    ).strip() if tmp_codemap.is_file() else ""
    if not tmp_text:
        log_phase_failure(
            scan_log_dir, "quick-codemap",
            codemap_path.name, "codemap output is empty",
        )
        return 1

    tmp_codemap.replace(codemap_path)
    codemap_text = codemap_path.read_text(encoding="utf-8", errors="ignore")
    validate_codemap_format(codemap_text)
    print(f"[CODEMAP] Wrote: {codemap_path}")
    return 0


def build_region_summary(
    region: str,
    codespace: Path,
    structural_scan_path: Path,
    scan_log_dir: Path,
) -> tuple[str, bool]:
    """Build a codemap summary for a single region."""
    safe_region = re.sub(r"[^A-Za-z0-9._-]", "_", region)
    prompt_file = scan_log_dir / f"codemap-region-{safe_region}-prompt.md"
    output_file = scan_log_dir / f"codemap-region-{safe_region}-output.md"
    stderr_file = scan_log_dir / f"codemap-region-{safe_region}.stderr.log"

    prompt_file.write_text(
        "\n".join(
            [
                "# Task: Build Region Codemap Summary",
                "",
                f"Region path: {region}",
                f"Project root: {codespace}",
                f"Structural scan artifact: {structural_scan_path}",
                "",
                "Goal:",
                "- Characterize this region's purpose, key files,"
                " file types, and relationships.",
                "- Use GLM for file reads when needed.",
                "",
                "GLM read pattern (one file per call):",
                "1) Write a GLM prompt file for a target file"
                " in this region.",
                "2) Run: uv run --frozen agents --model glm"
                f" --project \"{codespace}\""
                " --file <glm-prompt-file>",
                "3) Repeat sequentially for representative files.",
                "",
                "Use this GLM prompt template per file:",
                "# Task: Characterize Directory Region",
                "",
                "Read the following files in {directory}:",
                "{file_list}",
                "",
                "Write a summary covering:",
                "- What this directory is for (1-2 sentences)",
                "- Key files and their roles",
                "- How this directory relates to the rest of"
                " the project",
                "",
                "If a GLM dispatch fails, note the failure and"
                " continue with the remaining files. Produce a"
                " region summary from whatever files you"
                " successfully read.",
                "",
                "Output format:",
                f"### {region}/",
                "Purpose: <short purpose>",
                "Key files: <comma-separated repo-relative paths>",
                "File types: <short list>",
                "Relationships: <cross-directory links>",
                "",
                "Keep output concise and factual.",
            ]
        ),
        encoding="utf-8",
    )

    agent_cmd = [
        "uv", "run", "--frozen", "agents",
        "--model", "claude-opus",
        "--project", str(codespace),
        "--file", str(prompt_file),
    ]
    rc = run_cmd(
        agent_cmd,
        stdout_path=output_file,
        stderr_path=stderr_file,
    )
    if rc != 0:
        return region, False
    out_text = output_file.read_text(
        encoding="utf-8", errors="ignore",
    ).strip() if output_file.is_file() else ""
    if not out_text:
        return region, False
    return region, True


def synthesize_codemap(
    codespace: Path,
    structural_scan_path: Path,
    codemap_path: Path,
    scan_log_dir: Path,
    size_class: str,
) -> int:
    """Synthesize region summaries into a unified codemap."""
    prompt_file = scan_log_dir / "codemap-synthesis-prompt.md"
    stderr_file = scan_log_dir / "codemap-synthesis.stderr.log"
    tmp_codemap = scan_log_dir / "codemap-synthesis-output.md"
    region_list_file = scan_log_dir / "codemap-region-summaries.list"
    budget_kb = 15 if size_class == "medium" else 30

    region_outputs = sorted(scan_log_dir.glob("codemap-region-*-output.md"))
    region_list_file.write_text(
        "\n".join(str(path) for path in region_outputs) + "\n",
        encoding="utf-8",
    )

    prompt_file.write_text(
        "\n".join(
            [
                "# Task: Synthesize Final Repository Codemap",
                "",
                "Read all per-region codemap summaries listed in this file:",
                str(region_list_file),
                "",
                f"Project root: {codespace}",
                f"Structural scan artifact: {structural_scan_path}",
                "",
                "Generate final codemap output in exactly this format:",
                "",
                "# Codemap: <project-name>",
                "## Project Shape",
                "## Directory Map",
                "### <directory>/",
                "Purpose: / Key files: / File types: / Relationships:",
                "## Cross-Cutting Patterns",
                "",
                "Requirements:",
                "- Output markdown only; no code fences.",
                "- Use available region summaries; if one is"
                " missing, proceed with remaining summaries.",
                "- Keep directory entries concise (3-5 lines each).",
                f"- Total output should be under {budget_kb}KB.",
            ]
        ),
        encoding="utf-8",
    )

    agent_cmd = [
        "uv", "run", "--frozen", "agents",
        "--model", "claude-opus",
        "--project", str(codespace),
        "--file", str(prompt_file),
    ]
    rc = run_cmd(
        agent_cmd,
        stdout_path=tmp_codemap,
        stderr_path=stderr_file,
    )
    if rc != 0:
        log_phase_failure(
            scan_log_dir,
            "quick-codemap-synthesis",
            codemap_path.name,
            f"synthesis command failed (see {stderr_file})",
        )
        return 1
    tmp_text = tmp_codemap.read_text(
        encoding="utf-8", errors="ignore",
    ).strip() if tmp_codemap.is_file() else ""
    if not tmp_text:
        log_phase_failure(
            scan_log_dir, "quick-codemap-synthesis",
            codemap_path.name, "synthesis output is empty",
        )
        return 1

    tmp_codemap.replace(codemap_path)
    codemap_text = codemap_path.read_text(
        encoding="utf-8", errors="ignore",
    )
    validate_codemap_format(codemap_text)
    print(f"[CODEMAP] Wrote: {codemap_path}")
    return 0


def build_codemap_tiered(
    codespace: Path,
    structural_scan_path: Path,
    codemap_path: Path,
    scan_log_dir: Path,
    size_class: str,
) -> int:
    """Build a tiered codemap for medium/large projects."""
    codemap_path.parent.mkdir(parents=True, exist_ok=True)
    for path in scan_log_dir.glob("codemap-region-*-output.md"):
        path.unlink(missing_ok=True)
    for path in scan_log_dir.glob("codemap-region-*-prompt.md"):
        path.unlink(missing_ok=True)

    regions = identify_regions(structural_scan_path, codespace, size_class)
    if not regions:
        log_phase_failure(
            scan_log_dir, "quick-codemap-region-identify",
            size_class, "no regions found",
        )
        return 1

    max_workers = 5
    failed_regions: list[str] = []
    pool = concurrent.futures.ThreadPoolExecutor(
        max_workers=max_workers,
    )
    with pool as executor:
        futures = [
            executor.submit(
                build_region_summary, region,
                codespace, structural_scan_path,
                scan_log_dir,
            )
            for region in regions
        ]
        for future in concurrent.futures.as_completed(futures):
            region, ok = future.result()
            if not ok:
                failed_regions.append(region)
                log_phase_failure(
                    scan_log_dir,
                    "quick-codemap-region-dispatch",
                    region,
                    "region command failed or empty",
                )

    if len(failed_regions) == len(regions):
        return 1

    rc = synthesize_codemap(
        codespace, structural_scan_path,
        codemap_path, scan_log_dir, size_class,
    )
    if rc != 0:
        log_phase_failure(
            scan_log_dir, "quick-codemap-synthesis",
            size_class, "failed to synthesize codemap",
        )
        return 1
    return 0


def main(argv: list[str]) -> int:
    """CLI entry point for codemap building."""
    if len(argv) != 6:
        print(
            "Usage: codemap_build.py <planspace> <codespace>"
            " <structural_scan_path> <codemap_path>"
            " <scan_log_dir>",
            file=sys.stderr,
        )
        return 1

    _planspace = Path(argv[1])
    codespace = Path(argv[2])
    structural_scan_path = Path(argv[3])
    codemap_path = Path(argv[4])
    scan_log_dir = Path(argv[5])

    if codemap_path.is_file() and codemap_path.stat().st_size > 0:
        print(f"[CODEMAP] Reusing existing artifact: {codemap_path}")
        return 0

    try:
        size_class = determine_project_size(structural_scan_path, codespace)
    except Exception:
        log_phase_failure(
            scan_log_dir, "quick-codemap-size-detect",
            codemap_path.name,
            "failed to determine project size",
        )
        return 1

    if size_class == "small":
        return build_codemap_small(
            codespace, structural_scan_path,
            codemap_path, scan_log_dir,
        )
    if size_class in {"medium", "large"}:
        return build_codemap_tiered(
            codespace, structural_scan_path,
            codemap_path, scan_log_dir, size_class,
        )

    log_phase_failure(
        scan_log_dir, "quick-codemap-size-detect",
        codemap_path.name,
        f"unknown size class: {size_class}",
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
