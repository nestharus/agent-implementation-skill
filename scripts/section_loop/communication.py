import json
import os
import re
import subprocess
from pathlib import Path

WORKFLOW_HOME = Path(os.environ.get(
    "WORKFLOW_HOME",
    Path(__file__).resolve().parent.parent.parent,
))
DB_SH = WORKFLOW_HOME / "scripts" / "db.sh"
AGENT_NAME = "section-loop"


def log(msg: str) -> None:
    """Print a timestamped log message to stdout."""
    print(f"[section-loop] {msg}", flush=True)




def _summary_tag(message: str) -> str:
    """Extract a structured tag from a summary-worthy message.

    Maps message prefixes to queryable tags for db.sh log events::

      summary:proposal-align:03:PROBLEMS → proposal-align:03
      status:coordination:round-2        → coordination:round-2
      done:03:5 files modified           → done:03
      fail:03:error                      → fail:03
      complete                           → complete
      pause:underspec:03:detail          → underspec:03
    """
    parts = message.split(":")
    if message.startswith("summary:") and len(parts) >= 3:
        return f"{parts[1]}:{parts[2]}"
    if message.startswith("status:") and len(parts) >= 3:
        return f"{parts[1]}:{parts[2]}"
    if message.startswith(("done:", "fail:")) and len(parts) >= 2:
        return f"{parts[0]}:{parts[1]}"
    if message.startswith("pause:") and len(parts) >= 3:
        return f"{parts[1]}:{parts[2]}"
    if message == "complete":
        return "complete"
    return parts[0]


def mailbox_send(planspace: Path, target: str, message: str) -> None:
    """Send a message to a target mailbox.

    Summary-worthy messages (summary, done, complete, status, fail, pause
    prefixes) are also recorded as summary events in the database so that
    monitors can query them via ``db.sh tail``/``db.sh query``.
    """
    subprocess.run(  # noqa: S603
        ["bash", str(DB_SH), "send", str(planspace / "run.db"),  # noqa: S607
         target, "--from", AGENT_NAME, message],
        check=True,
        capture_output=True,
        text=True,
    )
    log(f"  mail → {target}: {message[:80]}")
    # Record summary event for messages that monitors track
    for prefix in ("summary:", "done:", "complete", "status:", "fail:",
                   "pause:"):
        if message.startswith(prefix):
            subprocess.run(  # noqa: S603
                ["bash", str(DB_SH), "log", str(planspace / "run.db"),  # noqa: S607
                 "summary", _summary_tag(message), message,
                 "--agent", AGENT_NAME],
                capture_output=True,
                text=True,
            )
            break


def mailbox_recv(planspace: Path, timeout: int = 0) -> str:
    """Block until a message arrives in our mailbox. Returns message text."""
    log(f"  mail ← waiting (timeout={timeout})...")
    result = subprocess.run(  # noqa: S603
        ["bash", str(DB_SH), "recv", str(planspace / "run.db"),  # noqa: S607
         AGENT_NAME, str(timeout)],
        capture_output=True,
        text=True,
    )
    msg = result.stdout.strip()
    if result.returncode != 0 or msg == "TIMEOUT":
        return "TIMEOUT"
    log(f"  mail ← received: {msg[:80]}")
    return msg


def mailbox_drain(planspace: Path) -> list[str]:
    """Read all pending messages without blocking."""
    result = subprocess.run(  # noqa: S603
        ["bash", str(DB_SH), "drain", str(planspace / "run.db"),  # noqa: S607
         AGENT_NAME],
        capture_output=True,
        text=True,
    )
    msgs = []
    # Split on line-delimited separator (matches db.sh's "---" on its
    # own line).  Using regex to avoid misparse when message body contains
    # the literal string "---".
    for chunk in re.split(r'\n---\n', result.stdout):
        chunk = chunk.strip()
        if chunk:
            msgs.append(chunk)
    return msgs


def mailbox_register(planspace: Path) -> None:
    """Register this agent for receiving messages."""
    subprocess.run(  # noqa: S603
        ["bash", str(DB_SH), "register", str(planspace / "run.db"),  # noqa: S607
         AGENT_NAME],
        check=True, capture_output=True, text=True,
    )


def mailbox_cleanup(planspace: Path) -> None:
    """Clean up and unregister this agent."""
    subprocess.run(  # noqa: S603
        ["bash", str(DB_SH), "cleanup", str(planspace / "run.db"),  # noqa: S607
         AGENT_NAME],
        capture_output=True, text=True,
    )
    subprocess.run(  # noqa: S603
        ["bash", str(DB_SH), "unregister", str(planspace / "run.db"),  # noqa: S607
         AGENT_NAME],
        capture_output=True, text=True,
    )


def _log_artifact(planspace: Path, name: str) -> None:
    """Log an artifact lifecycle event to the database."""
    subprocess.run(  # noqa: S603
        ["bash", str(DB_SH), "log", str(planspace / "run.db"),  # noqa: S607
         "lifecycle", f"artifact:{name}", "created",
         "--agent", AGENT_NAME],
        capture_output=True, text=True,
    )


def _record_traceability(
    planspace: Path,
    section: str,
    artifact: str,
    source: str,
    detail: str = "",
) -> None:
    """Append a traceability entry to artifacts/traceability.json.

    Records the provenance chain: section → excerpt → proposal →
    microstrategy → files/changes. Each entry captures what artifact
    was produced, from what source, and for which section.
    """
    trace_path = planspace / "artifacts" / "traceability.json"
    entries: list[dict] = []
    if trace_path.exists():
        try:
            entries = json.loads(trace_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError) as exc:
            # Preserve corrupted file for diagnosis
            import time
            corrupt_name = (
                f"traceability.corrupt-"
                f"{int(time.time())}.json"
            )
            corrupt_path = trace_path.parent / corrupt_name
            try:
                trace_path.rename(corrupt_path)
            except OSError:
                pass  # Best-effort preserve
            log(
                f"traceability.json malformed ({exc}) — "
                f"preserved as {corrupt_name}, starting fresh"
            )
            entries = []
    entries.append({
        "section": section,
        "artifact": artifact,
        "source": source,
        "detail": detail,
    })
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    trace_path.write_text(
        json.dumps(entries, indent=2) + "\n", encoding="utf-8",
    )
