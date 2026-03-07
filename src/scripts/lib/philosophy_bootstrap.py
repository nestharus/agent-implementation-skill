"""Global philosophy bootstrap helpers."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from lib.artifact_io import read_json, write_json
from lib.hash_service import content_hash, file_hash
from lib.path_registry import PathRegistry
from prompt_safety import write_validated_prompt
from section_loop.communication import _log_artifact, log
from section_loop.dispatch import (
    dispatch_agent,
    read_agent_signal,
    read_model_policy,
)


def walk_md_bounded(
    root: Path,
    *,
    max_depth: int,
    exclude_top_dirs: frozenset[str] = frozenset(),
    extensions: frozenset[str] = frozenset({".md"}),
):
    """Yield matching files under *root* with depth-bounded traversal."""
    if not root.is_dir():
        return
    root_s = str(root)
    for dirpath, dirnames, filenames in os.walk(root_s):
        rel = os.path.relpath(dirpath, root_s)
        depth = 0 if rel == "." else rel.count(os.sep) + 1

        if depth == 0:
            dirnames[:] = sorted(
                d for d in dirnames if d not in exclude_top_dirs
            )
        else:
            dirnames.sort()

        if depth + 1 >= max_depth:
            dirnames.clear()
        if depth + 1 > max_depth:
            continue

        for fname in sorted(filenames):
            if any(fname.endswith(ext) for ext in extensions):
                yield Path(dirpath) / fname


def build_philosophy_catalog(
    planspace: Path,
    codespace: Path,
    *,
    max_files: int = 50,
    max_size_kb: int = 100,
    max_depth: int = 3,
    extensions: frozenset[str] = frozenset({".md"}),
) -> list[dict]:
    """Build a mechanical catalog of candidate philosophy source files."""
    codespace_quota = max(max_files * 4 // 5, 1)
    planspace_quota = max(max_files - codespace_quota, 1)

    candidates: list[dict] = []
    seen: set[str] = set()

    for root_dir, quota, exclude_top in (
        (codespace, codespace_quota, frozenset()),
        (planspace, planspace_quota, frozenset({"artifacts"})),
    ):
        root_count = 0
        for found_file in walk_md_bounded(
            root_dir,
            max_depth=max_depth,
            exclude_top_dirs=exclude_top,
            extensions=extensions,
        ):
            try:
                size = found_file.stat().st_size
            except OSError:
                continue
            if size == 0 or size > max_size_kb * 1024:
                continue

            resolved = str(found_file.resolve())
            if resolved in seen:
                continue
            seen.add(resolved)

            try:
                lines = found_file.read_text(encoding="utf-8").splitlines()[:10]
            except (OSError, UnicodeDecodeError):
                continue

            candidates.append({
                "path": str(found_file),
                "size_kb": round(size / 1024, 1),
                "first_lines": "\n".join(lines),
            })
            root_count += 1
            if root_count >= quota:
                break

    return candidates


def validate_philosophy_grounding(
    philosophy_path: Path,
    source_map_path: Path,
    artifacts: Path,
) -> bool:
    """Validate that distilled philosophy is grounded in source files."""
    signal_dir = artifacts / "signals"
    signal_dir.mkdir(parents=True, exist_ok=True)
    fail_signal = signal_dir / "philosophy-grounding-failed.json"

    if not source_map_path.exists() or source_map_path.stat().st_size == 0:
        write_json(fail_signal, {
            "state": "philosophy_grounding_failed",
            "detail": (
                "Philosophy source map is missing or empty. "
                "Distilled philosophy cannot be verified as grounded. "
                "Section execution will be blocked until philosophy is available."
            ),
        })
        return False

    source_map = read_json(source_map_path)
    if source_map is None:
        log("Intent bootstrap: malformed source map — "
            "preserving as .malformed.json")
        write_json(fail_signal, {
            "state": "philosophy_grounding_failed",
            "detail": (
                "Philosophy source map is malformed. "
                "Section execution will be blocked until philosophy is available."
            ),
        })
        return False

    if not isinstance(source_map, dict):
        write_json(fail_signal, {
            "state": "philosophy_grounding_failed",
            "detail": (
                "Philosophy source map is not a JSON object. "
                "Section execution will be blocked until philosophy is available."
            ),
        })
        return False

    try:
        philosophy_text = philosophy_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False

    principle_ids = set(re.findall(r"\bP\d+\b", philosophy_text))
    if not principle_ids:
        return True

    map_keys = set(source_map.keys())
    unmapped = principle_ids - map_keys
    if unmapped:
        write_json(fail_signal, {
            "state": "philosophy_grounding_failed",
            "detail": (
                f"Principle IDs missing from source map: "
                f"{sorted(unmapped)}. Distilled philosophy may contain "
                f"invented principles. Section execution will be blocked."
            ),
            "unmapped_principles": sorted(unmapped),
            "total_principles": len(principle_ids),
            "mapped_principles": len(principle_ids - unmapped),
        })
        return False

    return True


def sha256_file(path: Path) -> str:
    """Return hex sha256 of file contents, or empty string on error."""
    return file_hash(path)


def ensure_global_philosophy(
    planspace: Path,
    codespace: Path,
    parent: str,
) -> Path | None:
    """Ensure the operational philosophy exists; distill if missing."""
    policy = read_model_policy(planspace)
    paths = PathRegistry(planspace)
    artifacts = paths.artifacts
    intent_global = paths.intent_global_dir()
    intent_global.mkdir(parents=True, exist_ok=True)
    philosophy_path = intent_global / "philosophy.md"

    if philosophy_path.exists() and philosophy_path.stat().st_size > 0:
        source_map_path = intent_global / "philosophy-source-map.json"
        if not source_map_path.exists():
            log("Intent bootstrap: philosophy exists but source-map "
                "missing — regenerating (fail-closed)")
        else:
            manifest_path = intent_global / "philosophy-source-manifest.json"
            if manifest_path.exists():
                manifest = read_json(manifest_path)
                if isinstance(manifest, dict):
                    sources_changed = False
                    for entry in manifest.get("sources", []):
                        src = Path(entry.get("path", ""))
                        if not src.exists():
                            sources_changed = True
                            break
                        if sha256_file(src) != entry.get("hash", ""):
                            sources_changed = True
                            break

                    catalog_fp_path = (
                        intent_global / "philosophy-catalog-fingerprint.txt"
                    )
                    catalog_changed = False
                    if catalog_fp_path.exists():
                        prev_fp = catalog_fp_path.read_text(
                            encoding="utf-8",
                        ).strip()
                        current_catalog = build_philosophy_catalog(
                            planspace, codespace,
                        )
                        current_fp = content_hash(
                            json.dumps(current_catalog, sort_keys=True),
                        )
                        if prev_fp != current_fp:
                            catalog_changed = True
                            log("Intent bootstrap: philosophy candidate "
                                "catalog changed — rerunning selector")

                    if sources_changed:
                        log("Intent bootstrap: philosophy sources "
                            "changed — regenerating")
                    elif not catalog_changed:
                        return philosophy_path
                else:
                    log("Intent bootstrap: source manifest malformed — "
                        "regenerating philosophy")
            else:
                return philosophy_path

    catalog = build_philosophy_catalog(planspace, codespace)
    if not catalog:
        log("Intent bootstrap: no markdown files found for philosophy "
            "catalog — blocking section (fail-closed)")
        signal_dir = artifacts / "signals"
        signal_dir.mkdir(parents=True, exist_ok=True)
        write_json(signal_dir / "philosophy-source-missing.json", {
            "state": "philosophy_source_missing",
            "detail": (
                "No markdown files found in planspace or codespace. "
                "Section execution will be blocked until philosophy "
                "is available."
            ),
        })
        return None

    catalog_path = artifacts / "philosophy-candidate-catalog.json"
    write_json(catalog_path, catalog)

    selector_prompt = artifacts / "philosophy-select-prompt.md"
    selector_output = artifacts / "philosophy-select-output.md"
    selected_signal = artifacts / "signals" / "philosophy-selected-sources.json"
    selected_signal.parent.mkdir(parents=True, exist_ok=True)

    selector_prompt.write_text(f"""# Task: Select Philosophy Source Files

