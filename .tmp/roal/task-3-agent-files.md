# Task 3: ROAL Agent Files — Risk Assessor and Execution Optimizer

## Context

You are implementing the Risk-Optimization Adaptive Loop (ROAL). This is Task 3 of 6 — agent instruction files.

**Prerequisite**: Tasks 1-2 have been completed. The `src/scripts/lib/risk/` subpackage exists with types, serialization, quantifier, posture, history, and engagement modules.

Read these existing agent files first to understand the style and structure:
- `src/agents/implementation-strategist.md`
- `src/agents/microstrategy-writer.md`
- `src/agents/impact-analyzer.md`
- `src/agents/coordination-planner.md`
- `src/agents/intent-triager.md`
- `src/agents/state-detector.md`

Also read:
- `src/scripts/lib/risk/types.py` — all ROAL data types and artifact shapes
- `src/scripts/lib/core/path_registry.py` — PathRegistry with risk accessors

## What to Create

### 1. `src/agents/risk-assessor.md`

This is the Risk Agent's instruction file. It is a diagnostic agent, not a planner or implementer.

The agent file must cover:

**Role**: Assess execution risk for a specific task package at a specific layer. Externalize the risk picture BEFORE the executing agent encounters difficulty.

**What it reads** (core inputs):
- concern/section specification
- proposal excerpt and alignment excerpt
- problem frame
- intent artifacts when present
- proposal-state and readiness artifacts
- reconciliation results and scope-delta artifacts
- consequence notes and impact artifacts
- tool registry and tool digest
- codemap and related-file hypotheses
- flow context / chain position / gate aggregates
- current package artifact
- risk history for the same concern
- monitor signals (LOOP_DETECTED, STALLED)
- freshness information

**Layer-specific input emphasis**:
- Intent/proposal layer: section summary, intent pack, codemap, proposal-state draft, prior failures, scope deltas, unresolved contracts/anchors
- Implementation layer: proposal-state, readiness, microstrategy, TODOs, flow context, modified-file manifests, verification surfaces, monitor signals
- Coordination layer: grouped problem batches, consequence notes, contract conflicts, modified-file manifests, alignment recheck results

**First-class output: understanding inventory**

Four buckets:
- Confirmed — grounded in current artifacts or verified reads
- Assumed — plausible but not yet verified
- Missing — required to safely execute a later step
- Stale — previously known but freshness is suspect

The inventory must be step-aware: for each proposed step, mark which prerequisites are confirmed vs assumed.

**Risk dimensions** — score each on 0-4 severity scale:
1. Context rot
2. Silent drift
3. Scope creep
4. Brute-force regression
5. Cross-section incoherence
6. Tool island isolation
7. Stale artifact contamination

**Cross-cutting modifiers** (scored separately):
- Blast radius (0-4)
- Reversibility (0-4, 4 = easy revert)
- Observability (0-4, 4 = easy detect)
- Confidence (0.0-1.0)

**Step classes**: explore, stabilize, edit, coordinate, verify

**Quantification**: produce raw risk (0-100) and assessment confidence (0-1) per step

**Output format**: JSON matching the RiskAssessment schema from types.py. Include the understanding inventory, per-step risk vectors, raw package risk, frontier candidates, dominant risks, and reopen recommendations.

**What it does NOT do**:
- Choose models or mitigation stacks
- Rewrite task flows
- Silently solve missing structural problems
- Decide root scope expansion
- Perform implementation

If it concludes a risk is structural, it recommends `reopen` but does not perform the reopen.

### 2. `src/agents/execution-optimizer.md`

This is the Tool Agent (Execution Optimizer) instruction file.

**Role**: Translate quantified risk into a minimum effective execution posture. Choose the lightest posture that brings residual risk below threshold.

**Operating principle**: Minimum effective guardrail. Select the lowest-cost posture that satisfies both:
1. Residual risk below threshold for the step class and layer
2. Hard invariants still satisfied

**What it reads**:
- The Risk Agent's risk-assessment.json
- Current package artifact
- Risk history
- Tool registry
- Risk parameters (thresholds)

**Posture profiles** it reasons about:

P0 direct: trivially bounded, high confidence. No extra decomposition, local verification only.

P1 light: low-risk but needs small structure. Targeted read refresh, narrow single-step, lightweight verify, standard freshness check.

P2 standard: nontrivial but contained. Explicit package artifact, targeted exploration before mutation, verify step or alignment check, monitor on multi-file work.

P3 guarded: high-risk but locally manageable. Decompose into explore/stabilize/edit/verify slices, stronger model on risky planning step, monitor required, freshness refresh before each mutation, consequence/impact analysis inserted, coordination/bridge-tool steps where needed, fanout only behind gates, failure policy = block.

P4 reopen/block: residual risk above threshold or structurally illegitimate. Reopen proposal/reconciliation/intent, route to SIS or coordination, emit NEED_DECISION/NEEDS_PARENT/blocker signal.

**Mitigation catalog** — for each risk type, list typical mitigations:

Context rot: shrink package, split chains, persist snapshots, narrow sidecars, stronger timeboxing/monitor

Silent drift: alignment check, refresh excerpts, require proposal-state cross-check, reopen if unresolved structure

Scope creep: narrow concern scope, split deferred child package, emit scope delta, block ungrounded expansion

Brute-force regression: add explore/stabilize pre-step, decompose further, require continuation after each slice, monitor + loop handling, upgrade model for planning step only

Cross-section incoherence: force reconciliation/coordination, write/consume consequence notes, gate fanout and synthesize, freeze shared contract first

Tool island isolation: consult tool registry, route to bridge-tools, propose adapter, tool_registry_repair

Stale artifact contamination: refresh artifact, rerun proposal/reconciliation/alignment, reject stale tasks, defer until upstream stabilizes

**Workflow rescaling**: The Tool Agent can split steps, merge overly-fragmented low-risk steps, change chains to fanouts, change gate failure policy, insert microstrategy, change model class, change cycle budgets.

It may NOT invent new runtime primitives.

**Output format**: JSON matching the RiskPlan schema from types.py.

Per-step decisions:
- `accept` — safe enough under chosen posture
- `reject_defer` — not safe enough yet, wait for earlier outputs
- `reject_reopen` — cannot be solved locally, route upward

**What it does NOT do**:
- Lower hard guardrails
- Forgive false execution readiness
- Invent structure missing from proposal
- Widen scope to make a package easier
- Directly mutate codespace

## Style Guidelines

Match the exact style of existing agent files. Each agent file should have:
- A clear role statement
- A "You receive" section listing inputs
- A "You produce" section with the output format (JSON schema)
- Behavioral rules and constraints
- What the agent does NOT do
- Examples if helpful

## Verification

No automated tests for agent files. Just verify they exist and follow the style:
```bash
ls -la src/agents/risk-assessor.md src/agents/execution-optimizer.md
```
