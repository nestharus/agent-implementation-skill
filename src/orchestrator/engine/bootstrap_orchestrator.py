"""Convergence loop for pre-section-loop artifact production.

Replaces the hard-coded prerequisite check in runner._handoff() with an
adaptive system that can produce all required artifacts (sections, codemap,
related files, proposal, alignment) with retry and crash recovery.

Follows the readiness-gate pattern from the section loop: assess what
exists, dispatch work to fill gaps, recheck until ready or failed.
"""
from __future__ import annotations

import json
import logging
import textwrap
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING

from orchestrator.path_registry import PathRegistry
from orchestrator.service.bootstrap_assessor import (
    ENTRY_PRD,
    STAGE_CODEMAP,
    STAGE_DECOMPOSE,
    STAGE_EXPLORE,
    STAGE_SUBSTRATE,
    BootstrapAssessor,
    EntryClassification,
)

if TYPE_CHECKING:
    from scan.codemap.codemap_builder import CodemapBuilder
    from scan.explore.section_explorer import SectionExplorer

logger = logging.getLogger("bootstrap")

MAX_RETRIES = 2

# Decompose prompt template — instructs the agent to read the spec and
# write sections, proposal, and alignment to the planspace.
_DECOMPOSE_PROMPT = textwrap.dedent("""\
    # Task: Decompose Project Specification

    ## Planspace
    `{planspace}`

    ## Codespace
    `{codespace}`

    ## Spec File
    Read the project specification at: `{spec_path}`

    ## Output Paths
    - Section files: `{sections_dir}/section-NN.md`
    - Global proposal: `{proposal_path}`
    - Global alignment: `{alignment_path}`

    ## Instructions

    Read the spec file above. Decompose it into:

    1. **Section files** — one per implementation unit at `{sections_dir}/section-NN.md`.
       Each section must have YAML frontmatter with `summary` and `keywords`.
       Section content should be verbatim from the spec where possible.

    2. **Global proposal** at `{proposal_path}` — a comprehensive implementation
       proposal describing the technical approach, architecture, key design
       decisions, and how components relate.

    3. **Global alignment** at `{alignment_path}` — constraints, quality standards,
       and architectural guidelines: shape constraints, anti-patterns, cross-cutting
       concerns, technology-specific constraints.

    All three artifact types are mandatory.
""")


