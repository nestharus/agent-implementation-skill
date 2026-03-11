# Section 01 — Refund approvals

## Objective
Define the refund approval workflow for large manual refunds.

## Constraints
- Refunds above $5,000 need a dual-approval path.
- Small refunds should remain automatic.

## Related Files

### src/payments/legacy_refunds.py
Legacy refund flow from an older design.

### src/payments/old_rules.py
Deprecated approval thresholds.
