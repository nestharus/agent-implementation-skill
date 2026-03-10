# Risk Register

Persistent record of landed-code risks and accepted technical debt. This is NOT execution risk (handled by ROAL). This records risks that exist in deployed code.

## Format

Each entry:
- **Risk ID**: RISK-XXXX
- **Category**: coupling | security | scalability | pattern-drift | coherence | operability
- **Region**: affected modules/sections
- **Description**: what the risk is
- **Severity**: low | medium | high
- **Status**: open | accepted | mitigated | resolved
- **Acceptance rationale**: why it was accepted (if accepted)
- **Mitigation**: what was done or planned

## Population

Post-implementation assessment emits `accept_with_debt` verdicts with typed `debt_items` into `risk-register-signal.json` files. The `promote_debt_signals()` function in `lib/governance/assessment.py` consumes these signals into a staging artifact (`risk-register-staging.json`). Staged entries are promoted into this register during stabilization or audit rounds.

## Entries

### RISK-0001: Stricter packet ambiguity gating transitional noise

- **Category**: operability
- **Region**: governance packets, readiness gate
- **Description**: R107 changed missing pattern applicability metadata from universal match to ambiguity. Until all governance contexts provide complete metadata, packet ambiguity will generate governance blockers that must be resolved or carried forward in proposal-state.
- **Severity**: low
- **Status**: accepted
- **Acceptance rationale**: Catalog metadata is complete as of R107. Transitional noise only applies to new patterns added without Regions/Solution surfaces.
- **Mitigation**: PAT-0011 conformance now requires metadata on all patterns. Audit catches missing metadata.

---

### RISK-0002: Policy resolver refactor model assignment regression

- **Category**: coupling
- **Region**: model policy, dispatch surfaces
- **Description**: R107 replaced ~47 `policy.get("key", "literal")` callsites with `resolve(policy, "key")`. If `resolve()` has a bug or if a key is missing from ModelPolicy, the wrong model (or None) could be dispatched.
- **Severity**: low
- **Status**: mitigated
- **Acceptance rationale**: All 1499 tests pass. `resolve()` falls back to ModelPolicy defaults which are the same values the literals used.
- **Mitigation**: ModelPolicy dataclass defines all known keys with defaults. Tests cover all dispatch paths with mocked agents.

---

### RISK-0003: Governance index parse-failure silent degradation

- **Category**: operability / pattern-drift
- **Region**: governance loader, packet builder, readiness gate
- **Description**: If governance markdown docs (problems/index.md, patterns/index.md, philosophy profiles) are corrupt or unparseable, `build_governance_indexes()` previously swallowed the exception, wrote empty indexes, and returned True. Downstream consumers interpreted empty indexes as "no governance applies" rather than "governance is corrupt." R108 added structured `index-status.json` tracking parse failures, and the packet builder surfaces these as governance questions with `ambiguous_applicability` state.
- **Severity**: medium
- **Status**: mitigated
- **Acceptance rationale**: Parse failures are now tracked and surfaced to downstream consumers. Readiness resolver will block descent when packet ambiguity is unresolved.
- **Mitigation**: `build_governance_indexes()` returns False on parse failure and writes `index-status.json`. Packet builder checks index status and emits governance questions on parse failure. Readiness resolver bridges ambiguity to descent gating (R107).

---

### RISK-0004: Advisory QA degradation misreported as pass

- **Category**: coherence / operability
- **Region**: QA interceptor, task dispatcher, QA verdict parser, lifecycle logging
- **Description**: When QA interception encounters internal errors, missing targets, or unparseable output, the degraded outcome is logged identically to genuine approval (`qa:passed`). QA verdict parser maps malformed output to PASS. Task dispatcher treats QA exceptions as `passed = True`. This erases the evidence distinction between "QA evaluated and approved" and "QA failed, dispatch fell back to baseline."
- **Severity**: low
- **Status**: open
- **Acceptance rationale**: QA fail-open is deliberate and tested (confirmed in R108 audit). The violation is evidence erasure, not fail-open itself. PAT-0014 established to govern this class.
- **Mitigation**: PAT-0014 (Advisory Gate Transparency) added in R108. Code changes to implement advisory status taxonomy deferred to R109.
