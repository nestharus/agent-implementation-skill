# Workflow Tools

Language-specific extraction tools used by the workflow pipeline for Python and shell sources.

## Naming Convention

`extract-docstring-<ext>` — extracts module-level docstring/comment for
files with the given extension.

## Available Tools

| Tool | Extension | What it extracts |
|------|-----------|-----------------|
| `extract-docstring-py` | `.py` | Module-level docstring (ast.get_docstring) |
| `extract-docstring-sh` | `.sh` | Module-level comment block (top-of-file comments) |
| `extract-summary-md` | `.md` | YAML frontmatter summary + keywords |

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
