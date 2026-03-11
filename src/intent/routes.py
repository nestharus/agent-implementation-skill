"""Task routes for the intent system."""

from taskrouter import TaskRouter

router = TaskRouter("intent")

router.route(
    "triage",
    agent="intent-triager.md",
    model="glm",
    policy_key="intent_triage",
)
router.route(
    "triage_escalation",
    agent="intent-triager.md",
    model="claude-opus",
    policy_key="intent_triage_escalation",
)
router.route(
    "problem_expander",
    agent="problem-expander.md",
    model="claude-opus",
    policy_key="intent_problem_expander",
)
router.route(
    "philosophy_expander",
    agent="philosophy-expander.md",
    model="claude-opus",
    policy_key="intent_philosophy_expander",
)
router.route(
    "recurrence_adjudicator",
    agent="recurrence-adjudicator.md",
    model="glm",
    policy_key="intent_recurrence_adjudicator",
)
router.route(
    "pack_generator",
    agent="intent-pack-generator.md",
    model="gpt-high",
    policy_key="intent_pack",
)
router.route(
    "philosophy_bootstrap",
    agent="philosophy-bootstrap-prompter.md",
    model="glm",
    policy_key="intent_philosophy_bootstrap_prompter",
)
router.route(
    "philosophy_selector",
    agent="philosophy-source-selector.md",
    model="gpt-high",
    policy_key="intent_philosophy_selector",
)
router.route(
    "philosophy_verifier",
    agent="philosophy-source-verifier.md",
    model="claude-opus",
    policy_key="intent_philosophy_verifier",
)
router.route(
    "philosophy_distiller",
    agent="philosophy-distiller.md",
    model="claude-opus",
    policy_key="intent_philosophy",
)
