"""Philosophy dispatch: agent dispatch with classified signal retry.

Provides the retry-with-escalation pattern used by the bootstrap
pipeline when calling selector, verifier, and distiller agents.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from containers import Services
from signals.service.communication import log


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


def _dispatch_with_signal_check(
    model: str,
    prompt: Path,
    output: Path,
    planspace: Path,
    parent: str,
    *,
    expected_signal: Path,
    classifier: Callable[[Path], dict[str, Any]],
    **kwargs: Any,
) -> dict[str, Any]:
    """Dispatch an agent and verify the expected signal artifact exists."""
    Services.dispatcher().dispatch(model, prompt, output, planspace, parent, **kwargs)
    return classifier(expected_signal)


def _dispatch_classified_signal_stage(
    *,
    stage_name: str,
    prompt_path: Path,
    output_path: Path,
    signal_path: Path,
    models: list[str],
    classifier: Callable[[Path], dict[str, Any]],
    planspace: Path,
    parent: str,
    codespace: Path,
    agent_file: str,
) -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []
    classification: dict[str, Any] = {"state": "missing_signal", "data": None}

    for attempt, model in enumerate(models, start=1):
        signal_path.unlink(missing_ok=True)
        classification = _dispatch_with_signal_check(
            model,
            prompt_path,
            _attempt_output_path(output_path, attempt),
            planspace,
            parent,
            expected_signal=signal_path,
            classifier=classifier,
            codespace=codespace,
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
            log(
                f"Intent bootstrap: {stage_name} produced "
                f"{classification['state']} on attempt {attempt}/{len(models)} "
                f"— {action} with {next_model}"
            )

    return {
        "classification": classification,
        "attempts": attempts,
    }
