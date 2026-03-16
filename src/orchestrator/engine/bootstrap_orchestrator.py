"""Convergence loop for pre-section-loop artifact production.

Replaces the hard-coded prerequisite check in runner._handoff() with an
adaptive system that can produce all required artifacts (sections, codemap,
related files, proposal, alignment) with retry and crash recovery.

Follows the readiness-gate pattern from the section loop: assess what
exists, dispatch work to fill gaps, recheck until ready or failed.
"""
from __future__ import annotations

import logging
import textwrap
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING

from orchestrator.path_registry import PathRegistry
from orchestrator.service.bootstrap_assessor import (
    STAGE_CODEMAP,
    STAGE_DECOMPOSE,
    STAGE_EXPLORE,
    BootstrapAssessor,
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

    def _execute_stage(
        self,
        stage: str,
        planspace: Path,
        codespace: Path,
        spec_path: Path,
        registry: PathRegistry,
    ) -> bool:
        if stage == STAGE_DECOMPOSE:
            return self._run_decompose(planspace, codespace, spec_path, registry)
        if stage == STAGE_CODEMAP:
            return self._run_codemap(codespace, registry)
        if stage == STAGE_EXPLORE:
            return self._run_explore(codespace, registry)
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
