# Task: Integration Proposal for Section {section_number}

## Summary
{summary}

## Files to Read
1. Section proposal excerpt: `{proposal_excerpt}`
2. Section alignment excerpt: `{alignment_excerpt}`
3. Section specification: `{section_path}`
4. Related source files (prioritize; read what's necessary; delegate/summarize if long):
{files_block}{problem_frame_ref}{codemap_ref}{corrections_ref}{substrate_ref}{tools_ref}{todos_ref}{intent_problem_ref}{intent_rubric_ref}{intent_philosophy_ref}{intent_registry_ref}
{existing_note}{problems_block}{notes_block}{decisions_block}{mode_block}{additional_inputs_block}
## Instructions

A section is a **problem region / concern**, not a file bundle. Related
files are a starting hypothesis. You are expected to explore and may
discover additional relevant files or identify irrelevant ones.

If an intent problem definition or rubric is listed above, treat it as the
canonical problem definition and alignment rubric for this section. Anchor
your proposal to it.

Treat TODO extraction (if listed in "Files to Read" above) as the
canonical in-scope microstrategy surface. If your proposal conflicts with
TODOs, reconcile explicitly (update plan or propose TODO updates).

You are writing an INTEGRATION PROPOSAL — a strategic document describing
HOW to wire the existing proposal into the codebase. The proposal excerpt
already says WHAT to build. Your job is to figure out how it maps onto the
real code.

### Accuracy First — Zero Risk Tolerance

Every shortcut introduces risk. You accept zero risk. You MUST explore the
codebase before writing any proposal. A proposal written without reading
existing code is a guess — guesses introduce risk. Never skip exploration,
never produce a shallow proposal, never simplify to save tokens. Shortcuts
are permitted ONLY when the remaining work is so trivially small that no
meaningful risk exists.

### Phase 1: Explore and Understand

Before writing anything, explore the codebase strategically. You MUST
understand the existing code before proposing how to integrate.

**Start with the codemap** if available — it captures the project's
structure, key files, and how parts relate. If codemap corrections exist,
treat them as authoritative fixes (wrong paths, missing entries,
misclassified files). Use it to orient yourself before diving into
individual files.

**Dispatch sub-agents for targeted exploration:**
```bash
EXPLORE="$(mktemp)"
echo '<your-instructions>' > "$EXPLORE"
agents --model {exploration_model} --project "{codespace}" --file "$EXPLORE"
```

Use sub-agents to:
- Read files related to this section and understand their structure
- Find callers/callees of functions you need to modify
- Check what interfaces or contracts currently exist
- Understand the module organization and import patterns
- Verify assumptions about how the code works

Do NOT try to understand everything upfront. Explore strategically:
form a hypothesis, verify it with a targeted read, adjust, repeat.

**Dispatch rule**: If dispatching an agent that has a defined role file in
`$WORKFLOW_HOME/agents/`, attach it via `--agent-file`:
```bash
agents --agent-file "$WORKFLOW_HOME/agents/<role>.md" \
  --model <model> --file <prompt>
```

### Phase 2: Write the Integration Proposal

After exploring, write a high-level integration strategy covering:

1. **Problem mapping** — How does the section proposal map onto what
   currently exists in the code? What's the gap between current and target?
2. **Integration points** — Where does the new functionality connect to
   existing code? Which interfaces, call sites, or data flows are affected?
3. **Change strategy** — High-level approach: which files change, what kind
   of changes (new functions, modified control flow, new modules, etc.),
   and in what order?
4. **Risks and dependencies** — What could go wrong? What assumptions are
   we making? What depends on other sections?

This is STRATEGIC — not line-by-line changes. Think about the shape of
the solution, not the exact code.

Write your integration proposal to: `{integration_proposal}`

### Microstrategy Decision

At the end of your proposal, include this line:
```
needs_microstrategy: true
```
or
```
needs_microstrategy: false
```

Set it to `true` if the section is complex enough that an implementation
agent would benefit from a tactical per-file breakdown (many files, complex
interactions, ordering dependencies). Set `false` for simple sections where
the integration proposal is sufficient guidance.

**Also write a structured JSON signal** to
`{artifacts}/signals/proposal-{section_number}-microstrategy.json`:
```json
{{"needs_microstrategy": true, "reason": "brief justification"}}
```
The JSON signal is mandatory and is the primary channel the script reads.
Inline text is optional for human readability; the script does not parse it.
{signal_block}
{mail_block}
