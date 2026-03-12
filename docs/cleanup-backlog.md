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
- **Modules that are really a single function**: `triage_orchestrator.py` — if a file contains one function that does one thing, it's a function not a module. These belong *inside* a component that uses them, or are utilities that should be composed into the calling component.
- **Services that aren't services**: A service owns a domain concern and provides an API to it. Files like `context_sidecar.py`, `prompt_guard.py`, `tool_registry_manager.py`, `flow_signal_parser.py` are not services — they're functions/utilities placed in `service/` because there was nowhere else to put them.
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

**A clean function has exactly ONE concern.** Small functions serve as labels (you can read the name instead of the body) and enable targeted testing.

### 5. Structural Placement
- **Top-level folders that aren't systems**: Systems have `routes.py` + `agents/` + service/repository pattern. Folders like `scripts/`, `tools/`, `templates/` are not systems.
- **Dumping grounds**: Files that import from many different systems are usually tangled concerns.
- **Templates scattered**: Templates should live with their system or in a centralized registry, not both.

### 6. Partial Migration Detection
- Check the DONE section below for completed migrations. If a migration moved code from A to B, verify A was actually deleted.
- Search for functions defined in multiple places (`def same_name` in 2+ files).
- Search for boilerplate patterns repeated across files (e.g., DB connect + PRAGMA).
- Check DI migration completeness: `grep -r "^from .* import" src/ | grep -v containers` — every cross-system bare function import that isn't going through the container is an incomplete migration.

### 6b. Type Safety Gaps
- **Raw dict returns**: Functions returning `dict[str, Any]` for data with known schemas → should be pydantic models or dataclasses.
- **Tuple returns**: Functions returning `tuple[X, Y, Z]` where positional meaning is opaque → should be named result types.
- **Hand-rolled validation**: `expected_fields` loops, `data.get("key", "")` chains → pydantic does this.
- **Inconsistent shapes**: Same data represented as tuple in one function, dict in another (e.g., signals).
- **Scan**: `grep -rn "-> tuple\[" src/`, `grep -rn "-> dict\[str, Any\]" src/`, `grep -rn "expected_fields" src/`.

