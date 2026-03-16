"""Bootstrap readiness assessment.

Checks which pre-section-loop artifacts exist and returns the next
stage to execute. Follows the readiness-gate pattern: assess artifact
presence, return the first actionable gap.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from orchestrator.path_registry import PathRegistry


STAGE_DECOMPOSE = "decompose"
STAGE_CODEMAP = "codemap"
STAGE_EXPLORE = "explore"

_ALL_STAGES = (STAGE_DECOMPOSE, STAGE_CODEMAP, STAGE_EXPLORE)


@dataclass
class BootstrapStatus:
    """Result of a bootstrap readiness assessment."""
    ready: bool
    next_stage: str | None = None  # "decompose" | "codemap" | "explore" | None
    completed: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


class BootstrapAssessor:
    """Evaluates which bootstrap artifacts exist and which stage to run next.

    Assessment order matches the dependency graph:
    1. Sections + proposal + alignment exist? If not -> "decompose"
    2. Codemap exists and non-empty? If not -> "codemap"
    3. All section files have "## Related Files"? If not -> "explore"
    4. All present -> ready=True
    """

    def assess(self, planspace: Path) -> BootstrapStatus:
        registry = PathRegistry(planspace)
        completed = []
        missing = []

        # Stage A: decompose — sections + proposal + alignment
        sections = sorted(registry.sections_dir().glob("section-*.md"))
        has_sections = len(sections) > 0
        has_proposal = registry.global_proposal().is_file() and registry.global_proposal().stat().st_size > 0
        has_alignment = registry.global_alignment().is_file() and registry.global_alignment().stat().st_size > 0

        if has_sections and has_proposal and has_alignment:
            completed.append(STAGE_DECOMPOSE)
        else:
            if not has_sections:
                missing.append("sections")
            if not has_proposal:
                missing.append("proposal.md")
            if not has_alignment:
                missing.append("alignment.md")
            return BootstrapStatus(
                ready=False, next_stage=STAGE_DECOMPOSE,
                completed=completed, missing=missing,
            )

        # Stage B: codemap
        codemap = registry.codemap()
        if codemap.is_file() and codemap.stat().st_size > 0:
            completed.append(STAGE_CODEMAP)
        else:
            missing.append("codemap.md")
            return BootstrapStatus(
                ready=False, next_stage=STAGE_CODEMAP,
                completed=completed, missing=missing,
            )

        # Stage C: explore — all sections have "## Related Files"
        all_explored = True
        for section_file in sections:
            text = section_file.read_text(encoding="utf-8")
            if "## Related Files" not in text:
                all_explored = False
                missing.append(f"{section_file.name} missing Related Files")

        if all_explored:
            completed.append(STAGE_EXPLORE)
        else:
            return BootstrapStatus(
                ready=False, next_stage=STAGE_EXPLORE,
                completed=completed, missing=missing,
            )

        return BootstrapStatus(ready=True, completed=completed, missing=[])
