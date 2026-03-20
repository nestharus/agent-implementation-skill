"""Unit tests for codemap fragment seeding (Piece 5B) and skeleton mode flag (Piece 4f).

Tests the pure functions that split a global codemap into per-section
fragments, and the skeleton_only plumbing on _prepare_build_prompt.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from orchestrator.path_registry import PathRegistry
from scan.codemap.codemap_builder import (
    CodemapBuilder,
    _extract_codemap_fragment,
    _normalise_path,
    write_section_fragments,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

GLOBAL_CODEMAP = """\
# Project Codemap

## Overview
A multi-module project with api, core, and db packages.

## API Layer
The api layer lives in src/api/ and handles HTTP requests.
- src/api/routes.py: URL routing
- src/api/handlers.py: Request handlers

## Core Logic
Business logic in src/core/ covers domain models.
- src/core/models.py: Domain models
- src/core/service.py: Service layer

## Database
Database code in src/db/ handles persistence.
- src/db/migrations.py: Schema migrations
- src/db/queries.py: Query helpers

## Routing Table

### Subsystems
- api: src/api -- HTTP API layer
- core: src/core -- Business logic
- db: src/db -- Database access
"""

SECTION_01_SPEC = """\
# Section 01: API Layer

Build the HTTP API endpoints.

## Related Files

### src/api/routes.py
URL routing definitions.

### src/api/handlers.py
Request handler implementations.
"""

SECTION_02_SPEC = """\
# Section 02: Database Layer

Set up database access.

## Related Files

### src/db/migrations.py
Schema migration scripts.

### src/db/queries.py
Query helper functions.
"""

SECTION_03_NO_RELATED = """\
# Section 03: Documentation

