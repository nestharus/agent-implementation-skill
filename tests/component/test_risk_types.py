"""Component tests for ROAL risk types and serialization."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.risk.repository.serialization import (
    deserialize_assessment,
    deserialize_history_entry,
    deserialize_package,
    deserialize_plan,
    load_risk_assessment,
    load_risk_package,
    load_risk_plan,
    read_risk_artifact,
    serialize_assessment,
    serialize_history_entry,
    serialize_package,
    serialize_plan,
    write_risk_artifact,
)
from risk.types import (
    DecisionClass,
    IntentRiskHint,
    PackageStep,
    PostureProfile,
    RiskAssessment,
    RiskConfidence,
    RiskHistoryEntry,
    RiskMode,
    RiskModifiers,
    RiskPackage,
    RiskPlan,
    RiskType,
    RiskVector,
    StepAssessment,
    StepClass,
    StepDecision,
    StepMitigation,
    UnderstandingInventory,
)


def _sample_package() -> RiskPackage:
    return RiskPackage(
        package_id="pkg-1",
        layer="section",
        scope="section-01",
        origin_problem_id="prob-1",
        origin_source="triage",
        steps=[
            PackageStep(
                step_id="step-1",
                assessment_class=StepClass.EXPLORE,
                summary="Inspect current behavior",
                prerequisites=["ready"],
                expected_outputs=["notes"],
                expected_resolutions=["open questions"],
                mutation_surface=["src/a.py"],
                verification_surface=["tests/test_a.py"],
                reversibility="high",
            )
        ],
    )


def _sample_assessment() -> RiskAssessment:
    return RiskAssessment(
        assessment_id="assess-1",
        layer="section",
        package_id="pkg-1",
        assessment_scope="section-01",
        understanding_inventory=UnderstandingInventory(
            confirmed=["a"],
            assumed=["b"],
            missing=["c"],
            stale=["d"],
        ),
        package_raw_risk=64,
        assessment_confidence=0.75,
        dominant_risks=[RiskType.CONTEXT_ROT, RiskType.SCOPE_CREEP],
        step_assessments=[
            StepAssessment(
                step_id="step-1",
                assessment_class=StepClass.STABILIZE,
                summary="Stabilize inputs",
                prerequisites=["step-0"],
                risk_vector=RiskVector(
                    context_rot=1,
                    scope_creep=2,
                    stale_artifact_contamination=3,
                ),
                modifiers=RiskModifiers(
                    blast_radius=2,
                    reversibility=3,
                    observability=1,
                    confidence=0.8,
                ),
                raw_risk=55,
                dominant_risks=[
                    RiskType.STALE_ARTIFACT_CONTAMINATION,
                    RiskType.SCOPE_CREEP,
                ],
            )
        ],
        frontier_candidates=["step-1"],
        reopen_recommendations=["reassess after fix"],
        notes=["first pass"],
    )


def _sample_plan() -> RiskPlan:
    return RiskPlan(
        plan_id="plan-1",
        assessment_id="assess-1",
        package_id="pkg-1",
        layer="section",
        step_decisions=[
            StepMitigation(
                step_id="step-1",
                decision=StepDecision.REJECT_REOPEN,
                posture=PostureProfile.P4_REOPEN,
                mitigations=["collect more evidence"],
                residual_risk=82,
                reason="cross-section mismatch",
                wait_for=["review"],
                route_to="coordination",
                dispatch_shape={"mode": "full"},
            )
        ],
        accepted_frontier=[],
        deferred_steps=["step-2"],
        reopen_steps=["step-1"],
        expected_reassessment_inputs=["updated trace"],
    )


class TestEnums:
    @pytest.mark.parametrize(
        ("enum_cls", "expected"),
        [
            (
                StepClass,
                {
                    "EXPLORE": "explore",
                    "STABILIZE": "stabilize",
                    "EDIT": "edit",
                    "COORDINATE": "coordinate",
                    "VERIFY": "verify",
                },
            ),
            (
                PostureProfile,
                {
                    "P0_DIRECT": "P0",
                    "P1_LIGHT": "P1",
                    "P2_STANDARD": "P2",
                    "P3_GUARDED": "P3",
                    "P4_REOPEN": "P4",
                },
            ),
            (
                RiskType,
                {
                    "CONTEXT_ROT": "context_rot",
                    "SILENT_DRIFT": "silent_drift",
                    "SCOPE_CREEP": "scope_creep",
                    "BRUTE_FORCE_REGRESSION": "brute_force_regression",
                    "CROSS_SECTION_INCOHERENCE": "cross_section_incoherence",
                    "TOOL_ISLAND_ISOLATION": "tool_island_isolation",
                    "STALE_ARTIFACT_CONTAMINATION": "stale_artifact_contamination",
                    "ECOSYSTEM_MATURITY": "ecosystem_maturity",
                    "DEPENDENCY_LOCK_IN": "dependency_lock_in",
                    "TEAM_CAPABILITY": "team_capability",
                    "SCALE_FIT": "scale_fit",
                    "INTEGRATION_FIT": "integration_fit",
                    "OPERABILITY_COST": "operability_cost",
                    "EVOLUTION_FLEXIBILITY": "evolution_flexibility",
                },
            ),
            (
                DecisionClass,
                {
                    "LOCAL": "local",
                    "COMPONENT": "component",
                    "CROSS_CUTTING": "cross_cutting",
                    "PLATFORM": "platform",
                    "IRREVERSIBLE": "irreversible",
                },
            ),
            (
                StepDecision,
                {
                    "ACCEPT": "accept",
                    "REJECT_DEFER": "reject_defer",
                    "REJECT_REOPEN": "reject_reopen",
                },
            ),
            (
                RiskMode,
                {
                    "LIGHT": "light",
                    "FULL": "full",
                },
            ),
            (
                RiskConfidence,
                {
                    "HIGH": "high",
                    "MEDIUM": "medium",
                    "LOW": "low",
                },
            ),
        ],
    )
    def test_expected_enum_values(
        self,
        enum_cls: type[object],
        expected: dict[str, str],
    ) -> None:
        assert {member.name: member.value for member in enum_cls} == expected


class TestDataclassDefaults:
    def test_default_instantiation(self) -> None:
        vector = RiskVector()
        modifiers = RiskModifiers()
        inventory = UnderstandingInventory()
        mitigation = StepMitigation(
            step_id="step-1",
            decision=StepDecision.ACCEPT,
        )
        history = RiskHistoryEntry(
            package_id="pkg-1",
            step_id="step-1",
            layer="section",
            assessment_class=StepClass.EDIT,
            posture=PostureProfile.P2_STANDARD,
            predicted_risk=42,
            actual_outcome="success",
        )
        hint = IntentRiskHint(
            risk_mode=RiskMode.LIGHT,
            risk_confidence=RiskConfidence.MEDIUM,
        )

        assert vector == RiskVector()
        assert modifiers == RiskModifiers()
        assert inventory == UnderstandingInventory()
        assert mitigation.mitigations == []
        assert mitigation.posture is None
        assert history.surfaced_surprises == []
        assert history.dominant_risks == []
        assert hint.risk_budget_hint == 0
        assert hint.posture_floor is None


class TestSerialization:
    def test_package_round_trip(self) -> None:
        package = _sample_package()

        serialized = serialize_package(package)
        restored = deserialize_package(serialized)

        assert serialized["steps"][0]["assessment_class"] == "explore"
        assert isinstance(restored.steps[0], PackageStep)
        assert restored == package

    def test_assessment_round_trip(self) -> None:
        assessment = _sample_assessment()

        serialized = serialize_assessment(assessment)
        restored = deserialize_assessment(serialized)

        assert serialized["dominant_risks"] == ["context_rot", "scope_creep"]
        assert isinstance(restored.understanding_inventory, UnderstandingInventory)
        assert isinstance(restored.step_assessments[0], StepAssessment)
        assert isinstance(restored.step_assessments[0].risk_vector, RiskVector)
        assert isinstance(restored.step_assessments[0].modifiers, RiskModifiers)
        assert restored == assessment

    def test_plan_round_trip(self) -> None:
        plan = _sample_plan()

        serialized = serialize_plan(plan)
        restored = deserialize_plan(serialized)

        assert serialized["step_decisions"][0]["decision"] == "reject_reopen"
        assert isinstance(restored.step_decisions[0], StepMitigation)
        assert restored == plan

    def test_history_entry_round_trip(self) -> None:
        entry = RiskHistoryEntry(
            package_id="pkg-1",
            step_id="step-1",
            layer="section",
            assessment_class=StepClass.VERIFY,
            posture=PostureProfile.P3_GUARDED,
            predicted_risk=40,
            actual_outcome="partial",
            surfaced_surprises=["fixture drift"],
            verification_outcome="warn",
            dominant_risks=[RiskType.SILENT_DRIFT],
            blast_radius_band=2,
        )

        serialized = serialize_history_entry(entry)

        assert serialized["posture"] == "P3"
        assert deserialize_history_entry(serialized) == entry

    def test_read_write_risk_artifact(self, tmp_path: Path) -> None:
        artifact_path = tmp_path / "risk" / "assessment.json"
        payload = {"assessment_id": "assess-1", "score": 10}

        write_risk_artifact(artifact_path, payload)

        assert read_risk_artifact(artifact_path) == payload

    def test_read_risk_artifact_returns_none_for_non_dict_json(
        self,
        tmp_path: Path,
    ) -> None:
        artifact_path = tmp_path / "risk" / "assessment.json"
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text("[1, 2, 3]\n", encoding="utf-8")

        assert read_risk_artifact(artifact_path) is None

    @pytest.mark.parametrize(
        ("loader", "serializer", "artifact", "filename"),
        [
            (
                load_risk_package,
                serialize_package,
                _sample_package(),
                "package.json",
            ),
            (
                load_risk_assessment,
                serialize_assessment,
                _sample_assessment(),
                "assessment.json",
            ),
            (
                load_risk_plan,
                serialize_plan,
                _sample_plan(),
                "plan.json",
            ),
        ],
    )
    def test_typed_loaders_deserialize_valid_artifacts(
        self,
        tmp_path: Path,
        loader,
        serializer,
        artifact,
        filename: str,
    ) -> None:
        artifact_path = tmp_path / "risk" / filename
        write_risk_artifact(artifact_path, serializer(artifact))

        assert loader(artifact_path) == artifact

    @pytest.mark.parametrize(
        ("loader", "payload", "filename", "message"),
        [
            (
                load_risk_package,
                {"layer": "section"},
                "package.json",
                "Malformed risk package",
            ),
            (
                load_risk_assessment,
                {"assessment_id": "assess-1"},
                "assessment.json",
                "Malformed risk assessment",
            ),
            (
                load_risk_plan,
                {"plan_id": "plan-1"},
                "plan.json",
                "Malformed risk plan",
            ),
        ],
    )
    def test_typed_loaders_preserve_schema_invalid_artifacts(
        self,
        tmp_path: Path,
        caplog,
        loader,
        payload: dict[str, object],
        filename: str,
        message: str,
    ) -> None:
        artifact_path = tmp_path / "risk" / filename
        write_risk_artifact(artifact_path, payload)

        with caplog.at_level("WARNING"):
            assert loader(artifact_path) is None

        assert not artifact_path.exists()
        assert artifact_path.with_suffix(".malformed.json").exists()
        assert message in caplog.text
        assert str(artifact_path) in caplog.text

    @pytest.mark.parametrize(
        ("loader", "filename"),
        [
            (load_risk_package, "package.json"),
            (load_risk_assessment, "assessment.json"),
            (load_risk_plan, "plan.json"),
        ],
    )
    def test_typed_loaders_return_none_for_missing_files(
        self,
        tmp_path: Path,
        loader,
        filename: str,
    ) -> None:
        assert loader(tmp_path / "risk" / filename) is None
