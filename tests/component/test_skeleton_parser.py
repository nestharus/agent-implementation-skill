"""Unit tests for skeleton codemap parser (Piece 4e).

Tests the pure parsing function that extracts ModuleEntry records
from skeleton codemap markdown text.
"""

from __future__ import annotations

import pytest

from scan.codemap.skeleton_parser import ModuleEntry, parse_skeleton_modules


# ---------------------------------------------------------------------------
# Fixture: realistic skeleton codemap text
# ---------------------------------------------------------------------------

SKELETON_CODEMAP = """\
# Project Codemap (Skeleton)

This is a multi-module Python project.

## Architecture Overview

The project is organized into several top-level packages.

## Routing Table

### Subsystems
- api: src/api — HTTP API layer and request handling
- core: src/core — Business logic and domain models
- db: src/db — Database access and migrations
- utils: src/utils — Shared utility functions

### Entry Points
- src/main.py: Application entry point

### Key Interfaces
- src/core/service.py: Main service interface

### Unknowns
- src/legacy: Purpose unclear

### Confidence
- overall: medium
- reason: skeleton scan only
"""


class TestParseSkeletonModules:
    """parse_skeleton_modules extracts subsystem entries from markdown."""

    def test_extracts_all_modules(self) -> None:
        entries = parse_skeleton_modules(SKELETON_CODEMAP)
        assert len(entries) == 4

    def test_module_fields_populated(self) -> None:
        entries = parse_skeleton_modules(SKELETON_CODEMAP)
        by_name = {e.name: e for e in entries}

        assert by_name["api"].path == "src/api"
        assert by_name["api"].description == "HTTP API layer and request handling"
        assert by_name["core"].path == "src/core"
        assert by_name["db"].description == "Database access and migrations"

    def test_sorted_by_name(self) -> None:
        entries = parse_skeleton_modules(SKELETON_CODEMAP)
        names = [e.name for e in entries]
        assert names == sorted(names)

    def test_empty_text_returns_empty(self) -> None:
        assert parse_skeleton_modules("") == []

    def test_no_routing_table_returns_empty(self) -> None:
        text = "# Codemap\n\nSome content without a routing table.\n"
        assert parse_skeleton_modules(text) == []

    def test_routing_table_without_subsystems_returns_empty(self) -> None:
        text = """\
## Routing Table

### Entry Points
- src/main.py: entry
"""
        assert parse_skeleton_modules(text) == []

    def test_empty_subsystems_block_returns_empty(self) -> None:
        text = """\
## Routing Table

### Subsystems

### Entry Points
- src/main.py: entry
"""
        assert parse_skeleton_modules(text) == []

    def test_double_hyphen_separator(self) -> None:
        text = """\
## Routing Table

### Subsystems
- frontend: web/app -- React SPA
"""
        entries = parse_skeleton_modules(text)
        assert len(entries) == 1
        assert entries[0].name == "frontend"
        assert entries[0].path == "web/app"
        assert entries[0].description == "React SPA"

    def test_em_dash_separator(self) -> None:
        text = """\
## Routing Table

### Subsystems
- backend: server/src \u2014 Express server
"""
        entries = parse_skeleton_modules(text)
        assert len(entries) == 1
        assert entries[0].description == "Express server"

    def test_en_dash_separator(self) -> None:
        text = """\
## Routing Table

### Subsystems
- lib: packages/lib \u2013 Shared library
"""
        entries = parse_skeleton_modules(text)
        assert len(entries) == 1
        assert entries[0].description == "Shared library"

    def test_malformed_lines_skipped(self) -> None:
        text = """\
## Routing Table

### Subsystems
- valid: src/valid -- Good entry
- this line has no path-description separator
Not even a bullet point
- also-missing: the-dash-separator-here
- another-valid: src/other -- Second good entry
"""
        entries = parse_skeleton_modules(text)
        assert len(entries) == 2
        names = {e.name for e in entries}
        assert names == {"valid", "another-valid"}

    def test_glob_pattern_paths(self) -> None:
        text = """\
## Routing Table

### Subsystems
- tests: tests/**/*.py -- Test suite
- configs: config/*.yaml -- Configuration files
"""
        entries = parse_skeleton_modules(text)
        assert len(entries) == 2
        by_name = {e.name: e for e in entries}
        assert by_name["tests"].path == "tests/**/*.py"
        assert by_name["configs"].path == "config/*.yaml"

    def test_subsystems_at_end_of_routing_table(self) -> None:
        """Subsystems block at end of document (no trailing ### header)."""
        text = """\
## Routing Table

### Entry Points
- main.py: entry

### Subsystems
- core: src/core -- Core module
"""
        entries = parse_skeleton_modules(text)
        assert len(entries) == 1
        assert entries[0].name == "core"

    def test_ignores_subsystems_outside_routing_table(self) -> None:
        """A ### Subsystems header outside ## Routing Table is ignored."""
        text = """\
## Other Section

### Subsystems
- fake: fake/path -- Should not be parsed

## Routing Table

### Subsystems
- real: real/path -- Should be parsed
"""
        entries = parse_skeleton_modules(text)
        assert len(entries) == 1
        assert entries[0].name == "real"

    def test_asterisk_bullets(self) -> None:
        """Asterisk bullets are accepted alongside hyphens."""
        text = """\
## Routing Table

### Subsystems
* api: src/api -- API layer
* db: src/db -- Database layer
"""
        entries = parse_skeleton_modules(text)
        assert len(entries) == 2

    def test_module_entry_is_frozen(self) -> None:
        entry = ModuleEntry(name="x", path="y", description="z")
        with pytest.raises(AttributeError):
            entry.name = "changed"