Write project docs.
"""


# ---------------------------------------------------------------------------
# _extract_codemap_fragment tests
# ---------------------------------------------------------------------------


class TestExtractCodemapFragment:
    """_extract_codemap_fragment extracts relevant portions of codemap."""

    def test_extracts_matching_lines(self) -> None:
        fragment = _extract_codemap_fragment(
            GLOBAL_CODEMAP,
            ["src/api/routes.py", "src/api/handlers.py"],
        )
        assert "src/api/routes.py" in fragment
        assert "src/api/handlers.py" in fragment

    def test_excludes_unrelated_lines(self) -> None:
        fragment = _extract_codemap_fragment(
            GLOBAL_CODEMAP,
            ["src/api/routes.py"],
        )
        assert "src/db/" not in fragment
        assert "src/core/" not in fragment

    def test_includes_section_headers_before_matches(self) -> None:
        fragment = _extract_codemap_fragment(
            GLOBAL_CODEMAP,
            ["src/core/models.py"],
        )
        assert "## Core Logic" in fragment

    def test_includes_document_title(self) -> None:
        fragment = _extract_codemap_fragment(
            GLOBAL_CODEMAP,
            ["src/db/queries.py"],
        )
        assert "# Project Codemap" in fragment

    def test_empty_related_files_returns_empty(self) -> None:
        assert _extract_codemap_fragment(GLOBAL_CODEMAP, []) == ""

    def test_no_matches_returns_empty(self) -> None:
        assert _extract_codemap_fragment(
            GLOBAL_CODEMAP, ["nonexistent/file.py"],
        ) == ""

    def test_empty_codemap_returns_empty(self) -> None:
        assert _extract_codemap_fragment("", ["src/api/routes.py"]) == ""


class TestNormalisePath:
    """_normalise_path strips leading ./ and /."""

    def test_strips_dot_slash(self) -> None:
        assert _normalise_path("./src/api") == "src/api"

    def test_strips_leading_slash(self) -> None:
        assert _normalise_path("/src/api") == "src/api"

    def test_plain_path_unchanged(self) -> None:
        assert _normalise_path("src/api") == "src/api"

    def test_strips_whitespace(self) -> None:
        assert _normalise_path("  src/api  ") == "src/api"


# ---------------------------------------------------------------------------
# write_section_fragments tests
# ---------------------------------------------------------------------------


class TestWriteSectionFragments:
    """write_section_fragments splits global codemap by section."""

    def _setup_planspace(self, tmp_path: Path) -> PathRegistry:
        """Create a planspace with codemap and section specs."""
        planspace = tmp_path / "planspace"
        paths = PathRegistry(planspace)
        paths.ensure_artifacts_tree()

        # Write global codemap
        paths.codemap().write_text(GLOBAL_CODEMAP, encoding="utf-8")

        # Write section specs
        paths.section_spec("01").write_text(SECTION_01_SPEC, encoding="utf-8")
        paths.section_spec("02").write_text(SECTION_02_SPEC, encoding="utf-8")
        paths.section_spec("03").write_text(SECTION_03_NO_RELATED, encoding="utf-8")

        return paths

    def test_writes_fragments_for_sections_with_related_files(
        self, tmp_path: Path,
    ) -> None:
        paths = self._setup_planspace(tmp_path)
        written = write_section_fragments(paths)

        assert written == 2
        assert paths.section_codemap("01").is_file()
        assert paths.section_codemap("02").is_file()

    def test_fragment_content_scoped_to_section(self, tmp_path: Path) -> None:
        paths = self._setup_planspace(tmp_path)
        write_section_fragments(paths)

        frag_01 = paths.section_codemap("01").read_text(encoding="utf-8")
        assert "src/api/routes.py" in frag_01
        assert "src/db/" not in frag_01

        frag_02 = paths.section_codemap("02").read_text(encoding="utf-8")
        assert "src/db/migrations.py" in frag_02
        assert "src/api/" not in frag_02

    def test_skips_section_without_related_files(self, tmp_path: Path) -> None:
        paths = self._setup_planspace(tmp_path)
        write_section_fragments(paths)

        assert not paths.section_codemap("03").exists()

    def test_returns_zero_when_no_codemap(self, tmp_path: Path) -> None:
        planspace = tmp_path / "planspace"
        paths = PathRegistry(planspace)
        paths.ensure_artifacts_tree()
        # No codemap written
        assert write_section_fragments(paths) == 0

    def test_returns_zero_when_codemap_empty(self, tmp_path: Path) -> None:
        planspace = tmp_path / "planspace"
        paths = PathRegistry(planspace)
        paths.ensure_artifacts_tree()
        paths.codemap().write_text("", encoding="utf-8")
        assert write_section_fragments(paths) == 0

    def test_returns_zero_when_no_sections_dir(self, tmp_path: Path) -> None:
        planspace = tmp_path / "planspace"
        paths = PathRegistry(planspace)
        # Don't call ensure_artifacts_tree — sections dir won't exist
        paths.codemap().parent.mkdir(parents=True, exist_ok=True)
        paths.codemap().write_text(GLOBAL_CODEMAP, encoding="utf-8")
        assert write_section_fragments(paths) == 0

    def test_creates_fragments_dir_if_missing(self, tmp_path: Path) -> None:
        paths = self._setup_planspace(tmp_path)
        # Remove fragments dir if ensure_artifacts_tree created it
        frag_dir = paths.codemap_fragments_dir()
        if frag_dir.is_dir():
            frag_dir.rmdir()

        write_section_fragments(paths)
        assert frag_dir.is_dir()


# ---------------------------------------------------------------------------
# Skeleton mode flag (4f) tests
# ---------------------------------------------------------------------------


class TestSkeletonModeFlag:
    """_prepare_build_prompt respects skeleton_only parameter."""

    def _make_builder(self) -> CodemapBuilder:
        return CodemapBuilder(
            prompt_guard=MagicMock(validate_dynamic=MagicMock(return_value=[])),
            task_router=MagicMock(),
            artifact_io=MagicMock(),
        )

    def test_default_uses_codemap_build_template(self, tmp_path: Path) -> None:
        """When skeleton_only is False (default), uses codemap_build.md."""
        builder = self._make_builder()
        artifacts_dir = tmp_path / "artifacts"
        artifacts_dir.mkdir()
        scan_log_dir = tmp_path / "scan-logs"
        scan_log_dir.mkdir()
        codemap_path = artifacts_dir / "codemap.md"

        with patch(
            "scan.codemap.codemap_builder.load_scan_template",
        ) as mock_load:
            mock_load.return_value = "prompt {project_mode_path} {project_mode_signal}"
            builder._prepare_build_prompt(
                codemap_path=codemap_path,
                artifacts_dir=artifacts_dir,
                scan_log_dir=scan_log_dir,
            )
            mock_load.assert_called_once_with("codemap_build.md")

    def test_skeleton_only_uses_skeleton_template(self, tmp_path: Path) -> None:
        """When skeleton_only is True, uses codemap_skeleton_build.md."""
        builder = self._make_builder()
        artifacts_dir = tmp_path / "artifacts"
        artifacts_dir.mkdir()
        scan_log_dir = tmp_path / "scan-logs"
        scan_log_dir.mkdir()
        codemap_path = artifacts_dir / "codemap.md"

        with patch(
            "scan.codemap.codemap_builder.load_scan_template",
        ) as mock_load:
            mock_load.return_value = "prompt {project_mode_path} {project_mode_signal}"
            builder._prepare_build_prompt(
                codemap_path=codemap_path,
                artifacts_dir=artifacts_dir,
                scan_log_dir=scan_log_dir,
                skeleton_only=True,
            )
            mock_load.assert_called_once_with("codemap_skeleton_build.md")
