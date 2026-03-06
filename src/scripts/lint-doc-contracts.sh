#!/usr/bin/env bash
# Lint: verify required contract statements exist in canonical docs.
# Primary verification layer — checks what MUST be true.
# Companion to lint-doc-drift.sh (secondary: checks what must NOT appear).
#
# Layout-portable: uses WORKFLOW_HOME env var to locate skill content root.
#   Dev repo:  WORKFLOW_HOME=src ./src/scripts/lint-doc-contracts.sh
#   Deployed:  ./scripts/lint-doc-contracts.sh  (WORKFLOW_HOME defaults to .)
set -euo pipefail

WH="${WORKFLOW_HOME:-.}"
EXIT_CODE=0

# --- Group 1: Execution model contracts (implement.md) ---

if ! grep -q "task submission\|task queue" "$WH/implement.md" 2>/dev/null; then
  echo "[CONTRACT] Missing required statement in implement.md: execution model must reference 'task submission' or 'task queue'"
  EXIT_CODE=1
fi

if ! grep -q "concern-based\|problem-interaction" "$WH/implement.md" 2>/dev/null; then
  echo "[CONTRACT] Missing required statement in implement.md: coordination frame must reference 'concern-based' or 'problem-interaction'"
  EXIT_CODE=1
fi

if ! grep -q "dispatch_agent\|dispatch boundary" "$WH/implement.md" 2>/dev/null; then
  echo "[CONTRACT] Missing required statement in implement.md: dispatch architecture must reference 'dispatch_agent' or 'dispatch boundary'"
  EXIT_CODE=1
fi

# --- Group 2: Workflow terminology contracts (SKILL.md) ---

if ! grep -q "concern-based problem decomposition" "$WH/SKILL.md" 2>/dev/null; then
  echo "[CONTRACT] Missing required statement in SKILL.md: must reference 'concern-based problem decomposition' (R78 ratified)"
  EXIT_CODE=1
fi

if ! grep -q "alignment tracing" "$WH/SKILL.md" 2>/dev/null; then
  echo "[CONTRACT] Missing required statement in SKILL.md: must reference 'alignment tracing' (R78 ratified)"
  EXIT_CODE=1
fi

# --- Group 3: Invocation form contracts (models.md) ---

if ! grep -q "\-\-file" "$WH/models.md" 2>/dev/null; then
  echo "[CONTRACT] Missing required statement in models.md: must reference '--file' (pipeline-standard invocation form)"
  EXIT_CODE=1
fi

# --- Group 4: Verification frame contracts (research.md) ---

if ! grep -qi "divergence review" "$WH/research.md" 2>/dev/null; then
  echo "[CONTRACT] Missing required statement in research.md: must reference 'Divergence Review' (R76 ratified, not 'Audit the Response')"
  EXIT_CODE=1
fi

# --- Group 5: Decomposition frame contracts (audit.md) ---

if ! grep -qi "concern-based\|problem decomposition" "$WH/audit.md" 2>/dev/null; then
  echo "[CONTRACT] Missing required statement in audit.md: must reference 'concern-based' or 'problem decomposition' (R78 ratified)"
  EXIT_CODE=1
fi

# --- Group 6: Stage 4/5 problem-state contracts (implement.md) ---

if ! grep -q "proposal-state\|problem-state\|execution.readiness\|execution_ready" "$WH/implement.md" 2>/dev/null; then
  echo "[CONTRACT] Missing required statement in implement.md: Stage 4/5 must reference 'proposal-state', 'problem-state', or 'execution readiness' language"
  EXIT_CODE=1
fi

if [ "$EXIT_CODE" -eq 0 ]; then
  echo "[CONTRACT] All required contract statements verified."
fi
exit $EXIT_CODE
