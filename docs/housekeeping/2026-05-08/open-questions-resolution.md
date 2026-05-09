# Phase-1 Audit — Open Questions Resolution

**Date resolved:** 2026-05-08
**Source audit:** `docs/housekeeping/2026-05-08/SUMMARY.md` § Open Questions
**Scope:** all 6 Open Questions raised by the Phase-1 audit synthesis pass.

---

## OQ1 — Domain-Purity Refactor (P2-07)

**Question:** Move `analysis/source_credibility.py`, `analysis/source_stats.py`, `analysis/keyword_stats.py` to `tasks/` to restore INV-4 layer purity, or accept the boundary leak?

**Decision:** **Defer to Phase 3.**

**Rationale:**
- Layer-purity refactor is architectural debt — important but not urgent
- P1 fixes (silent auth bypass, observability gaps) carry higher risk-reduction-per-effort
- Multi-file rename touches ~10 callsites; risk of merge conflicts with in-flight branches
- Doing it after small wins gives a cleaner blast radius

**Phase 3 plan when reached:**
- Rename in one commit
- Keep import shims at old paths for one release
- Delete shims after verification

**Logged as:** open Phase-3 carry-over. No code changes this session.

---

## OQ2 — Dead/Orphan Code Disposition (P2-15, P2-16, P2-17)

**Question:** For three orphan code clusters — abandoned (delete) or planned-future (keep + TODO)?

**Decisions:**

| Item | Location | Decision | Rationale |
|---|---|---|---|
| **A** | `analysis/dossier_builder.py:274` `identify_superseded`, `:295` `clear_on_resolution` | **Mark unused** | Test-only callers; built ahead of integration. Add `# UNUSED:` marker + 30-day reminder, delete if untouched. |
| **B** | `governance/decision.py` `to_disabled_source`, `to_disabled_keyword`, `to_threshold_override` (commented call site at `agent.py:235`) | **Keep** | Active future use — Task 4 governance roadmap interface. Add `# PLANNED:` comment + debt-log linkage. |
| **C** | `feeds/google_news_monitor.py` (not wired to `TradingBot.run()`) | **Delete** | Replaced by Reddit + Kalshi WS. Not coming back. |

**Phase 2 actions queued:**
- A: add `# UNUSED:` marker + calendar reminder for 2026-06-08 review
- B: add `# PLANNED: Task 4 governance interface — see PROFIT-GOV-002` and link in debt log
- C: delete `feeds/google_news_monitor.py` + matching tests + any imports

---

## OQ3 — Plugin Cleanup (P2-10)

**Question:** Uninstall the 9 disabled-but-installed plugins, or keep as fallback options?

**Decision:** **Option F — uninstalled 7 (across 2 batches), kept 1 (playwright) as fallback.**

**Execution log:**

