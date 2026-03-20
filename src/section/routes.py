"""Task routes for the per-section state machine pipeline.

Each section independently progresses through states, each mapped to
a single agent dispatch.  The state machine handles transitions and
retries -- handlers are single-shot.

Routes map ``section.<state>`` task types to their agent + model pairs.
States that do not dispatch an agent (e.g. ``readiness``) are omitted.

All 15 routes below are actively submitted by the state machine
orchestrator (``_STATE_TASK_MAP`` in ``state_machine_orchestrator.py``):

    Route                 Submitted from states
    -----                 ---------------------
    section.excerpt       PENDING, EXCERPT_EXTRACTION
    section.problem_frame PROBLEM_FRAME
    section.intent_triage INTENT_TRIAGE
    section.philosophy    PHILOSOPHY_BOOTSTRAP
    section.intent_pack   INTENT_PACK
    section.propose       PROPOSING
    section.assess        ASSESSING
    section.risk_eval     RISK_EVAL
    section.microstrategy MICROSTRATEGY
    section.implement     IMPLEMENTING
    section.impl_assess   IMPL_ASSESSING
    section.verify        VERIFYING
    section.post_complete POST_COMPLETION
    section.decompose_children DECOMPOSING
    section.reassemble    REASSEMBLING

Note: ``section.readiness_check`` is submitted only by the legacy
pipeline orchestrator (``pipeline_orchestrator.py``), not by the
state machine.  READINESS is script-only in the state machine model,
so no route is registered for it here.
"""

from taskrouter import TaskRouter

router = TaskRouter("section")

# --- excerpt extraction: setup-excerpter ---
router.route(
    "excerpt",
    agent="setup-excerpter.md",
    model="claude-opus",
    policy_key="setup",
)

# --- problem frame: setup-excerpter (retry for frame) ---
router.route(
    "problem_frame",
    agent="setup-excerpter.md",
    model="claude-opus",
    policy_key="setup",
)

# --- intent triage: intent-triager ---
router.route(
    "intent_triage",
    agent="intent-triager.md",
    model="glm",
    policy_key="intent_triage",
)

# --- philosophy bootstrap: philosophy chain (self-contained) ---
router.route(
    "philosophy",
    agent="philosophy-bootstrap-prompter.md",
    model="glm",
    policy_key="intent_philosophy_bootstrap_prompter",
)

# --- intent pack: intent-pack-generator ---
router.route(
    "intent_pack",
    agent="intent-pack-generator.md",
    model="gpt-high",
    policy_key="intent_pack",
)

# --- proposal: integration-proposer (single shot) ---
router.route(
    "propose",
    agent="integration-proposer.md",
    model="gpt-high",
    policy_key="proposal",
)

# --- proposal assessment: alignment-judge (single shot) ---
router.route(
    "assess",
    agent="alignment-judge.md",
    model="claude-opus",
    policy_key="alignment",
)

# --- readiness: script logic, no agent dispatch ---
# (omitted — no route needed)

# --- risk evaluation: ROAL (risk-assessor + execution-optimizer) ---
router.route(
    "risk_eval",
    agent="risk-assessor.md",
    model="gpt-high",
    policy_key="risk_assessor",
)

# --- microstrategy: microstrategy-decider ---
router.route(
    "microstrategy",
    agent="microstrategy-decider.md",
    model="glm",
    policy_key="microstrategy_decider",
)

# --- implementation: implementation-strategist (single shot) ---
router.route(
    "implement",
    agent="implementation-strategist.md",
    model="gpt-high",
    policy_key="implementation",
)

# --- implementation assessment: alignment-judge (single shot) ---
router.route(
    "impl_assess",
    agent="alignment-judge.md",
    model="claude-opus",
    policy_key="alignment",
)

# --- verification: structural-verifier (already task-driven) ---
router.route(
    "verify",
    agent="structural-verifier.md",
    model="gpt-high",
    policy_key="verification_structural",
)

# --- post-completion: impact-analyzer ---
router.route(
    "post_complete",
    agent="impact-analyzer.md",
    model="glm",
)

# --- fractal descent: section-decomposer ---
router.route(
    "decompose_children",
    agent="section-decomposer.md",
    model="claude-opus",
    policy_key="decompose",
)

# --- fractal reassembly: impact-analyzer placeholder ---
router.route(
    "reassemble",
    agent="impact-analyzer.md",
    model="glm",
)
