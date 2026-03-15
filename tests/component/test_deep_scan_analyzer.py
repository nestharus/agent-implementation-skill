from __future__ import annotations

import hashlib
import json
import subprocess

from dependency_injector import providers

from containers import PromptGuard, Services
from src.scan.explore.analyzer import Analyzer, safe_name
from src.scan.related.match_updater import MatchUpdater
from src.scan.codemap.cache import FileCardCache
from src.scan.scan_context import ScanContext


def test_safe_name_matches_bash_compatible_scheme() -> None:
    source_file = "src/main.py"
    expected_hash = hashlib.sha1(source_file.encode()).hexdigest()[:10]  # noqa: S324

    assert safe_name(source_file) == f"src_main_py.py.{expected_hash}"


def test_analyze_file_returns_false_when_source_missing(tmp_path) -> None:
    section_file = tmp_path / "section-01.md"
    section_file.write_text("# Section\n", encoding="utf-8")
    scan_log_dir = tmp_path / "scan-logs"
    scan_log_dir.mkdir()

    analyzer = Analyzer(
        prompt_guard=Services.prompt_guard(),
        task_router=Services.task_router(),
    )
    ok = analyzer.analyze_file(
        section_file,
        "section-01",
        "src/missing.py",
        ScanContext(
            codespace=tmp_path / "codespace",
            codemap_path=tmp_path / "codemap.md",
            corrections_path=tmp_path / "corrections.json",
            scan_log_dir=scan_log_dir,
            model_policy={"deep_analysis": "glm"},
        ),
        FileCardCache(tmp_path / "file-cards", hasher=Services.hasher(), artifact_io=Services.artifact_io()),
    )

    assert ok is False
    assert "source file missing in codespace" in (
        scan_log_dir / "failures.log"
    ).read_text(encoding="utf-8")


def test_analyze_file_uses_cached_response_and_feedback(
    tmp_path,
    monkeypatch,
) -> None:
    section_file = tmp_path / "section-01.md"
    section_file.write_text(
        "# Section\n\n## Related Files\n\n### src/main.py\n",
        encoding="utf-8",
    )
    codespace = tmp_path / "codespace"
    (codespace / "src").mkdir(parents=True)
    source_path = codespace / "src" / "main.py"
    source_path.write_text("print('ok')\n", encoding="utf-8")
    cache = FileCardCache(tmp_path / "file-cards", hasher=Services.hasher(), artifact_io=Services.artifact_io())
    response = tmp_path / "cached-response.md"
    response.write_text("cached response", encoding="utf-8")
    feedback = tmp_path / "cached-feedback.json"
    feedback.write_text(
        json.dumps({"relevant": True, "source_file": "src/main.py"}),
        encoding="utf-8",
    )
    content_key = cache.content_hash(section_file, source_path, tmp_path / "missing.json")
    cache.store(content_key, response, feedback)

    calls: list[tuple[str, str]] = []

    class _SpyUpdater(MatchUpdater):
        def update_match(self, section_path, source_file, response_path):
            calls.append((str(section_path), source_file))
            assert response_path.read_text(encoding="utf-8") == "cached response"
            return True

    monkeypatch.setattr(
        "src.scan.explore.analyzer.dispatch_agent",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("dispatch_agent should not run on cache hit"),
        ),
    )

    analyzer = Analyzer(
        prompt_guard=Services.prompt_guard(),
        task_router=Services.task_router(),
        match_updater=_SpyUpdater(artifact_io=Services.artifact_io()),
    )
    ok = analyzer.analyze_file(
        section_file,
        "section-01",
        "src/main.py",
        ScanContext(
            codespace=codespace,
            codemap_path=tmp_path / "codemap.md",
            corrections_path=tmp_path / "missing.json",
            scan_log_dir=tmp_path / "scan-logs",
            model_policy={"deep_analysis": "glm"},
        ),
        cache,
    )

    assert ok is True
    assert calls == [(str(section_file), "src/main.py")]


def test_analyze_file_dispatches_and_caches_response(
    tmp_path,
    monkeypatch,
) -> None:
    section_file = tmp_path / "section-01.md"
    section_file.write_text(
        "# Section\n\n## Related Files\n\n### src/main.py\n",
        encoding="utf-8",
    )
    codespace = tmp_path / "codespace"
    (codespace / "src").mkdir(parents=True)
    (codespace / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")
    cache = FileCardCache(tmp_path / "file-cards", hasher=Services.hasher(), artifact_io=Services.artifact_io())
    scan_log_dir = tmp_path / "scan-logs"

    monkeypatch.setattr(
        "src.scan.explore.analyzer.load_scan_template",
        lambda _name: "{section_file}\n{abs_source}\n{feedback_file}",
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
        kwargs["stdout_file"].write_text("fresh response", encoding="utf-8")
        feedback_path = kwargs["prompt_file"].parent / kwargs["stdout_file"].name.replace(
            "-response.md",
            "-feedback.json",
        )
        feedback_path.write_text(
            json.dumps({"relevant": True, "source_file": "src/main.py"}),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(
        "src.scan.explore.analyzer.dispatch_agent",
        fake_dispatch,
    )

    # Use a no-op match updater
    match_updater = MatchUpdater(artifact_io=Services.artifact_io())

    try:
        analyzer = Analyzer(
            prompt_guard=_NoopGuard(),
            task_router=Services.task_router(),
            match_updater=match_updater,
        )
        ok = analyzer.analyze_file(
            section_file,
            "section-01",
            "src/main.py",
            ScanContext(
                codespace=codespace,
                codemap_path=tmp_path / "codemap.md",
                corrections_path=tmp_path / "corrections.json",
                scan_log_dir=scan_log_dir,
                model_policy={"deep_analysis": "glm"},
            ),
            cache,
        )

        assert ok is True
    finally:
        Services.prompt_guard.reset_override()
    assert cache.get(
        cache.content_hash(
            section_file,
            codespace / "src" / "main.py",
            tmp_path / "corrections.json",
        ),
    ) is not None