Batch 1 (kalshi-irrelevant, definitely won't need):
- `frontend-design@claude-plugins-official` — uninstalled
- `code-simplifier@claude-plugins-official` — uninstalled
- `commit-commands@claude-plugins-official` — uninstalled

Batch 2 (ECC-overlap, ECC covers fully):
- `code-review@claude-plugins-official` — uninstalled
- `github@claude-plugins-official` — uninstalled
- `context7@claude-plugins-official` — uninstalled
- `feature-dev@claude-plugins-official` — uninstalled

Re-enabled (was disabled by my earlier consolidation error):
- `claude-md-management@claude-plugins-official` — re-enabled (ships `claude-md-improver` skill ECC does not duplicate)

Kept disabled (fallback):
- `playwright@claude-plugins-official` — most likely future need (signal scraping if Truth Social/X scraping becomes useful); ECC's playwright is also disabled but standalone is insurance

**Final plugin state:** 5 enabled, 1 disabled (playwright). Down from 13 originally.

**Lesson:** before disabling a plugin as "ECC-redundant," verify ECC ships the same operation by name. ECC's `revise-claude-md` is NOT equivalent to `claude-md-management:claude-md-improver` — different operations (session-derived update vs general audit).

---

## OQ4 — CLAUDE.md Format Style (P3-09 / P3-10)

**Question:** Adopt strict Trigger/Action format for gotchas (matches rule files), or keep current bold-inline narrative?

**Decision:** **Format-by-purpose. No conversion of existing files. Codify the principle.**

**Principle:**
- **Rule files** (`~/.claude/rules/*.md`) → Trigger/Action format. Mechanical guardrails. Machine-checkable.
- **Gotcha/context files** (CLAUDE.md, hard-won knowledge) → bold-inline narrative. WHY is load-bearing.
- **Test:** "if you remove the explanation, is the entry still useful?" Yes → Trigger/Action; no → narrative.
- **Don't mix formats within one file.** Split if needed.

**Implementation:**
- Created `~/.claude/rules/documentation_format.md` with 7 Trigger/Action rules codifying the principle
- Auto-loaded into every future Claude session
- Future audits flagging "format inconsistency" between rule files and CLAUDE.md will now hit this rule and back down

**Lesson:** "uniformity" ≠ "correctness." Format should follow function. The audit conflated the two.

---

## OQ5 — Domain Constraint for `/governance` (P2-06)

**Question:** Add a `/governance` rule to `~/.claude/rules/domain_constraints.md`, or is omission deliberate?

**Decision:** **Add the rule.**

**Rationale:**
- Risk is real and demonstrated — qwen3 anchor_rate regression (PROFIT-GOV-002) was a load-bearing fix
- Rule layer is the right home for "trigger: editing governance/prompts.py → action: preserve anchor_rate block"
- Phase runbooks are episodic; rule files are continuous-context — guardrail belongs in rules

**Implementation:**
Added 3 Trigger/Action bullets to `~/.claude/rules/domain_constraints.md` (after `/feeds` rule):
1. `/governance` directory scope discipline (prompt-edits, anchor_rate, decision authority stay scoped)
2. `governance/prompts.py` anchor_rate polarity block protection (cycle-17C lines 27–31, PROFIT-GOV-002)
3. `governance/agent.py` real-mode flip authority flagged as high-blast-radius (requires explicit user confirmation before merge)

**Auto-loaded** into every future Claude session via the rules loader.

---

## OQ6 — GitLab MCP (workspace-surface MI-1)

**Question:** Add a structured GitLab MCP, or is the existing `glab` Bash allowlist enough?

**Decision:** **Neither. Implement a custom read-only subagent wrapper instead.**

**Rationale:**
- MCP would add ~15-25 tools, pushing the harness back over Affaan's <80 ceiling
- Raw `glab` CLI works but bloats main-thread context with verbose output and trial-and-error on flag syntax
- Subagent wrapper keeps tool count flat, isolates glab output to subagent's sandbox, and forces structured handoff back to main thread
- RTK already caches `glab` output — subagent benefits compound (cached glab + compressed return)

**Implementation:**
Created `~/.claude/agents/gitlab-investigator.md` — read-only subagent that:
- Runs `glab` CLI for MR / CI / pipeline / issue queries
- Returns markdown tables + the exact `glab` command(s) used
- Refuses any state-changing GitLab operation (no merge, close, retry, cancel, update)
- Adds tools: 0 (uses Bash + Read + Grep only)
- Includes top-line summary + suggested follow-up commands

**Side-by-side test results (this session):**

| Metric | Raw `glab` in main thread | `gitlab-investigator` subagent |
|---|---|---|
| Main-thread tokens drained | ~800 (output + 2 flag-error retries) | ~650 (clean structured return) |
| Total system tokens | ~800 | ~40,000 (subagent context spin-up) |
| Structure | Free text, ad-hoc | Markdown tables, deterministic |
| Flag-trial-and-error visibility | In main thread | Absorbed inside subagent |
| Time | ~3-5s | ~13s |
| Actionable signal | Buried in raw output | Surfaced as top-line + follow-up suggestion |

**Verdict:** Subagent wins for multi-step or recurring GitLab queries (kalshi-bot operator workflows). Total token cost is higher per-query, but main-thread context savings + interpretive layer earn the cost. Raw CLI remains appropriate for one-shot trivial lookups.

**How to use:**
- Implicit: ask "give me a GitLab status snapshot" — Claude dispatches the subagent automatically
- Explicit: "use the gitlab-investigator agent to check CI failures on main"
- Refuses: state-changing ops (merge, close, etc.) — those stay in main-thread `glab` calls under user control

**Worth re-evaluating to MCP later if:** GitLab usage spikes >20 calls/session, or you need exact-JSON output for downstream piping.

### Refinement (same session, post-test)

Side-by-side test revealed the subagent's 40K cost was 50× higher than raw CLI's 800 tokens — almost entirely fixed subagent infrastructure overhead, not query cost. For routine status snapshots, that ratio is unjustifiable.

**Two-tier strategy adopted:**

| Tier | Use case | Cost | Mechanism |
|---|---|---|---|
| 1 | Routine status snapshots ("show me open MRs", "any failed CI on main") | ~800 tokens | `/gl-status` slash command (`~/.claude/commands/gl-status.md`) — inline chained `glab` Bash + main-thread post-processing |
| 2 | Interpretive multi-step queries ("investigate why CI is failing", "trace what broke MR 42") | ~40K tokens | `gitlab-investigator` subagent (`~/.claude/agents/gitlab-investigator.md`) — reserved for cross-correlation, trace work, synthesis |
| 3 | One-off ad-hoc lookup | ~600 tokens | Direct `glab` via Bash |

**Subagent updated** with auto-rejection logic: if dispatched for what looks like a routine snapshot, returns a redirect to `/gl-status` and exits without running any glab calls. Prevents accidental 50× cost.

**Result:** routine GitLab status now costs the same as raw CLI (~800 tokens) with structured output. Subagent reserved for queries that actually benefit from sandboxed iteration.

**Files added in refinement:**
- `~/.claude/commands/gl-status.md` — slash command for cheap snapshots
- `~/.claude/agents/gitlab-investigator.md` — updated description + auto-rejection block

---

## Summary of Changes This Session

| File | Change | Reason |
|---|---|---|
| `~/.claude/settings.json` | Plugin enable/disable updates (5 enabled, 1 disabled, 7 uninstalled) | OQ3 |
| `~/.claude/rules/documentation_format.md` | Created — 7 Trigger/Action rules | OQ4 |
| `~/.claude/rules/domain_constraints.md` | Appended 3 bullets for `/governance` | OQ5 |
| `~/.claude/agents/gitlab-investigator.md` | Created — read-only GitLab subagent | OQ6 |
| `~/.zshrc` | Added `ECC_DISABLED_HOOKS=pre:edit-write:gateguard-fact-force` | Friction reduction (separate, prior pass) |

**Carry-overs to Phase 3:** OQ1 (domain-purity refactor).
**Queued for Phase 2:** OQ2 actions (A unused-marker, B planned-comment, C delete).
**Closed permanently:** OQ3, OQ4, OQ5, OQ6.

---

Phase 1 Open Questions resolution complete. Ready for Phase 2 selective remediation.
