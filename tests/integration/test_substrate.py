"""Tests for Stage 3.5 Shared Integration Substrate (SIS) discovery.

Mock boundary: ``substrate.substrate_discoverer._dispatch_agent`` is mocked.
Everything else — trigger detection, schema validation, related-files
updates, prompt building — runs for real.

Tests cover:
- Schema validation (shard + seed-plan)
- Trigger detection (greenfield, brownfield, vacuum counting)
- Related-files updates (signal application, deduplication, missing blocks)
- Prompt building (shard, pruner, seeder)
- Runner orchestration (full pipeline, skip, failure paths)
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---- Fixtures ----


@pytest.fixture()
def substrate_planspace(tmp_path: Path) -> Path:
    """Create a planspace directory for substrate tests."""
    ps = tmp_path / "planspace"
    ps.mkdir()
    artifacts = ps / "artifacts"
    for subdir in (
        "sections",
        "signals",
        "substrate",
        "substrate/shards",
        "substrate/prompts",
        "substrate/logs",
        "signals/related-files-update",
        "inputs",
    ):
        (artifacts / subdir).mkdir(parents=True, exist_ok=True)
    return ps


@pytest.fixture()
def substrate_codespace(tmp_path: Path) -> Path:
    """Create a minimal codespace for substrate tests."""
    cs = tmp_path / "codespace"
    cs.mkdir()
    (cs / "src").mkdir()
    (cs / "src" / "main.py").write_text("def main(): pass\n")
    return cs


def _write_section(
    sections_dir: Path,
    num: str,
    related_files: list[str] | None = None,
) -> Path:
    """Write a minimal section spec file."""
    content = f"# Section {num}\n\nTest section.\n\n## Related Files\n\n"
    if related_files:
        for rf in related_files:
            content += f"### {rf}\n\nExisting file.\n\n"
    path = sections_dir / f"section-{num}.md"
    path.write_text(content, encoding="utf-8")
    return path


def _write_project_mode(artifacts_dir: Path, mode: str) -> None:
    """Write project-mode.txt signal."""
    artifacts_dir / "project-mode.txt"
    (artifacts_dir / "project-mode.txt").write_text(mode, encoding="utf-8")


def _write_project_mode_json(artifacts_dir: Path, mode: str) -> None:
    """Write project-mode.json signal."""
    signals_dir = artifacts_dir / "signals"
    signals_dir.mkdir(parents=True, exist_ok=True)
    (signals_dir / "project-mode.json").write_text(
        json.dumps({"mode": mode}), encoding="utf-8",
    )


# ---- Schema validation tests ----


class TestShardValidation:
    """Test shard JSON validation."""

    def test_valid_shard(self) -> None:
        from scan.substrate.schemas import validate_shard

        shard = {
            "schema_version": 1,
            "section_number": "01",
            "mode": "greenfield",
            "touchpoints": ["types", "config"],
            "provides": [{"id": "auth.check", "kind": "api", "summary": "Auth check"}],
            "needs": [],
            "shared_seams": [],
            "open_questions": [],
        }
        assert validate_shard(shard) == []

    def test_missing_required_fields(self) -> None:
        from scan.substrate.schemas import validate_shard

        errors = validate_shard({"schema_version": 1})
        # Missing: section_number, mode, touchpoints, provides, needs,
        # shared_seams, open_questions
        assert len(errors) == 7

    def test_invalid_mode(self) -> None:
        from scan.substrate.schemas import validate_shard

        shard = {
            "schema_version": 1,
            "section_number": "01",
            "mode": "invalid",
            "touchpoints": [],
            "provides": [],
            "needs": [],
            "shared_seams": [],
            "open_questions": [],
        }
        errors = validate_shard(shard)
        assert any("invalid mode" in e for e in errors)

    def test_unknown_touchpoint(self) -> None:
        from scan.substrate.schemas import validate_shard

        shard = {
            "schema_version": 1,
            "section_number": "01",
            "mode": "greenfield",
            "touchpoints": ["types", "unknown_tp"],
            "provides": [],
            "needs": [],
            "shared_seams": [],
            "open_questions": [],
        }
        errors = validate_shard(shard)
        # V10/R67: touchpoints are open strings — unknown values accepted
        assert not any("unknown touchpoint" in e for e in errors)

    def test_wrong_schema_version(self) -> None:
        from scan.substrate.schemas import validate_shard

        shard = {
            "schema_version": 99,
            "section_number": "01",
            "mode": "greenfield",
            "touchpoints": [],
            "provides": [],
            "needs": [],
            "shared_seams": [],
            "open_questions": [],
        }
        errors = validate_shard(shard)
        assert any("unsupported schema_version" in e for e in errors)

    def test_non_dict_input(self) -> None:
        from scan.substrate.schemas import validate_shard

        assert validate_shard("not a dict") == ["shard is not a JSON object"]

    def test_touchpoints_not_list(self) -> None:
        from scan.substrate.schemas import validate_shard

        shard = {
            "schema_version": 1,
            "section_number": "01",
            "mode": "greenfield",
            "touchpoints": "types",
            "provides": [],
            "needs": [],
            "shared_seams": [],
            "open_questions": [],
        }
        errors = validate_shard(shard)
        assert any("touchpoints must be a list" in e for e in errors)


class TestSeedPlanValidation:
    """Test seed-plan JSON validation."""

    def test_valid_seed_plan(self) -> None:
        from scan.substrate.schemas import validate_seed_plan

        plan = {
            "schema_version": 1,
            "anchors": [
                {"path": "src/types.py", "purpose": "Shared types"},
            ],
            "wire_sections": [1, 4],
        }
        assert validate_seed_plan(plan) == []

    def test_missing_fields(self) -> None:
        from scan.substrate.schemas import validate_seed_plan

        errors = validate_seed_plan({})
        assert len(errors) == 3

    def test_anchor_missing_path(self) -> None:
        from scan.substrate.schemas import validate_seed_plan

        plan = {
            "schema_version": 1,
            "anchors": [{"purpose": "no path"}],
            "wire_sections": [],
        }
        errors = validate_seed_plan(plan)
        assert any("missing 'path'" in e for e in errors)

    def test_anchor_invalid_kind(self) -> None:
        from scan.substrate.schemas import validate_seed_plan

        plan = {
            "schema_version": 1,
            "anchors": [{"path": "x.py", "kind": "unknown_kind"}],
            "wire_sections": [],
        }
        errors = validate_seed_plan(plan)
        # V10/R67: kinds are open strings — unknown values accepted
        assert not any("unknown kind" in e for e in errors)


class TestFailClosedReading:
    """Test fail-closed JSON reading behavior."""

    def test_read_valid_shard(self, tmp_path: Path) -> None:
        from scan.substrate.schemas import read_shard_failclosed

        shard = {
            "schema_version": 1,
            "section_number": "01",
            "mode": "greenfield",
            "touchpoints": [],
            "provides": [],
            "needs": [],
            "shared_seams": [],
            "open_questions": [],
        }
        path = tmp_path / "shard.json"
        path.write_text(json.dumps(shard))
        assert read_shard_failclosed(path) == shard

    def test_malformed_json_renames(self, tmp_path: Path) -> None:
        from scan.substrate.schemas import read_shard_failclosed

        path = tmp_path / "shard.json"
        path.write_text("{bad json")
        assert read_shard_failclosed(path) is None
        assert (tmp_path / "shard.malformed.json").exists()
        assert not path.exists()

    def test_invalid_shard_renames(self, tmp_path: Path) -> None:
        from scan.substrate.schemas import read_shard_failclosed

        path = tmp_path / "shard.json"
        path.write_text(json.dumps({"schema_version": 99}))
        assert read_shard_failclosed(path) is None
        assert (tmp_path / "shard.malformed.json").exists()

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        from scan.substrate.schemas import read_shard_failclosed

        assert read_shard_failclosed(tmp_path / "nonexistent.json") is None

    def test_read_valid_seed_plan(self, tmp_path: Path) -> None:
        from scan.substrate.schemas import read_seed_plan_failclosed

        plan = {
            "schema_version": 1,
            "anchors": [],
            "wire_sections": [],
        }
        path = tmp_path / "seed-plan.json"
        path.write_text(json.dumps(plan))
        assert read_seed_plan_failclosed(path) == plan


# ---- Trigger detection tests ----


class TestTriggerDetection:
    """Test should_run / trigger detection logic."""

    def test_greenfield_triggers_all_sections(
        self, substrate_planspace: Path, substrate_codespace: Path,
    ) -> None:
        from scan.substrate.substrate_discoverer import run_substrate_discovery

        artifacts = substrate_planspace / "artifacts"
        _write_project_mode(artifacts, "greenfield")
        _write_section(artifacts / "sections", "01")
        _write_section(artifacts / "sections", "02")
        _write_section(artifacts / "sections", "03")

        # We'll mock dispatch to track what gets called
        with patch("scan.substrate.substrate_discoverer._dispatch_agent") as mock_dispatch:
            mock_dispatch.return_value = False  # all agents "fail"
            run_substrate_discovery(substrate_planspace, substrate_codespace)

        # Shard should be called for all 3 sections
        assert mock_dispatch.call_count >= 3

    def test_brownfield_with_two_vacuum_triggers(
        self, substrate_planspace: Path, substrate_codespace: Path,
    ) -> None:
        from scan.substrate.substrate_discoverer import run_substrate_discovery

        artifacts = substrate_planspace / "artifacts"
        _write_project_mode(artifacts, "brownfield")

        # Section 01 has existing related files (not vacuum)
        (substrate_codespace / "src" / "existing.py").write_text("pass\n")
        _write_section(artifacts / "sections", "01", ["src/existing.py"])

        # Sections 02, 03 are vacuum (no existing files)
        _write_section(artifacts / "sections", "02", ["src/nonexistent1.py"])
        _write_section(artifacts / "sections", "03", ["src/nonexistent2.py"])

        with patch("scan.substrate.substrate_discoverer._dispatch_agent") as mock_dispatch:
            mock_dispatch.return_value = False
            run_substrate_discovery(substrate_planspace, substrate_codespace)

        # Shard should be called for sections 02 and 03 only
        assert mock_dispatch.call_count >= 2

    def test_brownfield_one_vacuum_skips(
        self, substrate_planspace: Path, substrate_codespace: Path,
    ) -> None:
        from scan.substrate.substrate_discoverer import run_substrate_discovery

        artifacts = substrate_planspace / "artifacts"
        _write_project_mode(artifacts, "brownfield")

        (substrate_codespace / "src" / "a.py").write_text("pass\n")
        _write_section(artifacts / "sections", "01", ["src/a.py"])
        _write_section(artifacts / "sections", "02", ["src/nonexistent.py"])

        with patch("scan.substrate.substrate_discoverer._dispatch_agent") as mock_dispatch:
            result = run_substrate_discovery(
                substrate_planspace, substrate_codespace,
            )

        # Should skip — only 1 vacuum section, threshold is 2
        mock_dispatch.assert_not_called()
        assert result is True  # skip is success

        # Check status signal
        status = json.loads(
            (artifacts / "substrate" / "status.json").read_text()
        )
        assert status["state"] == "SKIPPED"

    def test_no_project_mode_returns_needs_parent(
        self, substrate_planspace: Path, substrate_codespace: Path,
    ) -> None:
        from scan.substrate.substrate_discoverer import run_substrate_discovery

        # No project-mode file at all
        _write_section(
            substrate_planspace / "artifacts" / "sections", "01",
        )

        result = run_substrate_discovery(
            substrate_planspace, substrate_codespace,
        )
        assert result is False

        status = json.loads(
            (substrate_planspace / "artifacts" / "substrate" / "status.json")
            .read_text()
        )
        assert status["state"] == "NEEDS_PARENT"

    def test_json_mode_preferred_over_txt(
        self, substrate_planspace: Path, substrate_codespace: Path,
    ) -> None:
        from scan.substrate.substrate_discoverer import _read_project_mode

        artifacts = substrate_planspace / "artifacts"
        # Write conflicting modes
        _write_project_mode(artifacts, "brownfield")
        _write_project_mode_json(artifacts, "greenfield")

        mode = _read_project_mode(artifacts)
        assert mode == "greenfield"

    def test_malformed_json_mode_renames_to_malformed(
        self, substrate_planspace: Path,
    ) -> None:
        """_read_project_mode renames malformed JSON per corruption-preserving pattern."""
        from scan.substrate.substrate_discoverer import _read_project_mode

        artifacts = substrate_planspace / "artifacts"
        signals_dir = artifacts / "signals"
        signals_dir.mkdir(parents=True, exist_ok=True)
        json_path = signals_dir / "project-mode.json"
        json_path.write_text("{bad json", encoding="utf-8")

        # Also write txt fallback so we get a result
        (artifacts / "project-mode.txt").write_text("brownfield", encoding="utf-8")

        mode = _read_project_mode(artifacts)
        assert mode == "brownfield"  # falls back to txt
        assert not json_path.exists()  # original removed
        assert (signals_dir / "project-mode.malformed.json").exists()

    def test_vacuum_with_no_related_files_block(
        self, substrate_planspace: Path, substrate_codespace: Path,
    ) -> None:
        """Section with no ## Related Files block is vacuum."""
        from scan.substrate.substrate_discoverer import _count_existing_related

        sections_dir = substrate_planspace / "artifacts" / "sections"
        # Write a section without ## Related Files
        path = sections_dir / "section-01.md"
        path.write_text("# Section 01\n\nJust content.\n")

        count = _count_existing_related(path, substrate_codespace)
        assert count == 0


