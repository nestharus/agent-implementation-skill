"""Component tests for prompt template loading and rendering."""

from __future__ import annotations

from src.pipeline.template import (
    SYSTEM_CONSTRAINTS,
    TASK_SUBMISSION_SEMANTICS,
    load_template,
    render,
    render_template,
)


def test_render_template_wraps_dynamic_content_with_constraints() -> None:
    result = render_template("demo-task", "Body content.")

    assert SYSTEM_CONSTRAINTS in result
    assert "## Task: demo-task" in result
    assert "Body content." in result
    assert "## Constraint Reminder" in result
    assert result.index(SYSTEM_CONSTRAINTS) < result.index("Body content.")
    assert result.index("Body content.") < result.index("## Constraint Reminder")


def test_render_template_includes_file_scope_when_paths_present() -> None:
    result = render_template(
        "demo-task",
        "Body content.",
        file_paths=["src/app.py", "tests/test_app.py"],
    )

    assert "## Permitted File Scope" in result
    assert "- `src/app.py`" in result
    assert "- `tests/test_app.py`" in result


def test_render_template_omits_file_scope_for_none_or_empty() -> None:
    assert "## Permitted File Scope" not in render_template("demo", "Body")
    assert (
        "## Permitted File Scope"
        not in render_template("demo", "Body", file_paths=[])
    )


def test_render_substitutes_context_and_defaults_missing_keys() -> None:
    assert render("Hello {name}", {"name": "world"}) == "Hello world"
    assert render("Missing {value}", {}) == "Missing "


def test_load_template_reads_from_injected_directory(tmp_path) -> None:
    template_dir = tmp_path / "templates"
    template_dir.mkdir()
    (template_dir / "example.md").write_text("Hello, {name}.\n", encoding="utf-8")

    assert load_template("example.md", template_dir=template_dir) == "Hello, {name}.\n"


def test_load_template_reads_utf8_content(tmp_path) -> None:
    template_dir = tmp_path / "templates"
    template_dir.mkdir()
    (template_dir / "utf8.md").write_text("café\n", encoding="utf-8")

    assert load_template("utf8.md", template_dir=template_dir) == "café\n"


def test_exported_constants_remain_available() -> None:
    assert "NEEDS_PARENT" in SYSTEM_CONSTRAINTS
    assert "dispatcher handles agent selection and model choice" in (
        TASK_SUBMISSION_SEMANTICS.lower()
    )
