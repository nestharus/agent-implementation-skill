"""End-to-end greenfield QA tests for the pipeline runner.

Verifies that the bootstrap runner creates the planspace correctly:
parameters.json, run-metadata.json, run.db initialization, spec copy,
and the handoff seam.

Mock boundary: ``_handoff`` is patched to avoid real orchestrator
construction.  Everything else -- file I/O, SQLite initialization --
runs for real.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from intake.repository.governance_loader import bootstrap_governance_if_missing
from orchestrator.path_registry import PathRegistry
from pipeline.runner import (
    _init_planspace,
    main,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_greenfield_codespace(tmp_path: Path) -> Path:
    """Create a codespace with only a project-spec.md -- no existing code."""
    cs = tmp_path / "codespace"
    cs.mkdir()
    (cs / "project-spec.md").write_text(
        "# Project Spec\n\nBuild a greenfield widget service.\n",
        encoding="utf-8",
    )
    return cs


def _make_spec(codespace: Path) -> Path:
    """Return the spec file path inside the codespace."""
    return codespace / "project-spec.md"


# ---------------------------------------------------------------------------
# Test 1: Planspace structure
# ---------------------------------------------------------------------------

class TestPlanspaceStructure:
    """Verify that runner init creates the planspace artifacts root."""

    def test_runner_creates_planspace_structure(self, tmp_path: Path) -> None:
        cs = _make_greenfield_codespace(tmp_path)
        ps = tmp_path / "planspace"
        spec = _make_spec(cs)

        registry = _init_planspace(ps, cs, "test-slug", False, spec)

        assert ps.is_dir()
        assert registry.artifacts.is_dir()


# ---------------------------------------------------------------------------
# Test 2: parameters.json
# ---------------------------------------------------------------------------

class TestParametersJson:
    """Verify parameters.json content reflects qa_mode flag."""

    def test_runner_writes_parameters_json_qa_true(self, tmp_path: Path) -> None:
        cs = _make_greenfield_codespace(tmp_path)
        ps = tmp_path / "planspace-qa-true"
        spec = _make_spec(cs)

        registry = _init_planspace(ps, cs, "test", True, spec)

        params_path = registry.parameters()
        assert params_path.exists()
        data = json.loads(params_path.read_text(encoding="utf-8"))
        assert data["qa_mode"] is True

    def test_runner_writes_parameters_json_qa_false(self, tmp_path: Path) -> None:
        cs = _make_greenfield_codespace(tmp_path)
        ps = tmp_path / "planspace-qa-false"
        spec = _make_spec(cs)

        registry = _init_planspace(ps, cs, "test", False, spec)

        params_path = registry.parameters()
        assert params_path.exists()
        data = json.loads(params_path.read_text(encoding="utf-8"))
        assert data["qa_mode"] is False


# ---------------------------------------------------------------------------
# Test 3: run-metadata.json
# ---------------------------------------------------------------------------

class TestRunMetadata:
    """Verify run-metadata.json contains expected fields."""

    def test_runner_writes_run_metadata(self, tmp_path: Path) -> None:
        cs = _make_greenfield_codespace(tmp_path)
        ps = tmp_path / "planspace"
        spec = _make_spec(cs)

        registry = _init_planspace(ps, cs, "test-slug", False, spec)

        meta_path = registry.artifacts / "run-metadata.json"
        assert meta_path.exists()
        data = json.loads(meta_path.read_text(encoding="utf-8"))
        assert data["slug"] == "test-slug"
        assert data["planspace"] == str(ps)
        assert data["codespace"] == str(cs)
        assert data["spec"] == str(spec)
        assert "started_at" in data
        # started_at should be a valid ISO timestamp
        assert "T" in data["started_at"]


# ---------------------------------------------------------------------------
# Test 4: Slug overrides planspace
# ---------------------------------------------------------------------------

class TestSlugOverridesPlanspace:
    """Verify --slug causes planspace to be under ~/.claude/workspaces/."""

    def test_runner_slug_overrides_planspace(self, tmp_path: Path) -> None:
        cs = _make_greenfield_codespace(tmp_path)
        spec = _make_spec(cs)

        try:
            with patch("pipeline.runner._handoff"):
                result = main([
                    str(tmp_path / "dummy-planspace"),
                    str(cs),
                    "--spec", str(spec),
                    "--slug", "myproject",
                ])

            expected_planspace = Path.home() / ".claude" / "workspaces" / "myproject"
            assert expected_planspace.is_dir() or result == 0
            # Verify by checking that parameters.json was written there
            params = expected_planspace / "artifacts" / "parameters.json"
            assert params.exists()
        finally:
            # Clean up the slug directory if created
            slug_dir = Path.home() / ".claude" / "workspaces" / "myproject"
            if slug_dir.exists():
                import shutil
                shutil.rmtree(slug_dir)


# ---------------------------------------------------------------------------
# Test 5: Governance bootstrap
# ---------------------------------------------------------------------------

class TestGovernanceBootstrap:
    """Verify bootstrap_governance_if_missing creates governance scaffolding."""

    def test_runner_bootstraps_governance(self, tmp_path: Path) -> None:
        cs = tmp_path / "codespace-empty"
        cs.mkdir()

        created = bootstrap_governance_if_missing(cs)

        assert created is True
        assert (cs / "governance" / "problems" / "index.md").exists()
        assert (cs / "governance" / "patterns" / "index.md").exists()
        assert (cs / "governance" / "risk-register.md").exists()
        assert (cs / "governance" / "constraints" / "index.md").exists()
        assert (cs / "system-synthesis.md").exists()

    def test_governance_not_recreated_if_present(self, tmp_path: Path) -> None:
        """If governance already exists, bootstrap returns False."""
        cs = tmp_path / "codespace-existing"
        cs.mkdir()
        (cs / "governance" / "problems").mkdir(parents=True)
        (cs / "governance" / "problems" / "index.md").write_text("# Existing\n")

        created = bootstrap_governance_if_missing(cs)

        assert created is False

    def test_governance_content_is_parseable(self, tmp_path: Path) -> None:
        """Bootstrapped governance files contain valid markdown headings."""
        cs = tmp_path / "codespace-parse"
        cs.mkdir()
        bootstrap_governance_if_missing(cs)

        problems = (cs / "governance" / "problems" / "index.md").read_text(encoding="utf-8")
        assert "# Problem Archive" in problems

        patterns = (cs / "governance" / "patterns" / "index.md").read_text(encoding="utf-8")
        assert "# Pattern Catalog" in patterns

        risk = (cs / "governance" / "risk-register.md").read_text(encoding="utf-8")
        assert "# Risk Register" in risk

        constraints = (cs / "governance" / "constraints" / "index.md").read_text(encoding="utf-8")
        assert "# Constraint Archive" in constraints

        synthesis = (cs / "system-synthesis.md").read_text(encoding="utf-8")
        assert "# System Synthesis" in synthesis


# ---------------------------------------------------------------------------
# Test 6: run.db initialization
# ---------------------------------------------------------------------------

class TestRunDbInit:
    """Verify that _init_planspace creates a valid SQLite database."""

    def test_runner_initializes_run_db(self, tmp_path: Path) -> None:
        cs = _make_greenfield_codespace(tmp_path)
        ps = tmp_path / "planspace"
        spec = _make_spec(cs)

        registry = _init_planspace(ps, cs, "test", False, spec)

        db_path = registry.run_db()
        assert db_path.exists()

        # Verify it is a valid SQLite database with expected tables
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = {row[0] for row in cursor.fetchall()}
        conn.close()

        assert "tasks" in tables
        assert "messages" in tables
        assert "events" in tables
        assert "gates" in tables

    def test_run_db_is_queryable(self, tmp_path: Path) -> None:
        """Verify we can execute queries against the initialized DB."""
        cs = _make_greenfield_codespace(tmp_path)
        ps = tmp_path / "planspace"
        spec = _make_spec(cs)

        registry = _init_planspace(ps, cs, "test", False, spec)

        conn = sqlite3.connect(str(registry.run_db()))
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM tasks")
        count = cursor.fetchone()[0]
        conn.close()

        assert count == 0  # No tasks inserted yet


# ---------------------------------------------------------------------------
# Test 7: Handoff seam
# ---------------------------------------------------------------------------

class TestHandoff:
    """Verify the handoff seam delegates correctly."""

    def test_main_calls_handoff(self, tmp_path: Path) -> None:
        """main() calls _handoff after initialization."""
        cs = _make_greenfield_codespace(tmp_path)
        ps = tmp_path / "planspace"
        spec = _make_spec(cs)

        with patch("pipeline.runner._handoff") as mock_handoff:
            result = main([str(ps), str(cs), "--spec", str(spec)])

        assert result == 0
        mock_handoff.assert_called_once()
        call_args = mock_handoff.call_args
        assert call_args[0][0] == ps  # planspace
        assert call_args[0][1] == cs  # codespace

    def test_spec_copied_to_artifacts(self, tmp_path: Path) -> None:
        """main() copies the spec into artifacts/spec.md."""
        cs = _make_greenfield_codespace(tmp_path)
        ps = tmp_path / "planspace"
        spec = _make_spec(cs)

        with patch("pipeline.runner._handoff"):
            main([str(ps), str(cs), "--spec", str(spec)])

        spec_dest = ps / "artifacts" / "spec.md"
        assert spec_dest.exists()
        assert spec_dest.read_text(encoding="utf-8") == spec.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Test 8: Full init path
# ---------------------------------------------------------------------------

class TestFullInitPath:
    """Verify the full initialization sequence that main() performs."""

    def test_full_init_creates_all_artifacts(self, tmp_path: Path) -> None:
        cs = _make_greenfield_codespace(tmp_path)
        ps = tmp_path / "planspace"
        spec = _make_spec(cs)

        with patch("pipeline.runner._handoff"):
            main([str(ps), str(cs), "--spec", str(spec)])

        # Verify everything is in place
        assert (ps / "artifacts" / "parameters.json").exists()
        assert (ps / "artifacts" / "run-metadata.json").exists()
        assert (ps / "run.db").exists()
        assert (ps / "artifacts" / "spec.md").exists()

    def test_init_is_idempotent(self, tmp_path: Path) -> None:
        """Calling _init_planspace twice does not corrupt state."""
        cs = _make_greenfield_codespace(tmp_path)
        ps = tmp_path / "planspace"
        spec = _make_spec(cs)

        _init_planspace(ps, cs, "test", False, spec)
        registry2 = _init_planspace(ps, cs, "test", True, spec)

        # Second call preserves original parameters.json (idempotent)
        data = json.loads(registry2.parameters().read_text(encoding="utf-8"))
        assert data["qa_mode"] is False

        # DB should still be valid
        conn = sqlite3.connect(str(registry2.run_db()))
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM tasks")
        conn.close()
