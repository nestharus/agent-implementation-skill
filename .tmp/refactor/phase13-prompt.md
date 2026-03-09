# Refactoring Phase 13: Decompose section_engine/runner.py

You are orchestrating the decomposition of the largest file in the codebase: `src/scripts/section_loop/section_engine/runner.py` (1858 lines).

## Important: Parallel Work

You are working in parallel with other agents on this repository. Do NOT revert changes you do not recognize.

## Completed So Far

### lib/ modules (54 total, all wired)
### Test counts: 1111 passed

## Context

`run_section()` is a 1170-line function with 9 distinct phases:
1. Recurrence signal emission (lines ~182-197)
2. Incoming notes + impact triage (lines ~199-344)
3. Tool surface setup (lines ~346-366)
4. Section setup / excerpt extraction loop (lines ~368-463)
5. Problem frame quality gate (lines ~465-590)
6. Intent bootstrap + TODO extraction (lines ~592-707)
7. Integration proposal loop (lines ~708-1122)
8. Readiness gate + blocker routing (lines ~1123-1307)

`_run_section_implementation_steps()` is an 548-line function with:
9. Upstream freshness gate (lines ~1323-1342)
10. Microstrategy decision + generation (lines ~1344-1550)
11. Strategic implementation loop (lines ~1536-1810)
12. Post-completion (lines ~1810-end)

## Phase 13 Tasks

### Task 1: Extract RecurrenceEmitter

Read `src/scripts/section_loop/section_engine/runner.py` lines 182-197. This writes a recurrence signal when a section has been solved 2+ times.

Create `src/scripts/lib/recurrence_emitter.py`:
- `emit_recurrence_signal(planspace, section_number, solve_count)` — writes the recurrence JSON signal

Wire into runner.py (replace the inline code block with a function call).

Write tests at `tests/component/test_recurrence_emitter.py`.

### Task 2: Extract ExcerptExtractor (Section Setup)

Read runner.py lines 368-463 (the setup excerpt extraction loop). This is a self-contained loop that dispatches an agent to extract proposal/alignment excerpts.

Create `src/scripts/lib/excerpt_extractor.py`:
- `extract_excerpts(section, planspace, codespace, parent, policy) -> str | None`
  Returns None if alignment_changed or aborted. Returns "ok" on success.
  Contains the while loop, dispatch, signal checking, and pause_for_parent handling.

Wire into runner.py.

Write tests at `tests/component/test_excerpt_extractor.py`.

### Task 3: Extract ProposalLoopOrchestrator

Read runner.py lines 708-1122 (the integration proposal loop). This is the largest single phase — a while loop that dispatches proposal generation, runs alignment checks, handles escalation, processes feedback.

Create `src/scripts/lib/proposal_loop.py`:
- `run_proposal_loop(section, planspace, codespace, parent, policy, cycle_budget, incoming_notes) -> str | None`
  Returns None if alignment_changed/aborted, the problems string on alignment, or empty string on success.
  This is the entire Step 2 extracted as a service.

Wire into runner.py.

Write tests at `tests/component/test_proposal_loop.py`.

### Task 4: Extract ReadinessGate

Read runner.py lines 1123-1307 (readiness gate + blocker routing). This resolves readiness, publishes discoveries (scope deltas, research questions), routes blockers to signals/reconciliation queue, and handles the proposal-mode exit.

Create `src/scripts/lib/readiness_gate.py`:
- `resolve_and_route(section, planspace, parent, pass_mode) -> ReadinessResult`
  ReadinessResult dataclass with: ready, blockers, proposal_pass_result (for proposal mode)
- `publish_discoveries(section_number, proposal_state, planspace)` — writes scope deltas and research questions
- `route_blockers(section_number, proposal_state, planspace, parent)` — routes blockers to signals

Wire into runner.py.

Write tests at `tests/component/test_readiness_gate.py`.

### Task 5: Extract MicrostrategyOrchestrator

Read runner.py lines 1344-1550 (microstrategy decision + generation). This checks whether a microstrategy is needed, generates the prompt, dispatches the agent, and handles retry/escalation.

Create `src/scripts/lib/microstrategy_orchestrator.py`:
- `run_microstrategy(section, planspace, codespace, parent, policy) -> Path | None`
  Returns the microstrategy path if generated, None if not needed or failed.

Wire into runner.py.

Write tests at `tests/component/test_microstrategy_orchestrator.py`.

### Task 6: Extract ImplementationLoopOrchestrator

Read runner.py lines 1536-1810 (strategic implementation loop). This dispatches implementation, runs alignment checks, handles feedback and retry.

Create `src/scripts/lib/implementation_loop.py`:
- `run_implementation_loop(section, planspace, codespace, parent, policy, cycle_budget) -> list[str] | None`
  Returns modified files list on success, None on abort.

Wire into runner.py.

Write tests at `tests/component/test_implementation_loop.py`.

## Process for each extraction

1. Read the relevant lines in runner.py
2. Extract the complete phase into a new lib/ module as a single function
3. The function should take the same parameters the inline code uses
4. Write component tests
5. Replace the inline code in runner.py with a function call
6. Run `uv run pytest tests/ -q --tb=short` — must pass

## Rules

- Do NOT change any behavior
- Test after EVERY extraction
- runner.py should become a thin orchestrator that calls phase functions in sequence
- Each phase function should be self-contained — it imports what it needs
- Keep run_section as the entry point, but it should just be a sequence of phase calls

## Verification

After ALL tasks, run:
```bash
uv run pytest tests/ -q --tb=short
```

Write a summary to `.tmp/refactor/phase13-results.md` including the final line count of runner.py.
