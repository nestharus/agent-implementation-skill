"""Component tests for reusable prompt helper functions."""

from __future__ import annotations

from pathlib import Path

from src.dispatch.prompt.prompt_formatters import (
    agent_mail_instructions,
    format_existing_file_listing,
    scoped_context_block,
    signal_instructions,
)


def test_signal_instructions_contains_signal_path(tmp_path: Path) -> None:
    signal_path = tmp_path / "signal.json"

    instructions = signal_instructions(signal_path)

    assert str(signal_path) in instructions


def test_agent_mail_instructions_contains_db_send_command(tmp_path: Path) -> None:
    instructions = agent_mail_instructions(
        tmp_path,
        "impl-01",
        "impl-01-monitor",
    )

    assert "db.sh" in instructions
    assert "send" in instructions
    assert "impl-01-monitor" in instructions


def test_format_existing_file_listing_skips_missing_paths(tmp_path: Path) -> None:
    codespace = tmp_path / "codespace"
    codespace.mkdir()
    (codespace / "src").mkdir()
    (codespace / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")

    listing = format_existing_file_listing(
        codespace,
        {"src/main.py", "src/missing.py"},
    )

    assert listing == f"   - `{codespace / 'src/main.py'}`"


def test_scoped_context_block_formats_sidecar_reference() -> None:
    block = scoped_context_block("/tmp/context.json")

    assert "Scoped Context" in block
    assert "/tmp/context.json" in block
