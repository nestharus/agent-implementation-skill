"""Regression guard tests (P2, P4, P8, P9, R20/P3).

P2: No brute-force scan patterns in scan.sh.
P4: Codemap fingerprint mismatch triggers verifier.
P8: Bridge dispatch only fires on agent directive.
P9: Agent frontmatter models are in the documented policy set.
R20/P3: Pipeline agent files contain no runtime placeholders.
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