# ---- Config-driven threshold tests ----


class TestTriggerThreshold:
    """Test that trigger threshold is read from model-policy.json."""

    def test_default_threshold_when_no_policy(
        self, substrate_planspace: Path,
    ) -> None:
        from scan.substrate.substrate_discoverer import _read_trigger_threshold

        artifacts = substrate_planspace / "artifacts"
        assert _read_trigger_threshold(artifacts) == 2

    def test_reads_custom_threshold(
        self, substrate_planspace: Path,
    ) -> None:
        from scan.substrate.substrate_discoverer import _read_trigger_threshold

        artifacts = substrate_planspace / "artifacts"
        (artifacts / "model-policy.json").write_text(
            json.dumps({"substrate_trigger_min_vacuum_sections": 5}),
        )
        assert _read_trigger_threshold(artifacts) == 5

    def test_ignores_invalid_threshold(
        self, substrate_planspace: Path,
    ) -> None:
        from scan.substrate.substrate_discoverer import _read_trigger_threshold

        artifacts = substrate_planspace / "artifacts"
        (artifacts / "model-policy.json").write_text(
            json.dumps({"substrate_trigger_min_vacuum_sections": 0}),
        )
        # val < 1 => falls back to default
        assert _read_trigger_threshold(artifacts) == 2

    def test_custom_threshold_used_in_trigger(
        self, substrate_planspace: Path, substrate_codespace: Path,
    ) -> None:
        from scan.substrate.substrate_discoverer import run_substrate_discovery

        artifacts = substrate_planspace / "artifacts"
        _write_project_mode(artifacts, "brownfield")

        # 3 vacuum sections
        _write_section(artifacts / "sections", "01", ["src/x.py"])
        _write_section(artifacts / "sections", "02", ["src/y.py"])
        _write_section(artifacts / "sections", "03", ["src/z.py"])

        # Set threshold to 5 — 3 vacuums < 5, should skip
        (artifacts / "model-policy.json").write_text(
            json.dumps({"substrate_trigger_min_vacuum_sections": 5}),
        )

        with patch("scan.substrate.substrate_discoverer._dispatch_agent") as mock_dispatch:
            result = run_substrate_discovery(
                substrate_planspace, substrate_codespace,
            )

        mock_dispatch.assert_not_called()
        assert result is True  # skip is success

        status = json.loads(
            (artifacts / "substrate" / "status.json").read_text()
        )
        assert status["state"] == "SKIPPED"
        assert status["threshold"] == 5


