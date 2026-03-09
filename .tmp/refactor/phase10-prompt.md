# Refactoring Phase 10: Scan Subsystem + Remaining Thin-Layer Conversions

You are orchestrating the next phase of a bottom-up refactoring of the agent-implementation-skill codebase.

## Important: Parallel Work

You are working in parallel with other agents on this repository. Do NOT revert changes you do not recognize.

## Completed So Far

### lib/ modules (39 total, all wired)
### Test counts: 1054 passed

## Phase 10 Tasks

### Task 1: Extract ScanTemplateLoader shared across scan modules

Read `src/scripts/scan/deep_scan.py`, `src/scripts/scan/exploration.py`, `src/scripts/scan/codemap.py`, `src/scripts/scan/feedback.py`. Each has its own `_load_template` function doing the same thing.

Create `src/scripts/lib/scan_template_loader.py`:
- `load_scan_template(name: str) -> str` — loads templates from the scan prompts directory

Wire into all 4 scan modules.

Write tests at `tests/component/test_scan_template_loader.py`.

### Task 2: Extract ScanPhaseLogger shared across scan modules

Same 4 files each have `_log_phase_failure`. Extract the shared concern.

Create `src/scripts/lib/scan_phase_logger.py`:
- `log_phase_failure(phase, section, error, planspace)` — shared failure logging

Wire into scan modules.

Write tests at `tests/component/test_scan_phase_logger.py`.

### Task 3: Extract IntentTriageService from intent/triage.py

Read `src/scripts/section_loop/intent/triage.py`. This module handles triage decisions for sections.

Create `src/scripts/lib/intent_triage.py`:
- Move the triage logic
- Move `load_triage_result`

Wire into `intent/triage.py` and any files importing from it.

Write tests at `tests/component/test_intent_triage.py`.

### Task 4: Extract ReadinessResolver from readiness.py

Read `src/scripts/section_loop/readiness.py`. This is a small file (86 lines) that resolves section readiness.

Create `src/scripts/lib/readiness_resolver.py`:
- Move `resolve_readiness` and any helpers

Wire into `readiness.py` and any files importing from it.

Write tests at `tests/component/test_readiness_resolver.py`.

### Task 5: Extract CommunicationConstants from communication.py

Read `src/scripts/section_loop/communication.py`. This module defines shared constants (`AGENT_NAME`, `DB_SH`, `WORKFLOW_HOME`, `DB_PATH`) and logging helpers that are imported by virtually every module.

Create `src/scripts/lib/communication.py`:
- Move `AGENT_NAME`, `DB_SH`, `WORKFLOW_HOME`, `DB_PATH` constants
- Move `log()`, `_log_artifact()`, `_record_traceability()` helpers
- Move `mailbox_send`, `mailbox_recv`, `mailbox_drain`, `mailbox_cleanup` if they haven't already been moved

Update the original `communication.py` to import and re-export from the new module.

Write tests at `tests/component/test_communication.py`.

## Process for each extraction

1. Read the source file(s) thoroughly
2. Identify the concern boundary
3. Create the new module in `src/scripts/lib/`
4. Write component tests in `tests/component/`
5. Update the original file to import from the new module
6. Update any other files that imported from the original
7. Run `uv run pytest tests/ -q --tb=short` — must pass at each step

## Rules

- Do NOT change any behavior
- Test after EVERY change
- Write component tests for every new module
- Do not create abstractions that are more complex than the code they replace

## Verification

After ALL tasks, run:
```bash
uv run pytest tests/ -q --tb=short
```

Write a summary to `.tmp/refactor/phase10-results.md`.
