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

from flow.service.task_db_client import count_tasks, init_db
from orchestrator.path_registry import PathRegistry

logger = logging.getLogger("pipeline.runner")


def _init_planspace(
    planspace: Path, codespace: Path, slug: str, qa_mode: bool, spec_path: Path,
) -> PathRegistry:
    """Create the planspace root + artifacts dir and write metadata.

    Idempotent: parameters.json and run-metadata.json are only written
    when they do not already exist, so a resume pass does not clobber
    the original run metadata.
    """
    registry = PathRegistry(planspace)
    registry.artifacts.mkdir(parents=True, exist_ok=True)

    params_path = registry.parameters()
    if not params_path.exists():
        params_path.write_text(
            json.dumps({"qa_mode": qa_mode}, indent=2) + "\n", encoding="utf-8",
        )

    meta_path = registry.artifacts / "run-metadata.json"
    if not meta_path.exists():
        metadata = {
            "slug": slug, "planspace": str(planspace), "codespace": str(codespace),
            "spec": str(spec_path), "started_at": datetime.now(timezone.utc).isoformat(),
        }
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
    from flow.service.task_db_client import next_task as _db_next_task, reset_stuck_running_tasks
    from taskrouter import ensure_discovered

    dispatcher = _get_dispatcher()
    db_path = str(PathRegistry(planspace).run_db())

    if not Path(db_path).exists():
        log(f"WARNING: run.db not found at {db_path} — dispatcher thread exiting")
        return

    ensure_discovered()

    reset_count = reset_stuck_running_tasks(db_path)
    if reset_count:
        log(f"Reset {reset_count} stuck running tasks to pending on startup")

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

    # Drain: process any tasks that were submitted just before the stop
    # signal.  Without this, tasks submitted near pipeline exit (e.g.
    # research gate members for late sections) remain stuck in 'pending'.
    log("Draining remaining tasks before shutdown")
    while True:
        try:
            task = _db_next_task(db_path)
            if not task:
                break
            model_policy = dispatcher._policies.load(planspace)
            dispatcher.dispatch_task(
                db_path, planspace, task,
                codespace=codespace,
                model_policy=model_policy,
            )
        except Exception as e:  # noqa: BLE001 — drain must not crash
            log(f"ERROR draining task: {e}")
            break

    log("Dispatcher thread stopped")


def _build_bootstrap_orchestrator():
    """Build the bootstrap orchestrator with injected dependencies."""
    from containers import Services
    from orchestrator.engine.bootstrap_orchestrator import BootstrapOrchestrator
    from orchestrator.service.bootstrap_assessor import BootstrapAssessor
    from scan.codemap.codemap_builder import CodemapBuilder
    from scan.explore.section_explorer import SectionExplorer
    from scan.related.related_file_resolver import RelatedFileResolver

    assessor = BootstrapAssessor(artifact_io=Services.artifact_io())
    codemap_builder = CodemapBuilder(
        prompt_guard=Services.prompt_guard(),
        task_router=Services.task_router(),
        artifact_io=Services.artifact_io(),
    )
    section_explorer = SectionExplorer(
        prompt_guard=Services.prompt_guard(),
        task_router=Services.task_router(),
        related_file_resolver=RelatedFileResolver(
            artifact_io=Services.artifact_io(),
            hasher=Services.hasher(),
            prompt_guard=Services.prompt_guard(),
            task_router=Services.task_router(),
        ),
    )

    return BootstrapOrchestrator(
        assessor=assessor,
        codemap_builder=codemap_builder,
        section_explorer=section_explorer,
        artifact_io=Services.artifact_io(),
        policies=Services.policies(),
        prompt_guard=Services.prompt_guard(),
    )


