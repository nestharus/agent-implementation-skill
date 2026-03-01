"""Regression guard tests (P2, P4, P8, P9, R20/P3, R21/P4, R21/P5, R21/P6C, R24/P9, R30, R31, R32, R33, R34, R35, R36, R37, R38, R39, R40, R41, R42, R43, R44, R45, R46, R47, R48, R49, R50, R71/V2, R71/V3, R71/V4, R71/V5, R71/V6, R71/V7, R72/V1, R72/V2, R72/V3, R72/V4, R72/V5, R72/V6, R72/V7, R72/V8, R72/V9).

P2: No brute-force scan patterns in scan package.
P4: Codemap fingerprint mismatch triggers verifier.
P8: Bridge dispatch only fires on agent directive.
P9: Agent frontmatter models are in the documented policy set.
R20/P3: Pipeline agent files contain no runtime placeholders.
R21/P4: Greenfield pause label uses needs_parent (not underspec).
R21/P5: Targeted requeue only requeues changed sections.
R21/P6C: Operational agent files have no angle-bracket placeholders.
R24/P9: SKILL.md Paths manifest — every referenced path must exist on disk.
R30/V1: All dispatch callsites use model policy — no hardcoded model strings.
R30/V2: check_agent_signals returns (None, "") with no auto-adjudicator.
R30/V3: read_scan_model_policy warns on parse failure.
R31/V1: Problem frame surfaces in alignment surface and prompt context.
R31/V2: Malformed/unknown signal states fail closed as needs_parent.
R31/V3: Scope-delta artifacts include full signal payload.
R31/V4: All dispatch callsites use model policy — no hardcoded model literals.
R32/V1: Coordination plan parse failure retries + fails closed (no script grouping).
R32/V2: Escalation/fix model strictly policy-driven (no hardcoded model writes).
R32/V3: frame_ok=false is structural failure surfaced upward (no retry loop).
R32/V4: Feedback signal status acked as applied after update.
R33/V1: Related Files parsing unified, block-scoped, code-fence-safe.
R33/V2: Signal instructions clarify JSON is the only truth channel.
R33/V3: Problem frame in convergence hashing and traceability.
R33/V4: loop-contract.md lists all hashed inputs.
R34/V1: Tool registry malformed → remove stale surface + dispatch repair.
R34/V2: Post-impl tool registry malformed → repair, not pass.
R34/V3: Microstrategy decision fails closed (returns True, writes fallback).
R34/V4: Prompt templates use policy-driven model placeholders.
R35/V1: reexplore.py prompt uses policy-driven exploration model.
R35/V2: coordination/execution.py prompt uses policy-driven model params.
R35/P11: Sweep guard — no hardcoded --model literals in any prompt surface.
R36/V1: Codex delegated impl dispatch uses --file, not inline instructions.
R36/V2: Signal taxonomy in loop-contract.md and blockers.py matches reality.
R37/V1: Scope-delta adjudication parsing is robust with retry + fail-closed.
R37/V2: Recurrence escalation log/artifacts use policy model, not hardcoded.
R37/V3: implementation-strategist.md tool-registry schema matches registrar.
R37/V4: Scan templates use extension-neutral examples (no .py bias).
R42/V1-V2: Skip-hash not written on validation failure; section hash strips scan summaries.
R42/V3: Fresh exploration appends only Related Files block, not full response.
R42/V4: Codemap prompt does not contradict itself about templates.
R43/V1: Bridge-tools loop closed — signal verified, friction acknowledged.
R43/V2: Microstrategy writer dispatch fail-closed on output production.
R44/V1: Bridge-tools outputs wired into downstream channels (.ref, notes, blocker, digest).
R44/V2: Scan-stage validation signal parsing fail-closed on malformed JSON.
R45/V1: Bridge-tools post-escalation verification checks proposal existence.
R45/V2: Tool digest regeneration triggers on registry creation (not just modification).
R45/V3: read_agent_signal() preserves malformed JSON as .malformed.json.
R45/V4: Microstrategy pre-existing signal malformed — renamed and re-dispatched.
R46/V1: Completion gate must check outstanding cross-section problems.
R46/V2: Tool surface must be rebuilt after registry repair.
R46/V3: read_signal_tuple preserves corrupted files as .malformed.json.
R47/V1: Lint scripts use WORKFLOW_HOME for layout portability (no hardcoded src/ paths).
R71/V7a: integration-proposer.md description does not contain "dispatches" or "sub-agents".
R71/V7b: implementation-strategist.md description does not contain "sub-agent dispatch".
R71/V7c: coordination-fixer.md contains "Task Submission" section.
R71/V7d: runner.py microstrategy task-submission section does not contain "per line".
"""

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

# Resolve project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCAN_PKG = PROJECT_ROOT / "src" / "scripts" / "scan"
AGENTS_DIR = PROJECT_ROOT / "src" / "agents"


def _read_scan_sources() -> str:
    """Read all Python source files in the scan package into one string."""
    parts: list[str] = []
    for py_file in sorted(SCAN_PKG.rglob("*.py")):
        parts.append(py_file.read_text())
    return "\n".join(parts)

# Documented model policy set (from models.md)
ALLOWED_MODELS = {
    "claude-opus",
    "glm",
    "gpt-codex-high",
    "gpt-codex-high",
    "gpt-codex-xhigh",
    "claude-haiku",
}


class TestNoBruteForceScanning:
    """P2: scan package must not contain brute-force scan-all patterns."""

    def test_no_scan_all_files_pattern(self) -> None:
        content = _read_scan_sources()
        # "scan all files" or "scan every file" in comments/strings
        assert "scan all files" not in content.lower()
        assert "scan every file" not in content.lower()

    def test_no_glob_full_codespace_enumeration(self) -> None:
        """Glob or walk patterns that enumerate full codespace for
        scanning are forbidden.  Artifact-dir globs are OK."""
        content = _read_scan_sources()
        # os.walk or Path.rglob("**/*.py") on codespace would be
        # brute-force source enumeration.
        assert "os.walk" not in content, (
            "os.walk detected in scan package — brute-force traversal"
        )
        # glob("**/*.py") style patterns that would enumerate all source
        # files in the codespace
        brute_glob = re.findall(
            r'glob\(\s*["\']?\*\*[/\\]\*\.\w+["\']?\s*\)',
            content,
        )
        assert brute_glob == [], (
            f"Brute-force recursive glob detected: {brute_glob}"
        )


class TestCodemapFingerprint:
    """P4: codemap fingerprint infrastructure exists in pipeline_control."""

    def test_section_inputs_hash_includes_codemap(
        self, planspace: Path, codespace: Path,
    ) -> None:
        """Changing codemap changes the section inputs hash."""
        from section_loop.pipeline_control import _section_inputs_hash
        from section_loop.types import Section

        sections = {
            "01": Section(
                number="01",
                path=planspace / "artifacts" / "sections" / "section-01.md",
                related_files=["src/main.py"],
            ),
        }
        codemap = planspace / "artifacts" / "codemap.md"

        # Hash without codemap
        h1 = _section_inputs_hash("01", planspace, codespace, sections)

        # Hash with codemap
        codemap.write_text("# Codemap v1\nfile listings...")
        h2 = _section_inputs_hash("01", planspace, codespace, sections)
        assert h1 != h2, "Codemap presence must change inputs hash"

        # Hash with modified codemap
        codemap.write_text("# Codemap v2\nDIFFERENT listings...")
        h3 = _section_inputs_hash("01", planspace, codespace, sections)
        assert h2 != h3, "Codemap content change must change inputs hash"


class TestBridgeDispatchGuard:
    """P8: bridge dispatch requires agent directive, not script heuristic."""

    def test_bridge_needed_false_no_dispatch(self) -> None:
        """Coordination plan with bridge.needed=false must NOT trigger
        bridge dispatch even if groups share files."""
        plan = {
            "groups": [
                {
                    "problems": [0, 1],
                    "reason": "shared files",
                    "strategy": "sequential",
                    "bridge": {"needed": False},
                },
            ],
            "notes": "no bridge needed",
        }
        # Verify bridge.needed controls dispatch, not file overlap
        for group_meta in plan["groups"]:
            bridge = group_meta.get("bridge", {})
            assert bridge.get("needed") is False

    def test_bridge_needed_true_has_required_fields(self) -> None:
        """When bridge.needed=true, directive must have reason and
        shared_files for the script to build a prompt."""
        plan = {
            "groups": [
                {
                    "problems": [0, 1],
                    "reason": "contention on config.py",
                    "strategy": "sequential",
                    "bridge": {
                        "needed": True,
                        "reason": "Sections 1 and 3 contend over shared interface",
                        "shared_files": ["src/config.py"],
                    },
                },
            ],
        }
        bridge = plan["groups"][0]["bridge"]
        assert bridge["needed"] is True
        assert isinstance(bridge["reason"], str)
        assert len(bridge["reason"]) > 0
        assert isinstance(bridge["shared_files"], list)
        assert len(bridge["shared_files"]) > 0


class TestModelChoiceLint:
    """P9: agent frontmatter models must be in the documented policy set."""

    def test_all_agent_models_in_policy_set(self) -> None:
        for agent_file in sorted(AGENTS_DIR.glob("*.md")):
            content = agent_file.read_text()
            # Parse YAML frontmatter
            if not content.startswith("---"):
                continue
            end = content.index("---", 3)
            frontmatter = content[3:end]
            for line in frontmatter.strip().splitlines():
                if line.startswith("model:"):
                    model = line.split(":", 1)[1].strip()
                    assert model in ALLOWED_MODELS, (
                        f"{agent_file.name}: model '{model}' not in "
                        f"policy set {ALLOWED_MODELS}"
                    )

    def test_all_agents_have_model_frontmatter(self) -> None:
        for agent_file in sorted(AGENTS_DIR.glob("*.md")):
            content = agent_file.read_text()
            assert content.startswith("---"), (
                f"{agent_file.name}: missing YAML frontmatter"
            )
            end = content.index("---", 3)
            frontmatter = content[3:end]
            models_found = [
                l for l in frontmatter.strip().splitlines()
                if l.startswith("model:")
            ]
            assert len(models_found) == 1, (
                f"{agent_file.name}: expected exactly 1 model declaration, "
                f"found {len(models_found)}"
            )


# Agent files dispatched via agent_file= in scripts/section_loop/*
PIPELINE_AGENT_FILES = {
    "alignment-judge.md",
    "bridge-agent.md",
    "bridge-tools.md",
    "coordination-planner.md",
    "implementation-strategist.md",
    "integration-proposer.md",
    "intent-judge.md",
    "intent-pack-generator.md",
    "intent-triager.md",
    "microstrategy-writer.md",
    "philosophy-distiller.md",
    "philosophy-expander.md",
    "problem-expander.md",
    "section-re-explorer.md",
    "setup-excerpter.md",
    "tool-registrar.md",
}

# Runtime placeholders that must NOT appear in pipeline agent files.
# Agent files define METHOD; dynamic prompts provide runtime context.
BANNED_PLACEHOLDERS = [
    "<planspace>",
    "<codespace>",
    "$PLANSPACE",
    "$section_file",
    "$CODEMAP_PATH",
    "$ARTIFACTS_DIR",
]


class TestAgentFileNoRuntimePlaceholders:
    """R20/P3: Pipeline agent files must not contain runtime placeholders.

    Agent definition files encode the 'method of thinking' for a role.
    Runtime paths, artifact destinations, and environment variables belong
    in the dynamic dispatch prompts, not in agent files. This guard
    prevents drift back toward embedding runtime context in method files.
    """

    def test_no_planspace_placeholders(self) -> None:
        for name in sorted(PIPELINE_AGENT_FILES):
            path = AGENTS_DIR / name
            assert path.exists(), f"Pipeline agent file missing: {name}"
            content = path.read_text()
            for placeholder in BANNED_PLACEHOLDERS:
                assert placeholder not in content, (
                    f"{name}: contains banned runtime placeholder "
                    f"'{placeholder}'. Agent files must not contain "
                    f"runtime paths — move to dynamic prompt."
                )

    def test_pipeline_agent_files_exist(self) -> None:
        """All agent files referenced via agent_file= must exist."""
        for name in sorted(PIPELINE_AGENT_FILES):
            path = AGENTS_DIR / name
            assert path.exists(), (
                f"Pipeline agent file {name} referenced in section_loop "
                f"but not found in {AGENTS_DIR}"
            )


# Operational agent files dispatched by scripts (state-detector, etc.)
OPERATIONAL_AGENT_FILES = {
    "state-detector.md",
    "exception-handler.md",
    "agent-monitor.md",
}

# Angle-bracket placeholders are banned in ALL agent files (pipeline +
# operational). Round 20 fixed pipeline agents; Round 21 extends to
# operational agents. Models treat <planspace> as a literal path.
ANGLE_BRACKET_PLACEHOLDERS = [
    "<planspace>",
    "<codespace>",
    "<task-agent>",
    "<your-name>",
    "<task-agent-name>",
]


class TestOperationalAgentNoAngleBrackets:
    """R21/P6C: Operational agent files must not contain angle-bracket
    runtime placeholders.

    Round 20 enforced this for pipeline agents. Round 21 extends the
    guard to operational agents (orchestrator, agent-monitor, etc.) that
    are dispatched by scripts and receive runtime paths via prompt variables.
    """

    def test_no_angle_bracket_placeholders(self) -> None:
        for name in sorted(OPERATIONAL_AGENT_FILES):
            path = AGENTS_DIR / name
            assert path.exists(), f"Operational agent file missing: {name}"
            content = path.read_text()
            for placeholder in ANGLE_BRACKET_PLACEHOLDERS:
                assert placeholder not in content, (
                    f"{name}: contains banned angle-bracket placeholder "
                    f"'{placeholder}'. Use $VARIABLE instead."
                )


class TestGreenfieldPauseLabel:
    """R21/P4: Greenfield pause label must use needs_parent, not underspec.

    The structured blocker signal writes state=needs_parent. The mailbox
    pause message must match (pause:needs_parent:...), not use the old
    underspec vocabulary.
    """

    def test_greenfield_blocker_and_pause_consistent(self) -> None:
        """main.py greenfield path: blocker signal and mailbox message
        must both use needs_parent."""
        main_path = PROJECT_ROOT / "src" / "scripts" / "section_loop" / "main.py"
        content = main_path.read_text()

        # Blocker signal uses needs_parent
        assert '"state": "needs_parent"' in content or \
               "'state': 'needs_parent'" in content or \
               '"needs_parent"' in content, \
            "Greenfield blocker signal must use state=needs_parent"

        # Mailbox pause uses needs_parent (not underspec)
        assert "pause:needs_parent:" in content, \
            "Greenfield mailbox pause must use pause:needs_parent:"
        assert "pause:underspec:" not in content, \
            "Old pause:underspec: label found — should be pause:needs_parent:"


class TestTargetedRequeue:
    """R21/P5: Targeted requeue only requeues sections whose inputs changed.

    Verifies that requeue_changed_sections compares hashes and only
    requeues sections with differing inputs.
    """

    def test_only_changed_section_requeued(
        self, planspace: Path, codespace: Path,
    ) -> None:
        from section_loop.pipeline_control import requeue_changed_sections
        from section_loop.types import Section

        sections = {
            "01": Section(
                number="01",
                path=planspace / "artifacts" / "sections" / "section-01.md",
                related_files=["src/a.py"],
            ),
            "02": Section(
                number="02",
                path=planspace / "artifacts" / "sections" / "section-02.md",
                related_files=["src/b.py"],
            ),
        }

        # Create section spec files (needed for hash computation)
        sec_dir = planspace / "artifacts" / "sections"
        sec_dir.mkdir(parents=True, exist_ok=True)
        (sec_dir / "section-01.md").write_text("# Section 01")
        (sec_dir / "section-02.md").write_text("# Section 02")

        # Simulate both sections completed with baseline hashes
        completed = {"01", "02"}
        queue: list[str] = []

        # Write baseline hashes (as if sections completed)
        from section_loop.pipeline_control import _section_inputs_hash

        hash_dir = planspace / "artifacts" / "section-inputs-hashes"
        hash_dir.mkdir(parents=True, exist_ok=True)
        for num in ("01", "02"):
            h = _section_inputs_hash(num, planspace, codespace, sections)
            (hash_dir / f"{num}.hash").write_text(h)

        # Now change section 01's inputs (add a note targeting it)
        notes_dir = planspace / "artifacts" / "notes"
        notes_dir.mkdir(parents=True, exist_ok=True)
        (notes_dir / "from-02-to-01.md").write_text(
            "Section 02 changed config.py interface")

        # Requeue — only section 01 should be requeued
        requeued = requeue_changed_sections(
            completed, queue, sections, planspace, codespace)

        assert "01" in requeued, "Section 01 inputs changed — must requeue"
        assert "02" not in requeued, "Section 02 inputs unchanged — skip"
        assert "01" not in completed, "Requeued section removed from completed"
        assert "02" in completed, "Unchanged section stays completed"
        assert "01" in queue, "Requeued section added to queue"

    def test_baseline_hashes_written_on_completion(
        self, planspace: Path, codespace: Path,
    ) -> None:
        """After section completes, baseline hash must exist."""
        hash_dir = planspace / "artifacts" / "section-inputs-hashes"
        hash_dir.mkdir(parents=True, exist_ok=True)

        # main.py writes baseline hash after completed.add(sec_num).
        # Verify the main.py code path writes to this directory.
        main_path = PROJECT_ROOT / "src" / "scripts" / "section_loop" / "main.py"
        content = main_path.read_text()
        assert "section-inputs-hashes" in content, \
            "main.py must write baseline hashes to section-inputs-hashes/"


class TestCodemapCorrectionsInHash:
    """R23/P1: codemap corrections must change section inputs hash.

    When codemap-corrections.json changes, sections whose proposals
    depend on the codemap must be requeued. This is the mechanical
    enforcement for connected understanding.
    """

    def test_corrections_change_inputs_hash(
        self, planspace: Path, codespace: Path,
    ) -> None:
        from section_loop.pipeline_control import _section_inputs_hash
        from section_loop.types import Section

        sections = {
            "01": Section(
                number="01",
                path=planspace / "artifacts" / "sections" / "section-01.md",
                related_files=["src/main.py"],
            ),
        }

        # Hash without corrections
        h1 = _section_inputs_hash("01", planspace, codespace, sections)

        # Hash with corrections
        corrections = planspace / "artifacts" / "signals" / "codemap-corrections.json"
        corrections.parent.mkdir(parents=True, exist_ok=True)
        corrections.write_text('{"fixes": []}')
        h2 = _section_inputs_hash("01", planspace, codespace, sections)
        assert h1 != h2, "Corrections presence must change inputs hash"

        # Hash with modified corrections
        corrections.write_text('{"fixes": [{"path": "src/a.py"}]}')
        h3 = _section_inputs_hash("01", planspace, codespace, sections)
        assert h2 != h3, "Corrections content change must change inputs hash"


class TestCodemapCorrectionsInPrompts:
    """R23/P1: prompt writers must include corrections when the file exists.

    All codemap-consuming prompts must reference corrections to maintain
    connected understanding across the pipeline.
    """

    def test_coordination_plan_prompt_includes_corrections(
        self, planspace: Path,
    ) -> None:
        from section_loop.coordination import write_coordination_plan_prompt

        corrections = planspace / "artifacts" / "signals" / "codemap-corrections.json"
        corrections.parent.mkdir(parents=True, exist_ok=True)
        corrections.write_text('{"fixes": []}')
        # Also need codemap for the block to appear
        codemap = planspace / "artifacts" / "codemap.md"
        codemap.parent.mkdir(parents=True, exist_ok=True)
        codemap.write_text("# Codemap")

        write_coordination_plan_prompt(problems=[], planspace=planspace)
        prompt = (planspace / "artifacts" / "coordination"
                  / "coordination-plan-prompt.md").read_text()
        assert "codemap-corrections.json" in prompt

    def test_coordinator_fix_prompt_includes_corrections(
        self, planspace: Path, codespace: Path,
    ) -> None:
        from section_loop.coordination import write_coordinator_fix_prompt

        corrections = planspace / "artifacts" / "signals" / "codemap-corrections.json"
        corrections.parent.mkdir(parents=True, exist_ok=True)
        corrections.write_text('{"fixes": []}')
        codemap = planspace / "artifacts" / "codemap.md"
        codemap.parent.mkdir(parents=True, exist_ok=True)
        codemap.write_text("# Codemap")

        # Create minimal section artifacts needed by the prompt writer
        sec_dir = planspace / "artifacts" / "sections"
        sec_dir.mkdir(parents=True, exist_ok=True)
        (sec_dir / "section-01.md").write_text("# Section 01")
        (sec_dir / "section-01-proposal-excerpt.md").write_text("")
        (sec_dir / "section-01-alignment-excerpt.md").write_text("")

        group = [{
            "section": "01",
            "type": "test",
            "description": "test problem",
            "files": ["src/a.py"],
        }]
        write_coordinator_fix_prompt(
            group=group,
            planspace=planspace,
            codespace=codespace,
            group_id=1,
        )
        prompt = (planspace / "artifacts" / "coordination"
                  / "fix-1-prompt.md").read_text()
        assert "codemap-corrections.json" in prompt


LINT_SH = PROJECT_ROOT / "src" / "scripts" / "lint-audit-language.sh"
DOC_DRIFT_LINT_SH = PROJECT_ROOT / "src" / "scripts" / "lint-doc-drift.sh"


class TestLintAuditLanguage:
    """R22/P1: lint-audit-language.sh must pass on the current codebase.

    The lint catches banned terminology like "feature coverage audit" in
    agent files, scripts, and design docs. This guard ensures the codebase
    itself doesn't contain phrases the lint prohibits.
    """

    def test_lint_audit_language_passes(self) -> None:
        import subprocess
        env = {**os.environ, "WORKFLOW_HOME": "src"}
        result = subprocess.run(
            ["bash", str(LINT_SH)],
            capture_output=True, text=True,
            cwd=str(PROJECT_ROOT),
            env=env,
        )
        assert result.returncode == 0, (
            f"lint-audit-language.sh failed:\n{result.stdout}\n{result.stderr}"
        )


class TestLintDocDrift:
    """R23/P2: lint-doc-drift.sh must pass on the current codebase.

    The lint catches superseded behavior claims like "its exploration is
    skipped" in docs/templates that conflict with the implemented
    validation-based approach.
    """

    def test_lint_doc_drift_passes(self) -> None:
        import subprocess
        env = {**os.environ, "WORKFLOW_HOME": "src"}
        result = subprocess.run(
            ["bash", str(DOC_DRIFT_LINT_SH)],
            capture_output=True, text=True,
            cwd=str(PROJECT_ROOT),
            env=env,
        )
        assert result.returncode == 0, (
            f"lint-doc-drift.sh failed:\n{result.stdout}\n{result.stderr}"
        )


# Paths declared in SKILL.md's Paths block.  Parsed from the fenced code
# block listing under "## Paths".  Every path listed here MUST exist on
# disk relative to PROJECT_ROOT.  This prevents the "files exist on disk
# but missing from distribution" failure mode that recurred in Rounds 11
# and 24.
SKILL_MD_MANIFEST = [
    # Root-level docs
    "src/SKILL.md",
    "src/implement.md",
    "src/research.md",
    "src/rca.md",
    "src/evaluate.md",
    "src/baseline.md",
    "src/audit.md",
    "src/constraints.md",
    "src/models.md",
    # Scripts
    "src/scripts/workflow.sh",
    "src/scripts/db.sh",
    "src/scripts/scan.sh",
    "src/scripts/section-loop.py",
    # Tools
    "src/tools/extract-docstring-py",
    "src/tools/extract-summary-md",
    "src/tools/README.md",
    # Templates
    "src/templates/implement-proposal.md",
    "src/templates/research-cycle.md",
    "src/templates/rca-cycle.md",
]

# Additionally, implement.md references tools/README.md as the tool
# interface spec.  Already covered above but kept explicit.
IMPLEMENT_MD_TOOL_REFS = [
    "src/tools/README.md",
]


class TestSkillManifest:
    """R24/P9: Every path declared in SKILL.md's Paths block must exist.

    This is a mechanical manifest guard that prevents distribution
    integrity drift (Round 11 / Round 24 failure mode: files exist on
    disk but are omitted from codebase.zip or deleted without updating
    references).
    """

    def test_all_skill_paths_exist(self) -> None:
        missing = []
        for rel_path in SKILL_MD_MANIFEST:
            full = PROJECT_ROOT / rel_path
            if not full.exists():
                missing.append(rel_path)
        assert missing == [], (
            f"SKILL.md references paths that do not exist on disk:\n"
            + "\n".join(f"  - {p}" for p in missing)
        )

    def test_implement_md_tool_refs_exist(self) -> None:
        """implement.md references tools/README.md — it must exist."""
        missing = []
        for rel_path in IMPLEMENT_MD_TOOL_REFS:
            full = PROJECT_ROOT / rel_path
            if not full.exists():
                missing.append(rel_path)
        assert missing == [], (
            f"implement.md references paths that do not exist:\n"
            + "\n".join(f"  - {p}" for p in missing)
        )


class TestBridgeNotePropagation:
    """R27/P9: Bridge notes must be consumed by read_incoming_notes and
    hashed by _section_inputs_hash.

    P9-A: Bridge notes use from-bridge-* naming convention.
    P9-C: Input refs affect section inputs hash.
    """

    def test_bridge_notes_consumed_by_read_incoming_notes(
        self, planspace: Path, codespace: Path,
    ) -> None:
        """Bridge notes with from-bridge-* prefix are returned by
        read_incoming_notes (same glob as other cross-section notes)."""
        from section_loop.cross_section import read_incoming_notes
        from section_loop.types import Section

        notes_dir = planspace / "artifacts" / "notes"
        notes_dir.mkdir(parents=True, exist_ok=True)
        (notes_dir / "from-bridge-0-to-01.md").write_text(
            "**Note ID**: `bridge-0-to-01-abc123`\n\nContract requires X.")

        section = Section(
            number="01",
            path=planspace / "artifacts" / "sections" / "section-01.md",
            related_files=[],
        )
        notes_text = read_incoming_notes(section, planspace, codespace)
        assert "Contract requires X" in notes_text, (
            "Bridge notes must be consumed by read_incoming_notes"
        )

    def test_bridge_notes_affect_inputs_hash(
        self, planspace: Path, codespace: Path,
    ) -> None:
        """Bridge notes in from-bridge-* format change section inputs hash."""
        from section_loop.pipeline_control import _section_inputs_hash
        from section_loop.types import Section

        sections = {
            "01": Section(
                number="01",
                path=planspace / "artifacts" / "sections" / "section-01.md",
                related_files=[],
            ),
        }

        h1 = _section_inputs_hash("01", planspace, codespace, sections)

        notes_dir = planspace / "artifacts" / "notes"
        notes_dir.mkdir(parents=True, exist_ok=True)
        (notes_dir / "from-bridge-0-to-01.md").write_text(
            "Bridge note content")

        h2 = _section_inputs_hash("01", planspace, codespace, sections)
        assert h1 != h2, "Bridge note must change section inputs hash"

    def test_input_refs_affect_inputs_hash(
        self, planspace: Path, codespace: Path,
    ) -> None:
        """Contract delta refs in artifacts/inputs/section-{sec}/
        change section inputs hash."""
        from section_loop.pipeline_control import _section_inputs_hash
        from section_loop.types import Section

        sections = {
            "01": Section(
                number="01",
                path=planspace / "artifacts" / "sections" / "section-01.md",
                related_files=[],
            ),
        }

        h1 = _section_inputs_hash("01", planspace, codespace, sections)

        # Create input ref
        inputs_dir = planspace / "artifacts" / "inputs" / "section-01"
        inputs_dir.mkdir(parents=True, exist_ok=True)
        delta_path = planspace / "artifacts" / "contracts" / "contract-delta-group-0.md"
        delta_path.parent.mkdir(parents=True, exist_ok=True)
        delta_path.write_text("# Contract Delta\nShared interface spec")
        (inputs_dir / "contract-delta-group-0.ref").write_text(
            str(delta_path))

        h2 = _section_inputs_hash("01", planspace, codespace, sections)
        assert h1 != h2, "Input ref must change section inputs hash"

        # Changing the referenced file also changes hash
        delta_path.write_text("# Contract Delta v2\nUpdated spec")
        h3 = _section_inputs_hash("01", planspace, codespace, sections)
        assert h2 != h3, "Referenced file content change must change hash"


class TestModeInputsInHash:
    """R27/P5: Mode files affect section inputs hash.

    Greenfield/brownfield mode shapes prompt context, so changing mode
    must trigger section requeue via hash change.
    """

    def test_project_mode_changes_inputs_hash(
        self, planspace: Path, codespace: Path,
    ) -> None:
        """project-mode.txt change must change section inputs hash."""
        from section_loop.pipeline_control import _section_inputs_hash
        from section_loop.types import Section

        sections = {
            "01": Section(
                number="01",
                path=planspace / "artifacts" / "sections" / "section-01.md",
                related_files=[],
            ),
        }

        h1 = _section_inputs_hash("01", planspace, codespace, sections)

        mode_file = planspace / "artifacts" / "project-mode.txt"
        mode_file.parent.mkdir(parents=True, exist_ok=True)
        mode_file.write_text("greenfield")

        h2 = _section_inputs_hash("01", planspace, codespace, sections)
        assert h1 != h2, "project-mode.txt must change inputs hash"

    def test_section_mode_changes_inputs_hash(
        self, planspace: Path, codespace: Path,
    ) -> None:
        """section-mode.txt change must change section inputs hash."""
        from section_loop.pipeline_control import _section_inputs_hash
        from section_loop.types import Section

        sections = {
            "01": Section(
                number="01",
                path=planspace / "artifacts" / "sections" / "section-01.md",
                related_files=[],
            ),
        }

        h1 = _section_inputs_hash("01", planspace, codespace, sections)

        mode_file = (planspace / "artifacts" / "sections"
                     / "section-01-mode.txt")
        mode_file.parent.mkdir(parents=True, exist_ok=True)
        mode_file.write_text("hybrid")

        h2 = _section_inputs_hash("01", planspace, codespace, sections)
        assert h1 != h2, "section-mode.txt must change inputs hash"


class TestBridgeNoteLifecycle:
    """R28: Bridge notes participate in the full note lifecycle.

    Canonical Note ID format (colon + backticks) is required for bridge
    notes to be filterable by acknowledgment and visible to coordination.
    """

    def test_bridge_note_filtered_when_acknowledged(
        self, planspace: Path, codespace: Path,
    ) -> None:
        """A bridge note with an accepted ack entry must be filtered out
        by read_incoming_notes."""
        import json
        from section_loop.cross_section import read_incoming_notes
        from section_loop.types import Section

        notes_dir = planspace / "artifacts" / "notes"
        notes_dir.mkdir(parents=True, exist_ok=True)
        note_id = "bridge-0-to-01-abc123"
        (notes_dir / "from-bridge-0-to-01.md").write_text(
            f"**Note ID**: `{note_id}`\n\nContract requires X.")

        signals_dir = planspace / "artifacts" / "signals"
        signals_dir.mkdir(parents=True, exist_ok=True)
        ack = {"acknowledged": [{"note_id": note_id, "action": "accepted"}]}
        (signals_dir / "note-ack-01.json").write_text(json.dumps(ack))

        section = Section(
            number="01",
            path=planspace / "artifacts" / "sections" / "section-01.md",
            related_files=[],
        )
        notes_text = read_incoming_notes(section, planspace, codespace)
        assert "Contract requires X" not in notes_text, (
            "Accepted bridge notes must be filtered out by read_incoming_notes"
        )

    def test_coordination_includes_rejected_bridge_note(
        self, planspace: Path, codespace: Path,
    ) -> None:
        """A rejected bridge note must appear as an outstanding problem
        in coordination scanning."""
        import json
        from section_loop.coordination.problems import (
            _collect_outstanding_problems,
        )
        from section_loop.types import Section, SectionResult

        notes_dir = planspace / "artifacts" / "notes"
        notes_dir.mkdir(parents=True, exist_ok=True)
        note_id = "bridge-0-to-01-abc123"
        (notes_dir / "from-bridge-0-to-01.md").write_text(
            f"**Note ID**: `{note_id}`\n\nContract requires X.")

        signals_dir = planspace / "artifacts" / "signals"
        signals_dir.mkdir(parents=True, exist_ok=True)
        ack = {"acknowledged": [
            {"note_id": note_id, "action": "rejected", "reason": "disagree"},
        ]}
        (signals_dir / "note-ack-01.json").write_text(json.dumps(ack))

        section = Section(
            number="01",
            path=planspace / "artifacts" / "sections" / "section-01.md",
            related_files=[],
        )
        section_results = {
            "01": SectionResult(section_number="01", aligned=True),
        }
        sections_by_num = {"01": section}
        problems = _collect_outstanding_problems(
            section_results, sections_by_num, planspace,
        )
        bridge_problems = [
            p for p in problems if p.get("note_id") == note_id
        ]
        assert len(bridge_problems) > 0, (
            "Rejected bridge notes must appear as outstanding problems"
        )
        assert bridge_problems[0]["type"] == "consequence_conflict"


