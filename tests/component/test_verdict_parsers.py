"""Component tests for VerdictParsers: parse JSON verdicts from LLM output."""

from __future__ import annotations

import json

from src.proposal.verdict_parsers import parse_alignment_verdict


# --- parse single-line JSON with frame_ok ---


def test_parse_single_line_json_with_frame_ok():
    """Single-line JSON containing frame_ok is parsed correctly."""
    output = '{"frame_ok": true, "aligned": true, "problems": []}'

    result = parse_alignment_verdict(output)

    assert result is not None
    assert result["frame_ok"] is True
    assert result["aligned"] is True
    assert result["problems"] == []


# --- parse code-fenced JSON with frame_ok ---


def test_parse_code_fenced_json_with_frame_ok():
    """JSON inside triple-backtick fences is parsed correctly."""
    output = (
        "Analysis complete.\n"
        "```json\n"
        '{"frame_ok": true, "aligned": false, '
        '"problems": ["Missing error handling"]}\n'
        "```\n"
        "End of analysis."
    )

    result = parse_alignment_verdict(output)

    assert result is not None
    assert result["frame_ok"] is True
    assert result["aligned"] is False
    assert result["problems"] == ["Missing error handling"]


# --- return None for output with no JSON ---


def test_return_none_for_no_json():
    """Plain text with no JSON returns None."""
    output = "Some general text without any JSON verdict."

    result = parse_alignment_verdict(output)

    assert result is None


# --- return None for JSON without frame_ok key ---


def test_return_none_for_json_without_frame_ok():
    """JSON that lacks the frame_ok key is ignored."""
    output = '{"aligned": true, "problems": []}'

    result = parse_alignment_verdict(output)

    assert result is None


# --- parse verdict with aligned=true ---


def test_parse_verdict_aligned_true():
    """Verdict with aligned=true is returned with all fields."""
    output = json.dumps({
        "frame_ok": True,
        "aligned": True,
        "problems": [],
    })

    result = parse_alignment_verdict(output)

    assert result is not None
    assert result["aligned"] is True
    assert result["problems"] == []


# --- parse verdict with aligned=false and problems list ---


def test_parse_verdict_aligned_false_with_problems():
    """Verdict with aligned=false includes the problems list."""
    output = json.dumps({
        "frame_ok": True,
        "aligned": False,
        "problems": ["auth bypass", "missing validation"],
    })

    result = parse_alignment_verdict(output)

    assert result is not None
    assert result["aligned"] is False
    assert result["problems"] == ["auth bypass", "missing validation"]


# --- handle multiple JSON blocks (first valid one wins) ---


def test_multiple_json_blocks_first_valid_wins():
    """When multiple JSON blocks appear, the first with frame_ok wins."""
    output = (
        '{"unrelated": "data"}\n'
        '{"frame_ok": true, "aligned": true, "problems": []}\n'
        '{"frame_ok": false, "aligned": false, "problems": ["late"]}\n'
    )

    result = parse_alignment_verdict(output)

    assert result is not None
    # The first JSON with frame_ok is the second line (aligned=true)
    assert result["aligned"] is True


# --- handle empty string input ---


def test_handle_empty_string_input():
    """Empty string returns None without error."""
    result = parse_alignment_verdict("")

    assert result is None


# --- additional edge cases ---


def test_json_embedded_in_prose():
    """JSON on a line surrounded by prose is still found."""
    output = (
        "After careful review, the alignment verdict is:\n"
        '{"frame_ok": true, "aligned": true, "problems": []}\n'
        "No further action needed."
    )

    result = parse_alignment_verdict(output)

    assert result is not None
    assert result["aligned"] is True


def test_frame_ok_false():
    """frame_ok=false is still a valid verdict (structural failure)."""
    output = json.dumps({
        "frame_ok": False,
        "aligned": False,
        "problems": ["Invalid frame: feature coverage audit"],
    })

    result = parse_alignment_verdict(output)

    assert result is not None
    assert result["frame_ok"] is False


def test_code_fence_without_frame_ok_ignored():
    """Code-fenced JSON without frame_ok is skipped."""
    output = (
        "```json\n"
        '{"status": "ok", "count": 5}\n'
        "```\n"
    )

    result = parse_alignment_verdict(output)

    assert result is None
