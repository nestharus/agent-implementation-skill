# Refactoring Phase 18: Organize lib/ into domain subpackages

You are reorganizing the flat `src/scripts/lib/` directory (85 modules) into logical subpackages. This is a pure move+re-import operation — no behavior changes.

## Target Structure

Move every module from `src/scripts/lib/` into one of the following subpackages. Create `__init__.py` in each subpackage that re-exports all public names so existing imports like `from lib.artifact_io import ...` continue to work via the top-level `lib/__init__.py`.

### `lib/core/` — Foundation services (IO, hashing, paths, DB, comms)
- `artifact_io.py`
- `hash_service.py`
- `path_registry.py`
- `database_client.py`
- `communication.py`
- `pipeline_state.py`
- `model_policy.py`

### `lib/repositories/` — Data access / persistence layers
- `decision_repository.py`
- `excerpt_repository.py`
- `note_repository.py`
- `proposal_state_repository.py`
- `reconciliation_queue.py`
- `reconciliation_result_repository.py`
- `strategic_state.py`

### `lib/services/` — Stateless business logic and analysis
- `alignment_service.py`
- `alignment_change_tracker.py`
- `freshness_service.py`
- `impact_analyzer.py`
- `readiness_resolver.py`
- `snapshot_service.py`
- `section_input_hasher.py`
- `reconciliation_detectors.py`
- `scope_delta_parser.py`
- `qa_verdict_parser.py`
- `verdict_parsers.py`
- `signal_reader.py`

### `lib/dispatch/` — Agent dispatch, monitoring, execution
- `agent_executor.py`
- `dispatch_helpers.py`
- `dispatch_metadata.py`
- `mailbox_service.py`
- `message_poller.py`
- `monitor_service.py`
- `context_sidecar.py`

### `lib/prompts/` — Prompt construction and templates
- `prompt_template.py`
- `prompt_helpers.py`
- `prompt_context_assembler.py`
- `substrate_prompt_builder.py`

### `lib/pipelines/` — Multi-step orchestration flows
- `proposal_loop.py`
- `proposal_pass.py`
- `implementation_loop.py`
- `implementation_pass.py`
- `coordination_loop.py`
- `coordination_executor.py`
- `coordination_planner.py`
- `coordination_problem_resolver.py`
- `reconciliation_phase.py`
- `reconciliation_adjudicator.py`
- `global_alignment_recheck.py`
- `scope_delta_aggregator.py`
- `impact_triage.py`
- `problem_frame_gate.py`
- `excerpt_extractor.py`
- `microstrategy_orchestrator.py`
- `recurrence_emitter.py`
- `readiness_gate.py`

### `lib/intent/` — Intent layer services
- `intent_bootstrap.py`
- `intent_surface.py`
- `intent_triage.py`
- `philosophy_bootstrap.py`

### `lib/scan/` — Scan subsystem services
- `scan_dispatch.py`
- `scan_feedback_router.py`
- `scan_match_updater.py`
- `scan_phase_logger.py`
- `scan_related_files.py`
- `scan_section_iterator.py`
- `scan_template_loader.py`
- `deep_scan_analyzer.py`
- `tier_ranking.py`

### `lib/substrate/` — SIS subsystem services
- `substrate_dispatch.py`
- `substrate_helpers.py`
- `substrate_policy.py`

### `lib/sections/` — Section management
- `section_loader.py`
- `section_notes.py`
- `section_decisions.py`
- `project_mode.py`

### `lib/tasks/` — Task queue infrastructure
- `task_db_client.py`
- `task_ingestion.py`
- `task_notifier.py`
- `task_parser.py`

### `lib/flow/` — Task flow context management
- `flow_context.py`
- `flow_reconciler.py`
- `flow_submitter.py`

### `lib/tools/` — Tool registry and utilities
- `tool_surface.py`
- `log_extract_utils.py`

## Implementation Strategy

### Step 1: Create subpackage directories

Create each subpackage directory with an `__init__.py`.

### Step 2: Move files

Use `git mv` to move each file to its target subpackage.

### Step 3: Update internal lib imports

Within `lib/` modules, update imports from sibling modules. For example, if `lib/pipelines/proposal_loop.py` imports from `lib.artifact_io`, update to `lib.core.artifact_io`.

### Step 4: Create re-export `__init__.py` for backwards compatibility

Update `src/scripts/lib/__init__.py` to re-export all public names from subpackages so that EXISTING imports like `from lib.artifact_io import read_json` continue to work:

```python
# Re-exports for backwards compatibility
from lib.core.artifact_io import *
from lib.core.hash_service import *
# ... etc for every module
```

This is CRITICAL — without this, every existing import in the codebase breaks.

### Step 5: Update component tests

Tests in `tests/component/` import from `lib.<module>`. These should continue to work via the re-exports, but verify by running tests.

### Step 6: Run tests

```bash
uv run pytest tests/ -q --tb=short
```

Must pass with 1208+ tests.

## Rules

- Do NOT change any behavior
- Use `git mv` for moves (preserves git history)
- The re-export `__init__.py` is mandatory — do not break existing imports
- Test after completing ALL moves
- If any import breaks, fix it before moving on

## Verification

After ALL moves, run:
```bash
uv run pytest tests/ -q --tb=short
```

Write a summary to `.tmp/refactor/phase18-results.md` including the final directory structure.
