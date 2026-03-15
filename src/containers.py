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

from typing import TYPE_CHECKING

from dependency_injector import containers, providers

if TYPE_CHECKING:
    from dispatch.types import DispatchResult


# ---------------------------------------------------------------------------
# Service classes
# ---------------------------------------------------------------------------

class ConfigService:
    """Agent configuration constants routed through DI."""

    def __init__(self) -> None:
        from _config import AGENT_NAME, DB_SH
        self._agent_name = AGENT_NAME
        self._db_sh = DB_SH

    @property
    def agent_name(self) -> str:
        return self._agent_name

    @property
    def db_sh(self):
        return self._db_sh


class AgentDispatcher:
    """Dispatches agents to LLM providers.

    Delegates to ``dispatch.engine.section_dispatcher.SectionDispatcher``.
    """

    def _get(self):
        from dispatch.engine.section_dispatcher import SectionDispatcher
        return SectionDispatcher(
            config=Services.config(),
            pipeline_control=Services.pipeline_control(),
            logger=Services.logger(),
            communicator=Services.communicator(),
            task_router=Services.task_router(),
            prompt_guard=Services.prompt_guard(),
            artifact_io=Services.artifact_io(),
        )

    def dispatch(
        self,
        model: str,
        prompt_path,
        output_path,
        planspace=None,
        agent_name: str | None = None,
        codespace=None,
        section_number: str | None = None,
        *,
        agent_file: str,
    ) -> DispatchResult:
        return self._get().dispatch_agent(
            model, prompt_path, output_path, planspace,
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

    Delegates to ``dispatch.service.model_policy.ModelPolicyLoader``.
    """

    def _get_loader(self):
        from dispatch.service.model_policy import ModelPolicyLoader
        return ModelPolicyLoader(artifact_io=Services.artifact_io())

    def load(self, planspace):
        return self._get_loader().load_model_policy(planspace)

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
    """Pipeline control: pausing, polling, alignment, requeue, hashing."""

    def _get(self):
        from orchestrator.service.pipeline_control import PipelineControl
        return PipelineControl(
            config=Services.config(),
            logger=Services.logger(),
            change_tracker=Services.change_tracker(),
        )

    def pause_for_parent(self, planspace, message) -> str:
        return self._get().pause_for_parent(planspace, message)

    def poll_control_messages(self, planspace, current_section=None) -> str | None:
        return self._get().poll_control_messages(planspace, current_section)

    def handle_pending_messages(self, planspace) -> bool:
        return self._get().handle_pending_messages(planspace)

    def alignment_changed_pending(self, planspace) -> bool:
        from staleness.service.change_tracker import check_pending
        return check_pending(planspace)

    def check_alignment_and_raise(self, planspace, checker, exc_class, message=""):
        """Check alignment, clear if changed, raise *exc_class*.

        Consolidates the common guard pattern::

            if alignment_changed_pending(planspace):
                if _check_and_clear(planspace):
                    log(message)
                    raise SomeException

        Also handles the single-call variant where only
        ``_check_and_clear`` is called (the checker already returns
        ``False`` when no flag is set, so the pending pre-check is
        optional).

        *checker* is the callable returned by
        ``ChangeTrackerService.make_alignment_checker()``.
        """
        if checker(planspace):
            if message:
                from containers import Services
                Services.logger().log(message)
            raise exc_class()

    def check_alignment_and_return(self, planspace, checker) -> bool:
        """Check alignment, clear if changed, return ``True`` if changed.

        Consolidates the common guard pattern::

            if alignment_changed_pending(planspace):
                log(...)
                return True  # or return None / "abort"

        Also handles the single-call variant where only
        ``_check_and_clear`` is called.

        Returns ``True`` when the caller should abort/return, ``False``
        otherwise.  The caller decides what value to return.
        """
        return checker(planspace)

    def wait_if_paused(self, planspace) -> None:
        return self._get().wait_if_paused(planspace)

    def requeue_changed_sections(
        self, completed, queue, sections_by_num, planspace,
        *, current_section=None,
    ) -> list[str]:
        return self._get().requeue_changed_sections(
            completed, queue, sections_by_num, planspace,
            current_section=current_section,
        )

    def section_inputs_hash(self, section_number, planspace, sections_by_num=None) -> str:
        from staleness.service.input_hasher import section_inputs_hash
        if sections_by_num is None:
            sections_by_num = {}
        return section_inputs_hash(section_number, planspace, sections_by_num)

    def coordination_recheck_hash(self, sec_num, planspace, codespace, sections_by_num=None, modified_files=None) -> str:
        from staleness.service.input_hasher import coordination_recheck_hash
        if sections_by_num is None:
            sections_by_num = {}
        if modified_files is None:
            modified_files = []
        return coordination_recheck_hash(sec_num, planspace, codespace, sections_by_num, modified_files)


class Communicator:
    """Inter-agent communication: mailbox, artifact logging, traceability."""

    def __init__(self) -> None:
        self._parent: str = ""

    def _get(self):
        from signals.service.section_communicator import SectionCommunicator
        return SectionCommunicator(config=Services.config())

    def set_parent(self, parent: str) -> None:
        """Set the parent mailbox name (called once at pipeline startup)."""
        self._parent = parent

    def mailbox_send(self, planspace, target, message):
        return self._get().mailbox_send(planspace, target, message)

    def send_to_parent(self, planspace, message):
        if not self._parent:
            return
        self.mailbox_send(planspace, self._parent, message)

    def log_artifact(self, planspace, artifact_name):
        return self._get().log_artifact(planspace, artifact_name)

    def record_traceability(self, planspace, section_number, file_path, source, category=""):
        from signals.service.section_communicator import _record_traceability
        return _record_traceability(planspace, section_number, file_path, source, category)

    def mailbox_register(self, planspace):
        return self._get().mailbox_register(planspace)

    def mailbox_cleanup(self, planspace):
        return self._get().mailbox_cleanup(planspace)


class LogService:
    """Structured logging to coordination database."""

    def log(self, msg: str) -> None:
        from signals.service.section_communicator import log
        log(msg)

    def log_lifecycle(self, planspace, event: str, status: str) -> None:
        """Log a lifecycle event to the coordination database."""
        import subprocess
        cfg = Services.config()
        subprocess.run(  # noqa: S603
            [
                "bash",
                str(cfg.db_sh),  # noqa: S607
                "log",
                str(planspace / "run.db"),
                "lifecycle",
                event,
                status,
                "--agent",
                cfg.agent_name,
            ],
            capture_output=True,
            text=True,
        )


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
        from staleness.helpers.content_hasher import file_hash
        return file_hash(path)

    def content_hash(self, data) -> str:
        from staleness.helpers.content_hasher import content_hash
        return content_hash(data)

    def fingerprint(self, items: list[str]) -> str:
        from staleness.helpers.content_hasher import fingerprint
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

    def _get(self):
        from dispatch.helpers.signal_checker import SignalChecker
        return SignalChecker(
            artifact_io=Services.artifact_io(),
            signals=Services.signals(),
        )

    def check_agent_signals(
        self, signal_path=None,
    ):
        return self._get().check_agent_signals(signal_path)

    def summarize_output(self, output: str, max_len: int = 200) -> str:
        from dispatch.helpers.signal_checker import summarize_output
        return summarize_output(output, max_len)

    def write_model_choice_signal(
        self, planspace, section, step, model, reason,
        escalated_from=None,
    ) -> None:
        return self._get().write_model_choice_signal(
            planspace, section, step, model, reason, escalated_from,
        )


class ContextAssemblyService:
    """Context sidecar materialization for agent dispatch."""

    def _get(self):
        from dispatch.service.context_sidecar import ContextSidecar
        return ContextSidecar(artifact_io=Services.artifact_io())

    def materialize_context_sidecar(self, agent_file_path, planspace, section=None):
        return self._get().materialize_context_sidecar(agent_file_path, planspace, section)


class CrossSectionService:
    """Cross-section decision persistence, summaries, and note exchange."""

    def _get_recorder(self):
        from coordination.service.decision_recorder import DecisionRecorder
        return DecisionRecorder(communicator=Services.communicator())

    def persist_decision(self, planspace, section_number: str, payload: str) -> None:
        return self._get_recorder().persist_decision(planspace, section_number, payload)

    def extract_section_summary(self, path) -> str:
        from orchestrator.service.section_decision_store import extract_section_summary
        return extract_section_summary(path)

    def write_consequence_note(self, planspace, from_section, to_section, content):
        from coordination.repository.notes import write_consequence_note
        return write_consequence_note(planspace, from_section, to_section, content)


class FlowIngestionService:
    """Flow task submission and ingestion."""

    def _get_submitter(self):
        from flow.engine.flow_submitter import FlowSubmitter
        from flow.repository.flow_context_store import FlowContextStore
        return FlowSubmitter(
            freshness=Services.freshness(),
            flow_context_store=FlowContextStore(Services.artifact_io()),
        )

    def _get_ingestor(self):
        from flow.service.task_request_ingestor import TaskRequestIngestor
        from flow.service.flow_signal_parser import FlowSignalParser
        return TaskRequestIngestor(
            freshness=Services.freshness(),
            logger=Services.logger(),
            flow_submitter=self._get_submitter(),
            signal_parser=FlowSignalParser(
                logger=Services.logger(),
                artifact_io=Services.artifact_io(),
            ),
        )

    def ingest_and_submit(self, planspace, submitted_by, signal_path, *, db_path=None, **kwargs):
        return self._get_ingestor().ingest_and_submit(
            planspace, submitted_by, signal_path, db_path=db_path, **kwargs,
        )

    def submit_chain(self, env, steps, **kwargs):
        return self._get_submitter().submit_chain(env, steps, **kwargs)

    def submit_fanout(self, env, branches, **kwargs):
        return self._get_submitter().submit_fanout(env, branches, **kwargs)

    def new_flow_id(self) -> str:
        from flow.types.context import new_flow_id
        return new_flow_id()


class StalenessDetectionService:
    """File snapshot and diff detection for implementation tracking."""

    def snapshot_files(self, codespace, rel_paths: list[str]) -> dict[str, str]:
        from staleness.helpers.file_differ import snapshot_files
        return snapshot_files(codespace, rel_paths)

    def diff_files(self, codespace, before: dict[str, str], reported: list[str]) -> list[str]:
        from staleness.helpers.file_differ import diff_files
        return diff_files(codespace, before, reported)


class ChangeTrackerService:
    """Alignment change flag and excerpt invalidation."""

    def set_flag(self, planspace) -> None:
        from staleness.service.change_tracker import set_flag
        cfg = Services.config()
        set_flag(planspace, db_sh=cfg.db_sh, agent_name=cfg.agent_name)

    def make_alignment_checker(self):
        from staleness.service.change_tracker import make_alignment_checker
        cfg = Services.config()
        return make_alignment_checker(cfg.db_sh, cfg.agent_name)

    def invalidate_excerpts(self, planspace) -> None:
        from staleness.service.change_tracker import invalidate_excerpts
        invalidate_excerpts(planspace)


class FreshnessService:
    """Section freshness token computation."""

    def compute(self, planspace, section_number: str) -> str:
        from staleness.service.freshness_calculator import compute_section_freshness
        return compute_section_freshness(planspace, section_number)


class ResearchOrchestratorService:
    """Research lifecycle: status, plan validation, execution, verification."""

    def _get_orchestrator(self):
        from research.engine.orchestrator import ResearchOrchestrator
        return ResearchOrchestrator(
            hasher=Services.hasher(),
            artifact_io=Services.artifact_io(),
        )

    def _get_prompt_writer(self):
        from research.prompt.writers import ResearchPromptWriter
        return ResearchPromptWriter(
            prompt_guard=Services.prompt_guard(),
            artifact_io=Services.artifact_io(),
        )

    def _get_branch_builder(self):
        from research.engine.research_branch_builder import ResearchBranchBuilder
        return ResearchBranchBuilder(
            prompt_guard=Services.prompt_guard(),
            artifact_io=Services.artifact_io(),
            prompt_writer=self._get_prompt_writer(),
        )

    def _get_executor(self):
        from research.engine.research_plan_executor import ResearchPlanExecutor
        return ResearchPlanExecutor(
            freshness=Services.freshness(),
            flow_ingestion=Services.flow_ingestion(),
            orchestrator=self._get_orchestrator(),
            branch_builder=self._get_branch_builder(),
            prompt_writer=self._get_prompt_writer(),
        )

    def load_status(self, section_number, planspace):
        return self._get_orchestrator().load_research_status(section_number, planspace)

    def validate_plan(self, plan_path):
        return self._get_orchestrator().validate_research_plan(plan_path)

    def write_status(self, section_number, planspace, status, **kwargs):
        return self._get_orchestrator().write_research_status(
            section_number, planspace, status, **kwargs,
        )

    def compute_trigger_hash(self, questions):
        return self._get_orchestrator().compute_trigger_hash(questions)

    def is_complete_for_trigger(self, section_number, planspace, trigger_hash):
        return self._get_orchestrator().is_research_complete_for_trigger(
            section_number, planspace, trigger_hash,
        )

    def execute_plan(self, section_number, planspace, codespace, plan_output_path):
        return self._get_executor().execute_research_plan(
            section_number, planspace, codespace, plan_output_path,
        )

    def submit_verify(self, section_number, planspace, *, db_path, declared_by_task_id, origin_refs=None):
        return self._get_executor().submit_research_verify(
            section_number, planspace,
            db_path=db_path, declared_by_task_id=declared_by_task_id,
            origin_refs=origin_refs,
        )


class RiskAssessmentService:
    """Risk assessment dispatch: lightweight checks and full ROAL loop."""

    def _get(self):
        from risk.engine.risk_assessor import RiskAssessor
        return RiskAssessor(
            logger=Services.logger(),
            policies=Services.policies(),
            dispatcher=Services.dispatcher(),
            task_router=Services.task_router(),
            prompt_guard=Services.prompt_guard(),
            artifact_io=Services.artifact_io(),
        )

    def run_lightweight_check(self, planspace, scope, layer, package, posture_floor=None):
        return self._get().run_lightweight_risk_check(
            planspace, scope, layer, package, posture_floor,
        )

    def run_risk_loop(self, planspace, scope, layer, package, max_iterations=5, posture_floor=None):
        return self._get().run_risk_loop(
            planspace, scope, layer, package, max_iterations, posture_floor,
        )


class SectionAlignmentService:
    """Section alignment checking and problem extraction."""

    def _get_checker(self):
        from staleness.service.section_alignment_checker import SectionAlignmentChecker
        return SectionAlignmentChecker(
            logger=Services.logger(),
            dispatcher=Services.dispatcher(),
            task_router=Services.task_router(),
            pipeline_control=Services.pipeline_control(),
            prompt_guard=Services.prompt_guard(),
        )

    def _get_rechecker(self):
        from staleness.service.global_alignment_rechecker import GlobalAlignmentRechecker
        from coordination.service.completion_handler import CompletionHandler
        from implementation.service.impact_analyzer import ImpactAnalyzer
        return GlobalAlignmentRechecker(
            logger=Services.logger(),
            policies=Services.policies(),
            pipeline_control=Services.pipeline_control(),
            communicator=Services.communicator(),
            dispatch_helpers=Services.dispatch_helpers(),
            alignment_checker=self._get_checker(),
            completion_handler=CompletionHandler(
                artifact_io=Services.artifact_io(),
                change_tracker=Services.change_tracker(),
                communicator=Services.communicator(),
                hasher=Services.hasher(),
                impact_analyzer=ImpactAnalyzer(
                    communicator=Services.communicator(),
                    config=Services.config(),
                    context_assembly=Services.context_assembly(),
                    cross_section=Services.cross_section(),
                    dispatcher=Services.dispatcher(),
                    logger=Services.logger(),
                    policies=Services.policies(),
                    prompt_guard=Services.prompt_guard(),
                    task_router=Services.task_router(),
                ),
                logger=Services.logger(),
            ),
        )

    def extract_problems(
        self, result, output_path=None, planspace=None,
        codespace=None, *, adjudicator_model: str,
    ) -> str | None:
        return self._get_checker().extract_problems(
            result, output_path, planspace, codespace,
            adjudicator_model=adjudicator_model,
        )

    def collect_modified_files(self, planspace, section, codespace) -> list[str]:
        return self._get_checker().collect_modified_files(planspace, section, codespace)

    def run_alignment_check(
        self, section, planspace, codespace,
        output_prefix="align", max_retries=2, *, model: str,
    ):
        return self._get_checker().run_alignment_check_with_retries(
            section, planspace, codespace,
            output_prefix, max_retries,
            model=model,
        )

    def parse_alignment_verdict(self, result):
        from staleness.helpers.verdict_parsers import parse_alignment_verdict
        return parse_alignment_verdict(result)

    def run_global_recheck(
        self, sections_by_num, section_results,
        planspace, codespace,
    ) -> str:
        return self._get_rechecker().run_global_alignment_recheck(
            sections_by_num, section_results,
            planspace, codespace,
        )


# ---------------------------------------------------------------------------
# Container
# ---------------------------------------------------------------------------

class Services(containers.DeclarativeContainer):
    """Root container — one provider per cross-cutting service."""

    config = providers.Singleton(ConfigService)
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
    change_tracker = providers.Singleton(ChangeTrackerService)
    freshness = providers.Singleton(FreshnessService)
    research = providers.Singleton(ResearchOrchestratorService)
    risk_assessment = providers.Singleton(RiskAssessmentService)
    section_alignment = providers.Singleton(SectionAlignmentService)