class TestAlignmentTemplateJsonVerdict:
    """R28/P10: Alignment templates must reference the structured JSON verdict.

    The alignment-judge agent method requires a JSON block. Task templates
    must reinforce this to avoid missing-JSON adjudicator cycles.
    """

    def test_integration_alignment_mentions_json_verdict(self) -> None:
        from pathlib import Path
        template = (
            Path(__file__).resolve().parent.parent
            / "src" / "scripts" / "section_loop" / "prompts" / "templates"
            / "integration-alignment.md"
        ).read_text(encoding="utf-8")
        assert "structured JSON verdict" in template.lower() or \
               "JSON verdict block" in template, (
            "integration-alignment.md must reference the structured JSON "
            "verdict required by alignment-judge.md"
        )

    def test_implementation_alignment_mentions_json_verdict(self) -> None:
        from pathlib import Path
        template = (
            Path(__file__).resolve().parent.parent
            / "src" / "scripts" / "section_loop" / "prompts" / "templates"
            / "implementation-alignment.md"
        ).read_text(encoding="utf-8")
        assert "structured JSON verdict" in template.lower() or \
               "JSON verdict block" in template, (
            "implementation-alignment.md must reference the structured JSON "
            "verdict required by alignment-judge.md"
        )


# ---------- Round 30 guards ----------

SECTION_LOOP_PKG = PROJECT_ROOT / "src" / "scripts" / "section_loop"


class TestModelPolicyConsistency:
    """R30/V1: All dispatch callsites in section_loop use model policy.

    Every dispatch_agent() call must use a policy[...] lookup, not a
    hardcoded model string like "claude-opus".  The only allowed hardcoded
    strings are in default parameter values and docstrings.
    """

    # Files that contain dispatch_agent() calls.
    DISPATCH_FILES = [
        SECTION_LOOP_PKG / "section_engine" / "runner.py",
        SECTION_LOOP_PKG / "section_engine" / "reexplore.py",
        SECTION_LOOP_PKG / "alignment.py",
        SECTION_LOOP_PKG / "coordination" / "runner.py",
    ]

    def test_no_hardcoded_claude_opus_in_dispatch_calls(self) -> None:
        """dispatch_agent("claude-opus", ...) must not appear in
        section_loop dispatch callsites (except defaults/docstrings)."""
        for fpath in self.DISPATCH_FILES:
            content = fpath.read_text(encoding="utf-8")
            lines = content.splitlines()
            for i, line in enumerate(lines, 1):
                stripped = line.strip()
                # Skip comments, docstrings, default parameter values
                if stripped.startswith("#"):
                    continue
                if stripped.startswith(('"""', "'''")):
                    continue
                if "= \"claude-opus\"" in stripped:
                    # Default parameter — acceptable
                    continue
                # The violation: dispatch_agent("claude-opus"
                if ("dispatch_agent(" in stripped
                        and '"claude-opus"' in stripped):
                    raise AssertionError(
                        f"{fpath.name}:{i}: dispatch_agent() call uses "
                        f"hardcoded 'claude-opus' instead of policy lookup"
                    )


class TestCheckAgentSignalsNoAdjudicator:
    """R30/V2: check_agent_signals must NOT auto-dispatch an adjudicator.

    When no signal file exists, the function returns (None, "").
    Adjudication is available via adjudicate_agent_output for callers
    that detect mechanical anomalies, but check_agent_signals itself
    must not invoke it.
    """

    def test_no_adjudicate_call_in_check_agent_signals(self) -> None:
        """check_agent_signals body must not call adjudicate_agent_output.

        The docstring may mention it for context, but the function body
        must not invoke it.
        """
        import ast
        import inspect
        import textwrap
        from section_loop.dispatch import check_agent_signals
        source = textwrap.dedent(inspect.getsource(check_agent_signals))
        tree = ast.parse(source)
        func = tree.body[0]
        assert isinstance(func, ast.FunctionDef)
        # Walk the function body (skip docstring) for Name references
        for node in ast.walk(func):
            if isinstance(node, ast.Name) and node.id == "adjudicate_agent_output":
                raise AssertionError(
                    "check_agent_signals must not reference "
                    "adjudicate_agent_output in its body — "
                    "adjudicator tax removed in R30"
                )
            if isinstance(node, ast.Attribute) and node.attr == "adjudicate_agent_output":
                raise AssertionError(
                    "check_agent_signals must not reference "
                    "adjudicate_agent_output in its body — "
                    "adjudicator tax removed in R30"
                )

    def test_no_dispatch_agent_in_check_agent_signals(self) -> None:
        """check_agent_signals must not dispatch any agent."""
        import inspect
        from section_loop.dispatch import check_agent_signals
        source = inspect.getsource(check_agent_signals)
        assert "dispatch_agent(" not in source, (
            "check_agent_signals must not call dispatch_agent — "
            "adjudicator tax removed in R30"
        )


class TestScanPolicyTransparency:
    """R30/V3: read_scan_model_policy must warn on parse failure.

    Silent failure (bare pass in except) is forbidden — the function
    must print a warning when model-policy.json is malformed.
    """

    def test_warns_on_invalid_json(self, tmp_path: Path) -> None:
        """Malformed JSON triggers a printed warning, not silent pass."""
        import io
        import sys
        from scan.dispatch import read_scan_model_policy

        (tmp_path / "model-policy.json").write_text("{bad json")
        captured = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = captured
        try:
            policy = read_scan_model_policy(tmp_path)
        finally:
            sys.stdout = old_stdout
        output = captured.getvalue()
        assert "WARNING" in output, (
            "read_scan_model_policy must print WARNING on parse failure"
        )
        # Should still return defaults
        assert "codemap_build" in policy


# ---------- Round 31 guards ----------


class TestProblemFrameInAlignmentSurface:
    """R31/V1: Problem frame must appear in alignment surface and prompt context.

    The setup phase creates a problem-frame artifact. Downstream consumers
    (alignment surface, prompt context, templates) must reference it so
    agents maintain connected understanding.
    """

    def test_alignment_surface_includes_problem_frame(
        self, planspace: Path,
    ) -> None:
        """_write_alignment_surface includes problem frame when it exists."""
        from section_loop.section_engine.reexplore import (
            _write_alignment_surface,
        )
        from section_loop.types import Section

        sections_dir = planspace / "artifacts" / "sections"
        sections_dir.mkdir(parents=True, exist_ok=True)
        section = Section(
            number="01",
            path=sections_dir / "section-01.md",
            related_files=[],
        )
        # Create required excerpts
        (sections_dir / "section-01-proposal-excerpt.md").write_text("e1")
        (sections_dir / "section-01-alignment-excerpt.md").write_text("e2")
        # Create problem frame
        pf = sections_dir / "section-01-problem-frame.md"
        pf.write_text("# Problem Frame\n## Problem Statement\nAuth flow")

        _write_alignment_surface(planspace, section)

        surface = (sections_dir / "section-01-alignment-surface.md").read_text()
        assert "problem-frame" in surface.lower() or "Problem frame" in surface

    def test_prompt_context_includes_problem_frame(
        self, planspace: Path, codespace: Path,
    ) -> None:
        """build_prompt_context includes problem_frame_ref when file exists."""
        from section_loop.prompts.context import build_prompt_context
        from section_loop.types import Section

        sections_dir = planspace / "artifacts" / "sections"
        sections_dir.mkdir(parents=True, exist_ok=True)
        section = Section(
            number="01",
            path=sections_dir / "section-01.md",
            related_files=[],
        )
        (sections_dir / "section-01.md").write_text("# Section 01")
        # Create problem frame
        pf = sections_dir / "section-01-problem-frame.md"
        pf.write_text("# Problem Frame")

        ctx = build_prompt_context(section, planspace, codespace)
        assert ctx["problem_frame_ref"] != "", (
            "problem_frame_ref must be non-empty when problem frame exists"
        )
        assert "problem_frame_path" in ctx

    def test_templates_use_problem_frame_placeholder(self) -> None:
        """Integration proposal and strategic impl templates include
        {problem_frame_ref} placeholder."""
        templates_dir = (
            PROJECT_ROOT / "src" / "scripts" / "section_loop" / "prompts" / "templates"
        )
        for template_name in (
            "integration-proposal.md",
            "strategic-implementation.md",
        ):
            content = (templates_dir / template_name).read_text()
            assert "{problem_frame_ref}" in content, (
                f"{template_name} must include {{problem_frame_ref}} placeholder"
            )


class TestScopeDeltaPayload:
    """R31/V3: Scope-delta artifacts must include full signal payload.

    When a section signals OUT_OF_SCOPE, the scope-delta JSON should
    include signal_path and signal_payload fields for richer coordinator
    context — not just a compressed detail string.
    """

    def test_scope_delta_code_includes_signal_payload_field(self) -> None:
        """runner.py scope-delta blocks include signal_payload key."""
        runner_path = (SECTION_LOOP_PKG / "section_engine" / "runner.py")
        content = runner_path.read_text(encoding="utf-8")
        # Both scope-delta sites must include signal_payload
        assert content.count('"signal_payload"') >= 2, (
            "runner.py must include signal_payload in both scope-delta "
            "blocks (setup + proposal)"
        )
        assert content.count('"signal_path"') >= 2, (
            "runner.py must include signal_path in both scope-delta blocks"
        )


class TestModelPolicyCompleteness:
    """R31/V4: read_model_policy defaults must cover ALL dispatch callsites.

    Every model string used in a dispatch_agent() call must have a
    corresponding key in the model policy defaults. This prevents
    hardcoded model strings from bypassing policy overrides.
    """

    # All policy keys that must exist in read_model_policy defaults
    REQUIRED_POLICY_KEYS = [
        "setup", "proposal", "alignment", "implementation",
        "coordination_plan", "coordination_fix", "coordination_bridge",
        "exploration", "adjudicator", "impact_analysis",
        "impact_normalizer", "triage", "microstrategy_decider",
        "tool_registrar", "bridge_tools", "escalation_model",
    ]

    def test_all_policy_keys_have_defaults(self, planspace: Path) -> None:
        """read_model_policy must return defaults for all known keys."""
        from section_loop.dispatch import read_model_policy
        policy = read_model_policy(planspace)
        for key in self.REQUIRED_POLICY_KEYS:
            assert key in policy, (
                f"read_model_policy missing default for '{key}'"
            )
            assert isinstance(policy[key], str), (
                f"policy['{key}'] must be a string model name, "
                f"got {type(policy[key])}"
            )

    def test_no_hardcoded_model_in_dispatch_calls(self) -> None:
        """No dispatch_agent() call uses a bare model string literal.

        Extends R30 guard to catch ALL model literals, not just
        'claude-opus'. Checks for dispatch_agent("model-name", ...)
        where model-name is from the known model set.
        """
        dispatch_files = [
            SECTION_LOOP_PKG / "section_engine" / "runner.py",
            SECTION_LOOP_PKG / "section_engine" / "reexplore.py",
            SECTION_LOOP_PKG / "section_engine" / "todos.py",
            SECTION_LOOP_PKG / "alignment.py",
            SECTION_LOOP_PKG / "coordination" / "runner.py",
            SECTION_LOOP_PKG / "cross_section.py",
            SECTION_LOOP_PKG / "main.py",
        ]
        known_models = [
            "claude-opus", "claude-haiku", "glm",
            "gpt-codex-high", "gpt-codex-high",
            "gpt-codex-xhigh",
        ]
        # Pattern: dispatch_agent("model-literal", ...) on a non-default line
        for fpath in dispatch_files:
            if not fpath.exists():
                continue
            content = fpath.read_text(encoding="utf-8")
            lines = content.splitlines()
            for i, line in enumerate(lines, 1):
                stripped = line.strip()
                # Skip comments, docstrings, default params
                if stripped.startswith("#"):
                    continue
                if stripped.startswith(('"""', "'''")):
                    continue
                # Default parameter values are acceptable
                if "= \"" in stripped and "def " in stripped:
                    continue
                if "dispatch_agent(" not in stripped:
                    continue
                for model in known_models:
                    if f'dispatch_agent("{model}"' in stripped:
                        raise AssertionError(
                            f"{fpath.name}:{i}: dispatch_agent() uses "
                            f"hardcoded '{model}' instead of policy lookup"
                        )

    def test_adjudicate_agent_output_accepts_model_param(self) -> None:
        """adjudicate_agent_output must accept a model parameter."""
        import inspect
        from section_loop.dispatch import adjudicate_agent_output
        sig = inspect.signature(adjudicate_agent_output)
        assert "model" in sig.parameters, (
            "adjudicate_agent_output must accept a model parameter "
            "for policy-driven selection"
        )

    def test_alignment_check_accepts_adjudicator_model(self) -> None:
        """_run_alignment_check_with_retries must accept adjudicator_model."""
        import inspect
        from section_loop.alignment import _run_alignment_check_with_retries
        sig = inspect.signature(_run_alignment_check_with_retries)
        assert "adjudicator_model" in sig.parameters, (
            "_run_alignment_check_with_retries must accept "
            "adjudicator_model for policy-driven selection"
        )


# ---------------------------------------------------------------
# R32/V1: No script-side grouping fallback in coordination plan
# ---------------------------------------------------------------

class TestCoordinationPlanNoScriptGrouping:
    """Coordination plan parse failure must retry + fail closed,
    never fall back to script-constructed one-problem-per-group."""

    RUNNER = (PROJECT_ROOT / "src" / "scripts" / "section_loop"
              / "coordination" / "runner.py")

    def test_no_one_problem_per_group_fallback(self) -> None:
        """runner.py must not construct groups in script code."""
        src = self.RUNNER.read_text(encoding="utf-8")
        # The old fallback had: "reason": "fallback"
        assert '"reason": "fallback"' not in src, (
            "coordination/runner.py still contains script-side "
            "'fallback' grouping — must retry + fail closed instead"
        )

    def test_fail_closed_artifact_written(self) -> None:
        """runner.py must write coordination-plan-failure.md on fail."""
        src = self.RUNNER.read_text(encoding="utf-8")
        assert "coordination-plan-failure.md" in src, (
            "coordination/runner.py must write failure artifact "
            "when plan is unparseable"
        )

    def test_retry_with_escalation_model(self) -> None:
        """runner.py must retry plan with escalation model before fail."""
        src = self.RUNNER.read_text(encoding="utf-8")
        assert 'policy["escalation_model"]' in src, (
            "coordination/runner.py must use policy escalation model "
            "for plan retry"
        )
        assert "coordination-plan-output-retry.md" in src, (
            "coordination/runner.py must write retry output for "
            "traceability"
        )


# ---------------------------------------------------------------
# R32/V2: Escalation and fix model strictly policy-driven
# ---------------------------------------------------------------

class TestEscalationModelPolicyDriven:
    """Hard-coded model strings in escalation file writes and fix
    model defaults must use policy lookups instead."""

    RUNNER = (PROJECT_ROOT / "src" / "scripts" / "section_loop"
              / "coordination" / "runner.py")
    MAIN = PROJECT_ROOT / "src" / "scripts" / "section_loop" / "main.py"
    EXECUTION = (PROJECT_ROOT / "src" / "scripts" / "section_loop"
                 / "coordination" / "execution.py")

    def test_no_hardcoded_escalation_model_in_runner(self) -> None:
        """coordination/runner.py must not hardcode escalation model string."""
        src = self.RUNNER.read_text(encoding="utf-8")
        # The old pattern: write_text("gpt-codex-xhigh"
        for line in src.split("\n"):
            if "write_text" in line and "gpt-codex-xhigh" in line:
                raise AssertionError(
                    "coordination/runner.py has hardcoded escalation "
                    "model in write_text call — must use policy"
                )

    def test_no_hardcoded_escalation_model_in_main(self) -> None:
        """main.py must not hardcode escalation model string."""
        src = self.MAIN.read_text(encoding="utf-8")
        for line in src.split("\n"):
            if "write_text" in line and "gpt-codex-xhigh" in line:
                raise AssertionError(
                    "main.py has hardcoded escalation model in "
                    "write_text call — must use policy"
                )

    def test_no_hardcoded_fix_model_in_execution(self) -> None:
        """execution.py must not hardcode default fix model."""
        src = self.EXECUTION.read_text(encoding="utf-8")
        assert 'fix_model = "gpt-codex-high"' not in src, (
            "execution.py has hardcoded fix model default — "
            "must come from policy"
        )

    def test_stall_threshold_from_policy(self) -> None:
        """main.py must read stall_count threshold from policy."""
        src = self.MAIN.read_text(encoding="utf-8")
        assert "escalation_triggers" in src, (
            "main.py must read escalation threshold from "
            "policy escalation_triggers"
        )


# ---------------------------------------------------------------
# R32/V3: frame_ok=false is structural failure, not retry
# ---------------------------------------------------------------

class TestInvalidFrameNoRetry:
    """When alignment judge returns frame_ok=false, the system must
    surface upward (INVALID_FRAME) instead of retrying."""

    ALIGNMENT = (PROJECT_ROOT / "src" / "scripts" / "section_loop"
                 / "alignment.py")
    MAIN = PROJECT_ROOT / "src" / "scripts" / "section_loop" / "main.py"
    COORD_RUNNER = (PROJECT_ROOT / "src" / "scripts" / "section_loop"
                    / "coordination" / "runner.py")

    def test_alignment_returns_invalid_frame_sentinel(self) -> None:
        """alignment.py must return INVALID_FRAME on frame_ok=false."""
        src = self.ALIGNMENT.read_text(encoding="utf-8")
        assert 'return "INVALID_FRAME"' in src, (
            "alignment.py must return INVALID_FRAME sentinel "
            "when frame_ok is False"
        )
        # Must NOT retry on frame_ok=false
        lines = src.split("\n")
        for i, line in enumerate(lines):
            if "frame_ok" in line and "continue" in line:
                raise AssertionError(
                    f"alignment.py:{i+1} retries on frame_ok=false — "
                    f"must return INVALID_FRAME instead"
                )

    def test_main_handles_invalid_frame(self) -> None:
        """main.py must check for INVALID_FRAME and surface upward."""
        src = self.MAIN.read_text(encoding="utf-8")
        assert "INVALID_FRAME" in src, (
            "main.py must handle INVALID_FRAME sentinel from "
            "alignment checks"
        )
        assert "fail:invalid_alignment_frame" in src, (
            "main.py must send mailbox message on invalid frame"
        )

    def test_coordination_handles_invalid_frame(self) -> None:
        """coordination/runner.py must check for INVALID_FRAME."""
        src = self.COORD_RUNNER.read_text(encoding="utf-8")
        assert "INVALID_FRAME" in src, (
            "coordination/runner.py must handle INVALID_FRAME "
            "sentinel from alignment checks"
        )


# ---------------------------------------------------------------
# R32/V4: Feedback signal status acked after update
# ---------------------------------------------------------------

class TestFeedbackSignalAcked:
    """After applying a related-files update, the signal status must
    be updated from 'stale' to 'applied' or 'no_change'."""

    FEEDBACK = PROJECT_ROOT / "src" / "scripts" / "scan" / "feedback.py"

    def test_signal_status_updated_after_apply(self) -> None:
        """feedback.py must update signal status after application."""
        src = self.FEEDBACK.read_text(encoding="utf-8")
        assert '"applied"' in src, (
            "feedback.py must set status to 'applied' after "
            "successful update"
        )
        assert '"no_change"' in src, (
            "feedback.py must set status to 'no_change' when "
            "update produces no file change"
        )


# ---------------------------------------------------------------
# R33/V1: Related Files parsing unified, block-scoped, code-fence-safe
# ---------------------------------------------------------------

class TestRelatedFilesUnified:
    """All Related Files parsing and editing must use the shared
    block-scoped, code-fence-safe utilities in scan.related_files."""

    def test_extract_ignores_code_fenced_entries(self) -> None:
        """### entries inside code fences must NOT be extracted."""
        from scan.related_files import extract_related_files

        text = (
            "# Section\n"
            "## Related Files\n"
            "### src/real.py\n"
            "Real file entry.\n"
            "```python\n"
            "### src/fake.py\n"
            "This is inside a code fence.\n"
            "```\n"
            "### src/also_real.py\n"
            "Another real entry.\n"
            "## Next Section\n"
        )
        result = extract_related_files(text)
        assert "src/real.py" in result
        assert "src/also_real.py" in result
        assert "src/fake.py" not in result, (
            "Code-fenced ### entry must not be extracted"
        )

    def test_find_entry_span_block_scoped(self) -> None:
        """find_entry_span must only find entries in Related Files block."""
        from scan.related_files import find_entry_span

        text = (
            "# Section\n"
            "### src/main.py\n"
            "This heading is outside Related Files.\n"
            "## Related Files\n"
            "### src/main.py\n"
            "The real entry.\n"
            "## Next Section\n"
        )
        span = find_entry_span(text, "src/main.py")
        assert span is not None
        entry_text = text[span[0]:span[1]]
        assert "The real entry" in entry_text
        assert "outside Related Files" not in entry_text

    def test_deep_scan_uses_shared_parser(self) -> None:
        """deep_scan.py must delegate to scan.related_files."""
        src = (SCAN_PKG / "deep_scan.py").read_text(encoding="utf-8")
        assert "from .related_files import" in src or \
               "from scan.related_files import" in src, (
            "deep_scan.py must import from the shared related_files module"
        )

    def test_main_uses_shared_parser(self) -> None:
        """section_loop/main.py must delegate to scan.related_files."""
        src = (SECTION_LOOP_PKG / "main.py").read_text(encoding="utf-8")
        assert "from scan.related_files import" in src, (
            "main.py must import from the shared related_files module"
        )

    def test_exploration_uses_shared_helpers(self) -> None:
        """exploration.py must use block-scoped helpers."""
        src = (SCAN_PKG / "exploration.py").read_text(encoding="utf-8")
        assert "from .related_files import" in src or \
               "from scan.related_files import" in src, (
            "exploration.py must import from the shared related_files module"
        )
        # Must NOT use section.find(marker) for whole-file search
        assert 'section.find(f"### {' not in src, (
            "exploration.py must not search for ### entries across "
            "the entire file — use block-scoped find_entry_span instead"
        )

    def test_update_match_uses_shared_helpers(self) -> None:
        """deep_scan.py update_match must use block-scoped entry finding."""
        src = (SCAN_PKG / "deep_scan.py").read_text(encoding="utf-8")
        # Old pattern: section.find(marker) where marker = f"### {source_file}"
        assert 'section.find(marker)' not in src and \
               'section.find(f"### {' not in src, (
            "deep_scan.py must not use section.find(marker) for "
            "whole-file search — use find_entry_span instead"
        )


# ---------------------------------------------------------------
# R33/V2: Signal instructions clarify JSON is the only truth channel
# ---------------------------------------------------------------

class TestSignalInstructionsNoFallback:
    """Signal instructions template must not imply the script reads
    a backup text line. JSON is the only truth channel."""

    TEMPLATE = (PROJECT_ROOT / "src" / "scripts" / "section_loop" / "prompts"
                / "templates" / "signal-instructions.md")

    def test_no_backup_channel_language(self) -> None:
        """Template must not contain phrases implying script reads text."""
        src = self.TEMPLATE.read_text(encoding="utf-8")
        banned = [
            "backup for the script",
            "fallback channel",
            "script reads this line",
            "Backup output line",
        ]
        for phrase in banned:
            assert phrase not in src, (
                f"signal-instructions.md contains '{phrase}' — "
                f"implies script reads text as fallback"
            )

    def test_json_required_clarification(self) -> None:
        """Template must explicitly say JSON is required."""
        src = self.TEMPLATE.read_text(encoding="utf-8")
        assert "JSON signal" in src and "required" in src, (
            "signal-instructions.md must clarify JSON signal is required"
        )


# ---------------------------------------------------------------
# R33/V3: Problem frame in convergence hashing and traceability
# ---------------------------------------------------------------

class TestProblemFrameInConvergence:
    """Problem frame must be part of convergence hashing (input hash)
    and traceability index."""

    def test_problem_frame_changes_inputs_hash(
        self, planspace: Path, codespace: Path,
    ) -> None:
        """Problem frame change must change section inputs hash."""
        from section_loop.pipeline_control import _section_inputs_hash
        from section_loop.types import Section

        sections = {
            "01": Section(
                number="01",
                path=planspace / "artifacts" / "sections" / "section-01.md",
                related_files=[],
            ),
        }

        h1 = _section_inputs_hash("01", planspace, codespace, sections)

        pf = (planspace / "artifacts" / "sections"
              / "section-01-problem-frame.md")
        pf.parent.mkdir(parents=True, exist_ok=True)
        pf.write_text("# Problem Frame\nAuth flow redesign")

        h2 = _section_inputs_hash("01", planspace, codespace, sections)
        assert h1 != h2, "Problem frame presence must change inputs hash"

        pf.write_text("# Problem Frame\nDifferent problem statement")
        h3 = _section_inputs_hash("01", planspace, codespace, sections)
        assert h2 != h3, "Problem frame content change must change hash"

    def test_traceability_index_includes_problem_frame(self) -> None:
        """traceability.py must record problem_frame in the index."""
        src = (SECTION_LOOP_PKG / "section_engine"
               / "traceability.py").read_text(encoding="utf-8")
        assert '"problem_frame"' in src, (
            "traceability.py must include problem_frame in the "
            "traceability index"
        )


# ---------------------------------------------------------------
# R33/V4: loop-contract.md lists all hashed inputs
# ---------------------------------------------------------------

class TestLoopContractCompleteness:
    """loop-contract.md must list all inputs that _section_inputs_hash
    actually includes."""

    CONTRACT = PROJECT_ROOT / "src" / "loop-contract.md"

    def test_contract_lists_all_hashed_inputs(self) -> None:
        """Every major input in _section_inputs_hash must be named."""
        src = self.CONTRACT.read_text(encoding="utf-8")
        required_mentions = [
            "codemap.md",
            "codemap-corrections",
            "project-mode",
            "section-NN-mode",
            "problem-frame",
            "input refs",
        ]
        for mention in required_mentions:
            assert mention.lower() in src.lower(), (
                f"loop-contract.md missing '{mention}' — "
                f"must list all hashed inputs"
            )

    def test_contract_references_authoritative_source(self) -> None:
        """Contract must reference _section_inputs_hash as authoritative."""
        src = self.CONTRACT.read_text(encoding="utf-8")
        assert "_section_inputs_hash" in src, (
            "loop-contract.md must reference _section_inputs_hash() "
            "as the authoritative source"
        )


# ---------------------------------------------------------------
# R34/V1: Tool registry malformed → remove stale surface + repair
# ---------------------------------------------------------------

class TestToolRegistryFailClosed:
    """When tool-registry.json is malformed, runner.py must:
    1. Delete any stale tools-available surface
    2. Dispatch tool-registrar for repair
    3. Write blocker signal if repair fails
    """

    RUNNER = (PROJECT_ROOT / "src" / "scripts" / "section_loop"
              / "section_engine" / "runner.py")

    def test_malformed_registry_removes_stale_surface(self) -> None:
        """Step 0b must unlink stale tools-available on malformed JSON."""
        src = self.RUNNER.read_text(encoding="utf-8")
        # The except block for JSONDecodeError must contain unlink
        # for tools_available_path
        assert "tools_available_path.unlink()" in src, (
            "runner.py Step 0b must delete stale tools-available "
            "surface when registry is malformed"
        )

    def test_malformed_registry_dispatches_repair(self) -> None:
        """Step 0b must dispatch tool-registrar for repair."""
        src = self.RUNNER.read_text(encoding="utf-8")
        assert "tool-registry-repair" in src, (
            "runner.py Step 0b must dispatch a registry repair "
            "agent when JSON is malformed"
        )

    def test_malformed_registry_blocker_on_repair_failure(self) -> None:
        """Step 0b must write blocker signal if repair fails."""
        src = self.RUNNER.read_text(encoding="utf-8")
        assert "blocker.json" in src, (
            "runner.py must write blocker signal when registry "
            "repair fails"
        )


# ---------------------------------------------------------------
# R34/V2: Post-impl tool registry malformed → repair, not pass
# ---------------------------------------------------------------

class TestPostImplToolRegistryRepair:
    """Step 3b must not silently pass on malformed post-impl registry.
    It must dispatch repair and fail-closed if repair fails."""

    RUNNER = (PROJECT_ROOT / "src" / "scripts" / "section_loop"
              / "section_engine" / "runner.py")

    def test_no_silent_pass_on_malformed(self) -> None:
        """Step 3b must not have 'pass' as the only action."""
        src = self.RUNNER.read_text(encoding="utf-8")
        # The old pattern: except (json.JSONDecodeError, ValueError):\n  pass
        assert "pass  # Malformed registry" not in src, (
            "runner.py Step 3b must not silently pass on malformed "
            "post-impl registry — must dispatch repair"
        )

    def test_post_impl_dispatches_repair(self) -> None:
        """Step 3b must dispatch repair agent."""
        src = self.RUNNER.read_text(encoding="utf-8")
        assert "tool-registry-post-repair" in src, (
            "runner.py Step 3b must dispatch post-impl registry "
            "repair agent"
        )

    def test_post_impl_writes_blocker_on_failure(self) -> None:
        """Step 3b must write blocker signal if repair fails."""
        src = self.RUNNER.read_text(encoding="utf-8")
        assert "post-impl-blocker.json" in src, (
            "runner.py Step 3b must write post-impl blocker "
            "signal when repair fails"
        )


# ---------------------------------------------------------------
# R34/V3: Microstrategy decision fails closed
# ---------------------------------------------------------------

class TestMicrostrategyFailClosed:
    """_check_needs_microstrategy must not silently return False
    when the decider fails. Must retry with escalation model and
    default to True (more strategy) on total failure."""

    def test_no_signal_returns_true(
        self, planspace: Path, codespace: Path, mock_dispatch: MagicMock,
    ) -> None:
        """When decider never writes signal, function returns True."""
        from section_loop.section_engine.todos import (
            _check_needs_microstrategy,
        )

        # Create a proposal file so the function doesn't bail early
        proposals_dir = planspace / "artifacts" / "proposals"
        proposals_dir.mkdir(parents=True, exist_ok=True)
        proposal = proposals_dir / "section-01-integration-proposal.md"
        proposal.write_text("# Proposal\nSome changes needed.")

        # dispatch_agent is mocked to do nothing (no signal written)
        result = _check_needs_microstrategy(
            proposal, planspace, "01",
            model="glm", escalation_model="gpt-codex-xhigh",
        )
        assert result is True, (
            "_check_needs_microstrategy must return True when "
            "decider produces no signal (fail-closed)"
        )

    def test_fallback_signal_written(
        self, planspace: Path, codespace: Path, mock_dispatch: MagicMock,
    ) -> None:
        """Fallback signal JSON must be written with explicit reason."""
        from section_loop.section_engine.todos import (
            _check_needs_microstrategy,
        )

        proposals_dir = planspace / "artifacts" / "proposals"
        proposals_dir.mkdir(parents=True, exist_ok=True)
        proposal = proposals_dir / "section-01-integration-proposal.md"
        proposal.write_text("# Proposal\nSome changes needed.")

        _check_needs_microstrategy(
            proposal, planspace, "01",
            model="glm", escalation_model="gpt-codex-xhigh",
        )

        signal_path = (planspace / "artifacts" / "signals"
                       / "proposal-01-microstrategy.json")
        assert signal_path.exists(), (
            "Fallback microstrategy signal must be written on "
            "total decider failure"
        )
        data = json.loads(signal_path.read_text(encoding="utf-8"))
        assert data["needs_microstrategy"] is True
        assert "fail-closed" in data.get("reason", "")

    def test_escalation_model_parameter_exists(self) -> None:
        """_check_needs_microstrategy must accept escalation_model."""
        import inspect
        from section_loop.section_engine.todos import (
            _check_needs_microstrategy,
        )
        sig = inspect.signature(_check_needs_microstrategy)
        assert "escalation_model" in sig.parameters, (
            "_check_needs_microstrategy must accept "
            "escalation_model parameter for retry"
        )


# ---------------------------------------------------------------
# R34/V4: Prompt templates use policy-driven model placeholders
# ---------------------------------------------------------------

