"""Queue-backed substrate dispatch wrapper."""

from __future__ import annotations

from pathlib import Path

from scan.scan_dispatcher import dispatch_agent


class SubstrateDispatcher:
    """Route substrate work through the task queue while preserving a bool API."""

    def dispatch_substrate_agent(
        self,
        model: str,
        prompt_path: Path,
        output_path: Path,
        codespace: Path | None = None,
        *,
        task_type: str,
        concern_scope: str | None = None,
    ) -> bool:
        """Submit a substrate task and mirror its stdout to *output_path*."""
        if not task_type:
            raise ValueError(
                "task_type is required — substrate work must be queue-routed"
            )

        result = dispatch_agent(
            task_type=task_type,
            model=model,
            project=codespace or prompt_path.parent,
            prompt_file=prompt_path,
            stdout_file=output_path,
            concern_scope=concern_scope,
            submitted_by=f"{task_type}.sync",
        )
        return result.returncode == 0
