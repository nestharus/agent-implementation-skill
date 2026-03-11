from .approvals import requires_dual_approval
from .ledger import record_refund


def submit_refund(refund_id: str, amount_cents: int, actor_role: str) -> str:
    if requires_dual_approval(amount_cents, actor_role):
        status = "queued_for_manual_approval"
    else:
        status = "submitted"
    record_refund(refund_id, status)
    return status
