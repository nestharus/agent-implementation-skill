# Component Architecture

Strict component decomposition of the agent-implementation-skill codebase.
93 components organized into 14 tiers. Each component owns exactly one
concern with its own data, operations, and boundaries.

## Current State

23,025 lines across ~65 Python files. 98 identifiable concerns crammed into
files organized by proximity rather than responsibility. Key problems:

- **No data abstraction layer**: 49 independent `json.loads(path.read_text())`
  sites, 69 independent `.malformed.json` rename implementations, 142+ ad-hoc
  artifact path constructions, 14 independent hash computations.
- **No domain model**: Core concepts (Problem, ProposalState, ModelPolicy,
  Signal, Task) passed as untyped `dict[str, Any]` with `.get()` chains.
- **God functions**: `_run_loop()` (845 lines), `run_section()` (1333 lines),
  `run_global_coordination()` (580 lines) contain 40% of orchestration logic.
- **Fragmented dispatch**: 3 separate `dispatch_agent` implementations, 3
  overlapping model selection mechanisms, prompt safety not enforced at
  dispatch boundary.
- **Dumping grounds**: `pipeline_control.py` mixes 13 unrelated functions;
  `task_flow.py` contains 12 distinct concerns in 1126 lines.

## Target Architecture

### Design Principles

1. **One concern per boundary**: Each component has exactly one reason to
   change and owns its own data and operations.
2. **Dependency flows downward**: Higher tiers depend on lower tiers, never
   the reverse. No circular dependencies.
3. **Types over dicts**: Domain concepts are dataclasses with constructors,
   not raw dicts with string-key access.
4. **Repository pattern for all I/O**: No module outside Tier 1 and Tier 7
   performs direct file reads/writes or path construction.
5. **Composition over embedding**: Orchestrators compose services; they do
   not contain service logic inline.

### Tier Overview

```
Tier 14: Pipeline Orchestrators (compose everything)
Tier 13: QA Services
Tier 12: Task/Flow Services
Tier 11: Coordination Services
Tier 10: Section Engine Services
Tier  9: Dispatch Orchestration
Tier  8: Detection & Analysis Services
Tier  7: Artifact-Specific Repositories
Tier  6: Section Input Services
Tier  5: Context & Policy Services
Tier  4: Signal & Verdict Processing
Tier  3: Domain Types (pure data, no I/O)
Tier  2: Communication Services
Tier  1: Foundational Services (no domain knowledge)
```

---

## Tier 1: Foundational Services

No domain knowledge. Pure infrastructure abstractions.

### 1. ArtifactIO

JSON file read/write with corruption preservation. Replaces 49 independent
`json.loads()` sites and 69 independent `.malformed.json` rename sites.

```
Owns: JSON read/write contract, .malformed.json rename protocol
Operations:
  read_json(path) -> dict | None
  write_json(path, data)
  rename_malformed(path) -> Path
Depends on: nothing
```

### 2. PathRegistry

Centralized artifact path construction. Replaces 142+ ad-hoc
`planspace / "artifacts" / ...` constructions.

```
Owns: artifact directory layout (single source of truth)
Operations:
  section_spec(num) -> Path
  proposal_excerpt(num) -> Path
  alignment_excerpt(num) -> Path
  signals_dir() -> Path
  signal_file(type, num) -> Path
  proposals_dir() -> Path
  decisions_dir() -> Path
  notes_dir() -> Path
  codemap() -> Path
  corrections() -> Path
  scope_deltas_dir() -> Path
  reconciliation_dir() -> Path
  coordination_dir() -> Path
  tool_registry() -> Path
  tool_surface(num) -> Path
  todos(num) -> Path
  microstrategy(num) -> Path
  mode_file(num) -> Path
  problem_frame(num) -> Path
  cycle_budget(num) -> Path
  trace_map(num) -> Path
Depends on: nothing (initialized with planspace Path)
```

### 3. HashService

Canonical hashing. Replaces 14 independent `hashlib.sha256` implementations.

```
Owns: hashing algorithm choice, encoding contract
Operations:
  file_hash(path) -> str
  content_hash(data: str | bytes) -> str
  fingerprint(items: list[str]) -> str
Depends on: nothing
```

### 4. DatabaseClient

Wrapper around `db.sh` subprocess calls. Replaces scattered
`subprocess.run(["bash", DB_SH, ...])` invocations.

```
Owns: db.sh subprocess protocol, output parsing
Operations:
  query(db_path, table, **filters) -> str
  log_event(db_path, table, tag, **fields) -> str (event_id)
  execute(db_path, command, *args) -> str
Depends on: nothing (external: db.sh script)
```

### 5. AgentExecutor

Raw subprocess invocation of the `agents` binary. This is dispatch stripped
to its essential operation: run a model with a prompt and an agent file.

```
Owns: agents binary invocation, timeout handling, stdout/stderr capture
Operations:
  run(model, prompt_path, agent_file, codespace?, timeout=600)
    -> AgentResult(output: str, returncode: int, timed_out: bool)
Depends on: nothing (external: agents binary)
```

### 6. PromptSafety

Dynamic content validation against prohibited patterns. Already exists as
`prompt_safety.py`. No changes needed.

```
Owns: prohibited pattern registry (7 patterns)
Operations:
  validate(content) -> list[str]  (violations)
  write_validated(content, path) -> bool
Depends on: nothing
```

