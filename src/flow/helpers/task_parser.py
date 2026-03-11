"""Parse dispatcher task rows emitted by ``db.sh next-task``."""

from __future__ import annotations


def parse_task_output(output: str) -> dict[str, str] | None:
    """Parse pipe-separated ``next-task`` output into a task dict."""
    output = output.strip()
    if output == "NO_RUNNABLE_TASKS":
        return None

    result: dict[str, str] = {}
    for part in output.split(" | "):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        result[key.strip()] = value.strip()
    return result if "id" in result else None
