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

**Additional DI patterns to detect:**
- Hardcoded module/class lists that should be discovered or configured via the container (e.g., route module registries).
- `importlib.import_module()` calls with hardcoded string lists instead of container-managed registration.

### 14. Optional Fields as Missing Discriminated Unions
Dataclasses or dicts with many optional/nullable fields are a sign that multiple distinct shapes are being collapsed into one "god type." The type doesn't tell you what shape you have at any given call site.

**Patterns to detect:**
- Dataclasses with 3+ `Optional[X]` / `X | None` fields where different subsets are populated for different use cases.
- Dict constructions with type-dependent keys (e.g., some dicts have `note_id` and some don't, all typed as `dict[str, Any]`).
- `.get("key", default)` chains where different callers access different subsets of keys — the dict is actually 2+ distinct shapes.
- Functions that check `if obj.field is not None` to determine which "mode" an object is in — the mode should be encoded in the type.

**The fix**: Discriminated unions with narrow frozen dataclasses. Each variant has exactly its fields — no optionals. Cross-cutting fields shared by multiple variants belong on an intermediate base class (aspect-oriented composition), not duplicated across variants or made optional on a god class.

**Scan**: `grep -rn "| None = None" src/` for optional fields. `grep -rn '\.get("' src/` for dict-shape ambiguity. Look for `isinstance` checks or `if x.field is not None` branching.

### 15. Magic Strings That Should Be Enums
String literals used as protocol tokens, status values, or mode selectors throughout the codebase. These create invisible contracts — a typo silently falls through to else branches with no IDE autocomplete and no exhaustive matching.

**Patterns to detect:**
- Fixed sets of string values compared with `==` or checked with `in (...)` — these are enum values without the enum.
- Dict keys like `"status"`, `"state"`, `"mode"`, `"action"`, `"verdict"` whose values are always one of a known set.
- `.startswith("prefix:")` message parsing — stringly-typed protocol messages that should be structured types.
- Constants defined as `X = "x"` scattered across files — halfway to an enum but without the exhaustiveness guarantee.
- `frozenset({"a", "b", "c"})` for membership testing — these ARE enum value sets, just not declared as enums.

**The fix**: `class X(str, Enum)` (like `StepDecision`, `RiskMode`, `PostureProfile` in `risk/types.py`). The `str, Enum` pattern preserves backward-compatible string comparison while adding type safety.

**Scan**: `grep -rn 'frozenset({' src/`, `grep -rn '"status"' src/`, `grep -rn '"state"' src/`, `grep -rn '"action"' src/`, `grep -rn '"verdict"' src/`, `grep -rn '\.startswith("' src/`.

### 16. Duplicate Code Detection
Identical or near-identical logic duplicated across files. Each copy must be modified in lockstep — missing one creates silently divergent behavior.

**Patterns to detect:**
- Functions with identical bodies in different modules (copy-paste inheritance).
- Inline code blocks (5+ lines) that appear in 2+ files with only variable name changes.
- Construction patterns repeated across files (e.g., building the same object from the same inputs in 3 places).
- Validation/parsing logic duplicated between a "primary" and "retry/escalation" path.

**The fix**: Extract to a shared function in the appropriate service module. For construction patterns, use factory methods or `@classmethod` constructors on the type itself.

**Scan**: Compare function bodies across files with similar names. `grep -rn "def _validate" src/ | sort` — look for clusters. Check `engine/` dirs for duplicated abort/retry/guard patterns.

### 17. Vague Data Structures
Generic types (`list[list[X]]`, `tuple[A, B, C]`, `dict[str, Any]`) that hide semantic meaning behind positional or structural conventions. The type annotation reveals nothing about what the data *means* — consumers must rely on docstrings, variable names, or reading the source.

**Patterns to detect:**
- **Nested lists as unnamed groupings**: `list[list[Problem]]` where each inner list is a "problem group" — but the type doesn't say that. Parallel metadata travels in separate `list[str]` structures (strategies, reasons) indexed by position.
- **Tuple returns with positional semantics**: `tuple[list[list[Problem]], list[str], dict]` where index 0 = groups, 1 = strategies, 2 = raw plan. Consumers must know the positions by convention.
- **Loose dict bags**: `dict[str, Any]` used for structured data with known fields (`{"needed": bool, "reason": str}` for bridge directives, `{"recurring_sections": ..., "recurring_problem_count": ...}` for recurrence reports). `.get("key")` chains reveal the shape but the type doesn't.
- **Positionally coupled parallel lists**: Two or more lists where index `i` of each list carries related data — `groups[i]` matches `strategies[i]` matches `file_sets[i]`. A `zip` without a named container.

**The fix**: Frozen dataclasses. `ProblemGroup(problems, strategy, reason, bridge)` replaces `list[list[Problem]]` + `list[str]`. `BridgeDirective(needed, reason)` replaces `dict`. `RecurrenceReport(recurring_sections, count, max_attempt, indices)` replaces `dict[str, Any]`. The type annotation *is* the documentation.

**Scan**: `grep -rn "list\[list\[" src/`, `grep -rn "-> tuple\[" src/`, `grep -rn "-> dict\[str, Any\]" src/`, `grep -rn "dict\[str, Any\] | None" src/`. Look for parallel list constructions (`x.append(...); y.append(...)` in the same loop).

### 18. Derivable Parameters
Functions that accept parameters which could be computed from other parameters already in scope. The extra params widen the contract surface and force callers to do derivation work that the callee could handle.

**Patterns to detect:**
- `tool_registry_path` passed alongside `planspace` — derivable as `PathRegistry(planspace).tool_registry()`.
- `friction_signal_path` passed alongside `planspace` + `section_number` — derivable via `PathRegistry`.
- `artifacts: Path` passed alongside `planspace` — always `PathRegistry(planspace).artifacts`.
- `tools_available_path` passed alongside `planspace` + `section_number` — derivable.
- Multiple scan functions passing `codemap_path`, `artifacts_dir`, `scan_log_dir` separately when `ScanContext` bundles them.

**The fix**: Derive internally. If a function already has `planspace`, it can compute `PathRegistry(planspace).X()` itself. If derivation is expensive, `DispatchContext` (with `@cached_property paths`) or `ScanContext` (with `from_artifacts()` factory) already exist as bundling patterns. Adopt them more broadly.

**Existing patterns to reuse**:
- `DispatchContext(planspace, codespace, parent)` — `@cached_property paths: PathRegistry`
- `ScanContext.from_artifacts()` — bundles scan-specific paths with derivation
- `PipelineContext` — mutable state threading (less common)

**Scan**: Functions with 8+ params where 2+ are `Path` types and one is `planspace`. Check if the path params are always `PathRegistry(planspace).some_method()` at call sites.

---

## OPEN

### ~~93. Long parameter lists — procedural style bypasses encapsulation~~ CLOSED
- **Category**: God functions / missing abstraction
- **Source**: External code review (R118), coupling analysis (R118)
- **Example**: `_dispatch_implementation(section, planspace, codespace, parent, policy, paths, artifacts, impl_problems)` in `implementation_cycle.py` — 8 parameters threading world state through every helper.
- **Prior triage**: #73 closed as "won't fix" for `_handle_aligned_surfaces`, #63 closed for planspace+codespace pair. Re-opened for broader assessment: the procedural parameter-threading pattern is systemic, not isolated to one function.
- **Risk**: Every helper that takes 6+ world-state parameters is a hidden coupling surface. Callers must know internal parameter needs. Adding a parameter propagates across the call chain.
- **Scale**: 128 functions have 6+ parameters. Worst: `submit_task` (17), `handle_tool_friction` (14), `determine_engagement` (12), `_request_user_philosophy` (12). 83 functions thread the `(planspace, codespace, parent, section, policy)` tuple through up to 4 call-chain levels.
- **Resolution**: Won't fix beyond completed work. Original scale was 128 functions with 6+ params. Concrete reductions: #104 eliminated `policy: dict` from ~30 functions. #126 introduced `EngagementContext` (12→5 params). #129 reduced 8+ param functions from 128+ to 24 irreducible (verified as distinct data values). #155 internalized derivable path params (e.g. `handle_tool_friction` 9→6, `surface_tool_registry` 8→4). `DispatchContext` bundles `(planspace, codespace, parent)`. The 24 remaining functions at 8-9 params take genuinely distinct domain arguments — task metadata fields, section-specific paths, model choices — that don't belong in a context object. Further reduction would require class-based refactoring of the entire pipeline with no functional benefit.

### ~~94. Bash subprocess for SQLite operations (`db.sh`)~~ DONE
- **Category**: Abstraction gap / technology boundary violation
- **Source**: External code review (R118), concern decomposition (R118)
- **Example**: `subprocess.run(["bash", str(DB_SH), "init", str(paths.run_db())], check=True, ...)` in `pipeline_orchestrator.py`.
- **Prior triage**: #24 assessed as "db.sh stays." #29 migrated boilerplate to `task_db()`. Re-opened: `db.sh` is still invoked via subprocess for init and some operations despite Python having native `sqlite3`.
- **Risk**: Shell overhead, escaping surface, opaque error handling, breaks DI boundary (can't mock/test the DB init path).
- **Scale**: 6 `subprocess.run([...DB_SH...])` lifecycle-logging calls inlined in `implementation_phase.py` and `proposal_phase.py`. These are middleware concerns (lifecycle events) mixed with business logic (risk evaluation, proposal dispatch).
- **Status**: DONE — All `db.sh` subprocess calls migrated to pure Python: 6 lifecycle-logging calls → `Services.logger().log_lifecycle()`, `db.sh init` → `init_db()`, 5 `db_cmd()` calls in `task_dispatcher.py` → `claim_task`/`complete_task`/`fail_task`/`next_task`, 1 `db_cmd()` call in `notifier.py` → `send_message`, 1 `subprocess.run` in `notifier.py` → `log_event`. The `db_cmd()` wrapper and `db.sh` still exist for external scripts (qa-harness.sh) and `DatabaseClient` usage in other modules.

### ~~95. Service Locator pattern — global `Services.*` calls instead of constructor injection~~ CLOSED
- **Resolution**: Won't fix — accepted as architectural pattern. This is a single-process CLI pipeline, not a microservices system. Service Locator with `providers.override()` gives testability without constructor injection through 109 files. The `dependency_injector` library's override mechanism gives tests isolation without monkeypatching. Converting to constructor injection would require rewriting every free function into a class with injected dependencies — a rewrite with no functional benefit.

### ~~96. Circular dependencies — 65 three-way cycles, 24 bidirectional pairs~~ CLOSED
- **Resolution**: Won't fix — accepted. The cycles exist because proposal/implementation/reconciliation/coordination are inherently coupled pipeline phases sharing domain types (`Section`, `PathRegistry`), signal contracts, and dispatch types. The container's lazy-import pattern prevents import-time failures. Breaking cycles requires extracting shared types into a `core/` package and introducing event-driven phase boundaries — a foundational architecture rewrite that would change the system's execution model, not a cleanup.

### ~~97. Multi-concern god functions — 15 worst offenders identified~~ DONE
- **Category**: Concern decomposition / tangled business logic
- **Source**: Concern decomposition analysis (R118)
- **Top 5**:
  1. `run_implementation_pass()` in `implementation_phase.py` — 264 lines, 8 concerns (orchestration, control flow, logging, subprocess DB, domain decisions, hash writing, result mapping, communication)
  2. `dispatch_task()` in `task_dispatcher.py` — 258 lines, 8 concerns (routing, DB ops, QA interceptor, flow context, freshness gate, dispatch metadata, notification, orchestration)
  3. `run_reconciliation_loop()` in `cross_section_reconciler.py` — 209 lines, 6 concerns with duplicated adjudication blocks
  4. `run_proposal_pass()` in `proposal_phase.py` — 190 lines, 7 concerns including 35-line inlined re-explorer
  5. `_validate_governance_identity()` in `readiness_resolver.py` — 184 lines, 9 independent validation sub-concerns sharing only a mutable accumulator
- **Risk**: Each function fails the atomic concern test ("describe WITHOUT 'and' or 'then'"). Changing one concern (e.g., lifecycle logging format) requires reading and understanding all other inlined concerns.
- **Status**: DONE — Cycles 4-6 decomposed 24 god functions. Cycle 11 decomposed `ensure_global_philosophy` (918→12 exec-line coordinator + 10 phase helpers, max 37 exec each). All functions now under 50 exec lines (see #128).

### ~~98. Duplicated cross-cutting middleware — inlined instead of extracted~~ DONE
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
- **Status**: DONE — QA interceptor extracted to shared `qa/service/qa_gate.py`. Alignment guard consolidated on PipelineControlService. Remaining items (traceability, log_artifact, blocker_rollup) are DI-accessed service calls (`Services.communicator().*`) — this IS the extracted pattern. No further extraction needed.

### ~~99. Leaky dict contracts at system boundaries~~ DONE
- **Category**: Contract surface / type safety
- **Source**: Contract surface analysis (R118)
- **Worst offenders**:
  1. `load_proposal_state` -> `dict` (17 keys in schema, 4 cross-system consumers: implementation, reconciliation, risk, signals). Key rename silently returns None.
  2. `ensure_global_philosophy` -> `dict` with 18 return paths, different dict shapes per path. Callers match on string `status` values.
  3. `resolve_readiness` -> `dict` mutated AFTER serialization to disk — file and function return different shapes.
  4. `build_prompt_context` -> `dict` with 25+ keys consumed by all prompt writers. Silent KeyError on missing keys.
  5. ~~`read_dispatch_metadata` -> `dict | None | object` three-way return type with sentinel comparison trap.~~ DONE — `DispatchMetaResult` dataclass + `DispatchMetaStatus` enum.
- **Risk**: Untyped dicts at system boundaries mean contract changes are invisible until runtime. 4 systems depending on `load_proposal_state` dict keys can break silently from a key rename.
- **Status**: DONE — `resolve_readiness` → `ReadinessResult`. `dispatch_agent` → `DispatchResult` + `DispatchStatus` enum. `read_dispatch_metadata` → `DispatchMetaResult` + `DispatchMetaStatus` enum. `load_proposal_state` → `ProposalState` (35+ `.get()` sites migrated). `ensure_global_philosophy` → `BootstrapResult` dataclass with backward-compat `__getitem__`/`get()`. `build_prompt_context` → accepted as dict (template context consumed by `format(**ctx)` unpacking with arbitrary `**overrides`).

### ~~100. Stringly-typed protocol tokens — no enums for control flow~~ DONE
- **Category**: Type safety / silent bug risk
- **Source**: Additional quality axes research (R118)
- **Examples**: `"ALIGNMENT_CHANGED_PENDING"` checked via `==` in 15 locations across 5+ systems. `"TIMEOUT:"` via `.startswith()` in dispatch. `"restart_phase1"`, `"complete"`, `"exhausted"`, `"stalled"`, `"resume"`, `"abort"`, `"fail:"`, `"pause:"`, `"budget-exhausted:"`, `"underspec"` scattered across 20+ files.
- **Precedent**: `risk/types.py` already uses `str, Enum` for `StepDecision`, `RiskMode`, `PostureProfile`. The protocol layer hasn't adopted the pattern.
- **Risk**: A typo like `"ALIGNMENT_CHANGE_PENDING"` silently falls through to the else branch. No IDE autocomplete, no compile-time validation, no exhaustive match checking.
- **Status**: DONE — `DispatchStatus`, `CoordinationStatus`, `PauseType`, `ControlSignal` enums adopted. 18 string domains typed in #146. Remaining mailbox protocol prefixes (`"resume:"`, `"pause:"`, `"fail:"`, `"complete"`) are wire-format strings, not enum candidates.

### ~~101. Implicit nullability chains — None means 3+ different things~~ CLOSED
- **Category**: Error handling / semantic ambiguity
- **Source**: Additional quality axes research (R118)
- **Example**: `_dispatch_implementation` returns `None` for 3 distinct conditions: prompt safety blocked, ALIGNMENT_CHANGED_PENDING, and TIMEOUT. Caller `run_implementation_loop` does `if impl_result is None: return None`, collapsing all three. The outer loop cannot distinguish timeout (retryable) from alignment change (restart required).
- **Scale**: 134+ `return None` statements across 40+ files. Many functions have 3+ distinct `return None` paths representing semantically different failures.
- **Risk**: The orchestrator makes recovery decisions without knowing the failure cause. Retrying on alignment change wastes budget. Aborting on timeout loses recoverable progress.
- **Resolution**: Primary crash vectors fixed. 6 `.startswith("TIMEOUT:")` calls on `DispatchResult` objects migrated to `result.status is DispatchStatus.TIMEOUT`. All dispatch result consumers now use `.output` for text content and `.status` for status checks across 15+ files. `DispatchResult` + `DispatchStatus` enum (#99) encodes the status discriminator. #102 added logging to 5 `ALIGNMENT_CHANGED` paths that previously returned None silently. The remaining `return None` chains (134+ sites) are abort cascades — inner function returns None, caller checks `if result is None: return None` — where all failure causes lead to the same recovery action (abort). No incorrect behavior: the orchestrator doesn't make different recovery decisions based on None cause in these paths.

### ~~102. Error recovery asymmetry — error paths skip cleanup/notification~~ DONE
- **Category**: Reliability / state corruption
- **Source**: Additional quality axes research (R118)
- **Example**: `_dispatch_implementation` TIMEOUT path sends `fail:` message and logs, but ALIGNMENT_CHANGED_PENDING path returns None with no logging and no parent notification. Parent cannot distinguish "alignment changed" from "prompt blocked."
- **Scale**: 23+ `except Exception` handlers. Many do partial cleanup. ~~`pipeline_state.py:wait_if_paused` calls `sys.exit(0)` on abort~~ fixed in #108 — now raises `PipelineAbortError`. Other abort paths still return None and skip cleanup.
- **Risk**: Incomplete error recovery leaves the orchestrator making decisions on stale state. Missed notifications mean parent agents wait indefinitely or proceed with incorrect assumptions.
- **Status**: DONE — Added logging to 5 ALIGNMENT_CHANGED paths that previously returned None silently: `implementation_cycle.py` (2 sites: impl dispatch + alignment check), `cycle_control.py` (integration dispatch), `alignment_handler.py` (proposal alignment check), `excerpt_extractor.py` (setup dispatch). Parent agents now see log entries when alignment changes cause aborts.

### ~~103. Low-cohesion god modules — unrelated functions sharing a file~~ DONE
- **Category**: Cohesion / maintainability
- **Source**: Cohesion analysis (R118)
- **Worst offenders**:
  1. `philosophy_bootstrapper.py` — 1183 lines, analyzed in Cycle 19: all functions small (max 37 exec), further decomposition creates circular deps
  2. `tool_registry_manager.py` — DONE, decomposed into `tool_surface_writer.py`, `tool_validator.py`, `tool_bridge.py` (Cycle 20). Original deleted.
  3. `package_builder.py` — 414 lines, all functions small (max 40 exec). Microstrategy parsing is a sub-concern of package construction, not a separate module. Acceptable.
  4. ~~`risk_artifact_writer.py` — 269 lines mixing write-side producers with read-side queries.~~ Renamed to `risk_artifacts.py` — name now reflects mixed read/write responsibility.
  5. `pipeline_control.py` — 138 lines, facade re-exporting from 3 modules plus `requeue_changed_sections`. Intentional DI boundary pattern — binds `_config` globals so downstream doesn't need to. Acceptable.
  6. `research_plan_executor.py` — DONE, split into `research_branch_builder.py` + executor (Cycle 20).
- **Risk**: Any change to one topic within a low-cohesion file forces loading and understanding all other unrelated topics. Merge conflicts collide with unrelated code. Test files must cover unrelated concerns together.
- **Status**: DONE — 2 decomposed (#2, #6), 1 irreducible (#1), 3 analyzed as acceptable (#3 cohesive, #4 renamed, #5 intentional facade).

### ~~104. Policy dict threading — 30+ functions take policy: dict unnecessarily~~ DONE
- **Category**: DI gap / parameter coupling
- **Source**: DI gap analysis (R118)
- **Pattern**: `policy: dict` is threaded through `run_implementation_loop` -> `_dispatch_implementation` -> `_dispatch_alignment_check` -> `_extract_alignment_problems` and 8+ other chains. Every function already has `planspace` available and could call `Services.policies().load(planspace)` internally.
- **Files affected**: `triage_orchestrator.py`, `scope_delta_aggregator.py`, `expanders.py` (3 functions), `microstrategy_generator.py`, `excerpt_extractor.py` (accesses `policy["setup"]` directly, bypassing resolution), `global_alignment_rechecker.py`, and others.
- **Risk**: 30+ function signatures carry a `dict` parameter that adds no flexibility — every caller loads the same policy from the same planspace. The dict type provides no key validation; `excerpt_extractor.py` accessing `policy["setup"]` directly is a silent KeyError risk.
- **Status**: DONE — All `policy: dict` threading eliminated across 3 rounds (~30 functions). Only `model_policy: dict` (legitimate model routing config) and `pipeline/context.py` dataclass field remain. Round 1: `validate_problem_frame`, `extract_excerpts`, `run_impact_triage`, `run_microstrategy`. Round 2: `run_implementation_loop` chain, `_collect_and_persist_problems`, `run_global_alignment_recheck`. Round 3: `execute_coordination_plan`, `run_proposal_pass`, `run_reconciliation_phase`, `run_proposal_loop`, `run_intent_bootstrap`, `aggregate_scope_deltas`, and remaining coordination/proposal/intent/scan functions.

### ~~105. Callback parameters for already-containerized services~~ DONE
- **Category**: DI gap / unnecessary indirection
- **Source**: DI gap analysis (R118)
- **Instances**:
  - `tool_registry_manager.py`: takes `dispatch_agent: Callable`, `log: Callable`, `update_blocker_rollup: Callable`, `write_consequence_note: Callable` — all 4 are accessible via `Services` container. 3 functions in this file each take a different callback cocktail.
  - `impact_analyzer.py`: takes `summary_reader: Callable` — only caller passes `extract_section_summary` which is already `Services.cross_section().extract_section_summary`.
- **Risk**: Invisible dependencies — the callable's expected signature is not type-checked at the call site. Callers must construct compatible callables manually instead of overriding container providers. Tests must construct mock callables instead of using DI overrides.
- **Status**: DONE — `tool_registry_manager.py` callback parameters (`dispatch_agent`, `log`, `update_blocker_rollup`, `write_consequence_note`) replaced with direct `Services.*` calls. Callers simplified. Tests updated to use DI overrides.

### ~~106. _config globals bypassing DI container~~ DONE
- **Resolution**: Added `ConfigService` to the DI container exposing `agent_name` and `db_sh` properties. Removed all direct `from _config import` statements from 6 production files (`section_communicator.py`, `pipeline_orchestrator.py`, `impact_analyzer.py`, `section_dispatcher.py`, `pipeline_control.py`, `prompt_formatters.py`) and 1 test file. `_config.py` is now imported only by `ConfigService.__init__` in `containers.py`. Tests override config via `Services.config.override()`.

### ~~107. Temporal coupling in state machines — ordering not enforced~~ CLOSED
- **Resolution**: Won't fix — the temporal ordering IS the logic. `pre_hashes` snapshot-before-diff-after in `implementation_cycle.py` has a single call site that always computes first. Encapsulating into a context manager adds complexity without preventing misuse. `resolve_readiness`, `build_strategic_state`, and `run_intent_triage` are pipeline-stage ordering dependencies where fail-open/fail-closed defaults are correct behavior — the system gracefully degrades when upstream artifacts don't exist yet. Adding pre-condition assertions would create brittle coupling to pipeline ordering.

### ~~108. Lifecycle management gaps — sys.exit bypasses finally blocks~~ DONE
- **Resolution**: Replaced all `sys.exit(0)` in library code with `raise PipelineAbortError("abort received")` — `pipeline_state.py` (2 sites) and `message_poller.py` (1 site). Added `PipelineAbortError` to `orchestrator/types.py`. Caught in `pipeline_orchestrator.py:main()` so `finally` block (mailbox cleanup) now executes on abort. Added `ControlSignal.ABORT` enum value, replacing `"abort"` magic strings in `pipeline_state.py` and `message_poller.py`.

### ~~109. Overloaded contracts — behavioral modes hidden behind None params~~ CLOSED
- **Resolution**: Instance 4 (`build_strategic_state`) fixed — planspace now required. Instance 2 (`_extract_problems`) fixed — callers now pass `.output` correctly. Instance 1 (`dispatch_agent`) won't fix — optional parameters are independent feature gates (context sidecar, QA intercept, monitoring, logging) that layer on a single dispatch path, not distinct behavioral modes. All production code routes through a single DI wrapper. Splitting into 5 functions would push the "which variant" decision to callers, which is worse. Instance 3 (`submit_fanout`) won't fix — `gate=None` is required by the `FanoutAction` schema for agent-emitted task requests that don't specify convergence gates.

### ~~110. Defensive mkdir proliferation — 100 calls with no directory contracts~~ DONE
- **Resolution**: Added `PathRegistry.ensure_artifacts_tree()` which creates all 31 standard artifact directories upfront. Called once during pipeline init (`pipeline_orchestrator.py`) and in test fixtures (`conftest.py`). Removed 30 redundant `mkdir(parents=True, exist_ok=True)` calls across 20 production files. Updated 25 test files to use `ensure_artifacts_tree()` instead of manual directory creation. Remaining ~27 `.parent.mkdir()` calls are for ad-hoc file paths (correct pattern — create parent when writing a specific file).

### ~~111. Dead module: `dispatch/service/output_adjudicator.py` has zero callers~~ CLOSED
- **Resolution**: Not dead — `adjudicate_agent_output` is imported and called by `tests/integration/test_agent_templates.py` which validates prompt structure generation. Zero production callers, but it's a reusable dispatch utility extracted per #2 that tests verify works correctly. Deleting it would also delete the test coverage.

### ~~112. Dead functions in `intent/service/expansion_facade.py`: 3 private wrappers never called~~ DONE
- **Category**: Dead code
- **Source**: Rescan R119
- **What**: `_run_problem_expander()`, `_run_philosophy_expander()`, `_adjudicate_recurrence()` — zero callers. Only the two public functions are consumed. 56 lines dead.
- **Status**: DONE — removed 3 dead wrappers (56 lines).

### ~~113. Dead imports: 4 unused imports across production files~~ DONE
- **Resolution**: All 4 dead imports removed or verified as already cleaned in prior cycles.

### ~~114. Dead re-exports in `flow/service/flow_facade.py`: 2 names never imported~~ DONE
- **Category**: Dead code
- **Source**: Rescan R119
- **What**: `build_gate_aggregate_manifest` and `build_result_manifest` re-exported but never consumed via the facade.
- **Status**: DONE — removed dead re-exports. Test updated to import from canonical source (`flow.engine.reconciler`).

### ~~115. `proposal/engine/proposal_cycle.py` grown to 766 lines~~ DONE
- **Category**: God module
- **Source**: Rescan R119
- **What**: Was 519 lines at #43 closure ("acceptable"). Now 766 lines, 16 functions, `run_proposal_loop()` 155 lines with 19 `return None` paths. Mixes loop orchestration, model selection, prompt construction, alignment checking, and surface handling.
- **Status**: DONE — decomposed from 766 to 183 lines. Extracted 4 new modules: `proposal/service/cycle_control.py` (dispatch, budget, signal handling), `proposal/service/alignment_handler.py` (alignment checking), `proposal/service/surface_handler.py` (surface management), `proposal/service/proposal_prep.py` (prompt/model preparation).

### ~~116. `dispatch_fn: Callable` callback should use DI container (8 remaining sites)~~ DONE
- **Category**: DI gap / unnecessary indirection
- **Source**: Rescan R119
- **Where**: `risk/engine/risk_assessor.py` (5 sites), `proposal/engine/proposal_phase.py` (1), `implementation/engine/implementation_phase.py` (2). Every caller passes `Services.dispatcher().dispatch`. Same pattern fixed in #105 for `tool_registry_manager.py`.
- **Status**: DONE — all 8 `dispatch_fn` parameters removed. Functions now call `Services.dispatcher().dispatch` internally. Tests updated to use DI overrides.

### ~~117. `logger: Callable` callback threaded through 11 functions~~ DONE
- **Category**: DI gap / unnecessary indirection
- **Source**: Rescan R119
- **Where**: `pipeline_state.py`, `message_poller.py`, `mailbox_service.py`, `monitor_service.py`, `flow_signal_parser.py`, `alignment_collector.py`. Every caller passes `Services.logger().log`.
- **Status**: DONE — all 11 `log`/`logger` callback parameters removed. Functions now call `Services.logger().log` internally. 5 caller files and 5 test files updated.

### ~~118. Silent `except Exception` without logging: 2 regression sites from #36~~ DONE
- **Category**: Error handling
- **Source**: Rescan R119
- **Where**: `risk/engine/risk_assessor.py:267` (catches dispatch exception, returns None silently), `flow/service/notifier.py:100` (catches any exception, silently `pass`es).
- **Status**: DONE — both sites now log before failing open.

### ~~119. 22 inlined prompt f-strings remain across 15 files~~ DONE
- **Category**: Separation of concerns
- **Source**: Rescan R119
- **Worst**: `intent_pack_generator.py:209` (76 lines), `philosophy_bootstrapper.py:775` (68 lines), `assessment_evaluator.py:33` (62 lines), `section_reexplorer.py:46` (62 lines). Template system exists but is used inconsistently.
- **Status**: DONE — Cycle 8 extracted 22+ inlined prompts into dedicated `_compose_*_text()` builder functions across 15+ files. Cycle 12: extracted final 3 inlined prompts from `philosophy_bootstrapper.py` (bootstrap guidance 41 lines, source selector 68 lines, source verifier 40 lines). The 4th prompt (`_build_distiller_prompt`) was already a dedicated builder function.

### ~~120. `build_prompt_context()` is a 266-line god function~~ DONE
- **Category**: God function
- **Source**: Rescan R119
- **Where**: `dispatch/prompt/context_builder.py:17`. 25+ key dict, ~50 file-existence checks, path resolutions, and conditional reads in one body.
- **Status**: DONE — decomposed into 10 private helpers (largest 43 lines). Main function now 34 lines.

### ~~121. DI container bypass in `signal_checker.py` and `section_dispatcher.py`~~ DONE
- **Category**: DI gap
- **Source**: Rescan R119
- **What**: Direct imports of `read_signal_tuple` and `validate_dynamic_content` instead of `Services.*` in files that also use `Services` for other calls. Tests overriding providers won't affect these call sites.
- **Status**: DONE — both replaced with `Services.signals().read_tuple()` and `Services.prompt_guard().validate_dynamic()` respectively.

### ~~122. `gate_repository.py` raw `sqlite3.connect()` with manual PRAGMA boilerplate~~ DONE
- **Category**: Abstraction gap
- **Source**: Rescan R119
- **Where**: `flow/repository/gate_repository.py:127-248`. Sole remaining instance of old pattern after #29 created `task_db()`.
- **Status**: DONE — replaced with `task_db()` context manager. Last raw `sqlite3.connect()` in production code eliminated.

### ~~123. 49 production modules have zero test imports (coverage gap)~~ DONE
- **Category**: Test coverage
- **Source**: Rescan R119
- **Key untested**: `implementation_cycle.py` (535 lines), `proposal_cycle.py` (766 lines), `plan_executor.py` (460 lines), `governance_packet_builder.py` (477 lines), `expansion_orchestrator.py` (260 lines), `global_alignment_rechecker.py` (138-line function).
- **Status**: DONE — All 6 key modules now have test coverage: `test_implementation_loop.py`, `test_proposal_loop_research_surfaces.py` + `test_lightweight_escalation.py`, `test_governance_loader.py` + `test_flow_reconciler.py`, `test_governance_packet.py`, `test_proposal_loop_research_surfaces.py`, `test_global_alignment_recheck.py`.

### ~~124. Long if/elif chains — should use dict dispatch or match (CODE-B6)~~ DONE
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
- **Status**: DONE — 4 pure-lookup chains converted to dict dispatch. Remaining 5 are not pure lookups (complex logic, conditional construction, state transitions) — dict dispatch would not simplify them.

### ~~125. Deep nesting exceeds 4 levels (CODE-B4)~~ DONE
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
- **Status**: DONE — All real nesting depth >4 violations fixed. Cycle 12: `validate_proposal_state` (5→3 via guard clause), `find_entry_span` (5→4 via guard clauses), `_field_map` (5→4 via continue guards). `build_pending_surface_payload` reports depth 5 in AST but is a flat if/elif chain — Python AST represents elif as nested If nodes (`For > If > If > If > If`), not real code nesting. `philosophy_bootstrapper.py` depth 6 eliminated by decomposition into 11 helpers.

### ~~126. Boolean parameter clusters — hidden behavioral modes (CODE-B1)~~ DONE
- **Category**: Code anatomy / API design
- **Source**: Expanded reviewer scan R120 (CODE-B1 from code-anatomical-review)
- **Worst offenders**:
  1. `risk/service/engagement.py:8` `determine_engagement()` — 8 boolean parameters (`has_shared_seams`, `has_consequence_notes`, `has_stale_inputs`, `has_recent_failures`, `has_tool_changes`, `freshness_changed`, `has_decision_classes`, `has_unresolved_value_scales`). Should be a dataclass like `EngagementContext`.
  2. `signals/service/database_client.py` — 8 functions all taking `check: bool` parameter. Same boolean threaded uniformly.
  3. `intent/service/philosophy_bootstrapper.py:414` `_request_user_philosophy()` — `overwrite_decisions: bool` among 15 total parameters.
- **Scale**: 30 boolean parameters across function definitions.
- **Risk**: Boolean params create invisible behavioral modes. Callers must know what `True` means at each site. Replace with enums, dataclasses, or split into separate functions.
- **Status**: DONE — `determine_engagement()` refactored from 12 params (8 bools) to 5 params using `EngagementContext` frozen dataclass. `database_client.py` `check: bool` is a standard `subprocess.run(check=)` passthrough — uniform and transparent, not a hidden behavioral mode. `_request_user_philosophy()` `overwrite_decisions: bool` is the only call-site-specific boolean remaining (2 callers), acceptable for an internal helper.

### ~~127. Silent exception swallowing in `scripts/log_extract/utils.py:42`~~ DONE
- **Category**: Error handling (CODE-E2)
- **Source**: Expanded reviewer scan R120 (CODE-E2 from code-bug-review)
- **What**: `except Exception: continue` silently skips malformed TOML model config files during parsing. No logging, no warning.
- **Also**: `scripts/log_extract/extractors/common.py:98,129` — conditional silent failures (only log if `source_label` is set).
- **Risk**: Malformed config files are silently ignored, making debugging configuration issues difficult.
- **Status**: DONE — `utils.py` now prints warning to stderr before `continue`. `common.py` logging made unconditional with `label = source_label or "log_extract"` fallback.

### ~~128. 57 functions exceed 100 lines (CODE-S2 FAIL inventory)~~ DONE
- **Category**: Code style / function length
- **Source**: Expanded reviewer scan R120 (CODE-S2 from code-style-review)
- **Top 5**: `ensure_global_philosophy()` 923 lines, `run_substrate_discovery()` 338 lines, `dispatch_task()` 249 lines, `analyze_impacts()` 243 lines, `handle_tool_friction()` 232 lines.
- **Scale**: Originally 57 functions >100 lines (FAIL). After Cycles 4-9: **1 function remains** (56 brought under 100). Cycle 8 decompositions: `run_substrate_discovery` (334→sub-100, extracted 5 phase helpers), `run_proposal_loop` (134→77 lines, extracted `_dispatch_and_validate_proposal` + `_run_alignment_phase`), `run_implementation_pass` (122→sub-100, extracted `_implement_section` + `_prepare_risk_plan` + `_handle_failed_impl`), `_iter_file_events` (119→sub-100, extracted per-record-type handlers), `_run_frontier_iterations` (112→54 lines, extracted `_execute_frontier_slice`), `validate_philosophy_grounding` (111→sub-100, extracted source map validation helpers). Cycle 9: reduced 4 more functions in 80-97 range: `_write_alignment_surface` (97→22, data-driven via `_collect_surface_entries`), `_run_freshness_check` (96→69, extracted `_interpret_freshness_signal`), `_run_bridge_for_group` (91→58, extracted `_ensure_contract_delta`), `_write_traceability_index` (completed extraction of `_collect_alignment_verdicts` + `_optional_artifact`, now 45 lines).
- Cycle 10: Reduced `route_blockers` (81→22, extracted `_route_user_root_questions`, `_route_shared_seams`, `_route_unresolved_contracts`), `_recheck_section_alignment` (78→60, extracted `_classify_alignment_result`), `run_aligned_expansion` (72→52, extracted `_handle_budget_exhaustion`), `validate_risk_plan` (72→57, extracted `_validate_accept_decision`).
- Cycle 11: Decomposed `ensure_global_philosophy` (918 lines, 215 exec) → 12 exec-line coordinator + 10 phase helpers (max 37 exec lines each). Introduced `_BootstrapContext` dataclass for shared state. Phase loop pattern: each helper returns `dict | None` — non-None short-circuits the coordinator. Helpers: `_check_philosophy_freshness`, `_resolve_source_records`, `_run_source_selector`, `_run_extension_pass`, `_run_source_verifier`, `_build_verification_shortlist`, `_validate_selected_sources`, `_build_distiller_prompt`, `_run_distiller`, `_handle_distiller_failure`, `_finalize_philosophy`.
- Cycle 17: Decomposed 3 remaining 100+ exec-line functions in `philosophy_bootstrapper.py`: `_run_source_selector` (117→23, extracted `_handle_selector_empty` + `_handle_selector_failure`), `_run_source_verifier` (102→25, extracted `_handle_verifier_empty` + `_handle_verifier_failure`), `_handle_distiller_failure` (102→15, extracted `_handle_distiller_empty_user_source` + `_handle_distiller_empty_repo_source`). Extracted 3 modules from bootstrapper (1744→1188 lines): `philosophy_prompts.py` (4 prompt text composers, 237 lines), `philosophy_bootstrap_state.py` (constants, path helpers, signal/status writers, 166 lines), `philosophy_grounding.py` (grounding validation + sha256_file, 230 lines). Dead code cleanup: removed unused `integration_proposal` variable in `proposal_cycle.py`, removed dead `_invalidate_excerpts`/`_check_and_clear_alignment_changed` wrappers from `pipeline_control.py`, removed dead `_summary_tag` wrapper from `section_communicator.py`.
- **Status**: DONE — all functions under 50 exec lines (1 at exactly 50 in scripts/log_extract/cli.py).

### ~~129. 60 functions have 8+ parameters (CODE-S3 FAIL inventory)~~ DONE
- **Category**: Code style / parameter count
- **Source**: Expanded reviewer scan R120 (CODE-S3 from code-style-review)
- **Top 5**: `submit_task()` 18 params, `_request_user_philosophy()` 15 params, `_dispatch_classified_signal_stage()` 13 params, `_write_prompt()` 13 params, `handle_tool_friction()` 12 params.
- **Overlap**: Subsumes #93 (long parameter lists). This is the precise inventory.
- **Status**: DONE — Down from 128+ to 24 irreducible (4 at 9 params, 20 at 8 params — all verified as distinct data values with no derivability). See cycles 16-19 for reduction details.

### ~~130. Broad `except Exception` without `# noqa: BLE001` (CODE-E1)~~ DONE
- **Category**: Error handling / exception specificity
- **Source**: Expanded reviewer scan R120 (CODE-E1 from code-bug-review), Cycle 9 rescan
- **Scale**: 11 `except Exception` catches across risk_assessor.py, task_dispatcher.py, qa_gate.py (×2), qa_interceptor.py, adjudicator.py, plan_executor.py, common.py (×2), gemini.py (×2), utils.py. All are intentional fail-open handlers with logging — legitimate broad catches that needed `# noqa: BLE001` annotation.
- **Status**: DONE — All 11 catches annotated with `# noqa: BLE001` and purpose comments in Cycle 9. Cycle 10: Fixed 1 additional missed catch in `flow/service/notifier.py:101`. Codebase also clean on CODE-E11 (mutable defaults), CODE-B7 (guard clauses), CODE-S9 (dead code/unreachable code).

### ~~131. Duplicate status/signal string literals (CODE-S8)~~ DONE
- **Category**: Code style / magic strings
- **Source**: Cycle 9 expanded reviewer scan
- **Worst offenders**: `"ALIGNMENT_CHANGED_PENDING"` (32 occurrences, 20 files), `"needs_parent"` (39 occurrences, 18 files), `"why_blocked"` (34 occurrences), `"-output.md"` / `"-prompt.md"` suffix patterns (41/32 occurrences).
- **Risk**: String literals duplicated across many files. Typo in one file = silent behavioral divergence. `ALIGNMENT_CHANGED_PENDING` is partially mitigated by `DispatchResult.__eq__` backward-compat.
- **Status**: DONE — Cycle 9-10: extracted magic numbers to constants. Cycle 12: consolidated all high-risk string literals:
  - `ALIGNMENT_CHANGED_PENDING` constant in `dispatch/types.py` (34 literals → 1 constant, 20 files)
  - `ControlSignal.ALIGNMENT_CHANGED` enum usage expanded (9 literals → enum, 7 files)
  - `SIGNAL_NEEDS_PARENT`, `SIGNAL_NEED_DECISION`, `SIGNAL_OUT_OF_SCOPE`, `BLOCKING_NEEDS_PARENT`, `BLOCKING_NEED_DECISION` constants in `signals/types.py` (~60 literals → 5 constants, 23 files)
  - `ACTION_CONTINUE`, `ACTION_ABORT`, `ACTION_SKIP` for triage/proposal control flow (~30 literals → 3 constants, 16 files)
  - `ALIGNMENT_INVALID_FRAME` for alignment checker results (3 literals → 1 constant, 3 files)
  - `PASS_MODE_PROPOSAL`, `PASS_MODE_IMPLEMENTATION` for section pipeline (7 literals → 2 constants, 6 files)
  - Remaining: `"why_blocked"` (dict key, low risk), `"-output.md"`/`"-prompt.md"` suffixes (path construction, low risk).

### ~~132. Expanded reviewer category scan (Cycle 10)~~ DONE
- **Category**: Multi-category scan
- **Source**: External reviewer configs at `/mnt/c/Users/xteam/IdeaProjects/ai-workflow/.ai/agents/20-code-artifact/reviewers`
- **Scanned**: CODE-B6 (long if/elif chains ≥5 branches), CODE-E5 (resource cleanup / open without context manager), CODE-E10 (assertion misuse for runtime validation), CODE-S1 (module-level constant naming)
- **Status**: DONE — All 4 categories clean. Max if/elif chain is 4 branches. All sqlite3.connect() and open() calls properly managed. Zero assert statements in src/. All module-level constants use UPPER_SNAKE_CASE.

### ~~133. Cycle 10 continued — decompositions, magic numbers, dead code~~ DONE
- **Category**: CODE-S2 (function length), CODE-S8 (magic numbers), dead code removal
- **Decompositions** (5 functions):
  - `readiness_gate.resolve_and_route`: extract `_build_proposal_pass_result` (eliminates duplicate ProposalPassResult construction)
  - `global_alignment_rechecker._recheck_section`: extract `_apply_alignment_outcome` (problem extraction + signal check phase)
  - `task_request_ingestor.ingest_and_submit`: extract `_submit_chain_action` / `_submit_fanout_action`
  - `substrate_discoverer._run_pruning`: extract `_validate_pruner_outputs` (3 failure paths with shared status writes)
  - `signal_reader.read_signal_tuple`: replace 6-way state if-chain with `_SIGNAL_STATE_MAP` dict lookup
- **Magic numbers** (13 constants extracted across 5 files):
  - `correlator.py`: 8 scoring weights (`_SCORE_PROMPT_SIGNATURE`, `_SCORE_TIME_CLOSE/NEAR/MODERATE`, `_SCORE_CWD_EXACT/BASENAME`, `_SCORE_MODEL_EXACT/COMPATIBLE`)
  - `risk_artifact_writer.py`: `_RISK_ITERATIONS_BASE` / `_RISK_ITERATIONS_CAP`
  - `history.py`: `_SURPRISE_SCORE_WEIGHT` / `_SURPRISE_SCORE_CAP`
  - `proposal_phase.py`: `_RISK_SEVERITY_BLOCKER_THRESHOLD`
  - `pipeline_state.py`: `_PAUSE_POLL_TIMEOUT_SECONDS`
- **Dead code removed**:
  - `traceability_writer._verify_traceability` (71 lines) — defined but never called
  - `pipeline_control._set_alignment_changed_flag` — dead wrapper + unused import
- **Status**: DONE

### ~~134. Cycle 10 continued — hash constants, governance decomposition, remaining magic numbers~~ DONE
- **Category**: CODE-S8 (magic numbers), CODE-S2 (function length)
- **Hash truncation constants** (10 constants across 8 files):
  - `results.py`: `_TITLE_SLUG_MAX_LENGTH` (40) / `_TITLE_HASH_LENGTH` (8)
  - `assessment_evaluator.py`: `_DEBT_KEY_HASH_LENGTH` (16)
  - `surface_registry.py`: `_FINGERPRINT_LENGTH` (12)
  - `readiness_gate.py`: `_CANDIDATE_HASH_LENGTH` (8) / `_TRIGGER_HASH_LENGTH` (12)
  - `blocker_manager.py`: `_SEAM_HASH_LENGTH` (12)
  - `freshness_calculator.py`: `_FRESHNESS_HASH_LENGTH` (16)
  - `plan_executor.py`: `_NOTE_FINGERPRINT_LENGTH` (12)
  - `completion_handler.py`: `_NOTE_HASH_LENGTH` (12)
- **Function decompositions** (2 files):
  - `governance_loader.build_governance_indexes`: data-driven refactoring — 6 identical try/except blocks → `_parse_governance_indexes` loop over `_GOVERNANCE_INDEX_SPECS`
  - `problem_resolver._collect_scope_delta_problems`: extract `_resolve_delta_linked_sections`
- **Timeout/truncation constants** (8 constants across 5 files):
  - `agent_executor.py`: `_DEFAULT_AGENT_TIMEOUT_SECONDS` (600)
  - `section_dispatcher.py`: `_SECTION_DISPATCH_TIMEOUT_SECONDS` (1800)
  - `notifier.py`: `_NOTIFY_SUBPROCESS_TIMEOUT_SECONDS` (10)
  - `governance_packet_builder.py`: `_PROBLEM_FRAME_TRUNCATION` (2000)
  - `log_extract_helpers.py`: `_PROMPT_SIGNATURE_TRUNCATION` (4000)
  - `analyzer.py`: `_PATH_TOKEN_MAX_LENGTH` (80) / `_SOURCE_HASH_LENGTH` (10)
  - `intent_initializer.py`: `_SECTION_SUMMARY_TRUNCATION` (500)
  - `intent_triager.py`: `_SUMMARY_SNIPPET_TRUNCATION` (500)
- **Display truncation constants** (5 constants across 11 files): `TRUNCATE_DETAIL` (200), `TRUNCATE_SUMMARY` (80), `TRUNCATE_MEDIUM` (120), `TRUNCATE_REASON` (150), `TRUNCATE_TOKEN` (8) — 15 magic-number slice limits replaced.
- **Status**: DONE

### ~~135. Cycle 11 — full rescan, deduplication, reviewer category sweep~~ DONE
- **Category**: Multi-category rescan
- **Rescan results** (CODE-S2, CODE-S8, CODE-E1, CODE-E11, CODE-B4, CODE-B6, CODE-E2, CODE-E5, CODE-E10):
  - **CODE-S2 (function length)**: `ensure_global_philosophy` decomposed (215 exec → 12 exec coordinator + 10 helpers, max 37 each). `run()` CLI entry point (51, borderline). All under 50. Clean.
  - **CODE-S8 (magic numbers)**: All hash truncation, timeout, and content truncation constants extracted. Remaining `[:N]` are display-only in log/error messages. Clean.
  - **CODE-E1 (exception specificity)**: Zero `except Exception` without `# noqa: BLE001`. Zero bare `except:`. Clean.
  - **CODE-E11 (mutable defaults)**: Zero `def func(items=[])` patterns. Clean.
  - **CODE-E2 (exception swallowing)**: All `except ... pass` patterns are intentional fail-open with specific exception types (OSError, json.JSONDecodeError, etc.) and fallback paths. Clean.
  - **CODE-E5 (resource cleanup)**: All `open()` calls use `with` context managers. Clean.
  - **CODE-E10 (assert misuse)**: Zero `assert` statements in production code. Clean.
  - **CODE-B4 (nesting depth)**: `philosophy_bootstrapper` decomposition eliminated depth >4 nesting. Clean.
  - **CODE-B6 (if/elif chains)**: Max chain is 3 branches. All pure lookups already converted. Clean.
  - **Dead functions**: 25 candidates reported by scanner, all verified as false positives (registry-dispatched, test-imported, or method calls on self).
  - **Import-time side effects**: Only legitimate patterns (route decorators, CLI entry points, type registration).
- **Deduplication**:
  - `_unique_strings()` was copy-pasted in `risk_artifact_writer.py` and `risk_history_recorder.py`. Now imports from canonical location.
- **Status**: DONE — codebase clean across all CODE-* categories

### ~~136. Cycle 13 — dead code, magic numbers, parameter reduction~~ DONE
- **Category**: Multi-category rescan and cleanup
- **Dead code removed**:
  - `_inline_json_block()` in `risk/prompt/writers.py` — private function, test verified it was NOT used. Removed function + test + dead `json` import + dead `risk_prompt_writers` test import.
  - 4 dead imports from `risk/service/quantifier.py` (removed in Cycle 12 continuation): `deepcopy`, `Path`, `Any`, `Services`.
  - `all_agent_files()` from `taskrouter/agents.py` — zero production callers.
  - `DispatchResult.succeeded` property — zero callers.
  - 9 dead `PathRegistry` methods: `flow_continuation`, `flow_result_manifest`, `source_inventory`, `candidate_claims`, `hypothesis_sets`, `verification_packet_json`, `verification_packet_md`, `verification_receipts`, `value_scales`.
  - Dead module `flow/helpers/task_parser.py` + `flow/helpers/` directory + test.
- **Magic numbers extracted** (16 constants across 7 files):
  - `philosophy_bootstrapper.py`: `_MAX_SECTION_SPECS`, `_MAX_PROPOSALS`, `_MAX_DECISIONS`, `_MAX_NOTES`, `_MAX_FILE_EXTENSION_LENGTH`, `_MAX_DISTILLER_ATTEMPTS`
  - `philosophy_catalog.py`: `_PREVIEW_START_LINES`, `_PREVIEW_CONTEXT_BEFORE`, `_PREVIEW_CONTEXT_AFTER`, `_CODESPACE_QUOTA_NUMERATOR/_DENOMINATOR`
  - `governance_packet_builder.py`: `_MIN_TERM_LENGTH`, `_MAX_KEYWORDS_IN_BASIS`, `_MAX_BASIS_PARTS`
  - `microstrategy_decider.py`: `_TODO_CONTEXT_BEFORE`, `_TODO_CONTEXT_AFTER`
  - `monitor_service.py`: `_MONITOR_WAIT_TIMEOUT`, `_MIN_SIGNAL_LOG_FIELDS`, `_SIGNAL_LOG_TRUNCATION`
  - `pipeline_orchestrator.py`: `_MAX_BLOCKERS_IN_SUMMARY`
- **Parameter count reduction** (~35 functions reduced across 4 rounds):
  - Round 1 (Cycle 12): 11 functions — `paths: PathRegistry` removed from proposal system
  - Round 2 (Cycle 12): coordination + plan_executor `paths`/`policy` removed
  - Round 3 (Cycle 13): `_request_user_philosophy` (11→10), `_apply_and_finalize` (13→10), `_run_pruning` (10→8), `_compose_microstrategy_text` (10→8), `_build_microstrategy_prompt` (7→6)
  - Round 4 (Cycle 13): `_dispatch_new_tool_validation` (9→7), `_dispatch_post_impl_repair` (8→6), `_dispatch_registry_repair` (8→7), `_dispatch_and_retry` (9→8), `_try_escalation` (9→8), `_reexplore_missing_files` (8→7)
- **AST verification**: 0 functions >50 exec lines, 0 real nesting depth >4 violations (1 false positive: flat elif chain in AST).
- **Status**: DONE

### ~~137. Cycle 14 — comprehensive magic number extraction, parameter reduction, dead code~~ DONE
- **Category**: Multi-category rescan and cleanup
- **Dead code removed**:
  - `_validate_philosophy_grounding()` wrapper in `intent_pack_generator.py` — private, zero callers. Dead `_validate_grounding` import also removed.
- **Magic numbers extracted** (30+ constants across 20+ files):
  - `MAX_RESIDUAL_RISK` (100) shared constant in `risk/types.py` — used by `fallback.py`, `risk_assessor.py`, `risk_history_recorder.py`, `history.py`
  - Budget defaults: `_DEFAULT_PROPOSAL_MAX`, `_DEFAULT_IMPLEMENTATION_MAX`, `_DEFAULT_EXPANSION_MAX`, `_DEFAULT_MAX_NEW_SURFACES`, `_DEFAULT_MAX_NEW_AXES`, `_DEFAULT_RISK_BUDGET_HINT` in `intent_triager.py`
  - `_DEFAULT_PROPOSAL_MAX`, `_DEFAULT_IMPLEMENTATION_MAX` in `intent_initializer.py`
  - `_DEFAULT_RISK_ITERATIONS`, `_DEFAULT_HISTORY_ADJUSTMENT_BOUND` in `risk_assessor.py`
  - `_MAX_PARALLEL_FIX_WORKERS` in `plan_executor.py`
  - `_SUBSTRATE_AGENT_TIMEOUT_SECONDS` in `substrate_dispatcher.py`
  - `_DB_BODY_COLUMN_INDEX`, `_DB_MIN_COLUMNS` in `pipeline_state.py`
  - `_SIGNAL_BODY_COLUMN_INDEX` in `monitor_service.py`
  - `_DEFAULT_OUTCOME_SCORE` in `history.py`
  - `_DEFAULT_CATALOG_MAX_FILES/_MAX_SIZE_KB/_MAX_DEPTH` in `philosophy_catalog.py`
  - `_DEFAULT_AXIS_BUDGET` in `expanders.py`
  - `_DEFAULT_SUMMARY_MAX_LENGTH` in `signal_checker.py`
  - `_DEFAULT_SUMMARIZE_LIMIT`, `_ELLIPSIS_LENGTH` in `log_extract_helpers.py`
  - `_DEFAULT_POLL_INTERVAL_SECONDS` in `task_dispatcher.py`
  - `_MAX_SUMMARY_LINES` in `match_updater.py`
  - `_MAX_README_FILES_PER_DIR`, `_MAX_STALE_SOURCES_IN_MESSAGE` in `philosophy_bootstrapper.py`
  - `RiskModifiers` deserializer now references dataclass defaults instead of duplicating literals
  - Used existing `_RISK_SEVERITY_BLOCKER_THRESHOLD` instead of raw `3` in `proposal_phase.py`
  - Used existing `_RISK_ITERATIONS_BASE` instead of raw `5` in `risk_artifact_writer.py`
- **Parameter reduction** (8 functions, 3 patterns):
  - `_submit_fanout`: removed `paths: PathRegistry` (8→7)
  - `sec_num` derivable from `section.number`: `_execute_frontier_slice` (9→8), `_run_frontier_iterations` (8→7), `_run_alignment_check_with_retries` (9→8), `run_alignment_check` wrapper (10→9)
  - `artifacts` derivable from `planspace`: `_dispatch_normalizer` (8→7), `_dispatch_and_retry` (8→7)
- **Remaining 8+ param functions**: 60 (down from 64)
- **Status**: DONE

### ~~138. Cycle 15 — parameter reduction, dead code, consolidation scan~~ DONE
- **Category**: Multi-category rescan and cleanup
- **Dead code removed**:
  - Unused `dispatch_agent` import in `deep_scanner.py` (+ test monkeypatch reference)
  - Dead `surfaces` and `surface_count` params in `run_aligned_expansion` (never used in body)
  - Dead `_paths: PathRegistry` params in `_build_updater_prompt` and `_dispatch_updater_and_apply` (never used in body)
  - Dead `artifacts: Path` param in `handle_tool_friction` (never used in body)
- **Parameter reduction** (17 functions, 5 patterns):
  - `artifacts` derivable from `planspace`: `validate_tool_registry_after_implementation` (8→7), `_dispatch_new_tool_validation` (8→7), `_dispatch_post_impl_repair` (7→6), `_validate_tools_post_impl` (8→7)
  - `ticket_id`/`concern_scope`/`problem_id` derivable from `ticket`+`section_number`: `_build_web_branch` (8→5), `_build_code_branch` (8→5), `_build_both_branch` (8→5)
  - `coord_dir` derivable from `planspace`: `_classify_alignment_result` (9→8), `_record_recurrence_resolution` (5→4)
  - `sec_num` derivable from `section.number`: `_recheck_section_alignment` (8→7)
  - `integration_proposal` derivable from `planspace`+`section.number`: `_dispatch_and_validate_proposal` (8→7)
  - Derivable path params: `_try_escalation` (8→5) — prompt/output/signal paths derivable from planspace+section_number
- **Remaining 8+ param functions**: 51 (down from 60)
- **AST verification**: 1 function at 51 exec lines (CLI `run()` — acceptable for entry point), 0 real nesting depth >4 violations
- **Status**: DONE

### ~~139. Cycle 16 — deep redundant parameter sweep, dead code~~ DONE
- **Category**: Multi-category rescan and cleanup
- **Dead code removed**:
  - Dead `corrections_file` param in `_explore_section` (never used, re-derived internally)
  - Dead `apply_related_files_update` import in `section_explorer.py`
  - Dead `adjudicator_model` param in `_run_alignment_check_with_retries` + container wrapper chain (5 call sites)
- **Parameter reduction** (~35 functions across 6 patterns):
  - `adjudicator_model` dead in alignment check chain: `_run_alignment_check_with_retries`, `run_alignment_check` container, 3 callers
  - `sec_num` derivable from `section.number`: `_recheck_section` (8→7), `_run_risk_review` (3→2), `_prepare_risk_plan` (3→2), `_implement_section` (7→5)
  - `paths` derivable from `planspace`: `_persist_section_hashes` (5→4), `_validate_and_dispatch_assessment` (5→4), `_validate_and_dispatch_optimization` (8→7), `_validate_and_dispatch_lightweight_optimization` (7→6), `_check_budget` (7→6), `_dispatch_implementation` (7→5), `_handle_post_dispatch` (7→5), `_dispatch_alignment_check` (5→4), `_extract_alignment_problems` (6→5), `_handle_underspec_signal` (6→5), `_surface_tools` (6→4), `_run_microstrategy_step` (5→4), `_handle_microstrategy_failure` (4→3), `_build_reexplore_prompt` (5→4), `_emit_missing_frame_blocker` (4→3), `_emit_empty_frame_blocker` (4→3), `_validate_frame_content` (4→3), `_handle_budget_exhaustion` (6→5), `_handle_setup_signal` (7→5), `_collect_and_persist_problems` (4→3), `_dispatch_and_parse_plan` (5→4), `_build_coordination_plan` (4→3), `_validate_plan` (5→4), `_run_phase2` (7→6), `_collect_bootstrap_context_artifacts` (3→2), `_run_bootstrap_prompter` (4→3)
  - Context builder protocol simplified: 5 inner `_build_context` functions (4→3 params each)
  - Fixed latent `NameError` for `coord_dir` in `_build_coordination_plan`
- **Remaining 8+ param functions**: 49 (down from 51)
- **Status**: DONE

### ~~140. Cycle 16 continued — dead parameter sweep across flow, governance, pipeline~~ DONE
- **Category**: Dead code / parameter reduction
- **Dead params removed (Cycle 16b)**:
  - `artifacts: Path` dead in `_check_upstream_freshness` — propagated removal through `_run_section_implementation_steps`, `_run_implementation_pass`, and `run_section` (entire artifacts threading chain was dead)
  - `codespace: Path` dead in `write_bridge_prompt` (coordination/prompt/writers.py)
  - `model_policy: dict | None` dead in `write_integration_proposal_prompt` and `write_strategic_impl_prompt`
  - `codespace: Path` dead in `_write_traceability_index`
  - `task_id: int` dead in `build_flow_context` (5→4 params)
  - `planspace: Path` dead in `bootstrap_governance_if_missing`
  - `registry: PathRegistry` dead in `_check_prerequisites` (substrate_discoverer.py)
- **Remaining 8+ param functions**: 48 (down from 49)
- **Status**: DONE

### ~~141. Cycle 16c — remaining del-marked dead params and stale imports~~ DONE
- **Category**: Dead code / parameter reduction
- **Dead params removed**:
  - `task_type: str` dead in `record_task_routing` and `record_qa_intercept` (flow/service/notifier.py)
  - `parent: str` dead in `route_blockers` (proposal/engine/readiness_gate.py)
  - `assume_tz: str` dead in `parse_timestamp` (dispatch/helpers/log_extract_helpers.py)
  - `queue: list[str]` + `completed: set[str]` dead in `handle_pending_messages` — propagated through full chain: message_poller → pipeline_control → container → 5 production callers + 2 test stubs
  - `codespace: Path` dead in `section_inputs_hash` — propagated through container, `requeue_changed_sections`, `global_alignment_rechecker`, `_persist_section_hashes`
  - `codespace: Path` dead in `write_post_impl_assessment_prompt`, `build_section_governance_packet`, `_handle_post_impl_assessment_completion`, `_dispatch_post_impl_assessment`
  - `impl_completed: set[str]` newly dead in `_check_abort_conditions` after handle_pending_messages simplification — removed param and dead variable in caller
- **Stale imports removed**: `Services` and `RiskAssessment` in risk/prompt/writers.py (orphaned by prior del-param removals)
- **Remaining 8+ param functions**: 48 (unchanged — these removals were on <8 param functions)
- **Status**: DONE

### ~~142. Cycle 16d — dead variables, redundant params, string constants, db_path internalization~~ DONE
- **Category**: Dead code / parameter reduction / type safety
- **Dead variable assignments removed** (8 sites across 7 files):
  - 3× `policy = Services.policies().load(planspace)` in tool_registry_manager.py
  - 1× each in expansion_orchestrator.py, proposal_phase.py, expansion_handler.py, research_plan_executor.py, global_alignment_rechecker.py, pipeline_orchestrator.py
- **Redundant PathRegistry params removed** (4 functions):
  - `_collect_note_problems` (problem_resolver.py): removed `planspace`, use `paths.planspace`
  - `_apply_philosophy_expansion` (expansion_orchestrator.py): removed `planspace`, use `paths.planspace`
  - `_finalize_expansion` (expansion_orchestrator.py): removed `planspace`, use `paths.planspace`
  - `_run_shard_exploration` (substrate_discoverer.py): removed `planspace`, use `registry.planspace`
- **Stringly-typed constants extracted** (~45 raw string literals → 5 named constants):
  - `SOURCE_MODE_USER`, `SOURCE_MODE_REPO`, `SOURCE_MODE_NONE` in philosophy_classifier.py
  - `STATE_VALID_NONEMPTY`, `STATE_VALID_EMPTY` in philosophy_classifier.py
  - Replaced across philosophy_bootstrapper.py (~40 sites) and philosophy_classifier.py (~5 sites)
- **Parameter internalization** (`ingest_and_submit` db_path):
  - `db_path` made keyword-only optional param, derived from `PathRegistry(planspace).run_db()` when not provided
  - All 5 production callers simplified (removed `db_path=paths.run_db()`)
  - 17 test calls updated to pass `db_path=` as keyword arg
- **Status**: DONE

### ~~143. Cycle 18 — dead code cleanup, dead parameter sweep, parameter reduction~~ DONE
- **Category**: Dead code / parameter reduction
- **Dead code removed** (6 items across 5 files):
  - `PathRegistry.intake_session_dir` method (path_registry.py)
  - `DispatchEvaluation` dataclass (qa_interceptor.py)
  - `DEFAULT_FAILURE_COOLDOWN` constant (posture.py)
  - `DEFAULT_RISK_PARAMETERS` constant (quantifier.py)
  - `TOUCHPOINTS_ENUM`, `KIND_ENUM` constants (schemas.py)
- **Dead parameters removed** (20+ params across 15 functions):
  - `codespace` dead in: `_handle_post_dispatch`, `_handle_underspec_signal` (implementation_cycle.py), `_persist_section_hashes` (implementation_phase.py), `_check_alignment_and_requeue` (proposal_phase.py)
  - `codespace`+`align_result`+`align_output` dead in `handle_alignment_signals` (alignment_handler.py)
  - `codespace`+`intg_result` dead in `handle_proposal_signals` (cycle_control.py)
  - `impl_align_result` dead in `_handle_underspec_signal`, `impl_result` dead in `_handle_post_dispatch`
  - `sections` dead in `run_global_coordination` (global_coordinator.py)
  - `model`+`section_number` dead in `evaluate_qa_gate` (qa_gate.py, 8→5 params)
  - `assessment`+`package`+`parameters` dead in `_validate_and_dispatch_optimization` and `_validate_and_dispatch_lightweight_optimization` (risk_assessor.py, 6→3 and 6→2)
  - `workflow_home` dead in `resolve_scan_agent_path` (scan_dispatch_config.py)
  - `codespace` dead in `apply_related_files_updates` (related_files.py)
  - `origin_refs` suppressed with `del` in 3 catalog package functions (protocol interface)
- **Parameter reduction** (8+ param functions: 28→25):
  - `block_bootstrap`: merged redundant `status`/`bootstrap_state` (10→9)
  - `post_section_completion`: internalized `impact_model`+`normalizer_model` (8→6)
  - `_compose_microstrategy_text`: derived `integration_proposal`+`microstrategy_path` from PathRegistry (8→6)
  - `_compose_reexplore_text`: derived `output_path` from PathRegistry (8→7)
  - `_build_microstrategy_prompt`: cascade reduction (6→4)
  - `_build_reexplore_prompt`: cascade reduction (4→3)
- **Status**: DONE

### ~~144. Cycle 19 — dead parameter sweep, model internalization, rescan~~ DONE
- **Category**: Dead code / parameter reduction
- **Dead parameters removed** (4 params across 3 functions):
  - `all_sections` dead in `run_coordination_loop` (coordination_controller.py) — cascade through `_run_phase2` (pipeline_orchestrator.py), 5 test callers updated
  - `impl_result` dead in `_handle_post_dispatch` (implementation_cycle.py, 4→3 params) — caller updated
- **Model internalization** (`analyze_impacts`: 9→7 params):
  - `impact_model` and `normalizer_model` now derived internally via `Services.policies().load(planspace)` + `Services.policies().resolve(policy, ...)`
  - Caller `post_section_completion` simplified (removed policy resolution at call site)
  - Dead `policy` variable assignment removed from `post_section_completion`
  - 2 test callers updated to remove `impact_model`/`normalizer_model` kwargs
- **Comprehensive rescan results** (all clean):
  - Zero dead private functions (27 candidates verified as cross-module imports)
  - Zero dead constants
  - Zero dead classes
  - Zero dead variable assignments
  - Zero unused imports (8 known re-exports confirmed)
  - Zero dead parameters across entire codebase (excluding protocol functions)
- **8+ param functions**: 25→24 (5 at 9→4 at 9, 20 at 8 unchanged)
- **Status**: DONE

### ~~145. Cycle 20 — god module decomposition, duplicate elimination, dead param removal~~ DONE
- **Category**: God modules / duplicate code / dead params
- **God module decomposition**:
  - `tool_registry_manager.py` (726 lines) → 3 focused modules:
    - `tool_surface_writer.py` (~200 lines) — tool surfacing and registry repair
    - `tool_validator.py` (~190 lines) — post-implementation validation
    - `tool_bridge.py` (~290 lines) — bridge agent and friction handling
  - `research_plan_executor.py` (539 lines) → split into:
    - `research_branch_builder.py` (~270 lines) — branch building and ticket translation
    - `research_plan_executor.py` (~240 lines) — execution orchestration and flow submission
  - Original `tool_registry_manager.py` deleted (zero imports confirmed)
- **Duplicate code eliminated**:
  - Fence parser: extracted `extract_fenced_block()` to `dispatch/helpers/signal_checker.py`, replacing inline copies in `impact_analyzer.py` and `planner.py`
  - Abort check: removed `_should_abort()` from `implementation_cycle.py` (duplicate of `check_early_abort` in `cycle_control.py`)
- **Dead param removed**:
  - `path` param dead in `_extract_id(record, path)` in `gemini.py` — removed, 2 callers updated
- **Test import hygiene**:
  - `test_coordination.py` updated to import from canonical source modules instead of re-exports via `global_coordinator.py`
- **Files over 500 lines**: 9→8 (`tool_registry_manager.py` eliminated)
- **Additional cleanup** (Cycle 21):
  - Dead re-exports `build_gate_aggregate_manifest`/`build_result_manifest` removed from `flow_facade.py`, test updated to import from canonical `flow.engine.reconciler`
  - `ScanContext.from_artifacts()` factory method added — deduplicates `corrections_path = PathRegistry(artifacts_dir.parent).corrections()` + `ScanContext(...)` construction across 3 files (section_explorer, deep_scanner, feedback_collector)
  - `_unique_strings` promoted to public `unique_strings` — was private function imported cross-module by `risk_history_recorder.py`
  - `handle_pause_response()` extracted to `cycle_control.py` — deduplicates 7-line pause/resume/abort flow from `cycle_control.py` and `alignment_handler.py`
  - `_should_abort()` in `implementation_cycle.py` eliminated — was exact duplicate of `check_early_abort` in `cycle_control.py`
  - `_build_consequence_note` reduced from 9→8 params: internalized `paths` (derived from `planspace`) and `snapshot_dir` (derived from `PathRegistry.snapshot_section`)
  - Unused import `ACTION_ABORT`/`ACTION_CONTINUE` removed from `alignment_handler.py` (became dead after `handle_pause_response` extraction)
- **8+ param functions**: 5 at 9 params → 3 at 9 params
- **Status**: DONE

### ~~146. Magic strings across 18+ domains should be enums~~ DONE
- **Category**: Type safety / magic strings (methodology §15)
- **Source**: Comprehensive string literal scan (Cycle 22)
- **Scale**: 18+ distinct string domains used as protocol tokens, status values, and mode selectors without enum types. Many have partial constant extraction (#131) but no enum.
- **Domains identified**:
  1. ~~**Bridge status**~~ DONE — `"bridged"`, `"no_action"`, `"needs_parent"` typed via `BridgeSignal.status: Literal[...]` (see #148). `"failed"`, `"handled"` are write-only artifact values with no code comparisons — dismissed
  2. ~~**Coordination strategy**~~ DONE — `CoordinationStrategy(str, Enum)` in `coordination/types.py`
  3. ~~**Assessment verdicts**~~ DONE — `AssessmentVerdict(str, Enum)` in `assessment_evaluator.py`; consumers in `reconciler.py` migrated
  4. ~~**QA verdicts**~~ DONE — `Verdict(str, Enum)` in `qa/helpers/qa_verdict.py`; `QaVerdict.verdict` typed as `Verdict`
  5. ~~**Philosophy classifier states**~~ DONE — `ClassifierState(str, Enum)` in `philosophy_classifier.py`; all raw `"malformed_signal"`/`"missing_signal"` replaced in bootstrapper and dispatcher
  6. ~~**Scope delta actions**~~ DONE — `ScopeDeltaAction(str, Enum)` in `scope_delta_parser.py`
  7. ~~**Note ack actions**~~ DONE — `NoteAction(str, Enum)` in `coordination/types.py`; consumers in `problem_resolver.py`, `completion_handler.py` migrated
  8. ~~**Related file status**~~ DONE — `RelatedFileStatus(str, Enum)` in `related_file_resolver.py`; consumers in `feedback_collector.py` migrated
  9. ~~**Research states**~~ DONE — `ResearchState(str, Enum)` in `research/engine/orchestrator.py`; consumers in `research_plan_executor.py`, `reconciler.py` migrated
  10. ~~**Governance blocker states**~~ DONE — `GovernanceBlockerState(str, Enum)` in `readiness_resolver.py`
  11. ~~**Blocker categories**~~ DONE — `BlockerCategory(str, Enum)` in `blocker_manager.py`; all mapping dicts and rollup code migrated
  12. ~~**Pause signal types**~~ DONE — `PauseType(str, Enum)` in `orchestrator/types.py`; all 11 hardcoded `"pause:..."` constructions across 8 files migrated to use `PauseType.*`
  13. ~~**Task/flow status**~~ DONE — `TaskStatus(str, Enum)` in `flow/types/context.py`; consumers in `reconciler.py`, `task_dispatcher.py`, `gate_repository.py` migrated. Mailbox `"complete"` is a coordination protocol message (different domain) — left as-is
  14. ~~**Implementation loop control**~~ DONE — `_LoopAction(str, Enum)` (private) in `implementation_cycle.py`
  15. ~~**Flow context status**~~ DONE — `FlowReadStatus(str, Enum)` in `flow_context_store.py`; consumer in `gate_repository.py` migrated
  16. ~~**Problem types**~~ DONE — type-safe via discriminated union in `coordination/problem_types.py` (see #148)
  17. **Philosophy signal status**: `"selected"`, `"empty"` — `philosophy_prompts.py` (prompt text only, no code comparisons — dismissed)
  18. ~~**Surface status**~~ DONE — `SurfaceStatus(str, Enum)` in `surface_registry.py`; consumers in `expansion_orchestrator.py` migrated
- **Precedent**: `risk/types.py` uses `str, Enum` pattern for `StepDecision`, `RiskMode`, `PostureProfile`. `dispatch/types.py` uses `DispatchStatus` enum. These should be replicated for other domains.
- **Overlap**: Supersedes remaining work in #100 (stringly-typed protocol tokens). #131 extracted constants but not enums.
- **Risk**: String comparisons with no exhaustiveness checking. A typo like `"bridgd"` silently falls through. No IDE autocomplete or type-checker support.
- **Status**: DONE — all 18 domains converted: 15 new `str, Enum` types, 1 discriminated union (#148), 2 dismissed (write-only/prompt-only)

### ~~147. `taskrouter/discovery.py` hardcodes system module list outside DI~~ DONE
- **Resolution**: Replaced hardcoded `_SYSTEM_ROUTE_MODULES` list with `_find_route_modules()` that auto-discovers `routes.py` files by scanning sibling packages of `taskrouter/`. Adding a new system now requires only creating a `routes.py` file — no list to update.

### ~~148. Raw dict contracts in coordination system — should be discriminated union~~ DONE
- **Resolution**: Implemented Problem type hierarchy as discriminated union of frozen dataclasses in `coordination/problem_types.py` (7 leaf types with AOP intermediate `NoteProblem` for `note_id` cross-cutting aspect). BridgeSignal pydantic model in `dispatch/service/tool_bridge.py`. `_SKIP_ACCEPTED = object()` sentinel replacing `{"_skip": True}` dict. All 6 consumer files migrated from `p["section"]` to `p.section`. All tests updated.

### ~~149. Duplicate `list_section_files` — deduplicated~~ DONE
- **Status**: DONE — removed duplicate from `substrate_state_reader.py`, updated `substrate_discoverer.py` and test to import from canonical location `scan/related/related_file_resolver.py`.

### ~~150. Dead wrapper `_sha256_file` in `intent_pack_generator.py`~~ DONE
- **Status**: DONE — removed double-wrapper that delegated to `sha256_file` via `philosophy_bootstrapper.py` re-export. Now imports directly from `philosophy_grounding.py`.

### ~~151. QA `Verdict` enum~~ DONE
- **Status**: DONE — created `Verdict(str, Enum)` in `qa/helpers/qa_verdict.py` with `PASS`, `REJECT`, `DEGRADED` values. `QaVerdict.verdict` typed as `Verdict`. Backward-compatible via `str` inheritance.

### ~~152. Unused `Any` import in `global_coordinator.py`~~ DONE
- **Status**: DONE — stale import left from Problem type migration. Removed.

### ~~153. `sys.exit(0)` in `message_poller.py` (missed in #108)~~ DONE
- **Status**: DONE — replaced with `raise PipelineAbortError("abort received")`. Also replaced `"abort"` and `"alignment_changed"` magic strings with `ControlSignal.ABORT` and `ControlSignal.ALIGNMENT_CHANGED`.

### ~~154. Vague data structures in coordination pipeline — positionally coupled parallel lists~~ DONE
- **Category**: Type safety / missing domain concepts (methodology §17)
- **Source**: Codebase scan (Cycle 23)
- **Scale**: 7 functions pass `list[list[Problem]]` ("confirmed groups") alongside parallel `list[str]` ("group strategies") with positional coupling. Bridge directives travel as `dict` with `{"needed": bool, "reason": str}` fields. Recurrence reports travel as `dict[str, Any]` with 4 known fields. The tuple return `tuple[list[list[Problem]], list[str], dict]` from `_build_coordination_plan` carries 3 semantically related but unnamed components.
- **Vague structures identified**:
  1. `list[list[Problem]]` ("confirmed groups") — 6 function signatures across `global_coordinator.py` and `plan_executor.py`
  2. `list[str]` ("group strategies") — parallel list traveling with confirmed_groups, indexed positionally
  3. `dict` ("bridge directive") with `needed`/`reason` fields — accessed via `.get()` in `plan_executor.py:383-391`
  4. `dict[str, Any]` ("recurrence report") with `recurring_sections`/`recurring_problem_count`/`max_attempt`/`problem_indices` — returned by `_detect_recurrence_patterns`, consumed in `global_coordinator.py` at 4 sites
  5. `tuple[list[list[Problem]], list[str], dict]` — return type of `_build_coordination_plan`
  6. `list[list[int]]` ("execution batches") — group indices arranged for parallel execution in `plan_executor.py`
  7. `dict[str, Any]` ("coord_plan") — raw planner output dict threaded through execution pipeline
- **Files affected**: `coordination/engine/global_coordinator.py`, `coordination/engine/plan_executor.py`, `coordination/service/problem_resolver.py`, `coordination/service/planner.py`
- **Risk**: Type annotations hide semantic meaning. `list[list[Problem]]` reveals nothing about groups, strategies, or bridge directives. Consumers rely on variable names and docstrings. Adding a field to a group requires modifying parallel list constructions in lockstep.
- **Status**: DONE — `BridgeDirective`, `RecurrenceReport`, `ProblemGroup` dataclasses added to `coordination/types.py`. `_detect_recurrence_patterns` returns `RecurrenceReport`. `global_coordinator.py` uses `ProblemGroup` instead of parallel lists. `plan_executor.py` accepts `list[ProblemGroup]`, bridge directives accessed via `group.bridge.needed`/`group.bridge.reason`. `coord_plan` dict no longer threaded through execution — only `agent_batches: list[list[int]] | None` extracted from raw plan. Tests updated.

### ~~155. Derivable path parameters in tool dispatch functions — 8+ params with PathRegistry derivation~~ DONE
- **Category**: Parameter coupling / derivable params (methodology §18)
- **Source**: Codebase scan (Cycle 23)
- **Scale**: 42 functions with 8+ params found. 14 at exactly 8 verified irreducible (all distinct data values). Key functions with derivable params:
  1. `handle_tool_friction` (9 params, `tool_bridge.py:283`) — `tool_registry_path` and `friction_signal_path` derivable from `planspace` + `section_number` via `PathRegistry`
  2. `_handle_bridge_success` (9 params, `tool_bridge.py:185`) — `tool_registry_path` derivable from `planspace`
  3. `surface_tool_registry` (8 params, `tool_surface_writer.py:179`) — `tool_registry_path`, `tools_available_path`, `artifacts` all derivable from `planspace` + `section_number`
  4. `_dispatch_registry_repair` (8 params, `tool_surface_writer.py:40`) — same derivable set as `surface_tool_registry`
  5. `_explore_section` (9 params, `section_explorer.py:72`) — could use existing `ScanContext` pattern
  6. `run_quick_scan` (9 params, `cli.py:55`) — could bundle into `ScanContext`
  7. `_run_freshness_check` (10 params, `codemap_builder.py:252`) — could use `ScanContext`
- **Existing patterns**: `DispatchContext` (cached `paths: PathRegistry`), `ScanContext.from_artifacts()` — both already adopted in parts of the codebase but not consistently.
- **Risk**: Wide contracts force callers to understand internal path derivation. Adding a new derived path requires updating every caller in the chain.
- **Status**: DONE — `surface_tool_registry` (8→4 params), `handle_tool_friction` (9→6 params), `validate_tool_registry_after_implementation` (7→5 params) now derive `tool_registry_path`, `tools_available_path`, `friction_signal_path`, and `artifacts` internally from `planspace + section_number` via `PathRegistry`. Private helpers (`_dispatch_registry_repair`, `_handle_bridge_success`, `_dispatch_bridge_agent`, `_dispatch_new_tool_validation`, `_dispatch_post_impl_repair`) also internalized. Caller `_count_pre_impl_tools` simplified to return `int` instead of `tuple[Path, int]`.

### ~~157. Private functions used as public API — cross-module `_` prefix imports~~ DONE
- **Category**: Naming / API boundary violation (methodology §2)
- **Source**: Codebase scan (Cycle 24)
- **Scale**: 13 functions with `_` prefix imported directly by other modules (not DI wiring in containers.py). Most imported: `_update_blocker_rollup` (9 files), `_write_alignment_surface` (3 files), `_append_open_problem` (3 files), `_collect_outstanding_problems` (3 files). Also: `_reexplore_section`, `_extract_tools`, `_extract_todos_from_files`, `_check_needs_microstrategy`, `_write_traceability_index`, `_declared_principle_ids`, `_registry_for_artifacts`, `_history_signature`, `_detect_recurrence_patterns`.
- **Files affected**: 30+ source and test files across signals/, coordination/, dispatch/, implementation/, proposal/, intent/, scan/, risk/ systems.
- **Risk**: `_` prefix convention signals "private, don't import" — but these functions ARE imported. Misleads developers about API stability. `monkeypatch` strings in tests contain the old names, making test maintenance fragile.
- **Status**: DONE — All 13 functions renamed to remove `_` prefix. All importers, callers, `__all__` exports, and `monkeypatch` attribute strings updated. 30+ files modified.

### ~~158. Multi-element tuple returns — positionally coupled return values~~ DONE
- **Category**: Type safety / missing domain concepts (methodology §17, §67 regression)
- **Source**: Codebase scan (Cycle 25)
- **Scale**: 14 functions return tuples with 3+ elements. Worst offenders:
  1. `_find_target_sections` (5-tuple) — `substrate_discoverer.py:101`
  2. `_detect_cross_section_issues` (5-tuple of `list[dict]`) — `cross_section_reconciler.py:163`
  3. `_compute_validation_hashes` (4-tuple of `str`) — `related_file_resolver.py:330`
  4. `_resolve_log_paths` (4-tuple of `Path`) — `scan/explore/analyzer.py:38`
  5. `_scan_session_messages` (4-tuple) — `scripts/log_extract/extractors/opencode.py:233`
  6. `_evaluate_alignment_and_handle_surfaces` (3-tuple) — `proposal_cycle.py:98`
  7. `handle_aligned_surfaces` (3-tuple) — `surface_handler.py:98`
  8. `_build_inputs_block` (3-tuple) — `intent_pack_generator.py:131`
  9. `_determine_decision_outcome` / `_classify_implementation_outcome` (3-tuple) — `risk_history_recorder.py`
  10. `_collect_section_scan_feedback` (3-tuple) — `feedback_collector.py:25`
  11. `_validate_declared_ids_types` (3-tuple) — `readiness_resolver.py:99`
  12. `_validate_source_map_content` (3-tuple) — `philosophy_grounding.py:78`
  13. `_check_prerequisites` (3-tuple) — `substrate_discoverer.py:48`
  14. `_prepare_ticket_spec` (3-tuple of `Path`) — `research/prompt/writers.py:104`
- **Risk**: Positional coupling — callers must know element order. Adding/removing a field requires updating every unpacking site. `return x, y, z` reveals nothing about what each element means.
- **Prior work**: #67 fixed 8 worst offenders (Cycle 8). `FrontierSliceResult` fixed 4-tuple in `implementation_phase.py` (this cycle). 14 remain.
- **Status**: DONE — 13 of 14 functions fixed with frozen dataclasses. `FrontierSliceResult` (implementation_phase.py), `PrerequisiteResult` + `TargetingResult` (substrate_discoverer.py), `CrossSectionIssues` (cross_section_reconciler.py), `AnalysisLogPaths` (analyzer.py), `ValidationHashes` (related_file_resolver.py), `SurfaceActionResult` (surface_handler.py), `AlignmentPhaseResult` (proposal_cycle.py), `ImplementationOutcome` (risk_history_recorder.py), `SectionScanFeedback` (feedback_collector.py), `GovernanceIds` (readiness_resolver.py), `SourceMapValidationFailure` (philosophy_grounding.py), `PromptInputs` (intent_pack_generator.py), `TicketPaths` (research/prompt/writers.py). Remaining: `_scan_session_messages` in `scripts/log_extract/` (low priority, scripts dir).

### ~~159. Dead variable assignments — remnants of removed mkdir calls~~ DONE
- **Category**: Dead code
- **Source**: Rescan (Cycle 26)
- **Scale**: 8 dead variable assignments across 6 files. All were PathRegistry `*_dir()` calls whose `.mkdir()` was removed during #110 but the variable assignment was left behind.
- **Files**: `dispatch/prompt/writers.py` (2), `coordination/engine/global_coordinator.py` (2), `implementation/service/traceability_writer.py` (1), `scan/codemap/codemap_builder.py` (1), `flow/repository/flow_context_store.py` (2).
- **Status**: DONE — all 8 dead variables removed.

### ~~160. Duplicated pause response and scope delta patterns across proposal system~~ DONE
- **Category**: Duplicate code / DRY violation
- **Source**: Rescan (Cycle 26)
- **Instances fixed**:
  1. `excerpt_extractor.py` reimplemented `handle_pause_response()` inline (7 lines) — replaced with call to `cycle_control.handle_pause_response()`.
  2. `cycle_control.py` duplicated scope delta creation (12 lines) from `excerpt_extractor._write_scope_delta` — extracted shared `write_scope_delta()` with `origin` parameter in `cycle_control.py`.
  3. `expansion_handler.py` duplicated gate response handling twice internally — replaced both with `handle_pause_response()`.
- **Remaining assessed duplicates**:
  - Budget exhaustion pattern (proposal vs implementation): same structure but different budget keys, signal paths, and loop names — 5+ parameters would need parameterization, making shared function worse than duplication.
  - 3-line alignment-changed abort check: too small to extract.
- **Status**: DONE — 3 inline duplicates eliminated. 2 remaining patterns assessed as acceptable.

### ~~156. Shared config and validation-related migration issues~~ DONE
- **Category**: Migration hygiene
- **Source**: Prior refactoring (Cycle 23)
- **What**: `validate_dynamic_content` → `validate_dynamic` migration missed several call sites. Shared config extraction left some duplicate patterns. All fixed in prior commits.
- **Status**: DONE — `validate_dynamic_content` renamed to `validate_dynamic` across all call sites, shared config extracted.

### ~~161. Cycle 27 — magic strings, duplicate control handling, ASSESSED→CLOSED~~ DONE

- **Category**: Magic strings, duplicate code, backlog hygiene
- **Source**: Full rescan (Cycle 27)
- **What found**:
  1. "resume" magic string used in `.startswith("resume")` checks across 5 files
  2. "full" intent mode magic string compared in 3 files
  3. "web"/"code"/"both" research type magic strings in 4 sites
  4. Duplicate abort/alignment_changed handling in `pipeline_state.py` (10 identical lines in 2 functions)
  5. Duplicate pause-response logic in `implementation_cycle.py._handle_signal_pause` vs `cycle_control.handle_pause_response`
  6. Three backlog items stuck at ASSESSED (#93, #101) or unresolved (#111) — all had concrete work done but weren't marked CLOSED
- **Status**: DONE — Added `RESUME_PREFIX`, `INTENT_MODE_FULL`/`LIGHTWEIGHT`/`CACHED`, `RESEARCH_TYPE_WEB`/`CODE`/`BOTH` constants to `signals/types.py`. Updated 5 files to use `RESUME_PREFIX` instead of `"resume"`. Updated `surface_handler.py` and `intent_initializer.py` to use `INTENT_MODE_*` constants. Updated `research_branch_builder.py` to use `RESEARCH_TYPE_*` constants. Extracted `_handle_control_msg` helper in `pipeline_state.py` (10 duplicate lines → 1 shared function). Refactored `_handle_signal_pause` in `implementation_cycle.py` to delegate to `handle_pause_response`. Closed #93 (parameter lists — resolved by #104/#126/#129/#155), #101 (nullability — primary crash vectors fixed), #111 (output_adjudicator — used by test, not dead).

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
- **Status**: DONE — All engine files decomposed to under 600 lines. `tool_registry_manager.py` decomposed into 3 modules and deleted. `research_plan_executor.py` split into 2 modules. Remaining 8 files over 500 lines are coherent single-concern modules (largest: `philosophy_bootstrapper.py` 1183 lines — all functions small, further decomposition creates circular deps).

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
