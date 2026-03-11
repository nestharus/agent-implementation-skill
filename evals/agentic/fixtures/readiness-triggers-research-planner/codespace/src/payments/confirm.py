from .gateway_client import confirm_with_gateway
from .idempotency import idempotency_key


def confirm_payment(order_id: str, access_token: str) -> dict:
    key = idempotency_key(order_id)
    return confirm_with_gateway(
        order_id=order_id,
        access_token=access_token,
        idempotency_key=key,
    )
