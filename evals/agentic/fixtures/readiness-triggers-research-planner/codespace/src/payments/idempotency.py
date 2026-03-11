def idempotency_key(order_id: str) -> str:
    return f"confirm:{order_id}"
