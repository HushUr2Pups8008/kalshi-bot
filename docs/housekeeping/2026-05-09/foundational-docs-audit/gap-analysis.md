# Gap Analysis — Cross-Cutting Rule Candidates (Phase 2)

> **[RESOLVED 2026-05-08 — guidance-consolidation Phase 8 Batch 4]** All four C-1..C-4 candidates are now live in rule files: C-1 in `risk_review.md` (fail-fast on security/observability paths), C-2 in `documentation_format.md` (cite behavior or function name, not line numbers), C-3 in `portability.md` (absolute UTC at write time), C-4 in `editing_safety.md` (verify references before recommending). Phase 2 status of "all candidates are proposals requiring user approval" no longer reflects reality. No further action.

> Read-only gap analysis under the `rules-distill` rubric. Identify principles that appear in 2+ skills/commits but are NOT codified in any `~/.claude/rules/` file (or are partially covered and worth tightening).
>
> Scope: `~/.claude/rules/` (10 files), recent project commits (`git log --oneline -50` covering cycle-14 through phase-3 closure), and skill descriptions visible in this session.
>
> **No rule files were created.** All candidates are proposals requiring user approval per `claude-md-improver` workflow.

---

## 1. Inventory

- **Rule files scanned:** 10 (`domain_constraints`, `documentation_format`, `editing_safety`, `git_workflow`, `planning`, `portability`, `release_versioning`, `risk_review`, `validation`, `windows_local`)
- **Project commits scanned:** 50 (most recent: `bf60cb2`; oldest: `0c2a133`)
- **Skill descriptions referenced:** ~250+ visible in session preamble (subset of ~/.claude installed skills)
- **Candidates raised:** 4 (C-1 to C-4) + 2 already-covered + 1 too-specific
- **Candidates with high confidence:** 3
- **Candidates with medium confidence:** 1

---

## 2. Candidate Summary Table

| # | Principle (one line) | Verdict | Target | Confidence | Severity | Effort |
|---|----|----|----|----|----|----|
| C-1 | Fail-fast on security/observability paths; do not swallow exceptions | New Section | `risk_review.md` | high | P2 | S |
| C-2 | Cite behavior or function name, not line numbers, in CLAUDE.md gotchas | Append | `documentation_format.md` | high | P3 | S |
| C-3 | Convert relative dates to absolute UTC when persisting to memory or logs | Append | `portability.md` | high | P2 | S |
| C-4 | Verify file/symbol references before recommending action based on them | New Section | `editing_safety.md` | medium | P2 | S |
| — | Use unified project debt log; no parallel logs | Already Covered | `kalshi-bot/CLAUDE.md` only | — | — | — |
| — | Match action scope to request scope | Already Covered | `editing_safety.md` partial | — | — | — |
| — | Cycle-NN charter authority for verdict criteria | Too Specific | project-level only | — | — | — |

---

## 3. Candidate Details

### C-1 — Fail-fast on security and observability paths (P2, S — high confidence)

**Principle:** When code is on a security-critical path or an observability write path, raise on failure rather than swallowing the exception. Silent failure on these paths produces an indistinguishable "passing" run that hides real defects.

**Evidence:**
- `ce70924 fix(security): kalshi RSA-PSS signing failures fail fast (PROFIT-SEC-001)` — kalshi REST signing previously caught and logged signing errors; the fix raises so callers can react.
- `1d0714c fix(observability): subreddit_selector SQLite write failures no longer silent (PROFIT-OBS-006)` — observability metric writes previously hit `try/except: pass`; the fix surfaces the error.
- Skill `silent-failure-hunter` (visible in session): "Review code for silent failures, swallowed errors, bad fallbacks, and missing error propagation." Confirms the cross-cutting nature.

**Violation risk:** A swallowed exception on a security path can mean a request signed with an expired key continues to be retried; a swallowed observability error means the metric never appears in dashboards but the run "succeeded." Both produce false-pass conditions that hide real defects from operators.

**Verdict:** New Section in `~/.claude/rules/risk_review.md` (the closest existing surface — risk-review already covers safety-gate preservation; adding a fail-fast trigger fits).

