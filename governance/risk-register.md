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

_No entries yet. Debt signal staging (R102) and bounded promotion consumer (R103) are wired. Entries will appear when post-implementation assessments emit `accept_with_debt` verdicts during runtime execution._
