# Branch D fire procedure runbook

**Type:** operator runbook (Claude task per Implementation Contract §9 — operator decision input).
**Drafted:** 2026-05-05.
**Audience:** operator when one of the Branch D fire triggers in `docs/_archive/specs/2026-05-05-edge-004-lever-d-escalation-criteria-design.md` §2 fires.
**Companion:** `docs/_archive/specs/2026-05-05-edge-004-lever-d-escalation-criteria-design.md` §4 — high-level procedure outline expanded here into copy-pasteable operator commands.

## TL;DR

Branch D is **handoff**, not deploy. ~30 min wall-clock procedure: document trigger → tag → bump TLDR to v4 → open PROFIT-LLM-001 sizing audit → wait for Codex audit report → operator verdict.

## Pre-fire checklist

Before firing Branch D, confirm:

- [ ] Wave-2 Branch A 14 d window completed with 0 legal-niche PAPER_TRADE (per Lever-D §2.1 trigger)
- [ ] Wave-2 Branch C 14 d window completed with 0 PAPER_TRADE OR negative aggregate realized P&L (per §2.1 OR §2.2)
- [ ] OR operator §2.3 override conditions met (≥ 7 d Branch A + explicit attestation that experimentation cost exceeds marginal closure probability)
- [ ] Wave-3 deploy decision considered AND deferred (Wave-3 + Branch D are alternatives at the Branch-C-stall verdict point per `2026-05-05-wave-3-deploy-day-timing.md` §"Trigger conditions")

## Step 1 — Document the trigger

Open `docs/profit_path_debt_log.md` PROFIT-EDGE-004 entry. Add a "Branch D fire log" subsection:

```markdown
##### Branch D fire log — ${UTC_DATE}

**Trigger (per Lever-D escalation criteria spec §2):** [§2.1 / §2.2 / §2.3]

**Window data:**
- Branch A start: ${BRANCH_A_START_DATE}
- Branch A end: ${BRANCH_A_END_DATE} — legal-niche PAPER_TRADE count: 0
- Branch C deploy: ${BRANCH_C_DEPLOY_DATE} (or "not deployed")
- Branch C end: ${BRANCH_C_END_DATE} — PAPER_TRADE count: ${N}; aggregate realized P&L: ${P}

**Operator attestation:** intake-side levers exhausted; escalating to PROFIT-LLM-001 sizing per `2026-05-05-profit-llm-001-pre-sizing-scope-design.md`.

**Anti-trigger check:** [confirm none of Lever-D §5 anti-triggers apply]

**Sizing-audit ETA:** 5 days post-fire (Codex audit per PROFIT-LLM-001 spec §3 Step A).
```

Commit:

```bash
git add docs/profit_path_debt_log.md
git commit -m "$(cat <<'EOF'
docs(branch-d-fire): PROFIT-EDGE-004 Branch D fired — escalating to PROFIT-LLM-001 sizing

Wave-2 intake-side levers (Branch A passive observe + Branch C
legal-analyst onboard) both stalled. Branch D fire trigger per
§2.1 of docs/_archive/specs/2026-05-05-edge-004-lever-d-escalation-criteria-design.md.

Operator escalates per the spec § 4 procedure to PROFIT-LLM-001
sizing audit, Step A (prompt-template axis) per
2026-05-05-profit-llm-001-pre-sizing-scope-design.md § 3.
EOF
)"
```

## Step 2 — Tag the Branch D fire

```bash
git tag -a edge-004-branch-d-fired-$(date -u +%Y-%m-%d) -m "EDGE-004 Branch D fired per Lever-D escalation criteria spec §2.[1|2|3]"
git push origin edge-004-branch-d-fired-$(date -u +%Y-%m-%d)
```

This tag is the durable rollback / audit anchor for the Wave-2 → Branch D transition.

## Step 3 — Bump closure-path TLDR to v4

Create `docs/governance/edge-004-closure-path-tldr-v4.md` from v3 with the following deltas:

- **Status field:** "Branch D FIRED ${UTC_DATE}; PROFIT-LLM-001 sizing audit IN_PROGRESS"
- **Closure-path diagram:** annotate Branch A + Branch C as "FAILED 14 d window"; mark Branch D fire point
- **Lever map at a glance:** Wave-2 rows now show "stalled"; Wave-3 row "DEFERRED pending PROFIT-LLM-001 verdict"
- **New §"PROFIT-LLM-001 sizing audit status":** placeholder for audit progress + verdict insertion
- **Probability ranking:** drop Branch A / Branch C succeed estimates; bump Branch D outcomes (PROFIT-LLM-001 lands within 30 d / DEFERRED-CEILING)

Commit:

```bash
git add docs/governance/edge-004-closure-path-tldr-v4.md
git commit -m "docs(closure-path): TLDR v4 — Branch D fired"
```

(v3 stays in place per `2026-05-05-doc-index-audit.md` §B archiving recommendation; can be archived after PROFIT-LLM-001 sizing concludes.)

## Step 4 — Open PROFIT-LLM-001 sizing audit (delegate to Codex)

Per `2026-05-05-profit-llm-001-pre-sizing-scope-design.md` §3 Step A. Operator hands off to Codex with:

```
PROFIT-LLM-001 sizing audit, Step A (§2.1 prompt-template axis).

Per `docs/superpowers/specs/2026-05-05-profit-llm-001-pre-sizing-scope-design.md` §3 Step A.

Run audit against the post-Wave-1 OPPORTUNITY corpus (≥ 30 d) with these
4 prompt-template variants:
- A1 (current baseline)
- A2 (chain-of-thought guidance)
- A3 (calibration anchor)
- A4 (worked example ICL)

Audit output per spec §6: per-template magnitude!=none yield rate over the same corpus.

Decision criterion: > 1.5× yield over baseline without calibration drift = PROFIT-LLM-001 lands as prompt-template change.

Expected wall-clock: 3-5 days.

Output file: `docs/governance/${UTC_DATE}-profit-llm-001-sizing-report.md`.
```

