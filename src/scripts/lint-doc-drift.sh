#!/usr/bin/env bash
# Lint: detect superseded behavior claims in docs/templates.
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

if [ "$EXIT_CODE" -eq 0 ]; then
  echo "[LINT] No superseded behavior claims found."
fi
exit $EXIT_CODE
