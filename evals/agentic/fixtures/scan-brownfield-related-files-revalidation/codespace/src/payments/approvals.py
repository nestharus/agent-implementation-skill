def requires_dual_approval(amount_cents: int, actor_role: str) -> bool:
    return amount_cents >= 500000 and actor_role != "finance-admin"