# ---- Prune-signal and substrate.md verification tests ----


class TestPruneSignalHandling:
    """Test pruner verification: substrate.md and prune-signal.json."""

    def _setup_triggered(
        self,
        substrate_planspace: Path,
        substrate_codespace: Path,
    ) -> Path:
        """Set up a greenfield project with 2 sections ready to trigger."""
        artifacts = substrate_planspace / "artifacts"
        _write_project_mode(artifacts, "greenfield")
        _write_section(artifacts / "sections", "01")
        _write_section(artifacts / "sections", "02")
        return artifacts

    def _make_shards(self, artifacts: Path) -> None:
        shards_dir = artifacts / "substrate" / "shards"
        shards_dir.mkdir(parents=True, exist_ok=True)
        for num in ("01", "02"):
            shard = {
                "schema_version": 1,
                "section_number": num,
                "mode": "greenfield",
                "touchpoints": [],
                "provides": [],
                "needs": [],
                "shared_seams": [],
                "open_questions": [],
            }
            (shards_dir / f"shard-{num}.json").write_text(
                json.dumps(shard), encoding="utf-8",
            )

    def test_missing_substrate_md_aborts(
        self, substrate_planspace: Path, substrate_codespace: Path,
    ) -> None:
        from scan.substrate.substrate_discoverer import run_substrate_discovery

        artifacts = self._setup_triggered(
            substrate_planspace, substrate_codespace,
        )

        def fake_dispatch(model, prompt_path, output_path, **kwargs):
            if "shard" in prompt_path.name:
                sec = prompt_path.stem.split("-")[-1]
                self._make_shards(artifacts)
                return True
            if "pruner" in prompt_path.name:
                # Write seed-plan.json but NOT substrate.md
                sub_dir = artifacts / "substrate"
                (sub_dir / "seed-plan.json").write_text(
                    json.dumps({
                        "schema_version": 1,
                        "anchors": [],
                        "wire_sections": [],
                    }),
                )
                return True
            return False

        with patch("scan.substrate.substrate_discoverer._dispatch_agent", side_effect=fake_dispatch):
            result = run_substrate_discovery(
                substrate_planspace, substrate_codespace,
            )

        assert result is False
        status = json.loads(
            (artifacts / "substrate" / "status.json").read_text()
        )
        assert "substrate.md missing" in status["notes"]

    def test_prune_signal_needs_parent_aborts(
        self, substrate_planspace: Path, substrate_codespace: Path,
    ) -> None:
        from scan.substrate.substrate_discoverer import run_substrate_discovery

        artifacts = self._setup_triggered(
            substrate_planspace, substrate_codespace,
        )

        def fake_dispatch(model, prompt_path, output_path, **kwargs):
            if "shard" in prompt_path.name:
                self._make_shards(artifacts)
                return True
            if "pruner" in prompt_path.name:
                sub_dir = artifacts / "substrate"
                (sub_dir / "seed-plan.json").write_text(
                    json.dumps({
                        "schema_version": 1,
                        "anchors": [],
                        "wire_sections": [],
                    }),
                )
                (sub_dir / "substrate.md").write_text(
                    "# Substrate\n\nContent.\n",
                )
                (sub_dir / "prune-signal.json").write_text(
                    json.dumps({
                        "state": "NEEDS_PARENT",
                        "reason": "Sections too fragmented",
                    }),
                )
                return True
            return False

        with patch("scan.substrate.substrate_discoverer._dispatch_agent", side_effect=fake_dispatch):
            result = run_substrate_discovery(
                substrate_planspace, substrate_codespace,
            )

        assert result is False
        status = json.loads(
            (artifacts / "substrate" / "status.json").read_text()
        )
        assert status["state"] == "NEEDS_PARENT"
        assert "Pruner deferred" in status["notes"]


