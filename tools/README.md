# Workflow Tools

Language-specific extraction tools used by the workflow pipeline.

## Naming Convention

`extract-docstring-<ext>` — extracts module-level docstring/comment for
files with the given extension.

## Adding New Extensions

If the pipeline encounters a file extension with no extraction tool:
1. Opus agent writes a new `extract-docstring-<ext>` tool
2. Tool follows the same interface: `<tool> <file>` → prints docstring
3. Supports `--batch` and `--stdin` modes
4. Outputs `NO DOCSTRING` if no docstring found

## Interface

```bash
# Single file
extract-docstring-py <file-path>

# Multiple files
extract-docstring-py --batch <file1> <file2> ...

# From stdin (one path per line)
find . -name "*.py" | extract-docstring-py --stdin
```

Output format:
```
<file-path>
<docstring text or "NO DOCSTRING">
```

Batch/stdin mode separates entries with `---`.

## Tool Contract

Tools are agent-created capabilities that emerge from bottom-layer work.
When requesting or building a new tool:

- **Structured output**: Tool output must be machine-parseable (JSON, line
  protocol, or structured text). Scripts consume tool outputs mechanically —
  no prose interpretation.
- **Prefer existing tools**: Before requesting a new extractor, check if an
  existing tool covers the use case. Unnecessary duplication increases
  maintenance burden.
- **Interface stability**: Once a tool interface is published (arguments,
  output format), changes must be backwards-compatible or all callers must
  be updated simultaneously.
- **Fail-closed**: If a tool cannot produce valid output, it must exit
  non-zero with a diagnostic. Silent fallbacks are forbidden.
  - `NO DOCSTRING` / `NO SUMMARY` means **true absence** (file parsed OK
    but no docstring/summary found).
  - `ERROR: <type>: <message>` means **read or parse failure** — the file
    could not be processed. This is reported per-file so batch output
    remains complete; the tool exits with code 2 if any errors occurred.