def _submit_bootstrap_seed(registry: PathRegistry, spec_path: Path) -> None:
    """Submit the initial bootstrap.classify_entry task into run.db.

    Called once on fresh runs (not resume) to kick off the task-driven
    bootstrap chain.  The task dispatcher picks this up, the reconciler
    submits follow-on tasks, and bootstrap completes without a loop.
    """
    from flow.engine.flow_submitter import FlowSubmitter as _FS
    from flow.repository.flow_context_store import FlowContextStore
    from flow.service.task_db_client import log_bootstrap_stage
    from flow.types.context import FlowEnvelope
    from flow.types.schema import TaskSpec

    from containers import Services

    db_path = registry.run_db()
    submitter = _FS(
        freshness=Services.freshness(),
        flow_context_store=FlowContextStore(Services.artifact_io()),
    )
    env = FlowEnvelope(
        db_path=db_path,
        submitted_by="pipeline.runner",
        planspace=registry._planspace,
    )
    step = TaskSpec(
        task_type="bootstrap.classify_entry",
        concern_scope="bootstrap",
        payload_path=str(spec_path),
    )
    submitter.submit_chain(env, [step])

    log_bootstrap_stage(str(db_path), "classify_entry", "pending")
    logger.info("Submitted bootstrap seed task: bootstrap.classify_entry")


def _handoff(
    planspace: Path, codespace: Path, spec_path: Path, registry: PathRegistry,
    *, resume: bool = False,
) -> None:
    """Hand off to the adaptive orchestration system.

    Submits the bootstrap seed task (bootstrap.classify_entry) into
    run.db, starts the task dispatcher to process it, and launches
    the PipelineOrchestrator state machine.  The dispatcher drives
    the full bootstrap chain via reconciler follow-on tasks.

    When *resume* is True and run.db already contains tasks, the
    seed task is not submitted — the dispatcher picks up where it
    left off.
    """
    # Seed the bootstrap chain if this is a fresh run
    skip_seed = False
    if resume:
        db_path = registry.run_db()
        if db_path.exists():
            task_count = count_tasks(str(db_path))
            if task_count > 0:
                logger.info("Resuming from existing task queue (%d tasks)", task_count)
                skip_seed = True

    if not skip_seed:
        _submit_bootstrap_seed(registry, spec_path)

    # Build the orchestrator and start the dispatcher + state machine
    from containers import Services
    from orchestrator.engine.pipeline_orchestrator import (
        PipelineOrchestrator,
        _build_flow_submitter,
    )
    from orchestrator.engine.section_pipeline import build_section_pipeline

    # Create the halt event early so it can be wired into all builders.
    halt_event = threading.Event()
    Services.dispatcher().set_halt_event(halt_event)

    # The section_states and section_transitions tables are created by
    # init_db() in task_db_client (called during _init_planspace).
    # No additional schema initialization needed here.

    pipeline = build_section_pipeline()
    orchestrator = PipelineOrchestrator(
        communicator=Services.communicator(), logger=Services.logger(),
        config=Services.config(), artifact_io=Services.artifact_io(),
        prompt_guard=Services.prompt_guard(),
        section_alignment=Services.section_alignment(),
        change_tracker=Services.change_tracker(),
        pipeline_control=Services.pipeline_control(),
        section_pipeline=pipeline,
        flow_submitter=_build_flow_submitter(),
    )

    # Register mailbox and set parent for pause/resume messaging
    Services.communicator().mailbox_register(planspace)
    Services.communicator().set_parent("orchestrator")
    Services.pipeline_control().set_parent("orchestrator")

    from pipeline.context import DispatchContext
    ctx = DispatchContext(
        planspace=planspace, codespace=codespace, _policies=Services.policies(),
    )

    # Start HaltWatcher: polls the orchestrator mailbox for abort signals
    # and sets halt_event when one arrives.
    from orchestrator.service.halt_watcher import HaltWatcher
    halt_watcher = HaltWatcher(
        planspace=planspace,
        config=Services.config(),
        halt_event=halt_event,
    )
    halt_watcher.start()

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
        halt_watcher.stop()
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
    parser.add_argument("--resume", action="store_true", default=False, help="Resume from existing planspace.")
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

    logger.info("Pipeline bootstrap: slug=%s planspace=%s codespace=%s spec=%s qa_mode=%s resume=%s",
                slug, planspace, codespace, spec_path, args.qa_mode, args.resume)

    registry = _init_planspace(planspace, codespace, slug, args.qa_mode, spec_path)

    spec_dest = registry.artifacts / "spec.md"
    if not spec_dest.exists():
        spec_dest.write_text(spec_path.read_text(encoding="utf-8"), encoding="utf-8")

    _handoff(planspace, codespace, spec_path, registry, resume=args.resume)
    logger.info("Pipeline bootstrap complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