# ---- Substrate.ref writing tests ----


class TestSubstrateRefWriting:
    """Test that substrate.ref is written for each target section."""

    def test_substrate_ref_written_for_all_targets(
        self, substrate_planspace: Path, substrate_codespace: Path,
    ) -> None:
        from scan.substrate.substrate_discoverer import run_substrate_discovery

        artifacts = substrate_planspace / "artifacts"
        _write_project_mode(artifacts, "greenfield")
        _write_section(artifacts / "sections", "01")
        _write_section(artifacts / "sections", "02")

        shards_dir = artifacts / "substrate" / "shards"
        substrate_dir = artifacts / "substrate"

        def fake_dispatch(model, prompt_path, output_path, **kwargs):
            if "shard" in prompt_path.name:
                sec = prompt_path.stem.split("-")[-1]
                shard = {
                    "schema_version": 1, "section_number": sec,
                    "mode": "greenfield", "touchpoints": [],
                    "provides": [], "needs": [],
                    "shared_seams": [], "open_questions": [],
                }
                shards_dir.mkdir(parents=True, exist_ok=True)
                (shards_dir / f"shard-{sec}.json").write_text(
                    json.dumps(shard),
                )
                return True
            if "pruner" in prompt_path.name:
                (substrate_dir / "seed-plan.json").write_text(
                    json.dumps({
                        "schema_version": 1,
                        "anchors": [{"path": "src/shared.py", "purpose": "Shared"}],
                        "wire_sections": [1, 2],
                    }),
                )
                (substrate_dir / "substrate.md").write_text("# Substrate\n")
                (substrate_dir / "prune-signal.json").write_text(
                    json.dumps({"status": "READY"}),
                )
                return True
            if "seeder" in prompt_path.name:
                (substrate_dir / "seed-signal.json").write_text(
                    json.dumps({"status": "SEEDED"}),
                )
                return True
            return False

        with patch("scan.substrate.substrate_discoverer._dispatch_agent", side_effect=fake_dispatch):
            result = run_substrate_discovery(
                substrate_planspace, substrate_codespace,
            )

        assert result is True

        # Verify substrate.ref written for both sections
        for num in ("01", "02"):
            ref_path = artifacts / "inputs" / f"section-{num}" / "substrate.ref"
            assert ref_path.exists(), f"substrate.ref missing for section-{num}"
            ref_content = ref_path.read_text().strip()
            assert ref_content.endswith("substrate.md")


