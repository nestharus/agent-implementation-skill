"""System-level agentic workflow evals.

Pre-seeds realistic workspace state, triggers multi-agent workflows,
and evaluates outcomes with structural checks + LLM judges.

Usage:
    uv run agentic-evals --list
    uv run agentic-evals --scenario readiness-triggers-research-planner
    uv run agentic-evals --category happy-path
    uv run agentic-evals --wave 1
"""