---

## Tier 2: Communication Services

Database-backed messaging and process coordination. Use Tier 1.

### 7. MailboxService

Agent mailbox lifecycle and message passing. Currently embedded in
`dispatch.py` and `communication.py`.

```
Owns: mailbox registrations, message table, lifecycle events
Operations:
  register(db_path, agent_name)
  unregister(db_path, agent_name)
  send(db_path, target, message)
  recv(db_path, agent_name) -> list[str]
  log_lifecycle(db_path, tag, **fields) -> event_id
  log_summary(db_path, section, agent, model)
  cleanup(db_path, agent_name)
Depends on: DatabaseClient
```

### 8. MonitorService

Loop-detection agent lifecycle. Currently embedded in `dispatch.py`
lines 67-173.

```
Owns: monitor process lifecycle, signal collection
Operations:
  spawn(agent_name, planspace) -> MonitorHandle
  collect_signals(handle, since_event_id) -> list[Signal]
  shutdown(handle)
Depends on: DatabaseClient, AgentExecutor, MailboxService
```

### 9. PipelineStateService

Pipeline running/paused/aborted state. Currently in `pipeline_control.py`.

```
Owns: pipeline-state lifecycle events in DB
Operations:
  check_state(planspace) -> str  ("running" | "paused" | "aborted")
  wait_if_paused(planspace, parent) -> list[str]  (buffered messages)
  pause_for_parent(planspace, parent, reason) -> str  (resume payload)
Depends on: DatabaseClient, MailboxService
```

### 10. AlignmentChangeTracker

Flag file management for alignment-changed-pending. Currently in
`pipeline_control.py`.

```
Owns: alignment-changed-pending flag file, excerpt invalidation
Operations:
  set_flag(planspace)
  check_pending(planspace) -> bool  (non-clearing)
  check_and_clear(planspace) -> bool  (atomic check+clear)
  invalidate_excerpts(planspace, sections)
Depends on: PathRegistry (for flag/excerpt paths)
```

### 11. MessagePoller

Control message routing. Currently in `pipeline_control.py`.

```
Owns: message drain and classification logic
Operations:
  poll(planspace, parent, current_section?) -> ControlAction
  check_for_messages(planspace, parent) -> list[str]
  handle_pending(planspace, parent) -> bool  (abort_requested)
Depends on: MailboxService, AlignmentChangeTracker
```

---

## Tier 3: Domain Types

Pure data structures. No I/O, no side effects. Dataclasses with
`from_dict()` constructors.

### 12. Problem

Currently `list[dict[str, Any]]` passed through 8+ functions.

```python
@dataclass
class Problem:
    section: str
    type: Literal[
        "misaligned", "unaddressed_note", "consequence_conflict",
        "pending_negotiation", "needs_parent",
    ]
    description: str
    files: list[str]
    note_id: str | None = None
    note_path: str | None = None
    needs: str | None = None
    reason: str | None = None

    @classmethod
    def from_dict(cls, d: dict) -> Problem: ...
```

### 13. ProposalState

Currently raw `dict` with 10+ keys accessed via `.get()` chains.

```python
@dataclass
class ProposalState:
    resolved_anchors: list = field(default_factory=list)
    unresolved_anchors: list = field(default_factory=list)
    resolved_contracts: list = field(default_factory=list)
    unresolved_contracts: list = field(default_factory=list)
    execution_ready: bool = False
    readiness_rationale: str = ""
    new_section_candidates: list = field(default_factory=list)
    research_questions: list = field(default_factory=list)
    blocking_research_questions: list = field(default_factory=list)
    open_questions: list = field(default_factory=list)
    shared_seam_signals: list = field(default_factory=list)

    BLOCKING_FIELDS: ClassVar[list[str]] = [
        "unresolved_anchors", "unresolved_contracts",
        "open_questions", "shared_seam_signals",
        "blocking_research_questions",
    ]

    def has_blockers(self) -> bool: ...
    def extract_blockers(self) -> list[dict]: ...

    @classmethod
    def from_dict(cls, d: dict) -> ProposalState: ...
    @classmethod
    def fail_closed_default(cls) -> ProposalState: ...
```

### 14. ModelPolicy

Currently `dict[str, str]` with 30+ undocumented keys.

```python
@dataclass
class ModelPolicy:
    # Section-loop task models
    setup: str = "claude-opus"
    proposal: str = "gpt-5.4-high"
    alignment: str = "claude-opus"
    implementation: str = "gpt-5.4-high"
    adjudicator: str = "glm"
    triage: str = "glm"
    impact_analysis: str = "glm"
    microstrategy_decider: str = "glm"
    microstrategy_writer: str = "gpt-5.4-high"
    tool_registrar: str = "glm"
    bridge_tools: str = "gpt-5.4-high"
    coordination_plan: str = "gpt-5.4-high"
    fix_group: str = "gpt-5.4-high"
    # ... (all 30+ keys with defaults)

    # Escalation
    escalation_model: str = "gpt-5.4-xhigh"
    escalation_triggers: dict[str, int] = field(default_factory=dict)

    # Scan models (nested)
    scan: dict[str, str] = field(default_factory=dict)

    def resolve(self, key: str) -> str: ...

    @classmethod
    def from_file(cls, path: Path) -> ModelPolicy: ...
    @classmethod
    def defaults(cls) -> ModelPolicy: ...
```

