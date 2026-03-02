"""Live-LLM scenario evals for bounded strategic surfaces.

Optional dev/audit tool -- NOT a CI dependency. Requires real
model access (dispatch_agent calls are NOT mocked).

Usage:
    cd src && python3 -m evals.harness --list
    cd src && python3 -m evals.harness --run reexplorer
    cd src && python3 -m evals.harness --all
"""
