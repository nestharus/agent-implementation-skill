# Phase 18b: Remove backwards-compatibility wrapper modules

## Context

Phase 18 reorganized `src/scripts/lib/` from a flat directory into 13 domain subpackages. All 84 modules were moved to their correct subpackages. However, 84 backwards-compatibility wrapper files were left at the top level of `lib/`. Each wrapper looks like:

```python
from importlib import import_module as _import_module
import sys as _sys

_sys.modules[__name__] = _import_module(".core.artifact_io", __package__)
```

These shim files must be removed. This project has a strict NO BACKWARDS COMPATIBILITY policy. All import sites must be updated to use the new subpackage paths directly.

## The Mapping

Here is the complete mapping of old import paths to new import paths:

### core/
- `lib.artifact_io` → `lib.core.artifact_io`
- `lib.communication` → `lib.core.communication`
- `lib.database_client` → `lib.core.database_client`
- `lib.hash_service` → `lib.core.hash_service`
- `lib.model_policy` → `lib.core.model_policy`
- `lib.path_registry` → `lib.core.path_registry`
- `lib.pipeline_state` → `lib.core.pipeline_state`

### repositories/
- `lib.decision_repository` → `lib.repositories.decision_repository`
- `lib.excerpt_repository` → `lib.repositories.excerpt_repository`
- `lib.note_repository` → `lib.repositories.note_repository`
- `lib.proposal_state_repository` → `lib.repositories.proposal_state_repository`
- `lib.reconciliation_queue` → `lib.repositories.reconciliation_queue`
- `lib.reconciliation_result_repository` → `lib.repositories.reconciliation_result_repository`
- `lib.strategic_state` → `lib.repositories.strategic_state`

### services/
- `lib.alignment_service` → `lib.services.alignment_service`
- `lib.alignment_change_tracker` → `lib.services.alignment_change_tracker`
- `lib.freshness_service` → `lib.services.freshness_service`
- `lib.impact_analyzer` → `lib.services.impact_analyzer`
- `lib.readiness_resolver` → `lib.services.readiness_resolver`
- `lib.snapshot_service` → `lib.services.snapshot_service`
- `lib.section_input_hasher` → `lib.services.section_input_hasher`
- `lib.reconciliation_detectors` → `lib.services.reconciliation_detectors`
- `lib.scope_delta_parser` → `lib.services.scope_delta_parser`
- `lib.qa_verdict_parser` → `lib.services.qa_verdict_parser`
- `lib.verdict_parsers` → `lib.services.verdict_parsers`
- `lib.signal_reader` → `lib.services.signal_reader`

### dispatch/
- `lib.agent_executor` → `lib.dispatch.agent_executor`
- `lib.dispatch_helpers` → `lib.dispatch.dispatch_helpers`
- `lib.dispatch_metadata` → `lib.dispatch.dispatch_metadata`
- `lib.mailbox_service` → `lib.dispatch.mailbox_service`
- `lib.message_poller` → `lib.dispatch.message_poller`
- `lib.monitor_service` → `lib.dispatch.monitor_service`
- `lib.context_sidecar` → `lib.dispatch.context_sidecar`

### prompts/
- `lib.prompt_template` → `lib.prompts.prompt_template`
- `lib.prompt_helpers` → `lib.prompts.prompt_helpers`
- `lib.prompt_context_assembler` → `lib.prompts.prompt_context_assembler`
- `lib.substrate_prompt_builder` → `lib.prompts.substrate_prompt_builder`

### pipelines/
- `lib.proposal_loop` → `lib.pipelines.proposal_loop`
- `lib.proposal_pass` → `lib.pipelines.proposal_pass`
- `lib.implementation_loop` → `lib.pipelines.implementation_loop`
- `lib.implementation_pass` → `lib.pipelines.implementation_pass`
- `lib.coordination_loop` → `lib.pipelines.coordination_loop`
- `lib.coordination_executor` → `lib.pipelines.coordination_executor`
- `lib.coordination_planner` → `lib.pipelines.coordination_planner`
- `lib.coordination_problem_resolver` → `lib.pipelines.coordination_problem_resolver`
- `lib.reconciliation_phase` → `lib.pipelines.reconciliation_phase`
- `lib.reconciliation_adjudicator` → `lib.pipelines.reconciliation_adjudicator`
- `lib.global_alignment_recheck` → `lib.pipelines.global_alignment_recheck`
- `lib.scope_delta_aggregator` → `lib.pipelines.scope_delta_aggregator`
- `lib.impact_triage` → `lib.pipelines.impact_triage`
- `lib.problem_frame_gate` → `lib.pipelines.problem_frame_gate`
- `lib.excerpt_extractor` → `lib.pipelines.excerpt_extractor`
- `lib.microstrategy_orchestrator` → `lib.pipelines.microstrategy_orchestrator`
- `lib.recurrence_emitter` → `lib.pipelines.recurrence_emitter`
- `lib.readiness_gate` → `lib.pipelines.readiness_gate`

