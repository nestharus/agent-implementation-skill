"""Reconciliation request queue for cross-section contract resolution.

Sections with unresolved contracts or shared anchors write reconciliation
requests here.  The later reconciliation stage (Task 9) consumes them to
drive cross-section negotiation.

Requests are persisted as individual JSON files under
``artifacts/reconciliation-requests/`` so that they survive partial runs
and can be inspected by monitors.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def queue_reconciliation_request(
    section_dir: Path,
    section_number: str,
    unresolved_contracts: list[str],
    unresolved_anchors: list[str],
) -> Path:
    """Write a reconciliation-request artifact for *section_number*.

    Parameters
    ----------
    section_dir:
        The ``planspace / "artifacts"`` directory (or equivalent root
        that contains the per-section artifact tree).
    section_number:
        Zero-padded section number (e.g. ``"03"``).
    unresolved_contracts:
        Contract descriptions that could not be resolved locally.
    unresolved_anchors:
        Shared anchor descriptions that need cross-section agreement.

    Returns
    -------
    Path
        The path to the written reconciliation-request JSON file.
    """
    recon_dir = section_dir / "reconciliation-requests"
    recon_dir.mkdir(parents=True, exist_ok=True)

    request = {
        "section": section_number,
        "unresolved_contracts": unresolved_contracts,
        "unresolved_anchors": unresolved_anchors,
    }

    request_path = recon_dir / f"section-{section_number}-reconciliation.json"
    request_path.write_text(
        json.dumps(request, indent=2) + "\n", encoding="utf-8",
    )
    logger.info(
        "Reconciliation request written for section %s (%d contracts, "
        "%d anchors) at %s",
        section_number,
        len(unresolved_contracts),
        len(unresolved_anchors),
        request_path,
    )
    return request_path


def load_reconciliation_requests(run_dir: Path) -> list[dict]:
    """Load all reconciliation requests from a run directory.

    Scans ``run_dir / "artifacts" / "reconciliation-requests"`` for
    request JSON files and returns them as a list of dicts.  Malformed
    files are logged and skipped (fail-open for loading — the consumer
    decides how to handle gaps).

    Parameters
    ----------
    run_dir:
        The planspace root (contains an ``artifacts/`` subtree).

    Returns
    -------
    list[dict]
        All valid reconciliation request dicts found.
    """
    recon_dir = run_dir / "artifacts" / "reconciliation-requests"
    if not recon_dir.exists():
        return []

    requests: list[dict] = []
    for req_path in sorted(recon_dir.glob("section-*-reconciliation.json")):
        try:
            data = json.loads(req_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                requests.append(data)
            else:
                logger.warning(
                    "Reconciliation request at %s is not a dict "
                    "— renaming to .malformed.json",
                    req_path,
                )
                try:
                    req_path.rename(req_path.with_suffix(".malformed.json"))
                except OSError:
                    pass
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(
                "Malformed reconciliation request at %s (%s) "
                "— renaming to .malformed.json",
                req_path, exc,
            )
            try:
                req_path.rename(req_path.with_suffix(".malformed.json"))
            except OSError:
                pass
    return requests