## Step 5 — Wait for Codex audit report

Operator monitoring during the 5-day audit:

- [ ] Codex submits audit report
- [ ] Operator reads report (≤ 2,000 words target per spec §6)
- [ ] Operator verdicts which axis (A1 baseline; A2/A3/A4 alternative; or "Step A inadequate; escalate to Step B model swap")

## Step 6 — Operator verdict on PROFIT-LLM-001 axis

### Verdict 6a: prompt-template change adequate

If Step A audit returns yield > 1.5× baseline:

- Open new spec `docs/superpowers/specs/${UTC_DATE}-profit-llm-001-prompt-template-design.md` for the chosen variant
- Codex implements the prompt-template edit in `analysis/signal_analyzer.py`
- Wave 4 deploy timing: same shape as Wave-3 commit timing (UTC Mon-Thu 18:00-22:00; 14 d acceptance window)
- **EDGE-004 closes via PROFIT-LLM-001 prompt-template change** (Wave 4 acceptance criteria TBD per the new spec)

### Verdict 6b: Step A inadequate; escalate to Step B (model swap)

If Step A audit returns no variant > 1.5× yield:

- Operator delegates Step B to Codex per spec §3 Step B
- Step B audit ~1 week (model swap requires governance shadow-soak before promotion)
- Loop back to Step 5 with Step B audit report

### Verdict 6c: All 4 axes inadequate; escalate to P4-GATE Appendix A

If Steps A + B + C + D all return inadequate:

- Per `2026-05-05-profit-llm-001-pre-sizing-scope-design.md` §3, escalate to P4-GATE Appendix A sizing per `2026-05-05-p4-gate-appendix-a-pre-sizing-scope-design.md`
- New procedure: same shape as this runbook but per the P4-GATE Appendix A spec
- Bump TLDR to v5 reflecting escalation

### Verdict 6d: All P4-GATE Appendix A axes also inadequate

- **EDGE-004 closes DEFERRED-CEILING** per `edge-004-closure-path-tldr-v3.md` honest read §"What 'closure' looks like"
- Operator strategic decision: live-trade at 1 % conversion (not viable), pivot venue (out of scope), reset strategic frame
- Bump TLDR to vN reflecting closure
- Final commit: `git tag -a edge-004-deferred-ceiling-$(date -u +%Y-%m-%d)`

## What NOT to do during Branch D fire

Per `docs/_archive/specs/2026-05-05-edge-004-lever-d-escalation-criteria-design.md` §6:

- **DO NOT roll back Wave-1 / Wave-2 deploys.** Branch D is escalation, not regression. The base-stack improvements all stand.
- **DO NOT skip the §3 PROFIT-LLM-001 audit.** Even if operator suspects DEFERRED-CEILING, the audit produces evidence that justifies (or refutes) that suspicion.
- **DO NOT compress the audit timeline.** Step A's 3-5 day cost includes empirical sizing; rushing produces wrong verdicts.

## Operator-facing decision tree summary

```
Branch D fire trigger met?
    ├── NO (anti-triggers per Lever-D §5) → don't fire; document why; consider Wave-3 instead
    └── YES → continue
            │
            ▼
        Step 1: document trigger in debt log (commit)
            │
            ▼
        Step 2: tag the fire (git tag)
            │
            ▼
        Step 3: bump TLDR to v4 (commit)
            │
            ▼
        Step 4: open PROFIT-LLM-001 sizing audit (delegate to Codex)
            │
            ▼
        Step 5: wait 3-5 days; receive audit report
            │
            ▼
        Step 6: operator verdict
            ├── 6a: prompt-template adequate → Wave 4 deploy; EDGE-004 closes
            ├── 6b: Step A inadequate → Step B (model swap); loop Step 5
            ├── 6c: all 4 axes inadequate → escalate to P4-GATE Appendix A
            └── 6d: P4-GATE inadequate → EDGE-004 DEFERRED-CEILING
```

## Out of scope

- **PROFIT-LLM-001 implementation specs.** Drafted post-axis-verdict per `2026-05-05-profit-llm-001-pre-sizing-scope-design.md` §3.
- **P4-GATE Appendix A implementation specs.** Drafted post-PROFIT-LLM-001-inadequate per `2026-05-05-p4-gate-appendix-a-pre-sizing-scope-design.md`.
- **Live-trade pivot procedure.** Outside this runbook; outside EDGE-004 scope.
- **Strategic frame reset procedure.** Operator-only; outside bot-codebase scope.

## Cross-links

- `docs/_archive/specs/2026-05-05-edge-004-lever-d-escalation-criteria-design.md` — Branch D triggers + handoff structure (ARCHIVED Stream G R35)
- `docs/superpowers/specs/2026-05-05-profit-llm-001-pre-sizing-scope-design.md` — first-handoff sizing
- `docs/superpowers/specs/2026-05-05-p4-gate-appendix-a-pre-sizing-scope-design.md` — second-handoff sizing
- `docs/governance/edge-004-closure-path-tldr-v3.md` — closure-path-TLDR (v4 fires post-Branch-D)
- `docs/profit_path_debt_log.md` PROFIT-EDGE-004 entry — receives Branch-D-fire log
- `docs/profit_path_debt_log.md` PROFIT-LLM-001 entry — receives audit report cross-link
