"""Minimal bootstrap for the implementation pipeline.

Resolves paths, writes metadata (parameters.json, run-metadata.json),
initializes run.db, copies the spec, and hands off to the adaptive
orchestration system.  Does NOT own stages, schedule, governance, or
directory scaffolding -- those are the system's job.

Invoked via: python -m pipeline <planspace> <codespace> --spec <spec-path>
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

from flow.service.task_db_client import init_db
from orchestrator.path_registry import PathRegistry

logger = logging.getLogger("pipeline.runner")


def _init_planspace(
    planspace: Path, codespace: Path, slug: str, qa_mode: bool, spec_path: Path,
) -> PathRegistry:
    """Create the planspace root + artifacts dir and write metadata."""
    registry = PathRegistry(planspace)
    registry.artifacts.mkdir(parents=True, exist_ok=True)

    registry.parameters().write_text(
        json.dumps({"qa_mode": qa_mode}, indent=2) + "\n", encoding="utf-8",
    )

    metadata = {
        "slug": slug, "planspace": str(planspace), "codespace": str(codespace),
        "spec": str(spec_path), "started_at": datetime.now(timezone.utc).isoformat(),
    }
    meta_path = registry.artifacts / "run-metadata.json"
    meta_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")

    init_db(registry.run_db())
    logger.info("Initialized planspace: %s", planspace)
    return registry


def _run_task_dispatcher(
    stop_event: threading.Event,
    planspace: Path,
    codespace: Path | None,
    poll_interval: float = 3.0,
) -> None:
    """Background polling loop for the task dispatcher.

    Runs in a daemon thread alongside the orchestrator.  Polls run.db
    for pending tasks and dispatches them until *stop_event* is set.
    """
    from flow.engine.task_dispatcher import _get_dispatcher, log
    from flow.service.task_db_client import next_task as _db_next_task

    dispatcher = _get_dispatcher()
    db_path = str(PathRegistry(planspace).run_db())

    if not Path(db_path).exists():
        log(f"WARNING: run.db not found at {db_path} — dispatcher thread exiting")
        return

    log(f"Starting dispatcher thread (planspace={planspace}, poll={poll_interval}s)")

    while not stop_event.is_set():
        try:
            model_policy = dispatcher._policies.load(planspace)
            task = _db_next_task(db_path)

            if task:
                dispatcher.dispatch_task(
                    db_path, planspace, task,
                    codespace=codespace,
                    model_policy=model_policy,
                )
            else:
                stop_event.wait(timeout=poll_interval)
        except Exception as e:  # noqa: BLE001 — daemon loop, must not crash
            log(f"ERROR in dispatcher thread: {e}")
            stop_event.wait(timeout=poll_interval)

    log("Dispatcher thread stopped")


def _build_bootstrap_orchestrator():
    """Build the bootstrap orchestrator with injected dependencies."""
    from containers import Services
    from orchestrator.engine.bootstrap_orchestrator import BootstrapOrchestrator
    from orchestrator.service.bootstrap_assessor import BootstrapAssessor
    from scan.codemap.codemap_builder import CodemapBuilder
    from scan.explore.section_explorer import SectionExplorer

    assessor = BootstrapAssessor()
    codemap_builder = CodemapBuilder(
        prompt_guard=Services.prompt_guard(),
        task_router=Services.task_router(),
        artifact_io=Services.artifact_io(),
    )
    section_explorer = SectionExplorer(
        prompt_guard=Services.prompt_guard(),
        task_router=Services.task_router(),
    )

    return BootstrapOrchestrator(
        assessor=assessor,
        codemap_builder=codemap_builder,
        section_explorer=section_explorer,
    )


def _handoff(
    planspace: Path, codespace: Path, spec_path: Path, registry: PathRegistry,
) -> None:
    """Hand off to the adaptive orchestration system.

    Runs the bootstrap convergence loop to produce all pre-section-loop
    artifacts (sections, codemap, proposal, alignment), then delegates
    to PipelineOrchestrator for the section loop.
    """
    # Phase 1: Bootstrap — produce pre-section-loop artifacts
    bootstrap = _build_bootstrap_orchestrator()
    if not bootstrap.run_bootstrap(planspace, codespace, spec_path):
        logger.error(
            "Bootstrap failed — cannot proceed to section loop. "
            "Check bootstrap-logs/ for details."
        )
        return

    # Phase 2: Section loop — existing adaptive orchestration
    from containers import Services
    from orchestrator.engine.pipeline_orchestrator import (
        PipelineOrchestrator, _build_coordination_controller,
        _build_implementation_phase, _build_reconciliation_phase,
    )
    from orchestrator.engine.section_pipeline import build_section_pipeline

    pipeline = build_section_pipeline()
    orchestrator = PipelineOrchestrator(
        communicator=Services.communicator(), logger=Services.logger(),
        config=Services.config(), artifact_io=Services.artifact_io(),
        prompt_guard=Services.prompt_guard(),
        section_alignment=Services.section_alignment(),
        change_tracker=Services.change_tracker(),
        pipeline_control=Services.pipeline_control(),
        coordination_controller=_build_coordination_controller(),
        implementation_phase=_build_implementation_phase(section_pipeline=pipeline),
        reconciliation_phase=_build_reconciliation_phase(section_pipeline=pipeline),
        section_pipeline=pipeline,
    )

    # Register mailbox and set parent for pause/resume messaging
    Services.communicator().mailbox_register(planspace)
    Services.communicator().set_parent("orchestrator")
    Services.pipeline_control().set_parent("orchestrator")

    from pipeline.context import DispatchContext
    ctx = DispatchContext(
        planspace=planspace, codespace=codespace, _policies=Services.policies(),
    )

    # Start the task dispatcher as a background daemon thread so queued
    # tasks (e.g. research.plan) are consumed while the orchestrator runs.
    stop_event = threading.Event()
    dispatcher_thread = threading.Thread(
        target=_run_task_dispatcher,
        args=(stop_event, planspace, codespace),
        name="task-dispatcher",
        daemon=True,
    )
    dispatcher_thread.start()

    try:
        orchestrator._run_loop(ctx, registry.sections_dir(), registry.global_proposal(), registry.global_alignment())
    finally:
        stop_event.set()
        dispatcher_thread.join(timeout=10)


def main(argv: list[str] | None = None) -> int:
    """Bootstrap the pipeline and hand off. Returns 0 on success, 1 on failure."""
    logging.basicConfig(level=logging.INFO, format="[pipeline.runner] %(message)s", stream=sys.stderr)

    parser = argparse.ArgumentParser(prog="pipeline", description="Minimal pipeline bootstrap.")
    parser.add_argument("planspace", type=Path, help="Planspace directory.")
    parser.add_argument("codespace", type=Path, help="Codespace directory.")
    parser.add_argument("--spec", type=Path, required=True, help="Spec file path.")
    parser.add_argument("--slug", type=str, default=None, help="Workspace slug.")
    parser.add_argument("--qa-mode", action="store_true", default=False, dest="qa_mode")
    args = parser.parse_args(argv)

    spec_path: Path = args.spec.resolve()
    codespace: Path = args.codespace.resolve()
    planspace = (Path.home() / ".claude" / "workspaces" / args.slug) if args.slug else args.planspace.resolve()
    slug = args.slug or planspace.name

    if not spec_path.is_file():
        logger.error("Spec file not found: %s", spec_path)
        return 1
    if not codespace.is_dir():
        logger.error("Codespace not found: %s", codespace)
        return 1

    logger.info("Pipeline bootstrap: slug=%s planspace=%s codespace=%s spec=%s qa_mode=%s",
                slug, planspace, codespace, spec_path, args.qa_mode)

    registry = _init_planspace(planspace, codespace, slug, args.qa_mode, spec_path)

    spec_dest = registry.artifacts / "spec.md"
    spec_dest.write_text(spec_path.read_text(encoding="utf-8"), encoding="utf-8")

    _handoff(planspace, codespace, spec_path, registry)
    logger.info("Pipeline bootstrap complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
