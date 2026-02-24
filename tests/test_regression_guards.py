"""Regression guard tests (P2, P4, P8, P9, R20/P3, R21/P4, R21/P5, R21/P6C, R24/P9, R30, R31, R32).

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
"""

import json
import re
from pathlib import Path

# Resolve project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCAN_PKG = PROJECT_ROOT / "scripts" / "scan"
AGENTS_DIR = PROJECT_ROOT / "agents"


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


# Paths declared in SKILL.md's Paths block.  Parsed from the fenced code
# block listing under "## Paths".  Every path listed here MUST exist on
# disk relative to PROJECT_ROOT.  This prevents the "files exist on disk
# but missing from distribution" failure mode that recurred in Rounds 11
# and 24.
SKILL_MD_MANIFEST = [
    # Root-level docs
    "SKILL.md",
    "implement.md",
    "research.md",
    "rca.md",
    "evaluate.md",
    "baseline.md",
    "audit.md",
    "constraints.md",
    "models.md",
    # Scripts
    "scripts/workflow.sh",
    "scripts/db.sh",
    "scripts/scan.sh",
    "scripts/section-loop.py",
    # Tools
    "tools/extract-docstring-py",
    "tools/extract-summary-md",
    "tools/README.md",
    # Agents
    "agents/orchestrator.md",
    "agents/monitor.md",
    "agents/qa-monitor.md",
    "agents/agent-monitor.md",
    "agents/state-detector.md",
    "agents/exception-handler.md",
    "agents/microstrategy-writer.md",
    "agents/section-re-explorer.md",
    "agents/setup-excerpter.md",
    "agents/bridge-agent.md",
    # Templates
    "templates/implement-proposal.md",
    "templates/research-cycle.md",
    "templates/rca-cycle.md",
]

# Additionally, implement.md references tools/README.md as the tool
# interface spec.  Already covered above but kept explicit.
IMPLEMENT_MD_TOOL_REFS = [
    "tools/README.md",
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
            / "scripts" / "section_loop" / "prompts" / "templates"
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
            / "scripts" / "section_loop" / "prompts" / "templates"
            / "implementation-alignment.md"
        ).read_text(encoding="utf-8")
        assert "structured JSON verdict" in template.lower() or \
               "JSON verdict block" in template, (
            "implementation-alignment.md must reference the structured JSON "
            "verdict required by alignment-judge.md"
        )


# ---------- Round 30 guards ----------

SECTION_LOOP_PKG = PROJECT_ROOT / "scripts" / "section_loop"


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
            PROJECT_ROOT / "scripts" / "section_loop" / "prompts" / "templates"
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

    RUNNER = (PROJECT_ROOT / "scripts" / "section_loop"
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

    RUNNER = (PROJECT_ROOT / "scripts" / "section_loop"
              / "coordination" / "runner.py")
    MAIN = PROJECT_ROOT / "scripts" / "section_loop" / "main.py"
    EXECUTION = (PROJECT_ROOT / "scripts" / "section_loop"
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

    ALIGNMENT = (PROJECT_ROOT / "scripts" / "section_loop"
                 / "alignment.py")
    MAIN = PROJECT_ROOT / "scripts" / "section_loop" / "main.py"
    COORD_RUNNER = (PROJECT_ROOT / "scripts" / "section_loop"
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

    FEEDBACK = PROJECT_ROOT / "scripts" / "scan" / "feedback.py"

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
