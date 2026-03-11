"""Trigger adapters that exercise real workflow entry points."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from .scenario_loader import TriggerSpec


def _clean_env(**extra: str) -> dict[str, str]:
    """Build a subprocess env dict with CLAUDECODE stripped."""
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    env.update(extra)
    return env


def _prime_import_paths(project_root: Path) -> None:
    scripts = str(project_root / "src" / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)


def readiness_route(planspace: Path, codespace: Path, project_root: Path, **kwargs) -> None:
    """Call resolve_and_route for a section via subprocess to avoid circular imports."""
    section_number = kwargs["section_number"]
    parent = kwargs.get("parent", "eval-harness")
    pass_mode = kwargs.get("pass_mode", "proposal")

    script = (
        "from types import SimpleNamespace; "
        "from lib.pipelines.readiness_gate import resolve_and_route; "
        "from pathlib import Path; "
        f"resolve_and_route("
        f"SimpleNamespace(number={section_number!r}), "
        f"Path({str(planspace)!r}), "
        f"parent={parent!r}, "
        f"pass_mode={pass_mode!r}, "
        f"codespace=Path({str(codespace)!r}))"
    )
    subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        cwd=str(project_root),
        env=_clean_env(PYTHONPATH=str(project_root / "src" / "scripts")),
    )


def dispatcher_once(planspace: Path, codespace: Path, project_root: Path, **kwargs) -> None:
    """Run task_dispatcher.py --once."""
    del codespace, kwargs
    subprocess.run(
        [
            sys.executable,
            str(project_root / "src" / "scripts" / "task_dispatcher.py"),
            str(planspace),
            "--once",
        ],
        check=True,
        cwd=str(project_root),
        env=_clean_env(PYTHONPATH=str(project_root / "src" / "scripts")),
    )


def dispatcher_until_quiescent(
    planspace: Path,
    codespace: Path,
    project_root: Path,
    **kwargs,
) -> None:
    """Run dispatcher until no runnable tasks remain or max iterations hit."""
    del codespace
    max_iter = int(kwargs.get("max_iterations", 50))
    for _ in range(max_iter):
        result = subprocess.run(
            [
                sys.executable,
                str(project_root / "src" / "scripts" / "task_dispatcher.py"),
                str(planspace),
                "--once",
            ],
            capture_output=True,
            text=True,
            cwd=str(project_root),
            env=_clean_env(PYTHONPATH=str(project_root / "src" / "scripts")),
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "dispatcher failed")
        if "no runnable tasks" in result.stdout.lower():
            break


def ensure_global_philosophy(
    planspace: Path,
    codespace: Path,
    project_root: Path,
    **kwargs,
) -> None:
    """Call ensure_global_philosophy via subprocess."""
    parent = kwargs.get("parent", "eval-harness")
    script = (
        "from lib.intent.philosophy_bootstrap import ensure_global_philosophy; "
        "from pathlib import Path; "
        f"ensure_global_philosophy("
        f"Path({str(planspace)!r}), "
        f"Path({str(codespace)!r}), "
        f"parent={parent!r})"
    )
    subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        cwd=str(project_root),
        env=_clean_env(PYTHONPATH=str(project_root / "src" / "scripts")),
    )


def scan_quick(planspace: Path, codespace: Path, project_root: Path, **kwargs) -> None:
    """Run scan.cli quick."""
    del kwargs
    subprocess.run(
        [sys.executable, "-m", "scan", "quick", str(planspace), str(codespace)],
        check=True,
        cwd=str(project_root / "src" / "scripts"),
        env=_clean_env(PYTHONPATH=str(project_root / "src" / "scripts")),
    )


def append_to_file(planspace: Path, codespace: Path, project_root: Path, **kwargs) -> None:
    """Append text to a file."""
    del codespace, project_root
    path = planspace / kwargs["path"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(str(kwargs["text"]))


def delete_file(planspace: Path, codespace: Path, project_root: Path, **kwargs) -> None:
    """Delete a file."""
    del codespace, project_root
    path = planspace / kwargs["path"]
    path.unlink(missing_ok=True)


def overwrite_file(planspace: Path, codespace: Path, project_root: Path, **kwargs) -> None:
    """Overwrite a file."""
    del codespace, project_root
    path = planspace / kwargs["path"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(kwargs["content"]).replace("\r\n", "\n").replace("\r", "\n"), encoding="utf-8")


def qa_dispatch_intercept(
    planspace: Path, codespace: Path, project_root: Path, **kwargs,
) -> None:
    """Dispatch a single agent with qa_mode enabled to exercise QA interception.

    Creates a minimal prompt, dispatches via dispatch_agent, and verifies
    that the QA interceptor creates artifacts in qa-intercepts/.
    """
    agent_file = kwargs.get("agent_file", "alignment-judge.md")
    model = kwargs.get("model", "glm")
    script = (
        "from pathlib import Path; "
        "from section_loop.dispatch import dispatch_agent; "
        f"planspace = Path({str(planspace)!r}); "
        f"codespace = Path({str(codespace)!r}); "
        "prompt = planspace / 'artifacts' / 'qa-test-prompt.md'; "
        "prompt.parent.mkdir(parents=True, exist_ok=True); "
        "prompt.write_text('# Test prompt\\nReturn OK.\\n', encoding='utf-8'); "
        "output = planspace / 'artifacts' / 'qa-test-output.md'; "
        f"dispatch_agent({model!r}, prompt, output, planspace, None, "
        f"codespace=codespace, agent_file={agent_file!r})"
    )
    subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        cwd=str(project_root),
        env=_clean_env(PYTHONPATH=str(project_root / "src" / "scripts")),
    )


def governance_bootstrap(
    planspace: Path, codespace: Path, project_root: Path, **kwargs,
) -> None:
    """Call bootstrap_governance_if_missing + build_governance_indexes."""
    del kwargs
    script = (
        "from lib.governance.loader import bootstrap_governance_if_missing, build_governance_indexes; "
        "from pathlib import Path; "
        f"bootstrap_governance_if_missing(Path({str(codespace)!r}), Path({str(planspace)!r})); "
        f"build_governance_indexes(Path({str(codespace)!r}), Path({str(planspace)!r}))"
    )
    subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        cwd=str(project_root),
        env=_clean_env(PYTHONPATH=str(project_root / "src" / "scripts")),
    )


ADAPTERS = {
    "readiness_route": readiness_route,
    "dispatcher_once": dispatcher_once,
    "dispatcher_until_quiescent": dispatcher_until_quiescent,
    "ensure_global_philosophy": ensure_global_philosophy,
    "scan_quick": scan_quick,
    "qa_dispatch_intercept": qa_dispatch_intercept,
    "governance_bootstrap": governance_bootstrap,
    "append_to_file": append_to_file,
    "delete_file": delete_file,
    "overwrite_file": overwrite_file,
}


def run_trigger(spec: TriggerSpec, planspace: Path, codespace: Path, project_root: Path) -> None:
    """Run a scenario trigger sequence."""
    if spec.kind not in {"python_sequence", "cli", "mutation_sequence"}:
        raise ValueError(f"Unsupported trigger kind: {spec.kind}")
    for step in spec.steps:
        adapter = ADAPTERS.get(step.adapter)
        if adapter is None:
            raise KeyError(f"Unknown trigger adapter: {step.adapter}")
        adapter(planspace, codespace, project_root, **step.kwargs)
