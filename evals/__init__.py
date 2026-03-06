"""Live-LLM scenario evals for bounded strategic surfaces.

Optional dev/audit tool -- NOT a CI dependency. Requires real
model access (dispatch_agent calls are NOT mocked).

Usage:
    python3 -m evals.harness --list
    python3 -m evals.harness --run reexplorer
    python3 -m evals.harness --all
"""
