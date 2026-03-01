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

BANNED_PHRASES=(
  "its exploration is skipped"
  "skip codemap exploration"
  "skip it.*resume-safe"
)

for phrase in "${BANNED_PHRASES[@]}"; do
  while IFS= read -r match; do
    echo "[LINT] Superseded behavior claim: $match"
    EXIT_CODE=1
  done < <(grep -rn -i \
    -e "$phrase" \
    --include="*.md" \
    "$WH/implement.md" "$WH/SKILL.md" \
    2>/dev/null || true)
done

if [ "$EXIT_CODE" -eq 0 ]; then
  echo "[LINT] No superseded behavior claims found."
fi
exit $EXIT_CODE
