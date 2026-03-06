#!/usr/bin/env bash
# Lint: detect superseded behavior claims in docs/templates (secondary migration guard).
# Primary verification: lint-doc-contracts.sh checks required contract statements.
# Exits non-zero if known-wrong phrases reappear.
#
# Layout-portable: uses WORKFLOW_HOME env var to locate skill content root.
#   Dev repo:  WORKFLOW_HOME=src ./src/scripts/lint-doc-drift.sh
#   Deployed:  ./scripts/lint-doc-drift.sh  (WORKFLOW_HOME defaults to .)
set -euo pipefail

WH="${WORKFLOW_HOME:-.}"
EXIT_CODE=0

# --- Group 1: exploration-skip drift (original) ---
EXPLORATION_PHRASES=(
  "its exploration is skipped"
  "skip codemap exploration"
  "skip it.*resume-safe"
)

for phrase in "${EXPLORATION_PHRASES[@]}"; do
  while IFS= read -r match; do
    echo "[LINT] Superseded behavior claim: $match"
    EXIT_CODE=1
  done < <(grep -rn -i \
    -e "$phrase" \
    --include="*.md" \
    "$WH/implement.md" "$WH/SKILL.md" \
    2>/dev/null || true)
done

# --- Group 2: execution-model drift (R76) ---
# The old manual-orchestrator framing where the reader is told to build
# prompts, launch agents, and manage dispatch loops directly.
EXEC_MODEL_PHRASES=(
  "The orchestrator (you)"
  "orchestrator (you)"
  "Write prompt files for agents"
)

for phrase in "${EXEC_MODEL_PHRASES[@]}"; do
  while IFS= read -r match; do
    # Skip lines that describe what scripts do or are in comments
    if echo "$match" | grep -qi "script-level\|script-internal\|script-owned"; then
      continue
    fi
    echo "[LINT] Execution-model drift (old orchestrator framing): $match"
    EXIT_CODE=1
  done < <(grep -rn -F \
    -e "$phrase" \
    --include="*.md" \
    "$WH/implement.md" "$WH/SKILL.md" \
    2>/dev/null || true)
done

# --- Group 3: stale pipeline role-language (R76) ---
# "Audit proposal" as a pipeline step or model role description
while IFS= read -r match; do
  # Skip anti-pattern/terminology sections
  if echo "$match" | grep -qi "anti-pattern\|Terminology\|NOT.*audit\|never.*audit\|does not mean"; then
    continue
  fi
  echo "[LINT] Stale pipeline role-language: $match"
  EXIT_CODE=1
done < <(grep -rn \
  -e "Audit proposal" \
  --include="*.md" \
  "$WH/models.md" "$WH/research.md" \
  2>/dev/null || true)

# --- Group 4: stale delegation language in prompt templates (R77) ---
# "delegate/summarize" implies old-model direct delegation, not task submission.
while IFS= read -r match; do
  echo "[LINT] Stale delegation language in prompt template: $match"
  EXIT_CODE=1
done < <(grep -rn \
  -e "delegate/summarize" \
  --include="*.md" --include="*.py" \
  "$WH/scripts/section_loop/prompts/templates" \
  "$WH/scripts/section_loop/section_engine" \
  "$WH/scripts/section_loop/coordination" \
  2>/dev/null || true)

# --- Group 5: stale invocation style (R77, extended R78) ---
# "uv run agents" is the wrong binary invocation; the correct form is "agents".
# R78: also scan runtime dispatch Python files for subprocess invocations.
while IFS= read -r match; do
  echo "[LINT] Stale invocation style (uv run agents → agents): $match"
  EXIT_CODE=1
done < <(grep -rn \
  -e "uv run agents" \
  -e '"uv", "run".*"agents"' \
  -e "'uv', 'run'.*'agents'" \
  --include="*.md" --include="*.py" \
  "$WH/models.md" "$WH/research.md" "$WH/baseline.md" \
  "$WH/rca.md" "$WH/implement.md" "$WH/SKILL.md" "$WH/audit.md" \
  "$WH/scripts/section_loop/dispatch.py" \
  "$WH/scripts/scan/dispatch.py" \
  "$WH/scripts/substrate/runner.py" \
  2>/dev/null || true)