### intent/
- `lib.intent_bootstrap` → `lib.intent.intent_bootstrap`
- `lib.intent_surface` → `lib.intent.intent_surface`
- `lib.intent_triage` → `lib.intent.intent_triage`
- `lib.philosophy_bootstrap` → `lib.intent.philosophy_bootstrap`

### scan/
- `lib.scan_dispatch` → `lib.scan.scan_dispatch`
- `lib.scan_feedback_router` → `lib.scan.scan_feedback_router`
- `lib.scan_match_updater` → `lib.scan.scan_match_updater`
- `lib.scan_phase_logger` → `lib.scan.scan_phase_logger`
- `lib.scan_related_files` → `lib.scan.scan_related_files`
- `lib.scan_section_iterator` → `lib.scan.scan_section_iterator`
- `lib.scan_template_loader` → `lib.scan.scan_template_loader`
- `lib.deep_scan_analyzer` → `lib.scan.deep_scan_analyzer`
- `lib.tier_ranking` → `lib.scan.tier_ranking`

### substrate/
- `lib.substrate_dispatch` → `lib.substrate.substrate_dispatch`
- `lib.substrate_helpers` → `lib.substrate.substrate_helpers`
- `lib.substrate_policy` → `lib.substrate.substrate_policy`

### sections/
- `lib.section_loader` → `lib.sections.section_loader`
- `lib.section_notes` → `lib.sections.section_notes`
- `lib.section_decisions` → `lib.sections.section_decisions`
- `lib.project_mode` → `lib.sections.project_mode`

### tasks/
- `lib.task_db_client` → `lib.tasks.task_db_client`
- `lib.task_ingestion` → `lib.tasks.task_ingestion`
- `lib.task_notifier` → `lib.tasks.task_notifier`
- `lib.task_parser` → `lib.tasks.task_parser`

### flow/
- `lib.flow_context` → `lib.flow.flow_context`
- `lib.flow_reconciler` → `lib.flow.flow_reconciler`
- `lib.flow_submitter` → `lib.flow.flow_submitter`

### tools/
- `lib.tool_surface` → `lib.tools.tool_surface`
- `lib.log_extract_utils` → `lib.tools.log_extract_utils`

## Task

1. **Find every import** of the old `lib.<module>` paths across the entire codebase (`src/` and `tests/`). This includes:
   - `from lib.artifact_io import ...`
   - `import lib.artifact_io`
   - `"lib.artifact_io"` in string references (e.g., mock patch targets)

2. **Update every import** to use the new subpackage path per the mapping above.

3. **Delete all 84 wrapper files** from `src/scripts/lib/` (every `.py` file at the top level except `__init__.py`).

4. **Clean up `lib/__init__.py`** — it should be empty or just have the package marker. No re-exports needed.

5. **Run tests**: `uv run pytest tests/ -q --tb=short` — must pass with 1208+ tests.

## Important Notes

- Mock patch targets in tests use string paths like `"lib.artifact_io.some_function"`. These MUST be updated to the new paths or the mocks will silently stop working (tests pass but don't actually mock anything).
- Some lib modules import from other lib modules. Those internal imports were already updated in Phase 18 to use the subpackage paths. Verify they are correct.
- The orchestrator files (`section_loop/main.py`, `section_loop/section_engine/runner.py`, `coordination/runner.py`, `scan/deep_scan.py`, `substrate/runner.py`) import from lib — update those too.

## Verification

After ALL changes, run:
```bash
uv run pytest tests/ -q --tb=short
```

Must pass with 1208+ tests. Write results to `.tmp/refactor/phase18b-results.md`.
