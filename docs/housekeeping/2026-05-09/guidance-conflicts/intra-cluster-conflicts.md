# Intra-Cluster Conflicts — Phase 6 Agent 1

**Generated:** 2026-05-09
**Conflicts surfaced:** 30
**Severity scale:** HIGH (direct behavior risk) / MEDIUM (maintenance or clarity risk) / LOW (informational)

---

## Severity Rubric

| Rating | Definition |
|--------|------------|
| HIGH | A conflict that could cause an agent to take the wrong action — a direct contradiction between two pieces of enforced guidance, or a code comment that contradicts a load-bearing CLAUDE.md gotcha |
| MEDIUM | A conflict that creates maintenance burden or ambiguity — duplicated content that will diverge, stale audit findings that contradict the live tree, or wording that implies a wrong mechanism without causing immediate bad behavior |
| LOW | A conflict that is cosmetic, informational, or limited to cross-reference rot — broken links, imprecise labels, stale "FUTURE" tags, or ambiguous path notation |

Trust hierarchy for authority: Layer 1 = `~/.claude/rules/*.md` | Layer 2 = project CLAUDE.md Critical Gotchas | Layer 3 = project CLAUDE.md Working Style | Layer 4 = `~/.claude/CLAUDE.md` | Layer 5 = auto-memory entries | Layer 6 = `docs/superpowers/specs/` | Layer 7 = scratch/loose `.md`

---

## Distribution

| Type | Label | Count |
|------|-------|-------|
| A | Direct contradiction | 7 |
| B | Stale fact / outdated reference | 9 |
| C | Format violation | 3 |
| D | Duplicate content | 5 |
| E | Scope conflict | 2 |
| F | Tracking-system conflict | 0 |
| G | Cross-reference rot | 4 |

---

## Conflicts

### HIGH (1–9)

---

**#1 — [Type A] `windows_local.md` NSSM rule vs PLATFORMS.md Windows-deprecated declaration**

- **Side A (Layer 1 — enforced rule):** `~/.claude/rules/windows_local.md` line 17–18:
  > "Trigger: when installing packages for an NSSM-managed service.
  > Action: install them into the service virtual environment and verify imports there before restarting the service."

- **Side B (Layer 7 — project runbook):** `/Users/jacobparenti/vscode/kalshi-bot/PLATFORMS.md` line 43:
  > "Windows runtime is deprecated. The NSSM service setup (`setup_daily_task.ps1`) is preserved for reference but the primary runtime is now macOS."

- **Trust winner:** PLATFORMS.md documents factual runtime state; `windows_local.md` is a global enforcement rule that silently applies to this project. An agent following the NSSM rule for a deprecated service wastes time and may restart a service that should not exist.

- **Severity: HIGH.** Layer 1 rule actively directs work toward a deprecated component. The NSSM service no longer runs on the primary platform; the rule has no safe fallback for detecting this.

---

**#2 — [Type C] Project CLAUDE.md Release Versioning section uses Trigger/Action format inside a CLAUDE.md file**

- **Side A (Layer 1 — enforced rule):** `~/.claude/rules/documentation_format.md` lines 6–7:
  > "Trigger: when authoring or editing CLAUDE.md gotchas, hard-won-knowledge sections, or hidden-constraint context.
  > Action: use bold-inline narrative format. The WHY is load-bearing — preserve the full explanation."

  And lines 12–13:
  > "Trigger: when a single file contains both mechanical rules and narrative gotchas.
  > Action: split it into two files. Do not mix formats within one file."

- **Side B (Layer 2/3 — project CLAUDE.md):** `/Users/jacobparenti/vscode/kalshi-bot/CLAUDE.md` lines 39–46 contain:
  > "- **Trigger: when bumping VERSION.** Stage `VERSION` first; the hook handles README. Add a `CHANGELOG.md` entry in the same commit..."

  The Release Versioning section uses Trigger/Action format inside CLAUDE.md, mixing it with bold-inline narrative gotchas below.

- **Trust winner:** `documentation_format.md` (Layer 1) is the authority on format. The Release Versioning section violates the rule that CLAUDE.md must not mix formats. The rule also says mixed-format files must be split.

- **Severity: HIGH.** Layer 1 directly mandates splitting; project CLAUDE.md is the file that violates it. An agent adding a new CLAUDE.md entry and following the "split mixed-format" rule might move the Release Versioning block out of CLAUDE.md — splitting incorrectly — or add a Trigger/Action entry where it should be narrative.

