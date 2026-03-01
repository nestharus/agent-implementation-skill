#!/usr/bin/env bash
# Lint: detect "feature coverage audit" and stale verification-frame language
# in docs/prompts. Exits non-zero if prohibited patterns found outside
# anti-pattern sections or terminology contract definitions.
#
# Layout-portable: uses WORKFLOW_HOME env var to locate skill content root.
#   Dev repo:  WORKFLOW_HOME=src ./src/scripts/lint-audit-language.sh
#   Deployed:  ./scripts/lint-audit-language.sh  (WORKFLOW_HOME defaults to .)
set -euo pipefail

WH="${WORKFLOW_HOME:-.}"
EXIT_CODE=0

# --- Phrase group 1: feature-coverage framing (original) ---
while IFS= read -r match; do
  # Skip lines that are part of anti-pattern documentation or terminology contract
  if echo "$match" | grep -qi "anti-pattern\|NOT.*feature\|never.*feature.coverage\|not.*feature.audit\|Terminology"; then
    continue
  fi
  echo "[LINT] Prohibited audit-framing language: $match"
  EXIT_CODE=1
done < <(grep -rn -i \
  -e "feature checklist" \
  -e "feature coverage audit" \
  -e "coverage audit" \
  --include="*.md" --include="*.py" \
  --exclude="alignment-judge.md" \
  --exclude="qa-monitor.md" \
  --exclude="audit.md" \
  "$WH/agents" "$WH/scripts" "$WH/models.md" \
  "$WH/implement.md" "$WH/SKILL.md" \
  "$WH/research.md" "$WH/baseline.md" 2>/dev/null || true)

# --- Phrase group 2: stale verification-frame language (R76) ---
while IFS= read -r match; do
  # Skip anti-pattern/terminology-contract context
  if echo "$match" | grep -qi "anti-pattern\|Terminology\|NOT.*audit\|never.*audit"; then
    continue
  fi
  echo "[LINT] Stale verification-frame language: $match"
  EXIT_CODE=1
done < <(grep -rn -i \
  -e "audits for completeness" \
  -e "Audit the Response" \
  -e "Evaluate Audit Results" \
  -e "final audit against ALL" \
  -e "user audits for" \
  --include="*.md" --include="*.py" \
  --exclude="alignment-judge.md" \
  --exclude="qa-monitor.md" \
  --exclude="audit.md" \
  "$WH/agents" "$WH/scripts" "$WH/models.md" \
  "$WH/implement.md" "$WH/SKILL.md" \
  "$WH/research.md" "$WH/baseline.md" 2>/dev/null || true)

# --- Phrase group 3: stale model invocation (R76) ---
while IFS= read -r match; do
  echo "[LINT] Stale model invocation: $match"
  EXIT_CODE=1
done < <(grep -rn \
  -e "Use via.*Task tool" \
  --include="*.md" \
  "$WH/models.md" 2>/dev/null || true)

if [ "$EXIT_CODE" -eq 0 ]; then
  echo "[LINT] No prohibited audit-framing language found."
fi
exit $EXIT_CODE
