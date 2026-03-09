# Pattern Archive

Established solution templates. Each pattern traces to philosophy and has known instances in the codebase.

## Substrate Invariants

These are foundational invariants, not project-level patterns. They do not change without a fundamental redesign.

- **Scripts dispatch, agents decide** — scripts do mechanical coordination (dispatch, check, log); agents do reasoning (explore, understand, decide). Strategic decisions belong to agents.
- **File-path-based prompts** — prompts reference artifacts by file path, not inline content. Agents read what they need.
- **Task submission, not direct spawning** — agents request follow-on work via structured `TaskSpec`. No direct subprocess spawning.
- **Bounded typed substrate** — typed data flows with schema validation at boundaries.

## PAT-0001: Corruption Preservation

**Problem class**: Structured artifact read/write in a multi-agent system where any writer may produce malformed output.

**Philosophy**: Fail-closed. Evidence preservation over silent discard. Zero risk tolerance.

**Template**:
1. `read_json(path)` for syntax-level parsing (handles encoding, trailing data, etc.)
2. If parsed successfully, validate schema shape (required keys, types)
3. On schema mismatch: `rename_malformed(path)` to preserve the corrupt file for debugging
4. Return `None` on any failure — caller proceeds fail-closed

**Canonical instance**: `load_surface_registry()` in `section_loop/intent/surfaces.py`

**Known instances**:
- `surfaces.py` — surface registry, research surfaces
- `orchestrator.py` — research status, research plan validation
- `artifact_io.py` — `read_json()` / `rename_malformed()` primitives
- All `read_json` callsites follow this pattern

**Conformance**: Any new structured artifact reader MUST follow this pattern. No silent `json.loads()` with bare except.

---

## PAT-0002: Prompt Safety

**Problem class**: Prompt injection, malformed prompt content, untrusted dynamic values in prompt text.

**Philosophy**: Every prompt is a trust boundary. Zero risk tolerance.

**Template**:
1. Assemble prompt content as a string
2. Call `write_validated_prompt(content, path)` which validates and writes atomically
3. Use the written path as the prompt reference

**Canonical instance**: `write_research_plan_prompt()` in `lib/research/prompt_writer.py`

**Known instances**: All prompt writers in `prompt_writer.py`, `plan_executor.py`, readiness gate prompt emission.

**Conformance**: No prompt file may be written without going through `write_validated_prompt()`.

---

## PAT-0003: Path Registry

**Problem class**: Artifact path proliferation, hardcoded path construction, path inconsistency across modules.

**Philosophy**: Single source of truth for all artifact locations.

**Template**:
1. All artifact paths come from `PathRegistry(planspace)`
2. New artifact types get a new accessor method on `PathRegistry`
3. No module constructs paths by string concatenation or `Path(...)` from convention

**Canonical instance**: `PathRegistry` in `lib/core/path_registry.py`

**Conformance**: Any new artifact path MUST be added to `PathRegistry`. Grep for raw `Path(` construction in artifact contexts.

---

## PAT-0004: Flow System

**Problem class**: Multi-step, multi-agent task orchestration with dependencies, parallelism, and accumulation.

**Philosophy**: Scripts dispatch, agents decide. Structured submission over ad hoc spawning.

**Template**:
- **Chains** — sequential task lists via `submit_chain()`
- **Fanout** — parallel branches via `submit_fanout()` with `BranchSpec`
- **Gates** — accumulate branch results, fire synthesis via `GateSpec`
- **Named packages** — `_PACKAGE_REGISTRY` in `flow_catalog.py` maps names to `TaskSpec` sequences

**Canonical instance**: `execute_research_plan()` in `lib/research/plan_executor.py`

**Known instances**: Research flow (plan→tickets→synthesis→verify), section implementation chains, coordination fix chains.

**Conformance**: New multi-step workflows MUST use the flow system. No direct multi-agent orchestration outside the flow primitives.

---

## PAT-0005: Policy-Driven Models

**Problem class**: Model selection hardcoded in dispatch callsites, preventing flexible model rotation.

**Philosophy**: Configuration over convention. No magic strings in operational code.

**Template**:
1. Agent definitions specify model requirements by capability, not by model name
2. Dispatch resolves model from policy configuration
3. No hardcoded model strings in script code or prompt text

