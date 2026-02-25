# Loop Contract

This document defines the section loop's convergence semantics.

## Inputs (what triggers reruns)

A section is re-evaluated when ANY of these inputs change.
The authoritative list is ``_section_inputs_hash()`` in
``scripts/section_loop/pipeline_control.py`` — this document
must stay in sync with that function.

- Section spec file (`artifacts/sections/section-NN.md`)
- Proposal excerpt (`artifacts/sections/section-NN-proposal-excerpt.md`)
- Alignment excerpt (`artifacts/sections/section-NN-alignment-excerpt.md`)
- Integration proposal (`artifacts/proposals/section-NN-integration-proposal.md`)
- TODO extraction (`artifacts/todos/section-NN-todos.md`)
- Microstrategy artifact (`artifacts/proposals/section-NN-microstrategy.md`)
- Microstrategy prompt/output logs (`artifacts/microstrategy-NN*.md`)
- Decisions (`artifacts/decisions/section-NN.md`)
- Consequence notes targeting this section (`artifacts/notes/from-*-to-NN.md`)
- Tool registry (`artifacts/tool-registry.json`)
- Related files list (from section spec)
- Codemap digest (`artifacts/codemap.md`)
- Codemap corrections (`artifacts/signals/codemap-corrections.json`)
- Project mode (`artifacts/project-mode.txt`, `artifacts/signals/project-mode.json`)
- Section mode (`artifacts/sections/section-NN-mode.txt`)
- Problem frame (`artifacts/sections/section-NN-problem-frame.md`)
- Intent global philosophy (`artifacts/intent/global/philosophy.md`)
- Intent per-section problem definition (`artifacts/intent/sections/section-NN/problem.md`)
- Intent per-section problem alignment rubric (`artifacts/intent/sections/section-NN/problem-alignment.md`)
- Intent per-section philosophy excerpt (`artifacts/intent/sections/section-NN/philosophy-excerpt.md`)
- Input refs — contract deltas and registered inputs (`artifacts/inputs/section-NN/*.ref` + referenced files)

## Convergence Criteria

A section is ALIGNED when the alignment judge confirms:
1. The integration proposal is consistent with the section spec
2. TODO blocks in code match the proposal's obligations
3. No unaddressed consequence notes remain
4. No active blocker signals (UNDERSPECIFIED, NEED_DECISION, DEPENDENCY, OUT_OF_SCOPE, NEEDS_PARENT, LOOP_DETECTED)

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