# ---- Related-files update tests ----


class TestRelatedFilesUpdates:
    """Test the mechanical related-files updater."""

    def test_apply_adds_new_entries(
        self, substrate_planspace: Path, substrate_codespace: Path,
    ) -> None:
        from scan.substrate.related_files import apply_related_files_updates

        artifacts = substrate_planspace / "artifacts"
        _write_section(artifacts / "sections", "01")

        # Write a signal
        signal_dir = artifacts / "signals" / "related-files-update"
        signal_dir.mkdir(parents=True, exist_ok=True)
        (signal_dir / "section-01.json").write_text(
            json.dumps({"additions": ["src/anchor.py"], "removals": []})
        )

        count = apply_related_files_updates(substrate_planspace)
        assert count == 1

        # Verify the section was updated
        text = (artifacts / "sections" / "section-01.md").read_text()
        assert "### src/anchor.py" in text

    def test_deduplicates_existing_entries(
        self, substrate_planspace: Path, substrate_codespace: Path,
    ) -> None:
        from scan.substrate.related_files import apply_related_files_updates

        artifacts = substrate_planspace / "artifacts"
        _write_section(
            artifacts / "sections", "01",
            related_files=["src/already.py"],
        )

        signal_dir = artifacts / "signals" / "related-files-update"
        signal_dir.mkdir(parents=True, exist_ok=True)
        (signal_dir / "section-01.json").write_text(
            json.dumps({"additions": ["src/already.py"], "removals": []})
        )

        count = apply_related_files_updates(substrate_planspace)
        # No update needed — already present
        assert count == 0

    def test_malformed_signal_renamed(
        self, substrate_planspace: Path, substrate_codespace: Path,
    ) -> None:
        from scan.substrate.related_files import apply_related_files_updates

        artifacts = substrate_planspace / "artifacts"
        _write_section(artifacts / "sections", "01")

        signal_dir = artifacts / "signals" / "related-files-update"
        signal_dir.mkdir(parents=True, exist_ok=True)
        (signal_dir / "section-01.json").write_text("{bad json")

        count = apply_related_files_updates(substrate_planspace)
        assert count == 0
        assert (signal_dir / "section-01.malformed.json").exists()

    def test_no_signals_dir_returns_zero(
        self, substrate_planspace: Path, substrate_codespace: Path,
    ) -> None:
        from scan.substrate.related_files import apply_related_files_updates

        count = apply_related_files_updates(substrate_planspace)
        assert count == 0