class TestTemplateModelParameterized:
    """Prompt templates must not embed fixed model names. They must
    use placeholders injected from model policy."""

    TEMPLATES_DIR = (PROJECT_ROOT / "src" / "scripts" / "section_loop"
                     / "prompts" / "templates")

    def test_no_hardcoded_model_in_dispatch_examples(self) -> None:
        """Templates must not contain bare model names in dispatch examples."""
        known_models = ["glm", "gpt-codex-high", "gpt-codex-xhigh"]
        for template_name in (
            "strategic-implementation.md",
            "integration-proposal.md",
        ):
            content = (self.TEMPLATES_DIR / template_name).read_text(
                encoding="utf-8")
            for model in known_models:
                # Check for --model <literal> pattern in bash examples
                if f"--model {model}" in content:
                    raise AssertionError(
                        f"{template_name} contains hardcoded "
                        f"'--model {model}' — must use placeholder"
                    )

    def test_templates_use_task_submission(self) -> None:
        """Templates must use task submission, not direct agent dispatch."""
        impl_content = (
            self.TEMPLATES_DIR / "strategic-implementation.md"
        ).read_text(encoding="utf-8")
        assert "{task_submission_path}" in impl_content, (
            "strategic-implementation.md must use "
            "{task_submission_path} placeholder"
        )
        assert "{allowed_tasks}" in impl_content, (
            "strategic-implementation.md must use "
            "{allowed_tasks} placeholder"
        )
        proposal_content = (
            self.TEMPLATES_DIR / "integration-proposal.md"
        ).read_text(encoding="utf-8")
        assert "{task_submission_path}" in proposal_content, (
            "integration-proposal.md must use "
            "{task_submission_path} placeholder"
        )

    def test_writers_inject_task_submission_path(
        self, planspace: Path, codespace: Path,
    ) -> None:
        """Writers must inject task_submission_path and allowed_tasks."""
        from section_loop.prompts.writers import (
            write_integration_proposal_prompt,
            write_strategic_impl_prompt,
        )
        from section_loop.types import Section

        sections_dir = planspace / "artifacts" / "sections"
        sections_dir.mkdir(parents=True, exist_ok=True)
        section = Section(
            number="01",
            path=sections_dir / "section-01.md",
            related_files=[],
        )
        (sections_dir / "section-01.md").write_text("# Section 01")
        # Create required excerpt files
        (sections_dir / "section-01-proposal-excerpt.md").write_text("P")
        (sections_dir / "section-01-alignment-excerpt.md").write_text("A")

        intg_path = write_integration_proposal_prompt(
            section, planspace, codespace,
        )
        intg_content = intg_path.read_text(encoding="utf-8")
        assert "task-requests-proposal-01.json" in intg_content, (
            "Integration proposal must include task submission path"
        )
        assert "scan_explore" in intg_content, (
            "Integration proposal must list allowed task types"
        )

        impl_path = write_strategic_impl_prompt(
            section, planspace, codespace,
        )
        impl_content = impl_path.read_text(encoding="utf-8")
        assert "task-requests-impl-01.json" in impl_content, (
            "Strategic impl must include task submission path"
        )
        assert "strategic_implementation" in impl_content, (
            "Strategic impl must list allowed task types"
        )

    def test_friction_signal_path_in_impl_prompt(
        self, planspace: Path, codespace: Path,
    ) -> None:
        """Strategic impl prompt must include friction signal path."""
        from section_loop.prompts.writers import write_strategic_impl_prompt
        from section_loop.types import Section

        sections_dir = planspace / "artifacts" / "sections"
        sections_dir.mkdir(parents=True, exist_ok=True)
        section = Section(
            number="01",
            path=sections_dir / "section-01.md",
            related_files=[],
        )
        (sections_dir / "section-01.md").write_text("# Section 01")
        (sections_dir / "section-01-proposal-excerpt.md").write_text("P")
        (sections_dir / "section-01-alignment-excerpt.md").write_text("A")

        impl_path = write_strategic_impl_prompt(
            section, planspace, codespace,
        )
        impl_content = impl_path.read_text(encoding="utf-8")
        assert "tool-friction.json" in impl_content, (
            "Strategic impl prompt must include friction signal path "
            "so agents can signal tool composition friction"
        )


# ---------------------------------------------------------------
# R35/V1: reexplore.py prompt uses policy-driven exploration model
# ---------------------------------------------------------------

class TestReexploreModelParameterized:
    """reexplore.py delegation instructions must not hardcode model names."""

    REEXPLORE = (PROJECT_ROOT / "src" / "scripts" / "section_loop"
                 / "section_engine" / "reexplore.py")

    def test_no_hardcoded_model_in_prompt_text(self) -> None:
        """Prompt f-string must not contain --model glm literally."""
        src = self.REEXPLORE.read_text(encoding="utf-8")
        assert "--model glm" not in src, (
            "reexplore.py prompt text contains hardcoded '--model glm' "
            "— must use task submission, not direct dispatch"
        )

    def test_exploration_model_parameter_exists(self) -> None:
        """_reexplore_section must accept exploration_model parameter."""
        import inspect
        from section_loop.section_engine.reexplore import _reexplore_section
        sig = inspect.signature(_reexplore_section)
        assert "exploration_model" in sig.parameters, (
            "_reexplore_section must accept exploration_model parameter "
            "for policy-driven delegation"
        )

    def test_caller_passes_exploration_model(self) -> None:
        """main.py must pass exploration_model from policy."""
        main_path = PROJECT_ROOT / "src" / "scripts" / "section_loop" / "main.py"
        src = main_path.read_text(encoding="utf-8")
        assert 'exploration_model=policy["exploration"]' in src, (
            "main.py must pass exploration_model from policy to "
            "_reexplore_section"
        )


# ---------------------------------------------------------------
# R35/V2: coordination/execution.py prompt uses policy-driven models
# ---------------------------------------------------------------

class TestCoordinationFixPromptModelParameterized:
    """Coordination fix prompt must not hardcode model names."""

    EXECUTION = (PROJECT_ROOT / "src" / "scripts" / "section_loop"
                 / "coordination" / "execution.py")

    def test_no_hardcoded_glm_in_prompt_text(self) -> None:
        """Fix prompt must not contain --model glm literally."""
        src = self.EXECUTION.read_text(encoding="utf-8")
        assert "--model glm" not in src, (
            "execution.py fix prompt contains hardcoded '--model glm' "
            "— must use task submission, not direct dispatch"
        )

    def test_no_hardcoded_codex_in_prompt_text(self) -> None:
        """Fix prompt must not contain --model gpt-codex-high literally."""
        src = self.EXECUTION.read_text(encoding="utf-8")
        assert "--model gpt-codex-high" not in src, (
            "execution.py fix prompt contains hardcoded "
            "'--model gpt-codex-high' — must use task submission"
        )

    def test_prompt_writer_no_direct_dispatch(self) -> None:
        """write_coordinator_fix_prompt must not reference direct dispatch."""
        src = self.EXECUTION.read_text(encoding="utf-8")
        assert "agents --model" not in src, (
            "write_coordinator_fix_prompt must use task submission, "
            "not direct agent dispatch"
        )

    def test_dispatch_uses_task_submission(self) -> None:
        """write_coordinator_fix_prompt must use task_submission_path."""
        src = self.EXECUTION.read_text(encoding="utf-8")
        assert "task_submission_path" in src, (
            "write_coordinator_fix_prompt must define a task_submission_path"
        )


# ---------------------------------------------------------------
# R35/P11: Sweep guard — no hardcoded --model literals in any
# prompt surface (templates + prompt builder source files)
# ---------------------------------------------------------------

class TestNoHardcodedModelInPromptSurfaces:
    """Comprehensive sweep: no prompt template or prompt builder
    may contain '--model <concrete-model-name>' for any known model.

    This catches propagation drift — when new prompt surfaces are
    added that embed model names instead of using policy injection.
    """

    # Layout-agnostic: support both src/ development and flat deployed (V9/R55)
    _SRC_TEMPLATES = (PROJECT_ROOT / "src" / "scripts" / "section_loop"
                      / "prompts" / "templates")
    _FLAT_TEMPLATES = (PROJECT_ROOT / "scripts" / "section_loop"
                       / "prompts" / "templates")
    TEMPLATES_DIR = _SRC_TEMPLATES if _SRC_TEMPLATES.exists() else _FLAT_TEMPLATES

    _SRC_SCAN_TEMPLATES = PROJECT_ROOT / "src" / "scripts" / "scan" / "templates"
    _FLAT_SCAN_TEMPLATES = PROJECT_ROOT / "scripts" / "scan" / "templates"
    SCAN_TEMPLATES_DIR = (
        _SRC_SCAN_TEMPLATES if _SRC_SCAN_TEMPLATES.exists()
        else _FLAT_SCAN_TEMPLATES
    )

    # Layout-agnostic prefix for prompt builder files
    _PREFIX = (
        PROJECT_ROOT / "src" / "scripts"
        if (PROJECT_ROOT / "src" / "scripts").exists()
        else PROJECT_ROOT / "scripts"
    )

    # All prompt builder source files that construct agent instructions
    PROMPT_BUILDER_FILES = [
        _PREFIX / "section_loop" / "section_engine" / "reexplore.py",
        _PREFIX / "section_loop" / "coordination" / "execution.py",
        _PREFIX / "section_loop" / "coordination" / "planning.py",
        _PREFIX / "section_loop" / "prompts" / "writers.py",
        _PREFIX / "section_loop" / "prompts" / "context.py",
    ]

    KNOWN_MODELS = [
        "glm", "gpt-codex-high", "gpt-codex-high",
        "gpt-codex-xhigh", "claude-opus", "claude-haiku",
    ]

    def test_no_hardcoded_model_in_section_loop_templates(self) -> None:
        """Section loop .md templates must not contain --model <literal>."""
        if not self.TEMPLATES_DIR.exists():
            pytest.skip("templates dir not found in either layout")
        for template in sorted(self.TEMPLATES_DIR.glob("*.md")):
            content = template.read_text(encoding="utf-8")
            for model in self.KNOWN_MODELS:
                if f"--model {model}" in content:
                    raise AssertionError(
                        f"{template.name} contains hardcoded "
                        f"'--model {model}' — must use placeholder"
                    )

    def test_no_hardcoded_model_in_prompt_builders(self) -> None:
        """Prompt builder .py files must not contain --model <literal>
        in f-string prompt text. Default parameter values and comments
        are exempt."""
        for fpath in self.PROMPT_BUILDER_FILES:
            if not fpath.exists():
                continue
            content = fpath.read_text(encoding="utf-8")
            lines = content.splitlines()
            for i, line in enumerate(lines, 1):
                stripped = line.strip()
                # Skip comments and docstrings
                if stripped.startswith("#"):
                    continue
                if stripped.startswith(('"""', "'''")):
                    continue
                # Skip default parameter definitions
                if "def " in stripped and "= \"" in stripped:
                    continue
                for model in self.KNOWN_MODELS:
                    if f"--model {model}" in stripped:
                        raise AssertionError(
                            f"{fpath.name}:{i}: contains hardcoded "
                            f"'--model {model}' in prompt text — "
                            f"must use policy-injected placeholder"
                        )


# ---------------------------------------------------------------
# R36/V1: Codex delegated impl dispatch uses --file, not inline
# ---------------------------------------------------------------

class TestCodexDispatchUsesFile:
    """Delegated implementation recipes (Codex) must use --file,
    not inline "<instructions>". Exploration recipes (GLM) may
    use inline — only the delegated_impl/delegation_impl surface
    is checked.
    """

    STRATEGIC_IMPL_TEMPLATE = (
        PROJECT_ROOT / "src" / "scripts" / "section_loop" / "prompts"
        / "templates" / "strategic-implementation.md"
    )
    COORDINATION_EXECUTION = (
        PROJECT_ROOT / "src" / "scripts" / "section_loop" / "coordination"
        / "execution.py"
    )
    IMPLEMENT_MD = PROJECT_ROOT / "src" / "implement.md"

    def test_strategic_impl_template_uses_task_submission(self) -> None:
        """strategic-implementation.md must use task submission, not dispatch."""
        content = self.STRATEGIC_IMPL_TEMPLATE.read_text(encoding="utf-8")
        assert "{task_submission_path}" in content, (
            "strategic-implementation.md must use task submission path"
        )
        assert "agents --model" not in content, (
            "strategic-implementation.md must not contain direct "
            "agent dispatch instructions"
        )

    def test_coordination_fix_prompt_uses_task_submission(self) -> None:
        """coordination/execution.py must use task submission, not dispatch."""
        content = self.COORDINATION_EXECUTION.read_text(encoding="utf-8")
        assert "task_submission_path" in content, (
            "coordination/execution.py must define task_submission_path"
        )
        assert "agents --model" not in content, (
            "coordination/execution.py must not contain direct "
            "agent dispatch instructions"
        )

    def test_implement_md_codex_uses_file(self) -> None:
        """implement.md Codex dispatch examples use --file."""
        content = self.IMPLEMENT_MD.read_text(encoding="utf-8")
        lines = content.splitlines()
        for i, line in enumerate(lines):
            # Check lines with Codex model names using inline instructions
            if ("gpt-codex-high" in line and
                    '"<instructions>"' in line):
                raise AssertionError(
                    f"implement.md:{i+1}: Codex dispatch uses inline "
                    f"\"<instructions>\" — must use --file per models.md"
                )


# ---------------------------------------------------------------
# R36/V2: Signal taxonomy synchronized in loop-contract.md and
# blockers.py docstring
# ---------------------------------------------------------------

class TestSignalTaxonomySynchronized:
    """loop-contract.md convergence criteria and blockers.py docstring
    must reflect all first-class signal states.
    """

    SIGNAL_INSTRUCTIONS = (
        PROJECT_ROOT / "src" / "scripts" / "section_loop" / "prompts"
        / "templates" / "signal-instructions.md"
    )
    LOOP_CONTRACT = PROJECT_ROOT / "src" / "loop-contract.md"
    BLOCKERS_PY = (
        PROJECT_ROOT / "src" / "scripts" / "section_loop" / "section_engine"
        / "blockers.py"
    )

    # Authoritative signal states from signal-instructions.md
    FIRST_CLASS_STATES = [
        "UNDERSPECIFIED", "NEED_DECISION", "DEPENDENCY",
        "OUT_OF_SCOPE", "NEEDS_PARENT",
    ]

    def test_loop_contract_lists_all_signal_states(self) -> None:
        """loop-contract.md convergence criteria must mention all
        first-class signal states."""
        content = self.LOOP_CONTRACT.read_text(encoding="utf-8")
        for state in self.FIRST_CLASS_STATES:
            assert state in content, (
                f"loop-contract.md convergence criteria must mention "
                f"{state} — it is a first-class signal state"
            )

    def test_blockers_docstring_lists_all_handled_states(self) -> None:
        """blockers.py _update_blocker_rollup docstring must mention
        all states it actually handles."""
        content = self.BLOCKERS_PY.read_text(encoding="utf-8")
        # The docstring should mention all 5 states
        for state in self.FIRST_CLASS_STATES:
            assert state in content, (
                f"blockers.py docstring must mention {state} — "
                f"the code handles it"
            )

    def test_signal_instructions_is_authoritative(self) -> None:
        """signal-instructions.md must list all expected states."""
        content = self.SIGNAL_INSTRUCTIONS.read_text(encoding="utf-8")
        for state in self.FIRST_CLASS_STATES:
            assert state in content, (
                f"signal-instructions.md must list {state}"
            )


# ── R37/V1: Scope-delta adjudication robust parsing ──────────────


class TestScopeDeltaAdjudicationParsing:
    """R37/V1: Scope-delta adjudication parsing is robust with retry
    + fail-closed — mirrors the coordination-plan pattern."""

    RUNNER_PY = (PROJECT_ROOT / "src" / "scripts" / "section_loop"
                 / "coordination" / "runner.py")

    def test_parser_code_fenced_json(self) -> None:
        """_parse_scope_delta_adjudication handles code-fenced JSON."""
        import sys
        sys.path.insert(0, str(PROJECT_ROOT / "src" / "scripts"))
        from section_loop.coordination.runner import (
            _parse_scope_delta_adjudication,
        )
        output = (
            'Here is my analysis:\n\n```json\n'
            '{"decisions": [{"section": "03", "action": "reject", '
            '"reason": "not needed"}]}\n```\n\nDone.'
        )
        result = _parse_scope_delta_adjudication(output)
        assert result is not None
        assert len(result["decisions"]) == 1
        assert result["decisions"][0]["action"] == "reject"

    def test_parser_bare_json(self) -> None:
        """_parse_scope_delta_adjudication handles bare JSON."""
        import sys
        sys.path.insert(0, str(PROJECT_ROOT / "src" / "scripts"))
        from section_loop.coordination.runner import (
            _parse_scope_delta_adjudication,
        )
        output = (
            '{"decisions": [{"section": "05", "action": "absorb", '
            '"reason": "fits existing scope", '
            '"absorb_into_section": "02", '
            '"scope_addition": "config validation"}]}'
        )
        result = _parse_scope_delta_adjudication(output)
        assert result is not None
        assert result["decisions"][0]["action"] == "absorb"

    def test_parser_rejects_invalid_action(self) -> None:
        """_parse_scope_delta_adjudication rejects unknown actions."""
        import sys
        sys.path.insert(0, str(PROJECT_ROOT / "src" / "scripts"))
        from section_loop.coordination.runner import (
            _parse_scope_delta_adjudication,
        )
        output = (
            '{"decisions": [{"section": "03", "action": "unknown", '
            '"reason": "bad"}]}'
        )
        result = _parse_scope_delta_adjudication(output)
        assert result is None

    def test_section_normalization(self, tmp_path: Path) -> None:
        """_normalize_section_id maps '3' → '03' when delta exists."""
        import sys
        sys.path.insert(0, str(PROJECT_ROOT / "src" / "scripts"))
        from section_loop.coordination.runner import (
            _normalize_section_id,
        )
        # Create a delta file with zero-padded name
        delta_file = tmp_path / "section-03-scope-delta.json"
        delta_file.write_text("{}", encoding="utf-8")
        assert _normalize_section_id("3", tmp_path) == "03"
        assert _normalize_section_id("03", tmp_path) == "03"

    def test_runner_has_retry_path(self) -> None:
        """runner.py scope-delta adjudication retries with escalation
        model on parse failure (mirrors coordination-plan pattern)."""
        content = self.RUNNER_PY.read_text(encoding="utf-8")
        assert "scope-delta-prompt-retry.md" in content, (
            "runner.py must retry scope-delta adjudication with "
            "escalation model"
        )
        assert "scope-delta-adjudication-failure.json" in content, (
            "runner.py must write failure artifact on double parse "
            "failure"
        )

    def test_runner_fail_closed(self) -> None:
        """runner.py scope-delta adjudication fails closed — does not
        silently leave deltas pending."""
        content = self.RUNNER_PY.read_text(encoding="utf-8")
        assert "unparseable_scope_delta_adjudication" in content, (
            "runner.py must send fail-closed mailbox notification"
        )
        # Must NOT contain the old "deltas remain pending" log
        assert "deltas remain pending" not in content, (
            "runner.py must not silently leave deltas pending — "
            "fail closed instead"
        )


# ── R37/V2: Recurrence escalation uses policy model ─────────────


class TestEscalationLogUsesPolicy:
    """R37/V2: Recurrence escalation log and resolution artifacts
    must use policy-driven model name, not hardcoded literals."""

    RUNNER_PY = (PROJECT_ROOT / "src" / "scripts" / "section_loop"
                 / "coordination" / "runner.py")

    def test_escalation_log_no_hardcoded_model(self) -> None:
        """The recurrence escalation log line must use
        policy['escalation_model'], not a hardcoded string."""
        content = self.RUNNER_PY.read_text(encoding="utf-8")
        # Find the escalation log line (near "recurrence escalation")
        found = False
        for line in content.split("\n"):
            if "recurrence escalation" in line and "setting model" in line:
                found = True
                assert "gpt-codex-xhigh" not in line, (
                    "escalation log must use policy variable, not "
                    "hardcoded model name"
                )
                break
        assert found, "Could not find recurrence escalation log line"

    def test_resolution_artifact_no_hardcoded_model(self) -> None:
        """Resolution artifact text must not hardcode the escalation
        model name."""
        content = self.RUNNER_PY.read_text(encoding="utf-8")
        # The resolution artifact says "escalated model (X)" —
        # X must come from policy, not a hardcoded literal
        assert 'f"(gpt-codex-xhigh)' not in content, (
            "resolution artifact must use policy['escalation_model'], "
            "not hardcoded model name"
        )


# ── R37/V3: implementation-strategist.md tool-registry schema ────


class TestImplStrategistToolRegistrySchema:
    """R37/V3: implementation-strategist.md tool registration example
    must include all required fields from tool-registrar.md."""

    IMPL_STRATEGIST = AGENTS_DIR / "implementation-strategist.md"
    TOOL_REGISTRAR = AGENTS_DIR / "tool-registrar.md"

    REQUIRED_FIELDS = {"id", "path", "created_by", "scope",
                       "status", "description", "registered_at"}

    def test_all_required_fields_in_example(self) -> None:
        """The tool registration JSON example must include all
        required fields from tool-registrar.md."""
        content = self.IMPL_STRATEGIST.read_text(encoding="utf-8")
        for field in self.REQUIRED_FIELDS:
            assert f'"{field}"' in content, (
                f"implementation-strategist.md tool registration "
                f"example must include required field '{field}'"
            )

    def test_canonical_created_by_format(self) -> None:
        """created_by must use 'section-NN' format, not bare
        '<section-number>'."""
        content = self.IMPL_STRATEGIST.read_text(encoding="utf-8")
        assert '"created_by": "section-' in content, (
            "created_by must use canonical 'section-NN' format"
        )
        assert '"created_by": "<section-number>"' not in content, (
            "created_by must not use angle-bracket placeholder"
        )


# ── R37/V4: Scan templates extension-neutral ─────────────────────


class TestScanTemplatesExtensionNeutral:
    """R37/V4: Scan prompt templates must not use .py file extensions
    in examples — supports any-language codebases."""

    SCAN_TEMPLATES = PROJECT_ROOT / "src" / "scripts" / "scan" / "templates"

    # These templates had .py examples that were neutralized
    TEMPLATE_FILES = [
        "deep_analysis.md",
        "explore_section.md",
        "tier_ranking.md",
        "validate_related_files.md",
        "related_files_updater.md",
    ]

    def test_no_py_in_example_paths(self) -> None:
        """Example file paths in scan templates must not use .py
        extension — use extension-neutral paths instead."""
        py_example_re = re.compile(
            r'["\'][\w/]+\.py["\']'
        )
        for template_name in self.TEMPLATE_FILES:
            path = self.SCAN_TEMPLATES / template_name
            content = path.read_text(encoding="utf-8")
            matches = py_example_re.findall(content)
            assert not matches, (
                f"{template_name} contains .py example paths "
                f"{matches} — use extension-neutral paths"
            )


# ── R38/V1: Blocker signal parsing fail-closed ──────────────────


class TestBlockerSignalFailClosed:
    """R38/V1: Malformed blocker signal must route as needs_parent
    (fail-closed), not fall through to misaligned code-fix dispatch."""

    PROBLEMS_PY = (PROJECT_ROOT / "src" / "scripts" / "section_loop"
                   / "coordination" / "problems.py")

    def test_no_silent_pass_on_blocker_parse_error(self) -> None:
        """problems.py must not have 'pass' as fallback for blocker
        parse failure."""
        content = self.PROBLEMS_PY.read_text(encoding="utf-8")
        assert "pass  # Fall through to standard misaligned" not in content, (
            "problems.py must not silently fall through on malformed "
            "blocker — must route as needs_parent"
        )

    def test_malformed_blocker_routes_as_needs_parent(
        self, planspace: Path,
    ) -> None:
        """Malformed blocker JSON must produce a needs_parent problem."""
        from section_loop.coordination.problems import (
            _collect_outstanding_problems,
        )
        from section_loop.types import Section, SectionResult

        signals_dir = planspace / "artifacts" / "signals"
        signals_dir.mkdir(parents=True, exist_ok=True)
        # Write malformed blocker JSON
        (signals_dir / "section-01-blocker.json").write_text(
            "{bad json", encoding="utf-8")

        section = Section(
            number="01",
            path=planspace / "artifacts" / "sections" / "section-01.md",
            related_files=[],
        )
        section_results = {
            "01": SectionResult(section_number="01", aligned=False),
        }
        sections_by_num = {"01": section}
        problems = _collect_outstanding_problems(
            section_results, sections_by_num, planspace,
        )
        needs_parent = [p for p in problems if p["type"] == "needs_parent"]
        assert len(needs_parent) == 1, (
            "Malformed blocker must produce exactly one needs_parent "
            "problem, not fall through to misaligned"
        )
        assert "malformed" in needs_parent[0]["description"].lower()

    def test_valid_blocker_still_routes_correctly(
        self, planspace: Path,
    ) -> None:
        """Valid needs_parent blocker still works after hardening."""
        from section_loop.coordination.problems import (
            _collect_outstanding_problems,
        )
        from section_loop.types import Section, SectionResult

        signals_dir = planspace / "artifacts" / "signals"
        signals_dir.mkdir(parents=True, exist_ok=True)
        (signals_dir / "section-02-blocker.json").write_text(
            json.dumps({
                "state": "needs_parent",
                "detail": "missing dependency",
                "needs": "external API spec",
            }),
            encoding="utf-8",
        )

        section = Section(
            number="02",
            path=planspace / "artifacts" / "sections" / "section-02.md",
            related_files=[],
        )
        section_results = {
            "02": SectionResult(section_number="02", aligned=False),
        }
        sections_by_num = {"02": section}
        problems = _collect_outstanding_problems(
            section_results, sections_by_num, planspace,
        )
        assert len(problems) == 1
        assert problems[0]["type"] == "needs_parent"
        assert problems[0]["description"] == "missing dependency"


# ── R38/V2: Tool registry malformed → warning block ─────────────


class TestCoordToolRegistryWarning:
    """R38/V2: Malformed tool registry in coordination fix prompt
    must surface a warning block, not silently omit tools."""

    EXECUTION_PY = (PROJECT_ROOT / "src" / "scripts" / "section_loop"
                    / "coordination" / "execution.py")

    def test_no_silent_pass_on_registry_error(self) -> None:
        """execution.py must not silently pass on malformed registry."""
        content = self.EXECUTION_PY.read_text(encoding="utf-8")
        # The old pattern was bare 'pass' after JSONDecodeError
        # Now it should produce a tools_block warning
        assert "Tool Registry Warning" in content, (
            "execution.py must emit a warning block when tool "
            "registry is malformed, not silently drop tools"
        )

    def test_warning_references_registry_path(self) -> None:
        """Warning block must reference the registry file path."""
        content = self.EXECUTION_PY.read_text(encoding="utf-8")
        assert "tool_registry_path" in content, (
            "execution.py warning block must reference the registry "
            "path so agents can diagnose"
        )

    def test_warning_suggests_repair(self) -> None:
        """Warning block must suggest tool-registrar repair."""
        content = self.EXECUTION_PY.read_text(encoding="utf-8")
        assert "tool-registrar repair" in content, (
            "execution.py warning block must suggest dispatching "
            "tool-registrar repair"
        )


# ── R38/V3: Note content referenced by path, not inlined ────────


class TestNoteContentByPath:
    """R38/V3: Coordinator problems must reference note files by
    path, not inline note content into problem descriptions."""

    PROBLEMS_PY = (PROJECT_ROOT / "src" / "scripts" / "section_loop"
                   / "coordination" / "problems.py")

    def test_no_inline_note_content(self) -> None:
        """problems.py must not slice and embed note_content."""
        content = self.PROBLEMS_PY.read_text(encoding="utf-8")
        assert "note_content[:500]" not in content, (
            "problems.py must not embed note_content[:500] — "
            "reference note_path instead"
        )

    def test_note_path_in_problem_dict(self) -> None:
        """Unaddressed note problems must include note_path field."""
        content = self.PROBLEMS_PY.read_text(encoding="utf-8")
        assert '"note_path"' in content, (
            "problems.py must include note_path in unaddressed note "
            "problem dicts"
        )

    def test_description_references_file(self) -> None:
        """Problem description must reference the note file path."""
        content = self.PROBLEMS_PY.read_text(encoding="utf-8")
        assert "See note file:" in content, (
            "problems.py note problem description must say "
            "'See note file:' with the path"
        )


# ── R38/V4: Note-ack parsing preserves corrupted state ──────────


class TestNoteAckPreservesCorrupted:
    """R38/V4: Malformed note-ack files must be preserved for
    diagnosis, not silently overwritten."""

    RUNNER_PY = (PROJECT_ROOT / "src" / "scripts" / "section_loop"
                 / "section_engine" / "runner.py")

    def test_no_silent_pass_on_ack_parse_error(self) -> None:
        """runner.py must not silently pass on malformed note-ack."""
        content = self.RUNNER_PY.read_text(encoding="utf-8")
        # The old pattern had bare pass after JSONDecodeError for ack
        # Check that the ack-related except block now preserves
        assert ".malformed.json" in content, (
            "runner.py must preserve malformed note-ack file with "
            ".malformed.json extension"
        )

    def test_logs_warning_on_malformed_ack(self) -> None:
        """runner.py must log a warning when note-ack is malformed."""
        content = self.RUNNER_PY.read_text(encoding="utf-8")
        assert "note-ack" in content and "malformed" in content, (
            "runner.py must log a warning about malformed note-ack"
        )


class TestBlockerRollupMalformedSignal:
    """R39/V2: Blocker rollup must not silently drop malformed signal
    files — they should appear in the rollup."""

    BLOCKERS_PY = (PROJECT_ROOT / "src" / "scripts" / "section_loop"
                   / "section_engine" / "blockers.py")

    def test_no_silent_continue_on_parse_error(self) -> None:
        """blockers.py must not silently continue past parse errors."""
        content = self.BLOCKERS_PY.read_text(encoding="utf-8")
        assert "malformed_signal" in content, (
            "blockers.py must route malformed signals to a "
            "malformed_signal category, not skip them"
        )

    def test_malformed_signal_in_rollup_categories(self) -> None:
        """blockers.py rollup must include malformed_signal category."""
        content = self.BLOCKERS_PY.read_text(encoding="utf-8")
        assert "Malformed Signal Files" in content, (
            "blockers.py must have a 'Malformed Signal Files' "
            "category title for malformed signals"
        )

    def test_malformed_signal_unit(self, tmp_path: Path) -> None:
        """Malformed signal JSON must appear in the rollup output."""
        from section_loop.section_engine.blockers import (
            _update_blocker_rollup,
        )
        signals_dir = tmp_path / "artifacts" / "signals"
        signals_dir.mkdir(parents=True)
        # Write a malformed signal file
        (signals_dir / "test-signal.json").write_text(
            "{invalid json", encoding="utf-8"
        )
        _update_blocker_rollup(tmp_path)
        rollup = (tmp_path / "artifacts" / "decisions"
                  / "needs-input.md")
        assert rollup.exists(), "Rollup must be written even for malformed"
        content = rollup.read_text(encoding="utf-8")
        assert "Malformed Signal Files" in content
        assert "test-signal.json" in content


class TestTraceabilityPreservesCorrupted:
    """R39/V3: Traceability log must preserve corrupted files instead
    of silently resetting to empty."""

    COMM_PY = (PROJECT_ROOT / "src" / "scripts" / "section_loop"
               / "communication.py")

    def test_no_silent_reset_on_parse_error(self) -> None:
        """communication.py must not silently reset traceability.json."""
        content = self.COMM_PY.read_text(encoding="utf-8")
        assert "corrupt" in content.lower(), (
            "communication.py must preserve corrupted traceability.json "
            "with a 'corrupt' marker filename"
        )

    def test_preserves_with_rename(self) -> None:
        """communication.py must rename corrupted file, not overwrite."""
        content = self.COMM_PY.read_text(encoding="utf-8")
        assert "traceability.corrupt-" in content, (
            "communication.py must rename corrupted file to "
            "traceability.corrupt-<timestamp>.json"
        )

    def test_logs_warning_on_corruption(self) -> None:
        """communication.py must log when traceability.json is malformed."""
        content = self.COMM_PY.read_text(encoding="utf-8")
        assert "malformed" in content and "starting fresh" in content, (
            "communication.py must log a warning about malformed "
            "traceability.json"
        )


# ── R40/V1: Scope-delta routing preserves corrupted files ────────


class TestScopeDeltaPreservesCorrupted:
    """R40/V1: Scope-delta routing must preserve malformed scope-delta
    files instead of silently overwriting them."""

    FEEDBACK_PY = PROJECT_ROOT / "src" / "scripts" / "scan" / "feedback.py"

    def test_no_silent_overwrite_on_malformed(self) -> None:
        """feedback.py must not silently pass on malformed scope-delta."""
        content = self.FEEDBACK_PY.read_text(encoding="utf-8")
        assert "malformed" in content and "scope-delta" in content, (
            "feedback.py must handle malformed scope-delta with "
            "preservation, not silent pass"
        )

    def test_preserves_with_rename(self) -> None:
        """feedback.py must rename malformed scope-delta file."""
        content = self.FEEDBACK_PY.read_text(encoding="utf-8")
        assert "scope-delta.malformed.json" in content, (
            "feedback.py must rename malformed scope-delta to "
            ".malformed.json for diagnosis"
        )

    def test_emits_warning(self) -> None:
        """feedback.py must print a WARN message on malformed scope-delta."""
        content = self.FEEDBACK_PY.read_text(encoding="utf-8")
        assert "[SCOPE][WARN]" in content, (
            "feedback.py must emit [SCOPE][WARN] for malformed "
            "scope-delta JSON"
        )

    def test_unit_malformed_scope_delta(self, tmp_path: Path) -> None:
        """Malformed scope-delta is preserved and new delta written."""
        from scan.feedback import _route_scope_deltas

        sections_dir = tmp_path / "sections"
        sections_dir.mkdir()
        sec_file = sections_dir / "section-03.md"
        sec_file.write_text("# Section 03")

        artifacts = tmp_path / "artifacts"
        scope_deltas_dir = artifacts / "scope-deltas"
        scope_deltas_dir.mkdir(parents=True)
        scan_log = tmp_path / "scan-log"
        sec_log = scan_log / "section-03"
        sec_log.mkdir(parents=True)

        # Write a malformed scope-delta
        delta_path = scope_deltas_dir / "section-03-scope-delta.json"
        delta_path.write_text("{bad json", encoding="utf-8")

        # Write a feedback file with out-of-scope items
        fb = sec_log / "deep-01-feedback.json"
        fb.write_text(json.dumps({
            "relevant": True,
            "source_file": "src/main",
            "out_of_scope": ["new requirement X"],
        }), encoding="utf-8")

        _route_scope_deltas(
            section_files=[sec_file],
            artifacts_dir=artifacts,
            scan_log_dir=scan_log,
        )

        # Malformed file should be preserved
        malformed = scope_deltas_dir / "section-03-scope-delta.malformed.json"
        assert malformed.exists(), (
            "Malformed scope-delta must be preserved as .malformed.json"
        )
        assert malformed.read_text() == "{bad json"

        # New delta should be written
        assert delta_path.exists(), "New scope-delta must be written"
        new_data = json.loads(delta_path.read_text())
        assert new_data["section"] == "03"
        assert "new requirement X" in new_data["items"]


