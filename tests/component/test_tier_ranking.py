from __future__ import annotations

import json
import subprocess

from dependency_injector import providers

from containers import PromptGuard, Services
from src.orchestrator.path_registry import PathRegistry
from src.staleness.helpers.content_hasher import content_hash
from src.scan.explore.tier_ranker import TierRanker
from src.scan.codemap.cache import strip_scan_summaries


def _make_ranker():
    return TierRanker(
        artifact_io=Services.artifact_io(),
        hasher=Services.hasher(),
        prompt_guard=Services.prompt_guard(),
        task_router=Services.task_router(),
    )


def test_validate_tier_file_accepts_required_shape(tmp_path) -> None:
    tier_file = tmp_path / "tiers.json"
    tier_file.write_text(
        json.dumps({"tiers": {"critical": ["src/main.py"]}, "scan_now": ["critical"]}),
        encoding="utf-8",
    )

    assert _make_ranker().validate_tier_file(tier_file) is True


def test_validate_tier_file_rejects_missing_scan_now(tmp_path) -> None:
    tier_file = tmp_path / "tiers.json"
    tier_file.write_text(
        json.dumps({"tiers": {"critical": ["src/main.py"]}}),
        encoding="utf-8",
    )

    assert _make_ranker().validate_tier_file(tier_file) is False


def test_run_tier_ranking_reuses_matching_existing_tier_file(
    tmp_path,
    monkeypatch,
) -> None:
    section_file = tmp_path / "section-01.md"
    section_file.write_text(
        "# Section\n\n## Related Files\n\n### src/main.py\n",
        encoding="utf-8",
    )
    PathRegistry(tmp_path).ensure_artifacts_tree()
    artifacts_dir = tmp_path / "artifacts"
    sections_dir = artifacts_dir / "sections"
    tier_file = sections_dir / "section-01-file-tiers.json"
    tier_file.write_text(
        json.dumps({"tiers": {"critical": ["src/main.py"]}, "scan_now": ["critical"]}),
        encoding="utf-8",
    )
    sidecar = sections_dir / "section-01-file-tiers.inputs.sha256"
    related_files = ["src/main.py"]
    fingerprint = content_hash(
        strip_scan_summaries(section_file.read_text(encoding="utf-8"))
        + "\n"
        + "\n".join(sorted(related_files)),
    )
    sidecar.write_text(fingerprint, encoding="utf-8")

    def fail_dispatch(**_kwargs):
        raise AssertionError("dispatch_agent should not run when inputs match")

    monkeypatch.setattr("src.scan.explore.tier_ranker.dispatch_agent", fail_dispatch)

    result = _make_ranker().run_tier_ranking(
        section_file,
        "section-01",
        related_files,
        tmp_path / "codespace",
        artifacts_dir,
        tmp_path / "scan-logs",
        {"tier_ranking": "glm", "exploration": "claude-opus"},
    )

    assert result == tier_file


def test_run_tier_ranking_dispatches_and_writes_sidecar(
    tmp_path,
    monkeypatch,
) -> None:
    section_file = tmp_path / "section-01.md"
    section_file.write_text(
        "# Section\n\n## Related Files\n\n### src/main.py\n",
        encoding="utf-8",
    )
    PathRegistry(tmp_path).ensure_artifacts_tree()
    artifacts_dir = tmp_path / "artifacts"
    scan_log_dir = tmp_path / "scan-logs"
    codespace = tmp_path / "codespace"
    codespace.mkdir()

    monkeypatch.setattr(
        "src.scan.explore.tier_ranker.load_scan_template",
        lambda _name: "{section_file}\n{file_list_text}\n{tier_file}",
    )
    class _NoopGuard(PromptGuard):
        def validate_dynamic(self, content):
            return []
        def write_validated(self, content, path):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            return True

    Services.prompt_guard.override(providers.Object(_NoopGuard()))

    def fake_dispatch(**kwargs):
        tier_file = artifacts_dir / "sections" / "section-01-file-tiers.json"
        tier_file.write_text(
            json.dumps({"tiers": {"critical": ["src/main.py"]}, "scan_now": ["critical"]}),
            encoding="utf-8",
        )
        kwargs["stdout_file"].write_text("ranked", encoding="utf-8")
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    monkeypatch.setattr("src.scan.explore.tier_ranker.dispatch_agent", fake_dispatch)

    ranker = TierRanker(
        artifact_io=Services.artifact_io(),
        hasher=Services.hasher(),
        prompt_guard=_NoopGuard(),
        task_router=Services.task_router(),
    )

    try:
        result = ranker.run_tier_ranking(
            section_file,
            "section-01",
            ["src/main.py"],
            codespace,
            artifacts_dir,
            scan_log_dir,
            {"tier_ranking": "glm", "exploration": "claude-opus"},
        )

        assert result == artifacts_dir / "sections" / "section-01-file-tiers.json"
        assert (
            artifacts_dir / "sections" / "section-01-file-tiers.inputs.sha256"
        ).is_file()
    finally:
        Services.prompt_guard.reset_override()
