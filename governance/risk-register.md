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
- **Region**: QA interceptor, task dispatcher, QA verdict parser, lifecycle logging, reconciliation adjudicator
- **Description**: When QA interception encounters internal errors, missing targets, or unparseable output, the degraded outcome was logged identically to genuine approval (`qa:passed`). QA verdict parser mapped malformed output to PASS. Task dispatcher treated QA exceptions as `passed = True`. This erased the evidence distinction between "QA evaluated and approved" and "QA failed, dispatch fell back to baseline." Reconciliation adjudicator had similar degradation visibility issues.
- **Severity**: low
- **Status**: resolved
- **Acceptance rationale**: N/A — resolved.
- **Mitigation**: R109 implemented full advisory status taxonomy per PAT-0014: QA verdict parser returns DEGRADED (not PASS) for malformed/unknown output; interceptor returns 3-tuple `(passed, rationale_path, reason_code)` with codes `unparseable`/`dispatch_error`/`target_unavailable`/`safety_blocked`; dispatcher logs `qa:degraded:{task_id}:{reason_code}` distinctly from `qa:passed`; notifier carries reason_code through lifecycle events; reconciliation adjudicator references PAT-0014 degraded states.

---

### RISK-0005: Governance pattern projection truncation

- **Category**: pattern-drift / operability
- **Region**: governance loader, packet builder
- **Description**: The governance loader's `_extract_bullets` stopped on continuation lines (wrapped bullet items) and did not parse numbered template lists, causing `pattern-index.json` to lose structure from the real catalog. PAT-0001 was reduced from 9 known instances to 1 and 5 template items to 1; PAT-0011 dropped from 17 known instances to 5 and 7 template items to 3.
- **Severity**: medium
- **Status**: resolved
- **Acceptance rationale**: N/A — resolved.
- **Mitigation**: R110 fixed `_extract_bullets` to join continuation lines and parse numbered items. Representative contract test added with wrapped bullets and numbered templates from the real catalog shape.

---

### RISK-0006: Related-files signal-family ambiguity

- **Category**: coherence / operability
- **Region**: scan related-files surfaces, substrate wiring surfaces, PathRegistry
- **Description**: Scan-stage and substrate-stage related-files update signals used different durable layouts (`signals/{name}-related-files-update.json` vs `signals/related-files-update/section-{num}.json`), but only the substrate path had a PathRegistry accessor. The scan path was constructed ad-hoc, creating migration-ambiguity risk.
- **Severity**: low
- **Status**: resolved
- **Acceptance rationale**: N/A — resolved.
- **Mitigation**: R110 added `scan_related_files_update_signal()` accessor to PathRegistry, documented `related_files_update_dir()` as substrate-specific, extended PAT-0003 template with rule 7, and added a contract test verifying path distinctness.
