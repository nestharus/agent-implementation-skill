"""Component tests for risk-specific PathRegistry accessors."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.orchestrator.path_registry import PathRegistry


class TestRiskDirectoryAccessors:
    @pytest.fixture()
    def reg(self, tmp_path: Path) -> PathRegistry:
        return PathRegistry(tmp_path)

    def test_risk_dir(self, reg: PathRegistry, tmp_path: Path) -> None:
        assert reg.risk_dir() == tmp_path / "artifacts" / "risk"


class TestRiskFileAccessors:
    @pytest.fixture()
    def reg(self, tmp_path: Path) -> PathRegistry:
        return PathRegistry(tmp_path)

    @pytest.mark.parametrize("scope", ["section-01", "global"])
    def test_risk_package(
        self,
        reg: PathRegistry,
        tmp_path: Path,
        scope: str,
    ) -> None:
        assert reg.risk_package(scope) == (
            tmp_path / "artifacts" / "risk" / f"{scope}-risk-package.json"
        )

    @pytest.mark.parametrize("scope", ["section-01", "global"])
    def test_risk_assessment(
        self,
        reg: PathRegistry,
        tmp_path: Path,
        scope: str,
    ) -> None:
        assert reg.risk_assessment(scope) == (
            tmp_path / "artifacts" / "risk" / f"{scope}-risk-assessment.json"
        )

    @pytest.mark.parametrize("scope", ["section-01", "global"])
    def test_risk_plan(
        self,
        reg: PathRegistry,
        tmp_path: Path,
        scope: str,
    ) -> None:
        assert reg.risk_plan(scope) == (
            tmp_path / "artifacts" / "risk" / f"{scope}-risk-plan.json"
        )

    def test_risk_history(self, reg: PathRegistry, tmp_path: Path) -> None:
        assert reg.risk_history() == (
            tmp_path / "artifacts" / "risk" / "risk-history.jsonl"
        )

    @pytest.mark.parametrize("scope", ["section-01", "global"])
    def test_risk_summary(
        self,
        reg: PathRegistry,
        tmp_path: Path,
        scope: str,
    ) -> None:
        assert reg.risk_summary(scope) == (
            tmp_path / "artifacts" / "risk" / f"{scope}-risk-summary.md"
        )

    def test_risk_parameters(self, reg: PathRegistry, tmp_path: Path) -> None:
        assert reg.risk_parameters() == (
            tmp_path / "artifacts" / "risk" / "risk-parameters.json"
        )