### 15. Signal

Currently raw dicts with `state.get("state") == "needs_parent"`.

```python
@dataclass
class Signal:
    section: str
    state: Literal[
        "underspec", "need_decision", "dependency",
        "loop_detected", "out_of_scope", "needs_parent",
    ]
    detail: str
    needs: str | None = None
    assumptions_refused: list[str] | None = None
    suggested_escalation_target: str | None = None

    @classmethod
    def from_dict(cls, d: dict) -> Signal: ...
```

Subtypes as needed: `BlockerSignal`, `RecurrenceSignal`,
`SubstrateTriggerSignal`.

### 16. Task

Currently parsed from DB output into `dict[str, str]` via string splitting.

```python
@dataclass
class Task:
    id: str
    type: str
    by: str
    payload_path: str | None = None
    priority: str = "normal"
    section: str | None = None
    problem: str | None = None
    flow_id: str | None = None
    chain_id: str | None = None

    @classmethod
    def from_db_output(cls, output: str) -> Task | None: ...
```

### 17. SectionNumber

Currently sometimes `"03"`, sometimes `"3"`, extracted via regex.

```python
class SectionNumber:
    """Validated zero-padded section identifier."""
    _value: str  # always zero-padded: "03", "12"

    def __init__(self, raw: str | int): ...  # normalizes
    def __str__(self) -> str: ...
    def __eq__(self, other) -> bool: ...
    def __hash__(self) -> int: ...

    @classmethod
    def from_path(cls, path: Path) -> SectionNumber: ...
```

Existing types that are already well-defined and do not need changes:
`Section` (types.py), `Decision` (decisions.py), `FlowDeclaration`,
`TaskSpec`, `ChainAction`, `FanoutAction`, `BranchSpec`, `GateSpec`
(flow_schema.py).

---

## Tier 4: Signal & Verdict Processing

Parse and classify agent outputs. Use Tier 1, Tier 3.

### 18. SignalReader

Read structured JSON signals from files. Currently in `dispatch.py`
lines 297-355.

```
Owns: signal file schema, malformed signal quarantine
Operations:
  read(path) -> Signal | None
  read_tuple(path) -> tuple[Signal | None, str]
Depends on: ArtifactIO, Signal type
```

### 19. SignalClassifier

Classify raw agent output into signal types. Currently in `dispatch.py`
lines 358-438 (adjudicate_agent_output).

```
Owns: state-to-type mapping, adjudicator dispatch
Operations:
  classify(output: str, signal_path: Path) -> Signal | None
  adjudicate(output_path, planspace, parent, ...) -> Signal | None
Depends on: SignalReader, AgentExecutor (for adjudicator dispatch)
```

### 20. AlignmentVerdictParser

Extract JSON verdict from alignment judge output. Currently in
`alignment.py` lines 51-96.

```
Owns: verdict JSON extraction (single-line, code-fenced)
Operations:
  parse(output: str) -> AlignmentVerdict | None
    AlignmentVerdict: {frame_ok: bool, aligned: bool, problems: list}
Depends on: nothing (pure parsing)
```

### 21. QAVerdictParser

Extract verdict from QA interceptor output. Currently in
`qa_interceptor.py`.

```
Owns: QA verdict JSON extraction
Operations:
  parse(output: str) -> tuple[str, str, list]  (verdict, rationale, violations)
Depends on: nothing (pure parsing)
```

### 22. DispatchMetadataService

`.meta.json` sidecar for agent dispatch results. Currently in
`dispatch.py` lines 177-187 and `task_dispatcher.py`.

```
Owns: .meta.json sidecar schema
Operations:
  write(output_path, returncode, timed_out)
  read(output_path) -> DispatchMeta | None | CORRUPT
Depends on: ArtifactIO
```

---

## Tier 5: Context & Policy Services

Agent context resolution and model selection. Use Tier 1-2.

### 23. ModelSelector

Single source of truth for model selection. Replaces 3 overlapping
mechanisms: `TASK_ROUTES` defaults, `model-policy.json` overrides,
inline hardcoded fallbacks.

```
Owns: default model mappings (unified from section_loop + scan),
      policy file override logic, escalation rules
Operations:
  resolve(task_type: str) -> str  (model name)
  resolve_with_escalation(task_type, attempt_count) -> str
  agent_file(task_type) -> str
  policy_key(task_type) -> str | None
  load_policy(planspace) -> ModelPolicy
Depends on: ArtifactIO (for policy file), ModelPolicy type
```

### 24. ContextSidecarService

Agent context JSON file materialization. Currently in
`context_assembly.py`.

```
Owns: context sidecar JSON files (artifacts/context-*.json)
Operations:
  materialize(agent_file_path, planspace, section?) -> Path | None
Depends on: FrontmatterParser, ContextResolvers, ArtifactIO
```

### 25. FrontmatterParser

Parse `context:` field from agent file YAML frontmatter. Currently in
`context_assembly.py`.

```
Owns: YAML frontmatter parsing contract
Operations:
  parse_context_field(agent_file_path) -> list[str]
Depends on: nothing (pure parsing)
```

### 26. ContextResolvers

10 resolver functions, one per context category. Currently in
`context_assembly.py`.

