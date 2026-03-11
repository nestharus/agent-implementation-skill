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

---

## IDENTIFIED — NOT YET STARTED

### 2. `section_dispatch.py` is a frankenstein of concerns
- **Where**: `src/dispatch/engine/section_dispatch.py`
- **Problem**: `dispatch_agent()` does 7 unrelated things: pause checking, context sidecar materialization, monitor startup/shutdown, QA interception, actual dispatch, output writing, metadata writing. Also contains `adjudicate_agent_output()`, `create_signal_template()`, `read_model_policy()` — none of which are "section dispatch."
- **Fix**: Split into focused middleware (pause gate, monitor lifecycle, QA gate, dispatch core)

### 3. No typed domain objects — raw dicts everywhere
- **Where**: Throughout the codebase
- **Problem**: `build_flow_context` returns `dict | None`. `coord_plan` is `dict[str, Any]`. Problems are `list[dict[str, Any]]`. Everything is untyped bags of strings and dicts. No `FlowContext`, `CoordinationPlan`, `Problem` dataclasses.
- **Key files**: `src/flow/repository/context.py`, `src/coordination/engine/executor.py`, `src/coordination/engine/runner.py`
- **Fix**: Introduce domain dataclasses. Start with FlowContext (most contained).

### 5. Prompts inlined as f-strings in Python code
- **Where**: `src/coordination/engine/execution.py` (60-line prompt), `src/dispatch/engine/section_dispatch.py` (50-line monitor prompt), `src/coordination/engine/executor.py` (bridge prompt)
- **Problem**: Prompt templates embedded as massive f-strings. Can't be reviewed, tested, or versioned independently. Mixing content with logic.
- **Fix**: Extract to template files (e.g., `src/<system>/prompts/`), load at runtime via a template loader

### 6. `write_dispatch_prompt` is string concatenation, not structured
- **Where**: `src/flow/repository/context.py:88`
- **Problem**: Reads an original prompt file as raw text, prepends a `<flow-context>` header (also raw text), writes a new file. No typed FlowContext, no structured prompt object, no path abstraction. `flow_context_path` is a relative path string baked into prompt text.
- **Fix**: Create a proper PromptBuilder or FlowContext type

### 11. `intent` and `risk` systems missing `routes.py`
- **Where**: `src/intent/`, `src/risk/`
- **Problem**: These systems have agents (8 for intent, 3 for risk) dispatched via hardcoded `agent_file=` strings, bypassing the TaskRouter policy system entirely. No model policy override possible.
- **Fix**: Create `intent/routes.py` and `risk/routes.py`, register direct-dispatch agents as routes

### 12. Cross-system agent dispatch via hardcoded strings
- **Where**: 19 agents dispatched via `agent_file="foo.md"` scattered across ~15 files
- **Problem**: No routing, no policy override, no observability. The `agent_file` param is just a raw string. The whole point of the taskrouter system is to replace this pattern.
- **Fix**: After routes.py exists for all systems, migrate direct dispatch to use `taskrouter.registry.resolve()`

### 13. Old centralized `TASK_ROUTES` dict still exists
- **Where**: `src/flow/types/routing.py`
- **Problem**: The old flat routing table (`resolve_task()`, `submit_task()`) still exists and is still used by `src/flow/engine/dispatcher.py`. It duplicates the new `taskrouter` system.
- **Fix**: Migrate `dispatcher.py` to use `taskrouter.registry.resolve()`, delete `TASK_ROUTES` dict

### 14. `dispatch/prompt/writers.py` has hardcoded `allowed_tasks` strings
- **Where**: `src/dispatch/prompt/writers.py` lines ~176 and ~346
- **Problem**: Hardcoded task name lists like `"scan_explore, impact_analysis, integration_proposal, research_plan"` baked into prompt templates. Uses old flat names, not qualified names.
- **Fix**: Generate from `taskrouter.registry.allowed_tasks_for()` with qualified names

### 15. `context_sidecar.py` dumps ALL task routes as JSON
- **Where**: `src/dispatch/service/context_sidecar.py`
- **Problem**: `_resolve_allowed_tasks()` dumps all `TASK_ROUTES` keys. Should use scoped allowed_tasks from taskrouter.
- **Fix**: Replace with `taskrouter.registry.allowed_tasks_for(scope)`