---

**#3 — [Type A] `documentation_format.md` "split mixed-format files" rule contradicts project CLAUDE.md's existing mixed-format structure**

- **Side A (Layer 1 — enforced rule):** `~/.claude/rules/documentation_format.md` line 12–13:
  > "Trigger: when a single file contains both mechanical rules and narrative gotchas.
  > Action: split it into two files. Do not mix formats within one file."

- **Side B (Layer 2 — project CLAUDE.md):** `/Users/jacobparenti/vscode/kalshi-bot/CLAUDE.md` contains: (a) Working Style bullets (Layer 3 format), (b) Release Versioning with Trigger/Action bullets (Layer 1 format), and (c) Critical Gotchas in bold-inline narrative (Layer 2 format) — three distinct formats co-existing in one file.

- **Trust winner:** `documentation_format.md` says split. Project CLAUDE.md violates that rule. However, the same `documentation_format.md` file also says (lines 15–16): "Trigger: when an audit, agent, or reviewer flags format inconsistency between rule files (Trigger/Action) and CLAUDE.md gotchas (narrative). Action: reject the finding." This creates an internal self-contradiction: the file says split mixed-format files AND says to reject findings about CLAUDE.md format divergence.

- **Severity: HIGH.** The self-contradiction in `documentation_format.md` means two clauses of the same Layer 1 file point in opposite directions. An agent enforcing the "split" rule on project CLAUDE.md would be partially correct and partially wrong depending on which clause it reads first.

---

**#4 — [Type A] `analysis/__init__.py:19` comment says "$50 hard cap" — CLAUDE.md gotcha says cap is dynamic**

- **Side A (Layer 2 — project CLAUDE.md Critical Gotcha):** `/Users/jacobparenti/vscode/kalshi-bot/CLAUDE.md` line 79:
  > "**Bet size is dynamic** via `cfg.dynamic_max_bet(notional)`. Never hardcode a dollar cap — pass the current notional bankroll."

- **Side B (Layer 7 — inline code comment):** `analysis/__init__.py` line 19 (per Phase 2 comment-health audit):
  > "`capped_dollars: float  # after $50 hard cap`"

- **Trust winner:** Layer 2 CLAUDE.md gotcha is authoritative. The $50 figure is wrong — the cap is a bankroll percentage computed by `cfg.dynamic_max_bet(notional)`.

- **Severity: HIGH.** An agent reasoning about bet sizing from the `__init__.py` type annotation will calibrate a fixed $50 cap and never query `dynamic_max_bet`. The CLAUDE.md gotcha explicitly says "never hardcode a dollar cap."

---

**#5 — [Type A] `analysis/evidence_scorer.py` docstring says "word trigrams" — `_NGRAM_SIZE = 2` means bigrams**

