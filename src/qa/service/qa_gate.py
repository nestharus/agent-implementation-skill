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

from containers import Services
from qa.service.qa_interceptor import InterceptResult, QaInterceptor

logger = logging.getLogger(__name__)


def evaluate_qa_gate(
    planspace: Path,
    agent_file: str,
    prompt_path: Path,
    *,
    task: dict[str, str] | None = None,
    submitted_by: str = "section-loop",
) -> InterceptResult | None:
    """Evaluate QA gate.  Returns InterceptResult if QA is enabled, None if disabled or unavailable.

    Parameters
    ----------
    planspace:
        Root planspace directory.
    agent_file:
        Basename of the agent definition file (e.g. ``"alignment-judge.md"``).
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
    # 1. Create interceptor from the DI container.
    try:
        interceptor = QaInterceptor(
            artifact_io=Services.artifact_io(),
            task_router=Services.task_router(),
            policies=Services.policies(),
            dispatcher=Services.dispatcher(),
            prompt_guard=Services.prompt_guard(),
        )
    except Exception:  # noqa: BLE001 — fail-open: QA errors must not block pipeline
        logger.warning(
            "QA interceptor creation failed, skipping QA gate", exc_info=True,
        )
        return None

    # 2. Read QA parameters — fail open on errors.
    try:
        qa_params = interceptor.read_qa_parameters(planspace)
    except Exception:  # noqa: BLE001 — fail-open
        logger.warning(
            "QA parameter read failed, skipping QA gate", exc_info=True,
        )
        return None

    if not qa_params.get("qa_mode"):
        return None

    # 3. Run the appropriate intercept function.
    try:
        if task is not None:
            return interceptor.intercept_task(task, agent_file, planspace)
        else:
            return interceptor.intercept_dispatch(
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
