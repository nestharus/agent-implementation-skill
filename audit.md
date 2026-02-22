# Audit: Structured Task Decomposition + Delegation

### Terminology Contract

"Audit" in this pipeline means **alignment against stated problems and
constraints** — checking directional coherence between what the work claims
to solve and what it actually solves. It NEVER means feature-coverage
checking against a checklist. Plans describe problems and strategies, not
enumerable features. If you find yourself counting features, you are in the
wrong frame.

Use for most large tasks needing structured execution — not just code review.

## Core Algorithm

1. **Intake + scope.**
   - Capture user goal, success criteria, constraints, completion conditions.
   - If scope is underspecified for critical decisions, ask one concrete
     clarification before delegation.

2. **Identify natural sections.**
   - The input already has structure — section headers, numbered items,
     logical chunks, or other natural boundaries. Follow them.
   - Do NOT invent your own decomposition. The input's structure IS the
     decomposition.
   - Each natural section becomes one unit of work.

3. **Prepare tmp directory.**
   - Create `.tmp/audit/<run_slug>/`.
   - Create `.tmp/audit/<run_slug>/synthesis.md` for final consolidation.
   - Section agents create their own section files (step 4).

4. **Delegate one agent per natural section.**
   - Each agent:
     - Reads its section from the source document
     - Gathers context from surrounding sections and referenced files/code
     - Creates its section file at `.tmp/audit/<run_slug>/<section-key>.md`
     - The section file contains the section text **pasted verbatim** plus
       context decorations (what the agent researched, what relates, what's
       relevant from the codebase)
     - Flags blockers immediately
   - Because sections are verbatim copies (not interpretations), decomposition
     accuracy is guaranteed.

5. **Run section agents.**
   - Launch all section agents concurrently when independent.
   - Track incomplete items; if an agent is blocked, route it separately
     before final synthesis.

6. **Synthesize with a separate final agent.**
   - After all section agents finish, create one additional sub-agent:
     - Read all section files
     - Verify complete coverage — every part of the original input is
       represented in some section file
     - Deduplicate findings
     - Resolve conflicts between sections
     - Produce severity-ranked final report
     - Copy summary and risks to synthesis file

7. **Return final result.**
   - Present top-level decision/recommendation first.
   - Then include key findings, unresolved risks, and exact next actions.

## Section File Template

```markdown
### Verbatim Section
<paste of the original section text, unmodified>

### Context
<what the agent researched and found relevant>

### Findings
- Finding: ...
- Evidence: ...
- Blocker: ...

### Conclusion
- Key outcomes
- Risks
- Files reviewed
- Open questions
```

## Exit Criteria

1. Section files are complete — each contains verbatim text + context.
2. Synthesis file captures confirmed findings + action items.
3. Synthesis confirms complete coverage of the original input.
4. Open items are marked with owner, reason, and what would unblock them.