# --- Group 6: stale direct dispatch in ingestion callers (flow system) ---
# Runtime callers must use ingest_and_submit (queue-based), not
# ingest_and_dispatch (legacy direct dispatch).
while IFS= read -r match; do
  # Skip the definition itself and deprecation docs in task_ingestion.py
  if echo "$match" | grep -qi "def ingest_and_dispatch\|Deprecated\|Use.*ingest_and_submit\|Legacy.*ingest_and_dispatch"; then
    continue
  fi
  echo "[LINT] Stale direct dispatch (ingest_and_dispatch → ingest_and_submit): $match"
  EXIT_CODE=1
done < <(grep -rn \
  -e "ingest_and_dispatch" \
  --include="*.py" \
  "$WH/scripts/section_loop/section_engine" \
  "$WH/scripts/section_loop/coordination" \
  2>/dev/null || true)

# --- Group 7: inline prompt as normative invocation (R80) ---
# GLM section should not teach inline prompts as the standard mode.
while IFS= read -r match; do
  # Skip lines that explicitly say "not the pipeline-standard" or similar
  if echo "$match" | grep -qi "not.*pipeline\|non-normative\|not.*standard"; then
    continue
  fi
  echo "[LINT] Inline-prompt normative drift: $match"
  EXIT_CODE=1
done < <(grep -rn -i \
  -e "inline.*also accepted" \
  -e "inline.*instructions.*accepted" \
  --include="*.md" \
  "$WH/models.md" \
  2>/dev/null || true)

# --- Group 8: shared-file-only coordination framing (R80) ---
# Coordination grouping must be concern-based, not file-overlap only.
while IFS= read -r match; do
  echo "[LINT] Shared-file-only coordination framing: $match"
  EXIT_CODE=1
done < <(grep -rn \
  -e "Problems sharing files" \
  -e "relationships via shared files" \
  --include="*.md" \
  "$WH/implement.md" \
  2>/dev/null || true)

# --- Group 9: Stage 4/5 proposal drift (V2/V3 violations) ---
# Stale change-strategy and go-beyond language in implement.md.
PROPOSAL_DRIFT_PHRASES=(
  "which files change"
  "what kind of changes"
  "go beyond the integration proposal"
  "authority to go beyond"
)

for phrase in "${PROPOSAL_DRIFT_PHRASES[@]}"; do
  while IFS= read -r match; do
    echo "[LINT] Proposal drift (Stage 4/5): $match"
    EXIT_CODE=1
  done < <(grep -rn -i \
    -e "$phrase" \
    --include="*.md" \
    "$WH/implement.md" \
    2>/dev/null || true)
done

# "in what order" near changes context in implement.md
while IFS= read -r match; do
  # Only flag if the surrounding context relates to changes/files
  if echo "$match" | grep -qi "change\|file\|modif"; then
    echo "[LINT] Proposal drift (Stage 4/5): $match"
    EXIT_CODE=1
  fi
done < <(grep -rn -i \
  -e "in what order" \
  --include="*.md" \
  "$WH/implement.md" \
  2>/dev/null || true)

# --- Group 10: Stale microstrategy derivation stubs (V4 violation) ---
# runner.py (or any .py) must not tell the implementer to derive a microstrategy.
while IFS= read -r match; do
  echo "[LINT] Stale microstrategy derivation stub: $match"
  EXIT_CODE=1
done < <(grep -rn \
  -e "derive a microstrategy" \
  -e "derive the microstrategy" \
  --include="*.py" \
  "$WH/scripts/section_loop/section_engine" \
  2>/dev/null || true)

if [ "$EXIT_CODE" -eq 0 ]; then
  echo "[LINT] No superseded behavior claims found."
fi
exit $EXIT_CODE
