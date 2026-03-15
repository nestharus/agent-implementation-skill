# PHI-global: Global Philosophy Profile

The default philosophy profile governing all regions unless overridden.

## Values (priority order)

1. **Accuracy over shortcuts** — every shortcut introduces risk. Earn simplicity through confirmation, not assumption.
2. **Strategy over brute force** — strategy collapses many waves of problems in one pass. Fewer tokens, fewer cycles, same quality.
3. **Alignment over audit** — check directional coherence between adjacent layers, never feature coverage against a checklist. The system is never "done" in the checklist sense.
4. **Evidence preservation** — never silently discard data. Corrupt artifacts are debugging evidence.
5. **Bounded autonomy** — agents reason freely within structured boundaries. Short-lived agents; persist decisions.
6. **Proportional risk** — risk must be quantified and guardrails proportional to actual danger. The goal is risk below a defined threshold with effort proportional to the actual risk, not blanket maximum process. Fail closed at uncertainty boundaries; scale process by evidence when available.

## Preferred Failure Mode

Fail closed. On uncertainty, do more work rather than skip work. The cost of redundant work is lower than the cost of missed work.

## Risk Posture

Proportional. Accept performance cost for safety. Accept token cost for correctness. Scale guardrails to actual local risk through ROAL posture profiles (P0-P4). Fail closed at decision boundaries where the system lacks evidence; scale process proportionally when evidence exists.

## Anti-Patterns

- Feature coverage checklists (banned — alignment, not audit)
- Brute-force retry loops (use strategy instead)
- Silent discard of malformed data (preserve and fail closed)
- Hardcoded model strings (use policy)
- Ad hoc path construction (use path registry)
- Direct agent spawning (use task submission)
- Exhaustive scanning (use heuristic exploration)
- Mode-based routing forks (mode is observation, not routing key)

## Ordering-Sensitive Constraints

These constraints are ordering-sensitive — they apply in specific contexts
and are easy to lose when compressed into broad values:

- **Proposal evaluation rule**: A superior proposal must solve the same
  problems and must not add user-unspecified constraints. Optimization or
  complexity arguments do not excuse not solving the assigned task.
- **Sections are concerns, not file bundles**: A section's identity comes
  from the problem it addresses. The file list is a starting hypothesis that
  can change. Cross-section coordination is about problem interaction, not
  file overlap.
- **Atomic migration consistency**: When migrating execution models, the
  migration must be atomic per surface. Split instructions (method says one
  thing, runtime template says another) degrade behavior worse than either
  model alone.
- **System vs target system distinction**: Our system (pipeline) is Python +
  Bash + Markdown where mechanical enforcement is legitimate. The target
  system (analyzed codebases) can be anything — agents must reason flexibly
  without hardcoded language assumptions.
- **Model and agent-file choice carry method of thinking**: Model selection
  is sensitive to agent file definitions. Agent files impart a method of
  thinking. The choice of which model runs which agent file is a
  method-of-thinking decision, not a performance/cost decision.

## Pattern Implications

All patterns in the pattern archive embody this profile unless marked otherwise. Specifically:
- PAT-0001 (Corruption Preservation) ← evidence preservation
- PAT-0002 (Prompt Safety) ← accuracy over shortcuts
- PAT-0003 (Path Registry) ← accuracy over shortcuts
- PAT-0005 (Policy-Driven Models) ← bounded autonomy
- PAT-0008 (Fail-Closed) ← preferred failure mode + proportional risk
- PAT-0011 (Governance Packet Threading) ← alignment over audit
- PAT-0012 (Post-Implementation Governance Feedback) ← proportional risk + evidence preservation