```
Owns: resolver registry mapping category names to functions
Operations:
  resolve_section_spec(planspace, section) -> str
  resolve_decision_history(planspace, section) -> str
  resolve_strategic_state(planspace, section) -> str
  resolve_codemap(planspace, section) -> str
  resolve_related_files(planspace, section) -> str
  resolve_coordination_state(planspace, section) -> str
  resolve_allowed_tasks(planspace, section) -> str
  resolve_section_output(planspace, section) -> str
  resolve_model_policy(planspace, section) -> str
  resolve_flow_context(planspace, section) -> str
Depends on: PathRegistry, ArtifactIO
```

### 27. PromptTemplateService

Template loading, rendering, and constraint wrapping. Currently split
across `agent_templates.py` and `prompts/renderer.py`.

```
Owns: template files (prompts/templates/*.md), system constraints text
Operations:
  load(name) -> str
  render(template, context) -> str
  render_with_constraints(task_type, dynamic_body, file_paths?) -> str
Depends on: PromptSafety (for constraint wrapping)
```

---

## Tier 6: Section Input Services

Hash, freshness, and change tracking for sections. Use Tier 1-4.

### 28. SectionInputHasher

Canonical hash of all load-bearing section artifacts. Currently in
`pipeline_control.py` (165-line function).

```
Owns: canonical artifact set definition, hash computation
Operations:
  compute(sec_num, planspace, codespace, sections_by_num) -> str
Depends on: PathRegistry, HashService
```

### 29. FreshnessService

Compute and compare section freshness tokens. Currently in
`task_flow.py`.

```
Owns: freshness token computation
Operations:
  compute(planspace, section_number) -> str
  is_stale(stored_token, current_token) -> bool
Depends on: HashService, PathRegistry
```

### 30. CoordinationRecheckHasher

Canonical hash extended with coordinator-modified files. Currently in
`pipeline_control.py`.

```
Owns: coordination-specific hash extension
Operations:
  compute(sec_num, planspace, codespace, sections_by_num, modified_files) -> str
Depends on: SectionInputHasher, HashService
```

### 31. ChangeDetector

Pre/post file snapshots and diff detection. Currently in
`change_detection.py` (24 lines, already clean).

```
Owns: file hash snapshot protocol
Operations:
  snapshot(codespace, paths) -> dict[str, str]  (path -> hash)
  diff(before, after) -> list[str]  (changed paths)
Depends on: HashService
```

### 32. TraceabilityRecorder

Traceability chain and trace-map artifacts. Currently embedded in
`section_engine/runner.py` and `traceability.py`.

```
Owns: traceability chain artifacts, trace-map JSON
Operations:
  record(file, proposal, problem_chain)
  write_index(section, planspace)
  write_trace_map(section, problems, strategies, todo_ids, files)
Depends on: ArtifactIO, PathRegistry
```

---

## Tier 7: Artifact-Specific Repositories

Each repository owns one artifact type's read/write/validate lifecycle.
Use Tier 1-3.

### 33. ProposalStateRepository

```
Owns: proposal state JSON files
Operations:
  load(path) -> ProposalState
  save(state: ProposalState, path)
Depends on: ArtifactIO, ProposalState type
```

### 34. DecisionRepository

```
Owns: decision JSON arrays + markdown prose files
Operations:
  record(decisions_dir, decision: Decision)
  load(decisions_dir, section?) -> list[Decision]
Depends on: ArtifactIO, Decision type
```

### 35. StrategicStateBuilder

```
Owns: strategic-state.json artifact
Operations:
  build(decisions_dir, section_results, planspace) -> dict
Depends on: DecisionRepository, SignalRepository
```

### 36. ReconciliationQueueService

```
Owns: reconciliation request JSON files
Operations:
  queue(section_dir, section_number, contracts, anchors) -> Path
  load_requests(run_dir) -> list[dict]
Depends on: ArtifactIO, PathRegistry
```

### 37. ReconciliationResultRepository

```
Owns: reconciliation result, scope-delta, substrate-trigger JSON files
Operations:
  write_result(run_dir, result)
  write_scope_delta(run_dir, delta)
  write_substrate_trigger(run_dir, trigger)
  load(section_dir, section_number) -> dict | None
  was_affected(section_dir, section_number) -> bool
Depends on: ArtifactIO, PathRegistry
```

### 38. NoteRepository

```
Owns: cross-section note files (from-NN-to-NN.md)
Operations:
  read_incoming(artifacts_dir, section) -> list[dict]
  write_consequence(artifacts_dir, from_sec, to_sec, content)
Depends on: ArtifactIO, PathRegistry
```

### 39. NoteAcknowledgmentService

```
Owns: note-ack-{section}.json files
Operations:
  load(artifacts_dir, section) -> dict
  merge(artifacts_dir, section, new_acks)
  all_acknowledged(loaded_acks, incoming_notes) -> bool
Depends on: ArtifactIO, PathRegistry
```

### 40. ToolRegistryRepository

```
Owns: tool-registry.json, tools-available-{section}.md
Operations:
  load(planspace) -> dict | None
  write_surface(planspace, section, filtered_tools)
  repair(planspace, model) -> bool
Depends on: ArtifactIO, PathRegistry, AgentExecutor (for repair)
```

### 41. ScopeDeltaRepository

