"""Integration tests for Stage 3 scan package.

Mock boundary: only ``scan.dispatch.dispatch_agent`` is mocked.
Everything else — file I/O, caching, prompt generation — runs for real.

Guards the violations fixed in Round 26:
- V1: Feedback schema enforcement (fail-closed on missing fields)
- V2: Corrections propagation to updater prompt
- V3: Cache key invalidation includes corrections
- V4: Codemap reuse with missing fingerprint dispatches verifier
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest


PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def scan_planspace(tmp_path: Path) -> Path:
    """Create a planspace directory for scan stage tests."""
    ps = tmp_path / "planspace"
    ps.mkdir()
    artifacts = ps / "artifacts"
    for subdir in ("sections", "signals", "file-cards", "scope-deltas"):
        (artifacts / subdir).mkdir(parents=True)
    return ps


@pytest.fixture()
def scan_codespace(tmp_path: Path) -> Path:
    """Create a minimal codespace for scan stage tests."""
    cs = tmp_path / "codespace"
    cs.mkdir()
    (cs / "src").mkdir()
    (cs / "src" / "main.py").write_text("def main():\n    pass\n")
    (cs / "src" / "utils.py").write_text("def helper():\n    return 42\n")
    return cs


@pytest.fixture()
def mock_scan_dispatch(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Mock dispatch_agent at the scan package dispatch boundary.

    Per scan/dispatch.py docstring: mock ``scan.dispatch.dispatch_agent``
    the same way section_loop tests mock their dispatch boundary.
    """
    mock = MagicMock()
    mock.return_value = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="", stderr="",
    )
    monkeypatch.setattr("scan.dispatch.dispatch_agent", mock)
    # Also patch at import sites
    monkeypatch.setattr("scan.codemap.dispatch_agent", mock)
    monkeypatch.setattr("scan.deep_scan.dispatch_agent", mock)
    monkeypatch.setattr("scan.feedback.dispatch_agent", mock)
    return mock


class TestFeedbackSchemaEnforcement:
    """V1: Feedback entries with missing required fields are skipped."""

    def test_missing_relevant_field_skipped(
        self, scan_planspace: Path, scan_codespace: Path,
    ) -> None:
        """Feedback without 'relevant' is logged and skipped."""
        from scan.feedback import collect_and_route_feedback

        artifacts = scan_planspace / "artifacts"
        scan_log = scan_planspace / "scan-logs"

        # Create section
        sec_file = artifacts / "sections" / "section-01.md"
        sec_file.write_text(
            "# Section 01\n\n## Related Files\n\n### src/main.py\n",
        )

        # Create feedback without 'relevant' field
        sec_log = scan_log / "section-01"
        sec_log.mkdir(parents=True)
        fb = {"source_file": "src/main.py", "summary_lines": ["test"]}
        (sec_log / "deep-src_main_py-feedback.json").write_text(
            json.dumps(fb),
        )

        collect_and_route_feedback(
            section_files=[sec_file],
            codemap_path=artifacts / "codemap.md",
            codespace=scan_codespace,
            artifacts_dir=artifacts,
            scan_log_dir=scan_log,
        )

        # Entry should be logged as failure
        failures = (scan_log / "failures.log").read_text()
        assert "Missing required fields" in failures
        assert "relevant" in failures

    def test_missing_source_file_field_skipped(
        self, scan_planspace: Path, scan_codespace: Path,
    ) -> None:
        """Feedback without 'source_file' is logged and skipped."""
        from scan.feedback import collect_and_route_feedback

        artifacts = scan_planspace / "artifacts"
        scan_log = scan_planspace / "scan-logs"

        sec_file = artifacts / "sections" / "section-01.md"
        sec_file.write_text(
            "# Section 01\n\n## Related Files\n\n### src/main.py\n",
        )

        sec_log = scan_log / "section-01"
        sec_log.mkdir(parents=True)
        fb = {"relevant": True}  # missing source_file
        (sec_log / "deep-src_main_py-feedback.json").write_text(
            json.dumps(fb),
        )

        collect_and_route_feedback(
            section_files=[sec_file],
            codemap_path=artifacts / "codemap.md",
            codespace=scan_codespace,
            artifacts_dir=artifacts,
            scan_log_dir=scan_log,
        )

        failures = (scan_log / "failures.log").read_text()
        assert "Missing required fields" in failures
        assert "source_file" in failures

    def test_valid_feedback_not_skipped(
        self, scan_planspace: Path, scan_codespace: Path,
    ) -> None:
        """Feedback with all required fields is processed normally."""
        from scan.feedback import collect_and_route_feedback

        artifacts = scan_planspace / "artifacts"
        scan_log = scan_planspace / "scan-logs"

        sec_file = artifacts / "sections" / "section-01.md"
        sec_file.write_text(
            "# Section 01\n\n## Related Files\n\n### src/main.py\n",
        )

        sec_log = scan_log / "section-01"
        sec_log.mkdir(parents=True)
        fb = {
            "relevant": False,
            "source_file": "src/main.py",
            "reason": "Not related",
            "missing_files": [],
            "summary_lines": ["test"],
        }
        (sec_log / "deep-src_main_py-feedback.json").write_text(
            json.dumps(fb),
        )

        result = collect_and_route_feedback(
            section_files=[sec_file],
            codemap_path=artifacts / "codemap.md",
            codespace=scan_codespace,
            artifacts_dir=artifacts,
            scan_log_dir=scan_log,
        )

        assert result is True  # has_feedback
        # No failures logged for valid feedback
        failures_path = scan_log / "failures.log"
        if failures_path.is_file():
            assert "Missing required fields" not in failures_path.read_text()