## Context
Select which files from the candidate catalog contain execution
philosophy, design constraints, or operational principles that should
be distilled into the project's operational philosophy.

## Input
Read the candidate catalog at: `{catalog_path}`

Each entry has a path, size, and first 10 lines as a preview.

## Selection Criteria
- Files that describe HOW to build (design principles, constraints,
  operational rules) — not WHAT to build (requirements, specs)
- Files that contain explicit principles, constraints, or philosophy
- Prefer fewer, higher-quality sources over many marginal ones
- Select 1-10 files maximum

## Output
Write a JSON signal to: `{selected_signal}`

```json
{{
  "sources": [
    {{"path": "...", "reason": "Contains design constraints"}}
  ],
  "ambiguous": [
    {{"path": "...", "reason": "Preview inconclusive — title suggests principles"}}
  ],
  "additional_extensions": [".txt", ".rst"]
}}
```

The ``ambiguous`` field is **optional**. Include it only when the
10-line preview is genuinely insufficient to classify a candidate.
Up to 5 ambiguous candidates will be sent for full-read verification
by a stronger model. Do not nominate files you can classify from the
preview alone.

The ``additional_extensions`` field is **optional**. Include it only
if you believe philosophy sources may exist in non-markdown formats
that were not included in the catalog. The catalog will be rebuilt
with these extensions and you will be re-invoked once.

