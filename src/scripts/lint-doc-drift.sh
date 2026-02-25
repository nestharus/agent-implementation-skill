#!/usr/bin/env bash
# Lint: detect superseded behavior claims in docs/templates.
# Exits non-zero if known-wrong phrases reappear.
set -euo pipefail

REPO_ROOT="${1:-.}"
EXIT_CODE=0

# Phrases that describe superseded behavior. Each is a regex pattern
# matched case-insensitively. These phrases were accurate in earlier
# iterations but now conflict with the implemented validation approach.
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
    "$REPO_ROOT/src/implement.md" "$REPO_ROOT/src/SKILL.md" \
    "$REPO_ROOT/src/scripts/task-agent-prompt.md" 2>/dev/null || true)
done

if [ "$EXIT_CODE" -eq 0 ]; then
  echo "[LINT] No superseded behavior claims found."
fi
exit $EXIT_CODE
