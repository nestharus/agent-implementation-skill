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


class PipelineControlService:
    """Pipeline control: parent pausing, message polling, pending handling."""

    def pause_for_parent(self, planspace, parent, message) -> str:
        from orchestrator.service.pipeline_control import pause_for_parent
        return pause_for_parent(planspace, parent, message)

    def poll_control_messages(self, planspace, parent, current_section=None) -> str | None:
        from orchestrator.service.pipeline_control import poll_control_messages
        return poll_control_messages(planspace, parent, current_section)

    def handle_pending_messages(self, planspace, sections, affected) -> bool:
        from orchestrator.service.pipeline_control import handle_pending_messages
        return handle_pending_messages(planspace, sections, affected)


class Communicator:
    """Inter-agent communication: mailbox, artifact logging, traceability."""

    def mailbox_send(self, planspace, target, message):
        from signals.service.communication import mailbox_send
        return mailbox_send(planspace, target, message)

    def log_artifact(self, planspace, artifact_name):
        from signals.service.communication import _log_artifact
        return _log_artifact(planspace, artifact_name)

    def record_traceability(self, planspace, section_number, file_path, source, category=""):
        from signals.service.communication import _record_traceability
        return _record_traceability(planspace, section_number, file_path, source, category)


class LogService:
    """Structured logging to coordination database."""

    def log(self, msg: str) -> None:
        from signals.service.communication import log
        log(msg)


class TaskRouterService:
    """Agent file routing and resolution."""

    def agent_for(self, task_type: str) -> str:
        from taskrouter import agent_for
        return agent_for(task_type)

    def resolve_agent_path(self, agent_file: str):
        from taskrouter.agents import resolve_agent_path
        return resolve_agent_path(agent_file)


class HasherService:
    """Content and file hashing."""

    def file_hash(self, path) -> str:
        from staleness.helpers.hashing import file_hash
        return file_hash(path)

    def content_hash(self, data) -> str:
        from staleness.helpers.hashing import content_hash
        return content_hash(data)

    def fingerprint(self, items: list[str]) -> str:
        from staleness.helpers.hashing import fingerprint
        return fingerprint(items)


class ArtifactIOService:
    """JSON file read/write with corruption preservation."""

    def read_json(self, path):
        from signals.repository.artifact_io import read_json
        return read_json(path)

    def write_json(self, path, data, *, indent: int = 2) -> None:
        from signals.repository.artifact_io import write_json
        write_json(path, data, indent=indent)

    def read_if_exists(self, path) -> str:
        from signals.repository.artifact_io import read_if_exists
        return read_if_exists(path)

    def read_json_or_default(self, path, default):
        from signals.repository.artifact_io import read_json_or_default
        return read_json_or_default(path, default)

    def rename_malformed(self, path):
        from signals.repository.artifact_io import rename_malformed
        return rename_malformed(path)


class DispatchHelperService:
    """Cross-cutting dispatch helpers: signals, summaries, model-choice audit."""

    def check_agent_signals(
        self, output, signal_path=None, output_path=None,
        planspace=None, parent=None, codespace=None,
    ):
        from dispatch.helpers.utils import check_agent_signals
        return check_agent_signals(
            output, signal_path, output_path, planspace, parent, codespace,
        )

    def summarize_output(self, output: str, max_len: int = 200) -> str:
        from dispatch.helpers.utils import summarize_output
        return summarize_output(output, max_len)

    def write_model_choice_signal(
        self, planspace, section, step, model, reason,
        escalated_from=None,
    ) -> None:
        from dispatch.helpers.utils import write_model_choice_signal
        write_model_choice_signal(
            planspace, section, step, model, reason, escalated_from,
        )


class ContextAssemblyService:
    """Context sidecar materialization for agent dispatch."""

    def materialize_context_sidecar(self, agent_file_path, planspace, section=None):
        from dispatch.service.context_sidecar import materialize_context_sidecar
        return materialize_context_sidecar(agent_file_path, planspace, section)


class CrossSectionService:
    """Cross-section decision persistence, summaries, and note exchange."""

    def persist_decision(self, planspace, section_number: str, payload: str) -> None:
        from coordination.service.cross_section import persist_decision
        persist_decision(planspace, section_number, payload)

    def extract_section_summary(self, path) -> str:
        from orchestrator.service.section_decisions import extract_section_summary
        return extract_section_summary(path)

    def read_incoming_notes(self, planspace, section_number: str) -> list[dict]:
        from coordination.service.cross_section import read_incoming_notes
        return read_incoming_notes(planspace, section_number)

    def write_consequence_note(self, planspace, from_section, to_section, content):
        from coordination.repository.notes import write_consequence_note
        return write_consequence_note(planspace, from_section, to_section, content)


class FlowIngestionService:
    """Flow task submission and ingestion."""

    def ingest_and_submit(self, planspace, db_path, submitted_by, signal_path, **kwargs):
        from flow.service.section_ingestion import ingest_and_submit
        return ingest_and_submit(planspace, db_path, submitted_by, signal_path, **kwargs)

    def submit_chain(self, db_path, submitted_by, steps, **kwargs):
        from flow.engine.submitter import submit_chain
        return submit_chain(db_path, submitted_by, steps, **kwargs)


class StalenessDetectionService:
    """File snapshot and diff detection for implementation tracking."""

    def snapshot_files(self, codespace, rel_paths: list[str]) -> dict[str, str]:
        from staleness.helpers.detection import snapshot_files
        return snapshot_files(codespace, rel_paths)

    def diff_files(self, codespace, before: dict[str, str], reported: list[str]) -> list[str]:
        from staleness.helpers.detection import diff_files
        return diff_files(codespace, before, reported)


# ---------------------------------------------------------------------------
# Container
# ---------------------------------------------------------------------------

class Services(containers.DeclarativeContainer):
    """Root container — one provider per cross-cutting service."""

    dispatcher = providers.Singleton(AgentDispatcher)
    prompt_guard = providers.Singleton(PromptGuard)
    policies = providers.Singleton(ModelPolicyService)
    signals = providers.Singleton(SignalReader)
    pipeline_control = providers.Singleton(PipelineControlService)
    communicator = providers.Singleton(Communicator)
    logger = providers.Singleton(LogService)
    task_router = providers.Singleton(TaskRouterService)
    hasher = providers.Singleton(HasherService)
    artifact_io = providers.Singleton(ArtifactIOService)
    dispatch_helpers = providers.Singleton(DispatchHelperService)
    context_assembly = providers.Singleton(ContextAssemblyService)
    cross_section = providers.Singleton(CrossSectionService)
    flow_ingestion = providers.Singleton(FlowIngestionService)
    staleness = providers.Singleton(StalenessDetectionService)
