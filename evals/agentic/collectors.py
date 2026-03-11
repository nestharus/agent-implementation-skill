"""Collectors for files and database snapshots after a scenario run."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .scenario_loader import CollectSpec


@dataclass
class CollectedOutput:
    files: dict[str, str]
    files_json: dict[str, Any]
    db: dict[str, list[dict]]
    existence_map: dict[str, bool]
    parseability_map: dict[str, bool]


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _collect_files(file_globs: list[str], planspace: Path) -> tuple[dict[str, str], dict[str, Any], dict[str, bool], dict[str, bool]]:
    files: dict[str, str] = {}
    files_json: dict[str, Any] = {}
    existence_map: dict[str, bool] = {}
    parseability_map: dict[str, bool] = {}

    for pattern in file_globs:
        matches = sorted(planspace.glob(pattern))
        existence_map[pattern] = any(path.is_file() for path in matches)
        for path in matches:
            if path.is_dir():
                continue
            relative_path = path.relative_to(planspace).as_posix()
            content = _read_text(path)
            files[relative_path] = content
            existence_map[relative_path] = True
            if path.suffix.lower() == ".json":
                try:
                    files_json[relative_path] = json.loads(content)
                    parseability_map[relative_path] = True
                except json.JSONDecodeError:
                    parseability_map[relative_path] = False
    return files, files_json, existence_map, parseability_map


def _query_db(db_path: Path, queries: list[dict]) -> dict[str, list[dict]]:
    if not db_path.exists() or not queries:
        return {str(query["name"]): [] for query in queries}

    collected: dict[str, list[dict]] = {}
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        for query in queries:
            rows = conn.execute(str(query["sql"])).fetchall()
            collected[str(query["name"])] = [dict(row) for row in rows]
    return collected


def collect_outputs(spec: CollectSpec, planspace: Path) -> CollectedOutput:
    """Collect file artifacts and DB snapshots."""
    files, files_json, existence_map, parseability_map = _collect_files(
        spec.file_globs,
        planspace,
    )
    db = _query_db(planspace / "run.db", spec.db_queries)
    return CollectedOutput(
        files=files,
        files_json=files_json,
        db=db,
        existence_map=existence_map,
        parseability_map=parseability_map,
    )
