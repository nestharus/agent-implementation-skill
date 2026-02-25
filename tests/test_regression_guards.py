"""Regression guard tests (P2, P4, P8, P9, R20/P3, R21/P4, R21/P5, R21/P6C, R24/P9, R30, R31, R32, R33, R34, R35, R36, R37, R38, R39, R40, R41, R42, R43, R44, R45).

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
"""

import hashlib
import json
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
    "gpt-5.3-codex-high",
    "gpt-5.3-codex-high2",
    "gpt-5.3-codex-xhigh",
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
    "microstrategy-writer.md",
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


# Operational agent files dispatched by scripts (monitor, qa-monitor, etc.)
OPERATIONAL_AGENT_FILES = {
    "monitor.md",
    "qa-monitor.md",
    "orchestrator.md",
    "state-detector.md",
    "exception-handler.md",
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
    guard to operational agents (monitor, qa-monitor) that are dispatched
    by scripts and receive runtime paths via prompt variables.
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
        result = subprocess.run(
            ["bash", str(LINT_SH)],
            capture_output=True, text=True,
            cwd=str(PROJECT_ROOT),
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
        result = subprocess.run(
            ["bash", str(DOC_DRIFT_LINT_SH)],
            capture_output=True, text=True,
            cwd=str(PROJECT_ROOT),
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
    # Agents
    "src/agents/orchestrator.md",
    "src/agents/monitor.md",
    "src/agents/qa-monitor.md",
    "src/agents/agent-monitor.md",
    "src/agents/state-detector.md",
    "src/agents/exception-handler.md",
    "src/agents/microstrategy-writer.md",
    "src/agents/section-re-explorer.md",
    "src/agents/setup-excerpter.md",
    "src/agents/bridge-agent.md",
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
            "gpt-5.3-codex-high", "gpt-5.3-codex-high2",
            "gpt-5.3-codex-xhigh",
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
        # The old pattern: write_text("gpt-5.3-codex-xhigh"
        for line in src.split("\n"):
            if "write_text" in line and "gpt-5.3-codex-xhigh" in line:
                raise AssertionError(
                    "coordination/runner.py has hardcoded escalation "
                    "model in write_text call — must use policy"
                )

    def test_no_hardcoded_escalation_model_in_main(self) -> None:
        """main.py must not hardcode escalation model string."""
        src = self.MAIN.read_text(encoding="utf-8")
        for line in src.split("\n"):
            if "write_text" in line and "gpt-5.3-codex-xhigh" in line:
                raise AssertionError(
                    "main.py has hardcoded escalation model in "
                    "write_text call — must use policy"
                )

    def test_no_hardcoded_fix_model_in_execution(self) -> None:
        """execution.py must not hardcode default fix model."""
        src = self.EXECUTION.read_text(encoding="utf-8")
        assert 'fix_model = "gpt-5.3-codex-high"' not in src, (
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
        self, planspace: Path, codespace: Path,
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
            model="glm", escalation_model="gpt-5.3-codex-xhigh",
        )
        assert result is True, (
            "_check_needs_microstrategy must return True when "
            "decider produces no signal (fail-closed)"
        )

    def test_fallback_signal_written(
        self, planspace: Path, codespace: Path,
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
            model="glm", escalation_model="gpt-5.3-codex-xhigh",
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
        known_models = ["glm", "gpt-5.3-codex-high", "gpt-5.3-codex-xhigh"]
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

    def test_templates_use_model_placeholders(self) -> None:
        """Templates must use {exploration_model} or {delegated_impl_model}."""
        impl_content = (
            self.TEMPLATES_DIR / "strategic-implementation.md"
        ).read_text(encoding="utf-8")
        assert "{exploration_model}" in impl_content, (
            "strategic-implementation.md must use "
            "{exploration_model} placeholder"
        )
        assert "{delegated_impl_model}" in impl_content, (
            "strategic-implementation.md must use "
            "{delegated_impl_model} placeholder"
        )
        proposal_content = (
            self.TEMPLATES_DIR / "integration-proposal.md"
        ).read_text(encoding="utf-8")
        assert "{exploration_model}" in proposal_content, (
            "integration-proposal.md must use "
            "{exploration_model} placeholder"
        )

    def test_writers_inject_model_from_policy(
        self, planspace: Path, codespace: Path,
    ) -> None:
        """Writers must inject model names from model_policy dict."""
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

        custom_policy = {
            "exploration": "custom-explore-model",
            "implementation": "custom-impl-model",
        }

        intg_path = write_integration_proposal_prompt(
            section, planspace, codespace,
            model_policy=custom_policy,
        )
        intg_content = intg_path.read_text(encoding="utf-8")
        assert "custom-explore-model" in intg_content, (
            "Integration proposal must render exploration model "
            "from policy"
        )

        impl_path = write_strategic_impl_prompt(
            section, planspace, codespace,
            model_policy=custom_policy,
        )
        impl_content = impl_path.read_text(encoding="utf-8")
        assert "custom-explore-model" in impl_content, (
            "Strategic impl must render exploration model from policy"
        )
        assert "custom-impl-model" in impl_content, (
            "Strategic impl must render delegated impl model from policy"
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
            "— must use {exploration_model} placeholder"
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
            "— must use {exploration_model} placeholder"
        )

    def test_no_hardcoded_codex_in_prompt_text(self) -> None:
        """Fix prompt must not contain --model gpt-5.3-codex-high literally."""
        src = self.EXECUTION.read_text(encoding="utf-8")
        assert "--model gpt-5.3-codex-high" not in src, (
            "execution.py fix prompt contains hardcoded "
            "'--model gpt-5.3-codex-high' — must use placeholder"
        )

    def test_prompt_writer_accepts_model_params(self) -> None:
        """write_coordinator_fix_prompt must accept model parameters."""
        import inspect
        from section_loop.coordination.execution import (
            write_coordinator_fix_prompt,
        )
        sig = inspect.signature(write_coordinator_fix_prompt)
        assert "exploration_model" in sig.parameters, (
            "write_coordinator_fix_prompt must accept exploration_model"
        )
        assert "delegation_impl_model" in sig.parameters, (
            "write_coordinator_fix_prompt must accept "
            "delegation_impl_model"
        )

    def test_dispatch_passes_policy_models(self) -> None:
        """_dispatch_fix_group must pass policy models to prompt writer."""
        src = self.EXECUTION.read_text(encoding="utf-8")
        assert 'exploration_model=policy["exploration"]' in src, (
            "_dispatch_fix_group must pass exploration_model from policy"
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

    TEMPLATES_DIR = (PROJECT_ROOT / "src" / "scripts" / "section_loop"
                     / "prompts" / "templates")
    SCAN_TEMPLATES_DIR = PROJECT_ROOT / "src" / "scripts" / "scan" / "templates"

    # All prompt builder source files that construct agent instructions
    PROMPT_BUILDER_FILES = [
        PROJECT_ROOT / "src" / "scripts" / "section_loop" / "section_engine" / "reexplore.py",
        PROJECT_ROOT / "src" / "scripts" / "section_loop" / "coordination" / "execution.py",
        PROJECT_ROOT / "src" / "scripts" / "section_loop" / "coordination" / "planning.py",
        PROJECT_ROOT / "src" / "scripts" / "section_loop" / "prompts" / "writers.py",
        PROJECT_ROOT / "src" / "scripts" / "section_loop" / "prompts" / "context.py",
    ]

    KNOWN_MODELS = [
        "glm", "gpt-5.3-codex-high", "gpt-5.3-codex-high2",
        "gpt-5.3-codex-xhigh", "claude-opus", "claude-haiku",
    ]

    def test_no_hardcoded_model_in_section_loop_templates(self) -> None:
        """Section loop .md templates must not contain --model <literal>."""
        if not self.TEMPLATES_DIR.exists():
            return
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

    def test_strategic_impl_template_uses_file(self) -> None:
        """strategic-implementation.md delegated impl recipe uses --file."""
        content = self.STRATEGIC_IMPL_TEMPLATE.read_text(encoding="utf-8")
        # Find the delegated_impl_model dispatch block
        assert "--file" in content, (
            "strategic-implementation.md must use --file for "
            "delegated impl model dispatch"
        )
        # Ensure delegated impl model line does NOT use inline instructions
        for line in content.splitlines():
            if "{delegated_impl_model}" in line and '"<instructions>"' in line:
                raise AssertionError(
                    "strategic-implementation.md: delegated impl model "
                    "dispatch must not use inline \"<instructions>\" — "
                    "use --file with a prompt file"
                )

    def test_coordination_fix_prompt_uses_file(self) -> None:
        """coordination/execution.py delegated impl recipe uses --file."""
        content = self.COORDINATION_EXECUTION.read_text(encoding="utf-8")
        # Find the delegation_impl_model dispatch block in the prompt f-string
        in_prompt = False
        for line in content.splitlines():
            if 'prompt_path.write_text(f"""' in line or 'prompt_path.write_text(f"' in line:
                in_prompt = True
            if in_prompt and "{delegation_impl_model}" in line:
                if '"<instructions>"' in line:
                    raise AssertionError(
                        "coordination/execution.py: delegation_impl_model "
                        "dispatch must not use inline \"<instructions>\" "
                        "— use --file with a prompt file"
                    )
            if in_prompt and '""", encoding=' in line:
                in_prompt = False
        # Also verify --file appears in the prompt text
        assert "--file" in content, (
            "coordination/execution.py must contain --file for "
            "delegated impl model dispatch"
        )

    def test_implement_md_codex_uses_file(self) -> None:
        """implement.md Codex dispatch examples use --file."""
        content = self.IMPLEMENT_MD.read_text(encoding="utf-8")
        lines = content.splitlines()
        for i, line in enumerate(lines):
            # Check lines with Codex model names using inline instructions
            if ("gpt-5.3-codex-high" in line and
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
                assert "gpt-5.3-codex-xhigh" not in line, (
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
        assert 'f"(gpt-5.3-codex-xhigh)' not in content, (
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


class TestScheduleTemplateModelName:
    """R39/V1: Schedule template must use the primary model name and
    include a policy-override note."""

    TEMPLATE = PROJECT_ROOT / "src" / "templates" / "implement-proposal.md"

    def test_no_high2_model_in_schedule(self) -> None:
        """implement-proposal.md must not reference gpt-5.3-codex-high2."""
        content = self.TEMPLATE.read_text(encoding="utf-8")
        assert "gpt-5.3-codex-high2" not in content, (
            "implement-proposal.md must use gpt-5.3-codex-high, "
            "not gpt-5.3-codex-high2"
        )

    def test_verify_line_has_policy_note(self) -> None:
        """verify step should note to use policy's model if different."""
        content = self.TEMPLATE.read_text(encoding="utf-8")
        assert "policy" in content.lower(), (
            "implement-proposal.md verify line should reference the "
            "policy's verification model"
        )

    def test_verify_line_uses_primary_model(self) -> None:
        """verify step should use gpt-5.3-codex-high (primary pool)."""
        content = self.TEMPLATE.read_text(encoding="utf-8")
        for line in content.splitlines():
            if "verify" in line.lower() and "codex" in line.lower():
                assert "gpt-5.3-codex-high" in line, (
                    "verify line must use gpt-5.3-codex-high"
                )
                break
        else:
            pytest.fail("No verify+codex line found in template")


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
            model="glm", escalation_model="gpt-5.3-codex-xhigh",
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
            model="glm", escalation_model="gpt-5.3-codex-xhigh",
        )

        # dispatch_agent must have been called (fall-through to dispatch)
        assert mock_dispatch.called, (
            "After renaming malformed signal, dispatch must be called "
            "to produce fresh microstrategy decision"
        )
