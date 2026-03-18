"""Bootstrap readiness assessment.

Checks which pre-section-loop artifacts exist and returns the next
stage to execute. Follows the readiness-gate pattern: assess artifact
presence, return the first actionable gap.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from orchestrator.path_registry import PathRegistry


logger = logging.getLogger(__name__)

STAGE_DECOMPOSE = "decompose"
STAGE_CODEMAP = "codemap"
STAGE_EXPLORE = "explore"
STAGE_SUBSTRATE = "substrate"

_ALL_STAGES = (STAGE_DECOMPOSE, STAGE_CODEMAP, STAGE_EXPLORE, STAGE_SUBSTRATE)

# Entry classification constants
ENTRY_GREENFIELD = "greenfield"
ENTRY_BROWNFIELD = "brownfield"
ENTRY_PRD = "prd"
ENTRY_PARTIAL_GOVERNANCE = "partial_governance"

# Extensions that indicate source code (not docs, not config)
_CODE_EXTENSIONS = frozenset({
    ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java",
    ".c", ".cpp", ".h", ".hpp", ".cs", ".rb", ".swift", ".kt",
    ".scala", ".clj", ".ex", ".exs", ".zig", ".lua", ".sh",
})

# Governance doc markers — directories whose presence signals
# partial governance state in the codespace.
_GOVERNANCE_DIRS = ("governance/problems", "governance/patterns", "governance/constraints")
_PHILOSOPHY_DIRS = ("philosophy/profiles",)


@dataclass
class EntryClassification:
    """Result of classifying what the user brought to bootstrap."""
    path: str  # one of ENTRY_* constants
    has_code: bool = False
    has_spec: bool = False
    has_governance: bool = False
    has_philosophy: bool = False
    evidence: list[str] = field(default_factory=list)


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

    # ------------------------------------------------------------------
    # Entry classification — mechanical observation of what exists
    # ------------------------------------------------------------------

    def classify_entry(
        self,
        codespace: Path,
        spec_path: Path | None,
    ) -> EntryClassification:
        """Classify the entry path based on what the user brought.

        This is observation only — it does not change routing.  The same
        recursive bootstrap loop runs regardless; only starting conditions
        (governance seeding, problem extraction) differ.

        Returns one of:
            greenfield        — empty or near-empty codespace, no spec
            prd               — spec file present (current default path)
            brownfield        — code files present, no governance docs
            partial_governance — some governance docs already exist
        """
        has_code = self._detect_code_files(codespace)
        has_spec = spec_path is not None and spec_path.is_file()
        has_governance = self._detect_governance_docs(codespace)
        has_philosophy = self._detect_philosophy_docs(codespace)

        evidence: list[str] = []
        if has_code:
            evidence.append("code_files_present")
        if has_spec:
            evidence.append(f"spec_file={spec_path}")
        if has_governance:
            evidence.append("governance_docs_present")
        if has_philosophy:
            evidence.append("philosophy_docs_present")

        # Classification priority: partial_governance > brownfield > prd > greenfield
        if has_governance or has_philosophy:
            path = ENTRY_PARTIAL_GOVERNANCE
        elif has_code and not has_spec:
            path = ENTRY_BROWNFIELD
        elif has_spec:
            path = ENTRY_PRD
        else:
            path = ENTRY_GREENFIELD

        # Brownfield with spec is still brownfield (code dominates)
        if has_code and not has_governance and not has_philosophy and has_spec:
            path = ENTRY_BROWNFIELD
            evidence.append("code_with_spec_treated_as_brownfield")

        classification = EntryClassification(
            path=path,
            has_code=has_code,
            has_spec=has_spec,
            has_governance=has_governance,
            has_philosophy=has_philosophy,
            evidence=evidence,
        )
        logger.info("Entry classification: %s (evidence: %s)", path, evidence)
        return classification

    @staticmethod
    def _detect_code_files(codespace: Path) -> bool:
        """Check whether codespace contains source code files.

        Walks at most two levels deep and returns True on the first
        hit.  Ignores hidden directories and common non-source trees.
        """
        if not codespace.is_dir():
            return False

        skip_dirs = frozenset({
            ".git", ".hg", "node_modules", "__pycache__",
            ".venv", "venv", ".tox", ".mypy_cache",
        })

        for child in codespace.iterdir():
            if child.name.startswith(".") or child.name in skip_dirs:
                continue
            if child.is_file() and child.suffix in _CODE_EXTENSIONS:
                return True
            if child.is_dir():
                for grandchild in child.iterdir():
                    if grandchild.is_file() and grandchild.suffix in _CODE_EXTENSIONS:
                        return True
        return False

    @staticmethod
    def _detect_governance_docs(codespace: Path) -> bool:
        """Check whether codespace has governance markdown with real content."""
        for rel_dir in _GOVERNANCE_DIRS:
            index_path = codespace / rel_dir / "index.md"
            if index_path.is_file():
                text = index_path.read_text(encoding="utf-8")
                # Scaffold-only files don't count as real governance
                from intake.repository.governance_loader import _is_scaffold
                if not _is_scaffold(text):
                    return True
        return False

    @staticmethod
    def _detect_philosophy_docs(codespace: Path) -> bool:
        """Check whether codespace has philosophy profile documents."""
        for rel_dir in _PHILOSOPHY_DIRS:
            profiles_dir = codespace / rel_dir
            if profiles_dir.is_dir():
                md_files = list(profiles_dir.glob("*.md"))
                if md_files:
                    return True
        return False

    # ------------------------------------------------------------------
    # Stage readiness assessment
    # ------------------------------------------------------------------

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
