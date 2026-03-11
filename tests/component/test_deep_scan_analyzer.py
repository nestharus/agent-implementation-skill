from __future__ import annotations

import hashlib
import json
import subprocess

from src.scan.explore.analyzer import analyze_file, safe_name
from src.scan.codemap.cache import FileCardCache


def test_safe_name_matches_bash_compatible_scheme() -> None:
    source_file = "src/main.py"
    expected_hash = hashlib.sha1(source_file.encode()).hexdigest()[:10]  # noqa: S324

    assert safe_name(source_file) == f"src_main_py.py.{expected_hash}"


def test_analyze_file_returns_false_when_source_missing(tmp_path) -> None:
    section_file = tmp_path / "section-01.md"
    section_file.write_text("# Section\n", encoding="utf-8")
    scan_log_dir = tmp_path / "scan-logs"
    scan_log_dir.mkdir()

    ok = analyze_file(
        section_file,
        "section-01",
        "src/missing.py",
        tmp_path / "codespace",
        tmp_path / "codemap.md",
        tmp_path / "corrections.json",
        scan_log_dir,
        FileCardCache(tmp_path / "file-cards"),
        {"deep_analysis": "glm"},
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
    cache = FileCardCache(tmp_path / "file-cards")
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

    def fake_update_match(section_path, source_file, response_path):
        calls.append((str(section_path), source_file))
        assert response_path.read_text(encoding="utf-8") == "cached response"
        return True

    monkeypatch.setattr(
        "src.scan.explore.analyzer.update_match",
        fake_update_match,
    )
    monkeypatch.setattr(
        "src.scan.explore.analyzer.dispatch_agent",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("dispatch_agent should not run on cache hit"),
        ),
    )

    ok = analyze_file(
        section_file,
        "section-01",
        "src/main.py",
        codespace,
        tmp_path / "codemap.md",
        tmp_path / "missing.json",
        tmp_path / "scan-logs",
        cache,
        {"deep_analysis": "glm"},
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
    cache = FileCardCache(tmp_path / "file-cards")
    scan_log_dir = tmp_path / "scan-logs"

    monkeypatch.setattr(
        "src.scan.explore.analyzer.load_scan_template",
        lambda _name: "{section_file}\n{abs_source}\n{feedback_file}",
    )
    monkeypatch.setattr(
        "src.scan.explore.analyzer.validate_dynamic_content",
        lambda _prompt: [],
    )
    monkeypatch.setattr(
        "src.scan.explore.analyzer.update_match",
        lambda *_args, **_kwargs: True,
    )

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

    ok = analyze_file(
        section_file,
        "section-01",
        "src/main.py",
        codespace,
        tmp_path / "codemap.md",
        tmp_path / "corrections.json",
        scan_log_dir,
        cache,
        {"deep_analysis": "glm"},
    )

    assert ok is True
    assert cache.get(
        cache.content_hash(
            section_file,
            codespace / "src" / "main.py",
            tmp_path / "corrections.json",
        ),
    ) is not None
