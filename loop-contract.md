# Loop Contract

This document defines the section loop's convergence semantics.

## Inputs (what triggers reruns)

A section is re-evaluated when ANY of these inputs change:
- Section spec file (`artifacts/sections/section-NN.md`)
- Proposal excerpt (`artifacts/sections/section-NN-proposal-excerpt.md`)
- Alignment excerpt (`artifacts/sections/section-NN-alignment-excerpt.md`)
- Integration proposal (`artifacts/proposals/section-NN-integration-proposal.md`)
- TODO extraction (`artifacts/todos/section-NN-todos.md`)
- Microstrategy files (`artifacts/microstrategy-NN*.md`)
- Decisions (`artifacts/decisions/section-NN.md`)
- Consequence notes targeting this section (`artifacts/notes/from-*-to-NN.md`)
- Tool registry (`artifacts/tool-registry.json`)
- Related files list (from section spec)

## Convergence Criteria

A section is ALIGNED when the alignment judge confirms:
1. The integration proposal is consistent with the section spec
2. TODO blocks in code match the proposal's obligations
3. No unaddressed consequence notes remain
4. No signals (underspec, dependency, loop_detected) are active

## Rerun Semantics

- **Phase 1 (per-section):** Each section runs proposal -> align -> implement -> align loops until ALIGNED
- **Phase 2 (global):** Re-checks alignment across ALL sections; only reruns sections whose input hash changed
- **Coordination:** Groups related problems; dispatches coordinated fixes; re-checks affected sections
- **Targeted invalidation:** `alignment_changed` triggers selective requeue based on input hash comparison, NOT brute-force requeue of all sections

## Termination

- **Success:** All sections ALIGNED after Phase 2 or coordination
- **Stall:** Coordination makes no progress for 3 rounds -> stop, report remaining problems
- **Abort:** Parent sends abort message -> clean shutdown
- **Escalation:** After 2 stalled rounds, escalate to stronger model before giving up