class TestCorrectionsInUpdaterPrompt:
    """V2: Related-files-updater prompt includes corrections when present."""

    def test_updater_prompt_includes_corrections(
        self,
        scan_planspace: Path,
        scan_codespace: Path,
        mock_scan_dispatch: MagicMock,
    ) -> None:
        """When corrections exist, updater prompt references them."""
        from scan.feedback import _apply_feedback

        artifacts = scan_planspace / "artifacts"
        scan_log = scan_planspace / "scan-logs"

        # Create corrections
        corrections = artifacts / "signals" / "codemap-corrections.json"
        corrections.write_text('{"fixes": [{"path": "src/old.py"}]}')

        # Create section with related files
        sec_file = artifacts / "sections" / "section-01.md"
        sec_file.write_text(
            "# Section 01\n\n## Related Files\n\n### src/main.py\n",
        )

        # Create feedback with missing files
        sec_log = scan_log / "section-01"
        sec_log.mkdir(parents=True)
        fb = {
            "relevant": True,
            "source_file": "src/main.py",
            "missing_files": ["src/config.py"],
            "summary_lines": ["test"],
        }
        (sec_log / "deep-src_main_py-feedback.json").write_text(
            json.dumps(fb),
        )

        # Mock dispatch to produce a valid signal
        def write_signal(*args, **kwargs):
            signal_path = artifacts / "signals" / "section-01-related-files-update.json"
            signal_path.write_text(json.dumps({"status": "stale", "additions": [], "removals": []}))
            return subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr="",
            )
        mock_scan_dispatch.side_effect = write_signal

        codemap = artifacts / "codemap.md"
        codemap.write_text("# Codemap")

        _apply_feedback(
            section_files=[sec_file],
            codemap_path=codemap,
            codespace=scan_codespace,
            artifacts_dir=artifacts,
            scan_log_dir=scan_log,
        )

        # Check that the generated prompt includes corrections
        prompt_file = sec_log / "related-files-updater-prompt.md"
        assert prompt_file.is_file()
        prompt_text = prompt_file.read_text()
        assert "codemap-corrections.json" in prompt_text
        assert "authoritative" in prompt_text.lower()


class TestCacheKeyIncludesCorrections:
    """V3: FileCardCache key changes when corrections change."""

    def test_corrections_change_cache_key(self, tmp_path: Path) -> None:
        """Cache key must differ when corrections content changes."""
        from scan.cache import FileCardCache

        section = tmp_path / "section.md"
        source = tmp_path / "source.py"
        corrections = tmp_path / "corrections.json"

        section.write_text("# Section content")
        source.write_text("def foo(): pass")

        # Key without corrections (file doesn't exist)
        k1 = FileCardCache.content_hash(section, source, corrections)

        # Key with corrections
        corrections.write_text('{"fixes": []}')
        k2 = FileCardCache.content_hash(section, source, corrections)
        assert k1 != k2, "Corrections presence must change cache key"

        # Key with different corrections
        corrections.write_text('{"fixes": [{"path": "a.py"}]}')
        k3 = FileCardCache.content_hash(section, source, corrections)
        assert k2 != k3, "Corrections content change must change cache key"

    def test_no_corrections_matches_old_behavior(self, tmp_path: Path) -> None:
        """Without extra files, key matches the 2-file computation."""
        from scan.cache import FileCardCache

        section = tmp_path / "section.md"
        source = tmp_path / "source.py"
        section.write_text("# Section")
        source.write_text("code")

        k_two = FileCardCache.content_hash(section, source)
        nonexistent = tmp_path / "does-not-exist.json"
        k_three = FileCardCache.content_hash(section, source, nonexistent)

        # When extra file doesn't exist, it contributes nothing
        assert k_two == k_three