If NO files contain philosophy or constraints, write:
```json
{{"sources": []}}
```
""", encoding="utf-8")
    _log_artifact(planspace, "prompt:philosophy-select")

    dispatch_agent(
        policy.get("intent_philosophy_selector", "glm"),
        selector_prompt,
        selector_output,
        planspace,
        parent,
        codespace=codespace,
        agent_file="philosophy-source-selector.md",
    )

    selected = read_agent_signal(selected_signal)

    ambiguous_cap = 5
    if (selected and isinstance(selected.get("ambiguous"), list)
            and selected["ambiguous"]):
        ambiguous = selected["ambiguous"][:ambiguous_cap]
        verifiable = [
            a for a in ambiguous
            if isinstance(a, dict) and Path(a.get("path", "")).exists()
        ]
        if verifiable:
            log(f"Intent bootstrap: verifying {len(verifiable)} "
                "ambiguous philosophy candidate(s) (full-read)")
            verify_prompt = artifacts / "philosophy-verify-prompt.md"
            verify_output = artifacts / "philosophy-verify-output.md"
            verify_signal = (
                artifacts / "signals" / "philosophy-verified-sources.json"
            )
            verify_signal.parent.mkdir(parents=True, exist_ok=True)

            candidates_block = "\n".join(
                f"- `{a['path']}` — {a.get('reason', 'ambiguous')}"
                for a in verifiable
            )
            verify_prompt_text = f"""# Task: Verify Ambiguous Philosophy Candidates

## Context
The source selector could not classify these files from a 10-line
preview. Read each file in full and decide whether it contains
execution philosophy, design constraints, or operational principles.

## Candidates
{candidates_block}

## Instructions
For each candidate, read the FULL file and classify:
- **philosophy_source**: Contains principles, constraints, design rules → include
- **not_philosophy**: Specification, requirements, or irrelevant → exclude

## Output
Write a JSON signal to: `{verify_signal}`

```json
{{{{
  "verified_sources": [
    {{{{"path": "...", "reason": "Contains design constraints at ..."}}}}
  ],
  "rejected": [
    {{{{"path": "...", "reason": "Specification file, not philosophy"}}}}
  ]
}}}}
```
"""
            if not write_validated_prompt(verify_prompt_text, verify_prompt):
                return selected
            _log_artifact(planspace, "prompt:philosophy-verify")

            dispatch_agent(
                policy.get("intent_philosophy_verifier", "claude-opus"),
                verify_prompt,
                verify_output,
                planspace,
                parent,
                codespace=codespace,
                agent_file="philosophy-source-verifier.md",
            )

            verified = read_agent_signal(verify_signal)
            if verified and isinstance(verified.get("verified_sources"), list):
                existing_paths = {
                    s["path"] for s in selected.get("sources", [])
                }
                for verified_source in verified["verified_sources"]:
                    if (isinstance(verified_source, dict)
                            and verified_source.get("path") not in existing_paths):
                        selected.setdefault("sources", []).append(verified_source)
                        existing_paths.add(verified_source["path"])
                log(f"Intent bootstrap: verified "
                    f"{len(verified['verified_sources'])} source(s) "
                    "from ambiguous candidates")

    expansion_cap = 5
    if (selected and isinstance(selected.get("additional_extensions"), list)
            and selected["additional_extensions"]):
        raw_exts = selected["additional_extensions"][:expansion_cap]
        extra = frozenset(
            e for e in raw_exts
            if isinstance(e, str) and e.startswith(".")
            and len(e) <= 6 and "/" not in e and "\\" not in e
        )
        if extra:
            expanded_exts = frozenset({".md"}) | extra
            log(f"Intent bootstrap: selector requested extensions "
                f"{sorted(extra)} — rebuilding catalog (one-shot)")
            catalog = build_philosophy_catalog(
                planspace,
                codespace,
                extensions=expanded_exts,
            )
            write_json(catalog_path, catalog)

            selector_output2 = artifacts / "philosophy-select-output-2.md"
            dispatch_agent(
                policy.get("intent_philosophy_selector", "glm"),
                selector_prompt,
                selector_output2,
                planspace,
                parent,
                codespace=codespace,
                agent_file="philosophy-source-selector.md",
            )
            expanded = read_agent_signal(selected_signal)
            if expanded and expanded.get("sources"):
                selected = expanded

    if not selected or not selected.get("sources"):
        log("Intent bootstrap: source selector found no philosophy "
            "files — blocking section (fail-closed)")
        signal_dir = artifacts / "signals"
        signal_dir.mkdir(parents=True, exist_ok=True)
        write_json(signal_dir / "philosophy-source-missing.json", {
            "state": "philosophy_source_missing",
            "detail": (
                "Source selector found no philosophy files in the "
                "candidate catalog. Section execution will be blocked "
                "until philosophy is available."
            ),
        })
        return None

    sources = [
        Path(source["path"]) for source in selected["sources"]
        if Path(source["path"]).exists()
    ]
    if not sources:
        log("Intent bootstrap: selected source paths do not exist — "
            "skipping distillation (fail-closed)")
        return None

    log(f"Intent bootstrap: distilling operational philosophy from "
        f"{len(sources)} agent-selected source(s)")

    prompt_path = artifacts / "philosophy-distill-prompt.md"
    output_path = artifacts / "philosophy-distill-output.md"
    source_map_path = intent_global / "philosophy-source-map.json"

    sources_block = "\n".join(f"- `{source}`" for source in sources)
    distill_prompt_text = f"""# Task: Distill Operational Philosophy