# ---- Prompt building tests ----


class TestPromptBuilding:
    """Test prompt builders produce valid files."""

    def test_shard_prompt_written(
        self, substrate_planspace: Path, substrate_codespace: Path,
    ) -> None:
        from scan.substrate.prompt_builder import write_shard_prompt

        artifacts = substrate_planspace / "artifacts"
        section_path = artifacts / "sections" / "section-01.md"
        section_path.write_text("# Section 01\n")

        prompt_path = write_shard_prompt(
            "01", section_path, substrate_planspace, substrate_codespace,
        )
        assert prompt_path.exists()
        content = prompt_path.read_text()
        assert "section-01" in content or "Section 01" in content
        assert str(substrate_codespace) in content

    def test_pruner_prompt_lists_sections(
        self, substrate_planspace: Path, substrate_codespace: Path,
    ) -> None:
        from scan.substrate.prompt_builder import write_pruner_prompt

        prompt_path = write_pruner_prompt(
            substrate_planspace,
            substrate_codespace,
            ["01", "02", "03"],
        )
        assert prompt_path.exists()
        content = prompt_path.read_text()
        assert "01" in content
        assert "02" in content
        assert "03" in content

    def test_shard_prompt_includes_codemap_corrections(
        self, substrate_planspace: Path, substrate_codespace: Path,
    ) -> None:
        from scan.substrate.prompt_builder import write_shard_prompt

        artifacts = substrate_planspace / "artifacts"
        section_path = artifacts / "sections" / "section-01.md"
        section_path.write_text("# Section 01\n")

        # Create codemap and corrections
        (artifacts / "codemap.md").write_text("# Codemap\n")
        corrections_path = artifacts / "signals" / "codemap-corrections.json"
        corrections_path.parent.mkdir(parents=True, exist_ok=True)
        corrections_path.write_text("{}", encoding="utf-8")

        prompt_path = write_shard_prompt(
            "01", section_path, substrate_planspace, substrate_codespace,
        )
        content = prompt_path.read_text()
        assert "codemap-corrections.json" in content

    def test_pruner_prompt_includes_codemap_corrections(
        self, substrate_planspace: Path, substrate_codespace: Path,
    ) -> None:
        from scan.substrate.prompt_builder import write_pruner_prompt

        artifacts = substrate_planspace / "artifacts"
        (artifacts / "codemap.md").write_text("# Codemap\n")
        corrections_path = artifacts / "signals" / "codemap-corrections.json"
        corrections_path.parent.mkdir(parents=True, exist_ok=True)
        corrections_path.write_text("{}", encoding="utf-8")

        prompt_path = write_pruner_prompt(
            substrate_planspace, substrate_codespace, ["01", "02"],
        )
        content = prompt_path.read_text()
        assert "codemap-corrections.json" in content

    def test_seeder_prompt_includes_codemap_corrections(
        self, substrate_planspace: Path, substrate_codespace: Path,
    ) -> None:
        from scan.substrate.prompt_builder import write_seeder_prompt

        artifacts = substrate_planspace / "artifacts"
        (artifacts / "codemap.md").write_text("# Codemap\n")
        corrections_path = artifacts / "signals" / "codemap-corrections.json"
        corrections_path.parent.mkdir(parents=True, exist_ok=True)
        corrections_path.write_text("{}", encoding="utf-8")

        prompt_path = write_seeder_prompt(
            substrate_planspace, substrate_codespace,
        )
        content = prompt_path.read_text()
        assert "codemap-corrections.json" in content

    def test_seeder_prompt_written(
        self, substrate_planspace: Path, substrate_codespace: Path,
    ) -> None:
        from scan.substrate.prompt_builder import write_seeder_prompt

        prompt_path = write_seeder_prompt(
            substrate_planspace, substrate_codespace,
        )
        assert prompt_path.exists()
        content = prompt_path.read_text()
        assert "seed-plan" in content.lower() or "seed plan" in content.lower()