**Conformance**: New agents declare capability needs. Dispatch resolves.

---

## PAT-0006: Freshness Computation

**Problem class**: Stale artifacts causing incorrect dispatch or repeated work.

**Philosophy**: Change detection must be deterministic and content-based.

**Template**:
1. `content_hash()` from `hash_service.py` for fingerprinting inputs
2. Compare current hash against stored hash to detect staleness
3. Section-scoped freshness via `compute_section_freshness()`

**Canonical instance**: `readiness_gate.py` freshness checks

**Known instances**: Section input hashing, research trigger hashing, codemap freshness.

**Conformance**: Any "should we redo this?" decision MUST use content-based hashing, not timestamps or file existence.

---

## PAT-0007: Cycle-Aware Status

**Problem class**: Re-triggering workflows when inputs change, while avoiding re-triggering when inputs haven't changed.

**Philosophy**: Precision over coarseness. Don't repeat work that's still valid.

**Template**:
1. Compute `trigger_hash` from the inputs that would cause re-triggering
2. Store `trigger_hash` + `cycle_id` in status artifact
3. `is_complete_for_trigger(trigger_hash)` checks both terminal state AND hash match

**Canonical instance**: `is_research_complete_for_trigger()` in `lib/research/orchestrator.py`

**Conformance**: Any retriggerable workflow should adopt this pattern rather than coarse "is it done?" checks.

---

## PAT-0008: Fail-Closed Defaults

**Problem class**: Parse failures, missing data, unexpected states in a multi-agent pipeline.

**Philosophy**: Conservative behavior on uncertainty. The cost of doing too much work is lower than the cost of skipping necessary work.

**Template**:
- On parse failure: default to the conservative/safe behavior (e.g., `rebuild=True`, `friction=True`)
- On missing artifact: treat as "not yet done" rather than "already done"
- On unexpected state: fall through to full processing rather than short-circuiting

**Conformance**: Any conditional skip/optimization must have a fail-closed default path.

---

## PAT-0009: Blocker Taxonomy

**Problem class**: Work that cannot proceed needs structured routing to the right resolver.

**Philosophy**: Structured signals over freeform text. Route, don't block silently.

**Template**:
- Blockers carry: `state` (need_decision / needs_parent / needs_research), `section`, `detail`, `needs`, `why_blocked`, `source`
- Written as structured JSON to `signals/` directory
- Rollup function aggregates for visibility

**Canonical instance**: `_emit_not_researchable_signals()` in `lib/research/plan_executor.py`

**Conformance**: Anything that blocks progress must emit a structured blocker signal, not just log a warning.

---

## PAT-0010: Intent Surfaces

**Problem class**: Agents need context about section state, problem framing, and accumulated decisions.

**Philosophy**: Agents decide based on rich context, not raw artifacts.

**Template**:
1. Load and merge surfaces from multiple sources (problem frame, research, prior decisions)
2. Package as an intent surface accessible by file path
3. Include in prompts as context

**Canonical instance**: `load_combined_intent_surfaces()` in `section_loop/intent/surfaces.py`

**Conformance**: New context sources should be merged into the intent surface system, not passed as separate ad hoc prompt sections.

---

## Health Notes

- **PAT-0001 (Corruption Preservation)**: Healthy. R99-R100 extended to research surfaces and plan validation.
- **PAT-0002 (Prompt Safety)**: Healthy. R100 extended to all research prompt writers.
- **PAT-0003 (Path Registry)**: Healthy. R100 added research artifact accessors.
- **PAT-0004 (Flow System)**: Healthy. R100 added research packages to catalog.
- **PAT-0005 (Policy-Driven Models)**: Mostly healthy. Check for any remaining hardcoded model strings in newer agents.
- **PAT-0006 (Freshness)**: Healthy. Core infrastructure stable.
- **PAT-0007 (Cycle-Aware Status)**: New pattern (R100). Currently only research. Should extend to other retriggerable workflows.
- **PAT-0008 (Fail-Closed)**: Healthy. Established across all readers.
- **PAT-0009 (Blocker Taxonomy)**: Healthy. Used in research and readiness gate.
- **PAT-0010 (Intent Surfaces)**: Healthy. R100 added research surface merging.
