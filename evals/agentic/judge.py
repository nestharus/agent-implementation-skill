"""LLM judge integration for agentic workflow evals."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .collectors import CollectedOutput
from .scenario_loader import ScenarioSpec
from .structural_checks import CheckResult


@dataclass
class JudgeInput:
    scenario_id: str
    scenario_name: str
    system_under_test: str
    expected_behavior: str
    answer_key: list[dict]
    actual_outputs: dict


@dataclass
class AssertionResult:
    id: str
    verdict: str
    evidence: list[str]
    reason: str


@dataclass
class JudgeOutput:
    overall_verdict: str
    assertions: list[AssertionResult]
    summary: str
    critical_failures: list[str]
    raw_output: str


def build_judge_input(
    spec: ScenarioSpec,
    collected: CollectedOutput,
    structural_results: list[CheckResult],
) -> JudgeInput:
    """Build a bounded judge input from scenario spec and collected outputs."""
    answer_key: list[dict] = []
    for item in spec.checks.semantic:
        answer_key.append({"id": item.id, "type": "semantic", "assertion": item.assertion})
    for item in spec.checks.absence:
        answer_key.append(
            {
                "id": item.id,
                "type": "absence",
                "path_glob": item.path_glob,
                "should_exist": item.should_exist,
                "assertion": f"Forbidden outputs matching {item.path_glob} must not appear.",
            }
        )
    for item in spec.checks.signals:
        answer_key.append(
            {
                "id": item.id,
                "type": "signal",
                "path": item.path,
                "expected_state": item.expected_state,
                "required_fields": item.required_fields,
                "assertion": f"Signal {item.path} must have state {item.expected_state}.",
            }
        )

    actual_outputs = {
        "files": collected.files,
        "files_json": collected.files_json,
        "db": collected.db,
        "existence_map": collected.existence_map,
        "parseability_map": collected.parseability_map,
        "structural_results": [asdict(result) for result in structural_results],
    }
    expected_behavior = "; ".join(entry["assertion"] for entry in answer_key) or "Structural-only scenario."
    return JudgeInput(
        scenario_id=spec.id,
        scenario_name=spec.name,
        system_under_test=spec.system,
        expected_behavior=expected_behavior,
        answer_key=answer_key,
        actual_outputs=actual_outputs,
    )


def _load_prompt_safety(project_root: Path):
    scripts_dir = project_root / "src" / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    from prompt_safety import write_validated_prompt

    return write_validated_prompt


def build_judge_prompt(judge_input: JudgeInput, run_dir: Path) -> Path:
    """Build the judge prompt file and write it to ``run_dir``."""
    project_root = Path(__file__).resolve().parents[2]
    write_validated_prompt = _load_prompt_safety(project_root)

    scenario_description = json.dumps(
        {
            "scenario_id": judge_input.scenario_id,
            "scenario_name": judge_input.scenario_name,
            "system_under_test": judge_input.system_under_test,
            "expected_behavior": judge_input.expected_behavior,
        },
        indent=2,
    )
    answer_key_json = json.dumps(judge_input.answer_key, indent=2)
    actual_outputs_bundle = json.dumps(judge_input.actual_outputs, indent=2, default=str)
    prompt = (
        "# Task: Agentic Eval Judgment\n\n"
        "You are judging one bounded workflow eval.\n\n"
        "## Rules\n"
        "- Use only the provided scenario description, answer key, and actual outputs.\n"
        "- Do not give credit for plausible intent unless the evidence is present.\n"
        "- Treat missing evidence as FAIL.\n"
        "- Focus on behavioral contract, not prose style.\n"
        "- Ignore wording differences if the output is structurally and directionally correct.\n"
        "- For absence checks, FAIL if the forbidden behavior appears anywhere in the provided outputs.\n"
        "- For signal checks, require both the signal artifact and the expected metadata.\n"
        "- Be conservative: near misses are FAIL.\n\n"
        "## Scenario\n"
        f"{scenario_description}\n\n"
        "## Answer Key\n"
        f"{answer_key_json}\n\n"
        "## Actual Outputs\n"
        f"{actual_outputs_bundle}\n\n"
        "## Required Output\n"
        "Return EXACTLY one JSON object:\n"
        "{\n"
        '  "overall_verdict": "PASS | FAIL",\n'
        '  "assertions": [...],\n'
        '  "summary": "...",\n'
        '  "critical_failures": []\n'
        "}\n"
    )
    prompt_path = run_dir / "judge-prompt.md"
    ok = write_validated_prompt(prompt, prompt_path)
    if not ok:
        raise ValueError(f"Prompt safety blocked judge prompt at {prompt_path}")
    return prompt_path


def _strip_code_fences(text: str) -> str:
    """Remove markdown code fences wrapping JSON."""
    lines = text.strip().splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines)


def _extract_json_object(raw_output: str) -> dict[str, Any]:
    stripped = _strip_code_fences(raw_output.strip())
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("No JSON object found in judge output")
    candidate = stripped[start:end + 1]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass

    # LLMs sometimes produce unescaped quotes inside string values.
    # Attempt a lenient fix: replace curly quotes or try re-parsing
    # after fixing the most common pattern (unescaped " inside values).
    import re
    fixed = re.sub(
        r'(?<=: )"([^"]*)"([^",\]\}\n]+)"',
        lambda m: '"' + m.group(1) + m.group(2) + '"',
        candidate,
    )
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass

    raise ValueError(f"Could not parse JSON from judge output")


def _coerce_judge_output(data: dict[str, Any], raw_output: str) -> JudgeOutput:
    assertions = [
        AssertionResult(
            id=str(item.get("id", "")),
            verdict=str(item.get("verdict", "FAIL")),
            evidence=[str(entry) for entry in item.get("evidence", [])],
            reason=str(item.get("reason", "")),
        )
        for item in data.get("assertions", [])
    ]
    overall = str(data.get("overall_verdict", "FAIL"))
    if any(item.verdict != "PASS" for item in assertions):
        overall = "FAIL"
    return JudgeOutput(
        overall_verdict=overall,
        assertions=assertions,
        summary=str(data.get("summary", "")),
        critical_failures=[str(item) for item in data.get("critical_failures", [])],
        raw_output=raw_output,
    )


def invoke_judge(prompt_path: Path, project_root: Path) -> JudgeOutput:
    """Run the judge via the ``agents`` binary and parse the JSON output."""
    import os
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    result = subprocess.run(
        [
            "agents",
            "--model",
            "glm",
            "--file",
            str(prompt_path),
            "--agent-file",
            str(project_root / "agents" / "eval-judge.md"),
        ],
        capture_output=True,
        text=True,
        cwd=str(project_root),
        env=env,
    )
    raw_output = result.stdout.strip()
    if result.stderr.strip():
        raw_output = f"{raw_output}\n{result.stderr.strip()}".strip()
    if result.returncode != 0:
        return JudgeOutput(
            overall_verdict="FAIL",
            assertions=[],
            summary="Judge invocation failed.",
            critical_failures=["judge_invocation_failed"],
            raw_output=raw_output,
        )
    try:
        payload = _extract_json_object(raw_output)
    except (ValueError, json.JSONDecodeError) as exc:
        return JudgeOutput(
            overall_verdict="FAIL",
            assertions=[],
            summary=f"Judge output was not parseable JSON: {exc}",
            critical_failures=["judge_parse_failed"],
            raw_output=raw_output,
        )
    return _coerce_judge_output(payload, raw_output)
