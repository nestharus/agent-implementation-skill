"""ReconciliationQueueService: persistence for reconciliation requests."""

from __future__ import annotations

import logging
from pathlib import Path

from containers import Services
from orchestrator.path_registry import PathRegistry

logger = logging.getLogger(__name__)


def queue_reconciliation_request(
    planspace: Path,
    section_number: str,
    unresolved_contracts: list[str],
    unresolved_anchors: list[str],
) -> Path:
    """Write a reconciliation-request artifact for *section_number*."""
    paths = PathRegistry(planspace)

    request = {
        "section": section_number,
        "unresolved_contracts": unresolved_contracts,
        "unresolved_anchors": unresolved_anchors,
    }

    request_path = paths.reconciliation_request(section_number)
    Services.artifact_io().write_json(request_path, request)
    logger.info(
        "Reconciliation request written for section %s (%d contracts, %d anchors) at %s",
        section_number,
        len(unresolved_contracts),
        len(unresolved_anchors),
        request_path,
    )
    return request_path


def load_reconciliation_requests(planspace: Path) -> list[dict]:
    """Load all reconciliation requests from a planspace directory."""
    paths = PathRegistry(planspace)
    recon_dir = paths.reconciliation_requests_dir()
    if not recon_dir.exists():
        return []

    requests: list[dict] = []
    for req_path in sorted(recon_dir.glob("section-*-reconciliation.json")):
        data = Services.artifact_io().read_json(req_path)
        if data is None:
            logger.warning(
                "Malformed reconciliation request at %s — skipped",
                req_path,
            )
            continue
        if isinstance(data, dict):
            requests.append(data)
        else:
            logger.warning(
                "Reconciliation request at %s is not a dict "
                "— renaming to .malformed.json",
                req_path,
            )
            Services.artifact_io().rename_malformed(req_path)
    return requests
