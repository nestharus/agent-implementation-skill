# Problem Frame — Section 01

We need authoritative guidance before implementation can proceed.

Open factual questions:
1. What retry/backoff behavior is safe for repeated payment confirmation attempts
   that already use idempotency keys?
2. What token-refresh or clock-skew handling is required when the upstream
   gateway rejects a near-expiry access token?

The current code confirms payments directly against the gateway and assumes the
provided access token is always fresh.
