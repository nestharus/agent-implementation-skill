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

### 9. Coupling Analysis
Coupling measures how much a unit depends on other units. High coupling = fragile, hard to change.

**Metrics to measure:**
- **Afferent coupling (Ca)**: How many modules depend ON this module. High Ca = change here breaks many things.
- **Efferent coupling (Ce)**: How many modules this module depends on. High Ce = this module is fragile to changes elsewhere.
- **Cross-system imports**: A module importing from 4+ different systems is a coupling hotspot.
- **Parameter coupling**: Functions taking 5+ parameters are coupled to their callers' knowledge of internal needs.
- **Temporal coupling**: Functions that must be called in a specific order without that order being enforced by the type system or API.

**Scan**: `grep -rn "^from " src/ | cut -d: -f1,2 | sort` — build import graph. Count unique system prefixes per file.

### 10. Cohesion Analysis
Cohesion measures how related the internals of a unit are. Low cohesion = doing too many unrelated things.

**Metrics to measure:**
- **Functional cohesion**: Every function in a module contributes to a single well-defined purpose.
- **Sequential cohesion**: Output of one function feeds into the next — acceptable but weaker.
- **Utility cohesion**: Functions grouped because they're "similar" but don't share a purpose — smell.
- **Import spread**: If a module's functions each import from different systems, the module lacks focus.

**Red flags:**
- A `service/` file with functions that don't share parameters or domain concepts.
- A file where removing any one function wouldn't affect the others.
- Helper/utility files that are really "I didn't know where to put this."

### 11. Recursive Concern Decomposition
A function that "does one thing at a high level" can still be a tangled mess internally. Concerns must be decomposed recursively until each unit is atomic.

**Decomposition levels:**
1. **Module level**: Does this file have one responsibility?
2. **Function level**: Does this function do one thing?
3. **Block level**: Does each if/for/try block within the function serve the function's single concern, or does it introduce a new concern?

**The "one big concern" trap**: A function called `process_section()` technically has one concern — "process a section." But inside it might: read state, validate, transform, dispatch, log, persist, handle errors. Each is a separate concern. Keep decomposing until you can't.

**Atomic concern test**: Can you describe what this function does WITHOUT using "and" or "then"? If not, it has multiple concerns.

### 12. Contract Surface Cleanliness
Contracts are the interfaces between components. Messy contracts create coupling, confusion, and bugs.

**What makes a contract messy:**
- **Wide contracts**: Function takes 6+ parameters — callers must understand too much.
- **Leaky contracts**: Return type exposes internal structure (raw dicts instead of typed objects).
- **Implicit contracts**: Behavior depends on caller knowing undocumented invariants (call order, required state).
- **Multi-system contracts**: A single function signature spans concepts from 3+ different systems.
- **Overloaded contracts**: One function does different things based on parameter combinations.

**Scan**: Functions at module boundaries (imported by other systems) with `dict` return types or 5+ params.

### 13. Dependency Injection Gaps
Functions that take parameters obtainable from the DI container are exposing unnecessary coupling.

**Patterns to detect:**
- Functions taking `planspace: Path` when they could get it from a scoped service.
- Functions taking `policy: dict` when `Services.policies()` exists.
- Functions taking `paths: PathRegistry` when it's constructible from `planspace`.
- Helper functions that receive services as parameters instead of accessing them through the container.

**The judgment call**: Not every parameter should come from DI. Domain data (section numbers, payloads, results) belongs in parameters. Infrastructure (loggers, policies, registries) belongs in DI.

---

## OPEN

### 93. Long parameter lists — procedural style bypasses encapsulation
- **Category**: God functions / missing abstraction
- **Source**: External code review (R118), coupling analysis (R118)
- **Example**: `_dispatch_implementation(section, planspace, codespace, parent, policy, paths, artifacts, impl_problems)` in `implementation_cycle.py` — 8 parameters threading world state through every helper.
- **Prior triage**: #73 closed as "won't fix" for `_handle_aligned_surfaces`, #63 closed for planspace+codespace pair. Re-opened for broader assessment: the procedural parameter-threading pattern is systemic, not isolated to one function.
- **Risk**: Every helper that takes 6+ world-state parameters is a hidden coupling surface. Callers must know internal parameter needs. Adding a parameter propagates across the call chain.
- **Scale**: 128 functions have 6+ parameters. Worst: `submit_task` (17), `handle_tool_friction` (14), `determine_engagement` (12), `_request_user_philosophy` (12). 83 functions thread the `(planspace, codespace, parent, section, policy)` tuple through up to 4 call-chain levels.

