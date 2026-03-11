# User Philosophy Seed

1. Scripts own the workflow rails. Agents decide within declared scope, but they
   do not invent new workflow stages or bypass structured artifacts.
2. When correctness depends on an unresolved ambiguity, the system must surface
   a blocking question upward instead of guessing.
3. Preserve malformed or corrupt artifacts for inspection. Fail closed rather
   than silently normalizing away corruption.
4. Prefer small, composable artifacts with explicit contracts over one large
   document with implicit coupling.
5. Brownfield exploration should describe the current system before prescribing
   changes.
6. Any change that modifies project values or cross-cutting policy requires
   explicit user or parent approval before implementation proceeds.
