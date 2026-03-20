"""Task routes for the global (cross-section) pipeline.

Global tasks handle project-level concerns that precede or span
per-section execution: entry classification, problem/value extraction,
decomposition, proposal alignment, and project-wide scanning.

Routes map ``bootstrap.<task>`` task types to their agent + model pairs.

    Route                       Agent
    -----                       -----
    bootstrap.classify_entry       entry-classifier.md
    bootstrap.extract_problems     problem-extractor.md
    bootstrap.explore_problems     problem-explorer.md
    bootstrap.extract_values       value-extractor.md
    bootstrap.explore_values       value-explorer.md
    bootstrap.confirm_understanding user-researcher.md
    bootstrap.interpret_response   user-researcher.md
    bootstrap.assess_reliability   reliability-assessor.md
    bootstrap.decompose            decomposer.md
    bootstrap.align_proposal       proposal-aligner.md
    bootstrap.expand_proposal      proposal-expander.md
    bootstrap.explore_factors      factor-explorer.md
    bootstrap.build_codemap        codemap-builder.md
    bootstrap.explore_sections     section-explorer.md
    bootstrap.discover_substrate   substrate-discoverer.md
"""

from taskrouter import TaskRouter

router = TaskRouter("bootstrap")

# --- intake: classify the user entry ---
router.route(
    "classify_entry",
    agent="entry-classifier.md",
    model="claude-opus",
)

# --- problem extraction: extract problems from user input ---
router.route(
    "extract_problems",
    agent="problem-extractor.md",
    model="claude-opus",
)

# --- problem exploration: deepen understanding of extracted problems ---
router.route(
    "explore_problems",
    agent="problem-explorer.md",
    model="claude-opus",
)

# --- value extraction: extract values/constraints from user input ---
router.route(
    "extract_values",
    agent="value-extractor.md",
    model="claude-opus",
)

# --- value exploration: deepen understanding of extracted values ---
router.route(
    "explore_values",
    agent="value-explorer.md",
    model="claude-opus",
)

# --- confirm understanding: verify interpretation with the user ---
router.route(
    "confirm_understanding",
    agent="user-researcher.md",
    model="claude-opus",
)

# --- interpret response: convert raw user response into structured JSON ---
router.route(
    "interpret_response",
    agent="user-researcher.md",
    model="claude-opus",
)

# --- assess reliability: evaluate confidence in extracted understanding ---
router.route(
    "assess_reliability",
    agent="reliability-assessor.md",
    model="claude-opus",
)

# --- decompose: break the problem into sections ---
router.route(
    "decompose",
    agent="decomposer.md",
    model="claude-opus",
)

# --- align proposal: check proposal alignment with intent ---
router.route(
    "align_proposal",
    agent="proposal-aligner.md",
    model="claude-opus",
)

# --- expand proposal: expand proposal based on alignment feedback ---
router.route(
    "expand_proposal",
    agent="proposal-expander.md",
    model="claude-opus",
)

# --- explore factors: explore cross-cutting factors ---
router.route(
    "explore_factors",
    agent="factor-explorer.md",
    model="claude-opus",
)

# --- build codemap: produce project-level routing map ---
router.route(
    "build_codemap",
    agent="codemap-builder.md",
    model="claude-opus",
)

# --- explore sections: explore sections discovered during decomposition ---
router.route(
    "explore_sections",
    agent="section-explorer.md",
    model="claude-opus",
)

# --- discover substrate: discover substrate layer for the project ---
router.route(
    "discover_substrate",
    agent="substrate-discoverer.md",
    model="claude-opus",
)
