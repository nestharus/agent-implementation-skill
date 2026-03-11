# Cleanup Backlog

Tracked issues discovered during the Phase B architectural reorganization.
Each item describes a structural mess, why it's a problem, and where it lives.

---

## DONE

### 1. Agent files in shared `src/agents/` instead of per-system ownership
- **Status**: DONE — 53 agents in `src/<system>/agents/`, resolver in `taskrouter/agents.py`

### 8. 7 redundant WORKFLOW_HOME definitions
- **Status**: DONE — removed dead definitions from 5 files, removed dead `workflow_home` param from MonitorService, removed dead `agent_executor.WORKFLOW_HOME` assignment. Only 2 live uses remain: `communication.py` and `section-loop.py` (both derive DB_SH).

### 9. `artifacts/flows/` runtime state was committed to git
- **Status**: DONE — added to `.gitignore`, untracked

### 10. `.tmp/` scratch files were committed to git
- **Status**: DONE — added to `.gitignore`, untracked

### 16. Backwards-compat try/except import fallbacks
- **Status**: DONE — removed from `impact_analyzer.py` (3 blocks) and `context_sidecar.py` (1 block). No compat fallbacks remain in `src/`.

### 17. `scan/cli_dispatch.py` stale WORKFLOW_HOME comment
- **Status**: DONE — removed stale `# WORKFLOW_HOME` comment from `cli_dispatch.py`

### 4. `FlowCorruptionError` in wrong module
- **Status**: DONE — moved to `flow/exceptions.py`, updated all 6 import sites

### 7. `execution.py` / `executor.py` / `runner.py` naming confusion
- **Status**: DONE — renamed to `fix_dispatch.py`, `plan_executor.py`, `global_coordinator.py`

### 5. Prompts inlined as f-strings in Python code
- **Status**: DONE — extracted 3 prompts to `src/templates/` (coordinator-fix, agent-monitor, bridge-resolve), loaded via `load_template()` + `render()`

### 11. `intent` and `risk` systems missing `routes.py`
- **Status**: DONE — created `intent/routes.py` (10 routes) and `risk/routes.py` (3 routes), registered in `taskrouter/discovery.py`

### 13. Old centralized `TASK_ROUTES` dict still exists
- **Status**: DONE — deleted `TASK_ROUTES` dict and `resolve_task()` from `flow/types/routing.py`. Migrated `dispatcher.py` to `taskrouter.registry.resolve()`. Added `ensure_discovered()` lazy init. `submit_task()` kept as pure DB helper.

### 14. `dispatch/prompt/writers.py` has hardcoded `allowed_tasks` strings
- **Status**: DONE — updated hardcoded task lists to use qualified names (`scan.explore`, `signals.impact_analysis`, etc.). Agent prompts now reference qualified task types matching `taskrouter.registry`.

### 15. `context_sidecar.py` dumps ALL task routes as JSON
- **Status**: DONE — `_resolve_allowed_tasks()` now uses `taskrouter.registry.all_task_types` instead of `TASK_ROUTES.keys()`. Also migrated `flow/types/schema.py` validation to use registry.

### 12. Cross-system agent dispatch via hardcoded strings
- **Status**: DONE — added 7 missing routes (coordination.plan, coordination.bridge, dispatch.bridge_tools, dispatch.qa_intercept, implementation.microstrategy, implementation.reexplore, signals.impact_normalize). Added `agent_for()` helper to taskrouter. Migrated 61 dispatch calls across 30 files from `agent_file="foo.md"` to `agent_file=agent_for("task.type")`. Only legacy `section-loop.py` retains hardcoded strings.

### 2. `section_dispatch.py` is a frankenstein of concerns
- **Status**: DONE — extracted `adjudicate_agent_output` to `dispatch/service/output_adjudicator.py`, `create_signal_template` to `signals/repository/signal_template.py`. Removed `read_model_policy` wrapper (callers now import `load_model_policy` directly). Eliminated 5 re-exports (`check_agent_signals`, `summarize_output`, `write_model_choice_signal`, `read_agent_signal`, `read_signal_tuple`) — updated 18 callers to import from source modules. Module now contains only `dispatch_agent` + private helpers.

---

### 3. No typed domain objects — raw dicts everywhere
- **Status**: DONE (partial) — introduced `FlowContext` and `FlowTask` dataclasses in `src/flow/types/context.py`. `build_flow_context` now returns `FlowContext | None`. `write_flow_context` constructs `FlowContext` internally. All callers and tests updated to use attribute access. Remaining: `CoordinationPlan`, `Problem` dataclasses (separate future item).

### 6. `write_dispatch_prompt` is string concatenation, not structured
- **Status**: DONE — `write_dispatch_prompt` now uses `FlowContext` typed domain object. The prompt wrapper is structured via `PathRegistry.flows_dir()` for path management. Flow context is serialized through `FlowContext.to_dict()` / `FlowContext.from_dict()` round-trip.