- **Side A (Layer 7 — inline code comment):** `analysis/evidence_scorer.py` line 48 docstring (per Phase 2 comment-health audit):
  > "`"""Jaccard similarity over word trigrams …"""`"

- **Side B (same file — implementation):** `_NGRAM_SIZE = 2` — the function computes bigrams (n=2), not trigrams (n=3).

- **Trust winner:** Implementation is ground truth. The docstring is wrong by one n-gram size.

- **Severity: HIGH.** Any caller or agent reasoning about the dedup threshold (`NGRAM_OVERLAP_THRESHOLD`) will calibrate sensitivity for the wrong n-gram size. Trigrams are sparser than bigrams; a developer tuning the threshold based on this docstring will arrive at a wrong value.

---

**#6 — [Type A] `analysis/kelly.py:159` says "Rounds down to stay within budget" — `max(1, int(...))` can exceed budget**

- **Side A (Layer 7 — inline code comment):** `analysis/kelly.py` near line 159 (per Phase 2 comment-health audit):
  > `"Rounds down to stay within budget."`

- **Side B (same file — implementation):** `max(1, int(...))` — when a single contract costs more than the budget, this returns 1, which exceeds the budget rather than rounding down to zero.

- **Trust winner:** Implementation is ground truth. The docstring describes the wrong behavior for the edge case.

- **Severity: HIGH.** Code explicitly says it "stays within budget" but can exceed it. An agent or caller relying on this invariant for position sizing will size incorrectly for illiquid markets where a single contract exceeds the Kelly-computed dollar limit.

---

**#7 — [Type A] `analysis/fade_signal.py:10-13` says "WebSocket-based replacement" — both strategies run in parallel**

- **Side A (Layer 7 — inline code comment):** `analysis/fade_signal.py` lines 10–13 (per Phase 2 comment-health audit):
  > `"2. Price fade … — WebSocket-based replacement. … no Twitter dependency required."`

- **Side B (project architecture — `main.py`):** Both fade strategies (tweet fade and price fade) run simultaneously in `main.py` lines 876–1008. "Replacement" is factually wrong.

- **Trust winner:** `main.py` runtime architecture is ground truth. The fade_signal comment falsely implies tweet fade was retired.

- **Severity: HIGH.** An agent reading this comment may conclude tweet fade is dead and remove it or skip wiring it up. "No Twitter dependency required" reinforces the false impression. Both feeds are live.

---

**#8 — [Type B] `general-quality.md` G-7 describes `/analysis` purity rule as "silent on stats" — `domain_constraints.md` already says "no stats/aggregation modules"**

- **Side A (Layer 7 — audit document):** `/Users/jacobparenti/vscode/kalshi-bot/docs/housekeeping/2026-05-09/foundational-docs-audit/general-quality.md` lines 101–105:
  > "The current `domain_constraints.md` rule says `/analysis: keep it pure; do not add API calls or trade execution.` Stats are neither, but the move suggests the team is treating `/analysis` more strictly than the rule's literal wording (only 'API calls or trade execution' are forbidden — stats *are* allowed by the current rule)."

- **Side B (Layer 1 — live rule file):** `~/.claude/rules/domain_constraints.md` line 3–4:
  > "Trigger: when changing `/analysis`.
  > Action: keep it pure analysis only — no stats/aggregation modules, no API calls, no trade execution."

- **Trust winner:** Layer 1 live rule is authoritative. The rule already says "no stats/aggregation modules." The G-7 finding is based on stale wording — the rule was updated before or concurrent with the audit.

- **Severity: HIGH.** An agent reading G-7 and acting on "only API calls or trade execution are forbidden" would incorrectly conclude stats modules are allowed in `/analysis`. The live rule forbids them explicitly.

---

**#9 — [Type B] `feedback_edge_priority_over_deploy_safety.md` embeds point-in-time P&L metrics without staleness caveat; tags a now-existing doc as "FUTURE"**

- **Side A (Layer 5 — auto-memory entry):** `~/.claude/projects/-Users-jacobparenti-vscode-kalshi-bot/memory/feedback_edge_priority_over_deploy_safety.md`:
  > "As of 2026-05-06: 3 lifetime paper trades, 0 wins, -$7.50 P&L, 1-source dependence (VitalLaw), 89 % of OBS-003 SKIPPED records show `edge +0.0000`"

  And:
  > "`docs/governance/edge-replay-cycle12-report.md` (FUTURE; Codex's Cycle-12 deliverable)"

- **Side B (current inventory):** The guidance-discovery inventory (Phase 5 Agent 1) confirms `docs/governance/edge-replay-cycle12-report.md` exists on disk as of 2026-05-08. The point-in-time metrics are 3 days old with no staleness label.

- **Trust winner:** Current working tree is authoritative. The memory entry presents stale metrics as present-tense facts and marks an existing file as future.

- **Severity: HIGH.** An agent citing these metrics in a strategy or budget discussion will report wrong numbers. The "FUTURE" tag may cause an agent to not read a document that exists and contains relevant analysis.

---

### MEDIUM (10–18)

---

**#10 — [Type D] Working Style section near-verbatim copy between both CLAUDE.md files**

- **Side A (Layer 4 — global CLAUDE.md):** `~/.claude/CLAUDE.md` lines 3–9 (five Working Style bullets).

- **Side B (Layer 3 — project CLAUDE.md):** `/Users/jacobparenti/vscode/kalshi-bot/CLAUDE.md` lines 3–10 (same five bullets plus one project-unique bullet at line 5).

- **Trust winner:** No contradiction — the project file is additive. Risk is silent divergence if global is updated.

- **Severity: MEDIUM.** Four of five bullets are identical. If the global Working Style changes, the project copy will silently drift. The project-unique bullet ("Understand and honor the intent...") is the only non-redundant content.

---

**#11 — [Type D] Bug-Fixing Preference section identical in both CLAUDE.md files**

- **Side A (Layer 4):** `~/.claude/CLAUDE.md` lines 11–14 (two Bug-Fixing bullets).

- **Side B (Layer 3):** `/Users/jacobparenti/vscode/kalshi-bot/CLAUDE.md` lines 12–15 — exact verbatim copy of both bullets.

- **Severity: MEDIUM.** Exact duplicate with zero project-unique content. Silent divergence on global update.

---

**#12 — [Type D] Continuous Improvement near-identical in both CLAUDE.md files**

- **Side A (Layer 4):** `~/.claude/CLAUDE.md` line 18:
  > "After repeated correction on the same pattern, capture the lesson in the project's preferred tracking system if one exists."

- **Side B (Layer 3):** `/Users/jacobparenti/vscode/kalshi-bot/CLAUDE.md` lines 19–20: same bullet plus one project-unique line:
  > "This project's unified tracking system is `docs/profit_path_debt_log.md`; do not create parallel macOS, logging, S4.5, or architecture debt logs."

- **Severity: MEDIUM.** First bullet is verbatim copy. Second bullet is project-unique and valuable. The pattern is the same as #10 and #11 — only the project-additive line justifies the section's presence.

---

**#13 — [Type D] Rule cross-references duplicated in both CLAUDE.md files**

- **Side A (Layer 4):** `~/.claude/CLAUDE.md` lines 27–29:
  > "See `~/.claude/rules/planning.md` for planning rules.
  > See `~/.claude/rules/validation.md` for validation rules.
  > See `~/.claude/rules/git_workflow.md` for git workflow rules."

- **Side B (Layer 3):** `/Users/jacobparenti/vscode/kalshi-bot/CLAUDE.md` lines 22–25: same three lines plus one additional line for `documentation_format.md`.

- **Severity: MEDIUM.** Three of four cross-references are verbatim duplicates. Two places to update when a rule file is added or renamed. The `documentation_format.md` reference at project CLAUDE.md line 25 is project-unique.

---

**#14 — [Type E] `windows_local.md` `E:` drive rule is a deprecated-Windows artifact in an active global rule file**

- **Side A (Layer 1 — active global rule):** `~/.claude/rules/windows_local.md` lines 11–12:
  > "Trigger: when writing files on the `E:` drive for this project.
  > Action: verify the file is readable immediately after the write."

- **Side B (Layer 7 — project runbook):** `PLATFORMS.md` line 43: Windows runtime deprecated; macOS is primary.

- **Trust winner:** PLATFORMS.md documents runtime state. The E: drive does not exist on macOS. The rule applies to a platform that is no longer the primary runtime.

- **Severity: MEDIUM.** The rule is inert on macOS (no E: drive to trigger on), but it signals to any agent that the E: drive is a live concern when it is not. Adds confusion without adding protection.

---

**#15 — [Type B] `analysis/signal_analyzer.py:547-552` "Revert if 12h re-run shows no drop..." — P0.4 decision already made**

- **Side A (Layer 7 — inline code comment):** `analysis/signal_analyzer.py` lines 547–552 (per Phase 2 comment-health audit):
  > "v0.29.48 (P0-GATE / P0.4 experiment) … Revert if the 12h re-run … shows no drop from the 98.99% baseline anchor rate."

- **Side B (project ROADMAP):** P0.4 experiment committed 2026-04-24; ROADMAP records it as closed. The revert condition is a pending decision that has already been made.

- **Severity: MEDIUM.** The comment implies a pending decision when the decision is closed. An agent reading this may initiate an unnecessary revert or flag a false open action item.

---

**#16 — [Type B] `analysis/signal_analyzer.py:37` labels function as "Cycle-15B diagnostics" — current cycle is 17C**

- **Side A (Layer 7 — inline code comment):** `analysis/signal_analyzer.py` line 37 (per Phase 2 comment-health audit):
  > `"""Optional debug-only extraction trace hook for Cycle-15B diagnostics."""`

- **Side B (governance context):** Current cycle is 17C per multiple governance documents.

- **Severity: MEDIUM.** Two-cycle-stale label makes the function look like abandoned diagnostic scaffolding. An agent performing cleanup may remove it as dead code from a closed cycle.

---

**#17 — [Type B] `gap-analysis.md` lists C-1 through C-4 as "proposals requiring user approval" — all four are already implemented in live rule files**

- **Side A (Layer 7 — audit document):** `/Users/jacobparenti/vscode/kalshi-bot/docs/housekeeping/2026-05-09/foundational-docs-audit/gap-analysis.md` header:
  > "No rule files were created. All candidates are proposals requiring user approval per `claude-md-improver` workflow."

  And per-candidate labels: "Proposed Trigger/Action" for C-1 through C-4.

- **Side B (Layer 1 — live rule files):**
  - C-1 (fail-fast rule): present in `~/.claude/rules/risk_review.md` lines 15–16.
  - C-2 (cite behavior not line numbers): present in `~/.claude/rules/documentation_format.md` lines 24–25.
  - C-3 (absolute UTC dates): present in `~/.claude/rules/portability.md` lines 15–16.
  - C-4 (verify before recommending): present in `~/.claude/rules/editing_safety.md` lines 18–19.

- **Severity: MEDIUM.** An agent reading gap-analysis.md would spend time proposing rules that already exist. A Phase 7 resolver reading this doc would incorrectly treat these as open tasks.

---

**#18 — [Type D] Two AGENTS.md files differ on step 4 of the precedence chain**

- **Side A (Layer 1 — global AGENTS.md):** `~/.claude/AGENTS.md` line 13:
  > "4. project-root `AGENTS.md` (if present) and project-local rules: explicit project-specific additions or overrides"

- **Side B (Layer 1 — project AGENTS.md):** `/Users/jacobparenti/vscode/kalshi-bot/AGENTS.md` line 13:
  > "4. `project/AGENTS.md` and project-local rules: explicit project-specific additions or overrides"

- **Trust winner:** Global AGENTS.md describes "project-root `AGENTS.md`" which is accurate (the file lives at repo root). Project AGENTS.md says "`project/AGENTS.md`" which names a non-existent `project/` directory. Global wording is more accurate.

- **Severity: MEDIUM.** Precedence chain is a navigation contract. Agents reading the project AGENTS.md will look for a `project/` directory that does not exist and may fail to locate the correct file.

---

### LOW (19–30)

---

**#19 — [Type B] `reference-integrity.md` and `general-quality.md` report RTK.md:29 as BROKEN/UNRESOLVED — actual file shows fix was applied**

- **Side A (Layer 7 — audit documents):** `reference-integrity.md` row 43: "BROKEN — 'Refer to CLAUDE.md for full command reference.' Global CLAUDE.md has no RTK command section. Phase 1 SR-1, still open." And `general-quality.md` RTK.md assessment: "SR-1 carry-over."

- **Side B (Layer 4 — live RTK.md):** `~/.claude/RTK.md` line 29:
  > "Run `rtk --help` for the full command reference."

  Not the circular reference. Fix was applied; audit documents not updated.

- **Severity: LOW.** Stale audit finding. No active guidance contradiction — the live file is correct. A Phase 7 agent acting on this finding would spend time "fixing" something already fixed.

---

**#20 — [Type G] `~/.claude/AGENTS.md` step 4 references `project/AGENTS.md` path notation — no `project/` directory exists**

- **Side A (Layer 1 — global AGENTS.md):** `~/.claude/AGENTS.md` line 13: "project-root `AGENTS.md`" — this wording is correct (confirmed by #18 above; global file uses the right label).

  Wait: re-reading — the global file says "project-root `AGENTS.md`" (correct). The PROJECT file says "`project/AGENTS.md`" (wrong). This is already captured in #18. What reference-integrity.md F-3 calls out separately is the ambiguity of the notation itself.

- **Side B (reference-integrity.md F-3):** Row 44: "path/label AMBIGUOUS — no `project/` dir; project-level lives at repo root."

- **Severity: LOW.** Informational — the notation is ambiguous but the correct file is findable at repo root.

---

**#21 — [Type G] `domain_constraints.md` cites `cycle-17C` label without file path**

- **Side A (Layer 1 — rule file):** `~/.claude/rules/domain_constraints.md` line 19:
  > "preserve the anchor_rate polarity block (lines 27–31 per cycle-17C; see `docs/governance/2026-05-07-cycle-17c-charter-single-variable-redesign.md`)"

  Wait — reading the actual file, it DOES include the path: "per cycle-17C; see `docs/governance/2026-05-07-cycle-17c-charter-single-variable-redesign.md`". Reference-integrity.md F-4 says "label/ID AMBIGUOUS — no path given" but the file now has the path. The audit is stale on this point too.

- **Severity: LOW.** Stale finding — the path is present in the live file. No active conflict.

---

**#22 — [Type G] `analysis/regime_classifier.py:178` references `lessons.md` — file does not exist**

- **Side A (Layer 7 — inline code comment):** `analysis/regime_classifier.py` near line 178 (per Phase 2 comment-health audit):
  > "`series_ticker can be empty … — see lessons.md`"

- **Side B (working tree):** `lessons.md` confirmed not to exist anywhere in the repository.

- **Severity: LOW.** Dead cross-reference in a code comment. No guidance conflict; the implementation is not affected. An agent following the link will find nothing.

---

**#23 — [Type B] `feedback_edge_priority_over_deploy_safety.md` tags `edge-replay-cycle12-report.md` as "(FUTURE; Codex's Cycle-12 deliverable)"**

- **Side A (Layer 5 — auto-memory entry):** `feedback_edge_priority_over_deploy_safety.md` line 94:
  > "`docs/governance/edge-replay-cycle12-report.md` (FUTURE; Codex's Cycle-12 deliverable)"

- **Side B (current inventory):** Guidance-discovery inventory confirms `docs/governance/edge-replay-cycle12-report.md` exists on disk as of 2026-05-08.

- **Severity: LOW.** Informational stale tag. The document exists; an agent following the link will succeed. The "FUTURE" label is misleading but not harmful.

---

**#24 — [Type D] `~/.claude/project/AGENTS.md` architecture boundaries overlap with `domain_constraints.md`**

- **Side A (Layer 1 — rule file):** `~/.claude/rules/domain_constraints.md` defines per-directory boundaries with Trigger/Action rules for `/feeds`, `/analysis`, `/tasks`, `/trading`, `/governance`.

- **Side B (Layer 1 — `~/.claude/project/AGENTS.md`):** Lines 6–10:
  > "- `/feeds`: data ingestion only
  > - `/analysis`: transforms input into probabilities
  > - `/tasks`: workflow orchestration and scheduling
  > - `/trading`: decision-making and execution logic
  > - `/kalshi`: API interaction layer"

  And line 16: "See `rules/domain_constraints.md` for execution and safety constraints."

- **Trust winner:** `domain_constraints.md` has the enforcement rules. `~/.claude/project/AGENTS.md` has the summary descriptions. The descriptions are accurate and the cross-reference is correct. This is redundancy, not contradiction — but the descriptions could drift from the rules.

- **Severity: LOW.** The summary in `~/.claude/project/AGENTS.md` omits `/governance` (which has a domain constraint) and includes `/kalshi` (which does not). Minor gap; no active contradiction.

---

**#25 — [Type C] Project CLAUDE.md gotcha cites `executor.py:218` as a line number in a non-sticky context**

- **Side A (Layer 1 — documentation_format.md):** `~/.claude/rules/documentation_format.md` lines 24–25:
  > "Trigger: when authoring or editing CLAUDE.md gotchas.
  > Action: cite behavior or function/class names, not line numbers. Line citations rot at the next refactor. Exception: cite line numbers when the rule is 'do not touch these specific lines'"

- **Side B (Layer 2 — project CLAUDE.md):** `/Users/jacobparenti/vscode/kalshi-bot/CLAUDE.md` line 63:
  > "See self-documenting comment at `executor.py:218`."

  This is a "see also" navigation hint, not a "do not touch" citation. The rule says to prefer behavior/function references over line numbers in gotchas.

- **Severity: LOW.** Not a "do not touch" citation so the exception does not apply. Low rot risk because executors change less than analyzers; but the line number could move in a future refactor.

---

**#26 — [Type C] `~/.claude/CLAUDE.md` Tooling Preferences section uses hybrid bullet/sub-bullet format**

- **Side A (Layer 1 — documentation_format.md):** `~/.claude/rules/documentation_format.md` lines 18–19:
  > "Trigger: when adding a Working Style or Conventions section to CLAUDE.md.
  > Action: prefer narrative if the content explains preferences; prefer Trigger/Action if the content is a checklist of mechanical actions."

- **Side B (Layer 4 — global CLAUDE.md):** `~/.claude/CLAUDE.md` lines 22–25 — Tooling Preferences uses a hybrid: narrative lead ("prefer X over Y") with inline parenthetical actions, but not pure Trigger/Action and not pure narrative.

- **Severity: LOW.** The format is readable and unambiguous. Not a pure violation — `documentation_format.md` offers a preference, not a hard rule, for Working Style sections.

---

**#27 — [Type G] README:243-258 has 9 dead links to `transfer/macbook_handoff_2026-05-01/`**

- **Side A (Layer 7 — README):** `README.md` lines 243–258 contain 9 links to `transfer/macbook_handoff_2026-05-01/` paths.

- **Side B (working tree):** Directory was removed from git history 2026-05-02. Links are dead. README:219 explains the removal and names the recovery git tag (`pre-filter-repo-2026-05-02`).

- **Severity: LOW.** An agent grepping links will find dead ones. An agent reading the prose will understand the removal. Low confusion risk because the surrounding text explains the situation.

---

**#28 — [Type B] `windows_local.md` `E:` drive rule: E: drive is a deprecated Windows artifact (see also #14)**

- **Side A (Layer 1):** `~/.claude/rules/windows_local.md` lines 11–12: E: drive write-then-verify rule.

- **Side B (Layer 7):** PLATFORMS.md: Windows runtime deprecated.

- This conflict is partially captured in #14 (scope conflict). As a pure staleness finding: the E: drive rule references a specific Windows drive letter that does not exist in the macOS environment and cannot be triggered.

- **Severity: LOW.** Rule is inert — cannot trigger on macOS. Already addressed more fully under #14.

---

**#29 — [Type B] `feedback_market_implied_baseline.md` cites git commit SHA `c913ffd` — fragile reference**

- **Side A (Layer 5 — auto-memory entry):** `~/.claude/projects/-Users-jacobparenti-vscode-kalshi-bot/memory/feedback_market_implied_baseline.md`:
  > "Codex's Cycle-16E forensics (`c913ffd`) caught this..."

- **Side B (guidance principle):** `documentation_format.md` (Layer 1) says cite behavior or function/class names, not line numbers. Commit SHAs are similarly fragile — they become ambiguous after rebases, force-pushes, or history rewrites. There is no explicit rule against SHA citations in memory entries, but the spirit of the line-citation rule applies.

- **Severity: LOW.** Commit SHAs are stable in this repo (no force-push to main observed). Risk materializes only if history is rewritten. Informational finding.

---

**#30 — [Type B] `general-quality.md` G-6 says "`documentation_format.md` is new since Phase 1; not yet referenced from CLAUDE.md"**

- **Side A (Layer 7 — audit document):** `general-quality.md` line 125:
  > "`documentation_format.md` is new since Phase 1; not yet referenced from CLAUDE.md"

- **Side B (Layer 3 — project CLAUDE.md):** `/Users/jacobparenti/vscode/kalshi-bot/CLAUDE.md` line 25:
  > "See `~/.claude/rules/documentation_format.md` for documentation format rules."

  The cross-reference exists. The audit finding is stale.

- **Severity: LOW.** Stale finding — the cross-reference was added. A Phase 7 agent acting on G-6 would add a duplicate reference.

---

## Conflicts Seen But Cut (beyond cap of 30)

| # | Type | Summary | Reason cut |
|---|------|---------|------------|
| 31 | D | `~/.claude/project/AGENTS.md` omits `/governance` boundary but `domain_constraints.md` has it | Partial overlap with #24; low severity |
| 32 | B | Phase 1 M-4 carry-over: `_normalize_pem()` duplication not mentioned in CLAUDE.md gotcha | Already noted in Phase 1/2 audit chain; no new finding |
| 33 | B | Phase 1 D-2 carry-over: CLAUDE.md names `resolve_market()` but transaction logic is in `_resolve_market_sync()` | Low severity per prior audits; cosmetic naming only |
| 34 | B | Phase 1 D-3 carry-over: CLAUDE.md says "query all open trades" implying DB; implementation uses in-memory Portfolio | Code has self-documenting comment at `executor.py:218`; risk is low |
| 35 | B | Infrastructure gotcha says "Python 3.14 on Windows requires aiohttp>=3.10.0" — Windows runtime deprecated | Technically stale but harmless; gotcha is accurate if Windows is used |
| 36 | B | "Concurrent Mac + Windows instances trigger Reddit 403s" — Windows runtime deprecated; half the scenario no longer applies | Informational; the Mac-only caution remains valid |

