"""Queue-backed dispatch adapter for scan-stage synchronous callers.

Stage 3 scan still has several call sites that expect a blocking
``CompletedProcess``-like result.  This adapter preserves that surface
while routing execution through the task queue and shared dispatcher so
scan work is observable and policy-routed under PAT-0004.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from flow.engine.flow_submitter import FlowSubmitter
from flow.repository.flow_context_store import FlowContextStore
from flow.service.task_db_client import get_task, init_db
from flow.types.context import FlowEnvelope
from flow.types.schema import TaskSpec
from orchestrator.path_registry import PathRegistry
from taskrouter import ensure_discovered, registry as task_registry

from scan.service.scan_dispatch_config import (
    ScanDispatchConfig,
)


def _get_config() -> ScanDispatchConfig:
    from containers import Services
    return ScanDispatchConfig(
        artifact_io=Services.artifact_io(),
        task_router=Services.task_router(),
    )


def _get_submitter() -> FlowSubmitter:
    from containers import Services

    return FlowSubmitter(
        freshness=Services.freshness(),
        flow_context_store=FlowContextStore(Services.artifact_io()),
    )


def read_scan_model_policy(artifacts_dir):
    """Read scan-stage model policy from ``model-policy.json``."""
    return _get_config().read_scan_model_policy(artifacts_dir)


def resolve_scan_agent_path(agent_file: str):
    """Resolve a scan agent definition path."""
    return _get_config().resolve_scan_agent_path(agent_file)


def _infer_planspace(path: Path) -> Path:
    current = path.resolve().parent
    while current != current.parent:
        if current.name == "artifacts":
            return current.parent
        if current.name in {"scan-logs", "bootstrap-logs"}:
            if current.parent.name == "artifacts":
                return current.parent.parent
            return current.parent
        if (current / "artifacts").is_dir():
            return current
        current = current.parent
    raise ValueError(f"Cannot infer planspace from artifact path: {path}")


def _build_policy_override(task_type: str, model: str) -> dict:
    ensure_discovered()
    route = task_registry.get_route(task_type)
    lookup_key = route.policy_key or route.qualified_name
    if "." in lookup_key:
        namespace, local = lookup_key.split(".", 1)
        return {namespace: {local: model}}
    return {lookup_key: model}


def dispatch_agent(
    *,
    task_type: str,
    model: str,
    project: Path,
    prompt_file: Path,
    stdout_file: Path | None = None,
    stderr_file: Path | None = None,
    concern_scope: str | None = None,
    submitted_by: str = "scan.sync_dispatch",
) -> subprocess.CompletedProcess[str]:
    """Submit a queued task and synchronously run it through the dispatcher.

    Parameters
    ----------
    task_type:
        Qualified task type (for example ``"scan.codemap_build"``).
    model:
        Concrete model override for this dispatch.
    project:
        ``--project`` directory (typically the codespace).
    prompt_file:
        ``--file`` path containing the agent prompt.
    stdout_file:
        If given, the agent stdout stream is written to this path.
    stderr_file:
        If given, the agent stderr stream is written to this path.
    concern_scope:
        Optional task scope (for example ``"section-01"``).

    Returns
    -------
    subprocess.CompletedProcess
        The finished queued dispatch, preserving the legacy scan surface.
    """
    planspace = _infer_planspace(prompt_file)
    registry = PathRegistry(planspace)
    init_db(registry.run_db())

    submitter = _get_submitter()
    env = FlowEnvelope(
        db_path=registry.run_db(),
        submitted_by=submitted_by,
        planspace=planspace,
    )
    task_ids = submitter.submit_chain(
        env,
        [
            TaskSpec(
                task_type=task_type,
                concern_scope=concern_scope or "",
                payload_path=str(prompt_file.resolve()),
            ),
        ],
    )
    if not task_ids:
        raise RuntimeError(f"Queue submission failed for {task_type}")

    task_id = task_ids[0]
    task = get_task(registry.run_db(), task_id)
    if task is None:
        raise RuntimeError(f"Submitted task disappeared from run.db: {task_id}")

    from flow.engine.task_dispatcher import dispatch_task as dispatch_queued_task

    dispatch_queued_task(
        str(registry.run_db()),
        planspace,
        task,
        codespace=project,
        model_policy=_build_policy_override(task_type, model),
    )

    finished = get_task(registry.run_db(), task_id)
    if finished is None:
        raise RuntimeError(f"Dispatched task disappeared from run.db: {task_id}")

    stdout = ""
    stderr = ""
    output_path = Path(finished["output"]) if "output" in finished else None
    if output_path is not None:
        stdout_path = output_path.with_suffix(".stdout.txt")
        stderr_path = output_path.with_suffix(".stderr.txt")
        if stdout_path.is_file():
            stdout = stdout_path.read_text(encoding="utf-8")
        if stderr_path.is_file():
            stderr = stderr_path.read_text(encoding="utf-8")

    if stdout_file is not None:
        stdout_file.parent.mkdir(parents=True, exist_ok=True)
        stdout_file.write_text(stdout, encoding="utf-8")

    if stderr_file is not None:
        stderr_file.parent.mkdir(parents=True, exist_ok=True)
        stderr_file.write_text(stderr, encoding="utf-8")

    returncode = 0 if finished.get("status") == "complete" else 1

    return subprocess.CompletedProcess(
        args=[task_type],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr if stderr else finished.get("error", ""),
    )