**Proposed Trigger/Action:**

```
- Trigger: when adding or modifying code on a security-critical path (auth, signing, secrets, money movement) or an observability write path (metrics, audit logs, decision traces).
  Action: prefer raising over swallowing exceptions; let the caller decide whether to recover. Silent failure on these paths produces false-pass conditions that hide real defects from operators.
```

**Why not just leave it in `silent-failure-hunter`?** That skill triggers when the user asks for a review. The pattern needs to fire *during* edit, not only during review. A rule under risk_review.md catches it at write-time.

---

### C-2 — Cite behavior or function name, not line numbers, in CLAUDE.md gotchas (P3, S — high confidence)

**Principle:** Gotchas in CLAUDE.md should reference behavior or function names, not line numbers. Line citations rot at the first refactor and silently mislead future readers.

**Evidence:**
- `bcadc7e`, `2ad8072`, `24df410` — three signal_analyzer refactor commits in one day. CLAUDE.md gotchas survived intact because they cite *behavior* ("LLM JSON extraction must use `JSONDecoder.raw_decode()`") rather than line numbers.
- One exception: project CLAUDE.md:68 cites "lines 27–31" of `governance/prompts.py` deliberately — those lines are load-bearing and warrant the precise citation. This is the right exception (sticky lines that must not be touched).
- Cross-cutting skills `comment-analyzer`, `code-explorer`, `verification-before-completion` all warn against stale citations.

**Violation risk:** A future contributor adds a gotcha with `signal_analyzer.py:1123-1126`. Six months later, after another refactor, the citation points to unrelated code; an agent following the pointer learns nothing or — worse — concludes the gotcha was about whatever code happens to live at that line now.

**Verdict:** Append to `~/.claude/rules/documentation_format.md`.

**Proposed Trigger/Action:**

```
- Trigger: when authoring or editing CLAUDE.md gotchas.
  Action: cite behavior or function/class names, not line numbers. Line citations rot at the next refactor. Exception: cite line numbers when the rule is "do not touch these specific lines" (e.g. governance/prompts.py:27-31 polarity block).
```

---

### C-3 — Convert relative dates to absolute UTC when persisting (P2, S — high confidence)

**Principle:** When persisting a date that originated as a relative reference ("Thursday", "next week", "in two days"), convert it to absolute UTC at write time. Relative anchors decay; the conversation that defined them does not persist with the storage.

**Evidence:**
- User-level instructions (auto-memory section): "Always convert relative dates in user messages to absolute dates when saving (e.g., 'Thursday' → '2026-03-05'), so the memory remains interpretable after time passes."
- Project commit pattern: every cycle-NN governance commit uses an absolute date (`2026-05-07-cycle-17c-charter-...`); none use "this week."
- `~/.claude/rules/portability.md` already covers UTC for internal logic and logs but does NOT address the relative-to-absolute conversion at the user-input boundary.

**Violation risk:** A debt log entry captured as "blocked until Thursday" is unrecoverable a week later — neither the agent nor the operator can tell which Thursday. Same for plan documents, status updates, follow-up reminders.

**Verdict:** Append to `~/.claude/rules/portability.md` (existing UTC-timestamp rule is the closest semantic surface).

**Proposed Trigger/Action:**

```
- Trigger: when persisting a date that originated as a relative reference (e.g. "Thursday", "next week", "in two days") to memory, logs, plans, or any durable surface.
  Action: convert it to absolute UTC at write time before persisting. Relative anchors lose meaning once the originating conversation rolls off.
```

---

### C-4 — Verify file/symbol references before recommending action based on them (P2, S — medium confidence)

**Principle:** Before recommending an action that depends on a specific file path, function name, or flag existing, verify it (Read, grep, or graph query). Don't act on stale memory or stale documentation alone.

**Evidence:**
- User-level instructions (auto-memory section): "If the user is about to act on your recommendation (not just asking about history), verify first. 'The memory says X exists' is not the same as 'X exists now.'"
- Today's audit: subagents repeatedly verified line citations against current source rather than trusting Phase 1's report — turned up three resolutions and zero new drifts.
- Skills `verification-before-completion`, `comment-analyzer`, `claude-md-improver` (this skill) all enforce this at different scopes.
- Cross-cuts with C-2 above (line citations rot) and with `editing_safety.md` ("inspect the relevant files and confirm where the target behavior is defined").

