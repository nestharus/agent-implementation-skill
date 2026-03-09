# PHI-global: Global Philosophy Profile

The default philosophy profile governing all regions unless overridden.

## Values (priority order)

1. **Accuracy over shortcuts** — zero risk tolerance. Every shortcut introduces risk.
2. **Strategy over brute force** — strategy collapses many waves of problems in one pass.
3. **Alignment over audit** — check directional coherence, never feature coverage.
4. **Evidence preservation** — never silently discard data. Corrupt artifacts are debugging evidence.
5. **Bounded autonomy** — agents reason freely within structured boundaries.

## Preferred Failure Mode

Fail closed. On uncertainty, do more work rather than skip work. The cost of redundant work is lower than the cost of missed work.

## Risk Posture

Conservative. Accept performance cost for safety. Accept token cost for correctness. Do not accept correctness risk for efficiency.

## Anti-Patterns

- Feature coverage checklists (banned — alignment, not audit)
- Brute-force retry loops (use strategy instead)
- Silent discard of malformed data (preserve and fail closed)
- Hardcoded model strings (use policy)
- Ad hoc path construction (use path registry)
- Direct agent spawning (use task submission)

## Pattern Implications

All patterns in the pattern archive embody this profile unless marked otherwise. Specifically:
- PAT-0001 (Corruption Preservation) ← evidence preservation
- PAT-0002 (Prompt Safety) ← zero risk tolerance
- PAT-0003 (Path Registry) ← accuracy over shortcuts
- PAT-0008 (Fail-Closed) ← preferred failure mode