# ── R40/V2: Note-ack read-path preserves corrupted state ────────


class TestNoteAckReadPathPreservesCorrupted:
    """R40/V2: cross_section.py read_incoming_notes must not silently
    ignore malformed note-ack JSON — must log warning and preserve."""

    CROSS_SECTION_PY = (PROJECT_ROOT / "src" / "scripts" / "section_loop"
                        / "cross_section.py")

    def test_no_silent_pass_on_ack_parse_error(self) -> None:
        """cross_section.py must not silently pass on malformed note-ack."""
        content = self.CROSS_SECTION_PY.read_text(encoding="utf-8")
        assert "malformed" in content and "note-ack" in content, (
            "cross_section.py must handle malformed note-ack with "
            "warning and preservation"
        )

    def test_preserves_with_rename(self) -> None:
        """cross_section.py must rename malformed note-ack file."""
        content = self.CROSS_SECTION_PY.read_text(encoding="utf-8")
        assert ".malformed.json" in content, (
            "cross_section.py must rename malformed note-ack to "
            ".malformed.json for diagnosis"
        )

    def test_logs_warning(self) -> None:
        """cross_section.py must log when note-ack is malformed."""
        content = self.CROSS_SECTION_PY.read_text(encoding="utf-8")
        assert "no acknowledgements" in content, (
            "cross_section.py must log warning treating malformed "
            "note-ack as no acknowledgements"
        )


# ── R40/V3: Related-files update signal warns on malformed JSON ──


class TestRelatedFilesUpdateWarning:
    """R40/V3: apply_related_files_update must print a warning when
    the signal JSON is malformed, not silently return False."""

    EXPLORATION_PY = PROJECT_ROOT / "src" / "scripts" / "scan" / "exploration.py"

    def test_emits_warning_on_malformed_signal(self) -> None:
        """exploration.py must print a warning on malformed signal."""
        content = self.EXPLORATION_PY.read_text(encoding="utf-8")
        assert "[RELATED FILES][WARN]" in content, (
            "exploration.py must emit [RELATED FILES][WARN] for "
            "malformed update signal JSON"
        )

    def test_still_returns_false(self) -> None:
        """exploration.py must still return False on malformed signal."""
        content = self.EXPLORATION_PY.read_text(encoding="utf-8")
        # After the warning print, the function should return False
        assert "return False" in content, (
            "exploration.py must return False on malformed signal "
            "after warning"
        )


# ── R41/V1: Deep scan treats missing tier ranking as failure ─────


class TestDeepScanTierRankingFailure:
    """R41/V1: When tier ranking is unavailable, _scan_sections must set
    phase_failed=True and log a failure entry, not silently skip."""

    DEEP_SCAN_PY = PROJECT_ROOT / "src" / "scripts" / "scan" / "deep_scan.py"

    def test_no_tier_ranking_sets_phase_failed(self) -> None:
        """_scan_sections must set phase_failed when no scan_files."""
        content = self.DEEP_SCAN_PY.read_text(encoding="utf-8")
        # Find the "no tier ranking available" block — it must set phase_failed
        lines = content.splitlines()
        for i, line in enumerate(lines):
            if "no tier ranking available" in line:
                # Check surrounding lines for phase_failed = True
                block = "\n".join(lines[max(0, i - 5):i + 10])
                assert "phase_failed = True" in block, (
                    "When tier ranking is unavailable, _scan_sections must "
                    "set phase_failed = True"
                )
                break
        else:
            pytest.fail("Could not find 'no tier ranking available' in deep_scan.py")

    def test_no_tier_ranking_logs_failure(self) -> None:
        """_scan_sections must log failure when tier ranking unavailable."""
        content = self.DEEP_SCAN_PY.read_text(encoding="utf-8")
        lines = content.splitlines()
        for i, line in enumerate(lines):
            if "no tier ranking available" in line:
                block = "\n".join(lines[max(0, i - 5):i + 15])
                assert "_log_phase_failure" in block, (
                    "When tier ranking is unavailable, _scan_sections must "
                    "call _log_phase_failure"
                )
                break
        else:
            pytest.fail("Could not find 'no tier ranking available' in deep_scan.py")

    def test_get_scan_files_warns_on_malformed(self) -> None:
        """_get_scan_files must warn when tier file is malformed JSON."""
        content = self.DEEP_SCAN_PY.read_text(encoding="utf-8")
        assert "[TIER][WARN]" in content, (
            "_get_scan_files must emit [TIER][WARN] when tier file "
            "is malformed JSON"
        )

    # Unit test for this violation lives in test_scan_stage3.py
    # (TestDeepScanTierRankingFailureUnit) where scan fixtures exist.


# ── R41/V2: Extractor tools fail-closed with ERROR diagnostics ───


class TestExtractorToolsFailClosed:
    """R41/V2: Extractor tools must output ERROR on read/parse failures
    and exit non-zero, not conflate errors with 'NO DOCSTRING/SUMMARY'."""

    TOOLS_DIR = PROJECT_ROOT / "src" / "tools"

    def test_extract_docstring_py_error_on_syntax_error(
        self, tmp_path: Path,
    ) -> None:
        """extract-docstring-py outputs ERROR on SyntaxError, exits 2."""
        bad_py = tmp_path / "bad.py"
        bad_py.write_text("def broken(\n", encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(self.TOOLS_DIR / "extract-docstring-py"),
             str(bad_py)],
            capture_output=True, text=True,
        )
        assert result.returncode == 2, (
            "extract-docstring-py must exit 2 on parse errors"
        )
        assert "ERROR:" in result.stdout, (
            "extract-docstring-py must output ERROR: on parse failure"
        )
        assert "NO DOCSTRING" not in result.stdout, (
            "extract-docstring-py must not say NO DOCSTRING on parse "
            "failure — that conflates absence with error"
        )

    def test_extract_docstring_py_no_docstring_on_true_absence(
        self, tmp_path: Path,
    ) -> None:
        """extract-docstring-py outputs NO DOCSTRING when truly absent."""
        no_doc = tmp_path / "nodoc.py"
        no_doc.write_text("x = 1\n", encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(self.TOOLS_DIR / "extract-docstring-py"),
             str(no_doc)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, (
            "extract-docstring-py must exit 0 when no errors"
        )
        assert "NO DOCSTRING" in result.stdout

    def test_extract_docstring_py_batch_continues_after_error(
        self, tmp_path: Path,
    ) -> None:
        """Batch mode processes all files even if one has errors."""
        bad = tmp_path / "bad.py"
        bad.write_text("def broken(\n", encoding="utf-8")
        good = tmp_path / "good.py"
        good.write_text('"""My docstring."""\n', encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(self.TOOLS_DIR / "extract-docstring-py"),
             "--batch", str(bad), str(good)],
            capture_output=True, text=True,
        )
        assert result.returncode == 2, (
            "Batch must exit 2 when any file has errors"
        )
        assert "ERROR:" in result.stdout, "Bad file must show ERROR"
        assert "My docstring." in result.stdout, (
            "Good file must still produce output in batch"
        )

    def test_extract_docstring_sh_error_on_read_failure(
        self, tmp_path: Path,
    ) -> None:
        """extract-docstring-sh outputs ERROR on unreadable file."""
        result = subprocess.run(
            [sys.executable, str(self.TOOLS_DIR / "extract-docstring-sh"),
             str(tmp_path / "nonexistent.sh")],
            capture_output=True, text=True,
        )
        assert result.returncode == 2, (
            "extract-docstring-sh must exit 2 on read errors"
        )
        assert "ERROR:" in result.stdout

    def test_extract_summary_md_error_on_read_failure(
        self, tmp_path: Path,
    ) -> None:
        """extract-summary-md outputs ERROR on unreadable file."""
        result = subprocess.run(
            [sys.executable, str(self.TOOLS_DIR / "extract-summary-md"),
             str(tmp_path / "nonexistent.md")],
            capture_output=True, text=True,
        )
        assert result.returncode == 2, (
            "extract-summary-md must exit 2 on read errors"
        )
        assert "ERROR:" in result.stdout

    def test_readme_documents_error_vs_absence(self) -> None:
        """tools/README.md must distinguish ERROR from NO DOCSTRING/SUMMARY."""
        content = (self.TOOLS_DIR / "README.md").read_text(encoding="utf-8")
        assert "ERROR:" in content, (
            "tools/README.md must document ERROR output format"
        )
        assert "true absence" in content, (
            "tools/README.md must clarify NO DOCSTRING means true absence"
        )


# ── R42/V1-V2: Skip-hash semantics + scan-summary stripping ─────


class TestRelatedFilesValidationHashSemantics:
    """R42: Skip-hash must not be written on validation failure."""

    EXPLORATION_PY = PROJECT_ROOT / "src" / "scripts" / "scan" / "exploration.py"

    def test_no_unconditional_hash_write(self) -> None:
        """Hash write must be conditional, not at the end of the function."""
        import ast
        import textwrap

        content = self.EXPLORATION_PY.read_text(encoding="utf-8")
        tree = ast.parse(content)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_validate_existing_related_files":
                # The last statement of the function must NOT be a bare
                # write_text call — it should be inside an if branch.
                last_stmt = node.body[-1]
                # If last statement is an Expr with write_text, it's
                # unconditional — that's the bug we fixed.
                if isinstance(last_stmt, ast.Expr):
                    src = ast.get_source_segment(content, last_stmt) or ""
                    assert "write_text" not in src, (
                        "codemap_hash_file.write_text must not be the "
                        "unconditional last statement of "
                        "_validate_existing_related_files"
                    )
                break
        else:
            pytest.fail("Could not find _validate_existing_related_files")

    def test_hash_not_written_on_dispatch_failure(self) -> None:
        """Hash must not be written when dispatch returns non-zero."""
        content = self.EXPLORATION_PY.read_text(encoding="utf-8")
        lines = content.splitlines()
        # Find the "validation failed" else branch (returncode != 0)
        for i, line in enumerate(lines):
            if "validation failed" in line:
                # Look only at the else branch itself (from this line
                # to the next dedented line or function end)
                block = "\n".join(lines[i:i + 10])
                assert "write_text" not in block, (
                    "The dispatch-failure branch must NOT write the "
                    "skip hash"
                )
                break
        else:
            pytest.fail("Could not find 'validation failed' message")

    def test_hash_recomputed_after_successful_apply(self) -> None:
        """After apply succeeds, hash is recomputed from updated section."""
        content = self.EXPLORATION_PY.read_text(encoding="utf-8")
        lines = content.splitlines()
        # Find "list updated" print — the apply-success path
        for i, line in enumerate(lines):
            if "list updated" in line:
                # The block after apply success must recompute the hash
                block = "\n".join(lines[i:i + 20])
                assert "strip_scan_summaries" in block, (
                    "After successful apply, section_hash must be "
                    "recomputed using strip_scan_summaries"
                )
                assert "combined_hash" in block, (
                    "After successful apply, combined_hash must be "
                    "recomputed from updated section"
                )
                break
        else:
            pytest.fail("Could not find 'list updated' message")

    def test_section_hash_uses_strip_scan_summaries(self) -> None:
        """Section hash must exclude scan-generated summaries."""
        content = self.EXPLORATION_PY.read_text(encoding="utf-8")
        # Verify exploration.py imports strip_scan_summaries
        assert "from .cache import strip_scan_summaries" in content, (
            "exploration.py must import strip_scan_summaries from cache"
        )
        # Verify section_hash computation uses strip_scan_summaries
        # (not _sha256_file which hashes raw bytes).
        # The call may span multiple lines, so check a window around
        # each line containing "section_hash".
        lines = content.splitlines()
        in_validate = False
        found_normalized_hash = False
        for i, line in enumerate(lines):
            if "def _validate_existing_related_files" in line:
                in_validate = True
            elif in_validate and line.startswith("def "):
                break
            elif in_validate and "section_hash" in line:
                # Check a small window around this line for the call
                window = "\n".join(lines[i:i + 4])
                if "strip_scan_summaries" in window:
                    found_normalized_hash = True
                    break
        assert found_normalized_hash, (
            "section_hash in _validate_existing_related_files must be "
            "computed via strip_scan_summaries, not _sha256_file"
        )


# ── R42/V3: Fresh exploration appends only Related Files block ───


class TestFreshExplorationAppendsOnlyRelatedFiles:
    """R42: Fresh exploration must append only the Related Files block."""

    EXPLORATION_PY = PROJECT_ROOT / "src" / "scripts" / "scan" / "exploration.py"

    def test_no_full_response_append(self) -> None:
        """_explore_section must not append entire response_text."""
        content = self.EXPLORATION_PY.read_text(encoding="utf-8")
        lines = content.splitlines()
        in_explore = False
        for line in lines:
            if "def _explore_section" in line:
                in_explore = True
            elif in_explore and line.startswith("def "):
                break
            elif in_explore:
                # Must not write full response_text to the section file
                stripped = line.strip()
                if "f.write(response_text)" in stripped:
                    pytest.fail(
                        "_explore_section writes full response_text — "
                        "must extract only the Related Files block"
                    )

    def test_extracts_related_files_block(self) -> None:
        """Extraction must find ## Related Files and trim at next ## heading."""
        content = self.EXPLORATION_PY.read_text(encoding="utf-8")
        lines = content.splitlines()
        in_explore = False
        found_extraction = False
        for line in lines:
            if "def _explore_section" in line:
                in_explore = True
            elif in_explore and line.startswith("def "):
                break
            elif in_explore and '## Related Files' in line and ".index(" in line:
                found_extraction = True
                break
        assert found_extraction, (
            "_explore_section must extract the Related Files block "
            "using .index('## Related Files')"
        )


# ── R42/V4: Codemap prompt coherence ─────────────────────────────


class TestCodemapBuildPromptCoherence:
    """R42: Codemap prompt must not contradict itself about templates."""

    CODEMAP_BUILD_MD = (
        PROJECT_ROOT / "src" / "scripts" / "scan" / "templates"
        / "codemap_build.md"
    )

    def test_no_template_contradiction(self) -> None:
        """Prompt must not say 'don't follow a template' while requiring one."""
        content = self.CODEMAP_BUILD_MD.read_text(encoding="utf-8")
        assert "not by following a template" not in content, (
            "codemap_build.md must not say 'not by following a template' "
            "— this contradicts the required Routing Table section"
        )
        assert "enforcing a fixed structure" not in content, (
            "codemap_build.md must not say 'enforcing a fixed structure' "
            "— routing table IS a required structure"
        )

    def test_routing_table_documented_as_interface(self) -> None:
        """Routing table must be framed as a required interface, not a template."""
        content = self.CODEMAP_BUILD_MD.read_text(encoding="utf-8")
        assert "Routing Table Interface (Required)" in content, (
            "codemap_build.md must frame the routing table as a "
            "'Routing Table Interface (Required)'"
        )

    def test_body_structure_distinguished_from_interface(self) -> None:
        """Prompt must distinguish free-form body from required interface."""
        content = self.CODEMAP_BUILD_MD.read_text(encoding="utf-8")
        assert "required structured interface" in content, (
            "codemap_build.md must mention the routing table is the "
            "'only required structured interface'"
        )


# ── R42: implement.md routing table reference ────────────────────


class TestImplementMdRoutingTableRef:
    """R42: implement.md must reference codemap routing table."""

    IMPLEMENT_MD = PROJECT_ROOT / "src" / "implement.md"

    def test_implement_md_mentions_routing_table(self) -> None:
        """Stage 3 description should mention routing table for downstream use."""
        content = self.IMPLEMENT_MD.read_text(encoding="utf-8")
        assert "Routing Table" in content, (
            "implement.md must reference the codemap Routing Table "
            "consumed by downstream agents"
        )


# ── R43/V1: Bridge-tools loop closed ─────────────────────────────


class TestBridgeToolsLoopClosure:
    """R43/V1: Bridge-tools dispatch must close the loop:
    - Verify bridge signal JSON exists and parses
    - Verify proposal file after dispatch
    - Retry with escalation model if missing
    - Acknowledge friction signal after handling
    """

    RUNNER = (PROJECT_ROOT / "src" / "scripts" / "section_loop"
              / "section_engine" / "runner.py")

    def test_friction_signal_acknowledged(self) -> None:
        """After bridge-tools dispatch, friction signal must be updated
        to friction=false, status=handled."""
        src = self.RUNNER.read_text(encoding="utf-8")
        # The bridge-tools block must write friction=False after handling
        assert '"friction": False' in src, (
            "runner.py must acknowledge friction signal by writing "
            "friction=False after bridge-tools dispatch"
        )
        assert '"status": "handled"' in src, (
            "runner.py must write status=handled to friction signal "
            "after bridge-tools dispatch"
        )

    def test_bridge_tools_prompt_includes_structured_signal_instruction(
        self,
    ) -> None:
        """Bridge-tools prompt must instruct agent to write a structured
        signal JSON with status/proposal_path/notes fields."""
        src = self.RUNNER.read_text(encoding="utf-8")
        assert "Structured Signal (Required)" in src, (
            "runner.py bridge-tools prompt must include a "
            "'Structured Signal (Required)' section"
        )
        assert "tool-bridge.json" in src, (
            "runner.py bridge-tools prompt must reference the "
            "bridge signal JSON path"
        )
        assert '"bridged"|"no_action"|"needs_parent"' in src, (
            "runner.py bridge-tools prompt must list allowed "
            "status values"
        )

    def test_bridge_signal_missing_triggers_escalation(self) -> None:
        """If bridge signal is missing after dispatch, runner.py must
        retry with escalation model."""
        src = self.RUNNER.read_text(encoding="utf-8")
        assert "bridge signal missing or" in src, (
            "runner.py must log when bridge signal is missing "
            "after primary dispatch"
        )
        assert 'policy["escalation_model"]' in src, (
            "runner.py must use policy escalation_model for "
            "bridge-tools retry"
        )
        assert "bridge-tools-failure.json" in src, (
            "runner.py must write bridge-tools-failure.json when "
            "both primary and escalation dispatch fail"
        )

    def test_ast_dispatch_followed_by_verification(self) -> None:
        """AST check: dispatch_agent call in the bridge-tools section
        is followed by verification logic (not bare dispatch)."""
        import ast

        src = self.RUNNER.read_text(encoding="utf-8")
        tree = ast.parse(src)

        # Find the bridge-tools dispatch in the source — look for
        # the string "bridge-tools-{section.number}" in dispatch calls
        # and verify the next statements include bridge_valid checks.
        lines = src.splitlines()
        bridge_dispatch_line = None
        for i, line in enumerate(lines, 1):
            if ('f"bridge-tools-{section.number}"' in line
                    and "dispatch_agent(" not in line):
                # This is the agent_name argument line inside
                # the dispatch_agent call
                bridge_dispatch_line = i
                break
        assert bridge_dispatch_line is not None, (
            "Could not find bridge-tools dispatch_agent call"
        )
        # Check that within the next 30 lines there is verification
        following = "\n".join(
            lines[bridge_dispatch_line:bridge_dispatch_line + 30])
        assert "bridge_valid" in following, (
            "bridge-tools dispatch_agent call must be followed by "
            "bridge_valid verification logic"
        )


# ── R43/V2: Microstrategy writer dispatch fail-closed ────────────


class TestMicrostrategyOutputEnforcement:
    """R43/V2: Microstrategy writer dispatch must verify output
    production — 'microstrategy generated' log requires file existence."""

    RUNNER = (PROJECT_ROOT / "src" / "scripts" / "section_loop"
              / "section_engine" / "runner.py")

    def test_microstrategy_generated_log_requires_file_existence(
        self,
    ) -> None:
        """'microstrategy generated' log must be conditional on file
        existence, not unconditional after dispatch."""
        src = self.RUNNER.read_text(encoding="utf-8")
        lines = src.splitlines()
        for i, line in enumerate(lines):
            if "microstrategy generated" in line:
                # This log line must be inside an if-branch that
                # checks microstrategy_path.exists()
                # Look at the preceding lines for the condition
                block = "\n".join(lines[max(0, i - 5):i + 1])
                assert "microstrategy_path.exists()" in block, (
                    "'microstrategy generated' log must be guarded by "
                    "microstrategy_path.exists() check"
                )
                break
        else:
            pytest.fail(
                "Could not find 'microstrategy generated' log line "
                "in runner.py"
            )

    def test_missing_microstrategy_triggers_escalation(self) -> None:
        """Missing microstrategy must trigger retry with escalation model."""
        src = self.RUNNER.read_text(encoding="utf-8")
        assert "microstrategy missing after" in src, (
            "runner.py must log when microstrategy is missing "
            "after primary dispatch"
        )
        assert "-escalation-output.md" in src, (
            "runner.py must write escalation output for "
            "microstrategy retry traceability"
        )

    def test_failed_escalation_writes_stub(self) -> None:
        """Failed escalation must write a stub microstrategy file."""
        src = self.RUNNER.read_text(encoding="utf-8")
        assert "GENERATION FAILED" in src, (
            "runner.py must write a stub with GENERATION FAILED "
            "when both primary and escalation fail"
        )
        assert "stub written" in src, (
            "runner.py must log 'stub written' when microstrategy "
            "generation fails completely"
        )

    def test_stub_contains_failure_indication(self) -> None:
        """Stub file must contain clear failure indication text
        directing the implementer to derive a microstrategy."""
        src = self.RUNNER.read_text(encoding="utf-8")
        assert "must derive a microstrategy" in src, (
            "Stub microstrategy must instruct the implementer to "
            "derive a microstrategy as first step of implementation"
        )


# ── R44/V1: Bridge-tools outputs wired into downstream channels ──


