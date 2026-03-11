"""Seed isolated planspace/codespace state for agentic evals."""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .scenario_loader import ScenarioSpec


@dataclass
class SeededState:
    scenario_id: str
    root: Path
    planspace: Path
    codespace: Path
    project_root: Path


def _normalize_text(content: str, planspace: Path, codespace: Path) -> str:
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    normalized = normalized.replace("<PLANSPACE>", str(planspace))
    normalized = normalized.replace("<CODESPACE>", str(codespace))
    return normalized


def _copy_fixture_file(
    fixture_dir: Path,
    source_relative: str,
    destination: Path,
    planspace: Path,
    codespace: Path,
) -> None:
    source_path = fixture_dir / source_relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    raw = source_path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        destination.write_bytes(raw)
        return
    destination.write_text(
        _normalize_text(text, planspace, codespace),
        encoding="utf-8",
        newline="\n",
    )


def _write_pre_signal(planspace: Path, payload: dict) -> None:
    relative_path = payload.get("path")
    if not relative_path:
        raise ValueError("pre_signals entries must include 'path'")
    target = planspace / str(relative_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    content = payload.get("content", payload.get("data", payload))
    if isinstance(content, str):
        target.write_text(content.replace("\r\n", "\n").replace("\r", "\n"), encoding="utf-8")
        return
    target.write_text(json.dumps(content, indent=2) + "\n", encoding="utf-8")


def _ensure_standard_dirs(planspace: Path) -> None:
    directories = [
        planspace / "artifacts",
        planspace / "artifacts" / "signals",
        planspace / "artifacts" / "sections",
        planspace / "artifacts" / "proposals",
        planspace / "artifacts" / "coordination",
        planspace / "artifacts" / "flows",
        planspace / "artifacts" / "research",
        planspace / "artifacts" / "readiness",
        planspace / "artifacts" / "intent",
        planspace / "artifacts" / "intent" / "global",
        planspace / "artifacts" / "intent" / "sections",
        planspace / "artifacts" / "governance",
        planspace / "artifacts" / "risk",
        planspace / "artifacts" / "notes",
        planspace / "artifacts" / "decisions",
        planspace / "artifacts" / "todos",
        planspace / "artifacts" / "inputs",
        planspace / "artifacts" / "trace",
        planspace / "artifacts" / "contracts",
        planspace / "artifacts" / "scope-deltas",
        planspace / "artifacts" / "qa-intercepts",
        planspace / "artifacts" / "substrate",
        planspace / "artifacts" / "substrate" / "prompts",
        planspace / "artifacts" / "section-inputs-hashes",
        planspace / "artifacts" / "phase2-inputs-hashes",
    ]
    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)


def _apply_sql(planspace: Path, statements: list[str]) -> None:
    if not statements:
        return
    db_path = planspace / "run.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        for statement in statements:
            conn.executescript(statement)
        conn.commit()


def seed_scenario(
    spec: ScenarioSpec,
    project_root: Path,
    base_tmp: Path | None = None,
) -> SeededState:
    """Create isolated temp directories populated from a scenario fixture."""
    if base_tmp is None:
        root = Path(tempfile.mkdtemp(prefix=f"agentic-eval-{spec.id}-"))
    else:
        root = base_tmp / spec.id
        root.mkdir(parents=True, exist_ok=False)
    planspace = root / "planspace"
    codespace = root / "codespace"
    planspace.mkdir(parents=True, exist_ok=True)
    codespace.mkdir(parents=True, exist_ok=True)
    _ensure_standard_dirs(planspace)

    for file_spec in spec.seed.planspace_files:
        _copy_fixture_file(
            spec.fixture_dir,
            str(file_spec["source"]),
            planspace / str(file_spec["path"]),
            planspace,
            codespace,
        )
    for file_spec in spec.seed.codespace_files:
        _copy_fixture_file(
            spec.fixture_dir,
            str(file_spec["source"]),
            codespace / str(file_spec["path"]),
            planspace,
            codespace,
        )

    if spec.seed.init_db:
        subprocess.run(
            ["bash", "src/scripts/db.sh", "init", str(planspace / "run.db")],
            check=True,
            cwd=str(project_root),
        )

    _apply_sql(planspace, spec.seed.sql)

    for signal in spec.seed.pre_signals:
        _write_pre_signal(planspace, signal)

    # Always enable QA mode so dispatched agents are intercepted, not live.
    params_path = planspace / "artifacts" / "parameters.json"
    if not params_path.exists():
        params_path.write_text(
            json.dumps({"qa_mode": True}, indent=2) + "\n",
            encoding="utf-8",
        )

    return SeededState(
        scenario_id=spec.id,
        root=root,
        planspace=planspace,
        codespace=codespace,
        project_root=project_root,
    )


def cleanup_scenario(state: SeededState) -> None:
    """Remove a seeded scenario root directory."""
    shutil.rmtree(state.root, ignore_errors=True)
