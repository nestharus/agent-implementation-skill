"""Philosophy dispatch: agent dispatch with classified signal retry.

Provides the retry-with-escalation pattern used by the bootstrap
pipeline when calling selector, verifier, and distiller agents.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, TYPE_CHECKING

from intent.service.philosophy_classifier import ClassifierState
from pipeline.context import DispatchContext

if TYPE_CHECKING:
    from containers import AgentDispatcher, LogService


def _attempt_output_path(output_path: Path, attempt: int) -> Path:
    if attempt == 1:
        return output_path
    return output_path.with_name(
        f"{output_path.stem}-{attempt}{output_path.suffix}"
    )


def _record_stage_attempt(
    attempts: list[dict[str, Any]],
    *,
    attempt: int,
    model: str,
    classification: dict[str, Any],
) -> None:
    entry: dict[str, Any] = {
        "attempt": attempt,
        "model": model,
        "result": classification["state"],
    }
    preserved = classification.get("preserved")
    if preserved:
        entry["preserved"] = Path(preserved).name
    attempts.append(entry)


class PhilosophyDispatcher:
    """Agent dispatch with classified signal retry."""

    def __init__(
        self,
        dispatcher: AgentDispatcher,
        logger: LogService,
    ) -> None:
        self._dispatcher = dispatcher
        self._logger = logger

    def _dispatch_with_signal_check(
        self,
        model: str,
        prompt: Path,
        output: Path,
        ctx: DispatchContext,
        *,
        expected_signal: Path,
        classifier: Callable[[Path], dict[str, Any]],
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Dispatch an agent and verify the expected signal artifact exists."""
        self._dispatcher.dispatch(
            model, prompt, output, ctx.planspace, **kwargs,
        )
        return classifier(expected_signal)

    def _dispatch_classified_signal_stage(
        self,
        *,
        stage_name: str,
        prompt_path: Path,
        output_path: Path,
        signal_path: Path,
        models: list[str],
        classifier: Callable[[Path], dict[str, Any]],
        ctx: DispatchContext,
        agent_file: str,
    ) -> dict[str, Any]:
        attempts: list[dict[str, Any]] = []
        classification: dict[str, Any] = {"state": ClassifierState.MISSING_SIGNAL, "data": None}

        for attempt, model in enumerate(models, start=1):
            signal_path.unlink(missing_ok=True)
            classification = self._dispatch_with_signal_check(
                model,
                prompt_path,
                _attempt_output_path(output_path, attempt),
                ctx,
                expected_signal=signal_path,
                classifier=classifier,
                codespace=ctx.codespace,
                agent_file=agent_file,
            )
            _record_stage_attempt(
                attempts,
                attempt=attempt,
                model=model,
                classification=classification,
            )
            if classification["state"].startswith("valid_"):
                break
            if attempt < len(models):
                next_model = models[attempt]
                action = "retrying" if next_model == model else "escalating"
                self._logger.log(
                    f"Intent bootstrap: {stage_name} produced "
                    f"{classification['state']} on attempt {attempt}/{len(models)} "
                    f"— {action} with {next_model}"
                )

        return {
            "classification": classification,
            "attempts": attempts,
        }


# ---------------------------------------------------------------------------
# Backward-compat wrappers
# ---------------------------------------------------------------------------

def _get_philosophy_dispatcher() -> PhilosophyDispatcher:
    from containers import Services
    return PhilosophyDispatcher(
        dispatcher=Services.dispatcher(),
        logger=Services.logger(),
    )


def _dispatch_with_signal_check(
    model: str,
    prompt: Path,
    output: Path,
    ctx: DispatchContext,
    *,
    expected_signal: Path,
    classifier: Callable[[Path], dict[str, Any]],
    **kwargs: Any,
) -> dict[str, Any]:
    """Dispatch an agent and verify the expected signal artifact exists."""
    return _get_philosophy_dispatcher()._dispatch_with_signal_check(
        model, prompt, output, ctx,
        expected_signal=expected_signal,
        classifier=classifier,
        **kwargs,
    )


def _dispatch_classified_signal_stage(
    *,
    stage_name: str,
    prompt_path: Path,
    output_path: Path,
    signal_path: Path,
    models: list[str],
    classifier: Callable[[Path], dict[str, Any]],
    ctx: DispatchContext,
    agent_file: str,
) -> dict[str, Any]:
    return _get_philosophy_dispatcher()._dispatch_classified_signal_stage(
        stage_name=stage_name,
        prompt_path=prompt_path,
        output_path=output_path,
        signal_path=signal_path,
        models=models,
        classifier=classifier,
        ctx=ctx,
        agent_file=agent_file,
    )