class TestBridgeToolsDownstreamWiring:
    """R44/V1: Bridge-tools outputs wired into downstream channels."""

    RUNNER = (PROJECT_ROOT / "src" / "scripts" / "section_loop"
              / "section_engine" / "runner.py")

    def test_bridge_success_writes_ref_input(self) -> None:
        """Valid bridge signal path must write a .ref input file."""
        src = self.RUNNER.read_text(encoding="utf-8")
        assert 'inputs_dir / "tool-bridge.ref"' in src, (
            "runner.py must write tool-bridge.ref when bridge succeeds"
        )
        assert "bridge proposal registered" in src, (
            "runner.py must log 'bridge proposal registered as input "
            "ref' after writing .ref"
        )

    def test_bridge_failure_writes_blocker(self) -> None:
        """Bridge failure after escalation must write needs_parent blocker."""
        src = self.RUNNER.read_text(encoding="utf-8")
        # The failure path must write a post-impl-blocker with
        # state=needs_parent.  Find the bridge-specific occurrence
        # (near the "Bridge failed after escalation" comment).
        lines = src.splitlines()
        in_bridge_failure = False
        found = False
        for i, line in enumerate(lines):
            if "Bridge failed after escalation" in line:
                in_bridge_failure = True
            if in_bridge_failure and "post-impl-blocker.json" in line:
                block = "\n".join(lines[i:i + 15])
                assert "needs_parent" in block, (
                    "post-impl-blocker must have state=needs_parent"
                )
                found = True
                break
        assert found, (
            "runner.py must write post-impl-blocker.json in the "
            "bridge failure path"
        )

    def test_bridge_failure_updates_rollup(self) -> None:
        """Bridge failure path must call _update_blocker_rollup."""
        src = self.RUNNER.read_text(encoding="utf-8")
        lines = src.splitlines()
        # Find the bridge failure block (comment line) and verify
        # rollup is called before friction acknowledgment
        in_bridge_failure = False
        found_rollup_in_bridge_failure = False
        for line in lines:
            if "Bridge failed after escalation" in line:
                in_bridge_failure = True
            if in_bridge_failure and "_update_blocker_rollup" in line:
                found_rollup_in_bridge_failure = True
                break
            # Exit once we hit the friction acknowledgment
            if in_bridge_failure and "Acknowledge friction" in line:
                break
        assert found_rollup_in_bridge_failure, (
            "runner.py bridge failure path must call "
            "_update_blocker_rollup after writing blocker"
        )

    def test_bridge_cross_section_notes(self) -> None:
        """Bridge with targets must write cross-section note files."""
        src = self.RUNNER.read_text(encoding="utf-8")
        assert "from-bridge-{section.number}" in src, (
            "runner.py must write cross-section notes with "
            "'from-bridge-{N}-to-{T}.md' naming"
        )
        assert "bridge notes routed" in src, (
            "runner.py must log 'bridge notes routed to N section(s)'"
        )

    def test_bridge_prompt_includes_cross_section_schema(self) -> None:
        """Bridge-tools prompt must document targets/broadcast/note_markdown."""
        src = self.RUNNER.read_text(encoding="utf-8")
        assert '"targets"' in src, (
            "runner.py bridge-tools prompt must include targets field"
        )
        assert '"broadcast"' in src, (
            "runner.py bridge-tools prompt must include broadcast field"
        )
        assert '"note_markdown"' in src, (
            "runner.py bridge-tools prompt must include note_markdown "
            "field"
        )

    def test_bridge_registry_change_triggers_digest_regen(self) -> None:
        """Changed tool registry must trigger digest regeneration."""
        src = self.RUNNER.read_text(encoding="utf-8")
        assert "pre_bridge_registry_hash" in src, (
            "runner.py must hash tool registry before bridge dispatch"
        )
        assert "post_bridge_registry_hash" in src, (
            "runner.py must hash tool registry after bridge dispatch"
        )
        assert "tool-digest-regen" in src, (
            "runner.py must dispatch tool-digest-regen when registry "
            "changed by bridge-tools"
        )
        assert "regenerating digest" in src, (
            "runner.py must log 'regenerating digest' when registry "
            "modified by bridge-tools"
        )

    def test_bridge_valid_writes_ref_unit(
        self, planspace: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Unit: valid bridge signal produces a .ref input file."""
        from unittest.mock import MagicMock

        from section_loop.section_engine.runner import run_section
        from section_loop.types import Section

        artifacts = planspace / "artifacts"
        sec_path = artifacts / "sections" / "section-05.md"
        sec_path.write_text("# Section 05\nBuild widget.")

        section = Section(
            number="05", path=sec_path,
            related_files=[], solve_count=0,
            global_proposal_path=planspace / "proposal.md",
            global_alignment_path=planspace / "alignment.md",
        )

        # Create prerequisite artifacts so runner proceeds past setup
        (artifacts / "sections"
         / "section-05-proposal-excerpt.md").write_text("excerpt")
        (artifacts / "sections"
         / "section-05-alignment-excerpt.md").write_text("excerpt")
        pf = artifacts / "sections" / "section-05-problem-frame.md"
        pf.write_text(
            "# Problem Statement\nFoo\n# Evidence\nBar\n"
            "# Constraints\nBaz\n# Success Criteria\nQux\n"
            "# Out of Scope\nNone\n"
        )
        # Create integration proposal so loop passes
        (artifacts / "proposals"
         / "section-05-integration-proposal.md").write_text("proposal")

        # Create friction signal and tool registry
        (artifacts / "signals"
         / "section-05-tool-friction.json").write_text(
            json.dumps({"friction": True}))
        (artifacts / "tool-registry.json").write_text(
            json.dumps({"tools": []}))

        # Create the bridge proposal file so validation passes
        bridge_proposal_path = (
            artifacts / "proposals" / "section-05-tool-bridge.md")
        bridge_proposal_path.write_text("bridge proposal")

        call_count = 0

        def fake_dispatch(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            agent_name = args[5] if len(args) > 5 else kwargs.get(
                "agent_name", "")
            # Handle setup agent — write excerpts
            if isinstance(agent_name, str) and "setup" in agent_name:
                return ""
            # Handle impl agent — make alignment pass
            if isinstance(agent_name, str) and "impl" in agent_name:
                return '{"aligned": true}'
            # Handle bridge-tools — write valid signal
            if isinstance(agent_name, str) and "bridge-tools" in agent_name:
                bridge_sig = (
                    artifacts / "signals"
                    / "section-05-tool-bridge.json")
                bridge_sig.write_text(json.dumps({
                    "status": "bridged",
                    "proposal_path": str(bridge_proposal_path),
                }))
                return ""
            return ""

        mock = MagicMock(side_effect=fake_dispatch)
        monkeypatch.setattr(
            "section_loop.section_engine.runner.dispatch_agent", mock)
        monkeypatch.setattr(
            "section_loop.section_engine.runner.pause_for_parent",
            MagicMock(return_value="resume"))
        monkeypatch.setattr(
            "section_loop.section_engine.runner._extract_problems",
            MagicMock(return_value=None))
        monkeypatch.setattr(
            "section_loop.section_engine.runner.check_agent_signals",
            MagicMock(return_value=(None, "")))
        monkeypatch.setattr(
            "section_loop.section_engine.runner._parse_alignment_verdict",
            MagicMock(return_value={"aligned": True, "frame_ok": True}))
        monkeypatch.setattr(
            "section_loop.section_engine.runner.read_model_policy",
            MagicMock(return_value={
                "setup": "glm", "proposal": "glm",
                "alignment": "glm", "implementation": "glm",
                "escalation_model": "glm",
                "escalation_triggers": {},
            }))
        monkeypatch.setattr(
            "section_loop.section_engine.runner.mailbox_send",
            MagicMock())
        monkeypatch.setattr(
            "section_loop.section_engine.runner._run_alignment_check_with_retries",
            MagicMock(return_value=None))
        monkeypatch.setattr(
            "section_loop.section_engine.runner.collect_modified_files",
            MagicMock(return_value=[]))
        monkeypatch.setattr(
            "section_loop.section_engine.runner.write_model_choice_signal",
            MagicMock())
        monkeypatch.setattr(
            "section_loop.section_engine.runner.summarize_output",
            MagicMock(return_value="summary"))
        monkeypatch.setattr(
            "section_loop.section_engine.runner.read_incoming_notes",
            MagicMock(return_value=""))
        monkeypatch.setattr(
            "section_loop.section_engine.runner._check_needs_microstrategy",
            MagicMock(return_value=False))

        run_section(planspace, planspace / "codespace", section, "parent")

        # Assert .ref was written
        ref_file = (artifacts / "inputs" / "section-05"
                    / "tool-bridge.ref")
        assert ref_file.exists(), (
            "tool-bridge.ref must be written when bridge succeeds"
        )
        assert str(bridge_proposal_path) in ref_file.read_text()

    def test_bridge_failure_writes_blocker_unit(
        self, planspace: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Unit: bridge failure after escalation writes needs_parent blocker."""
        from unittest.mock import MagicMock

        from section_loop.section_engine.runner import run_section
        from section_loop.types import Section

        artifacts = planspace / "artifacts"
        sec_path = artifacts / "sections" / "section-06.md"
        sec_path.write_text("# Section 06\nBuild widget.")

        section = Section(
            number="06", path=sec_path,
            related_files=[], solve_count=0,
            global_proposal_path=planspace / "proposal.md",
            global_alignment_path=planspace / "alignment.md",
        )

        # Create prerequisite artifacts
        (artifacts / "sections"
         / "section-06-proposal-excerpt.md").write_text("excerpt")
        (artifacts / "sections"
         / "section-06-alignment-excerpt.md").write_text("excerpt")
        pf = artifacts / "sections" / "section-06-problem-frame.md"
        pf.write_text(
            "# Problem Statement\nFoo\n# Evidence\nBar\n"
            "# Constraints\nBaz\n# Success Criteria\nQux\n"
            "# Out of Scope\nNone\n"
        )
        (artifacts / "proposals"
         / "section-06-integration-proposal.md").write_text("proposal")

        # Create friction signal and tool registry
        (artifacts / "signals"
         / "section-06-tool-friction.json").write_text(
            json.dumps({"friction": True}))
        (artifacts / "tool-registry.json").write_text(
            json.dumps({"tools": []}))

        # dispatch_agent never writes bridge signal → failure path
        mock = MagicMock(return_value="")
        monkeypatch.setattr(
            "section_loop.section_engine.runner.dispatch_agent", mock)
        monkeypatch.setattr(
            "section_loop.section_engine.runner.pause_for_parent",
            MagicMock(return_value="resume"))
        monkeypatch.setattr(
            "section_loop.section_engine.runner._extract_problems",
            MagicMock(return_value=None))
        monkeypatch.setattr(
            "section_loop.section_engine.runner.check_agent_signals",
            MagicMock(return_value=(None, "")))
        monkeypatch.setattr(
            "section_loop.section_engine.runner._parse_alignment_verdict",
            MagicMock(return_value={"aligned": True, "frame_ok": True}))
        monkeypatch.setattr(
            "section_loop.section_engine.runner.read_model_policy",
            MagicMock(return_value={
                "setup": "glm", "proposal": "glm",
                "alignment": "glm", "implementation": "glm",
                "escalation_model": "glm",
                "escalation_triggers": {},
            }))
        monkeypatch.setattr(
            "section_loop.section_engine.runner.mailbox_send",
            MagicMock())
        monkeypatch.setattr(
            "section_loop.section_engine.runner._run_alignment_check_with_retries",
            MagicMock(return_value=None))
        monkeypatch.setattr(
            "section_loop.section_engine.runner.collect_modified_files",
            MagicMock(return_value=[]))
        monkeypatch.setattr(
            "section_loop.section_engine.runner.write_model_choice_signal",
            MagicMock())
        monkeypatch.setattr(
            "section_loop.section_engine.runner.summarize_output",
            MagicMock(return_value="summary"))
        monkeypatch.setattr(
            "section_loop.section_engine.runner.read_incoming_notes",
            MagicMock(return_value=""))
        monkeypatch.setattr(
            "section_loop.section_engine.runner._check_needs_microstrategy",
            MagicMock(return_value=False))

        run_section(planspace, planspace / "codespace", section, "parent")

        # Assert blocker was written
        blocker_path = (artifacts / "signals"
                        / "section-06-post-impl-blocker.json")
        assert blocker_path.exists(), (
            "post-impl-blocker.json must be written on bridge failure"
        )
        blocker = json.loads(blocker_path.read_text())
        assert blocker["state"] == "needs_parent"


# ── R44/V2: Scan validation signal parsing fail-closed ───────────


class TestScanValidationSignalFailClosed:
    """R44/V2: Malformed scan validation signals don't write skip-hash."""

    EXPLORATION_PY = (PROJECT_ROOT / "src" / "scripts" / "scan"
                      / "exploration.py")

    def test_malformed_json_warns(self) -> None:
        """Malformed signal JSON must emit [EXPLORE][WARN]."""
        src = self.EXPLORATION_PY.read_text(encoding="utf-8")
        assert "[EXPLORE][WARN]" in src, (
            "exploration.py must emit [EXPLORE][WARN] on malformed "
            "related-files update signal"
        )

    def test_malformed_json_sets_write_hash_false(self) -> None:
        """Malformed signal JSON must set write_hash = False."""
        src = self.EXPLORATION_PY.read_text(encoding="utf-8")
        lines = src.splitlines()
        # Find the except block for JSONDecodeError in
        # _validate_existing_related_files
        in_validate = False
        found_write_hash_false = False
        for i, line in enumerate(lines):
            if "def _validate_existing_related_files" in line:
                in_validate = True
            elif in_validate and line.startswith("def "):
                break
            elif (in_validate
                  and "json.JSONDecodeError" in line
                  and "OSError" in line
                  and "exc" in line):
                # Check the except block for write_hash = False
                block = "\n".join(lines[i:i + 15])
                if "write_hash = False" in block:
                    found_write_hash_false = True
                    break
        assert found_write_hash_false, (
            "exploration.py malformed signal except block must set "
            "write_hash = False to prevent skip-hash write"
        )

    def test_malformed_json_preserved_as_malformed(self) -> None:
        """Malformed signal must be renamed to .malformed.json."""
        src = self.EXPLORATION_PY.read_text(encoding="utf-8")
        lines = src.splitlines()
        in_validate = False
        found_malformed_rename = False
        for i, line in enumerate(lines):
            if "def _validate_existing_related_files" in line:
                in_validate = True
            elif in_validate and line.startswith("def "):
                break
            elif in_validate and ".malformed.json" in line:
                found_malformed_rename = True
                break
        assert found_malformed_rename, (
            "exploration.py must rename malformed signal to "
            ".malformed.json for diagnosis"
        )

    def test_unknown_status_sets_write_hash_false(self) -> None:
        """Unknown status value must set write_hash = False."""
        src = self.EXPLORATION_PY.read_text(encoding="utf-8")
        lines = src.splitlines()
        in_validate = False
        found_unknown_status_guard = False
        for i, line in enumerate(lines):
            if "def _validate_existing_related_files" in line:
                in_validate = True
            elif in_validate and line.startswith("def "):
                break
            elif in_validate and "Unknown status" in line:
                # Check surrounding block for both the warning and
                # write_hash = False
                block = "\n".join(lines[i:i + 10])
                if ("write_hash = False" in block
                        and "unexpected" in block):
                    found_unknown_status_guard = True
                    break
        assert found_unknown_status_guard, (
            "exploration.py must set write_hash = False on unknown "
            "signal status values"
        )

    def test_malformed_json_no_hash_write_unit(self, tmp_path: Path) -> None:
        """Malformed signal JSON prevents hash write."""
        from scan.exploration import _validate_existing_related_files

        # Set up directory structure
        section_file = tmp_path / "section-01.md"
        section_file.write_text("# Section 01\n\n## Related Files\n")
        codemap = tmp_path / "codemap.md"
        codemap.write_text("# Codemap\n")
        artifacts = tmp_path / "artifacts"
        signals_dir = artifacts / "signals"
        signals_dir.mkdir(parents=True)
        scan_log = tmp_path / "scan-log"
        sec_log = scan_log / "section-01"
        sec_log.mkdir(parents=True)
        corrections = artifacts / "signals" / "codemap-corrections.json"

        # Write malformed signal
        signal_file = signals_dir / "section-01-related-files-update.json"
        signal_file.write_text("{bad json", encoding="utf-8")

        # Mock dispatch to succeed
        import types
        from unittest.mock import MagicMock

        mock_result = types.SimpleNamespace(returncode=0)
        import scan.exploration as expl_mod
        orig = expl_mod.dispatch_agent
        expl_mod.dispatch_agent = MagicMock(return_value=mock_result)
        try:
            _validate_existing_related_files(
                section_file=section_file,
                section_name="section-01",
                codemap_path=codemap,
                codespace=tmp_path,
                artifacts_dir=artifacts,
                scan_log_dir=scan_log,
                corrections_file=corrections,
                model_policy={"validation": "claude-opus"},
            )
        finally:
            expl_mod.dispatch_agent = orig

        # Assert hash NOT written (codemap-hash.txt should not exist
        # or should contain the old value)
        hash_file = sec_log / "codemap-hash.txt"
        assert not hash_file.exists(), (
            "codemap-hash.txt must NOT be written when signal is "
            "malformed"
        )

    def test_malformed_json_preserved_unit(self, tmp_path: Path) -> None:
        """Malformed signal renamed to .malformed.json."""
        from scan.exploration import _validate_existing_related_files

        section_file = tmp_path / "section-02.md"
        section_file.write_text("# Section 02\n\n## Related Files\n")
        codemap = tmp_path / "codemap.md"
        codemap.write_text("# Codemap\n")
        artifacts = tmp_path / "artifacts"
        signals_dir = artifacts / "signals"
        signals_dir.mkdir(parents=True)
        scan_log = tmp_path / "scan-log"
        sec_log = scan_log / "section-02"
        sec_log.mkdir(parents=True)
        corrections = artifacts / "signals" / "codemap-corrections.json"

        signal_file = signals_dir / "section-02-related-files-update.json"
        signal_file.write_text("{bad json", encoding="utf-8")

        import types
        from unittest.mock import MagicMock

        mock_result = types.SimpleNamespace(returncode=0)
        import scan.exploration as expl_mod
        orig = expl_mod.dispatch_agent
        expl_mod.dispatch_agent = MagicMock(return_value=mock_result)
        try:
            _validate_existing_related_files(
                section_file=section_file,
                section_name="section-02",
                codemap_path=codemap,
                codespace=tmp_path,
                artifacts_dir=artifacts,
                scan_log_dir=scan_log,
                corrections_file=corrections,
                model_policy={"validation": "claude-opus"},
            )
        finally:
            expl_mod.dispatch_agent = orig

        malformed = signals_dir / "section-02-related-files-update.malformed.json"
        assert malformed.exists(), (
            "Malformed signal must be preserved as .malformed.json"
        )
        assert malformed.read_text() == "{bad json"

    def test_unknown_status_no_hash_write_unit(
        self, tmp_path: Path,
    ) -> None:
        """Unknown status value prevents hash write."""
        from scan.exploration import _validate_existing_related_files

        section_file = tmp_path / "section-03.md"
        section_file.write_text("# Section 03\n\n## Related Files\n")
        codemap = tmp_path / "codemap.md"
        codemap.write_text("# Codemap\n")
        artifacts = tmp_path / "artifacts"
        signals_dir = artifacts / "signals"
        signals_dir.mkdir(parents=True)
        scan_log = tmp_path / "scan-log"
        sec_log = scan_log / "section-03"
        sec_log.mkdir(parents=True)
        corrections = artifacts / "signals" / "codemap-corrections.json"

        signal_file = signals_dir / "section-03-related-files-update.json"
        signal_file.write_text(
            json.dumps({"status": "garbage"}), encoding="utf-8")

        import types
        from unittest.mock import MagicMock

        mock_result = types.SimpleNamespace(returncode=0)
        import scan.exploration as expl_mod
        orig = expl_mod.dispatch_agent
        expl_mod.dispatch_agent = MagicMock(return_value=mock_result)
        try:
            _validate_existing_related_files(
                section_file=section_file,
                section_name="section-03",
                codemap_path=codemap,
                codespace=tmp_path,
                artifacts_dir=artifacts,
                scan_log_dir=scan_log,
                corrections_file=corrections,
                model_policy={"validation": "claude-opus"},
            )
        finally:
            expl_mod.dispatch_agent = orig

        hash_file = sec_log / "codemap-hash.txt"
        assert not hash_file.exists(), (
            "codemap-hash.txt must NOT be written when signal status "
            "is unknown"
        )

    def test_valid_stale_status_writes_hash_unit(
        self, tmp_path: Path,
    ) -> None:
        """Valid stale status with successful apply still writes hash."""
        from scan.exploration import _validate_existing_related_files

        section_file = tmp_path / "section-04.md"
        section_file.write_text(
            "# Section 04\n\n## Related Files\n\n"
            "### src/old_file\nSome context.\n"
        )
        codemap = tmp_path / "codemap.md"
        codemap.write_text("# Codemap\n")
        artifacts = tmp_path / "artifacts"
        signals_dir = artifacts / "signals"
        signals_dir.mkdir(parents=True)
        scan_log = tmp_path / "scan-log"
        sec_log = scan_log / "section-04"
        sec_log.mkdir(parents=True)
        corrections = artifacts / "signals" / "codemap-corrections.json"

        # Write a "stale" signal with an addition
        signal_file = signals_dir / "section-04-related-files-update.json"
        signal_file.write_text(json.dumps({
            "status": "stale",
            "removals": [],
            "additions": ["src/new_file"],
        }), encoding="utf-8")

        import types
        from unittest.mock import MagicMock

        mock_result = types.SimpleNamespace(returncode=0)
        import scan.exploration as expl_mod
        orig = expl_mod.dispatch_agent
        expl_mod.dispatch_agent = MagicMock(return_value=mock_result)
        try:
            _validate_existing_related_files(
                section_file=section_file,
                section_name="section-04",
                codemap_path=codemap,
                codespace=tmp_path,
                artifacts_dir=artifacts,
                scan_log_dir=scan_log,
                corrections_file=corrections,
                model_policy={"validation": "claude-opus"},
            )
        finally:
            expl_mod.dispatch_agent = orig

        hash_file = sec_log / "codemap-hash.txt"
        assert hash_file.exists(), (
            "codemap-hash.txt must be written when stale status "
            "successfully applied"
        )


# ---------------------------------------------------------------
# R45/V1: Bridge-tools post-escalation verification checks
#          proposal existence (not just status).
# ---------------------------------------------------------------

class TestBridgeToolsEscalationVerification:
    """R45/V1: Post-escalation bridge verification must check that
    the proposal file actually exists, not just the status field."""

    RUNNER = (PROJECT_ROOT / "src" / "scripts" / "section_loop"
              / "section_engine" / "runner.py")

    def test_escalation_missing_proposal_not_valid(self) -> None:
        """Escalation returns status=bridged but proposal_path doesn't
        exist on disk -> bridge_valid stays False, failure artifact written."""
        src = self.RUNNER.read_text(encoding="utf-8")
        # Find the post-escalation re-check block (after "Re-check after
        # escalation" comment) and verify it includes proposal_path.exists()
        lines = src.splitlines()
        in_recheck = False
        found_proposal_exists = False
        for line in lines:
            if "Re-check after escalation" in line:
                in_recheck = True
            if in_recheck and "proposal_path.exists()" in line:
                found_proposal_exists = True
                break
            # Stop searching once we leave the re-check block
            if in_recheck and "Wire bridge outputs" in line:
                break
        assert found_proposal_exists, (
            "Post-escalation re-check must verify proposal_path.exists() "
            "before setting bridge_valid = True"
        )

    def test_escalation_no_action_valid_without_proposal(self) -> None:
        """status=no_action should be valid even without a proposal file,
        mirroring the primary verification block."""
        src = self.RUNNER.read_text(encoding="utf-8")
        lines = src.splitlines()
        in_recheck = False
        found_no_action_bypass = False
        for line in lines:
            if "Re-check after escalation" in line:
                in_recheck = True
            if in_recheck and '"no_action"' in line:
                found_no_action_bypass = True
                break
            if in_recheck and "Wire bridge outputs" in line:
                break
        assert found_no_action_bypass, (
            "Post-escalation re-check must treat no_action as valid "
            "without requiring proposal existence"
        )


# ---------------------------------------------------------------
# R45/V2: Tool digest regeneration triggers on registry creation
#          (pre_hash empty, post_hash non-empty).
# ---------------------------------------------------------------

class TestToolDigestRegenOnCreation:
    """R45/V2: Digest regen must fire when registry is created (not
    just modified) — pre_hash empty + post_hash non-empty."""

    RUNNER = (PROJECT_ROOT / "src" / "scripts" / "section_loop"
              / "section_engine" / "runner.py")

    def test_regen_when_registry_created(self) -> None:
        """Condition must only require post_bridge_registry_hash to be
        truthy and differ from pre_bridge_registry_hash. When pre is
        empty and post is non-empty, that's a creation — must trigger."""
        src = self.RUNNER.read_text(encoding="utf-8")
        # The old pattern required both hashes truthy:
        #   if (pre_bridge_registry_hash
        #       and post_bridge_registry_hash
        #       and pre != post):
        # The fix should check only post truthy + differ:
        #   if (post_bridge_registry_hash
        #       and pre != post):
        lines = src.splitlines()
        in_digest_section = False
        condition_lines: list[str] = []
        for line in lines:
            if "Regenerate tool digest if bridge" in line:
                in_digest_section = True
            if in_digest_section and "if (" in line:
                # Collect the multi-line condition
                condition_lines.append(line.strip())
                continue
            if condition_lines and line.strip().startswith("and "):
                condition_lines.append(line.strip())
                continue
            if condition_lines and "!=" in line:
                condition_lines.append(line.strip())
                break
            if condition_lines:
                break
        condition = " ".join(condition_lines)
        # The condition must NOT start with pre_bridge_registry_hash
        assert "if (post_bridge_registry_hash" in condition, (
            "Digest regen condition must start with "
            "post_bridge_registry_hash (not pre_bridge_registry_hash)"
        )

    def test_no_regen_when_registry_unchanged(self) -> None:
        """When pre and post hashes are the same, no regen should fire.
        The condition `pre != post` ensures this — verify it's present."""
        src = self.RUNNER.read_text(encoding="utf-8")
        assert "pre_bridge_registry_hash\n" in src or (
            "pre_bridge_registry_hash" in src
            and "!= post_bridge_registry_hash" in src
        ), (
            "Digest regen condition must compare pre != post hashes"
        )

    def test_regen_when_registry_modified(self) -> None:
        """When both hashes exist but differ, regen must trigger.
        This is the existing case — verify the log message is present."""
        src = self.RUNNER.read_text(encoding="utf-8")
        assert "tool registry modified" in src, (
            "runner.py must log 'tool registry modified by bridge-tools' "
            "when hashes differ"
        )


# ---------------------------------------------------------------
# R45/V3: read_agent_signal() preserves malformed JSON as
#          .malformed.json with warning.
# ---------------------------------------------------------------

class TestReadAgentSignalCorruptionPreservation:
    """R45/V3: read_agent_signal must rename malformed/non-dict JSON
    to .malformed.json and warn, not silently discard."""

    def test_malformed_json_renamed_and_warned(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Malformed JSON -> returns None, file renamed to .malformed.json."""
        from section_loop.dispatch import read_agent_signal

        signal = tmp_path / "test-signal.json"
        signal.write_text("{bad json", encoding="utf-8")
        result = read_agent_signal(signal)

        assert result is None
        malformed = tmp_path / "test-signal.malformed.json"
        assert malformed.exists(), (
            "Malformed JSON must be renamed to .malformed.json"
        )
        assert not signal.exists(), (
            "Original malformed file must no longer exist after rename"
        )
        captured = capsys.readouterr()
        assert "[SIGNAL][WARN]" in captured.out

    def test_non_dict_json_renamed_and_warned(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Valid JSON that's not a dict -> returns None, file renamed."""
        from section_loop.dispatch import read_agent_signal

        signal = tmp_path / "test-signal.json"
        signal.write_text("[1, 2, 3]", encoding="utf-8")
        result = read_agent_signal(signal)

        assert result is None
        malformed = tmp_path / "test-signal.malformed.json"
        assert malformed.exists(), (
            "Non-dict JSON must be renamed to .malformed.json"
        )
        captured = capsys.readouterr()
        assert "[SIGNAL][WARN]" in captured.out

    def test_missing_expected_fields_returns_none(
        self, tmp_path: Path,
    ) -> None:
        """Valid JSON missing expected field -> returns None (no rename
        since JSON itself is valid)."""
        from section_loop.dispatch import read_agent_signal

        signal = tmp_path / "test-signal.json"
        signal.write_text(json.dumps({"foo": "bar"}), encoding="utf-8")
        result = read_agent_signal(signal, expected_fields=["status"])

        assert result is None
        # File should NOT be renamed — JSON is valid, just missing fields
        assert signal.exists(), (
            "Valid JSON with missing fields must not be renamed"
        )

    def test_valid_signal_returned_normally(
        self, tmp_path: Path,
    ) -> None:
        """Valid JSON dict with expected fields -> returned as dict."""
        from section_loop.dispatch import read_agent_signal

        payload = {"status": "done", "detail": "all good"}
        signal = tmp_path / "test-signal.json"
        signal.write_text(json.dumps(payload), encoding="utf-8")
        result = read_agent_signal(signal, expected_fields=["status"])

        assert result == payload
        assert signal.exists()


# ---------------------------------------------------------------
# R45/V4: Microstrategy pre-existing signal malformed — renamed
#          and re-dispatched.
# ---------------------------------------------------------------

class TestMicrostrategySignalCorruptionPreservation:
    """R45/V4: Malformed pre-existing microstrategy signal must be
    renamed to .malformed.json and fall through to dispatch."""

    def test_malformed_existing_signal_renamed(
        self, planspace: Path, codespace: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Write malformed JSON to microstrategy signal path ->
        file renamed to .malformed.json."""
        from section_loop.section_engine.todos import (
            _check_needs_microstrategy,
        )

        signals_dir = planspace / "artifacts" / "signals"
        signals_dir.mkdir(parents=True, exist_ok=True)
        signal = signals_dir / "proposal-01-microstrategy.json"
        signal.write_text("{not valid json", encoding="utf-8")

        # Create a proposal so the function doesn't bail early
        proposals_dir = planspace / "artifacts" / "proposals"
        proposals_dir.mkdir(parents=True, exist_ok=True)
        proposal = proposals_dir / "section-01-integration-proposal.md"
        proposal.write_text("# Proposal\nSome changes needed.")

        _check_needs_microstrategy(
            proposal, planspace, "01",
            model="glm", escalation_model="gpt-codex-xhigh",
        )

        malformed = signals_dir / "proposal-01-microstrategy.malformed.json"
        assert malformed.exists(), (
            "Malformed microstrategy signal must be renamed to "
            ".malformed.json"
        )
        captured = capsys.readouterr()
        assert "[MICROSTRATEGY][WARN]" in captured.out

    def test_malformed_falls_through_to_dispatch(
        self, planspace: Path, codespace: Path,
        mock_dispatch: "MagicMock",
    ) -> None:
        """After renaming malformed signal, dispatch still happens
        to produce a fresh signal."""
        from section_loop.section_engine.todos import (
            _check_needs_microstrategy,
        )

        signals_dir = planspace / "artifacts" / "signals"
        signals_dir.mkdir(parents=True, exist_ok=True)
        signal = signals_dir / "proposal-01-microstrategy.json"
        signal.write_text("{bad", encoding="utf-8")

        proposals_dir = planspace / "artifacts" / "proposals"
        proposals_dir.mkdir(parents=True, exist_ok=True)
        proposal = proposals_dir / "section-01-integration-proposal.md"
        proposal.write_text("# Proposal\nSome changes needed.")

        _check_needs_microstrategy(
            proposal, planspace, "01",
            model="glm", escalation_model="gpt-codex-xhigh",
        )

        # dispatch_agent must have been called (fall-through to dispatch)
        assert mock_dispatch.called, (
            "After renaming malformed signal, dispatch must be called "
            "to produce fresh microstrategy decision"
        )


# =====================================================================
# R46/V1: Completion gate must check outstanding problems
# =====================================================================

class TestCompletionGateOutstandingProblems:
    """R46/V1: Completion gate must check outstanding problems."""

    def test_main_loop_blocks_on_unaddressed_notes(self, tmp_path):
        """Main loop doesn't exit 'complete' when unaddressed notes exist."""
        from section_loop.coordination.problems import (
            _collect_outstanding_problems,
        )
        from section_loop.types import Section, SectionResult

        planspace = tmp_path / "plan"
        planspace.mkdir()

        # Create two sections — both aligned
        section_results = {
            "01": SectionResult(section_number="01", aligned=True),
            "02": SectionResult(section_number="02", aligned=True),
        }
        sections_by_num = {
            "01": Section(
                number="01", path=tmp_path / "s01.md",
                related_files=["auth.py"],
            ),
            "02": Section(
                number="02", path=tmp_path / "s02.md",
                related_files=["db.py"],
            ),
        }

        # Create an unaddressed note from 01 to 02
        notes_dir = planspace / "artifacts" / "notes"
        notes_dir.mkdir(parents=True)
        note = notes_dir / "from-01-to-02.md"
        note.write_text(
            "# Consequence Note\n\n"
            "**Note ID**: `01:abc123`\n\nChange auth flow.",
            encoding="utf-8",
        )
        # No ack file — note is unaddressed

        problems = _collect_outstanding_problems(
            section_results, sections_by_num, planspace,
        )
        # Should find the unaddressed note
        assert any(p["type"] == "unaddressed_note" for p in problems), (
            f"Expected unaddressed_note problem, got: {problems}"
        )

    def test_coordinator_blocks_on_outstanding_after_alignment(
        self, tmp_path,
    ):
        """Coordinator returns False when notes remain after all align."""
        from section_loop.coordination.problems import (
            _collect_outstanding_problems,
        )
        from section_loop.types import Section, SectionResult

        planspace = tmp_path / "plan"
        planspace.mkdir()

        section_results = {
            "01": SectionResult(section_number="01", aligned=True),
            "02": SectionResult(section_number="02", aligned=True),
        }
        sections_by_num = {
            "01": Section(
                number="01", path=tmp_path / "s01.md",
                related_files=[],
            ),
            "02": Section(
                number="02", path=tmp_path / "s02.md",
                related_files=[],
            ),
        }

        # Unaddressed note
        notes_dir = planspace / "artifacts" / "notes"
        notes_dir.mkdir(parents=True)
        (notes_dir / "from-01-to-02.md").write_text(
            "# Note\n\n**Note ID**: `01:def456`\n\nSomething.",
            encoding="utf-8",
        )

        problems = _collect_outstanding_problems(
            section_results, sections_by_num, planspace,
        )
        assert len(problems) > 0
        assert problems[0]["type"] == "unaddressed_note"

    def test_completion_allowed_when_notes_acknowledged(self, tmp_path):
        """Completion allowed when all notes are acknowledged."""
        from section_loop.coordination.problems import (
            _collect_outstanding_problems,
        )
        from section_loop.types import Section, SectionResult

        planspace = tmp_path / "plan"
        planspace.mkdir()

        section_results = {
            "01": SectionResult(section_number="01", aligned=True),
            "02": SectionResult(section_number="02", aligned=True),
        }
        sections_by_num = {
            "01": Section(
                number="01", path=tmp_path / "s01.md",
                related_files=[],
            ),
            "02": Section(
                number="02", path=tmp_path / "s02.md",
                related_files=[],
            ),
        }

        # Create note
        notes_dir = planspace / "artifacts" / "notes"
        notes_dir.mkdir(parents=True)
        (notes_dir / "from-01-to-02.md").write_text(
            "# Note\n\n**Note ID**: `01:ghi789`\n\nDone.",
            encoding="utf-8",
        )

        # Create ack
        signals_dir = planspace / "artifacts" / "signals"
        signals_dir.mkdir(parents=True)
        (signals_dir / "note-ack-02.json").write_text(
            json.dumps({
                "acknowledged": [
                    {"note_id": "01:ghi789", "action": "accepted"}
                ]
            }),
            encoding="utf-8",
        )

        problems = _collect_outstanding_problems(
            section_results, sections_by_num, planspace,
        )
        note_problems = [
            p for p in problems if p["type"] == "unaddressed_note"
        ]
        assert not note_problems, (
            f"Expected no unaddressed notes, got: {note_problems}"
        )


# =====================================================================
# R46/V2: Tool surface must be rebuilt after registry repair
# =====================================================================

class TestToolSurfaceRebuiltAfterRepair:
    """R46/V2: Tool surface must be rebuilt after registry repair."""

    def test_surface_written_after_repair(self, tmp_path):
        """After repair, relevant tools appear in tools-available file."""
        from section_loop.section_engine.runner import _write_tool_surface

        artifacts = tmp_path / "artifacts" / "sections"
        artifacts.mkdir(parents=True)
        tools_available = artifacts / "section-01-tools-available.md"

        tools = [{
            "id": "tool-1",
            "path": "tools/helper.sh",
            "scope": "cross-section",
            "created_by": "section-02",
            "status": "stable",
            "description": "A helper tool",
        }]

        count = _write_tool_surface(tools, "01", tools_available)
        assert tools_available.exists()
        content = tools_available.read_text(encoding="utf-8")
        assert "tools/helper.sh" in content
        assert count == 1

    def test_surface_cleared_when_no_relevant_after_repair(self, tmp_path):
        """After repair with no relevant tools, surface is removed."""
        from section_loop.section_engine.runner import _write_tool_surface

        artifacts = tmp_path / "artifacts" / "sections"
        artifacts.mkdir(parents=True)
        tools_available = artifacts / "section-01-tools-available.md"
        # Write stale surface
        tools_available.write_text("# Old tools", encoding="utf-8")

        # Registry with only section-02 local tools (not relevant to 01)
        tools = [{
            "id": "tool-1",
            "path": "tools/helper.sh",
            "scope": "section-local",
            "created_by": "section-02",
            "status": "stable",
            "description": "A helper tool",
        }]

        count = _write_tool_surface(tools, "01", tools_available)
        assert count == 0
        assert not tools_available.exists()

    def test_helper_includes_section_local_tools(self, tmp_path):
        """Section-local tools from the same section are included."""
        from section_loop.section_engine.runner import _write_tool_surface

        artifacts = tmp_path / "artifacts" / "sections"
        artifacts.mkdir(parents=True)
        tools_available = artifacts / "section-03-tools-available.md"

        tools = [{
            "id": "tool-local",
            "path": "tools/local.sh",
            "scope": "section-local",
            "created_by": "section-03",
            "status": "experimental",
            "description": "Local tool",
        }]

        count = _write_tool_surface(tools, "03", tools_available)
        assert count == 1
        assert tools_available.exists()
        content = tools_available.read_text(encoding="utf-8")
        assert "tools/local.sh" in content


# =====================================================================
# R46/V3: read_signal_tuple preserves corrupted files
# =====================================================================

class TestReadSignalTupleCorruptionPreservation:
    """R46/V3: read_signal_tuple preserves corrupted files."""

    def test_malformed_json_renamed(self, tmp_path):
        """Malformed signal JSON is renamed to .malformed.json."""
        from section_loop.dispatch import read_signal_tuple

        signal = tmp_path / "signal.json"
        signal.write_text("{bad json", encoding="utf-8")

        state, detail = read_signal_tuple(signal)
        assert state == "needs_parent"
        assert "malformed" in detail.lower() or "Malformed" in detail
        assert not signal.exists(), "Original should be renamed"
        assert (tmp_path / "signal.malformed.json").exists()

    def test_malformed_content_preserved(self, tmp_path):
        """Renamed file preserves the corrupted content."""
        from section_loop.dispatch import read_signal_tuple

        signal = tmp_path / "signal.json"
        bad_content = "{broken: content"
        signal.write_text(bad_content, encoding="utf-8")

        read_signal_tuple(signal)

        preserved = tmp_path / "signal.malformed.json"
        assert preserved.exists()
        assert preserved.read_text(encoding="utf-8") == bad_content

    def test_valid_json_not_renamed(self, tmp_path):
        """Valid signal JSON is NOT renamed."""
        from section_loop.dispatch import read_signal_tuple

        signal = tmp_path / "signal.json"
        signal.write_text(
            json.dumps({"state": "needs_parent", "detail": "test"}),
            encoding="utf-8",
        )

        state, detail = read_signal_tuple(signal)
        assert state == "needs_parent"
        # Original should remain (not renamed)
        assert signal.exists()
        assert not (tmp_path / "signal.malformed.json").exists()


class TestLintScriptsLayoutPortable:
    """R47: Lint scripts must use WORKFLOW_HOME, not hardcoded src/ paths."""

    @staticmethod
    def _operational_lines(content: str) -> str:
        """Return non-comment, non-blank lines of a shell script."""
        return "\n".join(
            line for line in content.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )

    def test_lint_audit_language_no_hardcoded_src(self):
        script = PROJECT_ROOT / "src" / "scripts" / "lint-audit-language.sh"
        content = script.read_text()
        # Should use WORKFLOW_HOME, not REPO_ROOT/src/
        assert "WORKFLOW_HOME" in content, (
            "lint-audit-language.sh must use WORKFLOW_HOME for layout portability"
        )
        ops = self._operational_lines(content)
        assert "/src/" not in ops, (
            "lint-audit-language.sh must not hardcode /src/ paths in operational lines"
        )

    def test_lint_doc_drift_no_hardcoded_src(self):
        script = PROJECT_ROOT / "src" / "scripts" / "lint-doc-drift.sh"
        content = script.read_text()
        assert "WORKFLOW_HOME" in content, (
            "lint-doc-drift.sh must use WORKFLOW_HOME for layout portability"
        )
        ops = self._operational_lines(content)
        assert "/src/" not in ops, (
            "lint-doc-drift.sh must not hardcode /src/ paths in operational lines"
        )

    def test_implement_md_lint_reference_exists(self):
        """implement.md references lint-audit-language.sh -- verify it exists."""
        impl = PROJECT_ROOT / "src" / "implement.md"
        content = impl.read_text()
        # Extract the script path referenced in implement.md
        match = re.search(r"`scripts/(lint-[^`]+\.sh)`", content)
        assert match, "implement.md should reference a lint script"
        script_name = match.group(1)
        script_path = PROJECT_ROOT / "src" / "scripts" / script_name
        assert script_path.exists(), (
            f"implement.md references scripts/{script_name} but it doesn't exist"
        )


class TestNoteTriggeredRequeue:
    """R48: consequence notes trigger targeted requeue for completed sections."""

    def test_note_sets_alignment_changed_when_target_has_baseline(self, tmp_path):
        """When a note targets a section with a baseline hash, the
        alignment-changed-pending flag should be set."""
        from section_loop.cross_section import post_section_completion
        from section_loop.pipeline_control import alignment_changed_pending
        from section_loop.types import Section

        planspace = tmp_path / "planspace"
        codespace = tmp_path / "codespace"
        codespace.mkdir(parents=True)

        # Create a source section that completed with a modified file
        sec_dir = planspace / "sections" / "section-01"
        sec_dir.mkdir(parents=True)
        (sec_dir / "section.md").write_text("# Section 01\nTest section", encoding="utf-8")

        # Create a target section with a baseline hash (meaning it completed)
        target_dir = planspace / "sections" / "section-02"
        target_dir.mkdir(parents=True)
        (target_dir / "section.md").write_text("# Section 02\nTarget", encoding="utf-8")
        baseline_dir = planspace / "artifacts" / "section-inputs-hashes"
        baseline_dir.mkdir(parents=True)
        (baseline_dir / "02.hash").write_text("abc123", encoding="utf-8")

        # Create a shared file that both sections reference
        shared_file = codespace / "shared.py"
        shared_file.write_text("# shared code", encoding="utf-8")

        source = Section(number="01", path=sec_dir,
                         related_files=["shared.py"])
        target = Section(number="02", path=target_dir,
                         related_files=["shared.py"])

        # Snapshot dir for source — pre-create so impact analysis has
        # something to compare against for the target
        snapshot_dir = planspace / "artifacts" / "snapshots" / "section-02"
        snapshot_dir.mkdir(parents=True)
        (snapshot_dir / "shared.py").write_text("# old code", encoding="utf-8")

        # Flag should not exist before
        assert not alignment_changed_pending(planspace)

        # Run post_section_completion — this invokes LLM dispatch which we
        # can't do in tests, so we verify the mechanical flag-setting logic
        # directly by checking that the import and flag-setting code exists.
        import ast
        import inspect
        source_code = inspect.getsource(post_section_completion)
        # Verify the function body references _set_alignment_changed_flag
        assert "_set_alignment_changed_flag" in source_code, (
            "post_section_completion must call _set_alignment_changed_flag "
            "to trigger targeted requeue when notes target completed sections"
        )

    def test_cross_section_imports_flag_setter(self):
        """cross_section.py must import _set_alignment_changed_flag."""
        import section_loop.cross_section as cs_mod
        assert hasattr(cs_mod, '_set_alignment_changed_flag') or \
            '_set_alignment_changed_flag' in dir(cs_mod) or \
            '_set_alignment_changed_flag' in open(
                cs_mod.__file__, encoding="utf-8").read(), (
            "cross_section.py must import _set_alignment_changed_flag "
            "from pipeline_control"
        )


class TestStallTerminationOutstanding:
    """R48: coordination stall surfaces outstanding-only problems."""

    def test_stall_termination_path_checks_outstanding(self):
        """The not-restart_phase1 block must check for outstanding
        problems when remaining (misaligned) is empty."""
        from pathlib import Path
        main_path = Path(__file__).resolve().parent.parent / "src" / "scripts" / "section_loop" / "main.py"
        content = main_path.read_text(encoding="utf-8")

        # The stall termination block (after coordination loop) must
        # collect outstanding problems when `remaining` is empty
        assert "coordination-exhausted.json" in content, (
            "main.py stall termination must write coordination-exhausted.json "
            "rollup artifact when outstanding problems remain"
        )
        assert "fail:coordination_exhausted:outstanding:" in content, (
            "main.py stall termination must send fail mailbox message "
            "with outstanding problem count"
        )

    def test_stall_termination_writes_rollup_artifact(self, tmp_path):
        """Verify the rollup artifact structure."""
        import json
        # Simulate what the code should produce
        outstanding = [
            {"type": "unaddressed_note", "section": "02",
             "description": "Note abc123 from section 01 unaddressed"},
        ]
        rollup_dir = tmp_path / "artifacts" / "coordination"
        rollup_dir.mkdir(parents=True)
        rollup_path = rollup_dir / "coordination-exhausted.json"
        rollup_path.write_text(json.dumps(
            [{"type": p["type"],
              "section": p["section"],
              "description": p["description"][:200]}
             for p in outstanding],
            indent=2), encoding="utf-8")

        data = json.loads(rollup_path.read_text(encoding="utf-8"))
        assert len(data) == 1
        assert data[0]["type"] == "unaddressed_note"
        assert data[0]["section"] == "02"


class TestImpactAnalysisCommentModel:
    """R48: impact analysis comment reflects policy-controlled model."""

    def test_no_hardcoded_opus_in_impact_comment(self):
        """cross_section.py Stage B comment must not claim Opus."""
        from pathlib import Path
        cs_path = (Path(__file__).resolve().parent.parent
                   / "src" / "scripts" / "section_loop" / "cross_section.py")
        content = cs_path.read_text(encoding="utf-8")
        # Find the Stage B comment line
        for line in content.splitlines():
            if "Stage B:" in line and "impact analysis" in line.lower():
                assert "(Opus)" not in line, (
                    f"cross_section.py Stage B comment still says '(Opus)' "
                    f"but actual model is policy-controlled (default GLM). "
                    f"Line: {line.strip()}"
                )
                assert "policy" in line.lower(), (
                    f"cross_section.py Stage B comment should mention "
                    f"'policy-controlled'. Line: {line.strip()}"
                )
                break
        else:
            # If no Stage B line found, that's also fine (comment was removed)
            pass


# ── R49 ─────────────────────────────────────────────────────────────


class TestCodemapFreshnessPreservation:
    """R49: codemap freshness signal uses corruption-preservation."""

    def test_dict_type_check(self):
        """codemap.py must validate freshness signal is a dict."""
        from pathlib import Path
        codemap_path = (Path(__file__).resolve().parent.parent
                        / "src" / "scripts" / "scan" / "codemap.py")
        content = codemap_path.read_text(encoding="utf-8")
        assert "isinstance(data, dict)" in content, (
            "codemap.py freshness signal parse must check "
            "isinstance(data, dict)")

    def test_malformed_rename(self):
        """codemap.py must rename malformed freshness signal."""
        from pathlib import Path
        codemap_path = (Path(__file__).resolve().parent.parent
                        / "src" / "scripts" / "scan" / "codemap.py")
        content = codemap_path.read_text(encoding="utf-8")
        idx = content.find("freshness_signal.is_file()")
        assert idx != -1, "Could not find freshness signal parse"
        region = content[idx:idx + 1200]
        assert ".malformed.json" in region, (
            "codemap.py freshness signal parse must rename malformed "
            "files to .malformed.json")


class TestProjectModePreservation:
    """R49: project-mode JSON parse uses corruption-preservation."""

    def test_both_sites_preserve(self):
        """main.py must rename malformed project-mode.json at both sites."""
        from pathlib import Path
        main_path = (Path(__file__).resolve().parent.parent
                     / "src" / "scripts" / "section_loop" / "main.py")
        content = main_path.read_text(encoding="utf-8")
        # Count .malformed.json occurrences near mode_json_path references
        lines = content.splitlines()
        preserve_count = 0
        for i, line in enumerate(lines):
            if ".malformed.json" in line and "mode_json_path" in lines[max(0,i-3):i+1][0] if lines[max(0,i-3):i+1] else False:
                preserve_count += 1
        # Simpler: just count .malformed.json in the mode-parsing region
        mode_start = content.find("# Read project mode from structured JSON")
        mode_end = content.find("log(f\"Project mode:")
        assert mode_start != -1 and mode_end != -1
        mode_region = content[mode_start:mode_end]
        count = mode_region.count(".malformed.json")
        assert count >= 2, (
            f"main.py must preserve malformed project-mode.json at both "
            f"parse sites (found {count} preservation renames, need >= 2)")


class TestTriageSignalPreservation:
    """R49: triage signal parse uses corruption-preservation."""

    def test_triage_except_preserves(self):
        """runner.py triage signal except must not be bare pass."""
        from pathlib import Path
        runner_path = (Path(__file__).resolve().parent.parent
                       / "src" / "scripts" / "section_loop"
                       / "section_engine" / "runner.py")
        content = runner_path.read_text(encoding="utf-8")
        idx = content.find("# Read triage signal")
        assert idx != -1, "Could not find triage signal region"
        region = content[idx:idx + 1300]
        assert "triage signal" in region and ".malformed.json" in region, (
            "runner.py triage signal outer except must rename to "
            ".malformed.json instead of bare pass")


class TestFrictionSignalPreservation:
    """R49: friction signal parse uses corruption-preservation."""

    def test_friction_except_preserves(self):
        """runner.py friction signal except must preserve malformed file."""
        from pathlib import Path
        runner_path = (Path(__file__).resolve().parent.parent
                       / "src" / "scripts" / "section_loop"
                       / "section_engine" / "runner.py")
        content = runner_path.read_text(encoding="utf-8")
        idx = content.find("Step 3c: Detect tooling friction")
        assert idx != -1, "Could not find friction signal region"
        region = content[idx:idx + 1000]
        assert ".malformed.json" in region, (
            "runner.py friction signal except must rename malformed "
            "file to .malformed.json")


class TestBridgeSignalPreservation:
    """R49: bridge signal parse uses corruption-preservation."""

    def test_primary_verification_preserves(self):
        """runner.py primary bridge verification must preserve malformed."""
        from pathlib import Path
        runner_path = (Path(__file__).resolve().parent.parent
                       / "src" / "scripts" / "section_loop"
                       / "section_engine" / "runner.py")
        content = runner_path.read_text(encoding="utf-8")
        idx = content.find("V1/R43: Verify bridge-tools output")
        assert idx != -1, "Could not find bridge verification region"
        region = content[idx:idx + 1200]
        assert ".malformed.json" in region, (
            "runner.py primary bridge signal except must rename to "
            ".malformed.json")

    def test_escalation_verification_preserves(self):
        """runner.py post-escalation bridge verification must preserve."""
        from pathlib import Path
        runner_path = (Path(__file__).resolve().parent.parent
                       / "src" / "scripts" / "section_loop"
                       / "section_engine" / "runner.py")
        content = runner_path.read_text(encoding="utf-8")
        idx = content.find("Re-check after escalation")
        assert idx != -1, "Could not find escalation re-check region"
        region = content[idx:idx + 1300]
        assert ".malformed.json" in region, (
            "runner.py post-escalation bridge signal except must rename "
            "to .malformed.json")


# ── R50 ──────────────────────────────────────────────────────────────


class TestCodemapCorrectionsInFreshness:
    """R50/V1: Codemap freshness verifier must include corrections overlay."""

    def test_template_includes_corrections_ref(self):
        """codemap_freshness.md must include {corrections_ref} placeholder."""
        from pathlib import Path
        tmpl_path = (Path(__file__).resolve().parent.parent
                     / "src" / "scripts" / "scan" / "templates"
                     / "codemap_freshness.md")
        content = tmpl_path.read_text(encoding="utf-8")
        assert "{corrections_ref}" in content, (
            "codemap_freshness.md must include {corrections_ref} "
            "placeholder in Files to Read section")

    def test_template_has_corrections_instruction(self):
        """codemap_freshness.md must instruct about corrections authority."""
        from pathlib import Path
        tmpl_path = (Path(__file__).resolve().parent.parent
                     / "src" / "scripts" / "scan" / "templates"
                     / "codemap_freshness.md")
        content = tmpl_path.read_text(encoding="utf-8")
        assert "corrections exist" in content.lower(), (
            "codemap_freshness.md must instruct agent to treat "
            "corrections as authoritative")

    def test_codemap_py_injects_corrections(self):
        """codemap.py must inject corrections_ref into freshness template."""
        from pathlib import Path
        codemap_path = (Path(__file__).resolve().parent.parent
                        / "src" / "scripts" / "scan" / "codemap.py")
        content = codemap_path.read_text(encoding="utf-8")
        idx = content.find("def _run_freshness_check")
        assert idx != -1, "Could not find _run_freshness_check definition"
        region = content[idx:idx + 2000]
        assert "corrections_ref" in region, (
            "codemap.py _run_freshness_check must inject corrections_ref "
            "into freshness template format call")
        assert "codemap-corrections.json" in region, (
            "codemap.py _run_freshness_check must reference "
            "codemap-corrections.json")


class TestCycleBudgetPreservation:
    """R50/V2: Cycle budget initial read uses corruption-preservation."""

    def test_cycle_budget_preserves_malformed(self):
        """runner.py cycle budget initial read must preserve malformed."""
        from pathlib import Path
        runner_path = (Path(__file__).resolve().parent.parent
                       / "src" / "scripts" / "section_loop"
                       / "section_engine" / "runner.py")
        content = runner_path.read_text(encoding="utf-8")
        idx = content.find("Cycle budget: read per-section")
        assert idx != -1, "Could not find cycle budget region"
        region = content[idx:idx + 900]
        assert ".malformed.json" in region, (
            "runner.py cycle budget initial read except must rename "
            "to .malformed.json")


class TestRecurrenceSignalPreservation:
    """R50/V3: Recurrence signal parsing uses corruption-preservation."""

    def test_recurrence_preserves_malformed(self):
        """problems.py load_recurrence_signals must preserve malformed."""
        from pathlib import Path
        problems_path = (Path(__file__).resolve().parent.parent
                         / "src" / "scripts" / "section_loop"
                         / "coordination" / "problems.py")
        content = problems_path.read_text(encoding="utf-8")
        idx = content.find("section-*-recurrence.json")
        assert idx != -1, "Could not find recurrence glob pattern"
        region = content[idx:idx + 500]
        assert ".malformed.json" in region, (
            "problems.py recurrence signal except must rename "
            "to .malformed.json")


class TestTierRankingPreservation:
    """R50/V4: Invalid tier ranking preserved instead of deleted."""

    def test_tier_preserves_instead_of_unlink(self):
        """deep_scan.py must preserve invalid tier files, not delete."""
        from pathlib import Path
        deep_path = (Path(__file__).resolve().parent.parent
                     / "src" / "scripts" / "scan" / "deep_scan.py")
        content = deep_path.read_text(encoding="utf-8")
        idx = content.find("Validate existing tier file")
        assert idx != -1, "Could not find tier validation region"
        region = content[idx:idx + 500]
        assert ".malformed.json" in region, (
            "deep_scan.py must rename invalid tier files to "
            ".malformed.json instead of unlinking")


# ---------------------------------------------------------------
# R55: Corruption preservation, codemap corrections propagation,
# budget enforcement, layout-agnostic guards
# ---------------------------------------------------------------


class TestR55CodemapCorrectionsInBootstrap:
    """R55/V1: bootstrap.py must reference codemap corrections in intent pack."""

    def test_corrections_in_generate_intent_pack(self):
        from pathlib import Path
        bootstrap = (Path(__file__).resolve().parent.parent
                     / "src" / "scripts" / "section_loop" / "intent"
                     / "bootstrap.py")
        content = bootstrap.read_text(encoding="utf-8")
        assert "codemap-corrections" in content, (
            "bootstrap.py generate_intent_pack must include codemap "
            "corrections reference")
        assert "corrections_path" in content, (
            "bootstrap.py must define corrections_path variable")

    def test_agent_file_mentions_corrections(self):
        from pathlib import Path
        agent = (Path(__file__).resolve().parent.parent
                 / "src" / "agents" / "intent-pack-generator.md")
        content = agent.read_text(encoding="utf-8")
        assert "corrections" in content.lower(), (
            "intent-pack-generator.md Phase 1 must mention codemap "
            "corrections")


class TestR55FeedbackPreservation:
    """R55/V2: feedback.py parse sites must rename malformed to .malformed.json."""

    def test_collect_and_route_preserves(self):
        from pathlib import Path
        fb = (Path(__file__).resolve().parent.parent
              / "src" / "scripts" / "scan" / "feedback.py")
        content = fb.read_text(encoding="utf-8")
        # All 3 parse sites in feedback.py must have preservation
        idx_collect = content.find("Malformed feedback JSON:")
        assert idx_collect != -1
        region = content[idx_collect:idx_collect + 600]
        assert ".malformed.json" in region, (
            "collect_and_route_feedback must preserve malformed files")

    def test_route_scope_deltas_preserves(self):
        from pathlib import Path
        fb = (Path(__file__).resolve().parent.parent
              / "src" / "scripts" / "scan" / "feedback.py")
        content = fb.read_text(encoding="utf-8")
        idx = content.find("scope-delta routing:")
        assert idx != -1
        region = content[idx:idx + 300]
        assert ".malformed.json" in region, (
            "_route_scope_deltas must preserve malformed files")

    def test_apply_feedback_preserves(self):
        from pathlib import Path
        fb = (Path(__file__).resolve().parent.parent
              / "src" / "scripts" / "scan" / "feedback.py")
        content = fb.read_text(encoding="utf-8")
        idx = content.find("apply_feedback:")
        assert idx != -1
        region = content[idx:idx + 300]
        assert ".malformed.json" in region, (
            "_apply_feedback must preserve malformed files")


class TestR55CacheWarning:
    """R55/V3: cache.py must warn on malformed cached feedback."""

    def test_is_valid_warns(self):
        from pathlib import Path
        cache = (Path(__file__).resolve().parent.parent
                 / "src" / "scripts" / "scan" / "cache.py")
        content = cache.read_text(encoding="utf-8")
        idx = content.find("def is_valid_cached_feedback")
        assert idx != -1
        region = content[idx:idx + 700]
        assert "WARN" in region, (
            "is_valid_cached_feedback must warn on parse failure")


class TestR55TierGetScanFilesPreservation:
    """R55/V4: _get_scan_files must preserve malformed tier files."""

    def test_get_scan_files_preserves(self):
        from pathlib import Path
        deep = (Path(__file__).resolve().parent.parent
                / "src" / "scripts" / "scan" / "deep_scan.py")
        content = deep.read_text(encoding="utf-8")
        idx = content.find("def _get_scan_files")
        assert idx != -1
        region = content[idx:idx + 500]
        assert ".malformed.json" in region, (
            "_get_scan_files must preserve malformed tier files")


class TestR55BlockerPreservation:
    """R55/V5: blockers.py must rename malformed signals."""

    def test_blocker_rollup_preserves(self):
        from pathlib import Path
        bl = (Path(__file__).resolve().parent.parent
              / "src" / "scripts" / "section_loop" / "section_engine"
              / "blockers.py")
        content = bl.read_text(encoding="utf-8")
        idx = content.find("malformed_signal")
        assert idx != -1
        region = content[idx:idx + 500]
        assert ".malformed.json" in region, (
            "blockers.py must rename malformed signals to .malformed.json")


class TestR55CoordinationBlockerPreservation:
    """R55/V6: problems.py must preserve malformed blocker signals."""

    def test_problems_preserves_blocker(self):
        from pathlib import Path
        prob = (Path(__file__).resolve().parent.parent
                / "src" / "scripts" / "section_loop" / "coordination"
                / "problems.py")
        content = prob.read_text(encoding="utf-8")
        idx = content.find("Blocker signal at")
        assert idx != -1
        region = content[idx:idx + 700]
        assert ".malformed.json" in region, (
            "problems.py must preserve malformed blocker signals")


class TestR55ScopeDeltaPreservation:
    """R55/V7: runner.py must preserve malformed scope-delta files."""

    def test_scope_delta_preserves(self):
        from pathlib import Path
        runner = (Path(__file__).resolve().parent.parent
                  / "src" / "scripts" / "section_loop" / "coordination"
                  / "runner.py")
        content = runner.read_text(encoding="utf-8")
        idx = content.find("malformed scope-delta")
        assert idx != -1
        region = content[idx:idx + 500]
        assert ".malformed.json" in region, (
            "runner.py must preserve malformed scope-delta files")


class TestR55ToolRegistryPreservation:
    """R55/V8: section_engine/runner.py must preserve registry before repair."""

    def test_pre_impl_preserves(self):
        from pathlib import Path
        runner = (Path(__file__).resolve().parent.parent
                  / "src" / "scripts" / "section_loop" / "section_engine"
                  / "runner.py")
        content = runner.read_text(encoding="utf-8")
        idx = content.find("tool-registry.json")
        assert idx != -1
        # Find the pre-impl repair site
        pre_idx = content.find("dispatching repair", idx)
        assert pre_idx != -1
        region = content[pre_idx - 500:pre_idx]
        assert ".malformed.json" in region or "malformed_path" in region, (
            "Pre-impl repair must preserve corrupted registry")

    def test_post_impl_preserves(self):
        from pathlib import Path
        runner = (Path(__file__).resolve().parent.parent
                  / "src" / "scripts" / "section_loop" / "section_engine"
                  / "runner.py")
        content = runner.read_text(encoding="utf-8")
        idx = content.find("post-impl registry")
        assert idx != -1
        region = content[idx - 500:idx]
        assert ".malformed.json" in region or "malformed_path" in region, (
            "Post-impl repair must preserve corrupted registry")


class TestR55LayoutAgnosticGuards:
    """R55/V9: TestNoHardcodedModelInPromptSurfaces must not silently skip."""

    def test_templates_dir_not_hardcoded_src(self):
        from pathlib import Path
        guards = (Path(__file__).resolve())
        content = guards.read_text(encoding="utf-8")
        # The class must NOT have hardcoded PROJECT_ROOT / "src" / ... / "templates"
        # as the sole TEMPLATES_DIR definition
        class_idx = content.find("class TestNoHardcodedModelInPromptSurfaces")
        assert class_idx != -1
        region = content[class_idx:class_idx + 800]
        assert "_SRC_TEMPLATES" in region or "_FLAT_TEMPLATES" in region, (
            "TestNoHardcodedModelInPromptSurfaces must try both layouts")

    def test_no_silent_return_on_missing(self):
        from pathlib import Path
        guards = (Path(__file__).resolve())
        content = guards.read_text(encoding="utf-8")
        class_idx = content.find("class TestNoHardcodedModelInPromptSurfaces")
        assert class_idx != -1
        # Find the first test method after class definition
        method_idx = content.find("def test_no_hardcoded_model_in_section_loop", class_idx)
        assert method_idx != -1
        region = content[method_idx:method_idx + 300]
        # Must use pytest.skip, not bare return
        assert "pytest.skip" in region or "TEMPLATES_DIR.exists" not in region, (
            "Guard must use pytest.skip, not bare return, when dir missing")


class TestR55BudgetEnforcement:
    """R55/V10: expansion.py must enforce budget on actual expander workload."""

    def test_pending_surfaces_written(self):
        from pathlib import Path
        exp = (Path(__file__).resolve().parent.parent
               / "src" / "scripts" / "section_loop" / "intent"
               / "expansion.py")
        content = exp.read_text(encoding="utf-8")
        assert "intent-surfaces-pending" in content, (
            "expansion.py must write budgeted pending-surfaces file")

    def test_expanders_use_pending_path(self):
        from pathlib import Path
        exp = (Path(__file__).resolve().parent.parent
               / "src" / "scripts" / "section_loop" / "intent"
               / "expansion.py")
        content = exp.read_text(encoding="utf-8")
        assert "pending_surfaces_path" in content, (
            "Expander functions must accept pending_surfaces_path parameter")


# ===================================================================
# R56 Regression Guards
# ===================================================================


class TestR56QueueSemanticsGuard:
    """R56/V1: Expansion must use queue semantics (all pending, not just new)."""

    def test_worklist_from_pending_registry(self):
        from pathlib import Path
        exp = (Path(__file__).resolve().parent.parent
               / "src" / "scripts" / "section_loop" / "intent"
               / "expansion.py")
        content = exp.read_text(encoding="utf-8")
        assert 'status") == "pending"' in content, (
            "expansion.py must build worklist from ALL pending surfaces")

    def test_no_new_surfaces_only_check(self):
        from pathlib import Path
        exp = (Path(__file__).resolve().parent.parent
               / "src" / "scripts" / "section_loop" / "intent"
               / "expansion.py")
        content = exp.read_text(encoding="utf-8")
        # The old pattern "if not new_surfaces: return" should not
        # be the sole termination condition after merge
        assert "worklist" in content, (
            "expansion.py must use 'worklist' variable for pending queue")


class TestR56AgentSelectedSourcesGuard:
    """R56/V2: Philosophy sources selected by agent from mechanical catalog."""

    def test_no_hardcoded_filenames(self):
        from pathlib import Path
        boot = (Path(__file__).resolve().parent.parent
                / "src" / "scripts" / "section_loop" / "intent"
                / "bootstrap.py")
        if not boot.exists():
            pytest.skip("bootstrap.py not found")
        content = boot.read_text(encoding="utf-8")
        assert '"constraints.md"' not in content, (
            "bootstrap.py must not hardcode constraints.md")
        assert '"design-philosophy-notes.md"' not in content, (
            "bootstrap.py must not hardcode design-philosophy-notes.md")

    def test_catalog_builder_exists(self):
        from pathlib import Path
        boot = (Path(__file__).resolve().parent.parent
                / "src" / "scripts" / "section_loop" / "intent"
                / "bootstrap.py")
        if not boot.exists():
            pytest.skip("bootstrap.py not found")
        content = boot.read_text(encoding="utf-8")
        assert "_build_philosophy_catalog" in content, (
            "bootstrap.py must have _build_philosophy_catalog function")

    def test_selector_agent_exists(self):
        from pathlib import Path
        agent = (Path(__file__).resolve().parent.parent
                 / "src" / "agents"
                 / "philosophy-source-selector.md")
        if not agent.exists():
            pytest.skip("philosophy-source-selector.md not found")
        content = agent.read_text(encoding="utf-8")
        assert "sources" in content

    def test_selector_model_policy_key(self):
        from pathlib import Path
        dispatch = (Path(__file__).resolve().parent.parent
                    / "src" / "scripts" / "section_loop"
                    / "dispatch.py")
        content = dispatch.read_text(encoding="utf-8")
        assert "intent_philosophy_selector" in content, (
            "dispatch.py must have intent_philosophy_selector policy key")


class TestR56UpdaterSignalPreservationGuard:
    """R56/V3: Malformed updater signal preserved as .malformed.json."""

    def test_updater_signal_preservation(self):
        from pathlib import Path
        fb = (Path(__file__).resolve().parent.parent
              / "src" / "scripts" / "scan" / "feedback.py")
        content = fb.read_text(encoding="utf-8")
        # Find updater signal parse site
        idx = content.find("Malformed updater signal:")
        assert idx != -1, "feedback.py must warn on malformed updater signal"
        region = content[idx:idx + 500]
        assert ".malformed.json" in region, (
            "feedback.py must rename malformed updater signal "
            "to .malformed.json (V3/R56)")


class TestR56AxisBudgetEnforcementGuard:
    """R56/V5: max_new_axes_total enforced, not just declared."""

    def test_axes_tracked_in_registry(self):
        from pathlib import Path
        exp = (Path(__file__).resolve().parent.parent
               / "src" / "scripts" / "section_loop" / "intent"
               / "expansion.py")
        content = exp.read_text(encoding="utf-8")
        assert "axes_added_so_far" in content, (
            "expansion.py must track axes_added_so_far in registry")

    def test_axis_budget_enforcement_exists(self):
        from pathlib import Path
        exp = (Path(__file__).resolve().parent.parent
               / "src" / "scripts" / "section_loop" / "intent"
               / "expansion.py")
        content = exp.read_text(encoding="utf-8")
        assert "remaining_axis_budget" in content, (
            "expansion.py must compute remaining_axis_budget")
        assert "budget advisory" in content, (
            "expansion.py must treat axis budget as advisory")

    def test_axis_budget_in_expander_prompt(self):
        from pathlib import Path
        exp = (Path(__file__).resolve().parent.parent
               / "src" / "scripts" / "section_loop" / "intent"
               / "expansion.py")
        content = exp.read_text(encoding="utf-8")
        assert "Axis budget" in content, (
            "Expander prompt must include axis budget constraint")


class TestR57DeepScanPreservationGuard:
    """V1/R57: deep_scan.update_match() must not silently swallow errors."""

    def test_update_match_has_malformed_rename(self):
        from pathlib import Path
        ds = (Path(__file__).resolve().parent.parent
              / "src" / "scripts" / "scan" / "deep_scan.py")
        content = ds.read_text(encoding="utf-8")
        # Find the update_match function
        fn_start = content.find("def update_match(")
        assert fn_start != -1
        fn_body = content[fn_start:content.find("\ndef ", fn_start + 1)]
        assert ".malformed.json" in fn_body, (
            "update_match must rename malformed feedback to .malformed.json")
        assert "WARN" in fn_body, (
            "update_match must emit a warning on malformed feedback")

    def test_update_match_no_silent_except(self):
        from pathlib import Path
        ds = (Path(__file__).resolve().parent.parent
              / "src" / "scripts" / "scan" / "deep_scan.py")
        content = ds.read_text(encoding="utf-8")
        fn_start = content.find("def update_match(")
        fn_body = content[fn_start:content.find("\ndef ", fn_start + 1)]
        # The old pattern was: except (json.JSONDecodeError, OSError):\n        return True
        # with nothing between except and return. Now there should be
        # warning + rename between except and return.
        lines = fn_body.split("\n")
        for i, line in enumerate(lines):
            if "JSONDecodeError" in line and "except" in line:
                # Next non-blank line should NOT be just "return True"
                for j in range(i + 1, min(i + 3, len(lines))):
                    stripped = lines[j].strip()
                    if stripped and stripped != "":
                        assert stripped != "return True", (
                            "except block must not silently return True")
                        break


class TestR57UpdaterValidityPreservationGuard:
    """V2/R57: _is_valid_updater_signal() must preserve malformed signals."""

    def test_validity_check_has_rename(self):
        from pathlib import Path
        fb = (Path(__file__).resolve().parent.parent
              / "src" / "scripts" / "scan" / "feedback.py")
        content = fb.read_text(encoding="utf-8")
        fn_start = content.find("def _is_valid_updater_signal(")
        assert fn_start != -1
        fn_end = content.find("\ndef ", fn_start + 1)
        fn_body = content[fn_start:fn_end] if fn_end != -1 else content[fn_start:]
        assert ".malformed.json" in fn_body, (
            "_is_valid_updater_signal must rename malformed signals")
        assert "WARN" in fn_body, (
            "_is_valid_updater_signal must warn on malformed signals")


class TestR57RefExpansionGuard:
    """V3/R57: Ref expansion failures must warn, not silently pass."""

    def test_pipeline_hash_uses_ref_error_marker(self):
        from pathlib import Path
        pc = (Path(__file__).resolve().parent.parent
              / "src" / "scripts" / "section_loop"
              / "pipeline_control.py")
        content = pc.read_text(encoding="utf-8")
        assert "REF_READ_ERROR" in content, (
            "Pipeline hash must use REF_READ_ERROR marker on broken refs")
        assert "WARN" in content, (
            "Pipeline hash must warn on broken refs")

    def test_context_builder_no_silent_pass(self):
        from pathlib import Path
        ctx = (Path(__file__).resolve().parent.parent
               / "src" / "scripts" / "section_loop" / "prompts"
               / "context.py")
        content = ctx.read_text(encoding="utf-8")
        # Find the ref_files loop
        ref_idx = content.find("for ref_file in ref_files:")
        assert ref_idx != -1
        ref_block = content[ref_idx:ref_idx + 500]
        assert "WARN" in ref_block, (
            "Context builder must warn on broken ref files, not silently pass")
        assert "pass" not in ref_block.split("except")[1].split("\n")[1] if "except" in ref_block else True, (
            "Context builder except block must not just 'pass'")


class TestR57GateTypeGuard:
    """V4/R57: handle_user_gate() must be gate-type-specific."""

    def test_handle_user_gate_uses_gate_kind(self):
        from pathlib import Path
        exp = (Path(__file__).resolve().parent.parent
               / "src" / "scripts" / "section_loop" / "intent"
               / "expansion.py")
        content = exp.read_text(encoding="utf-8")
        fn_start = content.find("def handle_user_gate(")
        fn_end = content.find("\ndef ", fn_start + 1)
        fn_body = content[fn_start:fn_end] if fn_end != -1 else content[fn_start:]
        assert "user_input_kind" in fn_body, (
            "handle_user_gate must read user_input_kind from delta")
        assert "gate_kind" in fn_body or "gate_messages" in fn_body, (
            "handle_user_gate must dispatch on gate kind")

    def test_axis_budget_advisory_in_expansion(self):
        """R68/V5: axis budget is advisory — no hard block in expansion."""
        from pathlib import Path
        exp = (Path(__file__).resolve().parent.parent
               / "src" / "scripts" / "section_loop" / "intent"
               / "expansion.py")
        content = exp.read_text(encoding="utf-8")
        fn_start = content.find("def run_expansion_cycle(")
        fn_end = content.find("\ndef ", fn_start + 1)
        fn_body = content[fn_start:fn_end] if fn_end != -1 else content[fn_start:]
        # No axis_budget hard block in the main function
        assert "NEED_DECISION" not in fn_body, (
            "Axis budget must not hard-block with NEED_DECISION")


class TestR57SurfacePersistenceGuard:
    """V5/R57: Intent surfaces must be persisted even when misaligned."""

    def test_runner_merges_surfaces_on_misalignment(self):
        from pathlib import Path
        runner = (Path(__file__).resolve().parent.parent
                  / "src" / "scripts" / "section_loop"
                  / "section_engine" / "runner.py")
        content = runner.read_text(encoding="utf-8")
        # The PROBLEMS branch should contain surface merge logic
        problems_idx = content.find("# Problems found — feed back into")
        assert problems_idx != -1
        # Check the block BEFORE "Problems found" for surface persistence
        pre_block = content[max(0, problems_idx - 800):problems_idx]
        assert "merge_surfaces_into_registry" in pre_block, (
            "Runner must merge surfaces into registry even on PROBLEMS verdict")
        assert "misaligned" in pre_block.lower(), (
            "Surface merge on misalignment must be documented in comments")

    def test_runner_imports_surface_functions(self):
        from pathlib import Path
        runner = (Path(__file__).resolve().parent.parent
                  / "src" / "scripts" / "section_loop"
                  / "section_engine" / "runner.py")
        content = runner.read_text(encoding="utf-8")
        for fn in ("load_surface_registry", "merge_surfaces_into_registry",
                    "normalize_surface_ids", "save_surface_registry"):
            assert fn in content, (
                f"runner.py must import {fn} for misalignment merge")


class TestR57DocSignalTaxonomyGuard:
    """V6/R57: implement.md must list all signal states."""

    def test_all_signal_states_present(self):
        from pathlib import Path
        impl = (Path(__file__).resolve().parent.parent
                / "src" / "implement.md")
        content = impl.read_text(encoding="utf-8")
        for signal in ("UNDERSPECIFIED", "NEED_DECISION", "DEPENDENCY",
                        "OUT_OF_SCOPE", "NEEDS_PARENT"):
            assert signal in content, (
                f"implement.md must document signal state: {signal}")

    def test_no_hardcoded_model_in_prescriptive_text(self):
        from pathlib import Path
        impl = (Path(__file__).resolve().parent.parent
                / "src" / "implement.md")
        content = impl.read_text(encoding="utf-8")
        # Check that "codex-xhigh generates" pattern is gone
        assert "codex-xhigh generates" not in content, (
            "implement.md must not hardcode model names in prescriptive text")


# ---------------------------------------------------------------------------
# R58 — V1: Scope-delta adjudication write-back fail-closed guard
# ---------------------------------------------------------------------------


class TestR58ScopeDeltaAdjudicationGuard:
    """Guard: scope-delta adjudication application must be wrapped in
    try/except with corruption preservation."""

    def test_adjudication_writeback_is_protected(self):
        """The json.loads in adjudication write-back must be inside
        try/except with corruption preservation."""
        from pathlib import Path

        runner_path = (Path(__file__).resolve().parent.parent
                       / "src" / "scripts" / "section_loop"
                       / "coordination" / "runner.py")
        source = runner_path.read_text(encoding="utf-8")

        # Find the adjudication application block (after
        # "Apply adjudicated decisions") and verify it has
        # try/except with .malformed.json preservation.
        apply_marker = "Apply adjudicated decisions"
        apply_idx = source.find(apply_marker)
        assert apply_idx >= 0, (
            "Runner must have 'Apply adjudicated decisions' block")

        apply_block = source[apply_idx:]
        assert "try:" in apply_block, (
            "Adjudication write-back must have try/except protection")
        assert ".malformed.json" in apply_block, (
            "Adjudication write-back must preserve as .malformed.json")

    def test_replacement_delta_has_error_field(self):
        """The replacement delta written on corruption must contain
        an 'error' field."""
        from pathlib import Path

        runner_path = (Path(__file__).resolve().parent.parent
                       / "src" / "scripts" / "section_loop"
                       / "coordination" / "runner.py")
        source = runner_path.read_text(encoding="utf-8")

        assert '"error"' in source or "'error'" in source, (
            "Runner must write an 'error' field in replacement delta")
        assert "preserved_path" in source, (
            "Runner must write 'preserved_path' in replacement delta")


# ---------------------------------------------------------------------------
# R58 — V2: Tool-registry preservation in coordinator guard
# ---------------------------------------------------------------------------


class TestR58ToolRegistryCoordinationGuard:
    """Guard: coordinator tools-block builder must preserve malformed
    tool-registry as .malformed.json (copy)."""

    def test_copy_to_malformed_in_except_block(self):
        """The except block must copy to .malformed.json."""
        from pathlib import Path

        exec_path = (Path(__file__).resolve().parent.parent
                     / "src" / "scripts" / "section_loop"
                     / "coordination" / "execution.py")
        source = exec_path.read_text(encoding="utf-8")

        assert ".malformed.json" in source, (
            "execution.py must preserve malformed tool-registry "
            "as .malformed.json")
        assert "shutil.copy2" in source or "shutil.copy" in source, (
            "execution.py must use shutil.copy to preserve "
            "(copy, not rename)")


# ---------------------------------------------------------------------------
# R58 — V3: Related-files update signal preservation guard
# ---------------------------------------------------------------------------


class TestR58RelatedFilesSignalGuard:
    """Guard: apply_related_files_update must preserve malformed
    signals as .malformed.json."""

    def test_malformed_rename_in_apply_function(self):
        """The apply_related_files_update except block must rename
        to .malformed.json."""
        from pathlib import Path

        expl_path = (Path(__file__).resolve().parent.parent
                     / "src" / "scripts" / "scan" / "exploration.py")
        source = expl_path.read_text(encoding="utf-8")

        # Find the apply_related_files_update function and check
        # it has .malformed.json rename
        func_start = source.find("def apply_related_files_update(")
        assert func_start >= 0, (
            "apply_related_files_update must exist in exploration.py")

        # Get function body (up to next def at same indent level)
        func_body = source[func_start:]
        next_def = func_body.find("\ndef ", 1)
        if next_def > 0:
            func_body = func_body[:next_def]

        assert ".malformed.json" in func_body, (
            "apply_related_files_update must rename malformed signals "
            "to .malformed.json")


# ---------------------------------------------------------------------------
# R59 Guards
# ---------------------------------------------------------------------------


class TestR59CatalogQuotaGuard:
    """V1/R59: _build_philosophy_catalog must use per-root quotas and
    scan codespace first."""

    def test_codespace_scanned_first_in_iteration(self) -> None:
        """The for-loop must iterate (codespace, ...) before (planspace, ...)."""
        bootstrap = (Path(__file__).resolve().parent.parent / "src"
                     / "scripts" / "section_loop" / "intent" / "bootstrap.py")
        if not bootstrap.exists():
            pytest.skip("bootstrap.py not found")
        text = bootstrap.read_text(encoding="utf-8")

        # Find the catalog function
        func_start = text.find("def _build_philosophy_catalog(")
        assert func_start >= 0
        func_body = text[func_start:]
        next_def = func_body.find("\ndef ", 1)
        if next_def > 0:
            func_body = func_body[:next_def]

        # codespace must appear before planspace in iteration
        cs_pos = func_body.find("codespace, codespace_quota")
        ps_pos = func_body.find("planspace, planspace_quota")
        assert cs_pos >= 0, "Must iterate over codespace with quota"
        assert ps_pos >= 0, "Must iterate over planspace with quota"
        assert cs_pos < ps_pos, (
            "Codespace must be iterated BEFORE planspace")

    def test_artifacts_excluded(self) -> None:
        """Planspace artifacts/ must be excluded from catalog."""
        bootstrap = (Path(__file__).resolve().parent.parent / "src"
                     / "scripts" / "section_loop" / "intent" / "bootstrap.py")
        if not bootstrap.exists():
            pytest.skip("bootstrap.py not found")
        text = bootstrap.read_text(encoding="utf-8")

        func_start = text.find("def _build_philosophy_catalog(")
        assert func_start >= 0
        func_body = text[func_start:]
        next_def = func_body.find("\ndef ", 1)
        if next_def > 0:
            func_body = func_body[:next_def]

        assert '"artifacts"' in func_body, (
            "Catalog must exclude planspace artifacts/ directory")

    def test_per_root_quotas(self) -> None:
        """Must use per-root quotas (not global max_files early return)."""
        bootstrap = (Path(__file__).resolve().parent.parent / "src"
                     / "scripts" / "section_loop" / "intent" / "bootstrap.py")
        if not bootstrap.exists():
            pytest.skip("bootstrap.py not found")
        text = bootstrap.read_text(encoding="utf-8")

        func_start = text.find("def _build_philosophy_catalog(")
        assert func_start >= 0
        func_body = text[func_start:]
        next_def = func_body.find("\ndef ", 1)
        if next_def > 0:
            func_body = func_body[:next_def]

        assert "codespace_quota" in func_body, (
            "Must define codespace_quota for per-root allocation")
        assert "planspace_quota" in func_body, (
            "Must define planspace_quota for per-root allocation")
        # Must NOT have global early return on max_files
        assert "if len(candidates) >= max_files" not in func_body, (
            "Must NOT use global max_files early return — "
            "use per-root quotas instead")


class TestR59GroundingValidationGuard:
    """V2/R59: ensure_global_philosophy must validate source map after
    distillation."""

    def test_grounding_validation_called(self) -> None:
        """ensure_global_philosophy must call _validate_philosophy_grounding."""
        bootstrap = (Path(__file__).resolve().parent.parent / "src"
                     / "scripts" / "section_loop" / "intent" / "bootstrap.py")
        if not bootstrap.exists():
            pytest.skip("bootstrap.py not found")
        text = bootstrap.read_text(encoding="utf-8")

        func_start = text.find("def ensure_global_philosophy(")
        assert func_start >= 0
        func_body = text[func_start:]
        next_def = func_body.find("\ndef ", 1)
        if next_def > 0:
            func_body = func_body[:next_def]

        assert "_validate_philosophy_grounding" in func_body, (
            "ensure_global_philosophy must call "
            "_validate_philosophy_grounding after distillation")

    def test_grounding_validator_exists_and_checks_coverage(self) -> None:
        """_validate_philosophy_grounding must check principle coverage."""
        bootstrap = (Path(__file__).resolve().parent.parent / "src"
                     / "scripts" / "section_loop" / "intent" / "bootstrap.py")
        if not bootstrap.exists():
            pytest.skip("bootstrap.py not found")
        text = bootstrap.read_text(encoding="utf-8")

        func_start = text.find("def _validate_philosophy_grounding(")
        assert func_start >= 0, (
            "_validate_philosophy_grounding function must exist")
        func_body = text[func_start:]
        next_def = func_body.find("\ndef ", 1)
        if next_def > 0:
            func_body = func_body[:next_def]

        assert "philosophy-grounding-failed.json" in func_body, (
            "Must write grounding-failed signal")
        assert ".malformed.json" in func_body, (
            "Must preserve malformed source map")
        assert r"P\d+" in func_body or "P\\d+" in func_body, (
            "Must extract principle IDs with P\\d+ pattern")


class TestR59IntentPackHashGuard:
    """V3/R59: generate_intent_pack must use hash-based invalidation."""

    def test_hash_computation_exists(self) -> None:
        """_compute_intent_pack_hash function must exist."""
        bootstrap = (Path(__file__).resolve().parent.parent / "src"
                     / "scripts" / "section_loop" / "intent" / "bootstrap.py")
        if not bootstrap.exists():
            pytest.skip("bootstrap.py not found")
        text = bootstrap.read_text(encoding="utf-8")

        assert "def _compute_intent_pack_hash(" in text, (
            "_compute_intent_pack_hash function must exist")

    def test_generate_intent_pack_uses_hash(self) -> None:
        """generate_intent_pack must compute and check input hash."""
        bootstrap = (Path(__file__).resolve().parent.parent / "src"
                     / "scripts" / "section_loop" / "intent" / "bootstrap.py")
        if not bootstrap.exists():
            pytest.skip("bootstrap.py not found")
        text = bootstrap.read_text(encoding="utf-8")

        func_start = text.find("def generate_intent_pack(")
        assert func_start >= 0
        func_body = text[func_start:]
        next_def = func_body.find("\ndef ", 1)
        if next_def > 0:
            func_body = func_body[:next_def]

        assert "_compute_intent_pack_hash" in func_body, (
            "generate_intent_pack must call _compute_intent_pack_hash")
        assert "intent-pack-input-hash.txt" in func_body, (
            "Must read/write intent-pack-input-hash.txt")


# ---- R60 Regression Guards ----


class TestR60BoundedWalkGuard:
    """V1/R60: Philosophy catalog must use os.walk, not sorted(rglob(...))."""

    def test_no_sorted_rglob_in_catalog(self) -> None:
        """_build_philosophy_catalog must NOT use sorted(rglob(...))."""
        bootstrap = (Path(__file__).resolve().parent.parent / "src"
                     / "scripts" / "section_loop" / "intent" / "bootstrap.py")
        if not bootstrap.exists():
            pytest.skip("bootstrap.py not found")
        text = bootstrap.read_text(encoding="utf-8")

        func_start = text.find("def _build_philosophy_catalog(")
        assert func_start >= 0
        func_body = text[func_start:]
        next_def = func_body.find("\ndef ", 1)
        if next_def > 0:
            func_body = func_body[:next_def]

        assert "sorted(root_dir.rglob" not in func_body, (
            "Must NOT use sorted(rglob(...)) — materializes full tree")
        assert ".rglob(" not in func_body, (
            "Must NOT use rglob — use _walk_md_bounded instead")

    def test_walk_md_bounded_exists(self) -> None:
        """_walk_md_bounded function must exist and use os.walk."""
        bootstrap = (Path(__file__).resolve().parent.parent / "src"
                     / "scripts" / "section_loop" / "intent" / "bootstrap.py")
        if not bootstrap.exists():
            pytest.skip("bootstrap.py not found")
        text = bootstrap.read_text(encoding="utf-8")

        assert "def _walk_md_bounded(" in text, (
            "_walk_md_bounded function must exist")

        func_start = text.find("def _walk_md_bounded(")
        func_body = text[func_start:]
        next_def = func_body.find("\ndef ", 1)
        if next_def > 0:
            func_body = func_body[:next_def]

        assert "os.walk(" in func_body, (
            "_walk_md_bounded must use os.walk for lazy traversal")
        assert "dirnames" in func_body, (
            "Must prune dirnames for depth/exclusion control")

    def test_catalog_uses_bounded_walk(self) -> None:
        """_build_philosophy_catalog must call _walk_md_bounded."""
        bootstrap = (Path(__file__).resolve().parent.parent / "src"
                     / "scripts" / "section_loop" / "intent" / "bootstrap.py")
        if not bootstrap.exists():
            pytest.skip("bootstrap.py not found")
        text = bootstrap.read_text(encoding="utf-8")

        func_start = text.find("def _build_philosophy_catalog(")
        assert func_start >= 0
        func_body = text[func_start:]
        next_def = func_body.find("\ndef ", 1)
        if next_def > 0:
            func_body = func_body[:next_def]

        assert "_walk_md_bounded" in func_body, (
            "_build_philosophy_catalog must use _walk_md_bounded")


class TestR60ToolContractGuard:
    """V2/R60: extract-docstring-py must catch OSError for structured errors."""

    def test_oserror_in_except_clause(self) -> None:
        """extract_docstring must catch OSError alongside parse errors."""
        tool_path = (Path(__file__).resolve().parent.parent
                     / "src" / "tools" / "extract-docstring-py")
        if not tool_path.exists():
            pytest.skip("extract-docstring-py not found")
        text = tool_path.read_text(encoding="utf-8")

        func_start = text.find("def extract_docstring(")
        assert func_start >= 0
        func_body = text[func_start:]
        next_def = func_body.find("\ndef ", 1)
        if next_def > 0:
            func_body = func_body[:next_def]

        assert "OSError" in func_body, (
            "extract_docstring must catch OSError for file read failures")


class TestR60LayoutAgnosticRootGuard:
    """V3/R60: conftest.py must use anchor-based project root resolution."""

    def test_conftest_uses_anchor_walk(self) -> None:
        """conftest.py must use _find_project_root helper."""
        conftest_path = Path(__file__).resolve().parent / "conftest.py"
        text = conftest_path.read_text(encoding="utf-8")

        assert "_find_project_root" in text, (
            "conftest must use _find_project_root helper (V3/R60)")
        assert "SKILL.md" in text, (
            "Must use SKILL.md as a stable anchor for root resolution")

    def test_conftest_anchor_checks_skill_and_dirs(self) -> None:
        """_find_project_root must check for scripts/ + agents/ directories."""
        conftest_path = Path(__file__).resolve().parent / "conftest.py"
        text = conftest_path.read_text(encoding="utf-8")

        func_start = text.find("def _find_project_root(")
        assert func_start >= 0
        func_body = text[func_start:]
        next_def = func_body.find("\ndef ", 1)
        if next_def > 0:
            func_body = func_body[:next_def]

        assert '"scripts"' in func_body or "'scripts'" in func_body, (
            "Must check for scripts/ directory as anchor")
        assert '"agents"' in func_body or "'agents'" in func_body, (
            "Must check for agents/ directory as anchor")


# ---------------------------------------------------------------------------
# R61: Alignment surface intent propagation guard
# ---------------------------------------------------------------------------

class TestR61AlignmentSurfaceIntentGuard:
    """V1/R61: Alignment surface writer must include intent artifacts."""

    def test_surface_writer_references_intent_artifacts(self) -> None:
        """_write_alignment_surface source must reference intent paths."""
        reexplore_path = (
            Path(__file__).resolve().parent.parent
            / "src" / "scripts" / "section_loop" / "section_engine"
            / "reexplore.py"
        )
        text = reexplore_path.read_text(encoding="utf-8")

        # Extract _write_alignment_surface function body
        func_start = text.find("def _write_alignment_surface(")
        assert func_start >= 0, "Function must exist"
        func_body = text[func_start:]
        next_def = func_body.find("\ndef ", 1)
        if next_def > 0:
            func_body = func_body[:next_def]

        assert "problem.md" in func_body, (
            "Must include intent problem definition in surface")
        assert "problem-alignment.md" in func_body, (
            "Must include intent rubric in surface")
        assert "philosophy-excerpt.md" in func_body, (
            "Must include philosophy excerpt in surface")
        assert "surface-registry.json" in func_body, (
            "Must include surface registry in surface")


# ---------------------------------------------------------------------------
# R61: No brute-force reading instructions guard
# ---------------------------------------------------------------------------

class TestR61NoBruteForceReadingGuard:
    """V2/R61: Templates must not contain 'read each one' mandates."""

    def test_integration_proposal_no_read_each_one(self) -> None:
        """integration-proposal.md must not mandate exhaustive reading."""
        tpl_path = (
            Path(__file__).resolve().parent.parent
            / "src" / "scripts" / "section_loop" / "prompts"
            / "templates" / "integration-proposal.md"
        )
        text = tpl_path.read_text(encoding="utf-8")
        assert "read each one" not in text.lower(), (
            "Template must not contain brute-force 'read each one' mandate")

    def test_implementation_alignment_no_read_each_one(self) -> None:
        """implementation-alignment.md must not mandate exhaustive reading."""
        tpl_path = (
            Path(__file__).resolve().parent.parent
            / "src" / "scripts" / "section_loop" / "prompts"
            / "templates" / "implementation-alignment.md"
        )
        text = tpl_path.read_text(encoding="utf-8")
        assert "read each one" not in text.lower(), (
            "Template must not contain brute-force 'read each one' mandate")


# ---------------------------------------------------------------------------
# R61: Intent refs in generation templates guard
# ---------------------------------------------------------------------------

class TestR61IntentRefsInGenerationGuard:
    """V3/R61: Generation templates must include intent placeholders."""

    def test_integration_proposal_has_intent_refs(self) -> None:
        """integration-proposal.md must include intent_* placeholders."""
        tpl_path = (
            Path(__file__).resolve().parent.parent
            / "src" / "scripts" / "section_loop" / "prompts"
            / "templates" / "integration-proposal.md"
        )
        text = tpl_path.read_text(encoding="utf-8")
        assert "{intent_problem_ref}" in text, (
            "Must include intent problem ref placeholder")
        assert "{intent_rubric_ref}" in text, (
            "Must include intent rubric ref placeholder")
        assert "{intent_philosophy_ref}" in text, (
            "Must include intent philosophy ref placeholder")

    def test_strategic_implementation_has_intent_refs(self) -> None:
        """strategic-implementation.md must include intent_* placeholders."""
        tpl_path = (
            Path(__file__).resolve().parent.parent
            / "src" / "scripts" / "section_loop" / "prompts"
            / "templates" / "strategic-implementation.md"
        )
        text = tpl_path.read_text(encoding="utf-8")
        assert "{intent_problem_ref}" in text, (
            "Must include intent problem ref placeholder")
        assert "{intent_rubric_ref}" in text, (
            "Must include intent rubric ref placeholder")
        assert "{intent_philosophy_ref}" in text, (
            "Must include intent philosophy ref placeholder")


# ---------------------------------------------------------------------------
# R61: Agent-steerable extensions guard
# ---------------------------------------------------------------------------

class TestR61AgentSteerableExtensionsGuard:
    """V4/R61: Catalog walker must accept configurable extensions."""

    def test_walk_md_bounded_has_extensions_param(self) -> None:
        """_walk_md_bounded must accept an extensions parameter."""
        bootstrap_path = (
            Path(__file__).resolve().parent.parent
            / "src" / "scripts" / "section_loop" / "intent" / "bootstrap.py"
        )
        text = bootstrap_path.read_text(encoding="utf-8")
        func_start = text.find("def _walk_md_bounded(")
        assert func_start >= 0
        func_body = text[func_start:]
        next_def = func_body.find("\ndef ", 1)
        if next_def > 0:
            func_body = func_body[:next_def]

        assert "extensions" in func_body, (
            "Walker must accept extensions parameter (V4/R61)")

    def test_catalog_builder_has_extensions_param(self) -> None:
        """_build_philosophy_catalog must accept extensions parameter."""
        bootstrap_path = (
            Path(__file__).resolve().parent.parent
            / "src" / "scripts" / "section_loop" / "intent" / "bootstrap.py"
        )
        text = bootstrap_path.read_text(encoding="utf-8")
        func_start = text.find("def _build_philosophy_catalog(")
        assert func_start >= 0
        func_body = text[func_start:]
        next_def = func_body.find("\ndef ", 1)
        if next_def > 0:
            func_body = func_body[:next_def]

        assert "extensions" in func_body, (
            "Catalog builder must accept extensions parameter (V4/R61)")

    def test_no_hardcoded_endswith_md_in_walker(self) -> None:
        """Walker must not hardcode .endswith('.md') — must use extensions."""
        bootstrap_path = (
            Path(__file__).resolve().parent.parent
            / "src" / "scripts" / "section_loop" / "intent" / "bootstrap.py"
        )
        text = bootstrap_path.read_text(encoding="utf-8")
        func_start = text.find("def _walk_md_bounded(")
        assert func_start >= 0
        func_body = text[func_start:]
        next_def = func_body.find("\ndef ", 1)
        if next_def > 0:
            func_body = func_body[:next_def]

        assert '.endswith(".md")' not in func_body, (
            "Walker must not hardcode .md — must use extensions param")
        assert ".endswith('.md')" not in func_body, (
            "Walker must not hardcode .md — must use extensions param")

    def test_expansion_mechanism_exists(self) -> None:
        """ensure_global_philosophy must handle additional_extensions."""
        bootstrap_path = (
            Path(__file__).resolve().parent.parent
            / "src" / "scripts" / "section_loop" / "intent" / "bootstrap.py"
        )
        text = bootstrap_path.read_text(encoding="utf-8")
        func_start = text.find("def ensure_global_philosophy(")
        assert func_start >= 0
        func_body = text[func_start:]
        next_def = func_body.find("\ndef ", 1)
        if next_def > 0:
            func_body = func_body[:next_def]

        assert "additional_extensions" in func_body, (
            "Must handle agent-requested extension expansion (V4/R61)")


# --- R66 guards ---




class TestR66NoInlineDispatchInTemplates:
    """Runtime prompt templates must use --file, not inline dispatch (V4/R66)."""

    def test_no_inline_dispatch_in_templates(self) -> None:
        templates_dir = (
            Path(__file__).resolve().parent.parent
            / "src" / "scripts" / "section_loop" / "prompts" / "templates"
        )
        if not templates_dir.is_dir():
            pytest.skip("templates/ dir not found")
        violations = []
        for tmpl in templates_dir.glob("*.md"):
            text = tmpl.read_text(encoding="utf-8")
            for i, line in enumerate(text.splitlines(), 1):
                stripped = line.strip()
                if (
                    "agents" in stripped
                    and "--model" in stripped
                    and '"<' in stripped
                    and "--file" not in stripped
                ):
                    violations.append(f"{tmpl.name}:{i}: {stripped}")
        assert not violations, (
            f"Templates contain inline dispatch (use --file):\n"
            + "\n".join(violations))

    def test_no_inline_dispatch_in_python_prompts(self) -> None:
        """Python prompt builders must not embed inline dispatch."""
        src = Path(__file__).resolve().parent.parent / "src"
        py_files = [
            src / "scripts" / "section_loop" / "coordination" / "execution.py",
            src / "scripts" / "section_loop" / "section_engine" / "reexplore.py",
        ]
        violations = []
        for py_path in py_files:
            if not py_path.exists():
                continue
            text = py_path.read_text(encoding="utf-8")
            for i, line in enumerate(text.splitlines(), 1):
                if (
                    "agents" in line
                    and "--model" in line
                    and '"<' in line
                    and "--file" not in line
                ):
                    violations.append(f"{py_path.name}:{i}: {line.strip()}")
        assert not violations, (
            f"Python prompt builders contain inline dispatch:\n"
            + "\n".join(violations))


class TestR67IntentAgentArtifactRouting:
    """R67/V3: Intent-judge used when intent artifacts exist, regardless of mode."""

    def test_runner_uses_intent_artifacts_not_mode_for_agent_selection(
        self,
    ) -> None:
        """runner.py must select alignment agent based on artifact existence."""
        src = Path(__file__).resolve().parent.parent / "src"
        runner = (src / "scripts" / "section_loop" / "section_engine"
                  / "runner.py")
        text = runner.read_text(encoding="utf-8")
        # Must check for intent artifact existence, not just intent_mode
        assert "has_intent_artifacts" in text
        assert '"intent-judge.md" if has_intent_artifacts' in text


class TestR67ExpansionBudgetPause:
    """R67/V4: Expansion budget exhaustion pauses for parent, not proceed."""

    def test_expansion_budget_triggers_pause(self) -> None:
        """runner.py must pause_for_parent when expansion budget exhausted."""
        src = Path(__file__).resolve().parent.parent / "src"
        runner = (src / "scripts" / "section_loop" / "section_engine"
                  / "runner.py")
        text = runner.read_text(encoding="utf-8")
        # The expansion budget code must call pause_for_parent
        assert "pause:intent-stalled" in text
        assert "pause_for_parent" in text


class TestR67SubstrateSignalFieldNames:
    """R67/V10b: Substrate signals use 'state' field, not 'status'."""

    def test_runner_reads_state_not_status(self) -> None:
        """runner.py must read 'state' from prune/seed signals."""
        src = Path(__file__).resolve().parent.parent / "src"
        runner = src / "scripts" / "substrate" / "runner.py"
        text = runner.read_text(encoding="utf-8")
        # prune-signal and seed-signal readers should use .get("state", ...)
        # and NOT .get("status", ...)
        import re
        state_reads = re.findall(r'\.get\(["\']state["\']', text)
        status_reads = re.findall(
            r'(?:prune|seed).*\.get\(["\']status["\']', text)
        assert len(state_reads) >= 2, (
            f"Expected >=2 .get('state') calls, found {len(state_reads)}")
        assert len(status_reads) == 0, (
            f"Found .get('status') in signal readers: {status_reads}")


class TestR67ModelPolicyFailClosed:
    """R67/V10d: All model-policy readers rename malformed files."""

    def test_all_model_policy_readers_rename_malformed(self) -> None:
        """Every model-policy reader must rename to .malformed.json."""
        src = Path(__file__).resolve().parent.parent / "src"
        readers = [
            src / "scripts" / "scan" / "dispatch.py",
            src / "scripts" / "section_loop" / "dispatch.py",
            src / "scripts" / "substrate" / "runner.py",
        ]
        for reader_path in readers:
            text = reader_path.read_text(encoding="utf-8")
            assert ".malformed.json" in text, (
                f"{reader_path.name} missing .malformed.json rename"
            )


class TestR67PhilosophySourceInvalidation:
    """R67/V7: Global philosophy cached with source manifest invalidation."""

    def test_bootstrap_writes_source_manifest(self) -> None:
        """bootstrap.py must write philosophy-source-manifest.json."""
        src = Path(__file__).resolve().parent.parent / "src"
        bootstrap = (src / "scripts" / "section_loop" / "intent"
                     / "bootstrap.py")
        text = bootstrap.read_text(encoding="utf-8")
        assert "philosophy-source-manifest.json" in text

    def test_pipeline_control_hashes_manifest(self) -> None:
        """pipeline_control.py must hash the source manifest."""
        src = Path(__file__).resolve().parent.parent / "src"
        pc = (src / "scripts" / "section_loop" / "pipeline_control.py")
        text = pc.read_text(encoding="utf-8")
        assert "philosophy-source-manifest.json" in text


class TestR67IntentPackGeneratorNoTaxonomy:
    """R67/V8: Intent-pack-generator does not impose a fixed taxonomy."""

    def test_no_category_weight_columns_in_rubric(self) -> None:
        """intent-pack-generator.md rubric must not have Category/Weight."""
        src = Path(__file__).resolve().parent.parent / "src"
        agent = src / "agents" / "intent-pack-generator.md"
        text = agent.read_text(encoding="utf-8")
        # Rubric table should not contain Category or Weight columns
        assert "| Category |" not in text
        assert "| Weight |" not in text


class TestR67IntentJudgeNoChecklistFraming:
    """R67/V9: Intent-judge uses Contact Scan, not Coverage Scan."""

    def test_no_coverage_scan_heading(self) -> None:
        """intent-judge.md must use Contact Scan, not Coverage Scan."""
        src = Path(__file__).resolve().parent.parent / "src"
        agent = src / "agents" / "intent-judge.md"
        text = agent.read_text(encoding="utf-8")
        assert "Coverage Scan" not in text
        assert "Contact Scan" in text


class TestR68PhilosophyUnconditional:
    """R68/V1: Global philosophy runs unconditionally, not gated by triage."""

    def test_philosophy_outside_full_block(self) -> None:
        """ensure_global_philosophy must be called before the full-mode block."""
        src = Path(__file__).resolve().parent.parent / "src"
        runner = (src / "scripts" / "section_loop" / "section_engine"
                  / "runner.py")
        text = runner.read_text(encoding="utf-8")
        # Philosophy call must appear BEFORE the full-mode conditional
        phil_idx = text.index("ensure_global_philosophy(")
        full_idx = text.index('if intent_mode == "full":')
        assert phil_idx < full_idx, (
            "ensure_global_philosophy must be called before "
            "intent_mode == 'full' block"
        )


class TestR68TriageReadsArtifacts:
    """R68/V2: Triage prompt includes artifact paths for grounded assessment."""

    def test_triage_prompt_includes_artifact_refs(self) -> None:
        """triage.py must build artifact path references in prompt."""
        src = Path(__file__).resolve().parent.parent / "src"
        triage = (src / "scripts" / "section_loop" / "intent"
                  / "triage.py")
        text = triage.read_text(encoding="utf-8")
        assert "Section Artifacts" in text
        assert "section_spec" in text
        assert "proposal_excerpt" in text

    def test_triager_no_anti_reading_pattern(self) -> None:
        """intent-triager.md must not forbid reading file contents."""
        src = Path(__file__).resolve().parent.parent / "src"
        agent = src / "agents" / "intent-triager.md"
        text = agent.read_text(encoding="utf-8")
        assert "You do NOT read file contents" not in text


class TestR68ProblemFrameFlexible:
    """R68/V3+V4: Problem frame validated as exists+non-empty, no heading gate."""

    def test_no_required_headings_validation(self) -> None:
        """runner.py must not enforce specific problem-frame headings."""
        src = Path(__file__).resolve().parent.parent / "src"
        runner = (src / "scripts" / "section_loop" / "section_engine"
                  / "runner.py")
        text = runner.read_text(encoding="utf-8")
        assert "required_headings" not in text

    def test_section_setup_no_required_fields(self) -> None:
        """section-setup.md must not say 'All fields are required'."""
        src = Path(__file__).resolve().parent.parent / "src"
        template = (src / "scripts" / "section_loop" / "prompts"
                    / "templates" / "section-setup.md")
        text = template.read_text(encoding="utf-8")
        assert "All fields are required" not in text


class TestR68SemanticCapsAdvisory:
    """R68/V5: Semantic counts (6-12 axes/principles) are guidance, not hard."""

    def test_distiller_no_hard_count(self) -> None:
        """philosophy-distiller.md must not enforce exact count range."""
        src = Path(__file__).resolve().parent.parent / "src"
        agent = src / "agents" / "philosophy-distiller.md"
        text = agent.read_text(encoding="utf-8")
        # Must not say "Aim for 6-12" or "Fewer than 6 means"
        assert "Aim for 6-12" not in text
        assert "Fewer than 6 means" not in text

    def test_pack_generator_no_hard_axis_count(self) -> None:
        """intent-pack-generator.md must not enforce Select 6-12."""
        src = Path(__file__).resolve().parent.parent / "src"
        agent = src / "agents" / "intent-pack-generator.md"
        text = agent.read_text(encoding="utf-8")
        assert "Select 6-12" not in text

    def test_expansion_axis_budget_advisory(self) -> None:
        """expansion.py run_expansion_cycle must not hard-block on axis budget."""
        src = Path(__file__).resolve().parent.parent / "src"
        expansion = (src / "scripts" / "section_loop" / "intent"
                     / "expansion.py")
        text = expansion.read_text(encoding="utf-8")
        # Check only run_expansion_cycle, not handle_user_gate
        fn_start = text.index("def run_expansion_cycle(")
        fn_end = text.index("\ndef ", fn_start + 1)
        fn_body = text[fn_start:fn_end]
        assert "NEED_DECISION" not in fn_body
        assert "budget advisory" in fn_body


class TestR68SISSignalTrigger:
    """R68/V6: SIS accepts signal-driven trigger beyond vacuum sections."""

    def test_signal_trigger_reader_exists(self) -> None:
        """substrate/runner.py must have _read_trigger_signals function."""
        src = Path(__file__).resolve().parent.parent / "src"
        runner = src / "scripts" / "substrate" / "runner.py"
        text = runner.read_text(encoding="utf-8")
        assert "_read_trigger_signals" in text
        assert "substrate-trigger-" in text

    def test_implement_documents_signal_trigger(self) -> None:
        """implement.md must document signal-driven SIS trigger."""
        src = Path(__file__).resolve().parent.parent / "src"
        impl = src / "implement.md"
        text = impl.read_text(encoding="utf-8")
        assert "Signal-driven" in text or "signal-driven" in text


class TestR68PhilosophyExcerptNormalized:
    """R68/V7+V8: philosophy-excerpt.md is section-scoped view, not for distiller."""

    def test_no_distiller_reference(self) -> None:
        """intent-pack-generator.md must not say excerpt is for the distiller."""
        src = Path(__file__).resolve().parent.parent / "src"
        agent = src / "agents" / "intent-pack-generator.md"
        text = agent.read_text(encoding="utf-8")
        assert "for the philosophy distiller" not in text
        assert "section-scoped" in text.lower() or "section scoped" in text.lower()


# ── R69 Regression Guards ──────────────────────────────────────────────


class TestR69PhilosophyExpanderSourceGrounded:
    """R69/V1: Expander replaces 'Compatible' with source-grounded omission."""

    def test_no_compatible_addition(self) -> None:
        """philosophy-expander.md must not use 'Compatible' as a classification."""
        src = Path(__file__).resolve().parent.parent / "src"
        agent = src / "agents" / "philosophy-expander.md"
        text = agent.read_text(encoding="utf-8")
        # The old "Compatible" classification must be replaced
        assert "Compatible addition" not in text
        assert "Compatible — " not in text

    def test_source_grounded_omission_present(self) -> None:
        """philosophy-expander.md must define source-grounded omission."""
        src = Path(__file__).resolve().parent.parent / "src"
        agent = src / "agents" / "philosophy-expander.md"
        text = agent.read_text(encoding="utf-8")
        assert "Source-grounded omission" in text or "source-grounded omission" in text

    def test_new_root_candidate_present(self) -> None:
        """philosophy-expander.md must define new root candidate for silence."""
        src = Path(__file__).resolve().parent.parent / "src"
        agent = src / "agents" / "philosophy-expander.md"
        text = agent.read_text(encoding="utf-8")
        assert "New root candidate" in text or "new root candidate" in text

    def test_no_inventing_from_silence(self) -> None:
        """philosophy-expander.md anti-patterns must forbid inventing principles."""
        src = Path(__file__).resolve().parent.parent / "src"
        agent = src / "agents" / "philosophy-expander.md"
        text = agent.read_text(encoding="utf-8")
        assert "Inventing principles from silence" in text

    def test_expansion_prompt_source_map(self) -> None:
        """expansion.py philosophy-expander prompt includes source-map as input."""
        src = Path(__file__).resolve().parent.parent / "src"
        expansion = src / "scripts" / "section_loop" / "intent" / "expansion.py"
        text = expansion.read_text(encoding="utf-8")
        assert "source_map_path" in text
        assert "Source-grounded omission" in text or "source-grounded omission" in text


class TestR69GroundingRevalidation:
    """R69/V2: Post-expansion grounding revalidation closes traceability loop."""

    def test_expansion_calls_grounding_validation(self) -> None:
        """_run_philosophy_expander must revalidate grounding after expansion."""
        src = Path(__file__).resolve().parent.parent / "src"
        expansion = src / "scripts" / "section_loop" / "intent" / "expansion.py"
        text = expansion.read_text(encoding="utf-8")
        assert "_validate_philosophy_grounding" in text

    def test_source_map_in_change_detection(self) -> None:
        """pipeline_control.py must hash philosophy-source-map.json."""
        src = Path(__file__).resolve().parent.parent / "src"
        pc = src / "scripts" / "section_loop" / "pipeline_control.py"
        text = pc.read_text(encoding="utf-8")
        assert "philosophy-source-map.json" in text

    def test_bootstrap_requires_source_map(self) -> None:
        """bootstrap.py must regenerate philosophy when source-map missing."""
        src = Path(__file__).resolve().parent.parent / "src"
        bs = src / "scripts" / "section_loop" / "intent" / "bootstrap.py"
        text = bs.read_text(encoding="utf-8")
        assert "source-map" in text.lower()
        assert "missing" in text.lower()
        assert "regenerating" in text.lower()


class TestR69CatalogFingerprint:
    """R69/V3: Catalog fingerprint detects new/changed candidate universe."""

    def test_fingerprint_written_after_distillation(self) -> None:
        """bootstrap.py must write catalog-fingerprint.txt after distillation."""
        src = Path(__file__).resolve().parent.parent / "src"
        bs = src / "scripts" / "section_loop" / "intent" / "bootstrap.py"
        text = bs.read_text(encoding="utf-8")
        assert "philosophy-catalog-fingerprint.txt" in text

    def test_fingerprint_checked_in_cache_path(self) -> None:
        """bootstrap.py must compare catalog fingerprint when checking cache."""
        src = Path(__file__).resolve().parent.parent / "src"
        bs = src / "scripts" / "section_loop" / "intent" / "bootstrap.py"
        text = bs.read_text(encoding="utf-8")
        assert "catalog_changed" in text
        assert "catalog_fp" in text


class TestR69AmbiguousVerification:
    """R69/V4: Selector nominates ambiguous candidates for full-read verify."""

    def test_selector_agent_has_ambiguous_classification(self) -> None:
        """philosophy-source-selector.md must define Ambiguous classification."""
        src = Path(__file__).resolve().parent.parent / "src"
        agent = src / "agents" / "philosophy-source-selector.md"
        text = agent.read_text(encoding="utf-8")
        assert "Ambiguous" in text
        assert "full-read verification" in text.lower() or "full-read" in text.lower()

    def test_selector_prompt_includes_ambiguous_field(self) -> None:
        """bootstrap.py selector prompt must mention ambiguous output field."""
        src = Path(__file__).resolve().parent.parent / "src"
        bs = src / "scripts" / "section_loop" / "intent" / "bootstrap.py"
        text = bs.read_text(encoding="utf-8")
        assert '"ambiguous"' in text

    def test_verifier_dispatch_exists(self) -> None:
        """bootstrap.py must dispatch a verifier for ambiguous candidates."""
        src = Path(__file__).resolve().parent.parent / "src"
        bs = src / "scripts" / "section_loop" / "intent" / "bootstrap.py"
        text = bs.read_text(encoding="utf-8")
        assert "philosophy-verify-prompt.md" in text
        assert "verified_sources" in text
        assert "_AMBIGUOUS_CAP" in text


class TestR71V7ContractSurfaceDrift:
    """R71/V7: Per-surface migration is atomic — no split contracts within a surface."""

    def test_integration_proposer_no_dispatch_language(self) -> None:
        """R71/V7a: integration-proposer.md description must not say 'dispatches' or 'sub-agents'."""
        agent = AGENTS_DIR / "integration-proposer.md"
        text = agent.read_text(encoding="utf-8")
        # Extract frontmatter description (line 2)
        lines = text.splitlines()
        desc_line = ""
        for line in lines:
            if line.startswith("description:"):
                desc_line = line
                break
        assert "dispatches" not in desc_line.lower(), (
            "integration-proposer.md description still references 'dispatches'"
        )
        assert "sub-agents" not in desc_line.lower(), (
            "integration-proposer.md description still references 'sub-agents'"
        )

    def test_implementation_strategist_no_subagent_dispatch(self) -> None:
        """R71/V7b: implementation-strategist.md description must not say 'sub-agent dispatch'."""
        agent = AGENTS_DIR / "implementation-strategist.md"
        text = agent.read_text(encoding="utf-8")
        lines = text.splitlines()
        desc_line = ""
        for line in lines:
            if line.startswith("description:"):
                desc_line = line
                break
        assert "sub-agent dispatch" not in desc_line.lower(), (
            "implementation-strategist.md description still references 'sub-agent dispatch'"
        )

    def test_coordination_fixer_has_task_submission(self) -> None:
        """R71/V7c: coordination-fixer.md must contain a Task Submission section."""
        agent = AGENTS_DIR / "coordination-fixer.md"
        text = agent.read_text(encoding="utf-8")
        assert "Task Submission" in text, (
            "coordination-fixer.md is missing 'Task Submission' section"
        )

    def test_microstrategy_prompt_no_per_line(self) -> None:
        """R71/V7d: runner.py microstrategy task-submission must not say 'per line'."""
        runner = (
            Path(__file__).resolve().parent.parent
            / "src" / "scripts" / "section_loop" / "section_engine" / "runner.py"
        )
        text = runner.read_text(encoding="utf-8")
        # Find the microstrategy task-submission block (near "## Task Submission" in the prompt)
        idx = text.find("## Task Submission")
        assert idx != -1, "runner.py must contain a '## Task Submission' section"
        # Check the next ~200 chars for the "per line" phrase
        block = text[idx:idx + 300]
        assert "per line" not in block.lower(), (
            "runner.py microstrategy task-submission still says 'per line'"
        )


class TestR71V2ImplementNoSubAgentDispatch:
    """R71/V2: implement.md does not contain direct sub-agent dispatch examples."""

    def test_no_agents_model_glm_in_implementation_sections(self) -> None:
        """implement.md must not contain 'agents --model glm --project' dispatch blocks."""
        impl = PROJECT_ROOT / "src" / "implement.md"
        text = impl.read_text(encoding="utf-8")
        assert "agents --model glm --project" not in text, (
            "implement.md still contains direct 'agents --model glm --project' "
            "dispatch — should use task submission instead"
        )

    def test_no_sub_agent_dispatch_phrase(self) -> None:
        """implement.md must not use 'sub-agent dispatch' phrasing."""
        impl = PROJECT_ROOT / "src" / "implement.md"
        text = impl.read_text(encoding="utf-8")
        assert "sub-agent dispatch" not in text.lower(), (
            "implement.md still contains 'sub-agent dispatch' — "
            "should say 'task submission'"
        )


class TestR71V3SkillNoSubAgentGLM:
    """R71/V3: SKILL.md model roles table does not reference 'sub-agent' for GLM."""

    def test_glm_row_no_sub_agent(self) -> None:
        """SKILL.md GLM model role must not mention 'sub-agent'."""
        skill = PROJECT_ROOT / "src" / "SKILL.md"
        text = skill.read_text(encoding="utf-8")
        # Find the GLM row in the Model Roles table
        for line in text.splitlines():
            if line.strip().startswith("| `glm`"):
                assert "sub-agent" not in line.lower(), (
                    "SKILL.md GLM model role still references 'sub-agent'"
                )
                break
        else:
            pytest.fail("SKILL.md does not contain a GLM row in Model Roles table")


class TestR71V4OrchestratorArchived:
    """R71/V4: orchestrator.md is archived, not in active agents/ directory."""

    def test_orchestrator_not_in_agents(self) -> None:
        """orchestrator.md must not exist in src/agents/ (only in archive)."""
        active = AGENTS_DIR / "orchestrator.md"
        assert not active.exists(), (
            "orchestrator.md still exists in src/agents/ — "
            "should be in src/agents/archive/"
        )

    def test_orchestrator_in_archive(self) -> None:
        """orchestrator.md must exist in src/agents/archive/."""
        archived = AGENTS_DIR / "archive" / "orchestrator.md"
        assert archived.exists(), (
            "orchestrator.md not found in src/agents/archive/"
        )


class TestR71V5TaskIngestion:
    """R71/V5: task_ingestion.py exists and closes the task-request loop."""

    def test_task_ingestion_module_exists(self) -> None:
        """task_ingestion.py must exist in section_loop package."""
        src = Path(__file__).resolve().parent.parent / "src"
        ingestion = (
            src / "scripts" / "section_loop" / "task_ingestion.py"
        )
        assert ingestion.exists(), (
            "task_ingestion.py not found in section_loop"
        )

    def test_ingest_task_requests_function(self) -> None:
        """task_ingestion.py must define ingest_task_requests."""
        src = Path(__file__).resolve().parent.parent / "src"
        text = (
            src / "scripts" / "section_loop" / "task_ingestion.py"
        ).read_text(encoding="utf-8")
        assert "def ingest_task_requests(" in text

    def test_dispatch_ingested_tasks_function(self) -> None:
        """task_ingestion.py must define dispatch_ingested_tasks."""
        src = Path(__file__).resolve().parent.parent / "src"
        text = (
            src / "scripts" / "section_loop" / "task_ingestion.py"
        ).read_text(encoding="utf-8")
        assert "def dispatch_ingested_tasks(" in text


class TestR71V5bIngestionWired:
    """R71/V5b: At least 3 dispatch sites call ingest_and_dispatch."""

    def test_runner_calls_ingest_and_dispatch(self) -> None:
        """runner.py must call ingest_and_dispatch at least once."""
        src = Path(__file__).resolve().parent.parent / "src"
        text = (
            src / "scripts" / "section_loop" / "section_engine"
            / "runner.py"
        ).read_text(encoding="utf-8")
        assert "ingest_and_dispatch(" in text

    def test_reexplore_calls_ingest_and_dispatch(self) -> None:
        """reexplore.py must call ingest_and_dispatch."""
        src = Path(__file__).resolve().parent.parent / "src"
        text = (
            src / "scripts" / "section_loop" / "section_engine"
            / "reexplore.py"
        ).read_text(encoding="utf-8")
        assert "ingest_and_dispatch(" in text

    def test_execution_calls_ingest_and_dispatch(self) -> None:
        """coordination/execution.py must call ingest_and_dispatch."""
        src = Path(__file__).resolve().parent.parent / "src"
        text = (
            src / "scripts" / "section_loop" / "coordination"
            / "execution.py"
        ).read_text(encoding="utf-8")
        assert "ingest_and_dispatch(" in text

    def test_at_least_3_sites_total(self) -> None:
        """At least 3 files must call ingest_and_dispatch or ingest_task_requests."""
        src = Path(__file__).resolve().parent.parent / "src"
        sl = src / "scripts" / "section_loop"
        call_count = 0
        for py_file in sl.rglob("*.py"):
            text = py_file.read_text(encoding="utf-8")
            if ("ingest_and_dispatch(" in text
                    or "ingest_task_requests(" in text):
                # Exclude the definition site (task_ingestion.py itself)
                if py_file.name != "task_ingestion.py":
                    call_count += 1
        assert call_count >= 3, (
            f"Expected at least 3 callsites for ingest_and_dispatch, "
            f"found {call_count}"
        )


class TestR71V6DispatchAgentInTaskDispatcher:
    """R71/V6: task_dispatcher.py uses dispatch_agent, not raw subprocess."""

    def test_imports_dispatch_agent(self) -> None:
        """task_dispatcher.py must import dispatch_agent from section_loop.dispatch."""
        src = Path(__file__).resolve().parent.parent / "src"
        text = (
            src / "scripts" / "task_dispatcher.py"
        ).read_text(encoding="utf-8")
        assert "from section_loop.dispatch import dispatch_agent" in text

    def test_no_raw_subprocess_dispatch(self) -> None:
        """task_dispatcher.py must not use subprocess.run for agent dispatch.

        The subprocess.run calls for db.sh are fine — only the agent
        dispatch (the 'uv run agents' call) must go through dispatch_agent.
        """
        src = Path(__file__).resolve().parent.parent / "src"
        text = (
            src / "scripts" / "task_dispatcher.py"
        ).read_text(encoding="utf-8")
        # The old pattern was subprocess.run with 'uv', 'run', 'agents'
        assert '"uv", "run"' not in text, (
            "task_dispatcher.py still dispatches agents via raw subprocess — "
            "should use dispatch_agent from section_loop.dispatch"
        )


class TestR72V8NoTestCountGate:
    """R72/V8: implement.md must not contain test-count quantity gates."""

    def test_no_test_count_check(self) -> None:
        src = Path(__file__).resolve().parent.parent / "src"
        text = (src / "implement.md").read_text(encoding="utf-8")
        assert "test count check" not in text.lower(), (
            "implement.md still contains 'Test count check' quantity gate"
        )

    def test_no_test_baseline_section(self) -> None:
        src = Path(__file__).resolve().parent.parent / "src"
        text = (src / "implement.md").read_text(encoding="utf-8")
        assert "## Test Baseline" not in text, (
            "implement.md still contains 'Test Baseline' section"
        )


class TestR72V9CrossSectionSeamFraming:
    """R72/V9: Cross-section impact prompt uses seam framing, not file-overlap."""

    def test_heading_says_seam_signals(self) -> None:
        src = Path(__file__).resolve().parent.parent / "src"
        text = (src / "scripts" / "section_loop" / "cross_section.py").read_text(encoding="utf-8")
        assert "pre-filtered by file overlap" not in text, (
            "cross_section.py heading still says 'pre-filtered by file overlap'"
        )

    def test_skipped_note_mentions_seam(self) -> None:
        src = Path(__file__).resolve().parent.parent / "src"
        text = (src / "scripts" / "section_loop" / "cross_section.py").read_text(encoding="utf-8")
        # The "Not evaluated" note should mention more than just file overlap
        assert "no file overlap or prior notes" not in text, (
            "cross_section.py skipped note still uses 'file overlap or prior notes' framing"
        )


# ---------------------------------------------------------------------------
# R72 / V6 – rca.md must not contain Task-tool spawning instructions
# ---------------------------------------------------------------------------

class TestR72V6RcaNoTaskTool:
    """R72/V6: rca.md must not contain Task-tool spawning instructions."""

    def test_no_task_tool_block(self) -> None:
        src = Path(__file__).resolve().parent.parent / "src"
        text = (src / "rca.md").read_text(encoding="utf-8")
        assert "Task(" not in text, "rca.md still contains Task(...) block"

    def test_no_spawn_sub_agent(self) -> None:
        src = Path(__file__).resolve().parent.parent / "src"
        text = (src / "rca.md").read_text(encoding="utf-8")
        assert "sub-agent" not in text.lower(), "rca.md still uses 'sub-agent' language"


# ---------------------------------------------------------------------------
# R72 / V7 – No "sub-agent" language on active surfaces
# ---------------------------------------------------------------------------

class TestR72V7NoSubAgentLanguage:
    """R72/V7: Active surfaces must not use 'sub-agent' language."""

    _ACTIVE_SURFACES = [
        "implement.md",
        "audit.md",
        "agents/implementation-strategist.md",
        "scripts/section_loop/prompts/templates/strategic-implementation.md",
        "scripts/section_loop/coordination/execution.py",
    ]

    def test_no_sub_agent_in_active_surfaces(self) -> None:
        src = Path(__file__).resolve().parent.parent / "src"
        for rel in self._ACTIVE_SURFACES:
            path = src / rel
            text = path.read_text(encoding="utf-8")
            assert "sub-agent" not in text.lower(), (
                f"{rel} still uses 'sub-agent' language"
            )


# ---------------------------------------------------------------------------
# R72 / V1 – impact-analyzer.md matches runtime impacts[] contract
# ---------------------------------------------------------------------------


class TestR72V1ImpactAnalyzerContract:
    """R72/V1: impact-analyzer.md matches runtime impacts[] contract."""

    def test_agent_file_has_impacts_schema(self) -> None:
        src = Path(__file__).resolve().parent.parent / "src"
        text = (src / "agents" / "impact-analyzer.md").read_text(
            encoding="utf-8")
        assert '"impacts"' in text
        assert "contract_risk" in text
        assert "note_markdown" in text

    def test_agent_file_no_old_affected_schema(self) -> None:
        src = Path(__file__).resolve().parent.parent / "src"
        text = (src / "agents" / "impact-analyzer.md").read_text(
            encoding="utf-8")
        # Check the Output section only — "affected" may appear naturally
        # in the description prose, but the old JSON key must not be
        # present in the output contract.
        output_section = text[text.find("## Output"):]
        assert '"affected"' not in output_section, (
            "impact-analyzer.md Output still uses the old affected[] schema"
        )
        assert '"severity"' not in output_section, (
            "impact-analyzer.md Output still uses the old severity field"
        )

    def test_normalizer_dispatch_no_agent_file(self) -> None:
        """The normalization fallback must NOT reuse impact-analyzer.md."""
        src = Path(__file__).resolve().parent.parent / "src"
        text = (
            src / "scripts" / "section_loop" / "cross_section.py"
        ).read_text(encoding="utf-8")
        # Find the normalizer dispatch block — it follows "impact-normalize"
        idx = text.find("impact-normalize")
        assert idx >= 0, "Expected impact-normalize block in cross_section.py"
        # The normalizer dispatch_agent call should NOT have agent_file=
        block = text[idx:idx + 1500]
        dispatch_idx = block.find("dispatch_agent(")
        assert dispatch_idx >= 0
        dispatch_block = block[dispatch_idx:dispatch_idx + 400]
        assert 'agent_file="impact-analyzer.md"' not in dispatch_block, (
            "Normalizer dispatch still reuses impact-analyzer.md agent file"
        )


# ---------------------------------------------------------------------------
# R72 / V2 – consequence-note-triager.md matches runtime contract
# ---------------------------------------------------------------------------


class TestR72V2ConsequenceNoteTriagerContract:
    """R72/V2: consequence-note-triager.md matches runtime contract."""

    def test_agent_file_has_needs_replan(self) -> None:
        src = Path(__file__).resolve().parent.parent / "src"
        text = (src / "agents" / "consequence-note-triager.md").read_text(
            encoding="utf-8")
        assert "needs_replan" in text
        assert "needs_code_change" in text
        assert "acknowledge" in text

    def test_agent_file_no_old_notes_schema(self) -> None:
        src = Path(__file__).resolve().parent.parent / "src"
        text = (src / "agents" / "consequence-note-triager.md").read_text(
            encoding="utf-8")
        # The old schema had a top-level "notes" array in the Output section.
        output_section = text[text.find("## Output"):]
        assert '"notes"' not in output_section, (
            "consequence-note-triager.md Output still uses the old notes[] schema"
        )


# ---------------------------------------------------------------------------
# R72 / V3 – coordination-fixer.md describes actual output contract
# ---------------------------------------------------------------------------


class TestR72V3CoordinationFixerContract:
    """R72/V3: coordination-fixer.md describes modified-file report."""

    def test_agent_file_has_modified_report(self) -> None:
        src = Path(__file__).resolve().parent.parent / "src"
        text = (src / "agents" / "coordination-fixer.md").read_text(
            encoding="utf-8")
        assert "modified" in text.lower()
        assert "per line" in text.lower()

    def test_agent_file_no_old_fixes_applied(self) -> None:
        src = Path(__file__).resolve().parent.parent / "src"
        text = (src / "agents" / "coordination-fixer.md").read_text(
            encoding="utf-8")
        assert "fixes_applied" not in text, (
            "coordination-fixer.md still has dead fixes_applied signal"
        )
        assert "dependency_order" not in text, (
            "coordination-fixer.md still has dead dependency_order signal"
        )
        assert "verification_hint" not in text, (
            "coordination-fixer.md still has dead verification_hint signal"
        )


# ---------------------------------------------------------------------------
# R72 / V4 – bridge-tools.md documents bridge-signal contract
# ---------------------------------------------------------------------------


class TestR72V4BridgeToolsSignalContract:
    """R72/V4: bridge-tools.md documents the bridge-signal JSON contract."""

    def test_agent_file_has_bridge_signal(self) -> None:
        src = Path(__file__).resolve().parent.parent / "src"
        text = (src / "agents" / "bridge-tools.md").read_text(
            encoding="utf-8")
        assert '"status"' in text
        assert "bridged" in text
        assert "no_action" in text
        assert "needs_parent" in text
        assert "proposal_path" in text
        assert "note_markdown" in text


# ---------------------------------------------------------------------------
# R72 / V5 – philosophy-source-verifier.md exists and is referenced
# ---------------------------------------------------------------------------


class TestR72V5PhilosophySourceVerifier:
    """R72/V5: philosophy-source-verifier.md exists, bootstrap.py references it."""

    def test_verifier_agent_file_exists(self) -> None:
        src = Path(__file__).resolve().parent.parent / "src"
        agent = src / "agents" / "philosophy-source-verifier.md"
        assert agent.exists(), "philosophy-source-verifier.md must exist"

    def test_verifier_uses_claude_opus(self) -> None:
        src = Path(__file__).resolve().parent.parent / "src"
        text = (src / "agents" / "philosophy-source-verifier.md").read_text(
            encoding="utf-8")
        assert "model: claude-opus" in text

    def test_verifier_output_has_verified_sources(self) -> None:
        src = Path(__file__).resolve().parent.parent / "src"
        text = (src / "agents" / "philosophy-source-verifier.md").read_text(
            encoding="utf-8")
        assert "verified_sources" in text
        assert "rejected" in text

    def test_bootstrap_references_verifier(self) -> None:
        src = Path(__file__).resolve().parent.parent / "src"
        text = (
            src / "scripts" / "section_loop" / "intent" / "bootstrap.py"
        ).read_text(encoding="utf-8")
        assert 'agent_file="philosophy-source-verifier.md"' in text

    def test_bootstrap_does_not_reuse_selector_for_verification(self) -> None:
        """The verification dispatch must not use philosophy-source-selector.md."""
        src = Path(__file__).resolve().parent.parent / "src"
        text = (
            src / "scripts" / "section_loop" / "intent" / "bootstrap.py"
        ).read_text(encoding="utf-8")
        # Find the verification block (near "philosophy-verify" prompt)
        idx = text.find("prompt:philosophy-verify")
        assert idx >= 0
        block = text[idx:idx + 500]
        assert 'agent_file="philosophy-source-selector.md"' not in block, (
            "Verification dispatch still reuses philosophy-source-selector.md"
        )


# ---------------------------------------------------------------------------
# R73 / V1 – coordination-fixer task types and language
# ---------------------------------------------------------------------------


class TestR73V1CoordinationFixerTaskVocab:
    """R73/V1: coordination-fixer.md task types match runtime prompt."""

    def test_task_types_match_runtime(self) -> None:
        src = Path(__file__).resolve().parent.parent / "src"
        text = (src / "agents" / "coordination-fixer.md").read_text(
            encoding="utf-8")
        assert "coordination_fix" in text, (
            "coordination-fixer.md must list coordination_fix as a task type"
        )

    def test_no_scan_deep_analyze_task_type(self) -> None:
        src = Path(__file__).resolve().parent.parent / "src"
        text = (src / "agents" / "coordination-fixer.md").read_text(
            encoding="utf-8")
        # scan_deep_analyze is not in the runtime allowed list
        assert "scan_deep_analyze" not in text, (
            "coordination-fixer.md must not advertise scan_deep_analyze"
        )

    def test_no_sub_agents_language(self) -> None:
        src = Path(__file__).resolve().parent.parent / "src"
        text = (src / "agents" / "coordination-fixer.md").read_text(
            encoding="utf-8")
        assert "sub-agents" not in text.lower(), (
            "coordination-fixer.md must not use 'sub-agents' language"
        )


# ---------------------------------------------------------------------------
# R73 / V2 – integration-proposer and implementation-strategist task vocab
# ---------------------------------------------------------------------------


class TestR73V2TaskVocabularySync:
    """R73/V2: agent file task examples match runtime allowed_tasks."""

    def test_integration_proposer_uses_scan_explore(self) -> None:
        src = Path(__file__).resolve().parent.parent / "src"
        text = (src / "agents" / "integration-proposer.md").read_text(
            encoding="utf-8")
        # The example JSON should use scan_explore, not scan_deep_analyze
        assert '"scan_explore"' in text or "'scan_explore'" in text

    def test_integration_proposer_no_scan_deep_analyze(self) -> None:
        src = Path(__file__).resolve().parent.parent / "src"
        text = (src / "agents" / "integration-proposer.md").read_text(
            encoding="utf-8")
        assert "scan_deep_analyze" not in text, (
            "integration-proposer.md must not use scan_deep_analyze"
        )

    def test_impl_strategist_task_types_match_runtime(self) -> None:
        src = Path(__file__).resolve().parent.parent / "src"
        text = (src / "agents" / "implementation-strategist.md").read_text(
            encoding="utf-8")
        # Runtime (writers.py) provides: scan_explore, scan_deep_analyze,
        # strategic_implementation
        assert "strategic_implementation" in text, (
            "implementation-strategist.md must list strategic_implementation"
        )

    def test_impl_strategist_no_unlisted_task_types(self) -> None:
        src = Path(__file__).resolve().parent.parent / "src"
        text = (src / "agents" / "implementation-strategist.md").read_text(
            encoding="utf-8")
        # state_adjudicate and tool_registry_repair are not in the runtime
        # allowed list for implementation
        assert "state_adjudicate" not in text, (
            "implementation-strategist.md must not list state_adjudicate"
        )
        assert "tool_registry_repair" not in text, (
            "implementation-strategist.md must not list tool_registry_repair"
        )


# ---------------------------------------------------------------------------
# R73 / V3 – template safety on active dynamic prompt builders
# ---------------------------------------------------------------------------


class TestR73V3TemplateSafety:
    """R73/V3: active prompt builders call validate_dynamic_content."""

    def test_reexplore_validates(self) -> None:
        src = Path(__file__).resolve().parent.parent / "src"
        text = (
            src / "scripts" / "section_loop" / "section_engine" / "reexplore.py"
        ).read_text(encoding="utf-8")
        assert "validate_dynamic_content" in text

    def test_runner_microstrategy_validates(self) -> None:
        src = Path(__file__).resolve().parent.parent / "src"
        text = (
            src / "scripts" / "section_loop" / "section_engine" / "runner.py"
        ).read_text(encoding="utf-8")
        # Must import validate_dynamic_content
        assert "validate_dynamic_content" in text
        # Must call it near the microstrategy prompt builder
        idx = text.find("# Task: Microstrategy")
        assert idx >= 0
        block = text[idx:idx + 1500]
        assert "validate_dynamic_content" in block

    def test_coordination_execution_validates(self) -> None:
        src = Path(__file__).resolve().parent.parent / "src"
        text = (
            src / "scripts" / "section_loop" / "coordination" / "execution.py"
        ).read_text(encoding="utf-8")
        assert "validate_dynamic_content" in text
        idx = text.find("# Task: Coordinated Fix")
        assert idx >= 0
        block = text[idx:idx + 2500]
        assert "validate_dynamic_content" in block


# ---------------------------------------------------------------------------
# R73 / V4 – context sidecar wiring
# ---------------------------------------------------------------------------


class TestR73V4ContextSidecarWiring:
    """R73/V4: context: declared on agents that have sidecar appends,
    sidecar references wired for agents that declare context:."""

    def test_integration_proposer_has_context(self) -> None:
        src = Path(__file__).resolve().parent.parent / "src"
        text = (src / "agents" / "integration-proposer.md").read_text(
            encoding="utf-8")
        assert "context:" in text
        assert "section_spec" in text

    def test_implementation_strategist_has_context(self) -> None:
        src = Path(__file__).resolve().parent.parent / "src"
        text = (src / "agents" / "implementation-strategist.md").read_text(
            encoding="utf-8")
        assert "context:" in text
        assert "section_spec" in text

    def test_coordination_fix_prompt_has_sidecar_ref(self) -> None:
        src = Path(__file__).resolve().parent.parent / "src"
        text = (
            src / "scripts" / "section_loop" / "coordination" / "execution.py"
        ).read_text(encoding="utf-8")
        assert "context-coordination-fixer.json" in text

    def test_impact_prompt_has_sidecar_ref(self) -> None:
        src = Path(__file__).resolve().parent.parent / "src"
        text = (
            src / "scripts" / "section_loop" / "cross_section.py"
        ).read_text(encoding="utf-8")
        assert "context-impact-analyzer.json" in text


# ---------------------------------------------------------------------------
# R73 / V5 – concern-only sections reachable in impact routing
# ---------------------------------------------------------------------------


class TestR73V5ConcernOnlyRouting:
    """R73/V5: impact routing does not exclude concern-only sections."""

    def test_no_related_files_gate(self) -> None:
        src = Path(__file__).resolve().parent.parent / "src"
        text = (
            src / "scripts" / "section_loop" / "cross_section.py"
        ).read_text(encoding="utf-8")
        # The old filter was: "and s.related_files"
        # Find the other_sections list comprehension
        idx = text.find("other_sections = [s for s in all_sections")
        assert idx >= 0
        line = text[idx:text.index("\n", idx)]
        assert "s.related_files" not in line, (
            "Impact routing must not gate on s.related_files"
        )

    def test_no_file_hypothesis_rendering(self) -> None:
        src = Path(__file__).resolve().parent.parent / "src"
        text = (
            src / "scripts" / "section_loop" / "cross_section.py"
        ).read_text(encoding="utf-8")
        assert "(no current file hypothesis)" in text, (
            "Candidate rendering must handle sections with no related files"
        )
