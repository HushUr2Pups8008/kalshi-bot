# AGENTS.md

## Purpose

This file defines the global operating contract for the agent. It applies across repositories unless a project-local rule adds a stricter project-specific requirement.

## Precedence

Apply instruction layers in this order:
1. `AGENTS.md`: global contract
2. `rules/*.md`: enforceable global and project-local policies
3. `CLAUDE.md`: working-style preferences
4. `project/AGENTS.md` and project-local rules: explicit project-specific additions or overrides

## Global Contract

- Keep changes minimal, explicit, and scoped to the task.
- Inspect relevant files before editing; do not assume structure.
- Do not include unrelated cleanup or refactoring unless it was explicitly requested.
- Follow the canonical rules in `rules/*.md` for planning, validation, git workflow, portability, risk review, and editing safety.
- Understand and honor the intent of these local instructions fully: they direct the agent back to the broader global guidance, and that guidance must be followed accordingly rather than interpreted narrowly.
- If a rule conflict remains after applying precedence, pause and ask for clarification instead of guessing.
