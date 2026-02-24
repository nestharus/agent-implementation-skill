"""Integration tests for dispatch.py signal reading and model policy.

Tests signal file parsing, model choice writing, and policy merging.
No LLM mocks needed — these are pure file I/O and JSON parsing.
"""

import json
from pathlib import Path

from section_loop.dispatch import (
    check_agent_signals,
    read_agent_signal,
    read_model_policy,
    read_signal_tuple,
    summarize_output,
    write_model_choice_signal,
)


class TestReadSignalTuple:
    def test_missing_file(self, tmp_path: Path) -> None:
        sig, detail = read_signal_tuple(tmp_path / "no-such-signal.json")
        assert sig is None
        assert detail == ""

    def test_underspecified_signal(self, tmp_path: Path) -> None:
        p = tmp_path / "signal.json"
        p.write_text(json.dumps({
            "state": "underspecified",
            "detail": "missing requirements for auth",
        }))
        sig, detail = read_signal_tuple(p)
        assert sig == "underspec"
        assert "missing requirements" in detail

    def test_need_decision_signal(self, tmp_path: Path) -> None:
        p = tmp_path / "signal.json"
        p.write_text(json.dumps({
            "state": "need_decision",
            "detail": "OAuth vs JWT?",
        }))
        sig, detail = read_signal_tuple(p)
        assert sig == "need_decision"
        assert "OAuth" in detail

    def test_dependency_signal(self, tmp_path: Path) -> None:
        p = tmp_path / "signal.json"
        p.write_text(json.dumps({
            "state": "dependency",
            "detail": "needs section 03 first",
        }))
        sig, detail = read_signal_tuple(p)
        assert sig == "dependency"

    def test_out_of_scope_signal(self, tmp_path: Path) -> None:
        p = tmp_path / "signal.json"
        p.write_text(json.dumps({
            "state": "out_of_scope",
            "detail": "this belongs to infrastructure",
        }))
        sig, detail = read_signal_tuple(p)
        assert sig == "out_of_scope"

    def test_needs_parent_signal(self, tmp_path: Path) -> None:
        p = tmp_path / "signal.json"
        p.write_text(json.dumps({
            "state": "needs_parent",
            "detail": "architecture decision needed",
        }))
        sig, detail = read_signal_tuple(p)
        assert sig == "needs_parent"

    def test_loop_detected_signal(self, tmp_path: Path) -> None:
        p = tmp_path / "signal.json"
        p.write_text(json.dumps({
            "state": "loop_detected",
            "detail": "repeating same edit",
        }))
        sig, detail = read_signal_tuple(p)
        assert sig == "loop_detected"

    def test_enriched_detail_with_extras(self, tmp_path: Path) -> None:
        p = tmp_path / "signal.json"
        p.write_text(json.dumps({
            "state": "underspecified",
            "detail": "unclear",
            "needs": "API schema definition",
            "assumptions_refused": "assumed REST",
            "suggested_escalation_target": "architect",
        }))
        sig, detail = read_signal_tuple(p)
        assert sig == "underspec"
        assert "Needs: API schema" in detail
        assert "Refused assumptions: assumed REST" in detail
        assert "Escalation target: architect" in detail

    def test_unknown_state_fails_closed(self, tmp_path: Path) -> None:
        """R31/V2: Unknown signal state fails closed as needs_parent."""
        p = tmp_path / "signal.json"
        p.write_text(json.dumps({
            "state": "something_unexpected",
            "detail": "whatever",
        }))
        sig, detail = read_signal_tuple(p)
        assert sig == "needs_parent"
        assert "Unknown signal state" in detail
        assert "something_unexpected" in detail

    def test_malformed_json_fails_closed(self, tmp_path: Path) -> None:
        """R31/V2: Malformed signal JSON fails closed as needs_parent."""
        p = tmp_path / "signal.json"
        p.write_text("not json at all {{{")
        sig, detail = read_signal_tuple(p)
        assert sig == "needs_parent"
        assert "Malformed signal JSON" in detail


class TestReadAgentSignal:
    def test_missing_file(self, tmp_path: Path) -> None:
        assert read_agent_signal(tmp_path / "nope.json") is None

    def test_valid_json_dict(self, tmp_path: Path) -> None:
        p = tmp_path / "signal.json"
        p.write_text(json.dumps({"action": "rebuild", "reason": "stale"}))
        result = read_agent_signal(p)
        assert result == {"action": "rebuild", "reason": "stale"}

    def test_expected_fields_present(self, tmp_path: Path) -> None:
        p = tmp_path / "signal.json"
        p.write_text(json.dumps({"action": "rebuild", "reason": "stale"}))
        result = read_agent_signal(p, expected_fields=["action", "reason"])
        assert result is not None

    def test_expected_fields_missing(self, tmp_path: Path) -> None:
        p = tmp_path / "signal.json"
        p.write_text(json.dumps({"action": "rebuild"}))
        result = read_agent_signal(p, expected_fields=["action", "reason"])
        assert result is None

    def test_malformed_json(self, tmp_path: Path) -> None:
        p = tmp_path / "signal.json"
        p.write_text("not valid json")
        assert read_agent_signal(p) is None

    def test_non_dict_json(self, tmp_path: Path) -> None:
        p = tmp_path / "signal.json"
        p.write_text(json.dumps([1, 2, 3]))
        assert read_agent_signal(p) is None


