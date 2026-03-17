"""Bootstrap readiness assessment.

Checks which pre-section-loop artifacts exist and returns the next
stage to execute. Follows the readiness-gate pattern: assess artifact
presence, return the first actionable gap.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from orchestrator.path_registry import PathRegistry


STAGE_DECOMPOSE = "decompose"
STAGE_CODEMAP = "codemap"
STAGE_EXPLORE = "explore"
STAGE_SUBSTRATE = "substrate"

_ALL_STAGES = (STAGE_DECOMPOSE, STAGE_CODEMAP, STAGE_EXPLORE, STAGE_SUBSTRATE)


@dataclass
class BootstrapStatus:
    """Result of a bootstrap readiness assessment."""
    ready: bool
    next_stage: str | None = None  # "decompose" | "codemap" | "explore" | "substrate" | None
    completed: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


class BootstrapAssessor:
    """Evaluates which bootstrap artifacts exist and which stage to run next.

    Assessment order matches the dependency graph:
    1. Sections + proposal + alignment exist? If not -> "decompose"
    2. Codemap exists and non-empty? If not -> "codemap"
    3. All section files have "## Related Files"? If not -> "explore"
    4. Substrate artifact or terminal status exists? If not -> "substrate"
    5. All present -> ready=True
    """

    def assess(self, planspace: Path) -> BootstrapStatus:
        registry = PathRegistry(planspace)
        completed = []
        missing = []

        # Stage A: decompose — sections + proposal + alignment
        # Match only section-NN.md (2-digit number), not section-NN-excerpt.md etc.
        import re
        sections = sorted(
            f for f in registry.sections_dir().glob("section-*.md")
            if re.match(r"section-\d+\.md$", f.name)
        )
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

        # Stage D: substrate — SIS discovery artifact or terminal status
        substrate_dir = registry.substrate_dir()
        substrate_md = substrate_dir / "substrate.md"
        status_json = substrate_dir / "status.json"

        substrate_done = False
        if substrate_md.is_file() and substrate_md.stat().st_size > 0:
            substrate_done = True
        elif status_json.is_file():
            try:
                data = json.loads(status_json.read_text(encoding="utf-8"))
                if data.get("state") in ("complete", "skipped"):
                    substrate_done = True
            except (json.JSONDecodeError, OSError):
                pass

        if substrate_done:
            completed.append(STAGE_SUBSTRATE)
        else:
            missing.append("substrate artifacts")
            return BootstrapStatus(
                ready=False, next_stage=STAGE_SUBSTRATE,
                completed=completed, missing=missing,
            )

        return BootstrapStatus(ready=True, completed=completed, missing=[])
