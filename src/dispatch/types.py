from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto


ALIGNMENT_CHANGED_PENDING = "ALIGNMENT_CHANGED_PENDING"


class DispatchStatus(Enum):
    SUCCESS = auto()
    ALIGNMENT_CHANGED = auto()
    TIMEOUT = auto()
    QA_REJECTED = auto()


@dataclass(frozen=True, slots=True)
class DispatchResult:
    status: DispatchStatus
    output: str

    @property
    def succeeded(self) -> bool:
        return self.status is DispatchStatus.SUCCESS

    def __eq__(self, other):
        if isinstance(other, str):
            # Backward compatibility: allow comparison with sentinel strings
            if other == ALIGNMENT_CHANGED_PENDING:
                return self.status is DispatchStatus.ALIGNMENT_CHANGED
            if other.startswith("QA_REJECTED:"):
                return self.status is DispatchStatus.QA_REJECTED
            return self.output == other
        return super().__eq__(other)

    def __str__(self) -> str:
        if self.status is DispatchStatus.ALIGNMENT_CHANGED:
            return ALIGNMENT_CHANGED_PENDING
        if self.status is DispatchStatus.QA_REJECTED:
            return f"QA_REJECTED:{self.output}"
        return self.output

    def __hash__(self):
        return hash((self.status, self.output))