# ---- Runner orchestration tests ----


class TestRunnerOrchestration:
    """Test the full pipeline with mocked dispatch."""

    def _make_shard_agent(
        self, shards_dir: Path, section_num: str,
    ) -> None:
        """Simulate a successful shard explorer by writing a valid shard."""
        shard = {
            "schema_version": 1,
            "section_number": section_num,
            "mode": "greenfield",
            "touchpoints": ["types"],
            "provides": [
                {"id": "foo.create", "kind": "api", "summary": "Creates foo"},
            ],
            "needs": [
                {
                    "id": "bar.check",
                    "kind": "service",
                    "summary": "Needs bar",
                    "strength": "must",
                },
            ],
            "shared_seams": [
                {
                    "topic": "types",
                    "need": "must_decide",
                    "why": "Both need shared types",
                    "path_candidates": [],
                },
            ],
            "open_questions": [],
        }
        (shards_dir / f"shard-{section_num}.json").write_text(
            json.dumps(shard), encoding="utf-8",
        )

    def _make_seed_plan(self, substrate_dir: Path) -> None:
        """Simulate a successful pruner by writing a valid seed plan."""
        plan = {
            "schema_version": 1,
            "anchors": [
                {
                    "path": "src/shared/types.py",
                    "purpose": "Shared types",
                    "owned_by": "SIS",
                    "touched_by_sections": [1, 2],
                },
            ],
            "wire_sections": [1, 2],
        }
        (substrate_dir / "seed-plan.json").write_text(
            json.dumps(plan), encoding="utf-8",
        )
        (substrate_dir / "substrate.md").write_text(
            "# Substrate\n\nShared types decided.\n", encoding="utf-8",
        )

    def _make_seed_signal(self, substrate_dir: Path) -> None:
        """Simulate a successful seeder by writing signals."""
        (substrate_dir / "seed-signal.json").write_text(
            json.dumps({
                "state": "SEEDED",
                "anchors_created": ["src/shared/types.py"],
                "sections_wired": [1, 2],
                "refs_written": [1, 2],
            }),
            encoding="utf-8",
        )

    def test_full_greenfield_pipeline(
        self, substrate_planspace: Path, substrate_codespace: Path,
    ) -> None:
        from scan.substrate.substrate_discoverer import run_substrate_discovery

        artifacts = substrate_planspace / "artifacts"
        _write_project_mode(artifacts, "greenfield")
        _write_section(artifacts / "sections", "01")
        _write_section(artifacts / "sections", "02")

        shards_dir = artifacts / "substrate" / "shards"
        substrate_dir = artifacts / "substrate"
        call_count = {"n": 0}

        def fake_dispatch(model, prompt_path, output_path, **kwargs):
            call_count["n"] += 1
            # Phase A: shard explorers
            if "shard" in prompt_path.name:
                sec = prompt_path.stem.split("-")[-1]
                self._make_shard_agent(shards_dir, sec)
                return True
            # Phase B: pruner
            if "pruner" in prompt_path.name:
                self._make_seed_plan(substrate_dir)
                return True
            # Phase C: seeder
            if "seeder" in prompt_path.name:
                self._make_seed_signal(substrate_dir)
                return True
            return False

        with patch("scan.substrate.substrate_discoverer._dispatch_agent", side_effect=fake_dispatch):
            result = run_substrate_discovery(
                substrate_planspace, substrate_codespace,
            )

        assert result is True
        # 2 shards + 1 pruner + 1 seeder = 4 dispatches
        assert call_count["n"] == 4

        # Check status
        status = json.loads(
            (artifacts / "substrate" / "status.json").read_text()
        )
        assert status["state"] == "RAN"

    def test_all_shards_fail_aborts(
        self, substrate_planspace: Path, substrate_codespace: Path,
    ) -> None:
        from scan.substrate.substrate_discoverer import run_substrate_discovery

        artifacts = substrate_planspace / "artifacts"
        _write_project_mode(artifacts, "greenfield")
        _write_section(artifacts / "sections", "01")
        _write_section(artifacts / "sections", "02")

        with patch("scan.substrate.substrate_discoverer._dispatch_agent", return_value=False):
            result = run_substrate_discovery(
                substrate_planspace, substrate_codespace,
            )

        # Should fail — no valid shards
        assert result is False

    def test_partial_shard_failure_continues(
        self, substrate_planspace: Path, substrate_codespace: Path,
    ) -> None:
        from scan.substrate.substrate_discoverer import run_substrate_discovery

        artifacts = substrate_planspace / "artifacts"
        _write_project_mode(artifacts, "greenfield")
        _write_section(artifacts / "sections", "01")
        _write_section(artifacts / "sections", "02")

        shards_dir = artifacts / "substrate" / "shards"
        substrate_dir = artifacts / "substrate"

        def fake_dispatch(model, prompt_path, output_path, **kwargs):
            if "shard-01" in prompt_path.name:
                self._make_shard_agent(shards_dir, "01")
                return True
            if "shard-02" in prompt_path.name:
                return False  # Section 02 fails
            if "pruner" in prompt_path.name:
                self._make_seed_plan(substrate_dir)
                return True
            if "seeder" in prompt_path.name:
                self._make_seed_signal(substrate_dir)
                return True
            return False

        with patch("scan.substrate.substrate_discoverer._dispatch_agent", side_effect=fake_dispatch):
            result = run_substrate_discovery(
                substrate_planspace, substrate_codespace,
            )

        # Should succeed — one valid shard is enough
        assert result is True