```
Owns: scope-delta JSON files
Operations:
  write(planspace, delta)
  load_pending(planspace) -> list[dict]
  apply_decisions(planspace, decisions)
Depends on: ArtifactIO, PathRegistry
```

### 42. SignalRepository

```
Owns: artifacts/signals/*.json files
Operations:
  write(planspace, signal: Signal)
  read(planspace, signal_type, section) -> Signal | None
  list(planspace, signal_type?) -> list[Signal]
Depends on: ArtifactIO, PathRegistry, Signal type
```

### 43. ExcerptRepository

```
Owns: section-{num}-{type}-excerpt.md files
Operations:
  write(planspace, section, excerpt_type, content)
  read(planspace, section, excerpt_type) -> str | None
  exists(planspace, section, excerpt_type) -> bool
  invalidate(planspace, section)
Depends on: PathRegistry
```

### 44. CycleBudgetService

```
Owns: cycle-budget.json signal files
Operations:
  load(planspace, section) -> dict | None
  write(planspace, section, budget: dict)
Depends on: ArtifactIO, PathRegistry
```

---

## Tier 8: Detection & Analysis Services

Pure analysis or lightweight dispatch. Use Tier 7.

### 45. AnchorOverlapDetector

```
Owns: nothing (pure analysis)
Operations:
  detect(states: dict[str, ProposalState]) -> list[dict]
Depends on: ProposalState type
```

### 46. ContractConflictDetector

```
Owns: nothing (pure analysis)
Operations:
  detect(states: dict[str, ProposalState]) -> list[dict]
Depends on: ProposalState type
```

### 47. SectionCandidateConsolidator

```
Owns: nothing (dispatches adjudicator for semantic grouping)
Operations:
  consolidate(states, planspace) -> list[dict]
Depends on: AgentExecutor (for adjudicator), PromptSafety
```

### 48. SeamAggregator

```
Owns: nothing (dispatches adjudicator for semantic grouping)
Operations:
  aggregate(states, planspace) -> list[dict]
Depends on: AgentExecutor (for adjudicator), PromptSafety
```

### 49. RecurrenceDetector

```
Owns: recurrence signal artifacts
Operations:
  check(section) -> bool
  emit_signal(planspace, section)
Depends on: SignalRepository
```

### 50. SectionNumberNormalizer

```
Owns: section number format rules
Operations:
  normalize(raw: str | int) -> str  ("3" -> "03")
  build_map(sections: list[Section]) -> dict[str, Section]
Depends on: nothing (pure transformation)
```

### 51. TodoExtractor

```
Owns: todos-{section}.md artifacts
Operations:
  extract(codespace, related_files) -> str  (markdown)
  write(planspace, section, content)
  remove(planspace, section)
Depends on: PathRegistry
```

---

## Tier 9: Dispatch Orchestration

Compose foundational services into dispatch operations.

### 52. DispatchService

Core dispatch: validate prompt, materialize context, execute agent, write
output and metadata. Used by both section_loop and scan.

```
Owns: dispatch orchestration (no monitoring, no mailbox)
Operations:
  dispatch(model, prompt_path, output_path, agent_file,
           planspace, codespace?, section?) -> str
Depends on: AgentExecutor, PromptSafety, ContextSidecarService,
            DispatchMetadataService
```

### 53. MonitoredDispatchService

Section-loop's full-featured dispatch with monitoring and mailbox
integration. Wraps DispatchService.

```
Owns: monitored dispatch lifecycle
Operations:
  dispatch(model, prompt_path, output_path, agent_file,
           planspace, parent, codespace?, section?,
           agent_name?) -> str
Depends on: DispatchService, MonitorService, MailboxService,
            PipelineStateService, AlignmentChangeTracker
```

### 54. AlignmentCheckService

Alignment dispatch with retry and verdict parsing. Currently in
`alignment.py`.

```
Owns: alignment check lifecycle (dispatch + retry + verdict)
Operations:
  check_with_retries(section, planspace, codespace, parent,
                     model?, adjudicator_model?, max_retries=2)
    -> str | None | "ALIGNMENT_CHANGED_PENDING" | "INVALID_FRAME"
Depends on: MonitoredDispatchService, AlignmentVerdictParser,
            MessagePoller
```

### 55. ProblemExtractor

Extract problems from alignment verdict, with adjudicator fallback.
Currently in `alignment.py` lines 99-208.

```
Owns: verdict-to-problems transformation
Operations:
  extract(result, output_path?, planspace?, parent?,
          codespace?, adjudicator_model?) -> str | None
Depends on: AlignmentVerdictParser, DispatchService (for adjudicator)
```

---

## Tier 10: Section Engine Services

Per-section execution phases. Each service owns one phase of the section
pipeline. Use Tier 7-9.

### 56. ExcerptExtractorService

Section setup: extract proposal and alignment excerpts from global
documents.

```
Owns: excerpt extraction lifecycle
Operations:
  extract(section, planspace, codespace, parent) -> bool
Depends on: MonitoredDispatchService, ExcerptRepository,
            SignalClassifier, PipelineStateService
```

### 57. NoteTriageService

Incoming note processing and triage dispatch.

```
Owns: triage decision lifecycle
Operations:
  triage(section, incoming_notes, planspace, codespace, parent)
    -> TriageResult(rework_needed: bool, acks: dict)
Depends on: MonitoredDispatchService, NoteRepository,
            NoteAcknowledgmentService, AlignmentCheckService
```