class TestCodemapReuseMissingFingerprint:
    """V4: Codemap reuse with missing fingerprint dispatches verifier."""

    def test_missing_fingerprint_dispatches_verifier(
        self,
        scan_planspace: Path,
        scan_codespace: Path,
        mock_scan_dispatch: MagicMock,
    ) -> None:
        """When codemap exists but fingerprint is missing, verifier is
        dispatched instead of blind reuse."""
        from scan.codemap import run_codemap_build

        artifacts = scan_planspace / "artifacts"
        scan_log = scan_planspace / "scan-logs"
        scan_log.mkdir(parents=True)

        codemap = artifacts / "codemap.md"
        codemap.write_text("# Codemap\nExisting content")

        fingerprint = artifacts / "codespace.fingerprint"
        # Deliberately do NOT create fingerprint file

        # Mock: verifier says rebuild
        call_count = [0]
        def mock_dispatch_fn(*args, **kwargs):
            call_count[0] += 1
            # First call is freshness verifier (GLM) — say rebuild
            if call_count[0] == 1:
                signal = artifacts / "signals" / "codemap-freshness.json"
                signal.parent.mkdir(parents=True, exist_ok=True)
                signal.write_text(json.dumps({"rebuild": True, "reason": "test"}))
                return subprocess.CompletedProcess(
                    args=[], returncode=0, stdout="", stderr="",
                )
            # Second call is actual codemap build (Opus)
            codemap.write_text("# Rebuilt Codemap")
            return subprocess.CompletedProcess(
                args=[], returncode=0, stdout="# Rebuilt Codemap", stderr="",
            )

        mock_scan_dispatch.side_effect = mock_dispatch_fn

        # Initialize git so fingerprint works
        subprocess.run(
            ["git", "init"], cwd=str(scan_codespace),
            capture_output=True,
        )
        subprocess.run(
            ["git", "add", "."], cwd=str(scan_codespace),
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "init", "--allow-empty"],
            cwd=str(scan_codespace),
            capture_output=True,
            env={"GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "t@t",
                 "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "t@t",
                 "HOME": str(scan_codespace), "PATH": "/usr/bin:/bin"},
        )

        result = run_codemap_build(
            codemap_path=codemap,
            codespace=scan_codespace,
            artifacts_dir=artifacts,
            scan_log_dir=scan_log,
            fingerprint_path=fingerprint,
        )

        # Verifier was dispatched (not blind reuse)
        assert call_count[0] >= 1, (
            "Verifier must be dispatched when fingerprint is missing"
        )

    def test_missing_fingerprint_verifier_says_reuse(
        self,
        scan_planspace: Path,
        scan_codespace: Path,
        mock_scan_dispatch: MagicMock,
    ) -> None:
        """When verifier says reuse, codemap is kept and fingerprint stored."""
        from scan.codemap import run_codemap_build

        artifacts = scan_planspace / "artifacts"
        scan_log = scan_planspace / "scan-logs"
        scan_log.mkdir(parents=True)

        codemap = artifacts / "codemap.md"
        codemap.write_text("# Codemap\nExisting content")

        fingerprint = artifacts / "codespace.fingerprint"

        # Mock: verifier says reuse (rebuild=false)
        def mock_dispatch_fn(*args, **kwargs):
            signal = artifacts / "signals" / "codemap-freshness.json"
            signal.parent.mkdir(parents=True, exist_ok=True)
            signal.write_text(json.dumps({"rebuild": False, "reason": "still fresh"}))
            return subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr="",
            )

        mock_scan_dispatch.side_effect = mock_dispatch_fn

        # Initialize git
        subprocess.run(
            ["git", "init"], cwd=str(scan_codespace),
            capture_output=True,
        )
        subprocess.run(
            ["git", "add", "."], cwd=str(scan_codespace),
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "init", "--allow-empty"],
            cwd=str(scan_codespace),
            capture_output=True,
            env={"GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "t@t",
                 "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "t@t",
                 "HOME": str(scan_codespace), "PATH": "/usr/bin:/bin"},
        )

        result = run_codemap_build(
            codemap_path=codemap,
            codespace=scan_codespace,
            artifacts_dir=artifacts,
            scan_log_dir=scan_log,
            fingerprint_path=fingerprint,
        )

        assert result is True
        # Fingerprint should be written after successful reuse
        assert fingerprint.is_file()
