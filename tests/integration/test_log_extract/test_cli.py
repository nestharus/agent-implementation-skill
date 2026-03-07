"""Tests for CLI argument parsing — Packet 10."""

import json

from log_extract.cli import build_parser


class TestArgParsing:
    def test_planspace_required(self):
        p = build_parser()
        # Should fail without planspace
        try:
            p.parse_args([])
            assert False, "Should have raised"
        except SystemExit:
            pass

    def test_defaults(self):
        p = build_parser()
        args = p.parse_args(["/tmp/test-planspace"])
        assert str(args.planspace) == "/tmp/test-planspace"
        assert args.fmt == "jsonl"
        assert args.source is None
        assert args.agent is None
        assert args.section is None
        assert args.kind is None
        assert args.grep is None
        assert args.no_color is False

    def test_format_option(self):
        p = build_parser()
        args = p.parse_args(["/tmp/ps", "--format", "text"])
        assert args.fmt == "text"

    def test_repeated_source(self):
        p = build_parser()
        args = p.parse_args(["/tmp/ps", "--source", "claude", "--source", "codex"])
        assert args.source == ["claude", "codex"]

    def test_repeated_homes(self):
        p = build_parser()
        args = p.parse_args([
            "/tmp/ps",
            "--claude-home", "/a",
            "--claude-home", "/b",
        ])
        assert len(args.claude_home) == 2

    def test_grep_option(self):
        p = build_parser()
        args = p.parse_args(["/tmp/ps", "--grep", "bug.*fix"])
        assert args.grep == "bug.*fix"
