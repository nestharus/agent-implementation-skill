from .approvals import requires_dual_approval


def submit_refund(amount_cents: int, actor_role: str) -> str:
    if requires_dual_approval(amount_cents, actor_role):
        return "queued_for_manual_approval"
    return "submitted"
