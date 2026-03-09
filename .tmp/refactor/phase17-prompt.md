# Refactoring Phase 17: Final decomposition of runner.py and main.py

You are orchestrating the final decomposition of the two core orchestrator files.

## Important: Parallel Work

You are working in parallel with other agents on this repository. Do NOT revert changes you do not recognize. Do NOT touch files inside `substrate/`, `scan/`, or `task_dispatcher.py`.

## Completed So Far

### lib/ modules (79+ total, all wired)
### Test counts: 1189 passed

## Phase 17 Tasks

### Task 1: Extract ImpactTriageService from runner.py

Read `src/scripts/section_loop/section_engine/runner.py` lines 200-339. This is the impact triage block — builds prompt, dispatches triage agent, reads signal, handles acknowledgments, skip-to-alignment shortcut.

Create `src/scripts/lib/impact_triage.py`:
- `run_impact_triage(section, planspace, codespace, parent, policy, incoming_notes) -> str | None`
  Returns `"skip"` if triage says no rework needed and alignment passes, `"continue"` for normal processing, `None` if alignment changed. When `"skip"`, also return the modified files list.

  Actually better signature:
- `run_impact_triage(section, planspace, codespace, parent, policy, incoming_notes) -> tuple[str, list[str] | None]`
  Returns `("skip", modified_files)` or `("continue", None)` or `("abort", None)`.

Wire into runner.py (replace lines 200-339 with a function call).

Write tests at `tests/component/test_impact_triage.py`.

### Task 2: Extract ProblemFrameGate from runner.py

Read `src/scripts/section_loop/section_engine/runner.py` lines 370-493. This is the problem frame quality gate — retry setup, validate non-empty, hash tracking, traceability recording.

Create `src/scripts/lib/problem_frame_gate.py`:
- `validate_problem_frame(section, planspace, codespace, parent, policy) -> str | None`
  Returns `"ok"` if valid, `None` if blocked (needs_parent signal written). Handles retry and hash tracking.

Wire into runner.py.

Write tests at `tests/component/test_problem_frame_gate.py`.

### Task 3: Extract IntentBootstrap from runner.py

Read `src/scripts/section_loop/section_engine/runner.py` lines 496-618. This is the intent bootstrap block — triage, TODO extraction, philosophy check, intent pack generation, cycle budget merging.

Create `src/scripts/lib/intent_bootstrap.py`:
- `run_intent_bootstrap(section, planspace, codespace, parent, policy, incoming_notes) -> dict | None`
  Returns the cycle_budget dict on success, `None` if alignment changed or blocked.
  Handles: intent triage, TODO extraction, philosophy check, intent pack generation, budget assembly.

Wire into runner.py.

Write tests at `tests/component/test_intent_bootstrap.py`.

### Task 4: Extract GlobalAlignmentRecheck from main.py

Read `src/scripts/section_loop/main.py` lines 344-477. This is the Phase 2 global alignment recheck loop — per-section hash check, alignment dispatch, problem extraction.

Create `src/scripts/lib/global_alignment_recheck.py`:
- `run_global_alignment_recheck(sections_by_num, section_results, planspace, codespace, parent, policy) -> str`
  Returns `"all_aligned"`, `"has_problems"`, or `"restart_phase1"`.

Wire into main.py.

Write tests at `tests/component/test_global_alignment_recheck.py`.

### Task 5: Extract CoordinationLoop from main.py

Read `src/scripts/section_loop/main.py` lines 520-664. This is the adaptive coordination loop — runs coordination rounds, stall detection, escalation, completion/exhaustion reporting.

Create `src/scripts/lib/coordination_loop.py`:
- `run_coordination_loop(all_sections, section_results, sections_by_num, planspace, codespace, parent, policy) -> str`
  Returns `"complete"`, `"restart_phase1"`, `"exhausted"`, or `"stalled"`.
  Handles: stall counting, model escalation, outstanding problem checking, completion/exhaustion mailbox messages.

Wire into main.py.

Write tests at `tests/component/test_coordination_loop.py`.

### Task 6: Extract ReconciliationPhase from main.py

Read `src/scripts/section_loop/main.py` lines 164-300. This is the reconciliation handling + re-proposal pass — blocks affected sections, runs reproposal, recomputes ready/blocked.

Create `src/scripts/lib/reconciliation_phase.py`:
- `run_reconciliation_phase(proposal_results, sections_by_num, all_sections, planspace, codespace, parent, policy) -> tuple[list[str], list[str], bool]`
  Returns `(ready_sections, blocked_sections, restart_phase1)`.
  Handles: reconciliation blocking, re-proposal pass, alignment change handling.

Wire into main.py.

Write tests at `tests/component/test_reconciliation_phase.py`.

## Process for each extraction

1. Read the relevant code
2. Extract into a new lib/ module
3. Write component tests
4. Replace inline code with function call
5. Run `uv run pytest tests/ -q --tb=short` — must pass

## Rules

- Do NOT change any behavior
- Do NOT touch substrate/, scan/, or task_dispatcher.py
- Test after EVERY extraction
- runner.py should become a thin sequence of phase calls
- main.py should become a thin outer loop calling phase functions

## Verification

After ALL tasks, run:
```bash
uv run pytest tests/ -q --tb=short
```

Write a summary to `.tmp/refactor/phase17-results.md` including final line counts for runner.py and main.py.
