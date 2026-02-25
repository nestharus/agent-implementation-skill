# Task: Explore Codebase and Build Codemap

You are an exploration agent. Your job is to understand this codebase by exploring it — not by enforcing a rigid template on the codebase's structure.

## How to Explore

1. Start with the root: list the top-level directory to see what's there
2. Read key files that help you understand purpose: README, configuration files, entry points
3. Explore directories that seem important — read files, understand relationships
4. Use GLM agents for quick file reads when you need to check many files
5. Follow your curiosity — if something looks important, investigate it

## What to Write

Write a codemap that captures your understanding of the codebase. Include:
- What this project is and does
- How the code is organized (not just a directory listing — what the organization *means*)
- Key files and why they matter
- How different parts relate to each other
- Anything surprising, unusual, or important for someone working with this code

The format should fit what you discovered. Let the codemap body reflect the natural structure of the project. The only required structured interface is the Routing Table section below.

## Routing Table Interface (Required)

At the END of your codemap, include a structured routing section:

```
## Routing Table

### Subsystems
- <subsystem-name>: <glob-pattern-or-directory> — <one-line-purpose>

### Entry Points
- <entry-point-file>: <what-it-does>

### Key Interfaces
- <file-or-module>: <interface-description>

### Unknowns
- <area>: <what-is-unclear-and-why>

### Confidence
- overall: high|medium|low
- reason: <why-this-confidence-level>
```

This routing table is consumed by downstream agents for file selection.
Be honest about unknowns — it's better to say "I'm not sure about X"
than to guess wrong.

## Resolution Rubric

Keep the codemap COARSE by default. Only go deeper (function signatures,
class hierarchies) on: (a) main entrypoints, (b) central libraries
referenced across directories, or (c) interfaces called by multiple
subsystems. For everything else, describe purpose and relationships,
not implementation details.

The codemap is a ROUTING MAP — it helps agents find the right files,
not understand every line.

## Project Mode Classification

After writing the codemap, determine whether this is a **greenfield** or
**brownfield** project:
- **greenfield**: Empty or near-empty project (only config/scaffold files,
  no substantive source code yet)
- **brownfield**: Existing source code that new work must integrate with

Write your classification to: `{project_mode_path}`
The file should contain EXACTLY one word: `greenfield` or `brownfield`.

**Also write a structured JSON signal** to
`{project_mode_signal}`:
```json
{{"mode": "greenfield|brownfield", "confidence": "high|medium|low", "reason": "..."}}
```
