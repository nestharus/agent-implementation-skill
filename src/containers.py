"""Dependency injection containers.

Central wiring for cross-cutting services.  Systems receive
dependencies from these containers instead of importing functions
directly.  Tests override providers — no monkeypatching import sites.

Usage — production::

    from containers import Services

    result = Services.dispatcher.dispatch(model, prompt_path, ...)
    policy = Services.policies.load(planspace)
    signal = Services.signals.read(signal_path)

Usage — tests::

    Services.dispatcher.override(providers.Object(mock_dispatcher))
    # ... test ...
    Services.dispatcher.reset_override()
"""

from __future__ import annotations

from dependency_injector import containers, providers


# ---------------------------------------------------------------------------
# Service classes
# ---------------------------------------------------------------------------

class AgentDispatcher:
    """Dispatches agents to LLM providers.

    Wraps ``dispatch.engine.section_dispatch.dispatch_agent``.
    """

    def dispatch(
        self,
        model: str,
        prompt_path,
        output_path,
        planspace=None,
        parent: str | None = None,
        agent_name: str | None = None,
        codespace=None,
        section_number: str | None = None,
        *,
        agent_file: str,
    ) -> str:
        from dispatch.engine.section_dispatch import dispatch_agent
        return dispatch_agent(
            model, prompt_path, output_path, planspace, parent,
            agent_name, codespace, section_number,
            agent_file=agent_file,
        )


class PromptGuard:
    """Prompt safety validation.

    Wraps ``dispatch.service.prompt_guard`` functions.
    """

    def write_validated(self, content: str, path) -> bool:
        from dispatch.service.prompt_guard import write_validated_prompt
        return write_validated_prompt(content, path)

    def validate_dynamic(self, content: str) -> list[str]:
        from dispatch.service.prompt_guard import validate_dynamic_content
        return validate_dynamic_content(content)


class ModelPolicyService:
    """Model policy loading and resolution.

    Wraps ``dispatch.service.model_policy`` functions.
    """

    def load(self, planspace):
        from dispatch.service.model_policy import load_model_policy
        return load_model_policy(planspace)

    def resolve(self, policy, key: str) -> str:
        from dispatch.service.model_policy import resolve
        return resolve(policy, key)


class SignalReader:
    """Structured agent signal reading.

    Wraps ``signals.repository.signal_reader`` functions.
    """

    def read(self, signal_path, expected_fields=None):
        from signals.repository.signal_reader import read_agent_signal
        signal = read_agent_signal(signal_path)
        if signal is None:
            return None
        if expected_fields:
            for field_name in expected_fields:
                if field_name not in signal:
                    return None
        return signal

    def read_tuple(self, signal_path):
        from signals.repository.signal_reader import read_signal_tuple
        return read_signal_tuple(signal_path)


# ---------------------------------------------------------------------------
# Container
# ---------------------------------------------------------------------------

class Services(containers.DeclarativeContainer):
    """Root container — one provider per cross-cutting service."""

    dispatcher = providers.Singleton(AgentDispatcher)
    prompt_guard = providers.Singleton(PromptGuard)
    policies = providers.Singleton(ModelPolicyService)
    signals = providers.Singleton(SignalReader)
