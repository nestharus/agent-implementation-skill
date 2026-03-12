# Cleanup Backlog

Tracked structural messes, dead code, and architectural debt. Each item describes
what's wrong, why it's a problem, and where it lives.

---

## Mess Detection Methodology

How to find messes systematically. Apply these lenses in order.

### 1. Dead Code Detection
- **Fully migrated scripts**: Files in `scripts/` whose functions all exist in system packages. Check: zero imports from other files.
- **Orphaned agents**: Agent `.md` files with zero references across the codebase.
- **Dead modules**: `.py` files never imported by anything (exclude `__init__.py`, `__main__.py`, standalone scripts).
- **Empty directories**: Dirs containing only `__pycache__/` or no `.py` files.
- **One-time migration scripts**: Files in `.tmp/` from past reorganizations.

### 2. Naming & Identity (files must be nouns/components, not verbs/actions)
- **Files named like functions**: `bootstrap.py`, `loop.py`, `runner.py`, `implementation_pass.py` — these are verbs/actions, not components. A well-named file describes *what it is* (a thing), not *what it does* (an action). E.g., `bootstrap.py` → what component IS this? `intent_assembler.py`? `section_initializer.py`?
- **Modules that are really a single function**: `triage_orchestrator.py`, `microstrategy.py`, `snapshot.py`, `microstrategy_decision.py`, `reexplore.py`, `traceability.py` — if a file contains one function that does one thing, it's a function not a module. These belong *inside* a component that uses them, or are utilities that should be composed into the calling component.
- **Services that aren't services**: A service owns a domain concern and provides an API to it. Files like `context_sidecar.py`, `prompt_guard.py`, `tool_registry_manager.py`, `section_ingestion.py`, `task_flow.py`, `flow_signal_parser.py` are not services — they're functions/utilities placed in `service/` because there was nowhere else to put them.
- **Files in the wrong system**: ~~`dispatch/service/qa_interceptor.py`, `dispatch/qa-harness.sh`~~ → moved to `qa/` system (#58). Check for similar boundary violations in other systems.

### 3. `engine/` Folders Are Code Smells
The `engine/` subdirectory pattern is a dumping ground label for mixed concerns. Every `engine/` folder needs scrutiny:
- **What's really in there?** Usually: workflow orchestration (pipeline step sequencing), business logic, state management, error handling, traceability, logging — all inlined together.
- **What should be there?** If there are pipelines, they should be explicit pipeline definitions with clear step boundaries. If there's orchestration, it should be pure orchestration (call A, then B, then C) with no business logic of its own.
- **Signs of trouble**: Files named `loop.py`, `runner.py`, `pass.py` that contain hundreds of lines of mixed concerns.
- `engine/` is an Aspect-Oriented Programming label. Unless you're doing AOP (advice, pointcuts, weaving), the folder name is misleading. The actual architecture is routes → services (with pipelines/workflows composed from service calls).

### 4. God Functions With Inlined Concerns
The deepest mess: functions that inline multiple concerns so you can't tell what they do.

**Concern types that must be separated:**
- **Orchestration**: Pure step sequencing — call A, then B, then C. No if-statements, no business logic.
- **Business logic**: Domain decisions — if-blocks, loops, calculations. No orchestration calls.
- **Routing**: Dispatching to one of N handlers based on a condition. No logic beyond the dispatch.
- **Mapping**: Transforming data from one shape to another. Pure input→output.
- **Middleware/cross-cutting**: Traceability, logging, metrics, error wrapping. Should be decorators or wrapper layers, NOT inlined alongside business logic.

**Red flags in functions:**
- `_record_traceability(...)` calls alongside `log(...)` calls alongside business `if`-blocks = 3+ concerns inlined
- Checking `result["status"]` from one step and routing to blocking_state handlers = orchestration + routing + error handling mixed
- Reading/writing JSON budget files mid-function = persistence inlined with business logic
- `alignment_changed_pending()` checks sprinkled throughout = guard/middleware concern inlined

**Example**: `run_intent_bootstrap()` mixes: (1) path construction, (2) TODO extraction + file I/O, (3) traceability recording, (4) logging, (5) philosophy status routing, (6) blocker management, (7) parent pause signaling, (8) governance packet building, (9) budget assembly. That's 9 concerns in one function.

**A clean function has exactly ONE concern.** Small functions serve as labels (you can read the name instead of the body) and enable targeted testing.

### 5. Structural Placement
- **Top-level folders that aren't systems**: Systems have `routes.py` + `agents/` + service/repository pattern. Folders like `scripts/`, `tools/`, `templates/` are not systems.
- **Dumping grounds**: Files that import from many different systems are usually tangled concerns.
- **Templates scattered**: Templates should live with their system or in a centralized registry, not both.

### 6. Partial Migration Detection
- Check the DONE section below for completed migrations. If a migration moved code from A to B, verify A was actually deleted.
- Search for functions defined in multiple places (`def same_name` in 2+ files).
- Search for boilerplate patterns repeated across files (e.g., DB connect + PRAGMA).

### 7. Architectural Layer Identification
Layers we've identified:
- **Controller layer**: `taskrouter/` — routing registry with `TaskRouter`, `TaskRoute`, `TaskRegistry`
- **Service layer**: `service/` dirs — business logic, validation, transformation
- **Repository layer**: `repository/` dirs — artifact I/O, persistence
- **Prompt layer**: `prompt/` dirs — LLM prompt construction and template rendering
- **Types layer**: `types.py` files — dataclasses, enums, value objects

Missing/problematic patterns:
- **~~Pipeline layer~~**: `engine/` dirs were labeled as pipelines but are actually dumping grounds for god functions mixing orchestration + business logic + middleware. Need to be decomposed into proper service calls.
- **Workflow/orchestration**: The system runs multi-step workflows (triage → philosophy → governance → intent). These need explicit workflow definitions, not inlined god functions.
- **Middleware/cross-cutting**: Traceability (`_record_traceability`), logging (`log`), blocker management (`_update_blocker_rollup`), alignment guards (`alignment_changed_pending`) are cross-cutting concerns currently inlined everywhere. Need decorator/wrapper patterns.
- **DB abstraction**: ~~Raw `sqlite3.connect()` + PRAGMA boilerplate~~ → DONE (`task_db()` context manager)
- **Path abstraction**: 69+ manual path constructions bypassing `PathRegistry`
- **Service factories**: ~~`_database_client`, `_mailbox` constructors~~ → DONE (classmethods)

### 8. System Health Indicators
- System has `routes.py` but it's never imported (dead routing)
- System has `agents/` with `.md` files that have zero references
- Files over 500 lines (god modules)
- Functions over 100 lines (god functions)
- Functions with 3+ distinct concerns inlined (god functions even if short)
- `# noqa` comments suppressing security/style warnings (hidden debt)

---

## OPEN

### 24. ~~`scripts/` folder — assess what remains after cleanup~~ → ASSESSED (no move)
- **Status**: Assessed. `db.sh` stays — it's the backbone of both signals AND flow systems (2 DB_SH path definitions, 26 test files, evals). Moving would break hardcoded `../scripts/db.sh` paths everywhere. `log_extract/` is a standalone CLI tool (`logex` entry point), zero imports from other systems. Could move to `tools/` but not urgent — it's correctly placed as an operational script. `workflow.sh` moved here from orchestrator/ (item #39).

### 28. ~~`intent/service/philosophy.py` is a 2,005-line god module~~ → DONE
- **Status**: DONE — split into 4 modules: `philosophy_classifier.py` (306 lines, signal classifiers), `philosophy_catalog.py` (138 lines, file scanning), `philosophy_dispatch.py` (107 lines, agent dispatch with retry), `philosophy_bootstrap.py` (1,543 lines, orchestration). Original `philosophy.py` is now a thin re-export facade. `loop_bootstrap.py` updated to monkey-patch both facade and implementation modules.

### 33. ~~Manual path construction bypassing PathRegistry (69+ sites)~~ → DONE
- **Status**: DONE — Added ~35 new PathRegistry accessors. Migrated all 211+ manual `artifacts / "..."` constructions across 19+ files. Zero bypass sites remain outside `path_registry.py`. All 1550 tests pass.

### 34. ~~`dispatch/prompt/writers.py` — 5 copy-pasted prompt writers~~ → DONE
- **Status**: DONE — extracted `_write_prompt()` helper encapsulating PathRegistry setup, template loading, validation, writing, and logging. All 5 functions now delegate to it with per-function context builders.

### 36. ~~Silent failures throughout (broad exception catches)~~ → DONE
- **Status**: DONE — 4 of 5 sites already had `logger.warning(...)` logging (dispatcher.py, proposal_pass.py, reconciler.py, readiness_resolver.py). Fixed `qa_interceptor.py`: replaced `print()` with `logger.error(..., exc_info=True)`. All degraded paths now log before failing open.

### 39. ~~`orchestrator/` contains `workflow.sh`~~ → DONE
- **Status**: DONE — moved to `scripts/workflow.sh`. Markdown references updated.

### 42. ~~`_check_and_clear_alignment_changed` duplicated in 6 files~~ → DONE
- **Status**: DONE — extracted `make_alignment_checker(db_sh, agent_name)` factory to `staleness/service/change_tracker.py`. All 6 copies replaced with factory calls.

### 43. God modules (>500 lines) — tracking
- **Category**: God module / inlined concerns
- **Evidence** (current line counts after extraction):
  - `intent/service/philosophy.py` — 2,005 lines (see #28, split into 4 modules)
  - ~~`implementation/engine/implementation_pass.py` — 1,109 lines~~ → 591 lines (extracted ROAL index, risk artifacts, risk history)
  - ~~`risk/engine/loop.py` — 934 lines~~ → 433 lines (extracted prompt builders, response parser, posture hysteresis, fallback plans)
  - ~~`flow/engine/reconciler.py` — 683 lines~~ → 462 lines (extracted gate operations to repository)
  - ~~`proposal/engine/loop.py` — 610 lines~~ → 519 lines (extracted intent expansion service)
  - ~~`intent/engine/surface.py` — 606 lines~~ → 318 lines (extracted expanders to service)
  - `dispatch/service/tool_registry_manager.py` — 557 lines
  - ~~`dispatch/prompt/writers.py` — 507 lines~~ → DONE (#34)
- **Status**: All engine files under 600 lines. Remaining: `implementation_pass.py` (591), `dispatch/service/tool_registry_manager.py` (557), `proposal/engine/loop.py` (519) — all near the 500-line boundary. Only `tool_registry_manager.py` is outside engine/ and is a separate concern.

### 47. ~~Circular imports in `flow.*` modules~~ → DONE
- **Status**: DONE — removed all `sys.path` hacks and `_SCRIPTS_DIR` from `submitter.py`, `dispatcher.py`, `reconciler.py`, `section_ingestion.py`, `readiness_gate.py`. All E402 suppressions eliminated. 2 justified lazy imports remain: `qa_interceptor.py` (load-order), `runner.py` (documented circular with readiness_gate).

### 48. ~~`_database_client` factory duplicated in 4 files~~ → DONE
- **Status**: DONE — added `DatabaseClient.for_planspace(planspace, db_sh)` classmethod. Removed all 4 local `_database_client` functions.

### 49. ~~`_mailbox` factory duplicated in 3 files~~ → DONE
- **Status**: DONE — added `MailboxService.for_planspace(planspace, db_sh, agent_name)` classmethod. Removed 2 of 3 local `_mailbox` functions (communication.py retains a thin wrapper binding module-level constants).

### 50. ~~`_registry_for_artifacts` duplicated in 3 scan/substrate files~~ → DONE
- **Status**: DONE — canonical copy in `helpers.py`, `policy.py` imports from it, `runner.py` copy was dead code (removed).

### 51. ~~Risk utility duplication~~ → DONE
- **Status**: DONE — `scope_number`, `read_text` canonical in `package_builder.py`; `count_trailing_successes` canonical in `posture.py`. `loop.py` now imports from service modules.

### 52. ~~`_write_section_input_artifact` duplicated~~ → DONE
- **Status**: DONE — extracted to `orchestrator/repository/section_artifacts.py` as `write_section_input_artifact()`. Both callers updated.

### 53. ~~`_read_if_exists` duplicated~~ → DONE
- **Status**: DONE — extracted to `flow/helpers/file_utils.py` as `read_if_exists()`. Both callers updated.

### 54. ~~Log extractor duplication~~ → DONE
- **Status**: DONE — created `scripts/log_extract/extractors/common.py` with `safe_ts`, `safe_ts_from_record`, `events_from_home`, `session_candidates_from_home`. All 3 extractor files now delegate to common.

### 55. ~~Dispatch template split~~ — RESOLVED
- **Category**: Structural placement
- **Resolution**: All dispatch templates consolidated to `src/templates/dispatch/` (centralized), matching the scan precedent. `src/dispatch/templates/` removed. All callers updated to use `dispatch/` prefix with `SRC_TEMPLATE_DIR`.

### 56. `engine/` folders are dumping grounds for mixed concerns — DONE
- **Category**: Architectural (systemic)
- **Status**: DONE — All engine files decomposed to under 600 lines. Extracted concerns from 9 engine files:
  - `intent/engine/bootstrap.py`: decomposed 170-line god function into 6 Pipeline steps
  - `coordination/engine/loop.py`: extracted StallDetector, _assess_initial_state, _report_result
  - `implementation/engine/loop.py`: extracted change_verifier.py and trace_map.py services
  - `implementation/engine/implementation_pass.py`: extracted roal_index.py (repository), risk_artifacts.py and risk_history.py (services) — 1,109→591 lines
  - `proposal/engine/loop.py`: extracted intent_expansion.py service (605→519 lines)
  - `risk/engine/loop.py`: extracted prompt/builders.py, response_parser.py, posture_hysteresis.py, fallback.py — 905→433 lines
  - `intent/engine/surface.py`: extracted expanders.py service — 606→318 lines
  - `flow/engine/reconciler.py`: extracted gate_operations.py to repository (665→462 lines)
  - `proposal/engine/proposal_pass.py`: updated to use extracted roal_index

### 57. Files named as functions, not components — DONE
- **Category**: Naming/identity
- **Completed renames**: `todos.py`→`microstrategy_decision.py`, `task_ingestion.py`→`flow_signal_parser.py`, `prompt_safety.py`→`prompt_guard.py`, `impact_triage.py`→`triage_orchestrator.py`, `tool_surface.py`→`tool_registry_manager.py`. All import sites updated (48+ files).
- **Remaining single-function files** (`microstrategy.py`, `reexplore.py`, `snapshot.py`, `traceability.py`) are small, cohesive, and used by their callers — not worth absorbing. `context_assembler.py` (builder pattern), `traceability.py` (coherent domain) are acceptable as-is. Misplaced utilities (`section_ingestion.py`, `task_flow.py`, `context_sidecar.py`) are facade/convenience modules, not naming issues.

### 58. ~~QA system concerns misplaced in `dispatch/`~~ → DONE
- **Status**: DONE — Extracted `qa/` system: `qa/service/qa_interceptor.py`, `qa/agents/qa-interceptor.md`, `qa/qa-harness.sh`, `qa/routes.py`. Registered in `taskrouter/discovery.py`. Updated all import sites (2 src + 21 test patch targets). QA now depends on dispatch (cross-system import) but lives in its own system.

### 59. God functions with inlined concerns (systemic) — DONE
- **Category**: Concern separation (systemic)
- **Status**: DONE — Extracted 14 service/repository modules from engine files:
  - `coordination/service/stall_detector.py` — stall detection + model escalation
  - `implementation/service/change_verifier.py` — post-implementation file change verification
  - `implementation/service/trace_map.py` — trace-map building (problems → strategies → files)
  - `implementation/repository/roal_index.py` — ROAL input index CRUD operations
  - `implementation/service/risk_artifacts.py` — risk artifact writers + hint loaders
  - `implementation/service/risk_history.py` — risk history recording
  - `proposal/service/intent_expansion.py` — intent expansion cycle management
  - `intent/service/expanders.py` — problem/philosophy expander + recurrence adjudicator dispatchers
  - `risk/prompt/builders.py` — ROAL prompt construction
  - `risk/service/response_parser.py` — risk agent response parsing
  - `risk/service/posture_hysteresis.py` — posture hysteresis business logic
  - `risk/service/fallback.py` — fallback risk plan builders
  - `flow/repository/gate_operations.py` — gate/chain DB operations
  - AlignmentGuard middleware with `after_steps` mode for `alignment_changed_pending` checks
- **Remaining**: Traceability recording still inlined in implementation/engine/loop.py. Budget management pattern duplicated between proposal and implementation loops. These are minor and can be addressed opportunistically.

### 60. Missing workflow/pipeline engine — DONE
- **Category**: Missing abstraction
- **Status**: DONE — Built `src/pipeline/` package with `Pipeline`, `Step`, `PipelineContext`, `AlignmentGuard` (with `after_steps` mode), and `StepLogger` middleware. Refactored `intent/engine/bootstrap.py` from a 170-line god function to 6 single-concern step functions composed via `Pipeline`. 12 new tests, 1562 total pass. Loop-based workflows addressed via concern extraction into services (#59) rather than pipeline wrapping — correct approach for iterative loops vs. linear sequences.

---

## NOT A BUG

### 44. `ensure_global_philosophy` defined in 2 files
- `loop_bootstrap.py` is a dependency-injection wrapper around `philosophy.py`. Same pattern as `expansion.py`/`surface.py`.

### 45. `handle_user_gate`/`run_expansion_cycle` duplicated
- `expansion.py` wraps `surface.py` with dependency injection. All callers import from `expansion.py`. Same pattern as `loop_bootstrap.py`/`philosophy.py`.

---

## DONE

### 1. Agent files in shared `src/agents/` instead of per-system ownership
- **Status**: DONE — 53 agents in `src/<system>/agents/`, resolver in `taskrouter/agents.py`

### 2. `section_dispatch.py` is a frankenstein of concerns
- **Status**: DONE — extracted `adjudicate_agent_output` to `dispatch/service/output_adjudicator.py`, `create_signal_template` to `signals/repository/signal_template.py`. Removed `read_model_policy` wrapper (callers now import `load_model_policy` directly). Eliminated 5 re-exports — updated 18 callers to import from source modules. Module now contains only `dispatch_agent` + private helpers.

### 3. No typed domain objects — raw dicts everywhere
- **Status**: DONE (partial) — introduced `FlowContext` and `FlowTask` dataclasses. Remaining: `CoordinationPlan`, `Problem` dataclasses (separate future item).

### 4. `FlowCorruptionError` in wrong module
- **Status**: DONE — moved to `flow/exceptions.py`, updated all 6 import sites

### 5. Prompts inlined as f-strings in Python code
- **Status**: DONE — extracted 3 prompts to `src/templates/`, loaded via `load_template()` + `render()`

### 6. `write_dispatch_prompt` is string concatenation, not structured
- **Status**: DONE — uses `FlowContext` typed domain object, `PathRegistry.flows_dir()` for paths.

### 7. `execution.py` / `executor.py` / `runner.py` naming confusion
- **Status**: DONE — renamed to `fix_dispatch.py`, `plan_executor.py`, `global_coordinator.py`

### 8. 7 redundant WORKFLOW_HOME definitions
- **Status**: DONE — removed dead definitions. Only 2 live uses remain: `communication.py` and `section-loop.py`.

### 9. `artifacts/flows/` runtime state was committed to git
- **Status**: DONE — added to `.gitignore`, untracked

### 10. `.tmp/` scratch files were committed to git
- **Status**: DONE — added to `.gitignore`, untracked

### 11. `intent` and `risk` systems missing `routes.py`
- **Status**: DONE — created `intent/routes.py` (10 routes) and `risk/routes.py` (3 routes), registered in `taskrouter/discovery.py`

### 12. Cross-system agent dispatch via hardcoded strings
- **Status**: DONE — added 7 missing routes, `agent_for()` helper, migrated 61 dispatch calls across 30 files. Only legacy `section-loop.py` retains hardcoded strings.

### 13. Old centralized `TASK_ROUTES` dict still exists
- **Status**: DONE — deleted `TASK_ROUTES` dict and `resolve_task()` from `flow/types/routing.py`.

### 14. `dispatch/prompt/writers.py` has hardcoded `allowed_tasks` strings
- **Status**: DONE — updated to qualified names matching `taskrouter.registry`.

### 15. `context_sidecar.py` dumps ALL task routes as JSON
- **Status**: DONE — uses `taskrouter.registry.all_task_types`.

### 16. Backwards-compat try/except import fallbacks
- **Status**: DONE — removed from `impact_analyzer.py` and `context_sidecar.py`.

### 17. `scan/cli_dispatch.py` stale WORKFLOW_HOME comment
- **Status**: DONE — removed stale comment.

### 18. `scripts/section-loop.py` is dead code (4,605 lines)
- **Status**: DONE — deleted. All 67 functions duplicated in system packages. `orchestrator/engine/main.py` is the real entry point.

### 19. `scripts/run-section-loop.py` + `scripts/_pyc_loader.py` are dead
- **Status**: DONE — deleted both. Custom bytecode loader was unnecessary.

### 20. `scripts/agent_monitor.py` is dead
- **Status**: DONE — deleted. Replaced by `dispatch/service/monitor_service.py`.

### 21. `scripts/scan/` templates moved
- **Status**: DONE — moved 8 templates to `src/templates/scan/`, deleted `scripts/scan/`. Updated `scan/service/template_loader.py` path.

### 22. `scripts/substrate/` empty shell
- **Status**: DONE — deleted. Only contained `__pycache__/`.

### 23. `scripts/lib/` empty shell
- **Status**: DONE — deleted. Only contained a symlink.

### 25. `dispatch/agents/qa-monitor.md` orphaned
- **Status**: DONE — deleted. Zero references.

### 26. `agents/eval-judge.md` outside system pattern
- **Status**: DONE — deleted. Orphaned root-level agent file.

### 27. Dead `.py` modules (bulk)
- **Status**: DONE (partial) — deleted 5 confirmed dead: `risk/service/stack_eval.py`, `risk/service/value_scales.py`, `intake/service/verification.py`, `intake/session.py`, `signals/repository/signal_template.py`. Kept alive: `intent/engine/surface.py` (imported by expansion.py), `scan/codemap/fingerprint.py` (imported by lifecycle.py). Routes verified wired in `taskrouter/discovery.py`.

### 29. DB connection boilerplate repeated 15+ times
- **Status**: DONE — created `task_db()` context manager in `flow/service/task_db_client.py`. Migrated 13 of 15 sites across `reconciler.py` (7), `submitter.py` (2), `notifier.py` (1), `routing.py` (1). Also deleted duplicate `signals/db.sh`.

### 30. `flow/engine/reconciler.py` connection leak bug
- **Status**: DONE — consolidated `check_and_fire_gate()` from 3 separate DB connections to 1 with try/finally.

### 31. `risk/engine/loop.py` threshold parameter corruption bug
- **Status**: DONE — fixed 3 conditionals to write to their own keys (`step_thresholds`, `execution_thresholds`) instead of all writing to `class_thresholds`.

### 32. `proposal/engine/loop.py` function attribute state
- **Status**: DONE — replaced `run_proposal_loop._expansion_counts` with local dict. Fixed divergent gate handling in second loop copy.

### 35. `dispatcher.py` 4 pointless compatibility shims
- **Status**: DONE — removed `_db_cmd`, `_notify`, `_record_task_routing`, `_record_qa_intercept`. Updated all callers to use real functions.

### 37. `scan/templates/` placement
- **Status**: DONE — templates moved to `src/templates/scan/` (centralized pattern). `template_loader.py` updated.

### 38. `_posture_rank()` duplicated in 3 files
- **Status**: DONE — added `rank` property to `PostureProfile` enum in `risk/types.py`. Removed `_posture_rank()` from `risk/engine/loop.py`, `orchestrator/engine/strategic_state.py`, and `implementation/engine/implementation_pass.py`.

### 40. `_clamp_int`/`_clamp_float` duplicated in 3 risk files
- **Status**: DONE — centralized to `risk/types.py` as `clamp_int`/`clamp_float`. Removed from `risk/engine/loop.py`, `risk/service/quantifier.py`, `risk/repository/history.py`.

### 41. `intake/types.py` dead module (143 lines)
- **Status**: DONE — deleted. Zero imports.

### 46. `normalize_section_number` + `build_section_number_map` duplicated
- **Status**: DONE — removed copies from `implementation/service/impact_analyzer.py`, now imports from `orchestrator/service/section_decisions.py`.
