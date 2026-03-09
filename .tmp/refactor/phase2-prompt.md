# Refactoring Phase 2: HashService Wiring + Tier 2-4 Component Extraction

You are orchestrating the next phase of a bottom-up refactoring of the agent-implementation-skill codebase.

## Important: Parallel Work

You are working in parallel with other agents on this repository. Do NOT revert changes you do not recognize — they are from other parallel workers.

## Completed So Far

### Tier 1 Components (created and wired):
- `src/scripts/lib/artifact_io.py` — JSON read/write with corruption preservation (WIRED into all files)
- `src/scripts/lib/hash_service.py` — Canonical SHA-256 hashing (created, NOT yet wired)
- `src/scripts/lib/path_registry.py` — Centralized artifact path construction (created, NOT yet wired)
- `src/scripts/lib/verdict_parsers.py` — Alignment verdict parsing (extracted from alignment.py)

## Phase 2 Tasks

### Task 1: Wire HashService into existing code

`src/scripts/lib/hash_service.py` provides:
- `file_hash(path) -> str` — SHA-256 of file contents, empty string if missing
- `content_hash(data: str | bytes) -> str` — SHA-256 of string/bytes
- `fingerprint(items: list[str]) -> str` — SHA-256 of sorted concatenated items

Find all `hashlib.sha256` usage in the codebase and replace with hash_service calls:

```bash
grep -rn "hashlib" src/scripts/ --include="*.py"
```

Key files likely to have hashlib usage:
- `src/scripts/section_loop/pipeline_control.py` — section input hash, coordination recheck hash
- `src/scripts/section_loop/change_detection.py` — file snapshot hashing
- `src/scripts/task_flow.py` — freshness computation
- `src/scripts/scan/fingerprint.py` — git fingerprinting

For each file:
1. Read it fully
2. Replace `hashlib.sha256(data).hexdigest()` with `content_hash(data)` or `file_hash(path)`
3. Add `from lib.hash_service import file_hash, content_hash, fingerprint` as needed
4. Remove `import hashlib` if no longer used
5. Run tests: `uv run pytest tests/ -q --tb=short`

### Task 2: Extract DatabaseClient (Tier 1, Component 4)

Read `src/scripts/section_loop/communication.py` and `src/scripts/section_loop/dispatch.py` to find all `subprocess.run(["bash", str(DB_SH), ...])` patterns.

Create `src/scripts/lib/database_client.py`:
```python
"""DatabaseClient: Wrapper around db.sh subprocess calls."""

import subprocess
from pathlib import Path


class DatabaseClient:
    def __init__(self, db_sh: Path, db_path: Path) -> None:
        self._db_sh = db_sh
        self._db_path = db_path

    def execute(self, command: str, *args: str) -> str:
        """Run a db.sh command and return stdout."""
        result = subprocess.run(
            ["bash", str(self._db_sh), command, str(self._db_path), *args],
            capture_output=True, text=True, check=True,
        )
        return result.stdout.strip()

    def query(self, table: str, **filters: str) -> str:
        """Query a table with optional filters."""
        args = [table]
        for k, v in filters.items():
            args.extend([f"--{k}", v])
        return self.execute("query", *args)

    def log_event(self, table: str, tag: str, **fields: str) -> str:
        """Log an event and return the event ID."""
        args = [table, tag]
        for k, v in fields.items():
            args.extend([f"--{k}", v])
        return self.execute("log", *args)
```

Study the actual db.sh interface first by reading `src/scripts/db.sh` to understand the exact command format, then adjust the implementation accordingly.

Write tests at `tests/component/test_database_client.py`. These will need real db.sh calls with a temp database.

### Task 3: Extract MailboxService (Tier 2, Component 7)

Read `src/scripts/section_loop/communication.py` to find mailbox operations (register, send, recv, cleanup).

Create `src/scripts/lib/mailbox_service.py` extracting all mailbox logic from communication.py.

Write tests at `tests/component/test_mailbox_service.py`.

Then update communication.py to import from the new module.

### Task 4: Extract MonitorService (Tier 2, Component 8)

Read `src/scripts/section_loop/dispatch.py` lines 67-173 for the monitor lifecycle code.

Create `src/scripts/lib/monitor_service.py` extracting monitor spawn/collect/shutdown.

Write tests at `tests/component/test_monitor_service.py`.

Then update dispatch.py to import from the new module.

## Rules

- Do NOT change any behavior — same inputs, same outputs, same error handling
- Test after EVERY file change: `uv run pytest tests/ -q --tb=short`
- Write component tests for every new module
- All tests must pass at every step

## Verification

After ALL tasks, run:
```bash
uv run pytest tests/ -q --tb=short
```

Write a summary of what was done to `.tmp/refactor/phase2-results.md`.