**Violation risk:** Agent recommends "edit `kalshi/old_client.py:45`" from stale memory; file was renamed three days ago. Operator follows the instruction, gets confused, loses time.

**Verdict:** New Section in `~/.claude/rules/editing_safety.md` — the file already covers "before editing" inspection; this extends to "before *recommending*" verification, a slightly different surface.

**Proposed Trigger/Action:**

```
- Trigger: before recommending an action that depends on a specific file path, function name, env var, or flag still existing.
  Action: verify the reference resolves now (Read, grep, or graph query). Memory and prior documentation can be stale; the live working tree is authoritative when the user is about to act.
```

**Confidence: medium.** Could be argued that this is already implicit in `editing_safety.md`'s existing "inspect the relevant files" trigger. Stated explicitly because the failure mode (recommending without verifying) is distinct from the failure mode the existing rule prevents (editing without verifying).

---

## 4. Already-Covered Patterns (No New Rule Needed)

### Unified project debt log
Project `CLAUDE.md:20` says: "This project's unified tracking system is `docs/profit_path_debt_log.md`; do not create parallel macOS, logging, S4.5, or architecture debt logs." This is project-specific. No need to lift to global rules.

### Match action scope to request scope
`~/.claude/rules/editing_safety.md` already covers this: "Trigger: when unrelated cleanup or refactoring appears possible. Action: do not include it unless the user explicitly requested it." Sufficient. The system prompt's broader scope-matching guidance ("A user approving an action once does NOT mean...") is a meta-rule and stays in the system prompt.

---

## 5. Too-Specific (Stay at Skill or Project Level)

### Cycle-NN charter authority for verdict criteria
The cycle-14 → cycle-17C commit chain follows a strict pattern: charter locks criteria → review against criteria → verdict consumption → next-cycle charter. This is project-specific governance methodology. Belongs in project CLAUDE.md or a project-local runbook, not in `~/.claude/rules/`.

---

## 6. Top-of-List Actions (P0 → P3)

1. **(P2, S) Approve C-1 fail-fast rule.** Strongest cross-cutting evidence (two recent fix commits + dedicated `silent-failure-hunter` skill). Add as a new Trigger/Action in `risk_review.md`.

2. **(P2, S) Approve C-3 absolute-date rule.** Strong evidence in user-level instructions and current project practice. Add as a new Trigger/Action in `portability.md`.

3. **(P3, S) Approve C-2 line-citation rule.** Today's signal_analyzer refactor demonstrated the principle by example. Add as a new Trigger/Action in `documentation_format.md`.

4. **(P2, S) Decide on C-4 verify-before-recommending.** Medium confidence — could be argued as already covered by `editing_safety.md`. User judgment call: tighten with the new section, or accept as covered.

5. **(P3, M, deferred) Review whether the cycle-NN governance pattern warrants a project-level runbook reference in CLAUDE.md.** Currently lives only in commit messages and `docs/governance/...` charters. Worth one-line CLAUDE.md pointer if the pattern continues. Not a global rule.

---

## 7. Three-Bullet Summary

- **Four candidate rules surfaced.** Three high-confidence (C-1 fail-fast, C-2 line-citation, C-3 absolute-dates), one medium (C-4 verify-before-recommend). All four are small additions to existing rule files — no new rule file proposed.
- **Two strongest candidates have explicit recent commit evidence.** C-1 is backed by `ce70924` (PROFIT-SEC-001) and `1d0714c` (PROFIT-OBS-006) — two fail-fast fixes in the last week. C-3 is reinforced by the auto-memory section in user-level instructions and consistent project practice (every cycle uses absolute dates).
- **No new rule files needed.** All proposed additions append to or extend existing rule files (`risk_review.md`, `portability.md`, `documentation_format.md`, `editing_safety.md`). Each is one Trigger/Action block, drafted ready-to-apply pending user approval.