class TestWriteModelChoiceSignal:
    def test_writes_correct_json(self, planspace: Path) -> None:
        write_model_choice_signal(
            planspace, "03", "integration-proposal",
            "gpt-5.3-codex-high", "first attempt, default model",
        )
        sig_path = (planspace / "artifacts" / "signals"
                    / "model-choice-03-integration-proposal.json")
        assert sig_path.exists()
        data = json.loads(sig_path.read_text())
        assert data["section"] == "03"
        assert data["step"] == "integration-proposal"
        assert data["model"] == "gpt-5.3-codex-high"
        assert data["escalated_from"] is None

    def test_writes_escalation(self, planspace: Path) -> None:
        write_model_choice_signal(
            planspace, "01", "alignment",
            "gpt-5.3-codex-xhigh", "escalated after 2 failures",
            escalated_from="gpt-5.3-codex-high",
        )
        sig_path = (planspace / "artifacts" / "signals"
                    / "model-choice-01-alignment.json")
        data = json.loads(sig_path.read_text())
        assert data["escalated_from"] == "gpt-5.3-codex-high"


class TestCheckAgentSignals:
    def test_structured_signal_takes_priority(self, tmp_path: Path) -> None:
        sig_path = tmp_path / "signal.json"
        sig_path.write_text(json.dumps({
            "state": "dependency",
            "detail": "needs section 02",
        }))
        sig, detail = check_agent_signals(
            "some agent output text",
            signal_path=sig_path,
        )
        assert sig == "dependency"

    def test_no_signal_no_adjudicator_returns_none(
        self, tmp_path: Path,
    ) -> None:
        sig, detail = check_agent_signals(
            "some output",
            signal_path=tmp_path / "missing.json",
        )
        assert sig is None
        assert detail == ""

    def test_out_of_scope_routes_through(self, tmp_path: Path) -> None:
        """P6 regression: OUT_OF_SCOPE signal file → check_agent_signals
        returns out_of_scope with detail preserved."""
        sig_path = tmp_path / "signal.json"
        sig_path.write_text(json.dumps({
            "state": "out_of_scope",
            "detail": "belongs to infrastructure team",
        }))
        sig, detail = check_agent_signals(
            "some output", signal_path=sig_path,
        )
        assert sig == "out_of_scope"
        assert "infrastructure" in detail

    def test_needs_parent_routes_through(self, tmp_path: Path) -> None:
        """P6 regression: NEEDS_PARENT signal file → check_agent_signals
        returns needs_parent with detail preserved."""
        sig_path = tmp_path / "signal.json"
        sig_path.write_text(json.dumps({
            "state": "needs_parent",
            "detail": "architecture decision required at project level",
        }))
        sig, detail = check_agent_signals(
            "some output", signal_path=sig_path,
        )
        assert sig == "needs_parent"
        assert "architecture" in detail


class TestSummarizeOutput:
    def test_extracts_summary_line(self) -> None:
        output = "# Heading\nSummary: Auth module implemented\nDetails..."
        assert summarize_output(output) == "Auth module implemented"

    def test_falls_back_to_first_content_line(self) -> None:
        output = "# Heading\n---\nActual content here"
        assert summarize_output(output) == "Actual content here"

    def test_empty_output(self) -> None:
        assert summarize_output("") == "(no output)"

    def test_truncation(self) -> None:
        output = "x" * 300
        assert len(summarize_output(output, max_len=50)) == 50


class TestReadModelPolicy:
    def test_defaults_when_no_file(self, planspace: Path) -> None:
        policy = read_model_policy(planspace)
        assert policy["setup"] == "claude-opus"
        assert policy["proposal"] == "gpt-5.3-codex-high"
        assert policy["alignment"] == "claude-opus"
        assert policy["exploration"] == "glm"
        assert policy["escalation_model"] == "gpt-5.3-codex-xhigh"
        assert policy["escalation_triggers"]["stall_count"] == 2

    def test_custom_policy_overrides(self, planspace: Path) -> None:
        policy_path = planspace / "artifacts" / "model-policy.json"
        policy_path.write_text(json.dumps({
            "proposal": "gpt-5.3-codex-xhigh",
            "alignment": "gpt-5.3-codex-high",
        }))
        policy = read_model_policy(planspace)
        assert policy["proposal"] == "gpt-5.3-codex-xhigh"
        assert policy["alignment"] == "gpt-5.3-codex-high"
        # Defaults preserved for unset keys
        assert policy["setup"] == "claude-opus"

    def test_escalation_triggers_merge(self, planspace: Path) -> None:
        policy_path = planspace / "artifacts" / "model-policy.json"
        policy_path.write_text(json.dumps({
            "escalation_triggers": {"stall_count": 5},
        }))
        policy = read_model_policy(planspace)
        assert policy["escalation_triggers"]["stall_count"] == 5
        # Default preserved
        assert policy["escalation_triggers"]["max_attempts_before_escalation"] == 3

    def test_malformed_json_uses_defaults(self, planspace: Path) -> None:
        policy_path = planspace / "artifacts" / "model-policy.json"
        policy_path.write_text("not json {{{")
        policy = read_model_policy(planspace)
        assert policy["setup"] == "claude-opus"
