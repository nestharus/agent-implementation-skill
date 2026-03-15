"""Domain researcher scenario evals.

Tests that the domain-researcher agent produces proper dossiers when given
research questions with supporting spec context.

The domain-researcher reads a research ticket containing blocking questions,
searches the provided context for answers, and produces a structured JSON
result with findings, citations, and extracted constraints:
    {
      "ticket_id": "...",
      "status": "answered" | "partial" | "unanswerable",
      "findings": [...],
      "extracted_constraints": [...],
      ...
    }

These scenarios dispatch the real agent with pre-seeded artifacts and
check the structured output and citation behavior.

Scenarios:
  domain_researcher_answers_blocking_question: Answerable question -> non-empty findings
  domain_researcher_cites_evidence: Same question -> citations reference source artifacts
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

def _extract_result_json(text: str) -> dict | None:
    """Extract the structured ticket-result JSON from agent output.

    The agent is instructed to emit a JSON block with ticket_id and
    findings.  Try fenced block first, then raw JSON with expected keys.
    """
    # Try fenced JSON block (```json ... ```)
    match = re.search(r"```(?:json)?\s*\n(\{.*?\})\s*\n```", text, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(1))
            if "findings" in data:
                return data
        except json.JSONDecodeError:
            pass
    # Fallback: find any JSON object containing "findings" key
    # Use a greedy approach to capture nested structures
    for m in re.finditer(r"\{[^{}]*\"findings\"\s*:\s*\[.*?\][^{}]*\}", text, re.DOTALL):
        try:
            data = json.loads(m.group(0))
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            continue
    # Last resort: try to parse the largest JSON object in the text
    for m in re.finditer(r"\{[\s\S]*?\"findings\"[\s\S]*?\}", text):
        try:
            data = json.loads(m.group(0))
            if isinstance(data, dict) and "findings" in data:
                return data
        except json.JSONDecodeError:
            continue
    return None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_BLOCKING_QUESTION = (
    "What is the canonical database connectivity variable: "
    "POSTGRES_DSN or DATABASE_URL?"
)

_SPEC_EXCERPT = textwrap.dedent("""\
    # Section 03: Database Connectivity Layer

    ## Problem
    The application has inconsistent database connection configuration.
    Some modules read `POSTGRES_DSN`, others read `DATABASE_URL`.
    New developers routinely misconfigure their environment because the
    correct variable is undocumented.

    ## Requirements
    - REQ-01: A single canonical environment variable for DB connectivity
    - REQ-02: Backward compat shim for the deprecated variable name
    - REQ-03: Startup validation that fails fast if the variable is missing

    ## Constraints
    - The production Kubernetes manifests use `DATABASE_URL` exclusively
    - The legacy migration scripts reference `POSTGRES_DSN`
    - The ORM bootstrap in `db/engine.py` reads `DATABASE_URL`
""")

_DEPLOY_MANIFEST = textwrap.dedent("""\
    # File: k8s/deployment.yaml (excerpt)
    env:
      - name: DATABASE_URL
        valueFrom:
          secretKeyRef:
            name: db-credentials
            key: connection-string
""")

_ENGINE_FILE = textwrap.dedent("""\
    # File: db/engine.py
    import os
    from sqlalchemy import create_engine

    DATABASE_URL = os.environ["DATABASE_URL"]
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
""")

_LEGACY_MIGRATION = textwrap.dedent("""\
    # File: migrations/env.py
    import os

    # Legacy: reads POSTGRES_DSN for backward compatibility
    dsn = os.environ.get("POSTGRES_DSN") or os.environ["DATABASE_URL"]
""")

_RESEARCH_TICKET = textwrap.dedent("""\
    {{
      "ticket_id": "T-01",
      "scope": "section-03",
      "questions": [
        "{question}"
      ],
      "research_type": "code",
      "expected_deliverable": "recommended_approach",
      "stop_conditions": [
        "Identified which variable is canonical based on production usage",
        "Found authoritative configuration source"
      ],
      "output_path": "artifacts/research/T-01-result.json"
    }}
