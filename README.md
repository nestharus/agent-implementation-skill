# agent-implementation-skill

A Claude Code skill plus a Python control plane for running a multi-phase,
multi-model software development workflow. The skill (`src/SKILL.md`) tells the
session which phase it is in (research, proposal evaluation, design baseline,
implementation, root cause analysis) and which instruction file to read. The
implementation phase is driven by a Python pipeline (`python -m pipeline`) that
keeps its state in a separate "planspace" directory with a SQLite `run.db`, and
dispatches work to external models (named `claude-opus`, `gpt-high`,
`gpt-xhigh` and `glm` in the default model policy) by shelling out to an
`agents` command-line tool. Agent definitions are markdown files with YAML
frontmatter under `src/<system>/agents/`.

## What's in the repo

| Path | Contents |
|------|----------|
| `src/` | The skill itself: `SKILL.md`, phase docs (`implement.md`, `research.md`, `rca.md`, `evaluate.md`, `baseline.md`, `audit.md`, `constraints.md`, `models.md`), the Python packages (`pipeline`, `orchestrator`, `flow`, `dispatch`, `scan`, `coordination`, ...), 76 agent definitions, schedule `templates/`, `scripts/` (`db.sh`, `workflow.sh`, the `log_extract` package) and `tools/` |
| `tests/` | pytest suite (`component/` and `integration/`) |
| `evals/` | Live-LLM eval harnesses: `evals/harness.py` (per-agent scenarios in `evals/scenarios/`) and `evals/agentic/` (end-to-end scenarios in `evals/agentic/fixtures/`) |
| `governance/` | Problem archive, pattern catalog, audit prompt and history, risk register |
| `philosophy/`, `execution-philosophy/` | Design principles and diagrams |
| `system-synthesis.md` | Architecture notes linking systems to governance records |
| `docs/cleanup-backlog.md` | Tracked structural cleanup items |
| `AGENTS.md` | Instructions for coding agents working on this repo |
| `.github/workflows/deploy-skill.yml` | Mirrors `src/` to a public repo (see Status) |

## Requirements

- Python 3.14 or newer (`requires-python = ">=3.14"`)
- Python packages: `dependency-injector`, `pydantic`, `PyYAML` (see `pyproject.toml`); `pytest` for tests
- `bash` and a `python3` on `PATH` (`src/scripts/db.sh` runs its SQL through Python's `sqlite3` module)
- The `agents` CLI from [nestharus/agent-runner](https://github.com/nestharus/agent-runner),
  with model configs for the model names the pipeline uses (`claude-opus`,
  `gpt-high`, `gpt-xhigh`, `glm`). Model configs are TOML files in
  `~/.config/oulipoly-agent-runner/models/`; `AGENTS.md` describes the format.
- Claude Code, to use it as a skill

## Install

As a Claude Code skill, put the contents of `src/` in a skill folder. `SKILL.md`
locates itself by searching `~/.claude/skills/*/SKILL.md` and
`.claude/skills/*/SKILL.md`, so either location works:

```bash
git clone https://github.com/nestharus/agent-implementation-skill.git
mkdir -p ~/.claude/skills/agent-implementation-skill
cp -r agent-implementation-skill/src/* ~/.claude/skills/agent-implementation-skill/
```

The same `src/` contents are published at
[oulipoly/agent-software-developer-skill](https://github.com/oulipoly/agent-software-developer-skill),
which can be cloned directly into the skills folder instead.

For development (tests, evals, `logex`), install the project from the repo root:

```bash
uv sync            # or: pip install -e . pytest
```

## Usage

In Claude Code, invoke the `agent-implementation-skill` skill. `SKILL.md` walks
the session through phase detection and points it at the right phase document.

Start the implementation pipeline (run with `src/`, or the installed skill
folder, as the working directory or on `PYTHONPATH`):

```bash
python -m pipeline <planspace> <codespace> --spec <spec-path> [--slug <slug>] [--qa-mode] [--resume]
```

`<planspace>` is the workflow state directory (artifacts, `run.db`);
`<codespace>` is the project being changed. `--qa-mode` writes
`{"qa_mode": true}` to the planspace parameters so dispatched agents are
intercepted.

Coordination database helper (full command list in `src/SKILL.md`):

```bash
bash src/scripts/db.sh init <planspace>/run.db
bash src/scripts/db.sh tail <planspace>/run.db
```

Console scripts installed by `pyproject.toml`:

```bash
logex <planspace> [--format jsonl|text|csv] [--agent NAME] [--kind KIND] [--grep REGEX]
agentic-evals --list
agentic-evals --scenario <id> --keep-failed
agentic-evals --max-cost-tier cheap
```

`logex` merges `run.db` events with session logs from Claude Code, Codex,
OpenCode and Gemini CLI home directories into one timeline. `agentic-evals`
seeds a temporary planspace/codespace from a fixture, runs real workflow entry
points in QA mode, and checks the results structurally and with an LLM judge.
The per-agent harness runs with `python -m evals.harness --list | --run <scenario> | --all`.
Both eval harnesses make real model calls through `agents`.

## Tests

```bash
uv run python -m pytest -x -q
```

pytest is configured in `pyproject.toml` (`testpaths = ["tests"]`, with `src`,
`src/scripts` and `tests` on the path). Some integration tests call
`bash src/scripts/db.sh`, so they need `bash` and `python3` available.

## Status

- Version `0.1.0` in `pyproject.toml`.
- First commit 2026-02-18; most recent commit 2026-07-16 (two merged fixes:
  terminating timed-out agent process groups, and parsing OpenCode event output
  in the coordination planner).
- On every push to `main`, `deploy-skill.yml` replaces the contents of
  `oulipoly/agent-software-developer-skill` with `src/` (keeping that repo's
  `LICENSE`), removes Python caches, and commits as `github-actions[bot]`.

## License

MIT. See [LICENSE](LICENSE).
