# Problem Archive

Persistent record of problems this project exists to solve. Each entry traces to code that addresses it.

## PRB-0001: Safe Multi-Agent Orchestration

**Status**: active
**Provenance**: user-authored
**Regions**: flow system, task dispatch, flow reconciler, section loop

Coordinating multiple AI agents to work on a codebase introduces risks: race conditions on shared artifacts, conflicting edits, unbounded agent spawning, lost work from agent failures. The system needs structured orchestration that bounds agent behavior while preserving their reasoning autonomy.

**Solution surfaces**: Flow system (chains, fanout, gates), task submission protocol, scripts-dispatch-agents-decide boundary, ROAL.

---

## PRB-0002: Strategic Implementation Over Brute Force

**Status**: active
**Provenance**: user-authored
**Regions**: section loop, integration proposals, alignment checks, consequence propagation

Brute-force implementation (try → fail → retry) wastes tokens, creates churn, and converges slowly. Strategic implementation (understand the problem deeply → design a strategy → implement once) collapses many waves of problems in one pass.

**Solution surfaces**: Integration proposals, microstrategy, alignment judges, consequence notes, problem framing.

---

## PRB-0003: Stale Artifacts Cause Incorrect Dispatch

**Status**: active
**Provenance**: audit-inferred (multiple rounds)
**Regions**: readiness gate, freshness computation, input hashing

In a multi-pass pipeline, artifacts from earlier passes may be stale when later passes reference them. Stale artifacts lead to incorrect decisions — skipping necessary work or repeating completed work.

**Solution surfaces**: Content-based hashing (PAT-0006), cycle-aware status (PAT-0007), readiness gate freshness checks.

---

## PRB-0004: Agent Output Corruption

**Status**: active
**Provenance**: audit-inferred (early rounds)
**Regions**: all artifact readers, JSON parsing, prompt output consumption

AI agents produce structured output (JSON, markdown with frontmatter) that may be syntactically or semantically malformed. Silent discard loses debugging evidence. Silent acceptance propagates bad data.

**Solution surfaces**: Corruption preservation (PAT-0001), fail-closed defaults (PAT-0008), structured validation.

---

## PRB-0005: Research Information Gathering

**Status**: active
**Provenance**: user-authored
**Regions**: research module, readiness gate, intent surfaces

Implementation sections may require information that isn't available in the codebase — domain knowledge, API specifications, design patterns from other systems. The system needs bounded research capabilities that gather information without unbounded exploration.

**Solution surfaces**: Research planner, domain researcher, research synthesizer/verifier, cycle-aware research status, flow-based research orchestration (R100).

---

## PRB-0006: Cross-Section Coherence

**Status**: active
**Provenance**: user-authored
**Regions**: consequence propagation, reconciliation, coordination

Sections implemented independently may create conflicting interfaces, duplicate abstractions, or incompatible assumptions. Cross-section coordination must detect and resolve these tensions without centralizing all decisions.

**Solution surfaces**: Consequence notes, reconciliation adjudicator, coordination planner/fixer, substrate discovery.

---

## PRB-0007: Execution Risk

**Status**: active
**Provenance**: user-authored
**Regions**: ROAL, alignment checks, readiness gate

The execution pipeline itself introduces risk — proposals that don't solve the stated problem, implementations that diverge from proposals, dispatch to the wrong model. Execution risk must be bounded at each transition.

**Solution surfaces**: ROAL (Risk-Optimization Assessment Loop), alignment judges, readiness gates, freshness computation.

---

## PRB-0008: Implementation Risk (Post-Landing)

**Status**: latent — governance design proposed but not yet implemented
**Provenance**: user-authored (governance gaps analysis)
**Regions**: TBD (post-implementation assessment stage)

After code lands, we don't systematically assess what risks the implementation introduced: coupling, security surfaces, scalability bottlenecks, pattern drift, coherence friction. Currently this is reactive (someone notices → refactoring cycle). Needs to become a pipeline stage.

**Solution surfaces**: Proposed — post-implementation assessment + risk register + stabilization loop.

---

## PRB-0009: Problem Traceability

**Status**: latent — governance design proposed but not yet implemented
**Provenance**: user-authored (governance gaps analysis)
**Regions**: TBD (governance layer)

Code exists but we can't trace it back to the problem it solves. When problems evolve or become obsolete, we don't know which code should evolve or be removed with them. Problems span layers but there's no way to see the full surface of a problem across its manifestation points.

**Solution surfaces**: This archive is the beginning. Full solution requires traceability enrichment (problem_ids in traceability artifacts) and governance packets.

---

## PRB-0010: Pattern Governance

**Status**: latent — governance design proposed, initial archive created
**Provenance**: user-authored (governance gaps analysis)
**Regions**: governance layer, audit process

Established patterns exist but aren't cataloged. New modules may diverge from templates accidentally. The audit process catches violations ad hoc but doesn't systematically check pattern conformance.

**Solution surfaces**: Pattern archive (this directory's sibling), pattern alignment audit phase, archive-aware proposals.
