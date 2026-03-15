"""Component tests for philosophy signal classifier."""

from __future__ import annotations

import json
from pathlib import Path

from containers import ArtifactIOService
from src.intent.service.philosophy_classifier import (
    VALID_SOURCE_TYPES,
    ClassifierState,
    PhilosophyClassifier,
    _guidance_schema_error,
    _invalid_source_map_detail,
    _manifest_source_mode,
    _user_source_is_substantive,
    MIN_USER_SOURCE_BYTES,
    SOURCE_MODE_REPO,
    SOURCE_MODE_USER,
)


def _make_classifier() -> PhilosophyClassifier:
    return PhilosophyClassifier(artifact_io=ArtifactIOService())


# -- _classify_list_signal_result (via selector) ──────────────────────────────


def test_selector_missing_signal(tmp_path: Path) -> None:
    """Non-existent file yields MISSING_SIGNAL."""
    cls = _make_classifier()
    result = cls._classify_selector_result(tmp_path / "missing.json")
    assert result["state"] == ClassifierState.MISSING_SIGNAL
    assert result["data"] is None


def test_selector_malformed_not_json(tmp_path: Path) -> None:
    """Non-JSON content yields MALFORMED_SIGNAL."""
    p = tmp_path / "signal.json"
    p.write_text("not json {{{", encoding="utf-8")
    result = _make_classifier()._classify_selector_result(p)
    assert result["state"] == ClassifierState.MALFORMED_SIGNAL


def test_selector_malformed_not_dict(tmp_path: Path) -> None:
    """JSON array (not dict) yields MALFORMED_SIGNAL."""
    p = tmp_path / "signal.json"
    p.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    result = _make_classifier()._classify_selector_result(p)
    assert result["state"] == ClassifierState.MALFORMED_SIGNAL


def test_selector_malformed_missing_status(tmp_path: Path) -> None:
    """Dict without a 'status' field yields MALFORMED_SIGNAL (require_status=True)."""
    p = tmp_path / "signal.json"
    p.write_text(json.dumps({"sources": []}), encoding="utf-8")
    result = _make_classifier()._classify_selector_result(p)
    assert result["state"] == ClassifierState.MALFORMED_SIGNAL


def test_selector_malformed_invalid_status(tmp_path: Path) -> None:
    """Unrecognized status value yields MALFORMED_SIGNAL."""
    p = tmp_path / "signal.json"
    p.write_text(json.dumps({"status": "bogus", "sources": []}), encoding="utf-8")
    result = _make_classifier()._classify_selector_result(p)
    assert result["state"] == ClassifierState.MALFORMED_SIGNAL


def test_selector_malformed_status_empty_but_has_sources(tmp_path: Path) -> None:
    """Status says 'empty' but sources list is non-empty — MALFORMED."""
    p = tmp_path / "signal.json"
    p.write_text(
        json.dumps({"status": "empty", "sources": ["a"]}),
        encoding="utf-8",
    )
    result = _make_classifier()._classify_selector_result(p)
    assert result["state"] == ClassifierState.MALFORMED_SIGNAL


def test_selector_malformed_status_selected_but_no_sources(tmp_path: Path) -> None:
    """Status says 'selected' but sources list is empty — MALFORMED."""
    p = tmp_path / "signal.json"
    p.write_text(
        json.dumps({"status": "selected", "sources": []}),
        encoding="utf-8",
    )
    result = _make_classifier()._classify_selector_result(p)
    assert result["state"] == ClassifierState.MALFORMED_SIGNAL


def test_selector_valid_empty(tmp_path: Path) -> None:
    """Consistent empty selector signal yields VALID_EMPTY."""
    p = tmp_path / "signal.json"
    p.write_text(
        json.dumps({"status": "empty", "sources": []}),
        encoding="utf-8",
    )
    result = _make_classifier()._classify_selector_result(p)
    assert result["state"] == ClassifierState.VALID_EMPTY


def test_selector_valid_nonempty(tmp_path: Path) -> None:
    """Consistent selected selector signal yields VALID_NONEMPTY."""
    p = tmp_path / "signal.json"
    p.write_text(
        json.dumps({"status": "selected", "sources": ["file.md"]}),
        encoding="utf-8",
    )
    result = _make_classifier()._classify_selector_result(p)
    assert result["state"] == ClassifierState.VALID_NONEMPTY


# -- _classify_verifier_result ─────────────────────────────────────────────────


