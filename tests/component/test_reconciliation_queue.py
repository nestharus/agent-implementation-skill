from __future__ import annotations

import json
from pathlib import Path

from src.scripts.lib.repositories.reconciliation_queue import (
    load_reconciliation_requests,
    queue_reconciliation_request,
)


def test_queue_reconciliation_request_writes_expected_json(
    tmp_path: Path,
) -> None:
    artifacts_dir = tmp_path / "planspace" / "artifacts"

    request_path = queue_reconciliation_request(
        artifacts_dir,
        "03",
        ["contract-a"],
        ["anchor-b"],
    )

    assert request_path.name == "section-03-reconciliation.json"
    assert json.loads(request_path.read_text(encoding="utf-8")) == {
        "section": "03",
        "unresolved_contracts": ["contract-a"],
        "unresolved_anchors": ["anchor-b"],
    }


def test_load_reconciliation_requests_skips_malformed_and_renames_non_dict(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "planspace"
    recon_dir = run_dir / "artifacts" / "reconciliation-requests"
    recon_dir.mkdir(parents=True)
    (recon_dir / "section-01-reconciliation.json").write_text(
        json.dumps({"section": "01"}) + "\n",
        encoding="utf-8",
    )
    (recon_dir / "section-02-reconciliation.json").write_text(
        "{not-json",
        encoding="utf-8",
    )
    array_path = recon_dir / "section-03-reconciliation.json"
    array_path.write_text(json.dumps(["not", "a", "dict"]) + "\n", encoding="utf-8")

    requests = load_reconciliation_requests(run_dir)

    assert requests == [{"section": "01"}]
    assert not array_path.exists()
    assert (recon_dir / "section-03-reconciliation.malformed.json").exists()
    assert (recon_dir / "section-02-reconciliation.malformed.json").exists()