### 94. Bash subprocess for SQLite operations (`db.sh`)
- **Category**: Abstraction gap / technology boundary violation
- **Source**: External code review (R118), concern decomposition (R118)
- **Example**: `subprocess.run(["bash", str(DB_SH), "init", str(paths.run_db())], check=True, ...)` in `pipeline_orchestrator.py`.
- **Prior triage**: #24 assessed as "db.sh stays." #29 migrated boilerplate to `task_db()`. Re-opened: `db.sh` is still invoked via subprocess for init and some operations despite Python having native `sqlite3`.
- **Risk**: Shell overhead, escaping surface, opaque error handling, breaks DI boundary (can't mock/test the DB init path).
- **Scale**: 6 `subprocess.run([...DB_SH...])` lifecycle-logging calls inlined in `implementation_phase.py` and `proposal_phase.py`. These are middleware concerns (lifecycle events) mixed with business logic (risk evaluation, proposal dispatch).
- **Status**: PARTIALLY DONE — 6 lifecycle-logging subprocess calls replaced with `Services.logger().log_lifecycle()` container method. Remaining `db.sh` uses (init, other ops) still present in `pipeline_orchestrator.py`.

### 95. Service Locator pattern — global `Services.*` calls instead of constructor injection
- **Category**: DI architecture / testability
- **Source**: External code review (R118), DI gap analysis (R118)
- **Example**: `Services.logger().log(...)`, `Services.communicator().mailbox_send(...)` called globally in free functions throughout the codebase.
- **Prior triage**: #61 completed containerization with this exact pattern. Re-opened for assessment: Service Locator is a known anti-pattern that hides dependencies, makes testing harder (must mock globals), and couples every function to the container.
- **Risk**: Dependencies are invisible — function signatures don't declare what services they need. Mocking requires patching the global container rather than passing fakes.
- **Scale**: `containers.py` has Ca=109 (imported by 109 files) and Ce=26 (imports 26 modules). Participates in 28 of 65 three-way circular dependency cycles.

### 96. Circular dependencies — 65 three-way cycles, 24 bidirectional pairs
- **Category**: Coupling / structural fragility
- **Source**: Coupling analysis (R118)
- **Worst bidirectional pairs**: `implementation <-> proposal`, `implementation <-> orchestrator`, `implementation <-> reconciliation`, `implementation <-> coordination`, `orchestrator <-> proposal`, `orchestrator <-> signals`, `containers <-> orchestrator`.
- **Risk**: The system cannot be decomposed into independently testable/deployable units without breaking cycles first. `containers` participates in 28/65 three-way cycles. `implementation` participates in 22/65. `orchestrator` participates in 35/65. A change in any one node of a cycle potentially invalidates all other nodes.

### 97. Multi-concern god functions — 15 worst offenders identified
- **Category**: Concern decomposition / tangled business logic
- **Source**: Concern decomposition analysis (R118)
- **Top 5**:
  1. `run_implementation_pass()` in `implementation_phase.py` — 264 lines, 8 concerns (orchestration, control flow, logging, subprocess DB, domain decisions, hash writing, result mapping, communication)
  2. `dispatch_task()` in `task_dispatcher.py` — 258 lines, 8 concerns (routing, DB ops, QA interceptor, flow context, freshness gate, dispatch metadata, notification, orchestration)
  3. `run_reconciliation_loop()` in `cross_section_reconciler.py` — 209 lines, 6 concerns with duplicated adjudication blocks
  4. `run_proposal_pass()` in `proposal_phase.py` — 190 lines, 7 concerns including 35-line inlined re-explorer
  5. `_validate_governance_identity()` in `readiness_resolver.py` — 184 lines, 9 independent validation sub-concerns sharing only a mutable accumulator
- **Risk**: Each function fails the atomic concern test ("describe WITHOUT 'and' or 'then'"). Changing one concern (e.g., lifecycle logging format) requires reading and understanding all other inlined concerns.
- **Status**: PARTIALLY DONE — Cycles 4-6 decomposed 24 god functions. Key: `build_prompt_context` (266→34), `dispatch_task` (249→87), `analyze_impacts` (241→89), `handle_tool_friction` (232→61), `generate_intent_pack` (203→74), `_validate_governance_identity` (186→sub-100), `build_section_governance_packet` (186→78), `_apply_feedback` (181→sub-100), `reconcile_task_completion` (174→82), `run_impact_triage` (150→73), `post_section_completion` (149→80), `run_global_alignment_recheck` (139→25), `validate_flow_declaration` (130→35), `append_risk_history` (131→87), `_reexplore_section` (128→35), `_check_needs_microstrategy` (112→45). Remaining: `ensure_global_philosophy()` (918 lines — complex state machine).

### 98. Duplicated cross-cutting middleware — inlined instead of extracted
- **Category**: Separation of concerns / DRY violation
- **Source**: Concern decomposition analysis (R118)
- **Instances**:
  - QA interceptor: 28-40 line near-identical block in `section_dispatcher.py` (L88-116) AND `task_dispatcher.py` (L166-206). Must be modified in lockstep.
  - `alignment_changed_pending()` guard checks: inlined in 15 files across all engine modules
  - `_update_blocker_rollup(planspace)`: called in 8 files as point-in-time side effect
  - `Services.communicator().record_traceability(...)`: 44 calls across 22 files
  - `Services.communicator().log_artifact(...)`: inlined in 22 files
  - 100+ `Services.logger().log(...)` calls interleaved with business logic in engine files
- **Risk**: Cross-cutting concerns woven into business logic make every function harder to read, test, and modify. The duplicated QA interceptor is the most dangerous — a bug fix in one copy that misses the other creates silently divergent behavior.
- **Status**: PARTIALLY DONE — QA interceptor extracted to shared `qa/service/qa_gate.py` (evaluate_qa_gate). Both section_dispatcher.py and task_dispatcher.py now call it. Alignment guard variants consolidated via `check_alignment_and_raise`/`check_alignment_and_return` on PipelineControlService. Remaining: traceability, log_artifact, blocker_rollup still inlined.

### 99. Leaky dict contracts at system boundaries
- **Category**: Contract surface / type safety
- **Source**: Contract surface analysis (R118)
- **Worst offenders**:
  1. `load_proposal_state` -> `dict` (17 keys in schema, 4 cross-system consumers: implementation, reconciliation, risk, signals). Key rename silently returns None.
  2. `ensure_global_philosophy` -> `dict` with 18 return paths, different dict shapes per path. Callers match on string `status` values.
  3. `resolve_readiness` -> `dict` mutated AFTER serialization to disk — file and function return different shapes.
  4. `build_prompt_context` -> `dict` with 25+ keys consumed by all prompt writers. Silent KeyError on missing keys.
  5. `read_dispatch_metadata` -> `dict | None | object` three-way return type with sentinel comparison trap.
- **Risk**: Untyped dicts at system boundaries mean contract changes are invisible until runtime. 4 systems depending on `load_proposal_state` dict keys can break silently from a key rename.
- **Status**: PARTIALLY DONE — `resolve_readiness` now returns `ReadinessResult` dataclass. `dispatch_agent` now returns `DispatchResult` dataclass with `DispatchStatus` enum. Remaining: `load_proposal_state`, `ensure_global_philosophy`, `build_prompt_context`, `read_dispatch_metadata` still return raw dicts.

### 100. Stringly-typed protocol tokens — no enums for control flow
- **Category**: Type safety / silent bug risk
- **Source**: Additional quality axes research (R118)
- **Examples**: `"ALIGNMENT_CHANGED_PENDING"` checked via `==` in 15 locations across 5+ systems. `"TIMEOUT:"` via `.startswith()` in dispatch. `"restart_phase1"`, `"complete"`, `"exhausted"`, `"stalled"`, `"resume"`, `"abort"`, `"fail:"`, `"pause:"`, `"budget-exhausted:"`, `"underspec"` scattered across 20+ files.
- **Precedent**: `risk/types.py` already uses `str, Enum` for `StepDecision`, `RiskMode`, `PostureProfile`. The protocol layer hasn't adopted the pattern.
- **Risk**: A typo like `"ALIGNMENT_CHANGE_PENDING"` silently falls through to the else branch. No IDE autocomplete, no compile-time validation, no exhaustive match checking.
- **Status**: PARTIALLY DONE — `dispatch_agent` return values migrated to `DispatchStatus` enum (`SUCCESS`, `ALIGNMENT_CHANGED`, `QA_REJECTED`, `TIMEOUT`) with backward-compatible `__eq__`/`__str__` shims. Remaining: coordination loop tokens, pipeline control tokens still stringly-typed.

### 101. Implicit nullability chains — None means 3+ different things
- **Category**: Error handling / semantic ambiguity
- **Source**: Additional quality axes research (R118)
- **Example**: `_dispatch_implementation` returns `None` for 3 distinct conditions: prompt safety blocked, ALIGNMENT_CHANGED_PENDING, and TIMEOUT. Caller `run_implementation_loop` does `if impl_result is None: return None`, collapsing all three. The outer loop cannot distinguish timeout (retryable) from alignment change (restart required).
- **Scale**: 134+ `return None` statements across 40+ files. Many functions have 3+ distinct `return None` paths representing semantically different failures.
- **Risk**: The orchestrator makes recovery decisions without knowing the failure cause. Retrying on alignment change wastes budget. Aborting on timeout loses recoverable progress.

### 102. Error recovery asymmetry — error paths skip cleanup/notification
- **Category**: Reliability / state corruption
- **Source**: Additional quality axes research (R118)
- **Example**: `_dispatch_implementation` TIMEOUT path sends `fail:` message and logs, but ALIGNMENT_CHANGED_PENDING path returns None with no logging and no parent notification. Parent cannot distinguish "alignment changed" from "prompt blocked."
- **Scale**: 23+ `except Exception` handlers. Many do partial cleanup. `pipeline_state.py:wait_if_paused` calls `sys.exit(0)` on abort after sending fail, but other abort paths return None and skip cleanup.
- **Risk**: Incomplete error recovery leaves the orchestrator making decisions on stale state. Missed notifications mean parent agents wait indefinitely or proceed with incorrect assumptions.

### 103. Low-cohesion god modules — unrelated functions sharing a file
- **Category**: Cohesion / maintainability
- **Source**: Cohesion analysis (R118)
- **Worst offenders**:
  1. `philosophy_bootstrapper.py` — 1533 lines, 22 functions, 7 topics (path helpers, signal writers, context collectors, bootstrap prompter, user-input flow, grounding validation, multi-stage orchestration)
  2. `tool_registry_manager.py` — 536 lines, 4 functions with unrelated callback signatures. `handle_tool_friction` (222 lines) has no relationship to `write_tool_surface` (32 lines).
  3. `package_builder.py` — 449 lines, 22 functions mixing package lifecycle, persistence, microstrategy text parsing, step materialization, and generic text utilities.
  4. `risk_artifact_writer.py` — 269 lines mixing write-side producers with read-side queries (`has_stale_freshness_token`, `has_recent_loop_detected_signal`).
  5. `pipeline_control.py` — 164 lines, facade re-exporting from 3 modules plus local implementation for `requeue_changed_sections`.
- **Risk**: Any change to one topic within a low-cohesion file forces loading and understanding all other unrelated topics. Merge conflicts collide with unrelated code. Test files must cover unrelated concerns together.

### 104. Policy dict threading — 30+ functions take policy: dict unnecessarily
- **Category**: DI gap / parameter coupling
- **Source**: DI gap analysis (R118)
- **Pattern**: `policy: dict` is threaded through `run_implementation_loop` -> `_dispatch_implementation` -> `_dispatch_alignment_check` -> `_extract_alignment_problems` and 8+ other chains. Every function already has `planspace` available and could call `Services.policies().load(planspace)` internally.
- **Files affected**: `triage_orchestrator.py`, `scope_delta_aggregator.py`, `expanders.py` (3 functions), `microstrategy_generator.py`, `excerpt_extractor.py` (accesses `policy["setup"]` directly, bypassing resolution), `global_alignment_rechecker.py`, and others.
- **Risk**: 30+ function signatures carry a `dict` parameter that adds no flexibility — every caller loads the same policy from the same planspace. The dict type provides no key validation; `excerpt_extractor.py` accessing `policy["setup"]` directly is a silent KeyError risk.
- **Status**: DONE — All `policy: dict` threading eliminated across 3 rounds (~30 functions). Only `model_policy: dict` (legitimate model routing config) and `pipeline/context.py` dataclass field remain. Round 1: `validate_problem_frame`, `extract_excerpts`, `run_impact_triage`, `run_microstrategy`. Round 2: `run_implementation_loop` chain, `_collect_and_persist_problems`, `run_global_alignment_recheck`. Round 3: `execute_coordination_plan`, `run_proposal_pass`, `run_reconciliation_phase`, `run_proposal_loop`, `run_intent_bootstrap`, `aggregate_scope_deltas`, and remaining coordination/proposal/intent/scan functions.

### 105. Callback parameters for already-containerized services
- **Category**: DI gap / unnecessary indirection
- **Source**: DI gap analysis (R118)
- **Instances**:
  - `tool_registry_manager.py`: takes `dispatch_agent: Callable`, `log: Callable`, `update_blocker_rollup: Callable`, `write_consequence_note: Callable` — all 4 are accessible via `Services` container. 3 functions in this file each take a different callback cocktail.
  - `impact_analyzer.py`: takes `summary_reader: Callable` — only caller passes `extract_section_summary` which is already `Services.cross_section().extract_section_summary`.
- **Risk**: Invisible dependencies — the callable's expected signature is not type-checked at the call site. Callers must construct compatible callables manually instead of overriding container providers. Tests must construct mock callables instead of using DI overrides.
- **Status**: DONE — `tool_registry_manager.py` callback parameters (`dispatch_agent`, `log`, `update_blocker_rollup`, `write_consequence_note`) replaced with direct `Services.*` calls. Callers simplified. Tests updated to use DI overrides.

### 106. _config globals bypassing DI container
- **Category**: DI gap / testability
- **Source**: DI gap analysis (R118)
- **Pattern**: `AGENT_NAME`, `DB_SH`, `DB_PATH` imported directly from `_config` in 10 files. Functions in `message_poller.py` and `pipeline_state.py` accept these as keyword-only parameters, but callers always pass the same `_config` globals — making the parameterization ceremonial.
- **Risk**: Process-global state that cannot be overridden through the DI container. Tests must monkeypatch the `_config` module or accept that subprocess calls will be made. Prevents running parallel test suites with different config.

### 107. Temporal coupling in state machines — ordering not enforced
- **Category**: Structural fragility / state corruption
- **Source**: Additional quality axes research (R118)
- **Example**: `implementation_cycle.py` computes `pre_hashes = Services.staleness().snapshot_files(...)` at line 38, consumed 78 lines later in `_finalize(planspace, codespace, section, pre_hashes)`. Between these points, the implementation agent modifies files. If `_finalize` is ever called without prior snapshot, the diff silently produces wrong results.
- **Also**: `resolve_readiness` depends on `proposal-state.json` existing (returns fail-closed default if not), `build_strategic_state` produces empty risk fields if called before risk assessment, `run_intent_triage` silently absorbs missing artifacts into "(none)" prompt text.
- **Risk**: Refactoring that reorders calls or adds new code paths can silently corrupt state. The temporal contract exists only in code position, not in the type system.

### 108. Lifecycle management gaps — sys.exit bypasses finally blocks
- **Category**: Resource leaks / reliability
- **Source**: Additional quality axes research (R118)
- **Example**: `pipeline_state.py:pause_for_parent` calls `sys.exit(0)` at line 93 on abort. This is called from `implementation_cycle.py:_handle_signal_pause`, which is called from a chain inside `run_implementation_loop`. The `sys.exit(0)` bypasses the `finally` block in `pipeline_orchestrator.py:main()` that calls `mailbox_cleanup`. The mailbox registration in the coordination database is never removed.
- **Risk**: Stale mailbox registrations on subsequent runs. `sys.exit` in library code (not `__main__`) is inherently dangerous — it prevents callers from cleaning up.

### 109. Overloaded contracts — behavioral modes hidden behind None params
- **Category**: Contract surface / readability
- **Source**: Contract surface analysis (R118)
- **Instances**:
  1. `dispatch_agent` — 5 behavioral modes based on None/non-None combinations of `planspace`, `parent`, `agent_name`, `codespace`. Providing `planspace` without `parent` gets context sidecar and QA intercept but no pause checks — a partial safety configuration.
  2. `_extract_problems` — when `output_path=None, planspace=None, parent=None`, returns None (meaning "aligned") when the alignment check actually failed. False-positive alignment.
  3. `submit_fanout` — `gate=None` means fire-and-forget with no convergence. Accidentally omitting gate = parallel tasks that never converge, with no error.
  4. `build_strategic_state` — output file location changes based on whether planspace is provided. Consumer reading canonical path gets stale data.
- **Risk**: Callers must know the full matrix of None/non-None combinations to use these functions safely. The wrong combination produces silently wrong behavior, not errors.

### 110. Defensive mkdir proliferation — 100 calls with no directory contracts
- **Category**: Implicit contracts / maintainability
- **Source**: Contract surface analysis (R118)
- **Scale**: 100 `mkdir(parents=True, exist_ok=True)` calls across the codebase. `readiness_gate.py` alone has 4 mkdir calls creating different directory trees.
- **Risk**: Each mkdir defensively creates its own tree, so no function documents which directories it creates or expects. If another function assumes a directory exists without its own mkdir, call ordering matters — but that ordering is nowhere documented. Removing a "redundant" mkdir silently breaks functions that depend on it existing.

### 111. Dead module: `dispatch/service/output_adjudicator.py` has zero callers
- **Category**: Dead code
- **Source**: Rescan R119
- **What**: `adjudicate_agent_output()` was extracted from `section_dispatch.py` per #2, but no module ever imported it afterward. 94-line unreachable dispatch workflow.
- **Status**: NOT DEAD — used by `tests/integration/test_agent_templates.py`. Skipped.

### 112. Dead functions in `intent/service/expansion_facade.py`: 3 private wrappers never called
- **Category**: Dead code
- **Source**: Rescan R119
- **What**: `_run_problem_expander()`, `_run_philosophy_expander()`, `_adjudicate_recurrence()` — zero callers. Only the two public functions are consumed. 56 lines dead.
- **Status**: DONE — removed 3 dead wrappers (56 lines).

### 113. Dead imports: 4 unused imports across production files
- **Category**: Dead code
- **Source**: Rescan R119
- **Where**: `dispatch_agent` in `deep_scanner.py`, `apply_related_files_update` in `section_explorer.py`, `Path` in `scope_delta_parser.py`, `_DEFAULT_MODELS` alias in `scan_dispatcher.py`.
- **Status**: PARTIALLY DONE — removed `Path` from `scope_delta_parser.py`. Other 3 are referenced by integration tests.

### 114. Dead re-exports in `flow/service/flow_facade.py`: 2 names never imported
- **Category**: Dead code
- **Source**: Rescan R119
- **What**: `build_gate_aggregate_manifest` and `build_result_manifest` re-exported but never consumed via the facade.
- **Status**: NOT DEAD — used by `tests/integration/test_task_flow_reconciliation.py`. Skipped.

### 115. `proposal/engine/proposal_cycle.py` grown to 766 lines
- **Category**: God module
- **Source**: Rescan R119
- **What**: Was 519 lines at #43 closure ("acceptable"). Now 766 lines, 16 functions, `run_proposal_loop()` 155 lines with 19 `return None` paths. Mixes loop orchestration, model selection, prompt construction, alignment checking, and surface handling.
- **Status**: DONE — decomposed from 766 to 183 lines. Extracted 4 new modules: `proposal/service/cycle_control.py` (dispatch, budget, signal handling), `proposal/service/alignment_handler.py` (alignment checking), `proposal/service/surface_handler.py` (surface management), `proposal/service/proposal_prep.py` (prompt/model preparation).

### 116. `dispatch_fn: Callable` callback should use DI container (8 remaining sites)
- **Category**: DI gap / unnecessary indirection
- **Source**: Rescan R119
- **Where**: `risk/engine/risk_assessor.py` (5 sites), `proposal/engine/proposal_phase.py` (1), `implementation/engine/implementation_phase.py` (2). Every caller passes `Services.dispatcher().dispatch`. Same pattern fixed in #105 for `tool_registry_manager.py`.
- **Status**: DONE — all 8 `dispatch_fn` parameters removed. Functions now call `Services.dispatcher().dispatch` internally. Tests updated to use DI overrides.

### 117. `logger: Callable` callback threaded through 11 functions
- **Category**: DI gap / unnecessary indirection
- **Source**: Rescan R119
- **Where**: `pipeline_state.py`, `message_poller.py`, `mailbox_service.py`, `monitor_service.py`, `flow_signal_parser.py`, `alignment_collector.py`. Every caller passes `Services.logger().log`.
- **Status**: DONE — all 11 `log`/`logger` callback parameters removed. Functions now call `Services.logger().log` internally. 5 caller files and 5 test files updated.

### 118. Silent `except Exception` without logging: 2 regression sites from #36
- **Category**: Error handling
- **Source**: Rescan R119
- **Where**: `risk/engine/risk_assessor.py:267` (catches dispatch exception, returns None silently), `flow/service/notifier.py:100` (catches any exception, silently `pass`es).
- **Status**: DONE — both sites now log before failing open.

### 119. 22 inlined prompt f-strings remain across 15 files
- **Category**: Separation of concerns
- **Source**: Rescan R119
- **Worst**: `intent_pack_generator.py:209` (76 lines), `philosophy_bootstrapper.py:775` (68 lines), `assessment_evaluator.py:33` (62 lines), `section_reexplorer.py:46` (62 lines). Template system exists but is used inconsistently.
- **Status**: PARTIALLY DONE — Cycle 8 extracted 22+ inlined prompts into dedicated `_compose_*_text()` builder functions across `intent_pack_generator.py`, `intent_triager.py`, `assessment_evaluator.py`, `section_reexplorer.py`, `planner.py`, `impact_analyzer.py`, `expanders.py`, `triage_orchestrator.py`, `microstrategy_generator.py`, `scope_delta_aggregator.py`, `tool_registry_manager.py`, `output_adjudicator.py`, `proposal_prep.py`, `substrate prompt_builder.py`, and more. 4 remain in `philosophy_bootstrapper.py` (deferred).

### 120. `build_prompt_context()` is a 266-line god function
- **Category**: God function
- **Source**: Rescan R119
- **Where**: `dispatch/prompt/context_builder.py:17`. 25+ key dict, ~50 file-existence checks, path resolutions, and conditional reads in one body.
- **Status**: DONE — decomposed into 10 private helpers (largest 43 lines). Main function now 34 lines.

### 121. DI container bypass in `signal_checker.py` and `section_dispatcher.py`
- **Category**: DI gap
- **Source**: Rescan R119
- **What**: Direct imports of `read_signal_tuple` and `validate_dynamic_content` instead of `Services.*` in files that also use `Services` for other calls. Tests overriding providers won't affect these call sites.
- **Status**: DONE — both replaced with `Services.signals().read_tuple()` and `Services.prompt_guard().validate_dynamic()` respectively.

### 122. `gate_repository.py` raw `sqlite3.connect()` with manual PRAGMA boilerplate
- **Category**: Abstraction gap
- **Source**: Rescan R119
- **Where**: `flow/repository/gate_repository.py:127-248`. Sole remaining instance of old pattern after #29 created `task_db()`.
- **Status**: DONE — replaced with `task_db()` context manager. Last raw `sqlite3.connect()` in production code eliminated.

### 123. 49 production modules have zero test imports (coverage gap)
- **Category**: Test coverage
- **Source**: Rescan R119
- **Key untested**: `implementation_cycle.py` (535 lines), `proposal_cycle.py` (766 lines), `plan_executor.py` (460 lines), `governance_packet_builder.py` (477 lines), `expansion_orchestrator.py` (260 lines), `global_alignment_rechecker.py` (138-line function).

### 124. Long if/elif chains — should use dict dispatch or match (CODE-B6)
- **Category**: Code anatomy / readability
- **Source**: Expanded reviewer scan R120 (CODE-B6 from code-anatomical-review)
- **Instances**:
  1. `signals/service/blocker_manager.py:84-96` — 7 elif mapping signal states to categories. Pure lookup table.
  2. `risk/repository/history.py:29,66,77` — 6 elif across `read_history()` and `compute_history_adjustment()`. Outcome status routing.
  3. `risk/service/threshold.py:27+` — 5 elif routing on `StepDecision` enum values. Could use match statement.
  4. `flow/types/schema.py:88-129` — 4 elif dispatching on action "kind" string (chain vs fanout).
  5. `intent/service/philosophy_bootstrapper.py:119,201,204,253+` — 7 elif across bootstrap state transitions.
  6. `scripts/log_extract/extractors/claude.py:35-52` — 5 elif dispatching on content block type.
  7. `intake/service/governance_packet_builder.py:13-110` — 5 elif matching region/profile.
  8. `scripts/log_extract/correlator.py:31-83` — 4 elif for event correlation scoring.
  9. `scripts/log_extract/extractors/gemini.py:79-151` — 4 elif for history file parsing.
- **Risk**: Long if/elif chains are harder to read and extend than dict dispatch. Adding a new case requires modifying the chain rather than adding an entry to a table.
- **Status**: PARTIALLY DONE — 4 chains converted: `blocker_manager.py` (2 chains → `_STATE_TO_CATEGORY`, `_BTYPE_TO_CATEGORY` dicts), `risk/repository/history.py` (2 chains → `_OUTCOME_SCORE`, `_VERIFICATION_ADJUSTMENT` dicts). Remaining 5 skipped: not pure lookups (complex logic, conditional construction, state transitions).

### 125. Deep nesting exceeds 4 levels (CODE-B4)
- **Category**: Code anatomy / complexity
- **Source**: Expanded reviewer scan R120 (CODE-B4 from code-anatomical-review)
- **Worst offenders**:
  1. `intent/service/philosophy_bootstrapper.py:549-670` — 8 levels deep. Philosophy state checking with cascading conditionals.
  2. `flow/engine/reconciler.py:155+` — 6 levels. Validation loops with nested error checking.
  3. `flow/types/schema.py:292-345` — 6 levels. Action/branch/step validation loop.
  4. `coordination/engine/plan_executor.py:33-51,422-444` — 6 levels. Batch compatibility + parallel execution.
  5. `signals/service/blocker_manager.py:80-97` — 5 levels. Blocker state categorization.
- **Scale**: 84 files have code at 5+ indentation levels. 800+ lines affected.
- **Risk**: Deep nesting makes control flow hard to follow and increases cyclomatic complexity. Refactor: extract inner blocks to helper functions, use early returns/guard clauses.
- **Status**: PARTIALLY DONE — `reconciler.py` reduced 8→3, `blocker_manager.py` 5→3, `plan_executor.py` 6→4, `readiness_gate.py` 6→3, `task_dispatcher.py` 9→reduced, `schema.py` 7→4 via `_validate_steps()` and `_validate_fanout_action()`. Cycle 8: Reduced max nesting from depth-9 to depth-6 (excluding philosophy_bootstrapper). Fixed: `_resolve_assessment_classes` (8→4 via `_parse_assessment_class`), `_session_candidate_from_file` (8→4 via `_extract_first_text`), `parse_context_field` (7→5 via `_parse_inline_yaml_list`), `_extract_text` in gemini extractor (7→4 via `_join_parts`), `_is_valid_dispatch_shape` (8→4 via `_is_valid_action`), `merge_surfaces_into_registry` (6→4 via `_build_surface_entry`), `build_pending_surface_payload` (6→4 via `_surface_from_entry`), `submit_fanout` (6→4 via `_freshness_from_steps`), `_build_session_candidates` in opencode (6→4 via `_scan_session_messages`), `_build_session_candidate` in claude (7→5 via `_extract_prompt_text`). Cycle 9: Fixed depth-5 violations: `read_history` (5→3 via `_parse_history_line`), `_collect_note_problems` (6→4 via `_classify_note_ack`). Cycle 10: Fixed 5 depth-5 violations: `merge_surfaces_into_registry` (5→4 via dict lookup replacing inner loop), `collect_modified_files` (5→3 via `_resolve_relative` helper + guard clause), `_record_timestamp` (5→3 via `_try_parse_ts` helper), `_iter_file_events` (5→4 via `_dispatch_record` helper), `_iter_records` (5→4 via flattened try/except). Remaining: `philosophy_bootstrapper.py` (depth 6).

### 126. Boolean parameter clusters — hidden behavioral modes (CODE-B1)
- **Category**: Code anatomy / API design
- **Source**: Expanded reviewer scan R120 (CODE-B1 from code-anatomical-review)
- **Worst offenders**:
  1. `risk/service/engagement.py:8` `determine_engagement()` — 8 boolean parameters (`has_shared_seams`, `has_consequence_notes`, `has_stale_inputs`, `has_recent_failures`, `has_tool_changes`, `freshness_changed`, `has_decision_classes`, `has_unresolved_value_scales`). Should be a dataclass like `EngagementContext`.
  2. `signals/service/database_client.py` — 8 functions all taking `check: bool` parameter. Same boolean threaded uniformly.
  3. `intent/service/philosophy_bootstrapper.py:414` `_request_user_philosophy()` — `overwrite_decisions: bool` among 15 total parameters.
- **Scale**: 30 boolean parameters across function definitions.
- **Risk**: Boolean params create invisible behavioral modes. Callers must know what `True` means at each site. Replace with enums, dataclasses, or split into separate functions.
- **Status**: PARTIALLY DONE — `determine_engagement()` refactored from 12 params (8 bools) to 5 params using `EngagementContext` frozen dataclass with `skip_floor_hit` property. 2 callers and 10 tests updated. Remaining: `database_client.py`, `philosophy_bootstrapper.py`.

### 127. Silent exception swallowing in `scripts/log_extract/utils.py:42`
- **Category**: Error handling (CODE-E2)
- **Source**: Expanded reviewer scan R120 (CODE-E2 from code-bug-review)
- **What**: `except Exception: continue` silently skips malformed TOML model config files during parsing. No logging, no warning.
- **Also**: `scripts/log_extract/extractors/common.py:98,129` — conditional silent failures (only log if `source_label` is set).
- **Risk**: Malformed config files are silently ignored, making debugging configuration issues difficult.
- **Status**: DONE — `utils.py` now prints warning to stderr before `continue`. `common.py` logging made unconditional with `label = source_label or "log_extract"` fallback.

### 128. 57 functions exceed 100 lines (CODE-S2 FAIL inventory)
- **Category**: Code style / function length
- **Source**: Expanded reviewer scan R120 (CODE-S2 from code-style-review)
- **Top 5**: `ensure_global_philosophy()` 923 lines, `run_substrate_discovery()` 338 lines, `dispatch_task()` 249 lines, `analyze_impacts()` 243 lines, `handle_tool_friction()` 232 lines.
- **Scale**: Originally 57 functions >100 lines (FAIL). After Cycles 4-9: **1 function remains** (56 brought under 100). Cycle 8 decompositions: `run_substrate_discovery` (334→sub-100, extracted 5 phase helpers), `run_proposal_loop` (134→77 lines, extracted `_dispatch_and_validate_proposal` + `_run_alignment_phase`), `run_implementation_pass` (122→sub-100, extracted `_implement_section` + `_prepare_risk_plan` + `_handle_failed_impl`), `_iter_file_events` (119→sub-100, extracted per-record-type handlers), `_run_frontier_iterations` (112→54 lines, extracted `_execute_frontier_slice`), `validate_philosophy_grounding` (111→sub-100, extracted source map validation helpers). Cycle 9: reduced 4 more functions in 80-97 range: `_write_alignment_surface` (97→22, data-driven via `_collect_surface_entries`), `_run_freshness_check` (96→69, extracted `_interpret_freshness_signal`), `_run_bridge_for_group` (91→58, extracted `_ensure_contract_delta`), `_write_traceability_index` (completed extraction of `_collect_alignment_verdicts` + `_optional_artifact`, now 45 lines).
- Cycle 10: Reduced `route_blockers` (81→22, extracted `_route_user_root_questions`, `_route_shared_seams`, `_route_unresolved_contracts`), `_recheck_section_alignment` (78→60, extracted `_classify_alignment_result`), `run_aligned_expansion` (72→52, extracted `_handle_budget_exhaustion`), `validate_risk_plan` (72→57, extracted `_validate_accept_decision`).
- **Overlap**: Subsumes #97 (god functions). **Only `ensure_global_philosophy` (918 lines) remains** — massive state machine in philosophy_bootstrapper.py, very high risk to decompose.

### 129. 60 functions have 8+ parameters (CODE-S3 FAIL inventory)
- **Category**: Code style / parameter count
- **Source**: Expanded reviewer scan R120 (CODE-S3 from code-style-review)
- **Top 5**: `submit_task()` 18 params, `_request_user_philosophy()` 15 params, `_dispatch_classified_signal_stage()` 13 params, `_write_prompt()` 13 params, `handle_tool_friction()` 12 params.
- **Overlap**: Subsumes #93 (long parameter lists). This is the precise inventory.

### 130. Broad `except Exception` without `# noqa: BLE001` (CODE-E1)
- **Category**: Error handling / exception specificity
- **Source**: Expanded reviewer scan R120 (CODE-E1 from code-bug-review), Cycle 9 rescan
- **Scale**: 11 `except Exception` catches across risk_assessor.py, task_dispatcher.py, qa_gate.py (×2), qa_interceptor.py, adjudicator.py, plan_executor.py, common.py (×2), gemini.py (×2), utils.py. All are intentional fail-open handlers with logging — legitimate broad catches that needed `# noqa: BLE001` annotation.
- **Status**: DONE — All 11 catches annotated with `# noqa: BLE001` and purpose comments in Cycle 9. Cycle 10: Fixed 1 additional missed catch in `flow/service/notifier.py:101`. Codebase also clean on CODE-E11 (mutable defaults), CODE-B7 (guard clauses), CODE-S9 (dead code/unreachable code).

### 131. Duplicate status/signal string literals (CODE-S8)
- **Category**: Code style / magic strings
- **Source**: Cycle 9 expanded reviewer scan
- **Worst offenders**: `"ALIGNMENT_CHANGED_PENDING"` (32 occurrences, 20 files), `"needs_parent"` (39 occurrences, 18 files), `"why_blocked"` (34 occurrences), `"-output.md"` / `"-prompt.md"` suffix patterns (41/32 occurrences).
- **Risk**: String literals duplicated across many files. Typo in one file = silent behavioral divergence. `ALIGNMENT_CHANGED_PENDING` is partially mitigated by `DispatchResult.__eq__` backward-compat.
- **Status**: Partially addressed — Cycle 9 extracted magic numbers (stall threshold, cycle budgets, correlation time deltas, DB timeouts) to named constants in 4 files. Cycle 10: Extracted 7 more magic numbers to constants: `_RAW_RISK_EXPLORATION_THRESHOLD` (proposal_phase.py), `_MIN_CORRELATION_SCORE` (correlator.py), `_AXIS_BUDGET_CONSERVE_THRESHOLD` (expanders.py), `_FULL_ASSESSMENT_FILE_THRESHOLD`/`_FULL_ASSESSMENT_STEP_THRESHOLD` (engagement.py), `_MAX_PARENT_TRAVERSAL_DEPTH` (utils.py), `_EPOCH_MS_MAGNITUDE_THRESHOLD` (log_extract_helpers.py), `_RELATED_FILES_DISPLAY_LIMIT` (impact_analyzer.py). String literal consolidation deferred due to 20-file blast radius.

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

### 92. Dead imports: `update_match` in deep_scanner.py, `WORKFLOW_HOME` in section_communicator.py
- **Status**: DONE — removed dead `update_match` import from deep_scanner.py (tests updated to import from source module `match_updater`). Removed dead `WORKFLOW_HOME` import from section_communicator.py (kept `DB_PATH` as documented re-export for tests).

### 91. DI bypass: `section_dispatcher.py` direct `_log_artifact` + `pipeline_orchestrator.py` direct mailbox calls
- **Status**: DONE — replaced 2 direct `_log_artifact` calls in section_dispatcher.py with `Services.communicator().log_artifact()`. Added `mailbox_register`/`mailbox_cleanup` to `Communicator` container service and migrated pipeline_orchestrator.py to use container calls.

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