def test_verifier_missing_signal(tmp_path: Path) -> None:
    result = _make_classifier()._classify_verifier_result(tmp_path / "missing.json")
    assert result["state"] == ClassifierState.MISSING_SIGNAL


def test_verifier_valid_nonempty(tmp_path: Path) -> None:
    p = tmp_path / "signal.json"
    p.write_text(
        json.dumps({"verified_sources": ["v1"], "rejected": []}),
        encoding="utf-8",
    )
    result = _make_classifier()._classify_verifier_result(p)
    assert result["state"] == ClassifierState.VALID_NONEMPTY


def test_verifier_valid_empty(tmp_path: Path) -> None:
    p = tmp_path / "signal.json"
    p.write_text(
        json.dumps({"verified_sources": [], "rejected": []}),
        encoding="utf-8",
    )
    result = _make_classifier()._classify_verifier_result(p)
    assert result["state"] == ClassifierState.VALID_EMPTY


def test_verifier_malformed_missing_required_field(tmp_path: Path) -> None:
    """Missing 'rejected' field yields MALFORMED_SIGNAL."""
    p = tmp_path / "signal.json"
    p.write_text(
        json.dumps({"verified_sources": ["v1"]}),
        encoding="utf-8",
    )
    result = _make_classifier()._classify_verifier_result(p)
    assert result["state"] == ClassifierState.MALFORMED_SIGNAL


# -- _classify_distiller_result ────────────────────────────────────────────────


def test_distiller_missing_philosophy(tmp_path: Path) -> None:
    """Missing philosophy file yields MISSING_SIGNAL."""
    source_map = tmp_path / "source_map.json"
    source_map.write_text("{}", encoding="utf-8")
    result = _make_classifier()._classify_distiller_result(
        tmp_path / "philosophy.md", source_map,
    )
    assert result["state"] == ClassifierState.MISSING_SIGNAL


def test_distiller_missing_source_map(tmp_path: Path) -> None:
    """Missing source_map file yields MISSING_SIGNAL."""
    phil = tmp_path / "philosophy.md"
    phil.write_text("Some philosophy", encoding="utf-8")
    result = _make_classifier()._classify_distiller_result(
        phil, tmp_path / "source_map.json",
    )
    assert result["state"] == ClassifierState.MISSING_SIGNAL


def test_distiller_empty_philosophy_empty_map_is_valid_empty(tmp_path: Path) -> None:
    """Empty philosophy text + empty source_map = VALID_EMPTY."""
    phil = tmp_path / "philosophy.md"
    phil.write_text("", encoding="utf-8")
    source_map = tmp_path / "source_map.json"
    source_map.write_text(json.dumps({}), encoding="utf-8")
    result = _make_classifier()._classify_distiller_result(phil, source_map)
    assert result["state"] == ClassifierState.VALID_EMPTY


def test_distiller_valid_nonempty(tmp_path: Path) -> None:
    """Non-empty philosophy + valid source_map = VALID_NONEMPTY."""
    phil = tmp_path / "philosophy.md"
    phil.write_text("# Philosophy\nP1: clarity", encoding="utf-8")
    source_map = tmp_path / "source_map.json"
    source_map.write_text(
        json.dumps({
            "P1": {
                "source_type": "repo_source",
                "source_file": "readme.md",
                "source_section": "intro",
            },
        }),
        encoding="utf-8",
    )
    result = _make_classifier()._classify_distiller_result(phil, source_map)
    assert result["state"] == ClassifierState.VALID_NONEMPTY


def test_distiller_nonempty_philosophy_empty_map_is_malformed(tmp_path: Path) -> None:
    """Non-empty philosophy but empty source_map is malformed (inconsistent)."""
    phil = tmp_path / "philosophy.md"
    phil.write_text("# Philosophy\nP1", encoding="utf-8")
    source_map = tmp_path / "source_map.json"
    source_map.write_text(json.dumps({}), encoding="utf-8")
    result = _make_classifier()._classify_distiller_result(phil, source_map)
    assert result["state"] == ClassifierState.MALFORMED_SIGNAL


# -- _classify_guidance_result ─────────────────────────────────────────────────


def test_guidance_missing(tmp_path: Path) -> None:
    result = _make_classifier()._classify_guidance_result(tmp_path / "missing.json")
    assert result["state"] == ClassifierState.MISSING_SIGNAL


