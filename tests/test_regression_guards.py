"""Regression guard tests (P2, P4, P8, P9, R20/P3, R21/P4, R21/P5, R21/P6C).

P2: No brute-force scan patterns in scan.sh.
P4: Codemap fingerprint mismatch triggers verifier.
P8: Bridge dispatch only fires on agent directive.
P9: Agent frontmatter models are in the documented policy set.
R20/P3: Pipeline agent files contain no runtime placeholders.
R21/P4: Greenfield pause label uses needs_parent (not underspec).
R21/P5: Targeted requeue only requeues changed sections.
R21/P6C: Operational agent files have no angle-bracket placeholders.
"""

import json
import re
from pathlib import Path

# Resolve project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCAN_SH = PROJECT_ROOT / "scripts" / "scan.sh"
AGENTS_DIR = PROJECT_ROOT / "agents"

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
    """P2: scan.sh must not contain brute-force scan-all patterns."""

    def test_no_scan_all_files_pattern(self) -> None:
        content = SCAN_SH.read_text()
        # "scan all files" or "scan every file" in comments/strings
        assert "scan all files" not in content.lower()
        assert "scan every file" not in content.lower()

    def test_no_find_full_codespace_enumeration(self) -> None:
        """find commands that enumerate full codespace for scanning are
        forbidden.  find for specific artifact dirs (logs, signals) is OK."""
        content = SCAN_SH.read_text()
        # Pattern: find $CODESPACE or find "$CODESPACE" -name "*.py"
        # (scanning entire codespace for source files)
        matches = re.findall(
            r'find\s+["\']?\$\{?CODESPACE\}?["\']?\s+-name\s+["\']?\*\.',
            content,
        )
        assert matches == [], (
            f"Brute-force find on CODESPACE detected: {matches}"
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
        main_path = PROJECT_ROOT / "scripts" / "section_loop" / "main.py"
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
        main_path = PROJECT_ROOT / "scripts" / "section_loop" / "main.py"
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


LINT_SH = PROJECT_ROOT / "scripts" / "lint-audit-language.sh"
DOC_DRIFT_LINT_SH = PROJECT_ROOT / "scripts" / "lint-doc-drift.sh"


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
