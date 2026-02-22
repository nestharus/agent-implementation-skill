#!/usr/bin/env bash
# Lint: detect "feature coverage audit" language in docs/prompts.
# Exits non-zero if prohibited patterns found outside anti-pattern sections.
set -euo pipefail

REPO_ROOT="${1:-.}"
EXIT_CODE=0

# Search for prohibited patterns
while IFS= read -r match; do
  # Skip lines that are part of anti-pattern documentation
  if echo "$match" | grep -qi "anti-pattern\|NOT.*feature\|never.*feature.coverage\|not.*feature.audit"; then
    continue
  fi
  echo "[LINT] Prohibited audit-framing language: $match"
  EXIT_CODE=1
done < <(grep -rn -i \
  -e "feature checklist" \
  -e "feature coverage audit" \
  -e "coverage audit" \
  --include="*.md" --include="*.py" \
  "$REPO_ROOT/agents" "$REPO_ROOT/scripts" "$REPO_ROOT/models.md" \
  "$REPO_ROOT/implement.md" "$REPO_ROOT/SKILL.md" 2>/dev/null || true)

if [ "$EXIT_CODE" -eq 0 ]; then
  echo "[LINT] No prohibited audit-framing language found."
fi
exit $EXIT_CODE
