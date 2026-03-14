"""ROAL risk data types."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


MAX_RESIDUAL_RISK = 100
HISTORY_ADJUSTMENT_BOUND = 10.0


class StepClass(str, Enum):
    EXPLORE = "explore"
    STABILIZE = "stabilize"
    EDIT = "edit"
    COORDINATE = "coordinate"
    VERIFY = "verify"


class DecisionClass(str, Enum):
    LOCAL = "local"
    COMPONENT = "component"
    CROSS_CUTTING = "cross_cutting"
    PLATFORM = "platform"
    IRREVERSIBLE = "irreversible"


AssessmentClass = StepClass | DecisionClass


class PostureProfile(str, Enum):
    P0_DIRECT = "P0"
    P1_LIGHT = "P1"
    P2_STANDARD = "P2"
    P3_GUARDED = "P3"
    P4_REOPEN = "P4"

    @property
    def rank(self) -> int:
        return _POSTURE_RANKS[self]


_POSTURE_RANKS = {
    PostureProfile.P0_DIRECT: 0,
    PostureProfile.P1_LIGHT: 1,
    PostureProfile.P2_STANDARD: 2,
    PostureProfile.P3_GUARDED: 3,
    PostureProfile.P4_REOPEN: 4,
}


class RiskType(str, Enum):
    CONTEXT_ROT = "context_rot"
    SILENT_DRIFT = "silent_drift"
    SCOPE_CREEP = "scope_creep"
    BRUTE_FORCE_REGRESSION = "brute_force_regression"
    CROSS_SECTION_INCOHERENCE = "cross_section_incoherence"
    TOOL_ISLAND_ISOLATION = "tool_island_isolation"
    STALE_ARTIFACT_CONTAMINATION = "stale_artifact_contamination"
    ECOSYSTEM_MATURITY = "ecosystem_maturity"
    DEPENDENCY_LOCK_IN = "dependency_lock_in"
    TEAM_CAPABILITY = "team_capability"
    SCALE_FIT = "scale_fit"
    INTEGRATION_FIT = "integration_fit"
    OPERABILITY_COST = "operability_cost"
    EVOLUTION_FLEXIBILITY = "evolution_flexibility"


class StepDecision(str, Enum):
    ACCEPT = "accept"
    REJECT_DEFER = "reject_defer"
    REJECT_REOPEN = "reject_reopen"


class RiskMode(str, Enum):
    LIGHT = "light"
    FULL = "full"


class RiskConfidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class RiskVector:
    context_rot: int = 0
    silent_drift: int = 0
    scope_creep: int = 0
    brute_force_regression: int = 0
    cross_section_incoherence: int = 0
    tool_island_isolation: int = 0
    stale_artifact_contamination: int = 0
    ecosystem_maturity: int = 0
    dependency_lock_in: int = 0
    team_capability: int = 0
    scale_fit: int = 0
    integration_fit: int = 0
    operability_cost: int = 0
    evolution_flexibility: int = 0


@dataclass
class RiskModifiers:
    blast_radius: int = 0
    reversibility: int = 4
    observability: int = 4
    confidence: float = 0.5


@dataclass
class UnderstandingInventory:
    confirmed: list[str] = field(default_factory=list)
    assumed: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)


@dataclass
class PackageStep:
    step_id: str
    assessment_class: AssessmentClass
    summary: str
    prerequisites: list[str] = field(default_factory=list)
    expected_outputs: list[str] = field(default_factory=list)
    expected_resolutions: list[str] = field(default_factory=list)
    mutation_surface: list[str] = field(default_factory=list)
    verification_surface: list[str] = field(default_factory=list)
    reversibility: str = "medium"


@dataclass
class StepAssessment:
    step_id: str
    assessment_class: AssessmentClass
    summary: str
    prerequisites: list[str]
    risk_vector: RiskVector
    modifiers: RiskModifiers
    raw_risk: int
    dominant_risks: list[RiskType]


@dataclass
class RiskAssessment:
    assessment_id: str
    layer: str
    package_id: str
    assessment_scope: str
    understanding_inventory: UnderstandingInventory
    package_raw_risk: int
    assessment_confidence: float
    dominant_risks: list[RiskType]
    step_assessments: list[StepAssessment]
    frontier_candidates: list[str]
    reopen_recommendations: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class StepMitigation:
    step_id: str
    decision: StepDecision
    posture: PostureProfile | None = None
    mitigations: list[str] = field(default_factory=list)
    residual_risk: int | None = None
    reason: str | None = None
    wait_for: list[str] = field(default_factory=list)
    route_to: str | None = None
    dispatch_shape: dict | None = None


@dataclass
class RiskPlan:
    plan_id: str
    assessment_id: str
    package_id: str
    layer: str
    step_decisions: list[StepMitigation]
    accepted_frontier: list[str]
    deferred_steps: list[str]
    reopen_steps: list[str]
    expected_reassessment_inputs: list[str] = field(default_factory=list)


@dataclass
class RiskPackage:
    package_id: str
    layer: str
    scope: str
    origin_problem_id: str
    origin_source: str
    steps: list[PackageStep]


@dataclass
class RiskHistoryEntry:
    package_id: str
    step_id: str
    layer: str
    assessment_class: AssessmentClass
    posture: PostureProfile
    predicted_risk: int
    actual_outcome: str
    surfaced_surprises: list[str] = field(default_factory=list)
    verification_outcome: str | None = None
    dominant_risks: list[RiskType] = field(default_factory=list)
    blast_radius_band: int = 0


@dataclass
class IntentRiskHint:
    risk_mode: RiskMode
    risk_confidence: RiskConfidence
    risk_budget_hint: int = 0
    posture_floor: PostureProfile | None = None


@dataclass(frozen=True)
class EngagementContext:
    """Bundled signals for risk engagement mode selection.

    Replaces the 8-boolean parameter list of ``determine_engagement``
    with a single typed container.
    """

    has_shared_seams: bool = False
    has_consequence_notes: bool = False
    has_stale_inputs: bool = False
    has_recent_failures: bool = False
    has_tool_changes: bool = False
    freshness_changed: bool = False
    has_decision_classes: bool = False
    has_unresolved_value_scales: bool = False

    @property
    def skip_floor_hit(self) -> bool:
        """True when safety-floor signals override a light/skip hint."""
        return self.has_shared_seams or self.has_stale_inputs or self.has_recent_failures


def clamp_int(value: int, lower: int, upper: int) -> int:
    return max(lower, min(upper, value))


def clamp_float(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))
