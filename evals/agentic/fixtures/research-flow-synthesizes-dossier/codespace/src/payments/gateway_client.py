def confirm_with_gateway(order_id: str, access_token: str, idempotency_key: str) -> dict:
    return {
        "order_id": order_id,
        "access_token_used": access_token,
        "idempotency_key": idempotency_key,
        "status": "confirmed",
    }