""").format(question=_BLOCKING_QUESTION)


# ---------------------------------------------------------------------------
# Setup: shared between both scenarios
# ---------------------------------------------------------------------------

def _setup_researcher(planspace: Path, codespace: Path) -> Path:
    """Seed artifacts for a domain-researcher ticket with answerable context."""
    artifacts = planspace / "artifacts"
    research_dir = artifacts / "research"
    sections = artifacts / "sections"
    research_dir.mkdir(parents=True, exist_ok=True)
    sections.mkdir(parents=True, exist_ok=True)

    # Section spec
    section_path = sections / "section-03.md"
    section_path.write_text(_SPEC_EXCERPT, encoding="utf-8")

    # Research ticket
    ticket_path = research_dir / "T-01-ticket.json"
    ticket_path.write_text(_RESEARCH_TICKET, encoding="utf-8")

    # Minimal codespace with evidence files
    db_dir = codespace / "db"
    db_dir.mkdir(parents=True, exist_ok=True)
    (db_dir / "__init__.py").write_text("", encoding="utf-8")
    (db_dir / "engine.py").write_text(_ENGINE_FILE, encoding="utf-8")

    k8s_dir = codespace / "k8s"
    k8s_dir.mkdir(parents=True, exist_ok=True)
    (k8s_dir / "deployment.yaml").write_text(_DEPLOY_MANIFEST, encoding="utf-8")

    migrations_dir = codespace / "migrations"
    migrations_dir.mkdir(parents=True, exist_ok=True)
    (migrations_dir / "env.py").write_text(_LEGACY_MIGRATION, encoding="utf-8")

    # Build the prompt -- inline the ticket and evidence so the agent can
    # research without needing file-system access.
    prompt_path = artifacts / "domain-researcher-T01-prompt.md"
    prompt_path.write_text(
        "# Task: Execute Research Ticket T-01\n\n"
        "## Research Ticket\n\n"
        f"```json\n{_RESEARCH_TICKET}\n```\n\n"
        "## Section Context\n\n"
        f"{_SPEC_EXCERPT}\n\n"
        "## Code Evidence\n\n"
        "### db/engine.py\n\n"
        f"```python\n{_ENGINE_FILE}\n```\n\n"
        "### k8s/deployment.yaml\n\n"
        f"```yaml\n{_DEPLOY_MANIFEST}\n```\n\n"
        "### migrations/env.py\n\n"
        f"```python\n{_LEGACY_MIGRATION}\n```\n\n"
        "## Instructions\n\n"
        "You are the domain researcher. Read the research ticket above and\n"
        "answer the blocking question using the provided code evidence.\n"
        "Produce a structured JSON result per the domain-researcher contract.\n",
        encoding="utf-8",
    )
    return prompt_path


# ---------------------------------------------------------------------------
# Check functions: answers_blocking_question scenario
# ---------------------------------------------------------------------------

def _check_output_nonempty(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify agent produced non-empty output."""
    if agent_output and agent_output.strip():
        return True, f"Output is non-empty ({len(agent_output)} chars)"
    return False, "Agent output is empty"