def test_guidance_valid(tmp_path: Path) -> None:
    p = tmp_path / "guidance.json"
    p.write_text(
        json.dumps({
            "project_frame": "Build a web app",
            "prompts": [
                {"prompt": "What is the goal?", "why_this_matters": "Clarity"},
            ],
            "notes": ["Note one"],
        }),
        encoding="utf-8",
    )
    result = _make_classifier()._classify_guidance_result(p)
    assert result["state"] == ClassifierState.VALID_NONEMPTY


def test_guidance_malformed_missing_project_frame(tmp_path: Path) -> None:
    p = tmp_path / "guidance.json"
    p.write_text(
        json.dumps({"prompts": [], "notes": []}),
        encoding="utf-8",
    )
    result = _make_classifier()._classify_guidance_result(p)
    assert result["state"] == ClassifierState.MALFORMED_SIGNAL


# -- Pure function: _invalid_source_map_detail ─────────────────────────────────


def test_source_map_valid() -> None:
    source_map = {
        "P1": {
            "source_type": "repo_source",
            "source_file": "readme.md",
            "source_section": "intro",
        },
    }
    assert _invalid_source_map_detail(source_map) is None


def test_source_map_invalid_key() -> None:
    source_map = {"X1": {"source_type": "repo_source", "source_file": "f", "source_section": "s"}}
    error = _invalid_source_map_detail(source_map)
    assert error is not None
    assert "principle IDs" in error


def test_source_map_invalid_source_type() -> None:
    source_map = {"P1": {"source_type": "unknown", "source_file": "f", "source_section": "s"}}
    error = _invalid_source_map_detail(source_map)
    assert error is not None
    assert "source_type" in error


def test_source_map_empty_source_file() -> None:
    source_map = {"P1": {"source_type": "repo_source", "source_file": "", "source_section": "s"}}
    error = _invalid_source_map_detail(source_map)
    assert error is not None
    assert "source_file" in error


# -- Pure function: _guidance_schema_error ─────────────────────────────────────


def test_guidance_schema_valid() -> None:
    payload = {
        "project_frame": "Build widgets",
        "prompts": [{"prompt": "How?", "why_this_matters": "Important"}],
        "notes": ["One note"],
    }
    assert _guidance_schema_error(payload) is None


def test_guidance_schema_not_dict() -> None:
    assert _guidance_schema_error("string") is not None


def test_guidance_schema_empty_project_frame() -> None:
    payload = {"project_frame": "", "prompts": [], "notes": []}
    assert _guidance_schema_error(payload) is not None


def test_guidance_schema_bad_prompt_entry() -> None:
    payload = {
        "project_frame": "Frame",
        "prompts": [{"prompt": "", "why_this_matters": "ok"}],
        "notes": [],
    }
    error = _guidance_schema_error(payload)
    assert error is not None
    assert "prompt" in error


def test_guidance_schema_bad_note() -> None:
    payload = {
        "project_frame": "Frame",
        "prompts": [],
        "notes": [""],
    }
    error = _guidance_schema_error(payload)
    assert error is not None
    assert "notes" in error


# -- Pure function: _user_source_is_substantive ────────────────────────────────


def test_user_source_substantive(tmp_path: Path) -> None:
    p = tmp_path / "source.md"
    p.write_text("x" * (MIN_USER_SOURCE_BYTES + 1), encoding="utf-8")
    assert _user_source_is_substantive(p) is True


def test_user_source_too_small(tmp_path: Path) -> None:
    p = tmp_path / "source.md"
    p.write_text("x" * 10, encoding="utf-8")
    assert _user_source_is_substantive(p) is False


def test_user_source_missing(tmp_path: Path) -> None:
    assert _user_source_is_substantive(tmp_path / "missing.md") is False


# -- Pure function: _manifest_source_mode ──────────────────────────────────────


def test_manifest_source_mode_defaults_to_repo() -> None:
    assert _manifest_source_mode(None) == SOURCE_MODE_REPO
    assert _manifest_source_mode({}) == SOURCE_MODE_REPO


def test_manifest_source_mode_user_only() -> None:
    manifest = {"sources": [{"source_type": "user_source"}]}
    assert _manifest_source_mode(manifest) == SOURCE_MODE_USER


def test_manifest_source_mode_mixed_returns_repo() -> None:
    manifest = {
        "sources": [
            {"source_type": "user_source"},
            {"source_type": "repo_source"},
        ],
    }
    assert _manifest_source_mode(manifest) == SOURCE_MODE_REPO


def test_manifest_source_mode_empty_sources_returns_repo() -> None:
    manifest = {"sources": []}
    assert _manifest_source_mode(manifest) == SOURCE_MODE_REPO
