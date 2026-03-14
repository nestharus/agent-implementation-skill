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

from _paths import SRC_DIR
from src.orchestrator.path_registry import PathRegistry


@pytest.fixture()
def scan_planspace(tmp_path: Path) -> Path:
    """Create a planspace directory for scan stage tests."""
    ps = tmp_path / "planspace"
    ps.mkdir()
    PathRegistry(ps).ensure_artifacts_tree()
    (ps / "artifacts" / "file-cards").mkdir(parents=True, exist_ok=True)
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
    monkeypatch.setattr("scan.scan_dispatcher.dispatch_agent", mock)
    # Also patch at import sites
    monkeypatch.setattr("scan.codemap.codemap_builder.dispatch_agent", mock)
    monkeypatch.setattr("scan.explore.section_explorer.dispatch_agent", mock)
    monkeypatch.setattr("scan.service.feedback_collector.dispatch_agent", mock)
    monkeypatch.setattr("scan.explore.tier_ranker.dispatch_agent", mock)
    monkeypatch.setattr("scan.explore.analyzer.dispatch_agent", mock)
    return mock


class TestFeedbackSchemaEnforcement:
    """V1: Feedback entries with missing required fields are skipped."""

    def test_missing_relevant_field_skipped(
        self, scan_planspace: Path, scan_codespace: Path,
    ) -> None:
        """Feedback without 'relevant' is logged and skipped."""
        from scan.service.feedback_collector import collect_and_route_feedback

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
        from scan.service.feedback_collector import collect_and_route_feedback

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
        from scan.service.feedback_collector import collect_and_route_feedback

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
        from scan.service.feedback_collector import _apply_feedback

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
            model_policy={"feedback_updater": "glm", "exploration": "claude-opus"},
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
        from scan.codemap.cache import FileCardCache

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
        from scan.codemap.cache import FileCardCache

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
        from scan.codemap.codemap_builder import run_codemap_build

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
        from scan.codemap.codemap_builder import run_codemap_build

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


# ------------------------------------------------------------------
# Round 29 regression guards
# ------------------------------------------------------------------


class TestScanSummaryIdempotency:
    """V1 (R29): Scan summaries use HTML comment markers for idempotent
    replacement and are stripped from cache keys."""

    def test_update_match_idempotent(self, tmp_path: Path) -> None:
        """Repeated update_match calls don't accumulate duplicate blocks."""
        from scan.related.match_updater import update_match

        section = tmp_path / "section.md"
        section.write_text(
            "# Section\n\n## Related Files\n\n### src/main.py\nOriginal\n",
        )

        feedback = tmp_path / "deep-response-feedback.json"
        feedback.write_text(json.dumps({
            "relevant": True,
            "source_file": "src/main.py",
            "summary_lines": ["Line A", "Line B"],
        }))
        response = tmp_path / "deep-response.md"
        response.write_text("analysis text")

        # Apply twice
        update_match(section, "src/main.py", response)
        first_text = section.read_text()
        update_match(section, "src/main.py", response)
        second_text = section.read_text()

        assert first_text == second_text, (
            "update_match must be idempotent — second call should not "
            "accumulate duplicate summary blocks"
        )

    def test_cache_key_ignores_scan_summaries(self, tmp_path: Path) -> None:
        """Cache key is stable regardless of scan summary content."""
        from scan.codemap.cache import FileCardCache

        section = tmp_path / "section.md"
        source = tmp_path / "source.py"
        source.write_text("code")

        # Key without summary
        section.write_text("# Section\n### src/main.py\n")
        k1 = FileCardCache.content_hash(section, source)

        # Key with summary block
        section.write_text(
            "# Section\n### src/main.py\n"
            "<!-- scan-summary:begin -->\n> summary\n"
            "<!-- scan-summary:end -->\n",
        )
        k2 = FileCardCache.content_hash(section, source)

        assert k1 == k2, (
            "Scan summaries must not change cache keys"
        )


