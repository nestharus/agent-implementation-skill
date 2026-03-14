"""Reusable prompt formatting helpers."""

from __future__ import annotations

from pathlib import Path

from containers import Services
from orchestrator.path_registry import PathRegistry
from pipeline.template import load_template, render


def signal_instructions(signal_path: Path) -> str:
    """Return signal instructions for an agent prompt."""
    template = load_template("dispatch/signal-instructions.md")
    return render(template, {"signal_path": signal_path})


def agent_mail_instructions(
    planspace: Path,
    agent_name: str,
    monitor_name: str,
) -> str:
    """Return narration-via-mailbox instructions for an agent."""
    run_db = PathRegistry(planspace).run_db()
    mailbox_cmd = (
        f'bash "{Services.config().db_sh}" send "{run_db}" '
        f"{agent_name} --from {agent_name}"
    )
    template = load_template("dispatch/mail-instructions.md")
    return render(template, {
        "agent_name": agent_name,
        "monitor_name": monitor_name,
        "mailbox_cmd": mailbox_cmd,
    })


def format_existing_file_listing(
    codespace: Path,
    rel_paths: set[str] | list[str],
    *,
    prefix: str = "   - ",
    empty_value: str = "   (none)",
) -> str:
    """Format existing related file paths as a prompt-ready block."""
    lines = [
        f"{prefix}`{codespace / rel_path}`"
        for rel_path in sorted(set(rel_paths))
        if (codespace / rel_path).exists()
    ]
    return "\n".join(lines) if lines else empty_value


def scoped_context_block(sidecar_path: Path | str | None) -> str:
    """Return the standard scoped-context appendix block."""
    if not sidecar_path:
        return ""
    return (
        f"\n## Scoped Context\n"
        f"Agent context sidecar with resolved inputs: "
        f"`{sidecar_path}`\n"
    )
