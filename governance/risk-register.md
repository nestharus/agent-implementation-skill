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