class TestCachedFeedbackValidation:
    """V3 (R29): Invalid cached feedback triggers re-analysis."""

    def test_invalid_cached_feedback_is_cache_miss(
        self, tmp_path: Path,
    ) -> None:
        """Cache with invalid feedback falls through to fresh analysis."""
        from scan.codemap.cache import FileCardCache, is_valid_cached_feedback

        cards_dir = tmp_path / "cards"
        cache = FileCardCache(cards_dir)

        # Store a response with invalid feedback (missing source_file)
        resp = tmp_path / "resp.md"
        resp.write_text("analysis")
        fb = tmp_path / "fb.json"
        fb.write_text(json.dumps({"relevant": True}))  # missing source_file

        key = "test_key"
        # Store should skip invalid feedback
        cache.store(key, resp, fb)

        assert cache.get(key) is not None  # response cached
        assert cache.get_feedback(key) is None, (
            "Invalid feedback must not be cached"
        )

    def test_valid_feedback_is_cached(self, tmp_path: Path) -> None:
        """Valid feedback is stored in cache."""
        from scan.codemap.cache import FileCardCache

        cards_dir = tmp_path / "cards"
        cache = FileCardCache(cards_dir)

        resp = tmp_path / "resp.md"
        resp.write_text("analysis")
        fb = tmp_path / "fb.json"
        fb.write_text(json.dumps({
            "relevant": True, "source_file": "src/main.py",
        }))

        key = "test_key_valid"
        cache.store(key, resp, fb)

        assert cache.get(key) is not None
        assert cache.get_feedback(key) is not None, (
            "Valid feedback must be cached"
        )


class TestBridgeDirectiveTypeSafety:
    """V4 (R29): Bridge directive handles bool and non-dict types."""

    def test_bool_bridge_coerced_to_dict(self) -> None:
        """Bridge directive as bool is coerced to dict in parser."""
        import inspect
        from coordination.service.planner import _normalize_bridge_directives
        src = inspect.getsource(_normalize_bridge_directives)
        assert "isinstance(bridge, bool)" in src, (
            "Parser must handle bool bridge directives"
        )

    def test_bridge_directive_is_typed_dataclass(self) -> None:
        """Bridge directives use typed BridgeDirective, not raw dicts."""
        from coordination.types import BridgeDirective
        bd = BridgeDirective()
        assert bd.needed is False
        assert bd.reason == ""


class TestScanModelPolicy:
    """V5 (R29): Scan-stage model selection is configurable via policy."""

    def test_default_policy_has_all_tasks(self) -> None:
        """Default policy covers all scan task types."""
        from scan.service.scan_dispatch_config import DEFAULT_SCAN_MODELS
        required = {
            "codemap_build", "codemap_freshness", "exploration",
            "validation", "tier_ranking", "deep_analysis",
            "feedback_updater",
        }
        assert required <= set(DEFAULT_SCAN_MODELS), (
            f"Missing default models: {required - set(DEFAULT_SCAN_MODELS)}"
        )

    def test_policy_override_from_file(self, tmp_path: Path) -> None:
        """model-policy.json overrides default scan models."""
        from scan.scan_dispatcher import read_scan_model_policy

        planspace = tmp_path / "planspace"
        planspace.mkdir()
        PathRegistry(planspace).ensure_artifacts_tree()
        artifacts_dir = planspace / "artifacts"
        policy_file = artifacts_dir / "model-policy.json"
        policy_file.write_text(json.dumps({
            "scan": {"tier_ranking": "custom-model"},
        }))

        policy = read_scan_model_policy(artifacts_dir)
        assert policy["tier_ranking"] == "custom-model"
        # Other keys remain defaults
        assert policy["codemap_build"] == "claude-opus"

    def test_no_hardcoded_models_in_scan(self) -> None:
        """Scan modules use model_policy, not hardcoded strings."""
        import ast
        from pathlib import Path as P

        scan_dir = P(__file__).resolve().parent.parent.parent / "src" / "scripts" / "scan"
        violations = []
        for py_file in scan_dir.glob("*.py"):
            if py_file.name in ("dispatch.py", "__init__.py", "__main__.py"):
                continue
            tree = ast.parse(py_file.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.keyword) and node.arg == "model":
                    if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                        violations.append(
                            f"{py_file.name}:{node.lineno}: "
                            f"hardcoded model={node.value.value!r}"
                        )
        assert not violations, (
            "Hardcoded model= strings found in scan modules:\n"
            + "\n".join(violations)
        )


class TestStaleToolSurfaceRemoval:
    """V6 (R29): Stale tool surface is removed when no tools are relevant."""

    def test_stale_removal_code_exists(self) -> None:
        """Section engine runner removes stale tools-available surface."""
        import inspect
        from implementation.engine.section_pipeline import run_section
        src = inspect.getsource(run_section)
        assert "tools_available_path.unlink()" in src or \
               "tools_available_path.exists()" in src, (
            "Runner must handle stale tool surface removal"
        )


