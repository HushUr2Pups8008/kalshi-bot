# CLAUDE.md

## Working Style

- Understand and honor the intent of these local instructions fully: they direct the agent back to the broader global guidance, and that guidance must be followed accordingly rather than interpreted narrowly.
- For non-trivial work, plan first and keep the user informed as scope changes.
- Prefer direct execution once the scope is clear.
- Prefer simple root-cause fixes over temporary patches.
- Use delegation only when the environment supports it and it clearly reduces risk or latency.
- Keep summaries concise and decision-oriented.

## Bug-Fixing Preference

- When given a bug report, diagnose it from concrete evidence such as logs, errors, and failing checks.
- Reduce user back-and-forth where the next safe step is clear.

## Continuous Improvement

- After repeated correction on the same pattern, capture the lesson in the project's preferred tracking system if one exists.
- This project's unified tracking system is `docs/profit_path_debt_log.md`; do not create parallel macOS, logging, S4.5, or architecture debt logs.

See `~/.claude/rules/planning.md` for planning rules.
See `~/.claude/rules/validation.md` for validation rules.
See `~/.claude/rules/git_workflow.md` for git workflow rules.