### 58. ToolSurfaceManager

Tool registry loading, filtering, repair, and friction handling.

```
Owns: tool surface lifecycle
Operations:
  prepare_surface(section, planspace) -> bool
  validate_post_impl(section, planspace, pre_count) -> bool
  handle_friction(section, planspace, codespace, parent) -> bool
Depends on: ToolRegistryRepository, MonitoredDispatchService,
            NoteRepository, SignalRepository
```

### 59. IntentManager

Intent triage, philosophy bootstrap, intent pack generation, budget
merging.

```
Owns: intent lifecycle per section
Operations:
  triage(section, planspace) -> IntentMode
  ensure_philosophy(planspace, codespace, parent) -> str | None
  generate_pack(section, planspace, codespace, parent) -> bool
  merge_budgets(section, planspace, intent_mode) -> dict
Depends on: MonitoredDispatchService, CycleBudgetService,
            SignalRepository
```

### 60. ProposalOrchestrator

Integration proposal generation, alignment checking, and feedback loop.

```
Owns: proposal loop lifecycle (generate -> align -> feedback)
Operations:
  run(section, planspace, codespace, parent, budget)
    -> ProposalPassResult
Depends on: MonitoredDispatchService, AlignmentCheckService,
            ProblemExtractor, SignalClassifier, MessagePoller,
            ProposalStateRepository, IntentManager
```

### 61. ReadinessGate

Readiness resolution and blocker routing to mechanical consumers.

```
Owns: readiness verdict, blocker-to-signal routing
Operations:
  resolve(section, planspace, proposal_state) -> ReadinessResult
  route_blockers(proposal_state, planspace, section)
Depends on: ProposalStateRepository, SignalRepository,
            ScopeDeltaRepository, ReconciliationQueueService
```

### 62. MicrostrategyManager

Detection, generation, validation, and retry with escalation.

```
Owns: microstrategy lifecycle
Operations:
  needs(proposal_path, planspace, section) -> bool
  generate(section, planspace, codespace, parent) -> Path | None
Depends on: MonitoredDispatchService, PromptSafety,
            TodoExtractor, SignalRepository
```

### 63. ImplementationOrchestrator

Strategic implementation, alignment checking, and feedback loop.

```
Owns: implementation loop lifecycle (code -> align -> feedback)
Operations:
  run(section, planspace, codespace, parent, budget) -> list[str]
Depends on: MonitoredDispatchService, AlignmentCheckService,
            ProblemExtractor, SignalClassifier, MessagePoller,
            ChangeDetector
```

### 64. PostCompletionService

Impact analysis and consequence note generation after implementation.

```
Owns: post-completion lifecycle
Operations:
  run(section, modified_files, all_sections, planspace,
      codespace, parent)
Depends on: MonitoredDispatchService, NoteRepository,
            ChangeDetector, TraceabilityRecorder
```

---

## Tier 11: Coordination Services

Global coordination across sections. Use Tier 9-10.

### 65. ProblemCollector

Aggregate outstanding problems from section results.

```
Owns: problem aggregation logic
Operations:
  collect(section_results, sections_by_num) -> list[Problem]
Depends on: Problem type, NoteRepository
```

### 66. ScopeDeltaAdjudicator

Dispatch coordinator agent for scope-delta decisions.

```
Owns: adjudication lifecycle
Operations:
  adjudicate(pending_deltas, planspace, codespace, parent) -> list[dict]
Depends on: MonitoredDispatchService, ScopeDeltaRepository,
            PromptSafety
```

### 67. CoordinationPlanner

Dispatch plan generator for outstanding problems.

```
Owns: coordination plan lifecycle
Operations:
  plan(problems, planspace, codespace, parent) -> list[FixGroup]
Depends on: MonitoredDispatchService, PromptSafety
```

### 68. FixGroupDispatcher

Execute fix groups and re-alignment.

```
Owns: fix group execution lifecycle
Operations:
  dispatch(fix_group, planspace, codespace, parent) -> bool
Depends on: MonitoredDispatchService, AlignmentCheckService
```

### 69. ReconciliationOrchestrator

Main reconciliation flow: detect overlaps, conflicts, shared seams.

```
Owns: reconciliation lifecycle
Operations:
  run(run_dir, proposal_results) -> ReconciliationSummary
Depends on: AnchorOverlapDetector, ContractConflictDetector,
            SectionCandidateConsolidator, SeamAggregator,
            ReconciliationResultRepository, ReconciliationQueueService
```

---

## Tier 12: Task/Flow Services

Task queuing, flow orchestration, and chain/fanout management. Use Tier 1.

### 70. TaskRegistry

Static mapping of task types to agent files and models.

```
Owns: TASK_ROUTES dict
Operations:
  resolve(task_type, policy?) -> (agent_file, model)
  known_types() -> set[str]
Depends on: ModelPolicy type
```

### 71. TaskSubmitter

DB insert for new tasks.

```
Owns: task queue INSERT protocol
Operations:
  submit(db_path, type, by, payload?, priority?, ...) -> task_id
Depends on: DatabaseClient
```

### 72. FlowIdAllocator

UUID generation with type prefixes.

```
Owns: ID format contract
Operations:
  instance_id() -> str  ("inst-...")
  flow_id() -> str  ("flow-...")
  chain_id() -> str  ("chain-...")
  gate_id() -> str  ("gate-...")
Depends on: nothing
```