def _check_has_result_json(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify output contains a structured ticket-result JSON block."""
    data = _extract_result_json(agent_output)
    if data is not None:
        return True, f"Result JSON found with keys: {list(data.keys())}"
    return False, "No structured ticket-result JSON block found in agent output"


def _check_references_question(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify output references the blocking question."""
    lower = agent_output.lower()
    # The agent should mention both variable names from the question
    has_postgres_dsn = "postgres_dsn" in lower
    has_database_url = "database_url" in lower
    if has_postgres_dsn and has_database_url:
        return True, "Output references both POSTGRES_DSN and DATABASE_URL"
    missing = []
    if not has_postgres_dsn:
        missing.append("POSTGRES_DSN")
    if not has_database_url:
        missing.append("DATABASE_URL")
    return False, f"Output does not reference: {', '.join(missing)}"


def _check_provides_concrete_finding(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify output provides a concrete finding or recommendation.

    The evidence clearly shows DATABASE_URL is canonical (used in
    production k8s manifests and the ORM engine).  The agent should
    state a clear recommendation, not hedge.
    """
    data = _extract_result_json(agent_output)
    if data is None:
        # Fall back to checking the narrative text
        lower = agent_output.lower()
        if "database_url" in lower and any(
            term in lower for term in [
                "canonical", "recommend", "standard", "primary",
                "authoritative", "production", "should use",
            ]
        ):
            return True, "Narrative contains a concrete recommendation for DATABASE_URL"
        return False, "No concrete finding or recommendation found"

    findings = data.get("findings", [])
    if not findings:
        return False, "Result JSON has empty findings array"

    # Check that at least one finding has a substantive answer
    for finding in findings:
        answer = finding.get("answer", "")
        if answer and len(answer) > 20:
            answer_lower = answer.lower()
            if "database_url" in answer_lower and any(
                term in answer_lower for term in [
                    "canonical", "recommend", "standard", "primary",
                    "authoritative", "production", "should use",
                ]
            ):
                return True, f"Finding provides concrete recommendation: {answer[:100]}..."
    return False, "Findings do not contain a concrete recommendation"


def _check_no_idk_when_answerable(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify agent does NOT say 'I don't know' when the answer is in context.

    The provided spec and code evidence clearly indicate DATABASE_URL is
    the canonical variable.  The agent should not punt.
    """
    lower = agent_output.lower()
    punt_phrases = [
        "i don't know",
        "i do not know",
        "cannot determine",
        "unable to determine",
        "insufficient information",
        "not enough information",
        "no way to know",
    ]
    for phrase in punt_phrases:
        if phrase in lower:
            return False, f"Agent punted with: '{phrase}'"

    # Also check the status field in JSON
    data = _extract_result_json(agent_output)
    if data is not None:
        status = data.get("status", "")
        if status == "unanswerable":
            return False, (
                "Result JSON has status='unanswerable' but the answer "
                "is clearly present in the provided context"
            )

    return True, "Agent did not punt -- provided a substantive answer"


# ---------------------------------------------------------------------------
# Check functions: cites_evidence scenario
# ---------------------------------------------------------------------------

def _check_cites_file_paths(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify output references specific file paths from the evidence.

    The evidence includes db/engine.py, k8s/deployment.yaml, and
    migrations/env.py.  The agent should cite at least one of these.
    """
    evidence_paths = [
        "db/engine.py",
        "engine.py",
        "k8s/deployment.yaml",
        "deployment.yaml",
        "migrations/env.py",
    ]
    lower = agent_output.lower()
    found = [p for p in evidence_paths if p.lower() in lower]
    if found:
        return True, f"Output references file paths: {found}"

    # Also check structured findings citations
    data = _extract_result_json(agent_output)
    if data is not None:
        for finding in data.get("findings", []):
            for citation in finding.get("citations", []):
                citation_lower = citation.lower()
                for path in evidence_paths:
                    if path.lower() in citation_lower:
                        return True, f"Finding cites file path in citation: {citation}"

    return False, (
        "Output does not reference any evidence file paths "
        "(expected db/engine.py, k8s/deployment.yaml, or migrations/env.py)"
    )


def _check_includes_evidence_substance(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify output includes quoted or paraphrased evidence from artifacts.

    The agent should reference concrete details from the code evidence,
    not just make generic claims.
    """
    lower = agent_output.lower()
    # Evidence-specific terms that would only appear if the agent read the context
    evidence_markers = [
        "secretkeyref",           # from k8s manifest
        "db-credentials",         # from k8s manifest
        "pool_pre_ping",          # from engine.py
        "create_engine",          # from engine.py
        "sqlalchemy",             # from engine.py
        "os.environ",             # from engine.py or migrations/env.py
        "backward compat",        # from migrations/env.py comment
        "legacy",                 # from migrations/env.py comment
        "kubernetes",             # paraphrasing k8s manifest
        "production manifest",    # paraphrasing k8s
        "k8s",                    # shorthand for kubernetes
        "migration",              # from migrations context
        "orm",                    # paraphrasing engine.py
        "connection-string",      # from k8s manifest
    ]
    found = [m for m in evidence_markers if m in lower]
    if len(found) >= 2:
        return True, f"Output references evidence details: {found}"
    if len(found) == 1:
        return True, f"Output references at least one evidence detail: {found}"
    return False, (
        "Output does not reference specific details from the provided "
        "code evidence (expected terms like 'k8s', 'sqlalchemy', "
        "'create_engine', 'secretKeyRef', etc.)"
    )


def _check_citations_in_findings(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify the structured findings include non-empty citations arrays.

    Per the domain-researcher contract: 'Every claim must have at least
    one citation.'
    """
    data = _extract_result_json(agent_output)
    if data is None:
        # Fall back: check if the narrative text mentions file references
        lower = agent_output.lower()
        has_file_ref = any(
            p in lower for p in ["engine.py", "deployment.yaml", "env.py"]
        )
        if has_file_ref:
            return True, "No structured JSON but narrative references source files"
        return False, "No result JSON and no file references in narrative"

    findings = data.get("findings", [])
    if not findings:
        return False, "Result JSON has empty findings array"

    cited_count = 0
    for finding in findings:
        citations = finding.get("citations", [])
        if citations:
            cited_count += 1

    if cited_count == len(findings):
        return True, f"All {cited_count} finding(s) include citations"
    if cited_count > 0:
        return True, (
            f"{cited_count}/{len(findings)} findings include citations "
            f"(partial but acceptable)"
        )
    return False, "No findings include citations"


# ---------------------------------------------------------------------------
# Exported scenarios
# ---------------------------------------------------------------------------

SCENARIOS = [
    Scenario(
        name="domain_researcher_answers_blocking_question",
        agent_file="domain-researcher.md",
        model_policy_key="research_domain_ticket",
        setup=_setup_researcher,
        checks=[
            Check(
                description="Agent output is non-empty",
                verify=_check_output_nonempty,
            ),
            Check(
                description="Output contains structured ticket-result JSON",
                verify=_check_has_result_json,
            ),
            Check(
                description="Output references the blocking question variables",
                verify=_check_references_question,
            ),
            Check(
                description="Output provides a concrete finding/recommendation",
                verify=_check_provides_concrete_finding,
            ),
            Check(
                description="Agent does not punt when answer is in context",
                verify=_check_no_idk_when_answerable,
            ),
        ],
    ),
    Scenario(
        name="domain_researcher_cites_evidence",
        agent_file="domain-researcher.md",
        model_policy_key="research_domain_ticket",
        setup=_setup_researcher,
        checks=[
            Check(
                description="Output references specific file paths from evidence",
                verify=_check_cites_file_paths,
            ),
            Check(
                description="Output includes quoted or paraphrased evidence details",
                verify=_check_includes_evidence_substance,
            ),
            Check(
                description="Structured findings include citations",
                verify=_check_citations_in_findings,
            ),
        ],
    ),
]