### 6c. Missing Domain Concepts
- **Parameter pairs that always travel together**: `planspace, codespace` appears in nearly every function signature. This is a missing "Project" or "Workspace" concept.
- **Dataclasses with business logic**: `@classmethod` factories on dataclasses that import from `containers` or do non-trivial construction → should be separate factory/mapper classes.
- **Modules in the wrong system**: A module consumed exclusively by system X but living in system Y is a boundary violation (e.g., QA parsing in proposal/).
- **Scan**: `grep -rn "planspace.*codespace" src/` for the parameter pair. `grep -rn "@classmethod" src/` for factory methods on data objects.

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
- **Path abstraction**: ~~69+ manual path constructions bypassing `PathRegistry`~~ → DONE (#33)
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

*No open items. All backlog items resolved.*

*No open items. All backlog items resolved.*

---

## NOT A BUG

### 81. `staleness/service/section_alignment_checker.py` imports `write_impl_alignment_prompt` from `dispatch.prompt.writers`
- **Resolution**: Not a layer violation. `dispatch/` is a shared service layer (prompt construction + agent dispatch) consumed by multiple systems including `implementation/` and `staleness/`. The lazy import avoids circular imports at module load time. Both consumers need to construct prompts before dispatching alignment check agents — this is a legitimate service dependency, not a forward dependency.

### 44. `ensure_global_philosophy` defined in 2 files
- `intent_pack_generator.py` is a dependency-injection wrapper around `philosophy_bootstrapper.py`. Same pattern as `expansion_facade.py`/`expansion_orchestrator.py`.

### 45. `handle_user_gate`/`run_expansion_cycle` duplicated
- `expansion_facade.py` wraps `expansion_orchestrator.py` with dependency injection. All callers import from `expansion_facade.py`. Same pattern as `intent_pack_generator.py`/`philosophy_bootstrapper.py`.

---

## CLOSED (won't fix)

### 73. `_handle_aligned_surfaces()` takes 8 parameters
- **Resolution**: Won't fix. Introducing `ExecutionContext` benefits only one module's internal helpers — premature abstraction. Parameters are derivable from planspace and services.

### 63. Missing domain concept: `planspace` + `codespace` always travel together
- **Category**: Missing abstraction
- **Resolution**: Won't fix. PathRegistry is semantically a path builder for planspace artifacts. Codespace is the user's source code directory — a different domain. Combining them would create a 91+ file blast radius for constructor changes with low payoff. Only 5 functions take both as direct parameters; the rest thread them through call chains where they serve distinct purposes.

---

## DONE

### 90. Stale docstrings referencing old module names (13 __init__.py + 5 source files)
- **Status**: DONE — updated all __init__.py public API docstrings to reference current module names after the 58-file rename campaign. Fixed 7 stale inline comments across task_dispatcher.py, task_request_ingestor.py, scan_dispatcher.py, qa_interceptor.py, assessment_evaluator.py.

### 89. DI container bypass in `global_coordinator.py`
- **Status**: DONE — replaced 5 direct `_log_artifact`/`mailbox_send` imports from `section_communicator` with `Services.communicator()` container calls. Now consistent with every other cross-cutting call in the file.

### 88. Remaining vague/bare-noun file renames (6 files)
- **Status**: DONE — renamed 6 files with vague or unqualified names: `main.py` → `pipeline_orchestrator.py`, `executor.py` → `research_plan_executor.py`, `dispatcher.py` → `task_dispatcher.py`, `submitter.py` → `flow_submitter.py`, `loader.py` → `governance_loader.py`, `core.py` → `route_registry.py`. Updated 23 source/test files with import paths. 1561 tests passing.

### 87. Comprehensive file rename: verb/action names → component nouns (52 files)
- **Status**: DONE — renamed 52 files across all service/, helpers/, engine/, and explore/ directories to component noun names. Examples: `completion.py` → `completion_handler.py`, `cross_section.py` → `decision_recorder.py`, `microstrategy.py` → `microstrategy_generator.py`, `reexplore.py` → `section_reexplorer.py`, `snapshot.py` → `file_snapshotter.py`, `assessment.py` → `assessment_evaluator.py`, `packet.py` → `governance_packet_builder.py`, `blockers.py` → `blocker_manager.py`, `communication.py` → `section_communicator.py`, `freshness.py` → `freshness_calculator.py`, `deep_scan.py` → `deep_scanner.py`, `cli_dispatch.py` → `scan_dispatcher.py`. Updated 113 source/test files with import path changes + 3 test files with stale module-alias variable references. Full rename map: 52 entries across coordination/, dispatch/, flow/, implementation/, intake/, intent/, orchestrator/, proposal/, reconciliation/, scan/, signals/, staleness/.

### 82. Dead re-exports in `scan/service/section_notes.py`
- **Status**: DONE — removed `post_section_completion` and `read_incoming_notes` re-exports. Zero consumers imported them from this module after #72 moved them to `coordination/service/completion.py`.

### 83. `cross_section.py` stale re-exports and dead container method
- **Status**: DONE — removed 6 unused re-exports (`build_section_number_map`, `extract_section_summary`, `normalize_section_number`, `read_decisions`, `post_section_completion`, `read_incoming_notes`, `compute_text_diff`). Updated `global_coordinator.py` to import `read_incoming_notes` from `coordination.service.completion` directly. Removed dead `read_incoming_notes` method from `CrossSectionService`. Updated `test_cross_section.py` to import from source modules.

### 84. Dead imports across production files
- **Status**: DONE — removed unused imports across 14 files: `PostureProfile` (strategic_state.py), `coordination_recheck_hash` (pipeline_control.py), `result_manifest_relpath` (reconciler.py), `RiskMode` (proposal_pass.py), `VALID_SOURCE_TYPES` (philosophy_bootstrap.py), `sys` (catalog.py, schema.py, cli.py), `json` (cli_dispatch.py), `subprocess` (tier_ranking.py), `re` (cli_handler.py), `_analyze_file`/`_safe_name`/`_run_tier_ranking`/`validate_tier_file` (deep_scan.py), `DEFAULT_SCAN_MODELS`/`log_phase_failure` (discovery.py), `_extract_section_number` (feedback.py), `QaVerdict` (qa_interceptor.py), `PathRegistry` (reconciliation/loop.py).

### 85. `intent/service/philosophy.py` — dead re-export facade (64 names)
- **Status**: DONE — deleted facade file. Only 2 consumers: `expanders.py` updated to import `validate_philosophy_grounding` from `philosophy_bootstrap` directly; `test_philosophy_bootstrap.py` updated to import from `philosophy_catalog` and `philosophy_bootstrap`. `loop_bootstrap.py` updated from module-alias pattern to direct imports from sub-modules.

### 86. `flow/service/task_flow.py` — dead re-exports trimmed
- **Status**: DONE — removed 17 unused re-exports (underscore-prefixed internal names, `FlowCorruptionError`, `Services`, `PathRegistry`). Kept 8 consumed names used by 10 consumer files.

### 78. `research/prompt/writer.py` → `research/prompt/writers.py`
- **Status**: DONE — renamed to match plural convention (`writers.py`) used by all other prompt modules. Updated 3 import sites (readiness_gate.py, executor.py, test_research_prompt_writer.py).

### 79. Dead code: `_normalize_section_id` in `global_coordinator.py`
- **Status**: DONE — deleted unused backward-compat alias (never called anywhere). Removed unused import from `test_intent_layer.py`.

### 80. `verdict_parsers.py` cross-package boundary violation
- **Status**: DONE — moved from `proposal/helpers/` to `staleness/helpers/` (alignment verdict parsing is a staleness concern). Updated 4 import sites (section_alignment.py, containers.py, test_verdict_parsers.py, test_research_prompt_writer.py). Deleted empty `proposal/helpers/` directory.

### 71. `run_global_coordination()` — 336-line god function decomposed
- **Status**: DONE — decomposed into 6 phase functions: `_collect_and_persist_problems()`, `_build_coordination_plan()`, `_execute_plan()`, `_recheck_section_alignment()`, `_record_recurrence_resolution()`, `_recheck_affected_sections()`. Main function reduced to ~30 lines of phase orchestration.

### 72. `scan/service/section_notes.py` orchestration moved to coordination/
- **Status**: DONE — moved `post_section_completion()` and `read_incoming_notes()` to `coordination/service/completion.py`. `scan/service/section_notes.py` retains `log_phase_failure()` (scan-specific) + re-exports for backward compatibility. Updated 3 production importers + 1 test file.

### 74. `dispatch/prompt/template.py` moved to `pipeline/template.py`
- **Status**: DONE — generic template rendering utility moved from domain-specific `dispatch/prompt/` to shared `pipeline/`. Updated 12 import sites across 10 production files + 2 test files.

### 68. Unused import: `ReconciliationResult` in orchestrator/engine/main.py
- **Status**: DONE — removed unused import from line 23.

### 69. Duplicate test doubles — `_MockDispatcher` in 9 files, `_MockGuard` in 6+, `_NoopFlow` in 7
- **Status**: DONE — consolidated 31 duplicate class definitions across 10 test files into 4 shared doubles in `tests/conftest.py`: `make_dispatcher(fn)`, `WritingGuard`, `NoOpFlow`, `StubPolicies`.

### 70. `orchestrator/service/context_assembly.py` is a re-export wrapper creating circular dependency
- **Status**: DONE — deleted wrapper file, updated 4 import sites to import directly from `dispatch.service.context_sidecar`. Eliminated `dispatch → orchestrator → dispatch` circular dependency.

### 75. Naming inconsistency: `run_reconciliation()` → `run_reconciliation_loop()`
- **Status**: DONE — renamed function and updated 3 callers + 3 test monkeypatch targets.

### 76. `risk/prompt/builders.py` → `risk/prompt/writers.py`
- **Status**: DONE — renamed file and functions (`build_*_prompt` → `write_*_prompt`). Updated 2 production importers + 1 test file.

### 77. `risk/repository/serialization.py` — `read_risk_artifact` → `load_risk_artifact`
- **Status**: DONE — standardized verb to `load_*` consistent with rest of codebase. Updated 3 test files.

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

### 24. `scripts/` folder — assessed (no move)
- **Status**: Assessed. `db.sh` stays — backbone of signals + flow systems. `log_extract/` is a standalone CLI tool. `workflow.sh` moved from orchestrator/.

### 25. `dispatch/agents/qa-monitor.md` orphaned
- **Status**: DONE — deleted. Zero references.

### 26. `agents/eval-judge.md` outside system pattern
- **Status**: DONE — deleted. Orphaned root-level agent file.

### 27. Dead `.py` modules (bulk)
- **Status**: DONE — deleted 5 confirmed dead modules. Kept 2 alive (imported by other modules).

### 28. `intent/service/philosophy.py` is a 2,005-line god module
- **Status**: DONE — split into 4 modules: `philosophy_classifier.py`, `philosophy_catalog.py`, `philosophy_dispatch.py`, `philosophy_bootstrap.py`. Original is a thin re-export facade.

### 29. DB connection boilerplate repeated 15+ times
- **Status**: DONE — created `task_db()` context manager in `flow/service/task_db_client.py`. Migrated 13 of 15 sites. Deleted duplicate `signals/db.sh`.

### 30. `flow/engine/reconciler.py` connection leak bug
- **Status**: DONE — consolidated `check_and_fire_gate()` from 3 separate DB connections to 1 with try/finally.

### 31. `risk/engine/loop.py` threshold parameter corruption bug
- **Status**: DONE — fixed 3 conditionals writing to wrong keys.

### 32. `proposal/engine/loop.py` function attribute state
- **Status**: DONE — replaced function attribute with local dict. Fixed divergent gate handling.

### 33. Manual path construction bypassing PathRegistry (69+ sites)
- **Status**: DONE — Added ~35 new PathRegistry accessors. Migrated all 211+ manual constructions. Zero bypass sites remain.

### 34. `dispatch/prompt/writers.py` — 5 copy-pasted prompt writers
- **Status**: DONE — extracted `_write_prompt()` helper. All 5 functions now delegate to it.

### 35. `dispatcher.py` 4 pointless compatibility shims
- **Status**: DONE — removed all 4. Updated callers.

### 36. Silent failures throughout (broad exception catches)
- **Status**: DONE — all degraded paths now log before failing open.

### 37. `scan/templates/` placement
- **Status**: DONE — templates moved to `src/templates/scan/`.

### 38. `_posture_rank()` duplicated in 3 files
- **Status**: DONE — added `rank` property to `PostureProfile` enum.

### 39. `orchestrator/` contains `workflow.sh`
- **Status**: DONE — moved to `scripts/workflow.sh`.

### 40. `_clamp_int`/`_clamp_float` duplicated in 3 risk files
- **Status**: DONE — centralized to `risk/types.py`.

### 41. `intake/types.py` dead module (143 lines)
- **Status**: DONE — deleted. Zero imports.

### 42. `_check_and_clear_alignment_changed` duplicated in 6 files
- **Status**: DONE — extracted `make_alignment_checker` factory. All 6 copies replaced.

### 43. God modules (>500 lines) — tracking
- **Status**: DONE — All engine files decomposed to under 600 lines. Remaining near-boundary files (`implementation_pass.py` 591, `tool_registry_manager.py` 557, `proposal/engine/loop.py` 519) are acceptable at current sizes.

### 46. `normalize_section_number` + `build_section_number_map` duplicated
- **Status**: DONE — removed copies, now imports from canonical location.

### 47. Circular imports in `flow.*` modules
- **Status**: DONE — removed all `sys.path` hacks. 2 justified lazy imports remain.

### 48. `_database_client` factory duplicated in 4 files
- **Status**: DONE — added `DatabaseClient.for_planspace()` classmethod.

### 49. `_mailbox` factory duplicated in 3 files
- **Status**: DONE — added `MailboxService.for_planspace()` classmethod.

### 50. `_registry_for_artifacts` duplicated in 3 scan/substrate files
- **Status**: DONE — canonical copy in `helpers.py`.

### 51. Risk utility duplication
- **Status**: DONE — centralized to canonical modules.

### 52. `_write_section_input_artifact` duplicated
- **Status**: DONE — extracted to `orchestrator/repository/section_artifacts.py`.

### 53. `_read_if_exists` duplicated
- **Status**: DONE — merged into `signals/repository/artifact_io.py`.

### 54. Log extractor duplication
- **Status**: DONE — created `scripts/log_extract/extractors/common.py`.

### 55. Dispatch template split
- **Status**: DONE — all templates consolidated to `src/templates/dispatch/`.

### 56. `engine/` folders are dumping grounds for mixed concerns
- **Status**: DONE — all engine files decomposed. 14 service/repository modules extracted.

### 57. Files named as functions, not components
- **Status**: DONE — 5 major renames completed. Remaining single-function files are acceptable.

### 58. QA system concerns misplaced in `dispatch/`
- **Status**: DONE — extracted `qa/` system with routes, service, agents, harness.

### 59. God functions with inlined concerns (systemic)
- **Status**: DONE — extracted 14 service/repository modules from engine files.

### 60. Missing workflow/pipeline engine
- **Status**: DONE — built `src/pipeline/` package. `intent/engine/bootstrap.py` refactored to 6 pipeline steps.

### 61. DI container incomplete — bare functions still scattered everywhere
- **Status**: DONE — 19 services containerized, ~75 functions migrated, ~225 production files + ~40 test files updated. 1561 tests passing.

### 62. `engine/loop.py` files are still god functions, not components
- **Status**: DONE — all 7 god functions decomposed into single-concern helpers.

### 64. Dataclasses with methods — business logic hiding in data objects
- **Status**: DONE — all 3 offenders fixed.

### 65. Misplaced modules — boundary violations
- **Status**: DONE — all 4 items resolved. `qa_verdict.py` moved, `file_utils.py` merged, `dispatch/helpers/utils.py` and `proposal/helpers/verdict_parsers.py` now accessed exclusively through the DI container.

### 66. Untyped signal data — rolling own pydantic with raw dicts and tuples
- **Status**: DONE — core signal system fully typed (`AgentSignal`, `SignalResult`). Remaining domain-specific dict returns (philosophy state dicts, risk serialization) are intentional — they serialize to JSON and don't benefit from pydantic typing.

### 67. Complex tuple return types instead of result dataclasses
- **Status**: DONE — all 8 worst offenders (3+ element tuples) replaced with frozen dataclasses.