### 73. FlowPathResolver

Flow artifact path construction.

```
Owns: flow artifact directory layout
Operations:
  context_path(planspace, flow_id) -> Path
  prompt_path(planspace, task_id) -> Path
  continuation_path(planspace, task_id) -> Path
  result_manifest_path(planspace, gate_id) -> Path
  gate_aggregate_path(planspace, gate_id) -> Path
Depends on: nothing
```

### 74. FlowParser

Normalize raw JSON to FlowDeclaration.

```
Owns: v1/v2 envelope parsing, format normalization
Operations:
  normalize(raw: object) -> FlowDeclaration
  parse_signal(path: Path) -> FlowDeclaration
Depends on: FlowDeclaration types, ArtifactIO
```

### 75. FlowValidator

Validate FlowDeclaration structure.

```
Owns: validation rules (known task types, chain refs, payload paths)
Operations:
  validate(decl: FlowDeclaration) -> list[str]  (errors)
Depends on: TaskRegistry (for known types)
```

### 76. ChainSubmitter

Insert chain tasks into DB with sequencing.

```
Owns: chain task insertion protocol
Operations:
  submit(db_path, chain: ChainAction, origin_refs) -> (chain_id, gate_id)
Depends on: TaskSubmitter, FlowIdAllocator, FlowContextBuilder
```

### 77. FanoutSubmitter

Create fanout branches and accumulation gates.

```
Owns: fanout + gate creation protocol
Operations:
  submit(db_path, fanout: FanoutAction, origin_refs) -> (fanout_id, gate_id)
Depends on: TaskSubmitter, FlowIdAllocator, FreshnessService
```

### 78. GateAggregator

Collect branch results when gate fires.

```
Owns: gate member tracking, result manifest construction
Operations:
  aggregate(planspace, gate_id) -> Path  (manifest)
Depends on: DatabaseClient, ArtifactIO, FlowPathResolver
```

### 79. TaskReconciler

Handle task completion: chain continuation, gate member updates, failure
cascading.

```
Owns: task completion lifecycle
Operations:
  reconcile(db_path, planspace, task_id, status)
Depends on: ChainSubmitter, GateAggregator, DatabaseClient
```

### 80. FlowContextBuilder

Build task-position context (where am I in the chain/fanout?).

```
Owns: flow context JSON construction
Operations:
  build(planspace, task_id) -> dict | None
Depends on: DatabaseClient, ArtifactIO, FlowPathResolver
```

### 81. PromptWrapper

Inject flow context headers into prompt files.

```
Owns: prompt wrapper file creation
Operations:
  wrap(planspace, task_id, prompt_path, flow_context_path) -> Path
Depends on: FlowPathResolver
```

### 82. FlowCatalog

Named chain packages for common task sequences.

```
Owns: package registry (proposal_alignment, implementation_alignment,
      coordination_fix)
Operations:
  resolve_ref(name, args, origin_refs) -> list[TaskSpec]
Depends on: TaskSpec type
```

### 83. CoordinationBranchBuilder

Convert problem groups into BranchSpec for fanout.

```
Owns: nothing (pure builder)
Operations:
  build(groups, planspace) -> list[BranchSpec]
Depends on: BranchSpec type
```

---

## Tier 13: QA Services

Optional contract compliance layer. Use Tier 5, 9.

### 84. QAInterceptor

Main QA gate orchestration.

```
Owns: QA intercept lifecycle (fail-open on errors)
Operations:
  intercept(task, agent_file, planspace) -> (passed: bool, rationale_path)
Depends on: ContractResolver, QAPromptBuilder, QARationaleWriter,
            DispatchService, QAVerdictParser
```

### 85. ContractResolver

Look up agent contracts for QA evaluation.

```
Owns: agent file contract lookup, infrastructure submitter descriptions
Operations:
  resolve(submitter_name) -> str  (contract text)
Depends on: nothing (reads agent .md files)
```

### 86. QAPromptBuilder

Construct QA evaluation prompts.

```
Owns: QA prompt template
Operations:
  build(task, target_contract, submitter_contract, payload) -> str
Depends on: nothing (pure construction)
```

### 87. QARationaleWriter

Write QA intercept result artifacts.

```
Owns: qa-intercepts/ artifact files
Operations:
  write(planspace, task, verdict, rationale, violations) -> Path
Depends on: ArtifactIO, PathRegistry
```

---

## Tier 14: Pipeline Orchestrators

Top-level orchestrators that compose services. Each orchestrator is thin:
it sequences service calls and handles control flow (restart, abort,
convergence), but contains no service logic.

### 88. SectionPipeline

Per-section execution: the sequential composition of Tier 10 services.
Replaces the 1333-line `run_section()`.

```
Owns: per-section execution order
Sequence:
  1. RecurrenceDetector.check()
  2. NoteTriageService.triage()
  3. ToolSurfaceManager.prepare_surface()
  4. ExcerptExtractorService.extract()
  5. IntentManager.triage() + ensure_philosophy() + generate_pack()
  6. TodoExtractor.extract()
  7. ProposalOrchestrator.run()
  8. ReadinessGate.resolve()
  9. MicrostrategyManager.generate()  (if needed)
  10. ImplementationOrchestrator.run()
  11. ChangeDetector.diff()
  12. TraceabilityRecorder.record()
  13. ToolSurfaceManager.validate_post_impl() + handle_friction()
  14. PostCompletionService.run()
Depends on: all Tier 10 services
```

