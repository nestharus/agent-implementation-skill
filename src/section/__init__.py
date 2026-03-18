"""Section system: per-section fractal pipeline task types.

Each section independently progresses through:
    section.propose -> section.readiness_check
      -> if ready: section.implement -> section.verify
      -> if blocked: emit signals (research, coordination, etc.)
"""
