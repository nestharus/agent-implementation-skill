# Refactoring Phase 16: Decompose prompts/writers.py, task_dispatcher.py, reconciliation.py

You are orchestrating the decomposition of three remaining large files.

## Important: Parallel Work

You are working in parallel with other agents on this repository. Do NOT revert changes you do not recognize. In particular, `src/scripts/substrate/` and `src/scripts/scan/` files may be modified by another agent — do NOT touch those directories.

## Completed So Far

### lib/ modules (66 total, all wired)
### Test counts: 1142 passed

## Phase 16 Tasks

### Task 1: Extract PromptContextBuilders from prompts/writers.py

Read `src/scripts/section_loop/prompts/writers.py`. There are 5 prompt writer functions, each ~100 lines, that follow the same pattern: build context, add prompt-specific keys, render template, write file.

The prompt-specific context assembly (lines building `problems_block`, `existing_note`, `notes_block`, decisions blocks, corrections refs, codemap refs, etc.) is mixed into each writer.

Create `src/scripts/lib/prompt_context_assembler.py`:
- `build_proposal_context_extras(section, planspace, alignment_problems, incoming_notes) -> dict` — assembles the problems_block, existing_note, notes_block for integration proposal prompts
- `build_impl_context_extras(section, planspace, alignment_problems) -> dict` — assembles the problems_block, decisions_block, corrections_ref, codemap_ref, todos_ref, tools_ref, tooling_block for implementation prompts

Wire into `prompts/writers.py`.

Write tests at `tests/component/test_prompt_context_assembler.py`.

### Task 2: Extract TaskDispatcherHelpers from task_dispatcher.py

Read `src/scripts/task_dispatcher.py`. It has:
- `_db` / `_db_cmd` — shell dispatch to db.sh
- `parse_next_task` — pipe-separated output parser
- `_read_dispatch_meta` — dispatch metadata reader
- `dispatch_task` — the main dispatch function (250+ lines)
- `_record_task_routing` / `_record_qa_intercept` — recording helpers
- `_notify` — mailbox notification
- `log` — simple logger
- `main` — CLI entry point

Create `src/scripts/lib/task_db_client.py`:
- `db_cmd(db_path, command, *args) -> str` — runs db.sh commands
- The `_db` helper can be inlined or removed if only used once

Create `src/scripts/lib/task_notifier.py`:
- `notify_task_result(db_path, submitted_by, task_id, task_type, status, detail)` — sends mailbox notification
- `record_task_routing(planspace, task_id, task_type, agent_file, model)` — records routing decisions
- `record_qa_intercept(planspace, task_id, task_type, rejection_reason)` — records QA intercepts

Wire into `task_dispatcher.py`.

Write tests at `tests/component/test_task_db_client.py` and `tests/component/test_task_notifier.py`.

### Task 3: Extract ReconciliationDispatch from reconciliation.py

Read `src/scripts/section_loop/reconciliation.py`. The file has:
- `_adjudicate_ungrouped_candidates` (lines 43-160) — dispatches adjudicator agent
- Thin wrappers around already-extracted lib detectors (lines 162-242)
- `load_reconciliation_result` / `was_section_affected` — result readers
- `run_reconciliation` (lines 286-444) — the main orchestrator

The thin wrappers (`_detect_anchor_overlaps`, `_detect_contract_conflicts`, `_consolidate_new_section_candidates`, `_aggregate_shared_seams`) already delegate to `lib/reconciliation_detectors.py`. They should be replaced with direct imports.

Create `src/scripts/lib/reconciliation_adjudicator.py`:
- `adjudicate_ungrouped_candidates(ungrouped, planspace, candidate_type) -> list[dict]` — dispatches adjudicator agent, parses JSON verdict

Wire into `reconciliation.py`. Also remove the thin wrapper functions and have `run_reconciliation` call the lib detectors directly.

Write tests at `tests/component/test_reconciliation_adjudicator.py`.

### Task 4: Extract CrossSectionHelpers from cross_section.py

Read `src/scripts/section_loop/cross_section.py` (393 lines). It has:
- `post_section_completion` (lines 28-210) — writes completion notes for other sections
- `read_incoming_notes` (lines 211-310) — reads incoming notes from completed sections
- `extract_section_summary` — extracts summary from section file
- `read_decisions` / `persist_decision` — decision file I/O
- `normalize_section_number` / `build_section_number_map` — section number helpers

Create `src/scripts/lib/section_notes.py`:
- `post_section_completion(...)` — completion note writer
- `read_incoming_notes(...)` — incoming note reader

Create `src/scripts/lib/section_decisions.py`:
- `read_decisions(planspace, section_number) -> str`
- `persist_decision(planspace, section_number, decision_text)`
- `extract_section_summary(section_path) -> str`
- `normalize_section_number(value) -> str`
- `build_section_number_map(sections) -> dict[int, str]`

Wire into `cross_section.py`.

Write tests at `tests/component/test_section_notes.py` and `tests/component/test_section_decisions.py`.

## Process for each extraction

1. Read the relevant code
2. Extract into a new lib/ module
3. Write component tests
4. Replace inline code with function call
5. Run `uv run pytest tests/ -q --tb=short` — must pass

## Rules

- Do NOT change any behavior
- Do NOT touch files inside `substrate/` or `scan/` (another agent is working there)
- Test after EVERY extraction
- Each source file should become a thin orchestrator

## Verification

After ALL tasks, run:
```bash
uv run pytest tests/ -q --tb=short
```

Write a summary to `.tmp/refactor/phase16-results.md` including final line counts.
