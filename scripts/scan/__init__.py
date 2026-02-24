"""scan — Stage 3 scan entrypoint and phase coordinator (Python package).

Public CLI contract (via bash shim — the supported entrypoint)::

    scripts/scan.sh <quick|deep|both> <planspace> <codespace>

The shim sets ``PYTHONPATH`` to include ``scripts/`` so that
``python -m scan`` resolves correctly.  Direct ``python -m scan``
invocation requires ``scripts/`` on ``PYTHONPATH``.
"""
