# Refactoring Phase 6: Tier 7-9 Service Extractions

You are orchestrating the next phase of a bottom-up refactoring of the agent-implementation-skill codebase.

## Important: Parallel Work

You are working in parallel with other agents on this repository. Do NOT revert changes you do not recognize.

## Completed So Far

### lib/ modules (19 total, all wired):
- `artifact_io.py`, `hash_service.py`, `path_registry.py`, `verdict_parsers.py`
- `database_client.py`, `mailbox_service.py`, `monitor_service.py`
- `model_policy.py`, `signal_reader.py`, `dispatch_metadata.py`
- `context_sidecar.py`, `prompt_template.py`, `section_input_hasher.py`
- `alignment_change_tracker.py`, `pipeline_state.py`, `message_poller.py`
- `note_repository.py`, `excerpt_repository.py`

### Test counts: 982 passed

## Phase 6 Tasks

### Task 1: Extract DecisionRepository from decisions.py

Read `src/scripts/section_loop/decisions.py`. This is already a clean, self-contained module with `Decision` dataclass, `record_decision`, `load_decisions`, and helpers.

Create `src/scripts/lib/decision_repository.py`:
- Move `Decision` dataclass
- Move `record_decision(decisions_dir, decision)`
- Move `load_decisions(decisions_dir, section?) -> list[Decision]`
- Move any supporting helpers (`_format_prose_entry`, etc.)

Update `decisions.py` to re-export from the new module (thin layer).
Update any other files that import from `decisions.py`.

Write tests at `tests/component/test_decision_repository.py`.

### Task 2: Extract StrategicStateBuilder

Read `src/scripts/section_loop/decisions.py` for the `build_strategic_state` function (if it exists there or in `main.py`). Search for `strategic-state.json` references:

```bash
grep -rn "strategic.state" src/scripts/ --include="*.py"
```

Create `src/scripts/lib/strategic_state.py`:
- Move the strategic state building/writing logic

Write tests at `tests/component/test_strategic_state.py`.

### Task 3: Extract FreshnessService from task_flow.py

Read `src/scripts/task_flow.py`. Find the `compute_section_freshness` function.

Create `src/scripts/lib/freshness_service.py`:
- Move `compute_section_freshness(planspace, section_number) -> str`

Update `task_flow.py` to import from the new module.

Write tests at `tests/component/test_freshness_service.py`.

### Task 4: Extract DispatchService (AgentExecutor) from dispatch.py

Read `src/scripts/section_loop/dispatch.py`. The core `dispatch_agent` function handles:
1. Pipeline state check
2. Context sidecar materialization
3. Agent subprocess invocation
4. Monitor lifecycle
5. Signal reading
6. Metadata writing

The raw subprocess invocation (the `agents` binary call) is the AgentExecutor concern (Tier 1). Extract it.

Create `src/scripts/lib/agent_executor.py`:
- `run_agent(model, prompt_path, output_path, *, agent_file, codespace=None, timeout=600) -> AgentResult`
- `AgentResult` dataclass: `output: str, returncode: int, timed_out: bool`

This handles ONLY the subprocess call — no monitoring, no mailbox, no pipeline state.

Update `dispatch.py` to use `agent_executor.run_agent()` instead of inline subprocess.

Write tests at `tests/component/test_agent_executor.py`.

### Task 5: Extract ReconciliationQueueService

Read `src/scripts/section_loop/reconciliation_queue.py`.

Create `src/scripts/lib/reconciliation_queue.py`:
- Move the queue/load logic for reconciliation request files

Update the original file to re-export.

Write tests at `tests/component/test_reconciliation_queue.py`.

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

Write a summary to `.tmp/refactor/phase6-results.md`.