class TestScanLoopClosure:
    """V2 (R29): Deep scan runs bounded follow-up pass on new files."""

    def test_max_scan_passes_is_bounded(self) -> None:
        """_MAX_SCAN_PASSES prevents unbounded iteration."""
        from scan.explore.deep_scanner import _MAX_SCAN_PASSES
        assert 1 < _MAX_SCAN_PASSES <= 3, (
            f"_MAX_SCAN_PASSES={_MAX_SCAN_PASSES} must be 2-3"
        )

    def test_already_scanned_files_skipped(
        self, scan_planspace: Path, scan_codespace: Path,
        mock_scan_dispatch: MagicMock,
    ) -> None:
        """Files already in already_scanned set are not re-dispatched."""
        from scan.explore.deep_scanner import _scan_sections
        from scan.codemap.cache import FileCardCache

        artifacts = scan_planspace / "artifacts"
        scan_log = scan_planspace / "scan-logs"
        scan_log.mkdir(parents=True, exist_ok=True)

        sec_file = artifacts / "sections" / "section-01.md"
        sec_file.write_text(
            "# Section 01\n\n## Related Files\n\n### src/main.py\n",
        )

        # Pre-populate tier file
        tier_file = artifacts / "sections" / "section-01-file-tiers.json"
        tier_file.write_text(json.dumps({
            "tiers": {"critical": ["src/main.py"]},
            "scan_now": ["critical"],
        }))
        tier_sidecar = artifacts / "sections" / "section-01-file-tiers.inputs.sha256"

        # Write source file
        (scan_codespace / "src" / "main.py").write_text("def main(): pass")

        # Mark src/main.py as already scanned
        already_scanned: dict[str, set[str]] = {
            "section-01": {"src/main.py"},
        }

        # Mock returns success + writes tier sidecar to skip regeneration
        import hashlib
        from scan.codemap.cache import strip_scan_summaries
        raw = sec_file.read_text()
        tier_input = strip_scan_summaries(raw) + "\n" + "src/main.py"
        tier_sidecar.write_text(hashlib.sha256(tier_input.encode()).hexdigest())

        from scan.scan_context import ScanContext
        _scan_sections(
            section_files=[sec_file],
            ctx=ScanContext(
                codespace=scan_codespace,
                codemap_path=artifacts / "codemap.md",
                corrections_path=artifacts / "signals" / "codemap-corrections.json",
                scan_log_dir=scan_log,
                model_policy={"tier_ranking": "glm", "exploration": "claude-opus",
                              "deep_analysis": "glm"},
            ),
            artifacts_dir=artifacts,
            file_card_cache=FileCardCache(artifacts / "file-cards"),
            already_scanned=already_scanned,
        )

        # dispatch_agent should NOT have been called for analysis
        # (tier ranking was skipped via sidecar, analysis skipped via already_scanned)
        assert mock_scan_dispatch.call_count == 0, (
            "Already-scanned files must not trigger dispatch_agent"
        )


# ------------------------------------------------------------------
# R41/V1: Missing tier ranking must be a first-class failure
# ------------------------------------------------------------------


class TestDeepScanTierRankingFailureUnit:
    """R41/V1: _scan_sections must return failure (True) and log when
    tier ranking is unavailable, not silently skip."""

    def test_missing_tier_ranking_returns_failure(
        self,
        scan_planspace: Path,
        scan_codespace: Path,
        mock_scan_dispatch: MagicMock,
    ) -> None:
        """_scan_sections returns True (failure) when no tier ranking."""
        from scan.codemap.cache import FileCardCache
        from scan.explore.deep_scanner import _scan_sections

        artifacts = scan_planspace / "artifacts"
        scan_log = scan_planspace / "scan-logs"
        scan_log.mkdir(parents=True, exist_ok=True)

        # Section with related files but NO tier file
        sec_file = artifacts / "sections" / "section-01.md"
        sec_file.write_text(
            "# Section 01\n\n## Related Files\n\n### src/main.py\n",
        )

        # Mock dispatch to fail tier ranking (returncode != 0, no tier file)
        mock_scan_dispatch.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="tier failed",
        )

        from scan.scan_context import ScanContext
        result = _scan_sections(
            section_files=[sec_file],
            ctx=ScanContext(
                codespace=scan_codespace,
                codemap_path=artifacts / "codemap.md",
                corrections_path=artifacts / "signals" / "codemap-corrections.json",
                scan_log_dir=scan_log,
                model_policy={
                    "tier_ranking": "glm", "exploration": "claude-opus",
                    "deep_analysis": "glm",
                },
            ),
            artifacts_dir=artifacts,
            file_card_cache=FileCardCache(artifacts / "file-cards"),
            already_scanned={},
        )

        assert result is True, (
            "_scan_sections must return True (failure) when tier "
            "ranking is unavailable"
        )
        # Failure must be logged
        failures_log = scan_log / "failures.log"
        assert failures_log.is_file(), (
            "failures.log must exist when tier ranking is unavailable"
        )
        assert "tier ranking unavailable" in failures_log.read_text()
