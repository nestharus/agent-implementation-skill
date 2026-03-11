# Payments Service

This service confirms card payments through an upstream gateway. The current
implementation already uses an idempotency key, but retry and auth-refresh
behavior has not been designed yet.
