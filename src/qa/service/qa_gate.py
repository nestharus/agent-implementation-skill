"""Shared QA gate evaluation — reads parameters and runs the interceptor.

Extracts the common "should we QA-gate this?" evaluation from both
``section_dispatcher`` and ``task_dispatcher``.  Each caller keeps its
own rejection-handling logic because they differ:

- section_dispatcher returns ``DispatchResult(QA_REJECTED, ...)``.
- task_dispatcher calls ``record_qa_intercept``, ``fail-task``, and
  ``notify_task_result``.

Only the evaluation (read params, check qa_mode, call interceptor,
handle import/runtime errors) is shared here.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def evaluate_qa_gate(
    planspace: Path,
    section_number: str | None,
    agent_file: str,
    model: str,
    prompt_path: Path,
    *,
    task: dict[str, str] | None = None,
    submitted_by: str = "section-loop",
) -> "InterceptResult | None":
    """Evaluate QA gate.  Returns InterceptResult if QA is enabled, None if disabled or unavailable.

    Parameters
    ----------
    planspace:
        Root planspace directory.
    section_number:
        Section being dispatched, if any (unused here but reserved for
        future per-section QA policies).
    agent_file:
        Basename of the agent definition file (e.g. ``"alignment-judge.md"``).
    model:
        Model string (unused here but reserved for future model-aware QA).
    prompt_path:
        Path to the prompt being dispatched.
    task:
        If called from the task dispatcher, the full task dict.  When
        provided, ``intercept_task()`` is called directly.  Otherwise
        ``intercept_dispatch()`` is used with a synthetic task.
    submitted_by:
        Identity of the submitter (used when ``task`` is None).

    Returns
    -------
    InterceptResult | None
        ``None`` when QA is disabled or unavailable (caller should
        proceed with dispatch).  An ``InterceptResult`` when QA ran
        (caller inspects ``.intercepted`` to decide pass/reject).
    """
    from qa.service.qa_interceptor import InterceptResult

    # 1. Read QA parameters — fail open on import/read errors.
    try:
        from qa.service.qa_interceptor import read_qa_parameters
        qa_params = read_qa_parameters(planspace)
    except Exception:  # noqa: BLE001 — fail-open: QA errors must not block pipeline
        logger.warning(
            "QA interceptor import failed, skipping QA gate", exc_info=True,
        )
        return None

    if not qa_params.get("qa_mode"):
        return None

    # 2. Run the appropriate intercept function.
    try:
        if task is not None:
            from qa.service.qa_interceptor import intercept_task
            return intercept_task(task, agent_file, planspace)
        else:
            from qa.service.qa_interceptor import intercept_dispatch
            return intercept_dispatch(
                agent_file=agent_file,
                prompt_path=prompt_path,
                planspace=planspace,
                submitted_by=submitted_by,
            )
    except Exception as exc:  # noqa: BLE001 — fail-open: QA errors must not block dispatch
        logger.error(
            "QA evaluation error: %s — failing open (degraded)", exc, exc_info=True,
        )
        return InterceptResult(intercepted=True, verdict=None, output_path="dispatch_error")
