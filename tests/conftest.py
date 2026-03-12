"""Shared fixtures for integration tests.

Mock boundary: only ``dispatch_agent`` (the LLM call) is mocked.
Everything else — file I/O, db.sh SQLite, hashing — runs for real.
"""

from __future__ import annotations

import contextlib
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from dependency_injector import providers

from _paths import DB_SH
from containers import (
    AgentDispatcher,
    ChangeTrackerService,
    Communicator,
    CrossSectionService,
    FlowIngestionService,
    FreshnessService,
    ModelPolicyService,
    PipelineControlService,
    PromptGuard,
    SectionAlignmentService,
    Services,
)


class MockDispatcher(AgentDispatcher):
    """Test double for AgentDispatcher that records calls."""

    def __init__(self) -> None:
        self.mock = MagicMock(return_value="")

    def dispatch(self, *args, **kwargs) -> str:
        return self.mock(*args, **kwargs)


def make_dispatcher(dispatch_fn) -> AgentDispatcher:
    """Factory: create a dispatcher that delegates to *dispatch_fn*."""

    class _Dispatcher(AgentDispatcher):
        def dispatch(self, *args, **kwargs):
            return dispatch_fn(*args, **kwargs)

    return _Dispatcher()


class WritingGuard(PromptGuard):
    """Test double that writes prompts to disk without validation."""

    def validate_dynamic(self, content):
        return []

    def write_validated(self, content, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return True


class NoOpFlow(FlowIngestionService):
    """Test double that silently discards flow ingestion calls."""

    def ingest_and_submit(self, *_args, **_kwargs):
        return None

    def submit_chain(self, *_args, **_kwargs):
        return [1]


class StubPolicies(ModelPolicyService):
    """Test double returning configurable model policy defaults."""

    def __init__(self, overrides: dict[str, str] | None = None) -> None:
        self._overrides = overrides or {}

    def load(self, planspace):
        return self._overrides

    def resolve(self, policy, key):
        return policy.get(key, "test-model")


@contextlib.contextmanager
def override_dispatcher_and_guard(fake_dispatch_fn):
    """Context manager that overrides Services.dispatcher and prompt_guard.

    Use this in place of ``patch.object(module, "dispatch_agent", ...)`` and
    ``patch.object(module, "validate_dynamic_content", ...)`` for modules
    that were migrated to the container.

    ``fake_dispatch_fn`` receives the same args as ``AgentDispatcher.dispatch``.

    Yields the ``fake_dispatch_fn`` (unchanged) so callers can inspect
    call state if needed.
    """

    class _NoopCrossSection(CrossSectionService):
        def persist_decision(self, *_args, **_kwargs):
            return None

        def extract_section_summary(self, path):
            return ""

        def write_consequence_note(self, *_args, **_kwargs):
            return None

    Services.dispatcher.override(providers.Object(make_dispatcher(fake_dispatch_fn)))
    Services.prompt_guard.override(providers.Object(WritingGuard()))
    Services.pipeline_control.override(providers.Object(NoOpPipelineControl()))
    Services.communicator.override(providers.Object(NoOpCommunicator()))
    Services.flow_ingestion.override(providers.Object(NoOpFlow()))
    Services.cross_section.override(providers.Object(_NoopCrossSection()))
    Services.change_tracker.override(providers.Object(NoOpChangeTracker()))
    Services.freshness.override(providers.Object(NoOpFreshness()))
    Services.section_alignment.override(providers.Object(NoOpSectionAlignment()))
    try:
        yield fake_dispatch_fn
    finally:
        Services.dispatcher.reset_override()
        Services.prompt_guard.reset_override()
        Services.pipeline_control.reset_override()
        Services.communicator.reset_override()
        Services.flow_ingestion.reset_override()
        Services.cross_section.reset_override()
        Services.change_tracker.reset_override()
        Services.freshness.reset_override()
        Services.section_alignment.reset_override()


@pytest.fixture()
def planspace(tmp_path: Path) -> Path:
    """Create a realistic planspace directory with initialized SQLite DB.

    Mirrors the directory structure that section_engine.py and
    coordination.py expect at runtime.
    """
    ps = tmp_path / "planspace"
    ps.mkdir()
    artifacts = ps / "artifacts"
    for subdir in (
        "sections",
        "proposals",
        "signals",
        "notes",
        "decisions",
        "todos",
        "coordination",
        "readiness",
        "risk",
    ):
        (artifacts / subdir).mkdir(parents=True)

    # Initialize the coordination database via db.sh
    subprocess.run(
        ["bash", str(DB_SH), "init", str(ps / "run.db")],
        check=True,
        capture_output=True,
        text=True,
    )
    return ps


@pytest.fixture()
def codespace(tmp_path: Path) -> Path:
    """Create a minimal codespace with mock source files."""
    cs = tmp_path / "codespace"
    cs.mkdir()
    (cs / "src").mkdir()
    (cs / "src" / "main.py").write_text("def main():\n    pass\n")
    (cs / "src" / "utils.py").write_text("def helper():\n    return 42\n")
    return cs


@pytest.fixture()
def section_01(planspace: Path) -> None:
    """Create a minimal section-01 spec in the planspace."""
    sec = planspace / "artifacts" / "sections" / "section-01.md"
    sec.write_text(
        "# Section 01: Authentication\n\n"
        "Handle user login and session management.\n"
    )


class NoOpCommunicator(Communicator):
    """Test double that silently discards all communication calls."""

    def mailbox_send(self, planspace, target, message):
        pass

    def log_artifact(self, planspace, artifact_name):
        pass

    def record_traceability(self, planspace, section_number, file_path, source, category=""):
        pass


class CapturingCommunicator(Communicator):
    """Test double that captures all communication calls for assertions."""

    def __init__(self) -> None:
        self.messages: list[str] = []
        self.mailbox_calls: list[tuple] = []
        self.artifact_events: list[str] = []
        self.traceability_calls: list[tuple] = []

    def mailbox_send(self, planspace, target, message):
        self.messages.append(message)
        self.mailbox_calls.append((planspace, target, message))

    def log_artifact(self, planspace, artifact_name):
        self.artifact_events.append(artifact_name)

    def record_traceability(self, planspace, section_number, file_path, source, category=""):
        self.traceability_calls.append((planspace, section_number, file_path, source, category))


@pytest.fixture()
def noop_communicator():
    """Override Services.communicator with a no-op test double."""
    comm = NoOpCommunicator()
    Services.communicator.override(providers.Object(comm))
    yield comm
    Services.communicator.reset_override()


@pytest.fixture()
def capturing_communicator():
    """Override Services.communicator with a capturing test double.

    Provides ``messages``, ``mailbox_calls``, ``artifact_events``,
    and ``traceability_calls`` lists for assertions.
    """
    comm = CapturingCommunicator()
    Services.communicator.override(providers.Object(comm))
    yield comm
    Services.communicator.reset_override()


class NoOpPipelineControl(PipelineControlService):
    """Test double that provides safe defaults for pipeline control."""

    def pause_for_parent(self, planspace, parent, message) -> str:
        return "resume"

    def poll_control_messages(self, planspace, parent, current_section=None) -> str | None:
        return None

    def handle_pending_messages(self, planspace, sections, affected) -> bool:
        return False

    def alignment_changed_pending(self, planspace) -> bool:
        return False

    def wait_if_paused(self, planspace, parent) -> None:
        pass

    def requeue_changed_sections(
        self, completed, queue, sections_by_num, planspace, codespace,
        *, current_section=None,
    ) -> list[str]:
        return []

    def section_inputs_hash(self, section_number, planspace, codespace, *args) -> str:
        return "noop-hash"

    def coordination_recheck_hash(self, sec_num, planspace, codespace, *args) -> str:
        return "noop-hash"


class CapturingPipelineControl(PipelineControlService):
    """Test double that captures pipeline control calls for assertions."""

    def __init__(self) -> None:
        self.pause_calls: list[tuple] = []
        self.poll_calls: list[tuple] = []
        self.pending_calls: list[tuple] = []
        self._pause_return: str = "resume"
        self._poll_return: str | None = None
        self._pending_return: bool = False
        self._alignment_changed_return: bool = False
        self._pause_side_effect = None
        self._poll_side_effect = None
        self._pending_side_effect = None
        self._alignment_changed_side_effect = None
        self._section_inputs_hash_return: str = "hash-stub"
        self._coordination_recheck_hash_return: str = "hash-stub"

    def pause_for_parent(self, planspace, parent, message) -> str:
        self.pause_calls.append((planspace, parent, message))
        if self._pause_side_effect:
            return self._pause_side_effect(planspace, parent, message)
        return self._pause_return

    def poll_control_messages(self, planspace, parent, current_section=None) -> str | None:
        self.poll_calls.append((planspace, parent, current_section))
        if self._poll_side_effect:
            return self._poll_side_effect(planspace, parent, current_section)
        return self._poll_return

    def handle_pending_messages(self, planspace, sections, affected) -> bool:
        self.pending_calls.append((planspace, sections, affected))
        if self._pending_side_effect:
            return self._pending_side_effect(planspace, sections, affected)
        return self._pending_return

    def alignment_changed_pending(self, planspace) -> bool:
        if self._alignment_changed_side_effect:
            return self._alignment_changed_side_effect(planspace)
        return self._alignment_changed_return

    def wait_if_paused(self, planspace, parent) -> None:
        pass

    def requeue_changed_sections(
        self, completed, queue, sections_by_num, planspace, codespace,
        *, current_section=None,
    ) -> list[str]:
        return []

    def section_inputs_hash(self, section_number, planspace, codespace, *args) -> str:
        return self._section_inputs_hash_return

    def coordination_recheck_hash(self, sec_num, planspace, codespace, *args) -> str:
        return self._coordination_recheck_hash_return


@pytest.fixture()
def noop_pipeline_control():
    """Override Services.pipeline_control with safe-default test double."""
    ctrl = NoOpPipelineControl()
    Services.pipeline_control.override(providers.Object(ctrl))
    yield ctrl
    Services.pipeline_control.reset_override()


@pytest.fixture()
def capturing_pipeline_control():
    """Override Services.pipeline_control with a capturing test double.

    Configure return values via ``_pause_return``, ``_poll_return``,
    ``_pending_return``, or set ``_pause_side_effect`` etc. for custom behavior.
    """
    ctrl = CapturingPipelineControl()
    Services.pipeline_control.override(providers.Object(ctrl))
    yield ctrl
    Services.pipeline_control.reset_override()


class NoOpChangeTracker(ChangeTrackerService):
    """Test double that silently handles all change tracker calls."""

    def set_flag(self, planspace) -> None:
        pass

    def make_alignment_checker(self):
        return lambda _planspace: False

    def invalidate_excerpts(self, planspace) -> None:
        pass


class NoOpFreshness(FreshnessService):
    """Test double that returns a constant freshness token."""

    def compute(self, planspace, section_number: str) -> str:
        return "noop-freshness"


class NoOpSectionAlignment(SectionAlignmentService):
    """Test double for section alignment operations."""

    def extract_problems(self, result, output_path=None, planspace=None,
                         parent=None, codespace=None, *, adjudicator_model: str) -> str | None:
        return None

    def collect_modified_files(self, planspace, section, codespace) -> list[str]:
        return []

    def run_alignment_check(self, section, planspace, codespace, parent, sec_num,
                            output_prefix="align", max_retries=2, *, model: str,
                            adjudicator_model: str):
        return None

    def parse_alignment_verdict(self, result):
        return None

    def run_global_recheck(self, sections_by_num, section_results,
                           planspace, codespace, parent, policy) -> str:
        return "aligned"


@pytest.fixture()
def noop_change_tracker():
    """Override Services.change_tracker with a no-op test double."""
    ct = NoOpChangeTracker()
    Services.change_tracker.override(providers.Object(ct))
    yield ct
    Services.change_tracker.reset_override()


@pytest.fixture()
def noop_freshness():
    """Override Services.freshness with a no-op test double."""
    fs = NoOpFreshness()
    Services.freshness.override(providers.Object(fs))
    yield fs
    Services.freshness.reset_override()


@pytest.fixture()
def noop_section_alignment():
    """Override Services.section_alignment with a no-op test double."""
    sa = NoOpSectionAlignment()
    Services.section_alignment.override(providers.Object(sa))
    yield sa
    Services.section_alignment.reset_override()


@pytest.fixture()
def mock_dispatch() -> MagicMock:
    """Override the Services.dispatcher container provider.

    Returns the inner MagicMock so tests can configure return values::

        mock_dispatch.return_value = '{"aligned": true}'
    """
    mock_disp = MockDispatcher()
    Services.dispatcher.override(providers.Object(mock_disp))
    yield mock_disp.mock
    Services.dispatcher.reset_override()
