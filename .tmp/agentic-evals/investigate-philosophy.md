# Investigation: Philosophy Bootstrap Grounding Validation Too Strict

## Context

An agentic eval scenario (`philosophy-bootstrap-from-user-source`) exposed a behavioral gap:
the philosophy bootstrap correctly distills operational principles from user-provided source
material (AK1 PASS, AK2 PASS), but the **grounding validation** gate rejects the output,
leaving the bootstrap in a failed state instead of ready.

The eval log shows:
```
[section-loop] Intent bootstrap: distilling operational philosophy from 1 user-provided source(s)
[section-loop]   dispatch claude-opus → philosophy-distill-prompt.md
[section-loop] Intent bootstrap: philosophy grounding validation failed — blocking section (fail-closed)
```

The judge found:
- AK1 PASS: Philosophy contains P1-P6 preserving all major user seed rules
- AK2 PASS: Every principle has source_type=user_source pointing to the right file
- AK3 FAIL: Bootstrap status shows active blocking_state='NEEDS_PARENT'
- AK4 FAIL: Signal has bootstrap_state='failed' instead of 'ready'

So the LLM produced correct output, but the validation gate rejected it.

## Your Task

Investigate the philosophy bootstrap system to understand:

1. **How does the bootstrap flow work?**
   - Read `src/scripts/lib/intent/philosophy_bootstrap.py`
   - Trace the full flow: source discovery → distillation → grounding validation → status write

2. **What does grounding validation check?**
   - What criteria must the distilled philosophy meet?
   - Is it checking structural format? Content alignment? Source traceability?
   - What specific condition triggers the "grounding validation failed" message?

3. **Why does it fail?**
   - Is the validation checking something the LLM output doesn't provide?
   - Is there a format mismatch (e.g., expecting JSON but getting markdown)?
   - Is the threshold too strict for the eval's fixture content?
   - Is the validation checking against files that don't exist in the eval's seeded state?

4. **What would a fix look like conceptually?**
   - Should the validation criteria be relaxed?
   - Should the eval seed more files to satisfy the validation?
   - Is this a real quality gate that should be preserved, or an overly strict check?

## Files to Read

Start with these files and follow imports as needed:
- `src/scripts/lib/intent/philosophy_bootstrap.py` — main bootstrap logic
- Any grounding validation module referenced from there
- `evals/agentic/fixtures/philosophy-bootstrap-from-user-source/` — the eval fixture

## Output

Write your findings to `$PROJECT_ROOT/.tmp/agentic-evals/philosophy-investigation.md` with:
- A clear explanation of how grounding validation works
- The exact failure condition and why it triggers
- Whether this is a fixture issue or a code issue
- Suggested fix approach (conceptual, not implementation)