## Context
Convert the execution philosophy into an operational philosophy document
that alignment agents can use for per-section philosophy checks.

## Input
Read these philosophy source files:
{sources_block}

If a philosophy artifact already exists at `{philosophy_path}`, skip this task.

## Output
Write an operational philosophy to: `{philosophy_path}`

Structure:
1. Numbered principles (P1, P2, ...) — short, actionable
2. Interactions between principles (which ones tension with each other)
3. Expansion guidance (how new principles get added)

Write a source map to: `{source_map_path}`
Format: JSON mapping principle ID to source file/section.

## Rules
- Keep principles short and operational (1-2 sentences each)
- Number them P1..PN for machine-stable references
- Note known tensions between principles explicitly
- Include expansion guidance: what classifies as absorbable vs tension vs contradiction
- Do NOT invent principles — every principle must trace to one of the source files
"""
    if not write_validated_prompt(distill_prompt_text, prompt_path):
        return None
    _log_artifact(planspace, "prompt:philosophy-distill")

    result = dispatch_agent(
        policy.get("intent_philosophy", "claude-opus"),
        prompt_path,
        output_path,
        planspace,
        parent,
        codespace=codespace,
        agent_file="philosophy-distiller.md",
    )

    if result == "ALIGNMENT_CHANGED_PENDING":
        return philosophy_path

    if not philosophy_path.exists() or philosophy_path.stat().st_size == 0:
        log("Intent bootstrap: philosophy distillation failed — "
            "no output (fail-closed, blocking section)")
        signal_dir = artifacts / "signals"
        signal_dir.mkdir(parents=True, exist_ok=True)
        write_json(signal_dir / "philosophy-distillation-failed.json", {
            "state": "philosophy_distillation_failed",
            "detail": (
                "Philosophy distiller did not produce output despite "
                "source files being available. Section execution will "
                "be blocked until philosophy is available."
            ),
            "sources": [str(source) for source in sources],
        })
        return None

    grounding_ok = validate_philosophy_grounding(
        philosophy_path,
        source_map_path,
        artifacts,
    )
    if not grounding_ok:
        log("Intent bootstrap: philosophy grounding validation failed "
            "— blocking section (fail-closed)")
        return None

    manifest_path = intent_global / "philosophy-source-manifest.json"
    write_json(manifest_path, {
        "sources": [
            {"path": str(source), "hash": sha256_file(source)}
            for source in sources
        ],
    })

    catalog_fp_path = intent_global / "philosophy-catalog-fingerprint.txt"
    catalog_fp = content_hash(json.dumps(catalog, sort_keys=True))
    catalog_fp_path.write_text(catalog_fp, encoding="utf-8")

    return philosophy_path
