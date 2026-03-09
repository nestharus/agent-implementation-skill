# Refactoring Phase 5: Pipeline Control Decomposition + Tier 7 Repositories

You are orchestrating the next phase of a bottom-up refactoring of the agent-implementation-skill codebase.

## Important: Parallel Work

You are working in parallel with other agents on this repository. Do NOT revert changes you do not recognize.

## Completed So Far

### lib/ components (all created and wired):
- `artifact_io.py` — JSON read/write ✓
- `hash_service.py` — Canonical hashing ✓
- `path_registry.py` — Path construction ✓ WIRED into 33 files
- `verdict_parsers.py` — Alignment verdict parsing ✓
- `database_client.py` — db.sh wrapper ✓
- `mailbox_service.py` — Mailbox lifecycle ✓
- `monitor_service.py` — Monitor lifecycle ✓
- `model_policy.py` — ModelPolicy dataclass ✓
- `signal_reader.py` — Signal file reading ✓
- `dispatch_metadata.py` — .meta.json sidecar ✓
- `context_sidecar.py` — Agent context materialization ✓
- `prompt_template.py` — Template loading/rendering ✓
- `section_input_hasher.py` — Section input hash computation ✓

### Test counts: 962 passed

## Phase 5 Tasks

### Task 1: Extract AlignmentChangeTracker from pipeline_control.py

Read `src/scripts/section_loop/pipeline_control.py`. Extract the alignment-changed flag management concern.

Create `src/scripts/lib/alignment_change_tracker.py`:
- `set_flag(planspace: Path)` — writes the flag file + logs lifecycle event
- `check_pending(planspace: Path) -> bool` — non-clearing check
- `check_and_clear(planspace: Path) -> bool` — atomic check+clear
- `invalidate_excerpts(planspace: Path)` — delete excerpt files

These functions currently use `DB_SH`, `AGENT_NAME`, and `PathRegistry`. They should accept these as parameters or import them.

Update `pipeline_control.py` to import from the new module.
Update any other files that import `_set_alignment_changed_flag`, `alignment_changed_pending`, or `_check_and_clear_alignment_changed` from `pipeline_control`.

Write tests at `tests/component/test_alignment_change_tracker.py`.

### Task 2: Extract PipelineStateService from pipeline_control.py

Create `src/scripts/lib/pipeline_state.py`:
- `check_pipeline_state(planspace: Path) -> str` — query lifecycle events
- `wait_if_paused(planspace, parent) -> None` — block during pause
- `pause_for_parent(planspace, parent, signal) -> str` — send signal and wait

These depend on mailbox operations and alignment change tracking. Import from the appropriate lib modules.

Update `pipeline_control.py` to import/re-export from the new module.

Write tests at `tests/component/test_pipeline_state.py`.

### Task 3: Extract MessagePoller from pipeline_control.py

Create `src/scripts/lib/message_poller.py`:
- `poll_control_messages(planspace, parent, current_section?) -> str | None`
- `check_for_messages(planspace) -> list[str]`
- `handle_pending_messages(planspace, queue, completed) -> bool`

These depend on mailbox drain and alignment change tracker.

Update `pipeline_control.py` to import/re-export from the new module.

Write tests at `tests/component/test_message_poller.py`.

### Task 4: Extract NoteRepository

Read `src/scripts/section_loop/cross_section.py` and `src/scripts/section_loop/coordination/problems.py` for how notes are read/written.

Create `src/scripts/lib/note_repository.py`:
- `read_incoming_notes(planspace, section_number) -> list[dict]` — read from-*-to-NN.md files
- `write_consequence_note(planspace, from_section, to_section, content)` — write a note file

Wire into the originating modules.

Write tests at `tests/component/test_note_repository.py`.

### Task 5: Extract ExcerptRepository

Read the excerpt-related operations scattered across:
- `src/scripts/section_loop/pipeline_control.py` — `_invalidate_excerpts`
- `src/scripts/section_loop/section_engine/runner.py` — excerpt reads/writes

Create `src/scripts/lib/excerpt_repository.py`:
- `write(planspace, section, excerpt_type, content)` — write excerpt file
- `read(planspace, section, excerpt_type) -> str | None` — read excerpt
- `exists(planspace, section, excerpt_type) -> bool` — check existence
- `invalidate_all(planspace)` — delete all excerpts

Wire into originating modules.

Write tests at `tests/component/test_excerpt_repository.py`.

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
- Keep pipeline_control.py as a thin re-export layer until callers are migrated
- Do not create abstractions that are more complex than the code they replace

## Verification

After ALL tasks, run:
```bash
uv run pytest tests/ -q --tb=short
```

Write a summary to `.tmp/refactor/phase5-results.md`.
