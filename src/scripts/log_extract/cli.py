"""CLI argument parsing and orchestration for logex."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from log_extract import formatters, timeline
from log_extract.correlator import correlate
from log_extract.extractors import artifacts, claude, codex, gemini, opencode, run_db
from log_extract.utils import load_model_backend_map, parse_timestamp


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="logex",
        description="Extract and flatten agent orchestration run logs.",
    )
    p.add_argument("planspace", type=Path, help="Path to the planspace directory containing run.db")
    p.add_argument("--after", help="Only events after this ISO timestamp")
    p.add_argument("--before", help="Only events before this ISO timestamp")
    p.add_argument("--source", action="append", help="Filter by source (repeatable or comma-separated)")
    p.add_argument("--agent", action="append", help="Filter by agent name (repeatable)")
    p.add_argument("--section", action="append", help="Filter by section number (repeatable)")
    p.add_argument("--kind", action="append", help="Filter by event kind (repeatable or comma-separated)")
    p.add_argument("--grep", help="Regex search on detail/raw fields")
    p.add_argument("--format", choices=["jsonl", "text", "csv"], default="jsonl", dest="fmt")
    p.add_argument("--no-color", action="store_true", help="Disable ANSI colors in text format")
    p.add_argument("--claude-home", action="append", type=Path, help="Claude Code home (repeatable)")
    p.add_argument("--codex-home", action="append", type=Path, help="Codex home (repeatable)")
    p.add_argument("--opencode-home", action="append", type=Path, help="OpenCode home (repeatable)")
    p.add_argument("--gemini-home", action="append", type=Path, help="Gemini CLI home (repeatable)")
    return p


def _expand_csv(values: list[str] | None) -> set[str] | None:
    if not values:
        return None
    result: set[str] = set()
    for v in values:
        for part in v.split(","):
            part = part.strip()
            if part:
                result.add(part)
    return result or None


def _expand_list(values: list[str] | None) -> set[str] | None:
    if not values:
        return None
    return set(values)


def run(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    planspace: Path = args.planspace
    if not planspace.is_dir():
        parser.error(f"planspace directory does not exist: {planspace}")

    # Resolve default homes
    claude_homes = args.claude_home or [Path.home() / ".claude"]
    codex_homes = args.codex_home or [Path.home() / ".codex2"]
    opencode_homes = args.opencode_home or [Path.home() / ".local" / "share" / "opencode"]
    gemini_homes = args.gemini_home or [Path.home() / ".gemini"]

    # Model-backend map
    model_map = load_model_backend_map(planspace)

    # Parse filter timestamps
    after_ms = None
    before_ms = None
    if args.after:
        _, after_ms = parse_timestamp(args.after)
    if args.before:
        _, before_ms = parse_timestamp(args.before)

    sources_filter = _expand_csv(args.source)
    kinds_filter = _expand_csv(args.kind)
    agents_filter = _expand_list(args.agent)
    sections_filter = _expand_list(args.section)

    # Extract
    db_path = planspace / "run.db"
    db_events = list(run_db.iter_events(db_path, model_map)) if db_path.is_file() else []
    dispatch_cands = list(run_db.iter_dispatch_candidates(db_path, model_map)) if db_path.is_file() else []

    artifacts_dir = planspace / "artifacts"
    artifact_events = list(artifacts.iter_events(artifacts_dir)) if artifacts_dir.is_dir() else []

    claude_events = list(claude.iter_events(claude_homes))
    claude_sessions = list(claude.iter_session_candidates(claude_homes))

    codex_events = list(codex.iter_events(codex_homes))
    codex_sessions = list(codex.iter_session_candidates(codex_homes))

    opencode_events = list(opencode.iter_events(opencode_homes))
    opencode_sessions = list(opencode.iter_session_candidates(opencode_homes))

    gemini_events = list(gemini.iter_events(gemini_homes))
    gemini_sessions = list(gemini.iter_session_candidates(gemini_homes))

    # Correlate
    all_sessions = claude_sessions + codex_sessions + opencode_sessions + gemini_sessions
    links = correlate(dispatch_cands, all_sessions)

    # Merge
    all_event_streams = [
        db_events,
        artifact_events,
        claude_events,
        codex_events,
        opencode_events,
        gemini_events,
    ]
    merged = timeline.merge_and_sort(all_event_streams)

    # Decorate
    timeline.decorate(merged, links, dispatch_cands)

    # Dedup
    merged = timeline.dedup(merged)

    # Filter
    merged = timeline.apply_filters(
        merged,
        after_ms=after_ms,
        before_ms=before_ms,
        sources=sources_filter,
        agents=agents_filter,
        sections=sections_filter,
        kinds=kinds_filter,
        grep=args.grep,
    )

    # Format and output
    if args.fmt == "jsonl":
        lines = formatters.format_jsonl(merged)
    elif args.fmt == "csv":
        lines = formatters.format_csv(merged)
    else:
        lines = formatters.format_text(merged, use_color=not args.no_color)

    for line in lines:
        try:
            print(line)
        except BrokenPipeError:
            break
