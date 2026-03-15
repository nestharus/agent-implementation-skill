"""Philosophy agent scenario evals.

Tests that the philosophy-source-selector and philosophy-distiller agents
produce valid, non-empty outputs and handle degenerate inputs correctly.

These scenarios target QA failure I1 where philosophy never materialized
due to empty/malformed outputs going undetected.

Scenarios:
  philosophy_selector_greenfield_empty: No philosophy sources -> status "empty"
  philosophy_selector_with_spec: Spec with design values -> non-empty sources
  philosophy_distiller_from_spec: Clear philosophy in source -> non-empty output
  philosophy_distiller_empty_source: Empty source -> honest inability report
"""

from __future__ import annotations

import json
import re
import textwrap
from pathlib import Path

from evals.harness import Check, Scenario


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_json_from_output(text: str) -> dict | None:
    """Extract a JSON object from agent output.

    Try fenced JSON block first, then scan for bare JSON objects that
    look like selector output (contain "status" or "sources" keys).
    """
    # Try fenced JSON block
    match = re.search(r"```(?:json)?\s*\n(\{.*?\})\s*\n```", text, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(1))
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
    # Fallback: find JSON objects with expected keys
    for m in re.finditer(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", text):
        try:
            data = json.loads(m.group(0))
            if isinstance(data, dict) and ("status" in data or "sources" in data):
                return data
        except json.JSONDecodeError:
            continue
    return None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_BARE_SPEC = textwrap.dedent("""\
    # Project: Widget Service

    ## Overview
    A REST API service that manages widgets.  CRUD operations on
    widgets stored in PostgreSQL.

    ## Requirements
    - REQ-01: Create, read, update, delete widgets via REST endpoints
    - REQ-02: Input validation with descriptive error messages
    - REQ-03: Pagination for list endpoints
    - REQ-04: PostgreSQL persistence with migrations

    ## Tech Stack
    - Python 3.12 / FastAPI
    - SQLAlchemy ORM
    - Alembic migrations
""")

_STUB_GOVERNANCE = textwrap.dedent("""\
    # Governance

    ## Status
    Project is in initial setup phase.  No governance decisions recorded yet.
""")

_SPEC_WITH_PHILOSOPHY = textwrap.dedent("""\
    # Project: Distributed Task Scheduler

    ## Overview
    A distributed task scheduler that coordinates work across worker nodes.

    ## Design Philosophy
    We value simplicity over flexibility.  Every new abstraction must
    justify its existence with a concrete use case today -- not a
    hypothetical future need.

    Fail-closed on uncertainty.  When the scheduler cannot determine
    whether a task completed, it must assume failure and retry rather
    than assume success and risk silent data loss.

    Human approval gates are mandatory for destructive operations.
    No automated path may delete production data or reconfigure
    cluster topology without explicit operator confirmation.

    Prefer explicit error returns over exception hierarchies.
    Callers must handle errors at the call site, not rely on
    catch-all handlers three layers up.

    ## Requirements
    - REQ-01: Distribute tasks across worker nodes with load balancing
    - REQ-02: At-least-once delivery guarantee for all tasks
    - REQ-03: Dead letter queue for tasks that exhaust retry budget
    - REQ-04: Operator dashboard for cluster health and task status
    - REQ-05: Graceful worker drain for rolling deployments

    ## Constraints
    - Must run on Kubernetes with horizontal pod autoscaling
    - Task payloads must be serializable as JSON (max 1MB)
    - Scheduling latency target: < 100ms p99
""")

_PHILOSOPHY_SOURCES_FOR_DISTILLER = textwrap.dedent("""\
    # Design Philosophy

    We value simplicity over flexibility.  Every new abstraction must
    justify its existence with a concrete use case today -- not a
    hypothetical future need.

    Fail-closed on uncertainty.  When the scheduler cannot determine
    whether a task completed, it must assume failure and retry rather
    than assume success and risk silent data loss.

    Human approval gates are mandatory for destructive operations.
    No automated path may delete production data or reconfigure
    cluster topology without explicit operator confirmation.

    Prefer explicit error returns over exception hierarchies.
    Callers must handle errors at the call site, not rely on
    catch-all handlers three layers up.
""")

_EMPTY_USER_SOURCE = textwrap.dedent("""\
    # My Philosophy

    TODO: fill in later
""")

_CATALOG_NO_PHILOSOPHY = textwrap.dedent("""\
    # Source Catalog

    ## Candidate Files

    ### spec.md
    Preview headings: Overview, Requirements, Tech Stack
    First 3 lines: "# Project: Widget Service", "## Overview",
    "A REST API service that manages widgets."

    ### governance.md
    Preview headings: Governance, Status
    First 3 lines: "# Governance", "## Status",
    "Project is in initial setup phase."
""")

_CATALOG_WITH_PHILOSOPHY = textwrap.dedent("""\
    # Source Catalog

    ## Candidate Files

    ### spec.md
    Preview headings: Overview, Design Philosophy, Requirements, Constraints
    First 3 lines: "# Project: Distributed Task Scheduler", "## Overview",
    "A distributed task scheduler that coordinates work across worker nodes."

    ### spec.md > Design Philosophy (section preview)
    "We value simplicity over flexibility.  Every new abstraction must
    justify its existence with a concrete use case today."
    "Fail-closed on uncertainty."
    "Human approval gates are mandatory for destructive operations."
    "Prefer explicit error returns over exception hierarchies."
""")


# ---------------------------------------------------------------------------
# Setup: Scenario 1 -- selector with greenfield, no philosophy
# ---------------------------------------------------------------------------

def _setup_selector_greenfield_empty(planspace: Path, codespace: Path) -> Path:
    """Greenfield planspace with only spec.md and stub governance."""
    artifacts = planspace / "artifacts"
    signals = artifacts / "signals"
    artifacts.mkdir(parents=True, exist_ok=True)
    signals.mkdir(parents=True, exist_ok=True)

    # Write the spec and governance files into planspace
    (planspace / "spec.md").write_text(_BARE_SPEC, encoding="utf-8")
    (planspace / "governance.md").write_text(_STUB_GOVERNANCE, encoding="utf-8")

    # Minimal codespace
    (codespace / "src").mkdir(parents=True, exist_ok=True)
    (codespace / "src" / "__init__.py").write_text("", encoding="utf-8")

    # Selector prompt with catalog
    prompt_path = artifacts / "philosophy-source-selector-prompt.md"
    prompt_path.write_text(
        "# Task: Select Philosophy Sources\n\n"
        "## Instructions\n"
        "Review the candidate catalog below and classify each file.\n"
        "Return a JSON signal indicating which files contain execution\n"
        "philosophy.  If no files contain philosophy, return status "
        '"empty" with an empty sources array.\n\n'
        "## Candidate Catalog\n\n"
        f"{_CATALOG_NO_PHILOSOPHY}\n\n"
        "## Output\n"
        "Write your JSON response.  If no philosophy sources found:\n"
        "```json\n"
        '{"status": "empty", "sources": []}\n'
        "```\n",
        encoding="utf-8",
    )
    return prompt_path


# ---------------------------------------------------------------------------
# Setup: Scenario 2 -- selector with spec containing philosophy
# ---------------------------------------------------------------------------

def _setup_selector_with_spec(planspace: Path, codespace: Path) -> Path:
    """Planspace with spec.md that contains explicit design values."""
    artifacts = planspace / "artifacts"
    signals = artifacts / "signals"
    artifacts.mkdir(parents=True, exist_ok=True)
    signals.mkdir(parents=True, exist_ok=True)

    # Write the philosophy-bearing spec
    (planspace / "spec.md").write_text(_SPEC_WITH_PHILOSOPHY, encoding="utf-8")

    # Minimal codespace
    (codespace / "src").mkdir(parents=True, exist_ok=True)
    (codespace / "src" / "__init__.py").write_text("", encoding="utf-8")

    # Selector prompt with catalog that includes philosophy preview
    prompt_path = artifacts / "philosophy-source-selector-prompt.md"
    prompt_path.write_text(
        "# Task: Select Philosophy Sources\n\n"
        "## Instructions\n"
        "Review the candidate catalog below and classify each file.\n"
        "Return a JSON signal indicating which files contain execution\n"
        "philosophy.\n\n"
        "## Candidate Catalog\n\n"
        f"{_CATALOG_WITH_PHILOSOPHY}\n\n"
        "## Output\n"
        "Write your JSON response with status and sources array:\n"
        "```json\n"
        '{"status": "selected", "sources": [{"path": "...", "reason": "..."}]}\n'
        "```\n"
        "If no philosophy sources found:\n"
        "```json\n"
        '{"status": "empty", "sources": []}\n'
        "```\n",
        encoding="utf-8",
    )
    return prompt_path


# ---------------------------------------------------------------------------
# Setup: Scenario 3 -- distiller with clear philosophy source
# ---------------------------------------------------------------------------

def _setup_distiller_from_spec(planspace: Path, codespace: Path) -> Path:
    """Planspace with a spec file containing clear design philosophy."""
    artifacts = planspace / "artifacts"
    signals = artifacts / "signals"
    artifacts.mkdir(parents=True, exist_ok=True)
    signals.mkdir(parents=True, exist_ok=True)

    # Write the philosophy source
    source_path = planspace / "spec.md"
    source_path.write_text(_SPEC_WITH_PHILOSOPHY, encoding="utf-8")

    # Write extracted philosophy content as distiller input
    philosophy_source = planspace / "artifacts" / "philosophy-source.md"
    philosophy_source.write_text(_PHILOSOPHY_SOURCES_FOR_DISTILLER, encoding="utf-8")

    # Minimal codespace
    (codespace / "src").mkdir(parents=True, exist_ok=True)
    (codespace / "src" / "__init__.py").write_text("", encoding="utf-8")

    # Distiller prompt
    prompt_path = artifacts / "philosophy-distiller-prompt.md"
    prompt_path.write_text(
        "# Task: Distill Philosophy\n\n"
        "## Instructions\n"
        "Read the execution philosophy source material below and convert\n"
        "it into an operational philosophy with numbered principles,\n"
        "interactions, and expansion guidance.\n\n"
        "## Execution Philosophy Source\n\n"
        f"{_PHILOSOPHY_SOURCES_FOR_DISTILLER}\n\n"
        "## Output\n"
        "Produce the content for `philosophy.md` with:\n"
        "- Numbered principles (P1, P2, ...) with Statement, Grounding, Test\n"
        "- Interactions section mapping reinforcing/tension relationships\n"
        "- Expansion Guidance section\n\n"
        "Also produce `philosophy-source-map.json` mapping principle IDs to\n"
        "source locations.\n\n"
        "If no extractable philosophy exists, leave philosophy.md empty\n"
        "and write {} to philosophy-source-map.json.\n",
        encoding="utf-8",
    )
    return prompt_path


# ---------------------------------------------------------------------------
# Setup: Scenario 4 -- distiller with empty/scaffold source
# ---------------------------------------------------------------------------

def _setup_distiller_empty_source(planspace: Path, codespace: Path) -> Path:
    """Planspace with an empty/scaffold-only user source."""
    artifacts = planspace / "artifacts"
    signals = artifacts / "signals"
    artifacts.mkdir(parents=True, exist_ok=True)
    signals.mkdir(parents=True, exist_ok=True)

    # Write the near-empty user source
    source_path = planspace / "artifacts" / "philosophy-source.md"
    source_path.write_text(_EMPTY_USER_SOURCE, encoding="utf-8")

    # Minimal codespace
    (codespace / "src").mkdir(parents=True, exist_ok=True)
    (codespace / "src" / "__init__.py").write_text("", encoding="utf-8")

    # Distiller prompt with empty source
    prompt_path = artifacts / "philosophy-distiller-prompt.md"
    prompt_path.write_text(
        "# Task: Distill Philosophy\n\n"
        "## Instructions\n"
        "Read the execution philosophy source material below and convert\n"
        "it into an operational philosophy with numbered principles.\n\n"
        "## Execution Philosophy Source\n\n"
        f"{_EMPTY_USER_SOURCE}\n\n"
        "## Output\n"
        "Produce the content for `philosophy.md` with numbered principles.\n\n"
        "If the source material is too thin or empty to yield stable\n"
        "principles:\n"
        "- Do NOT invent filler principles\n"
        "- Report that the source is insufficient\n"
        "- Optionally provide follow-up clarification questions\n"
        "- Do NOT silently produce empty output\n",
        encoding="utf-8",
    )
    return prompt_path


# ---------------------------------------------------------------------------
# Check functions: Scenario 1 -- selector greenfield empty
# ---------------------------------------------------------------------------

def _check_selector_empty_valid_json(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify output contains valid JSON."""
    data = _extract_json_from_output(agent_output)
    if data is not None:
        return True, f"Valid JSON found: {json.dumps(data)[:200]}"
    return False, "No valid JSON found in agent output"


def _check_selector_empty_status(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify status is 'empty' or 'none' indicating no sources found."""
    data = _extract_json_from_output(agent_output)
    if data is None:
        return False, "No JSON found to check status"
    status = data.get("status", "")
    if status in ("empty", "none"):
        return True, f'status="{status}" (correct for no-philosophy planspace)'
    # Also accept if sources array is explicitly empty
    sources = data.get("sources", None)
    if isinstance(sources, list) and len(sources) == 0:
        return True, f'status="{status}" with empty sources array (acceptable)'
    return False, f'Expected status "empty"/"none" or empty sources, got status="{status}", sources={sources}'


def _check_selector_empty_no_false_positive(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify output does NOT claim to have found philosophy sources."""
    data = _extract_json_from_output(agent_output)
    if data is None:
        # If no JSON, check the raw text does not claim sources found
        lower = agent_output.lower()
        if "found philosophy" in lower or "selected" in lower:
            return False, "Agent claims found/selected sources despite none existing"
        return True, "No JSON and no false-positive claims in text"
    sources = data.get("sources", [])
    if isinstance(sources, list) and len(sources) > 0:
        return False, f"Agent returned {len(sources)} source(s) from a planspace with no philosophy"
    return True, "No false-positive sources in output"


# ---------------------------------------------------------------------------
# Check functions: Scenario 2 -- selector with spec
# ---------------------------------------------------------------------------

def _check_selector_spec_valid_json(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify output contains valid JSON."""
    data = _extract_json_from_output(agent_output)
    if data is not None:
        return True, f"Valid JSON found: {json.dumps(data)[:200]}"
    return False, "No valid JSON found in agent output"


def _check_selector_spec_has_sources(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify sources array is non-empty or status indicates found sources."""
    data = _extract_json_from_output(agent_output)
    if data is None:
        return False, "No JSON found to check sources"
    status = data.get("status", "")
    sources = data.get("sources", [])
    if isinstance(sources, list) and len(sources) > 0:
        return True, f"Found {len(sources)} source(s): {[s.get('path', '?') for s in sources]}"
    if status == "selected":
        return True, 'status="selected" indicates sources found'
    return False, f'Expected non-empty sources or status="selected", got status="{status}", sources={sources}'


def _check_selector_spec_mentions_spec(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify the spec file is identified as a source."""
    data = _extract_json_from_output(agent_output)
    if data is None:
        # Check raw text
        if "spec" in agent_output.lower():
            return True, "Agent mentions spec in text output"
        return False, "No JSON found and no mention of spec"
    sources = data.get("sources", [])
    ambiguous = data.get("ambiguous", [])
    all_candidates = sources + ambiguous
    for entry in all_candidates:
        path = entry.get("path", "")
        if "spec" in path.lower():
            return True, f"spec identified in sources/ambiguous: {path}"
    # Check if spec is mentioned anywhere in output
    if "spec" in agent_output.lower():
        return True, "Agent mentions spec file in output text"
    return False, "spec.md not identified as source or ambiguous candidate"


# ---------------------------------------------------------------------------
# Check functions: Scenario 3 -- distiller from spec
# ---------------------------------------------------------------------------

def _check_distiller_output_nonempty(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify the distiller produced non-empty output."""
    stripped = agent_output.strip()
    if not stripped:
        return False, "Agent output is completely empty (I1 failure mode)"
    if len(stripped) < 50:
        return False, f"Agent output suspiciously short ({len(stripped)} chars): {stripped[:100]}"
    return True, f"Non-empty output ({len(stripped)} chars)"


def _check_distiller_has_principles(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify output contains philosophy/principle-like content."""
    # Look for numbered principles (P1, P2, etc.) or principle headings
    principle_pattern = re.search(r"P\d+[:\s]", agent_output)
    if principle_pattern:
        count = len(re.findall(r"P\d+[:\s]", agent_output))
        return True, f"Found {count} principle reference(s) (P1, P2, ...)"
    # Also accept principle/philosophy keywords
    lower = agent_output.lower()
    philosophy_keywords = [
        "principle", "philosophy", "doctrine", "constraint",
        "grounding", "interaction", "tension", "reinforc",
    ]
    found = [kw for kw in philosophy_keywords if kw in lower]
    if len(found) >= 2:
        return True, f"Found philosophy keywords: {found}"
    return False, "No numbered principles (P1, P2) or sufficient philosophy keywords found"


def _check_distiller_not_empty_philosophy(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify distiller does NOT produce empty philosophy.md content.

    This is the core I1 check: the distiller must not silently emit
    empty content when given real philosophy source material.
    """
    stripped = agent_output.strip()
    # Check for known empty-output patterns
    empty_patterns = [
        r"^\s*$",
        r"^#\s*Operational Philosophy\s*$",
        r"^\{\s*\}\s*$",
    ]
    for pattern in empty_patterns:
        if re.match(pattern, stripped, re.MULTILINE):
            return False, f"Output matches empty pattern: {pattern}"
    # Check for substance: should have multiple lines of content
    lines = [line for line in stripped.split("\n") if line.strip()]
    if len(lines) < 5:
        return False, f"Output has only {len(lines)} non-empty lines -- likely too thin"
    return True, f"Output has {len(lines)} non-empty lines of content"


# ---------------------------------------------------------------------------
# Check functions: Scenario 4 -- distiller empty source
# ---------------------------------------------------------------------------

def _check_distiller_empty_not_silent(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify the distiller does NOT silently produce empty output.

    When given an empty/scaffold source, the agent must communicate
    inability -- not just return nothing.
    """
    stripped = agent_output.strip()
    if not stripped:
        return False, "Agent produced completely empty output (silent failure, I1 mode)"
    if len(stripped) < 20:
        return False, f"Agent output suspiciously short ({len(stripped)} chars): {stripped}"
    return True, f"Non-empty response ({len(stripped)} chars)"


def _check_distiller_empty_reports_inability(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify the agent mentions inability or requests input."""
    lower = agent_output.lower()
    inability_signals = [
        "insufficient", "too thin", "no extractable", "cannot extract",
        "unable to", "not enough", "no philosophy", "empty",
        "no principles", "cannot distill", "cannot identify",
        "clarification", "follow-up", "more information",
        "no durable", "no cross-cutting", "scaffold",
        "todo", "fill in", "placeholder",
    ]
    found = [s for s in inability_signals if s in lower]
    if found:
        return True, f"Agent reports inability/requests input: {found[:5]}"
    return False, "Agent does not mention inability or request clarification"


def _check_distiller_empty_no_invented_principles(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify the agent does NOT invent filler principles from nothing.

    The source is 'TODO: fill in later' -- there is nothing to extract.
    Producing numbered principles would be fabrication.
    """
    # Count principle-like patterns
    principles = re.findall(r"###?\s*P\d+[:\s]", agent_output)
    if len(principles) >= 2:
        return False, (
            f"Agent invented {len(principles)} principles from empty source: "
            f"{principles[:5]}"
        )
    return True, f"No fabricated principles (found {len(principles)} P-headers)"


# ---------------------------------------------------------------------------
# Exported scenarios
# ---------------------------------------------------------------------------

SCENARIOS = [
    Scenario(
        name="philosophy_selector_greenfield_empty",
        agent_file="philosophy-source-selector.md",
        model_policy_key="intent_philosophy_selector",
        setup=_setup_selector_greenfield_empty,
        checks=[
            Check(
                description="Output contains valid JSON",
                verify=_check_selector_empty_valid_json,
            ),
            Check(
                description='Status is "empty"/"none" or sources array is empty',
                verify=_check_selector_empty_status,
            ),
            Check(
                description="Does not claim to have found philosophy sources",
                verify=_check_selector_empty_no_false_positive,
            ),
        ],
    ),
    Scenario(
        name="philosophy_selector_with_spec",
        agent_file="philosophy-source-selector.md",
        model_policy_key="intent_philosophy_selector",
        setup=_setup_selector_with_spec,
        checks=[
            Check(
                description="Output contains valid JSON",
                verify=_check_selector_spec_valid_json,
            ),
            Check(
                description="Sources array is non-empty or status indicates found",
                verify=_check_selector_spec_has_sources,
            ),
            Check(
                description="Spec file identified as philosophy source",
                verify=_check_selector_spec_mentions_spec,
            ),
        ],
    ),
    Scenario(
        name="philosophy_distiller_from_spec",
        agent_file="philosophy-distiller.md",
        model_policy_key="intent_philosophy",
        setup=_setup_distiller_from_spec,
        checks=[
            Check(
                description="Distiller output is non-empty",
                verify=_check_distiller_output_nonempty,
            ),
            Check(
                description="Output contains numbered principles or philosophy keywords",
                verify=_check_distiller_has_principles,
            ),
            Check(
                description="Does not produce empty philosophy (I1 failure mode)",
                verify=_check_distiller_not_empty_philosophy,
            ),
        ],
    ),
    Scenario(
        name="philosophy_distiller_empty_source",
        agent_file="philosophy-distiller.md",
        model_policy_key="intent_philosophy",
        setup=_setup_distiller_empty_source,
        checks=[
            Check(
                description="Does not silently produce empty output",
                verify=_check_distiller_empty_not_silent,
            ),
            Check(
                description="Reports inability or requests clarification",
                verify=_check_distiller_empty_reports_inability,
            ),
            Check(
                description="Does not invent filler principles from empty source",
                verify=_check_distiller_empty_no_invented_principles,
            ),
        ],
    ),
]