### 89. CoordinationPipeline

Global coordination: the sequential composition of Tier 11 services.
Replaces the 580-line `run_global_coordination()`.

```
Owns: coordination execution order, convergence detection
Sequence:
  1. ProblemCollector.collect()
  2. ScopeDeltaAdjudicator.adjudicate()
  3. CoordinationPlanner.plan()
  4. FixGroupDispatcher.dispatch()  (per group)
  5. AlignmentCheckService.check()  (per affected section)
  6. Convergence check (problems decreasing?)
Depends on: all Tier 11 services, AlignmentCheckService
```

### 90. MainPipeline

Overall section-loop orchestration. Replaces the 845-line `_run_loop()`.

```
Owns: phase sequencing, alignment-changed restart, convergence
Phases:
  Phase 1a: Proposal pass (SectionPipeline per section, proposal mode)
  Phase 1b: Reconciliation (ReconciliationOrchestrator)
  Phase 1c: Implementation pass (SectionPipeline per section, impl mode)
  Phase 2:  Global coordination (CoordinationPipeline)
  Phase 3:  Re-alignment + convergence check
Control:
  - AlignmentChangeTracker triggers Phase 1 restart
  - SectionInputHasher enables targeted skip optimization
  - MessagePoller handles abort/pause at phase boundaries
Depends on: SectionPipeline, CoordinationPipeline,
            ReconciliationOrchestrator, AlignmentChangeTracker,
            SectionInputHasher, MessagePoller
```

### 91. TaskDispatchLoop

Task queue polling and dispatch. Replaces `task_dispatcher.py`.

```
Owns: poll-claim-dispatch-reconcile loop
Sequence:
  1. Poll for next runnable task
  2. Resolve task type (TaskRegistry)
  3. Validate payload (PromptSafety)
  4. QA intercept gate (QAInterceptor, optional)
  5. Check freshness (FreshnessService)
  6. Wrap prompt with flow context (PromptWrapper)
  7. Dispatch agent (DispatchService)
  8. Read dispatch metadata (DispatchMetadataService)
  9. Reconcile completion (TaskReconciler)
Depends on: TaskRegistry, DispatchService, QAInterceptor,
            FreshnessService, PromptWrapper, TaskReconciler,
            DispatchMetadataService
```

### 92. ScanPipeline

Scan stage orchestration. Already clean; minimal changes needed.

```
Owns: scan phase sequencing
Sequence:
  quick: CodemapBuilder -> SectionExplorer
  deep:  TierRanker -> PerFileAnalyzer
Depends on: DispatchService (not MonitoredDispatchService),
            ModelSelector
```

### 93. SubstratePipeline

Substrate stage orchestration. Already clean; minimal changes needed.

```
Owns: substrate phase sequencing
Sequence:
  Phase A: ShardExplorer (per section)
  Phase B: Pruner (aggregate)
  Phase C: Seeder (per section)
Depends on: DispatchService, ModelSelector
```

---

## Migration Strategy

### Phase 1: Foundation (unblocks everything else)

Extract Tier 1 services. These have zero domain knowledge and can be
built and tested independently.

1. **ArtifactIO** — eliminates 49 JSON read sites, 69 malformed-rename sites
2. **PathRegistry** — eliminates 142+ ad-hoc path constructions
3. **HashService** — eliminates 14 independent hash implementations

Expected impact: ~2000 LOC of duplication eliminated. Every subsequent
phase benefits from these immediately.

### Phase 2: Domain Types

Create Tier 3 dataclasses. Replace `dict` with typed objects at function
boundaries. This is incremental: each type can be introduced independently
and callers migrated one at a time.

Order: ModelPolicy (most scattered) -> ProposalState (most complex) ->
Problem (most passed around) -> Signal -> Task -> SectionNumber.

### Phase 3: Communication Extraction

Extract Tier 2 services from `dispatch.py` and `pipeline_control.py`.
MailboxService and MonitorService are the key wins — they untangle dispatch
from communication.

### Phase 4: Repository Layer

Extract Tier 7 repositories. Each one is a focused read/write interface
for one artifact type. The key enabler is PathRegistry from Phase 1.

### Phase 5: Dispatch Restructuring

Build Tier 9: DispatchService (shared) and MonitoredDispatchService
(section-loop specific). This unifies the 3 current dispatch
implementations.

### Phase 6: Pipeline Decomposition

Extract Tier 10 section engine services from `runner.py` and Tier 11
coordination services from `coordination/runner.py`. Each concern becomes
its own module. The orchestrators (Tier 14) become thin composition layers.

### What NOT to do

- Do not introduce abstract base classes or Protocol types unless they
  have 3+ implementations.
- Do not add dependency injection frameworks. Constructor parameters and
  module-level imports are sufficient.
- Do not refactor scan/ or substrate/ early. They are already clean.
  Focus on section_loop/ where the debt is concentrated.
- Do not attempt Phase 6 before Phase 1. The god-functions are hard to
  decompose precisely because artifact I/O is scattered — once you have
  `ArtifactIO.read_json()` instead of inline `json.loads(path.read_text())`,
  the functions naturally become easier to split.
