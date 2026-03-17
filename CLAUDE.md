# CLAUDE.md

## Plan Mode Default
- Enter plan mode for any non-trivial task (3+ steps or architectural decisions)
- If something goes wrong, STOP and re-plan immediately — don't keep pushing
- Use plan mode for verification steps, not just building
- Write detailed specs upfront to reduce ambiguity

## Subagent Strategy
- Use subagents frequently to keep the main context window clean
- Offload research, exploration, and parallel analysis to subagents
- For complex problems, throw more compute via subagents
- Assign one task per subagent for focused execution

## Self-Improvement Loop
- After any correction from the user, update `tasks/lessons.md` with the pattern
- Write rules for yourself to prevent repeating the same mistake
- Ruthlessly iterate on these lessons until the mistake rate drops
- Review `tasks/lessons.md` at the start of each session

## Verification Before Done
- Never mark a task complete without proving it works
- Diff behavior between main and your changes when relevant
- Ask yourself: "Would a staff engineer approve this?"
- Run tests, check logs, and demonstrate correctness

## Demand Elegance (Balanced)
- For non-trivial changes, ask: "Is there a more elegant solution?"
- If a fix feels hacky, ask: "Knowing everything I know now, implement the elegant solution."
- Skip this for simple fixes — don't over-engineer
- Challenge your own work before presenting it

## Autonomous Bug Fixing
- When given a bug report: just fix it
- Use logs, errors, and failing tests to diagnose
- Require zero context switching from the user
- Fix failing CI tests automatically

## Task Management
- Plan First
  - Write a clear, scoped plan in `tasks/todo.md` using checkable items
  - Group related tasks under a dated or named section
- Verify Plan
  - Re-read the plan in `tasks/todo.md` before implementation begins
  - Present a brief summary to the user and wait for explicit go-ahead
  - If the task was shelved and resumed later, always re-read the plan first — context may have drifted for both of us
- Track Progress
  - Mark items complete as you go
  - Add brief notes inline if needed for context
- Explain Changes
  - Provide a high-level summary of what was done and why
- Document Results
  - Add a short review section to `tasks/todo.md` summarizing outcomes, issues, and decisions
- Maintain the Todo File (Critical)
  - Move completed sections to `tasks/completed.md` (never delete — archive with date heading)
  - Move valuable insights to `tasks/lessons.md`
  - Keep `tasks/todo.md` focused on active and near-term work only
- Capture Lessons
  - Record reusable insights, fixes, or patterns in `tasks/lessons.md`


## Version Control
- Bump `VERSION` file on every commit that adds features, fixes bugs, or refactors
- Versioning scheme: `MAJOR.MINOR.PATCH`
  - PATCH: bug fixes, single-file tweaks (e.g. 0.4.0 → 0.4.1)
  - MINOR: new features, multi-file improvements, new modules (e.g. 0.4.0 → 0.5.0)
  - MAJOR: breaking architecture changes or go-live milestone (e.g. 0.x → 1.0.0)
- Include the VERSION bump in the same commit as the code change — never a follow-up commit
- If you forgot to bump VERSION, catch it before `git push` and amend the commit

## Core Principles

**Simplicity First**
Make every change as simple as possible and minimize code impact.

**No Laziness**
Find root causes. Avoid temporary fixes. Maintain senior-level engineering standards.