# ---- CLI tests ----


class TestCLI:
    """Test the CLI entry point."""

    def test_missing_planspace_returns_1(self, tmp_path: Path) -> None:
        from scan.substrate.substrate_discoverer import main

        rc = main([
            str(tmp_path / "nonexistent"),
            str(tmp_path),
        ])
        assert rc == 1

    def test_missing_codespace_returns_1(self, tmp_path: Path) -> None:
        from scan.substrate.substrate_discoverer import main

        planspace = tmp_path / "ps"
        planspace.mkdir()
        rc = main([str(planspace), str(tmp_path / "nonexistent")])
        assert rc == 1


# ---- Contract-boundary regression guards ----


class TestShardPromptNoSchemaRedefinition:
    """Shard prompt must NOT redefine schema — agent file owns it (R66)."""

    def test_no_schema_fields_in_shard_prompt(
        self, substrate_planspace: Path, substrate_codespace: Path,
    ) -> None:
        from scan.substrate.prompt_builder import write_shard_prompt

        artifacts = substrate_planspace / "artifacts"
        section_path = artifacts / "sections" / "section-01.md"
        section_path.write_text("# Section 01\n")

        prompt_path = write_shard_prompt(
            "01", section_path, substrate_planspace, substrate_codespace,
        )
        content = prompt_path.read_text()

        # Prompt must NOT list schema field names — those belong in agent file
        for field in ("from_section", "touchpoint", '"name"', '"sections"'):
            assert field not in content, (
                f"Shard prompt redefines schema field '{field}' — "
                f"agent file owns the schema"
            )
