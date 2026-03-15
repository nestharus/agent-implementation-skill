"""End-to-end greenfield QA tests for the pipeline runner.

Verifies that the full pipeline runner bootstraps correctly for a
greenfield project (no existing code, just a spec): planspace creation,
parameters.json, run-metadata.json, governance scaffolding, run.db
initialization, schedule rendering, and stage dispatch behaviour.

Mock boundary: ``Services.dispatcher()`` and ``Services.policies()``
are mocked to avoid real LLM calls.  Everything else — file I/O,
SQLite initialization, template rendering — runs for real.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from dependency_injector import providers

from conftest import MockDispatcher, StubPolicies
from containers import Services
from intake.repository.governance_loader import bootstrap_governance_if_missing
from orchestrator.path_registry import PathRegistry
from pipeline.runner import (
    StageError,
    _CRITICAL_STAGES,
    _STAGES,
    _dispatch_stage_agent,
    _init_planspace,
    _run_stage,
    _write_schedule,
    main,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_greenfield_codespace(tmp_path: Path) -> Path:
    """Create a codespace with only a project-spec.md — no existing code."""
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


def _override_policies_and_dispatcher():
    """Override Services with stubs that avoid real LLM/policy calls.

    Returns (mock_dispatcher, cleanup_fn) so tests can inspect calls
    and clean up after themselves.
    """
    stub_policies = StubPolicies()
    mock_disp = MockDispatcher()

    Services.dispatcher.override(providers.Object(mock_disp))
    Services.policies.override(providers.Object(stub_policies))

    def cleanup():
        Services.dispatcher.reset_override()
        Services.policies.reset_override()

    return mock_disp, cleanup


# ---------------------------------------------------------------------------
# Test 1: Planspace structure
# ---------------------------------------------------------------------------

class TestPlanspaceStructure:
    """Verify that runner init creates the planspace directory tree."""

    def test_runner_creates_planspace_structure(self, tmp_path: Path) -> None:
        cs = _make_greenfield_codespace(tmp_path)
        ps = tmp_path / "planspace"
        spec = _make_spec(cs)

        registry = _init_planspace(ps, cs, "test-slug", False, spec)

        assert ps.is_dir()
        assert registry.artifacts.is_dir()
        # Verify several key subdirectories exist
        assert registry.sections_dir().is_dir()
        assert registry.proposals_dir().is_dir()
        assert registry.signals_dir().is_dir()


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

        # Use main() with --slug to test the planspace override.
        # We need to mock out everything after init to avoid real dispatch.
        mock_disp, cleanup = _override_policies_and_dispatcher()
        mock_disp.mock.return_value = ""
        try:
            # Patch _run_stage to avoid real stage execution and
            # _write_schedule to avoid needing a real template render
            # when the planspace is under ~/.claude
            with patch("pipeline.runner._run_stage") as mock_stage, \
                 patch("pipeline.runner._write_schedule"):
                result = main([
                    str(tmp_path / "dummy-planspace"),
                    str(cs),
                    "--spec", str(spec),
                    "--slug", "myproject",
                ])

            expected_planspace = Path.home() / ".claude" / "workspaces" / "myproject"
            # Verify the planspace was set correctly by checking
            # the first call to _run_stage (if any) or the artifacts
            # created at the slug path.
            assert expected_planspace.is_dir() or result == 0
            # The main function resolves planspace from slug
            # Verify by checking that parameters.json was written there
            params = expected_planspace / "artifacts" / "parameters.json"
            assert params.exists()
        finally:
            cleanup()
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
# Test 7: Schedule rendering
# ---------------------------------------------------------------------------

class TestScheduleRendering:
    """Verify _write_schedule renders the template with expected stage names."""

    def test_runner_renders_schedule(self, tmp_path: Path) -> None:
        ps = tmp_path / "planspace"
        ps.mkdir()
        spec = tmp_path / "my-spec.md"
        spec.write_text("# Spec\n")

        _write_schedule(ps, spec)

        schedule_path = ps / "schedule.md"
        assert schedule_path.exists()

        content = schedule_path.read_text(encoding="utf-8")
        # All expected stage names must appear in the schedule
        assert "decompose" in content
        assert "docstrings" in content
        assert "scan" in content
        assert "substrate" in content
        assert "section-loop" in content
        assert "verify" in content
        assert "post-verify" in content
        assert "promote" in content

    def test_schedule_header_references_template_slots(self, tmp_path: Path) -> None:
        """Schedule header contains the task-name and proposal-path slots.

        The template uses double-brace escaping (``{{task-name}}``) which
        Python's ``format_map`` renders as literal ``{task-name}``.
        """
        ps = tmp_path / "planspace"
        ps.mkdir()
        spec = tmp_path / "build-widget.md"
        spec.write_text("# Widget Spec\n")

        _write_schedule(ps, spec)

        content = (ps / "schedule.md").read_text(encoding="utf-8")
        # Template renders header with slot markers
        assert "Schedule:" in content
        assert "Source:" in content

    def test_schedule_lines_start_with_wait(self, tmp_path: Path) -> None:
        """All stage lines start with [wait] after initial rendering."""
        ps = tmp_path / "planspace"
        ps.mkdir()
        spec = tmp_path / "spec.md"
        spec.write_text("# Spec\n")

        _write_schedule(ps, spec)

        content = (ps / "schedule.md").read_text(encoding="utf-8")
        stage_lines = [
            line for line in content.splitlines()
            if line.strip() and not line.startswith("#")
        ]
        for line in stage_lines:
            assert line.startswith("[wait]"), f"Expected [wait] prefix: {line}"


# ---------------------------------------------------------------------------
# Test 8: QA-mode dispatch goes through dispatcher
# ---------------------------------------------------------------------------

class TestDispatchGoesThruDispatcher:
    """Verify that stage dispatch uses Services.dispatcher()."""

    def test_runner_qa_mode_dispatch_goes_through_dispatcher(
        self, tmp_path: Path,
    ) -> None:
        cs = _make_greenfield_codespace(tmp_path)
        ps = tmp_path / "planspace"
        spec = _make_spec(cs)

        registry = _init_planspace(ps, cs, "test", True, spec)

        mock_disp, cleanup = _override_policies_and_dispatcher()
        mock_disp.mock.return_value = ""
        try:
            _dispatch_stage_agent("decompose", ps, cs, registry)
            assert mock_disp.mock.called
            assert mock_disp.mock.call_count == 1
        finally:
            cleanup()


# ---------------------------------------------------------------------------
# Test 9: Critical stage failure aborts
# ---------------------------------------------------------------------------

class TestCriticalStageFailureAborts:
    """Verify that a critical stage failure aborts the pipeline."""

    def test_runner_critical_stage_failure_aborts(self, tmp_path: Path) -> None:
        cs = _make_greenfield_codespace(tmp_path)
        spec = _make_spec(cs)

        mock_disp, cleanup = _override_policies_and_dispatcher()
        mock_disp.mock.return_value = "QA_REJECTED:decompose failed"
        try:
            # Use main() so we can verify return code and schedule state
            with patch("pipeline.runner._mark_schedule") as mock_schedule:
                mock_schedule.return_value = ""
                result = main([
                    str(tmp_path / "planspace"),
                    str(cs),
                    "--spec", str(spec),
                    "--qa-mode",
                ])

            # Critical stage (decompose) failed -> pipeline returns 1
            assert result == 1

            # Verify _mark_schedule was called with "fail" for decompose
            fail_calls = [
                call for call in mock_schedule.call_args_list
                if call[0][0] == "fail"
            ]
            assert len(fail_calls) > 0
        finally:
            cleanup()

    def test_critical_stages_are_defined(self) -> None:
        """decompose and section-loop are critical stages."""
        assert "decompose" in _CRITICAL_STAGES
        assert "section-loop" in _CRITICAL_STAGES


# ---------------------------------------------------------------------------
# Test 10: Non-critical stage failure continues
# ---------------------------------------------------------------------------

class TestNoncriticalStageFailureContinues:
    """Verify that a non-critical stage failure allows the pipeline to continue."""

    def test_runner_noncritical_stage_failure_continues(
        self, tmp_path: Path,
    ) -> None:
        cs = _make_greenfield_codespace(tmp_path)
        spec = _make_spec(cs)

        mock_disp, cleanup = _override_policies_and_dispatcher()

        # Track which stages are dispatched
        dispatched_stages: list[str] = []

        def fake_run_stage(stage_name, planspace, codespace, registry):
            dispatched_stages.append(stage_name)
            if stage_name == "docstrings":
                raise StageError("docstrings", "non-critical failure")

        try:
            with patch("pipeline.runner._run_stage", side_effect=fake_run_stage), \
                 patch("pipeline.runner._mark_schedule", return_value=""):
                result = main([
                    str(tmp_path / "planspace"),
                    str(cs),
                    "--spec", str(spec),
                ])

            # Pipeline should complete successfully (return 0)
            # because docstrings is non-critical
            assert result == 0

            # Verify decompose ran first, then docstrings, then scan
            # (pipeline did NOT abort after docstrings failure)
            assert "decompose" in dispatched_stages
            assert "docstrings" in dispatched_stages
            assert "scan" in dispatched_stages

            # Verify stages after docstrings were attempted
            docstrings_idx = dispatched_stages.index("docstrings")
            assert len(dispatched_stages) > docstrings_idx + 1

        finally:
            cleanup()

    def test_docstrings_is_not_critical(self) -> None:
        """docstrings stage is NOT in the critical stages set."""
        assert "docstrings" not in _CRITICAL_STAGES

    def test_noncritical_stage_skip_marks_schedule(self, tmp_path: Path) -> None:
        """When a non-critical agent dispatch returns QA_REJECTED,
        the schedule is marked skip (not fail) and the stage returns."""
        cs = _make_greenfield_codespace(tmp_path)
        ps = tmp_path / "planspace"
        spec = _make_spec(cs)

        registry = _init_planspace(ps, cs, "test", False, spec)
        _write_schedule(ps, spec)

        mock_disp, cleanup = _override_policies_and_dispatcher()
        mock_disp.mock.return_value = "QA_REJECTED:skipped"
        try:
            with patch("pipeline.runner._mark_schedule") as mock_schedule:
                mock_schedule.return_value = ""
                # docstrings is non-critical: dispatch rejected -> skip
                _run_stage("docstrings", ps, cs, registry)

            skip_calls = [
                call for call in mock_schedule.call_args_list
                if call[0][0] == "skip"
            ]
            assert len(skip_calls) > 0
        finally:
            cleanup()


# ---------------------------------------------------------------------------
# Additional: Full init path (init + governance + schedule)
# ---------------------------------------------------------------------------

class TestFullInitPath:
    """Verify the full initialization sequence that main() performs
    before driving stages."""

    def test_full_init_creates_all_artifacts(self, tmp_path: Path) -> None:
        cs = _make_greenfield_codespace(tmp_path)
        ps = tmp_path / "planspace"
        spec = _make_spec(cs)

        # Run the full init sequence manually
        registry = _init_planspace(ps, cs, "full-init", True, spec)
        bootstrap_governance_if_missing(cs)
        _write_schedule(ps, spec)

        # Copy spec like main() does
        spec_dest = registry.artifacts / "spec.md"
        spec_dest.write_text(spec.read_text(encoding="utf-8"), encoding="utf-8")

        # Verify everything is in place
        assert (ps / "artifacts" / "parameters.json").exists()
        assert (ps / "artifacts" / "run-metadata.json").exists()
        assert (ps / "run.db").exists()
        assert (ps / "schedule.md").exists()
        assert (ps / "artifacts" / "spec.md").exists()
        assert (cs / "governance" / "problems" / "index.md").exists()
        assert (cs / "governance" / "patterns" / "index.md").exists()

    def test_init_is_idempotent(self, tmp_path: Path) -> None:
        """Calling _init_planspace twice does not corrupt state."""
        cs = _make_greenfield_codespace(tmp_path)
        ps = tmp_path / "planspace"
        spec = _make_spec(cs)

        _init_planspace(ps, cs, "test", False, spec)
        registry2 = _init_planspace(ps, cs, "test", True, spec)

        # Second call should overwrite parameters.json with new value
        data = json.loads(registry2.parameters().read_text(encoding="utf-8"))
        assert data["qa_mode"] is True

        # DB should still be valid
        conn = sqlite3.connect(str(registry2.run_db()))
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM tasks")
        conn.close()
