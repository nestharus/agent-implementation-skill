"""Governance markdown loaders for planspace advisory indexes."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from signals.repository.artifact_io import write_json
from orchestrator.path_registry import PathRegistry

logger = logging.getLogger(__name__)

_HEADER_RE = re.compile(
    r"^##\s+(?P<identifier>(?:PRB|PAT)-\d+):\s*(?P<title>.+?)\s*$",
    re.MULTILINE,
)
_CONSTRAINT_HEADER_RE = re.compile(
    r"^##\s+(?P<identifier>CON-\d+):\s*(?P<title>.+?)\s*$",
    re.MULTILINE,
)
_FIELD_RE = re.compile(
    r"^\*\*(?P<label>[^*]+)\*\*:\s*(?P<value>.*)$",
    re.MULTILINE,
)


def _read_if_exists(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _split_records(text: str, prefix: str) -> list[tuple[str, str, str]]:
    matches = [
        match
        for match in _HEADER_RE.finditer(text)
        if match.group("identifier").startswith(prefix)
    ]
    records: list[tuple[str, str, str]] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        records.append((
            match.group("identifier").strip(),
            match.group("title").strip(),
            text[start:end].strip(),
        ))
    return records


def _field_map(body: str) -> dict[str, str]:
    """Extract bold-label fields, including continuation lines for multiline values."""
    fields: dict[str, str] = {}
    lines = body.splitlines()
    current_label: str | None = None
    current_parts: list[str] = []

    for line in lines:
        match = _FIELD_RE.match(line)
        if match:
            if current_label is not None:
                fields[current_label] = " ".join(current_parts).strip()
            current_label = match.group("label").strip().lower()
            current_parts = [match.group("value").strip()]
        elif current_label is not None:
            stripped = line.strip()
            if not stripped:
                fields[current_label] = " ".join(current_parts).strip()
                current_label = None
                current_parts = []
            elif stripped.startswith(("- ", "* ", "## ", "---")):
                fields[current_label] = " ".join(current_parts).strip()
                current_label = None
                current_parts = []
            else:
                current_parts.append(stripped)

    if current_label is not None:
        fields[current_label] = " ".join(current_parts).strip()

    return fields


def _comma_list(value: str) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _extract_bullets(body: str, label: str) -> list[str]:
    marker = f"**{label}**:"
    index = body.find(marker)
    if index < 0:
        return []

    lines = body[index + len(marker):].splitlines()
    items: list[str] = []
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            if items:
                break
            continue
        if line.startswith("**") and not line.startswith("- "):
            break
        if line.startswith("---") or line.startswith("## "):
            break
        if line.startswith(("- ", "* ")):
            items.append(line[2:].strip())
            continue
        # Numbered list items (e.g., "1. Use content_hash()...")
        numbered = re.match(r"^\d+\.\s+(.*)$", line)
        if numbered is not None:
            items.append(numbered.group(1).strip())
            continue
        # Continuation line — append to the current bullet/numbered item
        if items and line:
            items[-1] = items[-1] + " " + line
            continue
        if not items:
            continue
    return items


def _extract_section_items(text: str, heading: str) -> list[str]:
    pattern = re.compile(
        rf"^##\s+{re.escape(heading)}\s*$",
        re.MULTILINE,
    )
    match = pattern.search(text)
    if match is None:
        return []

    remainder = text[match.end():]
    next_heading = re.search(r"^##\s+", remainder, re.MULTILINE)
    block = remainder[:next_heading.start()] if next_heading else remainder

    items: list[str] = []
    for line in block.splitlines():
        stripped = line.strip()
        if stripped.startswith(("- ", "* ")):
            items.append(stripped[2:].strip())
            continue
        numbered = re.match(r"^\d+\.\s+(.*)$", stripped)
        if numbered is not None:
            items.append(numbered.group(1).strip())
    return items


def _extract_section_text(text: str, heading: str) -> str:
    pattern = re.compile(
        rf"^##\s+{re.escape(heading)}\s*$",
        re.MULTILINE,
    )
    match = pattern.search(text)
    if match is None:
        return ""

    remainder = text[match.end():]
    next_heading = re.search(r"^##\s+", remainder, re.MULTILINE)
    block = remainder[:next_heading.start()] if next_heading else remainder
    return block.strip()


def parse_problem_index(codespace: Path) -> list[dict]:
    """Parse governance/problems/index.md into structured records."""
    text = _read_if_exists(codespace / "governance" / "problems" / "index.md")
    if not text:
        return []

    records: list[dict] = []
    for problem_id, title, body in _split_records(text, "PRB-"):
        fields = _field_map(body)
        records.append({
            "problem_id": problem_id,
            "title": title,
            "status": fields.get("status", ""),
            "provenance": fields.get("provenance", ""),
            "regions": _comma_list(fields.get("regions", "")),
            "solution_surfaces": fields.get("solution surfaces", ""),
            "related_patterns": _comma_list(fields.get("related patterns", "")),
        })
    return records


def parse_pattern_index(codespace: Path) -> list[dict]:
    """Parse governance/patterns/index.md into structured records."""
    text = _read_if_exists(codespace / "governance" / "patterns" / "index.md")
    if not text:
        return []

    records: list[dict] = []
    for pattern_id, title, body in _split_records(text, "PAT-"):
        fields = _field_map(body)
        known_instances = _extract_bullets(body, "Known instances")
        if not known_instances:
            known_instances = _comma_list(fields.get("known instances", ""))
        template_items = _extract_bullets(body, "Template")
        if not template_items:
            template_text = fields.get("template", "")
            template_items = [template_text] if template_text else []
        records.append({
            "pattern_id": pattern_id,
            "title": title,
            "problem_class": fields.get("problem class", ""),
            "regions": _comma_list(fields.get("regions", "")),
            "solution_surfaces": fields.get("solution surfaces", ""),
            "philosophy": fields.get("philosophy", ""),
            "canonical_instance": fields.get("canonical instance", ""),
            "known_instances": known_instances,
            "template": template_items,
            "conformance": fields.get("conformance", ""),
        })
    return records


def parse_constraint_index(codespace: Path) -> list[dict]:
    """Parse governance/constraints/index.md into structured records."""
    text = _read_if_exists(codespace / "governance" / "constraints" / "index.md")
    if not text:
        return []

    records: list[dict] = []
    matches = [
        m for m in _CONSTRAINT_HEADER_RE.finditer(text)
    ]
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        constraint_id = match.group("identifier").strip()
        title = match.group("title").strip()
        body = text[start:end].strip()
        fields = _field_map(body)
        records.append({
            "constraint_id": constraint_id,
            "title": title,
            "status": fields.get("status", ""),
            "provenance": fields.get("provenance", ""),
            "scope": fields.get("scope", "global"),
            "enforcement": fields.get("enforcement", ""),
            "related_problems": _comma_list(fields.get("related problems", "")),
            "related_patterns": _comma_list(fields.get("related patterns", "")),
        })
    return records


def parse_philosophy_profiles(codespace: Path) -> list[dict]:
    """Parse philosophy/profiles/*.md into structured records."""
    profiles_dir = codespace / "philosophy" / "profiles"
    if not profiles_dir.is_dir():
        return []

    records: list[dict] = []
    for profile_path in sorted(profiles_dir.glob("*.md")):
        text = profile_path.read_text(encoding="utf-8")
        failure_mode = _extract_section_text(text, "Preferred Failure Mode")
        records.append({
            "profile_id": profile_path.stem,
            "values": _extract_section_items(text, "Values (priority order)"),
            "failure_mode": " ".join(failure_mode.split()),
            "risk_posture": " ".join(
                _extract_section_text(text, "Risk Posture").split()
            ),
            "anti_patterns": _extract_section_items(text, "Anti-Patterns"),
        })
    return records


def parse_region_profile_map(codespace: Path) -> dict:
    """Parse philosophy/region-profile-map.md."""
    path = codespace / "philosophy" / "region-profile-map.md"
    text = _read_if_exists(path)
    if not text:
        return {"default": "", "overrides": {}}

    default_profile = ""
    default_match = re.search(
        r"All regions:\s*`?([^`\n]+)`?",
        text,
    )
    if default_match is not None:
        default_profile = default_match.group(1).strip()

    overrides: dict[str, str] = {}
    overrides_block = _extract_section_text(text, "Overrides")
    for line in overrides_block.splitlines():
        stripped = line.strip()
        if not stripped.startswith(("- ", "* ")):
            continue
        match = re.match(
            r"[-*]\s+(.+?)\s*(?:->|:)\s*`?([^`]+)`?\s*$",
            stripped,
        )
        if match is not None:
            overrides[match.group(1).strip()] = match.group(2).strip()

    return {"default": default_profile, "overrides": overrides}


def parse_synthesis_cues(codespace: Path) -> dict[str, list[str]]:
    """Extract bounded runtime-region cues from system-synthesis.md.

    Returns a mapping of region names to associated problem IDs, pattern
    IDs, and philosophy profiles mentioned in that region's section.
    Bounded: only parses the ``## Regions`` block and extracts structured
    cross-references (PRB-*, PAT-*, PHI-*).  Does not mirror the full
    document.

    PAT-0011 (R109): synthesis cues must be consumed when available.
    """
    path = codespace / "system-synthesis.md"
    text = _read_if_exists(path)
    if not text:
        return {}

    # Find the Regions block
    regions_match = re.search(r"^## Regions\s*$", text, re.MULTILINE)
    if regions_match is None:
        return {}

    # End at the next top-level heading (## but not ###)
    remainder = text[regions_match.end():]
    next_top = re.search(r"^## (?!#)", remainder, re.MULTILINE)
    regions_block = remainder[:next_top.start()] if next_top else remainder

    # Parse ### subsections as region names
    cues: dict[str, list[str]] = {}
    subsection_re = re.compile(r"^### (.+?)\s*$", re.MULTILINE)
    sub_matches = list(subsection_re.finditer(regions_block))
    for idx, sub in enumerate(sub_matches):
        region_name = sub.group(1).strip()
        start = sub.end()
        end = sub_matches[idx + 1].start() if idx + 1 < len(sub_matches) else len(regions_block)
        section_text = regions_block[start:end]
        # Extract cross-references
        refs = re.findall(r"\b(PRB-\d+|PAT-\d+|PHI-\w+)\b", section_text)
        if refs:
            cues[region_name.lower()] = sorted(set(refs))

    return cues


def bootstrap_governance_if_missing(codespace: Path, planspace: Path) -> bool:
    """Create minimal governance scaffolding if codespace has none.

    For greenfield projects that have no governance docs, this creates
    the minimal directory structure and placeholder files so that
    ``build_governance_indexes()`` has something to parse.

    Returns True if scaffolding was created, False if governance
    already exists.
    """
    problems_path = codespace / "governance" / "problems" / "index.md"
    patterns_path = codespace / "governance" / "patterns" / "index.md"

    if problems_path.exists() or patterns_path.exists():
        return False

    logger.info("Bootstrapping governance scaffolding for greenfield project")

    problems_path.parent.mkdir(parents=True, exist_ok=True)
    patterns_path.parent.mkdir(parents=True, exist_ok=True)

    problems_path.write_text(
        "# Problem Archive\n\n"
        "Problems discovered during development are documented here.\n",
        encoding="utf-8",
    )
    patterns_path.write_text(
        "# Pattern Catalog\n\n"
        "Patterns discovered during development are documented here.\n",
        encoding="utf-8",
    )

    risk_path = codespace / "governance" / "risk-register.md"
    if not risk_path.exists():
        risk_path.write_text(
            "# Risk Register\n\n"
            "Risks identified during development are documented here.\n",
            encoding="utf-8",
        )

    constraints_path = codespace / "governance" / "constraints" / "index.md"
    if not constraints_path.exists():
        constraints_path.parent.mkdir(parents=True, exist_ok=True)
        constraints_path.write_text(
            "# Constraint Archive\n\n"
            "Verified constraints and value-scale commitments are documented here.\n",
            encoding="utf-8",
        )

    synthesis_path = codespace / "system-synthesis.md"
    if not synthesis_path.exists():
        synthesis_path.write_text(
            "# System Synthesis\n\n"
            "Architecture and governance connections.\n\n"
            "## Regions\n",
            encoding="utf-8",
        )

    return True


def build_governance_indexes(codespace: Path, planspace: Path) -> bool:
    """Parse governance docs and mirror advisory JSON indexes into planspace.

    Returns True only if all authoritative indexes parsed successfully.
    Returns False when any parse failed — the index status artifact records
    which indexes failed so downstream consumers can distinguish parse
    failure from true no-governance (PAT-0008 R108).
    """
    paths = PathRegistry(planspace)
    paths.governance_dir().mkdir(parents=True, exist_ok=True)

    problem_index: list[dict] = []
    pattern_index: list[dict] = []
    profile_index: list[dict] = []
    region_profile_map: dict = {"default": "", "overrides": {}}
    parse_failures: list[str] = []

    try:
        problem_index = parse_problem_index(codespace)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to parse governance problem index: %s", exc)
        parse_failures.append(f"problem_index: {exc}")

    try:
        pattern_index = parse_pattern_index(codespace)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to parse governance pattern index: %s", exc)
        parse_failures.append(f"pattern_index: {exc}")

    try:
        profile_index = parse_philosophy_profiles(codespace)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to parse philosophy profiles: %s", exc)
        parse_failures.append(f"profile_index: {exc}")

    try:
        region_profile_map = parse_region_profile_map(codespace)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to parse region-profile map: %s", exc)
        parse_failures.append(f"region_profile_map: {exc}")

    synthesis_cues: dict[str, list[str]] = {}
    try:
        synthesis_cues = parse_synthesis_cues(codespace)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to parse synthesis cues: %s", exc)
        parse_failures.append(f"synthesis_cues: {exc}")

    constraint_index: list[dict] = []
    try:
        constraint_index = parse_constraint_index(codespace)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to parse governance constraint index: %s", exc)
        parse_failures.append(f"constraint_index: {exc}")

    write_json(paths.governance_problem_index(), problem_index)
    write_json(paths.governance_pattern_index(), pattern_index)
    write_json(paths.governance_profile_index(), profile_index)
    write_json(paths.governance_region_profile_map(), region_profile_map)
    write_json(paths.governance_dir() / "synthesis-cues.json", synthesis_cues)
    write_json(paths.governance_constraint_index(), constraint_index)

    # Write index status so downstream consumers can distinguish parse
    # failure from true no-governance
    status = {
        "ok": len(parse_failures) == 0,
        "parse_failures": parse_failures,
    }
    write_json(paths.governance_dir() / "index-status.json", status)

    if parse_failures:
        logger.warning(
            "Governance index build completed with %d parse failure(s) — "
            "downstream consumers should check index-status.json",
            len(parse_failures),
        )
        return False

    return True
