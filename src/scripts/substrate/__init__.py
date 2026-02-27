"""substrate -- Stage 3.5 Shared Integration Substrate (SIS) discovery.

Runs between Stage 3 (scan) and Stage 4 (section-loop).  Discovers
shared integration seams across sections that have nothing to integrate
against (greenfield or vacuum sections in brownfield).

Pipeline: Shards (per-section structured JSON) -> Prune (one strategic
merge) -> Seed (minimal anchor files) -> Wire (refs + related-files
updates).

Public CLI contract::

    python -m substrate <planspace> <codespace>

Requires ``scripts/`` on ``PYTHONPATH`` (same as the scan package).
"""
