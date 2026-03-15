"""Canonical top-level workflow runner.

Owns stages 1-7 of the implementation pipeline end-to-end.
Invoked via: python -m pipeline <planspace> <codespace> --spec <spec-path>

Stage map (matches templates/implement-proposal.md):
  1. decompose   -- recursive section decomposition
  2. docstrings  -- ensure source files have module docstrings
  3. scan        -- agent-driven codemap exploration + per-section file ID
  3.5 substrate  -- shared integration substrate discovery
  4-5 section-loop -- proposals + implementation + coordination
  6. verify      -- constraint alignment check + lint + tests
  7. post-verify -- full suite + import check + commit + promote
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from flow.service.task_db_client import init_db
from intake.repository.governance_loader import bootstrap_governance_if_missing
from orchestrator.path_registry import PathRegistry
from pipeline.template import SRC_TEMPLATE_DIR, load_template, render

logger = logging.getLogger("pipeline.runner")

_WORKFLOW_SH = Path(__file__).resolve().parent.parent / "scripts" / "workflow.sh"

# Stages that abort the entire pipeline on failure.
_CRITICAL_STAGES = frozenset({"decompose", "section-loop"})


# ---------------------------------------------------------------------------
# Schedule helpers
# ---------------------------------------------------------------------------

def _mark_schedule(command: str, planspace: Path) -> str:
    """Run workflow.sh with the given command and return stdout."""
    result = subprocess.run(  # noqa: S603
        ["bash", str(_WORKFLOW_SH), command, str(planspace)],
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _write_schedule(planspace: Path, spec_path: Path) -> None:
    """Render the implement-proposal template into schedule.md."""
    template = load_template("implement-proposal.md", SRC_TEMPLATE_DIR)
    task_name = spec_path.stem
    rendered = render(template, {
        "task-name": task_name,
        "proposal-path": str(spec_path),
    })
    schedule_path = planspace / "schedule.md"
    schedule_path.write_text(rendered, encoding="utf-8")
    logger.info("Wrote schedule: %s", schedule_path)


# ---------------------------------------------------------------------------
# Planspace initialization
# ---------------------------------------------------------------------------

def _init_planspace(
    planspace: Path,
    codespace: Path,
    slug: str,
    qa_mode: bool,
    spec_path: Path,
) -> PathRegistry:
    """Create planspace directory tree and bootstrap metadata."""
    registry = PathRegistry(planspace)
    registry.ensure_artifacts_tree()

    # parameters.json
    registry.parameters().write_text(
        json.dumps({"qa_mode": qa_mode}, indent=2) + "\n",
        encoding="utf-8",
    )

    # run-metadata.json
    metadata = {
        "slug": slug,
        "planspace": str(planspace),
        "codespace": str(codespace),
        "spec": str(spec_path),
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    meta_path = registry.artifacts / "run-metadata.json"
    meta_path.write_text(
        json.dumps(metadata, indent=2) + "\n",
        encoding="utf-8",
    )

    # Initialize coordination database (idempotent)
    init_db(registry.run_db())
    logger.info("Initialized planspace: %s", planspace)

    return registry


# ---------------------------------------------------------------------------
# Stage dispatch helpers
# ---------------------------------------------------------------------------

def _run_scan(planspace: Path, codespace: Path) -> int:
    """Invoke Stage 3 scan via the scan.cli module."""
    from scan.cli import main as scan_main
    return scan_main(["both", str(planspace), str(codespace)])


def _run_substrate(planspace: Path, codespace: Path) -> bool:
    """Invoke Stage 3.5 substrate discovery."""
    from scan.substrate.substrate_discoverer import run_substrate_discovery
    return run_substrate_discovery(planspace, codespace)


def _run_section_loop(
    planspace: Path,
    codespace: Path,
    registry: PathRegistry,
) -> None:
    """Invoke Stages 4-5 section loop via pipeline_orchestrator."""
    from containers import Services
    from orchestrator.engine.pipeline_orchestrator import (
        PipelineOrchestrator,
        _build_coordination_controller,
        _build_implementation_phase,
        _build_reconciliation_phase,
    )
    from orchestrator.engine.section_pipeline import build_section_pipeline

    global_proposal = registry.global_proposal()
    global_alignment = registry.global_alignment()

    if not global_proposal.exists():
        raise FileNotFoundError(
            f"Global proposal not found: {global_proposal}. "
            "Stage 1 (decompose) must produce this artifact."
        )
    if not global_alignment.exists():
        raise FileNotFoundError(
            f"Global alignment not found: {global_alignment}. "
            "Stage 1 (decompose) must produce this artifact."
        )

    pipeline = build_section_pipeline()
    orchestrator = PipelineOrchestrator(
        communicator=Services.communicator(),
        logger=Services.logger(),
        config=Services.config(),
        artifact_io=Services.artifact_io(),
        prompt_guard=Services.prompt_guard(),
        section_alignment=Services.section_alignment(),
        change_tracker=Services.change_tracker(),
        pipeline_control=Services.pipeline_control(),
        coordination_controller=_build_coordination_controller(),
        implementation_phase=_build_implementation_phase(section_pipeline=pipeline),
        reconciliation_phase=_build_reconciliation_phase(section_pipeline=pipeline),
        section_pipeline=pipeline,
    )

    # Build a synthetic sys.argv for the orchestrator's argparse
    saved_argv = sys.argv
    sys.argv = [
        "pipeline_orchestrator",
        str(planspace),
        str(codespace),
        "--global-proposal", str(global_proposal),
        "--global-alignment", str(global_alignment),
        "--parent", "workflow-runner",
    ]
    try:
        orchestrator.main()
    finally:
        sys.argv = saved_argv


def _run_decompose(planspace: Path, codespace: Path, registry: PathRegistry) -> None:
    """Stage 1: Section decomposition.

    This is a multi-phase orchestration process (see implement.md Stage 1):
    - Phase A: Recursive identification (manifests)
    - Phase B: Materialize terminal section files
    - Phase C: Section summaries (GLM per section)

    TODO: Wire to proper decomposition orchestrator. Currently raises
    NotImplementedError — the stage must be implemented as domain-specific
    orchestration, not a generic single-agent dispatch.
    """
    raise NotImplementedError(
        "Stage 1 (decompose) requires multi-phase orchestration "
        "(recursive manifests → materialize → summaries). "
        "See implement.md Stage 1 for the full specification."
    )


def _run_docstrings(planspace: Path, codespace: Path, registry: PathRegistry) -> None:
    """Stage 2: Demand-driven docstring cache.

    Language-agnostic: discovers the codespace's language stack and adds
    module-level documentation to files that will be used by later stages.

    TODO: Wire to proper docstring orchestrator. Currently raises
    NotImplementedError.
    """
    raise NotImplementedError(
        "Stage 2 (docstrings) requires language-aware file discovery "
        "and per-file dispatch. See implement.md Stage 2."
    )


def _run_verify(planspace: Path, codespace: Path, registry: PathRegistry) -> None:
    """Stage 6: Verification.

    Discovers available verification tools from the codespace (linters,
    type checkers, test runners) and runs constraint alignment checks.

    TODO: Wire to proper verification orchestrator. Currently raises
    NotImplementedError.
    """
    raise NotImplementedError(
        "Stage 6 (verify) requires stack-aware tool discovery "
        "and constraint alignment checking. See implement.md Stage 6."
    )


def _run_post_verify(planspace: Path, codespace: Path, registry: PathRegistry) -> None:
    """Stage 7: Post-verification.

    Full test suite, import/dependency verification, and commit preparation.

    TODO: Wire to proper post-verification orchestrator. Currently raises
    NotImplementedError.
    """
    raise NotImplementedError(
        "Stage 7 (post-verify) requires stack-aware verification "
        "and commit preparation. See implement.md Stage 7."
    )


def _run_promote(planspace: Path, codespace: Path, registry: PathRegistry) -> None:
    """Promote: Project-level review.

    Reviews constraints and tradeoffs at the project level using the
    governance and risk artifacts produced during the section loop.

    TODO: Wire to proper promotion orchestrator. Currently raises
    NotImplementedError.
    """
    raise NotImplementedError(
        "Promote stage requires project-level review against "
        "governance artifacts. Not yet implemented."
    )


# ---------------------------------------------------------------------------
# Stage runner
# ---------------------------------------------------------------------------

class StageError(Exception):
    """A pipeline stage failed."""

    def __init__(self, stage: str, message: str) -> None:
        self.stage = stage
        super().__init__(f"Stage {stage} failed: {message}")


def _run_stage(
    stage_name: str,
    planspace: Path,
    codespace: Path,
    registry: PathRegistry,
) -> None:
    """Execute a single pipeline stage.

    Marks the schedule, runs the stage, marks done/fail.
    Raises StageError on failure.
    """
    logger.info("=== Stage: %s — starting ===", stage_name)
    next_output = _mark_schedule("next", planspace)
    if next_output == "COMPLETE":
        logger.info("Schedule reports COMPLETE — nothing to run")
        return

    try:
        if stage_name == "decompose":
            _run_decompose(planspace, codespace, registry)

        elif stage_name == "docstrings":
            _run_docstrings(planspace, codespace, registry)

        elif stage_name == "scan":
            rc = _run_scan(planspace, codespace)
            if rc != 0:
                raise StageError("scan", f"scan exited with code {rc}")

        elif stage_name == "substrate":
            ok = _run_substrate(planspace, codespace)
            if not ok:
                logger.warning("Substrate discovery returned False — may have been skipped")
                # Substrate skip is not a failure — it legitimately skips
                # when there are no vacuum sections.

        elif stage_name == "section-loop":
            _run_section_loop(planspace, codespace, registry)

        elif stage_name == "verify":
            _run_verify(planspace, codespace, registry)

        elif stage_name == "post-verify":
            _run_post_verify(planspace, codespace, registry)

        elif stage_name == "promote":
            _run_promote(planspace, codespace, registry)

        else:
            logger.warning("Unknown stage: %s — skipping", stage_name)
            _mark_schedule("skip", planspace)
            return

        _mark_schedule("done", planspace)
        logger.info("=== Stage: %s — done ===", stage_name)

    except StageError:
        _mark_schedule("fail", planspace)
        raise
    except Exception as exc:
        _mark_schedule("fail", planspace)
        raise StageError(stage_name, str(exc)) from exc


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

# The ordered stages that the runner drives. Each entry is the schedule
# step name that maps to a handler in _run_stage().
_STAGES = [
    "decompose",
    "docstrings",
    "scan",
    "substrate",
    "section-loop",
    "verify",
    "post-verify",
    "promote",
]


def main(argv: list[str] | None = None) -> int:
    """Run the full implementation pipeline.

    Returns 0 on success, 1 on failure.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="[pipeline.runner] %(message)s",
        stream=sys.stderr,
    )

    parser = argparse.ArgumentParser(
        prog="pipeline",
        description=(
            "Canonical top-level workflow runner. Drives stages 1-7 "
            "of the implementation pipeline end-to-end."
        ),
    )
    parser.add_argument(
        "planspace",
        type=Path,
        help="Planspace directory (will be created if it does not exist).",
    )
    parser.add_argument(
        "codespace",
        type=Path,
        help="Codespace directory (project source root).",
    )
    parser.add_argument(
        "--spec",
        type=Path,
        required=True,
        help="Path to the proposal / spec file that drives this run.",
    )
    parser.add_argument(
        "--slug",
        type=str,
        default=None,
        help=(
            "Optional workspace slug. When provided, overrides planspace "
            "to ~/.claude/workspaces/<slug>."
        ),
    )
    parser.add_argument(
        "--qa-mode",
        action="store_true",
        default=False,
        dest="qa_mode",
        help="Enable QA mode (activates QA gate interception).",
    )
    args = parser.parse_args(argv)

    # Resolve paths
    spec_path: Path = args.spec.resolve()
    codespace: Path = args.codespace.resolve()

    if args.slug:
        planspace = Path.home() / ".claude" / "workspaces" / args.slug
    else:
        planspace = args.planspace.resolve()

    slug = args.slug or planspace.name

    if not spec_path.is_file():
        logger.error("Spec file not found: %s", spec_path)
        return 1
    if not codespace.is_dir():
        logger.error("Codespace not found: %s", codespace)
        return 1

    # --- Initialize ---
    logger.info("Pipeline starting: slug=%s", slug)
    logger.info("  planspace: %s", planspace)
    logger.info("  codespace: %s", codespace)
    logger.info("  spec:      %s", spec_path)
    logger.info("  qa_mode:   %s", args.qa_mode)

    registry = _init_planspace(planspace, codespace, slug, args.qa_mode, spec_path)

    # Bootstrap governance scaffolding
    created = bootstrap_governance_if_missing(codespace)
    if created:
        logger.info("Bootstrapped governance scaffolding for greenfield project")

    # Render schedule from template
    _write_schedule(planspace, spec_path)

    # Copy spec into planspace for reference
    spec_dest = registry.artifacts / "spec.md"
    spec_dest.write_text(spec_path.read_text(encoding="utf-8"), encoding="utf-8")

    # --- Drive stages ---
    for stage_name in _STAGES:
        try:
            _run_stage(stage_name, planspace, codespace, registry)
        except StageError as exc:
            logger.error("Stage failed: %s", exc)
            if exc.stage in _CRITICAL_STAGES:
                logger.error(
                    "Critical stage %s failed — aborting pipeline",
                    exc.stage,
                )
                return 1
            logger.warning(
                "Non-critical stage %s failed — continuing",
                exc.stage,
            )

    # --- Done ---
    final_status = _mark_schedule("status", planspace)
    logger.info("Pipeline complete. Schedule status: %s", final_status)
    return 0


if __name__ == "__main__":
    sys.exit(main())