class BootstrapOrchestrator:
    """Convergence loop for pre-section-loop artifact production."""

    def __init__(
        self,
        assessor: BootstrapAssessor,
        codemap_builder: CodemapBuilder,
        section_explorer: SectionExplorer,
    ) -> None:
        self._assessor = assessor
        self._codemap_builder = codemap_builder
        self._section_explorer = section_explorer

    def run_bootstrap(
        self,
        planspace: Path,
        codespace: Path,
        spec_path: Path,
    ) -> bool:
        """Run the convergence loop. Returns True if all artifacts ready."""
        registry = PathRegistry(planspace)
        registry.ensure_artifacts_tree()

        # Classify entry path and persist the signal
        classification = self._classify_and_store(
            registry, codespace, spec_path,
        )

        # For PRD entries, seed governance from the spec after decompose
        # produces alignment.  The seeding is idempotent — safe to call
        # even if alignment doesn't exist yet; the decompose stage will
        # create it, then the next loop iteration picks up governance.
        self._entry_classification = classification

        attempt_counts: dict[str, int] = defaultdict(int)

        while True:
            status = self._assessor.assess(planspace)

            if status.ready:
                logger.info(
                    "Bootstrap complete: all artifacts present (%s)",
                    ", ".join(status.completed),
                )
                return True

            stage = status.next_stage
            assert stage is not None

            if attempt_counts[stage] >= MAX_RETRIES:
                logger.error(
                    "Bootstrap stage '%s' failed after %d attempts. "
                    "Missing: %s",
                    stage, MAX_RETRIES, status.missing,
                )
                return False

            attempt_counts[stage] += 1
            logger.info(
                "Bootstrap: running '%s' (attempt %d/%d, missing: %s)",
                stage, attempt_counts[stage], MAX_RETRIES,
                status.missing,
            )

            success = self._execute_stage(
                stage, planspace, codespace, spec_path, registry,
            )
            if not success:
                logger.warning("Bootstrap stage '%s' failed", stage)
                # Loop continues — assessor will re-evaluate

    # ------------------------------------------------------------------
    # Entry classification
    # ------------------------------------------------------------------

    def _classify_and_store(
        self,
        registry: PathRegistry,
        codespace: Path,
        spec_path: Path,
    ) -> EntryClassification:
        """Classify the entry path and write the signal file.

        Idempotent: if the signal file already exists, reads it back
        instead of re-classifying (supports resume without clobbering).
        """
        signal_path = registry.entry_classification_json()
        if signal_path.is_file():
            try:
                data = json.loads(signal_path.read_text(encoding="utf-8"))
                logger.info(
                    "Resuming with existing entry classification: %s",
                    data.get("path", "unknown"),
                )
                return EntryClassification(
                    path=data.get("path", "greenfield"),
                    has_code=data.get("has_code", False),
                    has_spec=data.get("has_spec", False),
                    has_governance=data.get("has_governance", False),
                    has_philosophy=data.get("has_philosophy", False),
                    evidence=data.get("evidence", []),
                )
            except (json.JSONDecodeError, OSError):
                logger.warning("Malformed entry-classification.json, re-classifying")

        classification = self._assessor.classify_entry(codespace, spec_path)

        signal_path.parent.mkdir(parents=True, exist_ok=True)
        signal_path.write_text(
            json.dumps({
                "path": classification.path,
                "has_code": classification.has_code,
                "has_spec": classification.has_spec,
                "has_governance": classification.has_governance,
                "has_philosophy": classification.has_philosophy,
                "evidence": classification.evidence,
            }, indent=2) + "\n",
            encoding="utf-8",
        )

        logger.info(
            "Entry classification: %s -> %s",
            classification.evidence, classification.path,
        )
        return classification

    # ------------------------------------------------------------------
    # Post-decompose problem extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _run_problem_extraction(
        codespace: Path,
        planspace: Path,
    ) -> bool:
        """Extract problems/constraints from spec via alignment seeding.

        Called after decompose produces alignment.md for PRD entries.
        Delegates to the existing governance seeding machinery which
        classifies alignment sections into CON/PAT/PRB records.

        Returns True if any governance records were seeded.
        """
        from intake.repository.governance_loader import (
            bootstrap_governance_if_missing,
            seed_governance_from_alignment,
        )

        # Ensure governance scaffolding exists first
        bootstrap_governance_if_missing(codespace)
        seeded = seed_governance_from_alignment(codespace, planspace)
        if seeded:
            logger.info("Seeded governance records from spec-derived alignment")
        return seeded

    def _execute_stage(
        self,
        stage: str,
        planspace: Path,
        codespace: Path,
        spec_path: Path,
        registry: PathRegistry,
    ) -> bool:
        if stage == STAGE_DECOMPOSE:
            success = self._run_decompose(planspace, codespace, spec_path, registry)
            # After successful decompose on a PRD entry, extract problems
            # from the spec-derived alignment before the next stage.
            if success and getattr(self, "_entry_classification", None) is not None:
                if self._entry_classification.path == ENTRY_PRD:
                    self._run_problem_extraction(codespace, planspace)
            return success
        if stage == STAGE_CODEMAP:
            return self._run_codemap(codespace, registry)
        if stage == STAGE_EXPLORE:
            return self._run_explore(codespace, registry)
        if stage == STAGE_SUBSTRATE:
            return self._run_substrate(planspace, codespace)
        logger.error("Unknown bootstrap stage: %s", stage)
        return False

    # ------------------------------------------------------------------
    # Stage A: Decompose spec into sections + proposal + alignment
    # ------------------------------------------------------------------

    def _run_decompose(
        self,
        planspace: Path,
        codespace: Path,
        spec_path: Path,
        registry: PathRegistry,
    ) -> bool:
        from scan.scan_dispatcher import dispatch_agent, read_scan_model_policy

        log_dir = registry.artifacts / "bootstrap-logs"
        log_dir.mkdir(parents=True, exist_ok=True)

        prompt = _DECOMPOSE_PROMPT.format(
            planspace=planspace,
            codespace=codespace,
            spec_path=spec_path,
            sections_dir=registry.sections_dir(),
            proposal_path=registry.global_proposal(),
            alignment_path=registry.global_alignment(),
        )

        prompt_path = log_dir / "decompose-prompt.md"
        prompt_path.write_text(prompt, encoding="utf-8")

        policy = read_scan_model_policy(registry.artifacts)
        model = policy.get("decompose", "claude-opus")

        result = dispatch_agent(
            model=model,
            project=codespace,
            prompt_file=prompt_path,
            agent_file="decompose.md",
            stdout_file=log_dir / "decompose-output.md",
            stderr_file=log_dir / "decompose.stderr.log",
        )

        if result.returncode != 0:
            logger.error("Decompose agent exited with code %d", result.returncode)
            return False

        # Verify outputs were created
        sections = sorted(registry.sections_dir().glob("section-*.md"))
        if not sections:
            logger.error("Decompose agent produced no section files")
            return False

        if not registry.global_proposal().is_file():
            logger.error("Decompose agent did not produce proposal.md")
            return False

        if not registry.global_alignment().is_file():
            logger.error("Decompose agent did not produce alignment.md")
            return False

        logger.info("Decompose complete: %d sections", len(sections))
        return True

    # ------------------------------------------------------------------
    # Stage B: Build codemap
    # ------------------------------------------------------------------

    def _run_codemap(self, codespace: Path, registry: PathRegistry) -> bool:
        scan_log_dir = registry.scan_logs_dir()
        scan_log_dir.mkdir(parents=True, exist_ok=True)

        return self._codemap_builder.run_codemap_build(
            codemap_path=registry.codemap(),
            codespace=codespace,
            artifacts_dir=registry.artifacts,
            scan_log_dir=scan_log_dir,
            fingerprint_path=registry.codemap_fingerprint(),
        )

    # ------------------------------------------------------------------
    # Stage C: Section exploration (related files)
    # ------------------------------------------------------------------

    def _run_explore(self, codespace: Path, registry: PathRegistry) -> bool:
        scan_log_dir = registry.scan_logs_dir()
        scan_log_dir.mkdir(parents=True, exist_ok=True)

        self._section_explorer.run_section_exploration(
            sections_dir=registry.sections_dir(),
            codemap_path=registry.codemap(),
            codespace=codespace,
            artifacts_dir=registry.artifacts,
            scan_log_dir=scan_log_dir,
        )
        return True

    # ------------------------------------------------------------------
    # Stage D: Shared Integration Substrate (SIS) discovery
    # ------------------------------------------------------------------

    def _run_substrate(self, planspace: Path, codespace: Path) -> bool:
        from scan.substrate.substrate_discoverer import run_substrate_discovery

        return run_substrate_discovery(planspace, codespace)
