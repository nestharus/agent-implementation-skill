# Section 01 — Payment confirmation reliability

## Objective
Normalize payment-confirmation behavior so the service can safely retry gateway
calls without double-charging customers.

## Constraints
- Preserve idempotency for repeated confirmation attempts.
- Tolerate short-lived access-token expiry when calling the upstream gateway.
- Do not broaden this slice into refund policy or settlement reporting.
