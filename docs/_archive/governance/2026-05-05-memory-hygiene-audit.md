# Memory hygiene audit (cycle 7)

**Type:** read-only review (Claude task per Implementation Contract §9 — review).
**Source:** `~/.claude/projects/-Users-jacobparenti-vscode-kalshi-bot/memory/` contents at HEAD `afa1dc9`.
**Drafted:** 2026-05-05.
**Audience:** operator considering memory cleanup post-cycle-7.

## TL;DR

**2 memory entries. Both still relevant + non-conflicting after 7 cycles.** No drift; no contradictions with Implementation Contract §9 or the operator's established cycle pattern. **No cleanup recommended.** Memory hygiene is healthy.

## Inventory

```
~/.claude/projects/-Users-jacobparenti-vscode-kalshi-bot/memory/
├── MEMORY.md
├── feedback_soak_acceleration_split.md
└── feedback_soak_confirmation_cadence.md
```

## Per-entry audit

### `feedback_soak_acceleration_split.md`

**Substance:** When user proposes compressing a soak, split into (a) calendar-floor cut (low-risk) vs (b) cadence/policy-knob cut (mid-soak invalidates measurement). Always (a) now, (b) for next-soak from cycle 1.

**Origin:** PROFIT-PHASE2-001 day-4 (2026-05-04). Operator + Codex aligned on the split; landed in §8.5.1 addendum.

**Adherence in cycles 1-7:**
- §8.5.1 addendum + §8.5.2 carve-out + criteria runbook all applied the (a) cut: 14d → 7d calendar floor.
- The (b) cut (90 min fast / 12 h deep cadence) was deferred to PHASE2-002 cycle 1 per `PROFIT-PHASE2-002-onboarding.md` §3.
- No mid-soak cadence/policy edits attempted in cycles 1-7.

**Verdict:** ✅ memory aligned with current behavior. Still load-bearing for PHASE2-002 onboarding decisions when that soak fires post-Wave-1.

### `feedback_soak_confirmation_cadence.md`

**Substance:** Mid-soak confirmation reports cap at 1 per UTC day per agent. Multi-snapshot cycles burn tokens; operator flagged 2026-05-04.

**Origin:** PROFIT-PHASE2-001 day-3/4 (2026-05-03/04). Operator flagged after observing 5 snapshots in a single UTC day.

**Adherence in cycles 1-7:**
- Cycles 1-7 produced 0 mid-soak confirmation reports outside the cycle-2 reference (which was a one-time pre-stage of the Day-4 confirmation, not a fresh confirmation report).
- Operator's "execute your N, I'll share with Codex" pattern doesn't trigger soak-confirmation; the cycle-pattern is task-execution, not soak-monitoring.
- Codex's empirical baseline reports (cycle 4) and audit reports (cycles 3-6) are NOT mid-soak confirmation reports per the memory's definition — they're empirical-data-capture artifacts that inform Wave-1/2/3 deploy decisions.

**Verdict:** ✅ memory aligned. Still load-bearing for any future soak (PHASE2-002, etc.) where mid-soak confirmation cadence might drift.

## What's NOT in memory (correct exclusions)

Per the auto-memory skill rules + cycle-1 conflict resolution:

- **No "Codex owns heavy coding" memory** (deleted 2026-05-05 cycle 0 because it conflicted with Implementation Contract §9; cycles 1-7 followed §9 directly).
- **No project-pattern memories** (e.g., "use docs/governance/ for operator-facing playbooks") — derivable from current project state; not memory-worthy.
- **No git-history-style memories** (e.g., "cycle 4 closed the DB backup gap") — `git log` + `docs/profit_path_debt_log.md` are authoritative.
- **No fix-recipe memories** (e.g., "if KILL_SWITCH fires, run §1 stop-bot first") — captured in `docs/governance/2026-05-05-kill-switch-fire-procedure-runbook.md`; lives where it's findable.

## MEMORY.md index review

```markdown
- [Soak confirmation cadence](feedback_soak_confirmation_cadence.md) — cap mid-soak confirmation reports at 1 per UTC day per agent
- [Soak-acceleration split](feedback_soak_acceleration_split.md) — calendar-floor cut vs decision-policy cut: never co-apply mid-soak
```

**Verdict:** ✅ index entries are concise, descriptive, and match the file content. No drift.

## Recommended cleanup actions

**None.** Memory is in a healthy state.

## Future-cycle memory triggers

If any of the following happens, a new memory entry may warrant capture:

1. **Operator surfaces a NEW preference about soak/deploy cadence** that I haven't already captured.
2. **A persistent cross-cycle pattern emerges** that future-Claude would otherwise miss (e.g., "operator prefers single-feature minor bumps over major bumps for Wave-N deploys").
3. **A non-obvious project constraint surfaces** that lives in conversation context, not docs (e.g., "operator's Mac Studio is ARM/M4 — qwen3:14b uses metal accel").

For each potential new entry: check if the substance lives in `docs/`, `CLAUDE.md`, or git-discoverable state first. Only memory-write if the answer is NO and the value to future-Claude is clear.

## Out of scope

- Project-level CLAUDE.md drift check (separate audit; cycle 5 README drift-check covers some surface).
- Global ~/.claude/CLAUDE.md hygiene (operator's broader agent config; not project-specific).
- Cross-project memory analysis (this audit is project-scoped only).

## Cross-links

- `~/.claude/projects/-Users-jacobparenti-vscode-kalshi-bot/memory/` — under audit
- `docs/IMPLEMENTATION_CONTRACT.md` §9 — sole authority on Claude/Codex split (NOT in memory)
- `docs/governance/2026-05-05-cross-cycle-contract-adherence-review.md` — cycle 4 cross-cycle audit (sibling)
