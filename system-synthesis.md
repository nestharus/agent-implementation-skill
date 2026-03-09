# System Synthesis

Governance-facing overlay connecting architecture, problems, philosophy, and patterns.

This complements `codebase_system_design.md` (which describes WHAT exists) by explaining WHY each region exists and HOW it connects to governance.

## Regions

### Flow System
- **Problems solved**: PRB-0001 (Safe Multi-Agent Orchestration), PRB-0003 (Stale Artifacts)
- **Philosophy**: PHI-global (bounded autonomy, fail-closed)
- **Patterns**: PAT-0004 (Flow System), PAT-0006 (Freshness), PAT-0007 (Cycle-Aware Status)
- **Modules**: `flow_schema.py`, `flow_catalog.py`, `lib/flow/flow_submitter.py`, `lib/flow/flow_reconciler.py`

### Section Loop
- **Problems solved**: PRB-0002 (Strategic Implementation), PRB-0006 (Cross-Section Coherence)
- **Philosophy**: PHI-global (strategy over brute force, alignment over audit)
- **Patterns**: PAT-0010 (Intent Surfaces), PAT-0009 (Blocker Taxonomy)
- **Modules**: `section_loop/`, `lib/pipelines/`

### Research
- **Problems solved**: PRB-0005 (Research Information Gathering)
- **Philosophy**: PHI-global (bounded autonomy)
- **Patterns**: PAT-0001 (Corruption Preservation), PAT-0002 (Prompt Safety), PAT-0004 (Flow System), PAT-0007 (Cycle-Aware Status)
- **Modules**: `lib/research/`, agents: research-planner, domain-researcher, research-synthesizer, research-verifier

### Artifact Infrastructure
- **Problems solved**: PRB-0004 (Agent Output Corruption), PRB-0003 (Stale Artifacts)
- **Philosophy**: PHI-global (evidence preservation, fail-closed)
- **Patterns**: PAT-0001 (Corruption Preservation), PAT-0003 (Path Registry), PAT-0008 (Fail-Closed)
- **Modules**: `lib/core/artifact_io.py`, `lib/core/path_registry.py`, `lib/core/hash_service.py`

### Execution Risk (ROAL)
- **Problems solved**: PRB-0007 (Execution Risk)
- **Philosophy**: PHI-global (accuracy over shortcuts, zero risk tolerance)
- **Patterns**: PAT-0002 (Prompt Safety), PAT-0005 (Policy-Driven Models)
- **Modules**: `lib/pipelines/readiness_gate.py`, alignment judges, risk assessor

### Dispatch & Task Routing
- **Problems solved**: PRB-0001 (Safe Multi-Agent Orchestration)
- **Philosophy**: PHI-global (scripts dispatch, agents decide)
- **Patterns**: PAT-0005 (Policy-Driven Models), PAT-0004 (Flow System)
- **Modules**: `task_router.py`, `task_dispatcher.py`

## Open Tensions

- **PRB-0008 (Implementation Risk)**: No post-landing risk assessment yet. Proposed in governance design.
- **PRB-0009 (Problem Traceability)**: This document is manual. Traceability enrichment (problem_ids in trace artifacts) not yet implemented.
- **PRB-0010 (Pattern Governance)**: Pattern archive created but not yet consumed by the runtime. Audit process references it manually.
- **Governance packets**: Designed but not yet threaded into prompt assembly or freshness hashing.
