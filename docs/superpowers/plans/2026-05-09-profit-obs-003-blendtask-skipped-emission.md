# PROFIT-OBS-003 — BlendTask SKIPPED Emission Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Emit a SKIPPED record from `BlendTask` whenever the blocked-reason early-return path fires, so `OPPORTUNITY = SKIPPED + PAPER_TRADE ± in-flight` becomes the single trade-log accounting invariant for both BlendTask-blocked and executor-rejected candidates.

**Architecture:** Add a private `_emit_skipped()` helper to `BlendTask` that composes an executor-compatible SKIPPED payload from `(blend_result, readiness, fast_lane_result)` and writes it via the existing injected logger before the blocked-reason early return at `tasks/blend_task.py:204`. Tests are pre-staged as `xfail(strict=True)` in `tests/test_blend_task.py`; implementation flips them to passing and the `xfail` markers come off.

**Tech Stack:** Python 3.14, asyncio, pytest + pytest-asyncio, `utils.logger.write_trade_log_async`, `utils.logger.trade_log`, dataclasses-frozen `BlendResult` / `ReadinessDecision` / `SignalAnalysis`.

---

## Soak-window gate (BLOCKING — read before Task 0)

Spec `docs/superpowers/specs/2026-05-03-obs-003-blendtask-skipped-emission-design.md` §10 says: do not implement before PROFIT-PHASE2-001 organic close (target `2026-05-09`) or hard ceiling (`2026-05-16`). Today is `2026-05-09`. Debt-log `PROFIT-PHASE2-001` priority field reads `IN_PROGRESS — soak clock running, no operator action until 2026-05-15`.

**Resolution required before Task 0 begins.** The plan author (controller) MUST confirm with the operator one of:

1. Soak has organically closed (no PARSE_ERROR / VALIDATION_ERROR / KILL_SWITCH events surfaced; §8.5 acceptance reached) → proceed.
2. Operator overrides the conservative soak reading because OBS-003 is purely additive observability emission on a decision-path file (IC §16 Rule 2 exempt) and does not alter control flow → proceed.
3. Soak is still active and operator wants to wait for `2026-05-15` → defer execution; plan stays valid.

If neither (1) nor (2) is confirmed, **STOP**. Do not dispatch Task 0.

---

## File Structure

| File | Role | Change type |
|------|------|------------|
| `tasks/blend_task.py` | Decision-path orchestration | Modify — add `_emit_skipped()` method; call it before line 204 early return |
| `tests/test_blend_task.py` | BlendTask test suite | Modify — strip 4 `xfail(reason=_OBS003_XFAIL_REASON, strict=True)` markers once green |
| `utils/logger.py` | Trade-log emitter definitions | Modify — extend `log_skipped` docstring with the BlendTask-vs-executor convention note |
| `docs/profit_path_debt_log.md` | Canonical tracker | Modify — flip PROFIT-OBS-003 status `OPEN` → `COMPLETE`; bump counters |
| `scripts/bothealth.sh` | Daily aggregator | No source change — synthetic-input validation only |

---

## Task 0: Worktree setup + baseline xfail confirmation

**Files:**
- Read-only: `tasks/blend_task.py`, `tests/test_blend_task.py`, `docs/superpowers/specs/2026-05-03-obs-003-blendtask-skipped-emission-design.md`

- [ ] **Step 1: Create worktree (if not already on a feature branch)**

```bash
git worktree add ../kalshi-bot-obs-003 -b feature/profit-obs-003-blendtask-skipped-emission
cd ../kalshi-bot-obs-003
```

- [ ] **Step 2: Confirm strict-xfail tests currently fail (the desired pre-implementation state)**

Run: `pytest tests/test_blend_task.py -v -k "obs003 or blocked_blend_emits_skipped or skipped_payload_carries_blended_edge or unblocked_blend_does_not_emit_blendtask_skipped"`

Expected: 4 strict-xfail tests show `XFAIL` in the result line; 1 happy-path test (`test_unblocked_blend_does_not_emit_blendtask_skipped_record` — no xfail) shows `PASS`. No `XPASS` (would mean strict-xfail mismatch and a stale test file).

- [ ] **Step 3: Confirm the implementation surface lines match the spec**

Run: `grep -n "if trade_blocked_reason is not None" tasks/blend_task.py`

Expected: exactly one match at line `204` (or close — line numbers may shift; the structural property is the single early-return guard inside `process_fast_lane_result`).

- [ ] **Step 4: Commit (no code change, just a no-op marker if the worktree was newly created — skip if there is nothing to commit)**

No source change at this task; if there is nothing staged, do not create a commit.

---

## Task 1: Add `_emit_skipped()` helper to `BlendTask` (TDD red phase already in place)

**Files:**
- Modify: `tasks/blend_task.py` — add new private method on `BlendTask`
- Test: `tests/test_blend_task.py` — already pre-staged; the `xfail(strict=True)` markers gate this task

**Why this is one task:** the helper has no side effect on control flow until Task 2 wires it. Landing it in isolation lets the next task be a single-line call-site insertion and matches the spec §2 diff shape.

- [ ] **Step 1: Re-read the test that the helper must satisfy**

Run: `pytest tests/test_blend_task.py::test_obs003_skipped_payload_carries_required_keys -v`

Expected: `XFAIL` (test currently does not run because the BlendTask path never emits SKIPPED). The test pins the executor-compatible key set: `reason, ticker, headline, source, method, llm_direction, llm_magnitude, model_probability, market_price, edge, min_edge_threshold`. Headline must truncate to ≤ 80 chars (executor convention at `trading/executor.py:140`).

- [ ] **Step 2: Add the `_emit_skipped()` method to the `BlendTask` class**

Insert immediately after `_emit_blend_decision()` (around line 337 of the current file, before the module-level `process_fast_lane_result` convenience function at line 340). Match the existing async-helper pattern.

```python
    async def _emit_skipped(
        self,
        *,
        ticker: str,
        blend_result: BlendResult,
        readiness: ReadinessDecision,
        trade_blocked_reason: str,
        fast_lane_result: SignalAnalysis,
    ) -> None:
        """Emit a SKIPPED record for the blocked-reason early-return path.

        Mirrors the executor's SKIPPED payload shape at trading/executor.py:137-152
        so downstream consumers (bothealth.sh, governance Phase 2 reasoning,
        future readiness-gate calibration) can treat the union of
        BlendTask-emitted and executor-emitted SKIPPED records as a single
        queryable stream. The `reason` field disambiguates origin: G1-G6 /
        blender-side reasons originate here; everything else originates at the
        executor. See PROFIT-OBS-003 closure notes in the debt log.
        """
        news_item = fast_lane_result.news_item
        headline = news_item.headline[:80] if news_item is not None else None
        source = news_item.source if news_item is not None else None
        method = (
            "llm"
            if any(
                value is not None
                for value in (
                    fast_lane_result.llm_direction,
                    fast_lane_result.llm_magnitude,
                    fast_lane_result.llm_confidence,
                )
            )
            else "keyword"
        )
        market_price = fast_lane_result.market_yes_price
        blended_p = blend_result.blended_p
        edge = blended_p - market_price / 100.0
        if readiness.readiness_gate_min_edge_override is not None:
            min_edge_threshold = readiness.readiness_gate_min_edge_override
        else:
            min_edge_threshold = (
                PAPER_MIN_EDGE if self._is_paper_mode else cfg.min_edge
            )
        skipped_kwargs: dict[str, Any] = {
            "reason": trade_blocked_reason,
            "ticker": ticker,
            "headline": headline,
            "source": source,
            "method": method,
            "llm_direction": fast_lane_result.llm_direction,
            "llm_magnitude": fast_lane_result.llm_magnitude,
            "model_probability": blended_p,
            "market_price": market_price,
            "edge": edge,
            "min_edge_threshold": min_edge_threshold,
        }
        signal_meta = fast_lane_result.signal_meta
        if signal_meta:
            skipped_kwargs["signal_meta"] = signal_meta
        await write_trade_log_async(
            self._logger.log_skipped,
            **skipped_kwargs,
        )
```

- [ ] **Step 3: Verify `BlendDecisionLogger` Protocol does not need to grow a `log_skipped` member**

The Protocol is structural; `trade_log` already implements `log_skipped` (defined at `utils/logger.py:688`), and `SpyLogger` in tests defines it (line 63). No Protocol amendment is required because the helper accesses `self._logger.log_skipped` at call time, not at type-check time. If a strict mypy run flags this, add `def log_skipped(self, **kwargs: Any) -> None: ...` to the `BlendDecisionLogger` Protocol — but only if the typing run actually fails.

Run: `python -c "from tasks.blend_task import BlendTask; print('import ok')"`

Expected: `import ok`. No NameError on `BlendResult`, `ReadinessDecision`, `SignalAnalysis`, or `Any` (all four are already imported at lines 17-19, 25, 15 respectively).

- [ ] **Step 4: Run the full BlendTask test file to confirm no regression on the 4 strict-xfail markers**

Run: `pytest tests/test_blend_task.py -v --no-header 2>&1 | tail -30`

Expected: every `xfail`-marked OBS-003 test still shows `XFAIL` (helper exists but is not yet called). The non-OBS-003 tests still pass. Zero `XPASS`.

- [ ] **Step 5: Commit (helper-only, no behavior change)**

```bash
git add tasks/blend_task.py
git commit -m "$(cat <<'EOF'
feat(blend_task): add _emit_skipped helper for OBS-003 emission path

Adds the BlendTask._emit_skipped() helper composing an executor-compatible
SKIPPED payload from (blend_result, readiness, fast_lane_result). No call
site yet; behaviour unchanged. Wiring lands in the next commit.

Per docs/superpowers/specs/2026-05-03-obs-003-blendtask-skipped-emission-design.md

Refs PROFIT-OBS-003.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Wire `_emit_skipped()` at the blocked-reason early return + strip xfail markers (TDD green phase)

**Files:**
- Modify: `tasks/blend_task.py` — single-call insertion immediately before the blocked-reason `return BlendTaskResult(...)` at line 204
- Modify: `tests/test_blend_task.py` — remove the 4 `@pytest.mark.xfail(reason=_OBS003_XFAIL_REASON, strict=True)` decorators

- [ ] **Step 1: Insert the `_emit_skipped` call at the blocked-reason early return**

Locate the block:

```python
        if trade_blocked_reason is not None:
            return BlendTaskResult(
                market_ticker=ticker,
                blend_result=blend_result,
                readiness_decision=readiness,
                trade_blocked_reason=trade_blocked_reason,
                candidate=None,
                enqueued=False,
            )
```

Replace with:

```python
        if trade_blocked_reason is not None:
            await self._emit_skipped(
                ticker=ticker,
                blend_result=blend_result,
                readiness=readiness,
                trade_blocked_reason=trade_blocked_reason,
                fast_lane_result=fast_lane_result,
            )
            return BlendTaskResult(
                market_ticker=ticker,
                blend_result=blend_result,
                readiness_decision=readiness,
                trade_blocked_reason=trade_blocked_reason,
                candidate=None,
                enqueued=False,
            )
```

- [ ] **Step 2: Run the OBS-003 strict-xfail tests — they should now `XPASS` and FAIL the suite (because `strict=True`)**

Run: `pytest tests/test_blend_task.py -v -k "obs003 or blocked_blend_emits_skipped or skipped_payload_carries_blended_edge"`

Expected: every test shows `XPASS` and pytest reports the suite as `FAILED` (strict-xfail policy). This is the intentional flip-signal: the implementation works; the test markers must come off.

- [ ] **Step 3: Strip the 4 strict-xfail decorators**

Locate each line in `tests/test_blend_task.py`:

```python
@pytest.mark.xfail(reason=_OBS003_XFAIL_REASON, strict=True)
```

at lines `546`, `627`, `718`, `783`. Delete that decorator line (and only that line; preserve the `@pytest.mark.asyncio` and `@pytest.mark.parametrize` decorators that follow).

- [ ] **Step 4: Run the full BlendTask suite green**

Run: `pytest tests/test_blend_task.py -v --no-header`

Expected: all tests `PASS`. Zero `XFAIL`, zero `XPASS`, zero `FAIL`. The parametrized `test_blocked_blend_emits_skipped_record` produces 7 passes (G1, G2, G3, G4, G5, G6, blender_side).

- [ ] **Step 5: Confirm the `_OBS003_XFAIL_REASON` constant is now unused — and decide**

Run: `grep -n "_OBS003_XFAIL_REASON" tests/test_blend_task.py`

If the only remaining match is the definition at line `489`, delete the definition (lines `489-493` or however many lines the constant occupies; verify by reading). If any other match remains, do not delete — investigate first.

- [ ] **Step 6: Commit**

```bash
git add tasks/blend_task.py tests/test_blend_task.py
git commit -m "$(cat <<'EOF'
feat(blend_task): emit SKIPPED on blocked-reason early return (OBS-003)

Wires _emit_skipped() at tasks/blend_task.py:204 so every BlendTask
blocked-reason exit produces a SKIPPED trade-log record carrying
trade_blocked_reason as the reason. Strips the 4 strict-xfail markers in
tests/test_blend_task.py that gated this implementation; all 11 OBS-003
assertions (per-reason emission, payload key set, async-write target pin,
post-blend edge convention) now pass.

Behavioural impact: SKIPPED stream volume scales ~13x over the prior
13-day window (20 -> 260 lifetime equivalent). Pre-fix audit: 240 of 260
OPPORTUNITY events exited silently. Post-fix invariant:
OPPORTUNITY = SKIPPED + PAPER_TRADE +/- in-flight.

Per docs/superpowers/specs/2026-05-03-obs-003-blendtask-skipped-emission-design.md

Closes the implementation half of PROFIT-OBS-003. Closure (status
OPEN -> COMPLETE) follows after the 24h paper-mode audit confirms the
invariant.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: SKIPPED schema docstring update

**Files:**
- Modify: `utils/logger.py` — extend the `log_skipped` method docstring at line `688`

- [ ] **Step 1: Read the current `log_skipped` definition**

Run: `sed -n '686,734p' utils/logger.py`

Expected: method definition has no docstring today (the immediate body opens with `record = {...}`).

- [ ] **Step 2: Insert a triple-quoted docstring**

Locate the line:

```python
    def log_skipped(
        self,
        *,
        reason: str,
        ticker: str | None = None,
        headline: str | None = None,
        ...
        signal_meta: dict[str, Any] | None = None,
    ) -> None:
        record = {
```

Insert immediately before `record = {`:

```python
        """Emit a SKIPPED trade-log record.

        Two emission origins share this method:

        - `trading/executor.py:152` — emits when the executor's `_validate()`
          rejects a candidate (cooldown, opposing-position guard, capped_dollars,
          insufficient balance, etc.). Reason values come from the executor's
          internal skip-reason vocabulary (e.g.
          ``"edge +0.0000 below min_edge 0.02"``, ``"paper cooldown active"``).
          `model_probability` and `edge` are the executor's pre-trade values
          (= `analysis.estimated_probability` and `analysis.edge`, the fast-lane
          raw values).

        - `tasks/blend_task.py` — emits when the blender or readiness gate
          produces a non-None `trade_blocked_reason` and the candidate is
          dropped before reaching the executor. Reason values are the G1-G6
          readiness-gate enum (``"G1_blended_confidence"`` ...
          ``"G6_recency_score"``) or a blender-side reason. `model_probability`
          is the *post-blend* value (`blend_result.blended_p`), and `edge` is
          the post-blend edge (`blended_p - market_yes_price/100.0`). The
          headline is truncated to 80 chars to match the executor convention.

        Per PROFIT-OBS-003: BlendTask-emitted SKIPPED records were added on
        2026-05-09 to close the OPPORTUNITY -> SKIPPED accounting gap. Before
        this change, ~92% of OPPORTUNITY events exited silently with no
        matching SKIPPED record because the BlendTask blocked-reason path
        bypassed this emitter entirely.
        """
```

- [ ] **Step 3: Confirm logger module still imports cleanly**

Run: `python -c "from utils.logger import trade_log; print('ok')"`

Expected: `ok`.

- [ ] **Step 4: Run any existing logger tests**

Run: `pytest tests/test_trade_log_store.py -v --no-header`

Expected: all pass. Docstring is non-functional; tests should be unaffected.

- [ ] **Step 5: Commit**

```bash
git add utils/logger.py
git commit -m "$(cat <<'EOF'
docs(logger): document BlendTask vs executor SKIPPED origin convention

Adds a docstring to log_skipped clarifying that two emission paths share
the method post-OBS-003: executor (raw fast-lane edge) and BlendTask
(post-blend edge). Reason-value vocabulary differs by origin and lets
audit consumers attribute kills to a specific gate.

Refs PROFIT-OBS-003.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: `bothealth.sh` aggregator validation against synthetic post-fix log

**Files:**
- Read-only: `scripts/bothealth.sh`
- Synthetic test artifact (temporary): `/tmp/obs003-synthetic-trades.jsonl`

- [ ] **Step 1: Locate the SKIPPED-grouping line in bothealth.sh**

Run: `grep -nE "SKIPPED|reason" scripts/bothealth.sh | head`

Expected: at least one line that filters or aggregates `"type": "SKIPPED"` records and groups by the `reason` field. If the script does not key off `reason`, no validation is required for this task — proceed to Step 5 with a one-line note in the commit message confirming bothealth.sh does not depend on the SKIPPED reason vocabulary.

- [ ] **Step 2: Build a synthetic post-fix trade log**

Write the following to `/tmp/obs003-synthetic-trades.jsonl` using a heredoc — one JSON object per line:

```bash
cat > /tmp/obs003-synthetic-trades.jsonl <<'EOF'
{"type":"OPPORTUNITY","ticker":"KXTRUMPIRAN-26MAY01","edge":0.0,"side":"yes"}
{"type":"BLEND_DECISION","ticker":"KXTRUMPIRAN-26MAY01","trade_blocked_reason":"G1_blended_confidence"}
{"type":"SKIPPED","ticker":"KXTRUMPIRAN-26MAY01","reason":"G1_blended_confidence","model_probability":0.50,"market_price":50.0,"edge":0.0}
{"type":"OPPORTUNITY","ticker":"KXMOCTRUMP25-26-APR24","edge":0.0,"side":"yes"}
{"type":"BLEND_DECISION","ticker":"KXMOCTRUMP25-26-APR24","trade_blocked_reason":"G6_recency_score"}
{"type":"SKIPPED","ticker":"KXMOCTRUMP25-26-APR24","reason":"G6_recency_score","model_probability":0.50,"market_price":50.0,"edge":0.0}
{"type":"OPPORTUNITY","ticker":"KXFISAEXTEND-26APR-MAY01","edge":0.068,"side":"yes"}
{"type":"BLEND_DECISION","ticker":"KXFISAEXTEND-26APR-MAY01","trade_blocked_reason":null}
{"type":"PAPER_TRADE","ticker":"KXFISAEXTEND-26APR-MAY01","edge":0.068,"side":"yes"}
{"type":"OPPORTUNITY","ticker":"KXVANCEPAKISTAN-26MAY15","edge":0.0,"side":"yes"}
{"type":"SKIPPED","ticker":"KXVANCEPAKISTAN-26MAY15","reason":"edge +0.0000 below min_edge 0.02","edge":0.0}
EOF
```

This represents 4 OPPORTUNITY events: 2 blocked at BlendTask (G1 + G6), 1 traded, 1 rejected at executor — exercising both SKIPPED origins.

- [ ] **Step 3: Run bothealth.sh against the synthetic file (only if Step 1 found a SKIPPED-grouping path)**

Run with whatever invocation the script supports for a custom log path. Most likely:

```bash
KALSHI_TRADES_LOG=/tmp/obs003-synthetic-trades.jsonl bash scripts/bothealth.sh
```

If the script hard-codes the trade-log path, copy the synthetic file to that path in a scratch worktree (do NOT overwrite production logs), or skip this step and document the limitation in the commit message.

Expected: the SKIPPED histogram displays at least three distinct reason values (`G1_blended_confidence`, `G6_recency_score`, `edge +0.0000 below min_edge 0.02`). No script error. If the histogram cannot render the new high-cardinality reasons, that is the rollback trigger named in spec §9.

- [ ] **Step 4: Clean up**

```bash
rm -f /tmp/obs003-synthetic-trades.jsonl
```

- [ ] **Step 5: Commit**

If a script change was needed, commit it. If validation was passive (no source change), do not create an empty commit — proceed to Task 5.

---

## Task 5: Full-suite green + lint/type pass

**Files:** No source modifications.

- [ ] **Step 1: Run the full pytest suite**

Run: `pytest --no-header -q 2>&1 | tail -40`

Expected: all tests pass. Zero `FAIL`, zero `ERROR`, zero `XPASS`. Note the exact total-count line for the closure commit later.

- [ ] **Step 2: Run lint (ruff or whatever the project uses — confirm by inspecting `pyproject.toml`)**

Run: `ruff check tasks/blend_task.py utils/logger.py tests/test_blend_task.py`

Expected: no errors. If the project does not use ruff, locate the linter via `ls .pre-commit-config.yaml setup.cfg pyproject.toml` and run that instead.

- [ ] **Step 3: Run type check (only if the project enforces one)**

Run: `mypy tasks/blend_task.py utils/logger.py 2>&1 | tail -20`

Expected: clean, OR a list of pre-existing errors that the implementation did not introduce. If new type errors appear, add `def log_skipped(self, **kwargs: Any) -> None: ...` to the `BlendDecisionLogger` Protocol at `tasks/blend_task.py:65` to widen the structural type. Re-run.

- [ ] **Step 4: Commit (only if the type check forced a Protocol amendment)**

If no source change, skip. Otherwise:

```bash
git add tasks/blend_task.py
git commit -m "$(cat <<'EOF'
chore(blend_task): widen BlendDecisionLogger Protocol with log_skipped

Adds the log_skipped member to BlendDecisionLogger so structural-type
checking accepts the OBS-003 emission. trade_log and tests/SpyLogger
already implement the method; this is a typing-only widening.

Refs PROFIT-OBS-003.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: 24-hour paper-mode audit kickoff

**Files:** Operator-side action; no source changes.

**Why this is a plan task:** Spec §8 acceptance criterion #3 requires a 24h paper-mode audit confirming `OPPORTUNITY = SKIPPED + PAPER_TRADE ± in-flight (N small, < 5)` before the debt-log entry can flip to `COMPLETE`. Task 7 (closure) is gated on this.

- [ ] **Step 1: Push the feature branch (if working in a worktree)**

```bash
git push origin feature/profit-obs-003-blendtask-skipped-emission
```

Operator: ask for explicit confirmation before pushing. Do not push without confirmation.

- [ ] **Step 2: Operator merges + redeploys**

Operator action — out of scope for the implementer subagent. The plan controller (you) records the merge SHA and the LaunchAgent reload timestamp.

- [ ] **Step 3: Wait 24 hours of paper-mode runtime**

Monitor via the existing `bothealth.sh` cadence. Note: per the soak-confirmation cadence feedback memory, mid-soak confirmation reports cap at 1 per UTC day per agent.

- [ ] **Step 4: Compute the audit invariant**

Once the 24h window is closed, run the trade-log accounting query. The exact incantation depends on the project's audit tooling — start with:

```bash
python - <<'EOF'
import json, pathlib, collections
counts = collections.Counter()
log_path = pathlib.Path("logs/trades/live/trades.jsonl")
for line in log_path.read_text(encoding="utf-8").splitlines():
    try:
        rec = json.loads(line)
    except json.JSONDecodeError:
        continue
    counts[rec.get("type", "UNKNOWN")] += 1
print(counts)
opp = counts["OPPORTUNITY"]
skip = counts["SKIPPED"]
paper = counts["PAPER_TRADE"]
delta = opp - skip - paper
print(f"OPPORTUNITY={opp} SKIPPED={skip} PAPER_TRADE={paper} delta={delta}")
EOF
```

Expected: `delta` is small (< 5). If `delta` is large and positive, candidates are still silently exiting — investigate before closing OBS-003. If `delta` is negative, SKIPPED is double-firing somewhere — rollback per spec §9.

- [ ] **Step 5: Capture the SKIPPED reason histogram**

```bash
python - <<'EOF'
import json, pathlib, collections
reasons = collections.Counter()
for line in pathlib.Path("logs/trades/live/trades.jsonl").read_text(encoding="utf-8").splitlines():
    try:
        rec = json.loads(line)
    except json.JSONDecodeError:
        continue
    if rec.get("type") == "SKIPPED":
        reasons[rec.get("reason", "?")] += 1
for reason, count in reasons.most_common():
    print(f"{count:6d}  {reason}")
EOF
```

Expected: at least 3 distinct reason strings (the 17/17 monoculture is broken). G1 / G2 / G6 should dominate per the 2026-05-03 attribution.

---

## Task 7: Debt-log closure — PROFIT-OBS-003 OPEN → COMPLETE

**Files:**
- Modify: `docs/profit_path_debt_log.md` — flip status field; bump header counters; cross-reference closure from PROFIT-EDGE-004 notes per spec §7.5.

- [ ] **Step 1: Locate the PROFIT-OBS-003 entry**

Run: `grep -n "^### PROFIT-OBS-003\|Open.*HIGH\|Items COMPLETE" docs/profit_path_debt_log.md | head`

Capture the line numbers for: the entry header, the `Open — HIGH` counter row, the `Items COMPLETE` counter row.

- [ ] **Step 2: Flip the entry's `Status` field**

In the OBS-003 metadata table, change `| **Status** | OPEN |` to `| **Status** | COMPLETE (2026-05-09) |`. Preserve the `Severity`, `Priority`, `Owner`, `Depends On`, `Blocks` rows.

- [ ] **Step 3: Update the header counters**

`Open — HIGH`: decrement by 1 (current value `5` becomes `4`).
`Items COMPLETE`: append `, PROFIT-OBS-003` to the parenthesized list and bump the count by 1 (current value `36` becomes `37`).

- [ ] **Step 4: Append a closure note to the OBS-003 entry**

Add to the entry, after the existing `Validation Notes` section:

```markdown
**Closure (2026-05-09)**

Implemented per spec `docs/superpowers/specs/2026-05-03-obs-003-blendtask-skipped-emission-design.md`. `BlendTask._emit_skipped()` lands at the blocked-reason early return; payload mirrors the executor's SKIPPED shape (post-blend `model_probability` and `edge`, headline truncated to 80 chars). 4 strict-xfail OBS-003 tests in `tests/test_blend_task.py` flipped to passing (per-reason emission for G1-G6 + blender-side, async-write target pin, executor-compatible key set). 24h post-deploy audit: `OPPORTUNITY = SKIPPED + PAPER_TRADE ± <5`. SKIPPED reason monoculture broken (was 17/17 `"edge +0.0000 below min_edge 0.02"`; post-fix histogram includes G1, G6, G2). Cross-references this closure: PROFIT-EDGE-004 audit can now quote per-gate kill counts directly from the SKIPPED stream.
```

- [ ] **Step 5: Cross-reference from PROFIT-EDGE-004**

Locate the EDGE-004 entry. In its Notes / Evidence section, add a one-line entry: `OBS-003 closed 2026-05-09 — per-gate kill attribution now available via SKIPPED reason field.`

- [ ] **Step 6: Verify counter math**

Run: `grep -nE "Open — HIGH|Items COMPLETE|^### PROFIT-OBS-003" docs/profit_path_debt_log.md | head`

Confirm: `Open — HIGH | 4`; `Items COMPLETE | 37`; OBS-003 entry first line shows the new `COMPLETE (2026-05-09)` status.

- [ ] **Step 7: Commit**

```bash
git add docs/profit_path_debt_log.md
git commit -m "$(cat <<'EOF'
docs(debt): close PROFIT-OBS-003 — BlendTask SKIPPED emission landed

Flips PROFIT-OBS-003 from OPEN to COMPLETE after the 24h paper-mode
audit confirmed the OPPORTUNITY = SKIPPED + PAPER_TRADE +/- in-flight
invariant (delta < 5). SKIPPED reason monoculture broken: post-fix
histogram includes G1_blended_confidence, G6_recency_score,
G2_evidence_source_class_diversity in addition to the executor's
pre-existing reason set.

Counter updates:
- Open - HIGH: 5 -> 4
- Items COMPLETE: 36 -> 37 (PROFIT-OBS-003 added)

Cross-referenced the closure from PROFIT-EDGE-004's notes per spec
docs/superpowers/specs/2026-05-03-obs-003-blendtask-skipped-emission-design.md
section 7.5 — EDGE-004's evidence section can now quote per-gate kill
counts directly from the unified SKIPPED stream.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Branch finishing

**Files:** No source modifications.

- [ ] **Step 1: Run the final verification**

Run: `pytest tests/test_blend_task.py tests/test_trade_log_store.py -v --no-header`

Expected: all green. Capture the test count for the PR description.

- [ ] **Step 2: Hand off to `superpowers:finishing-a-development-branch`**

Skill description: integrates the feature branch back to main, decides squash vs merge vs rebase based on commit history, runs final verification, optionally creates the PR. Invoke it.

The plan ends here. Do not push or merge in the controller turn — the finishing skill owns that decision per its own checklist.

---

## Self-Review Notes

**Spec coverage check:**
- Spec §1 (Problem): explained in plan goal + soak-window gate.
- Spec §2 (Fix): Task 2 step 1 shows the exact diff.
- Spec §3 (Components): file table at the top covers blend_task.py + tests + logger.py + bothealth.
- Spec §4 (Data flow): described implicitly via the closure-note text in Task 7.
- Spec §5 (Payload shape): full helper body in Task 1 step 2 implements the field-by-field mapping.
- Spec §6 (Risk): captured in soak-window gate + Task 4 (bothealth validation) + Task 6 (24h audit).
- Spec §7 (Implementation plan): every numbered item maps to a task.
- Spec §8 (Acceptance criteria): Task 6 verifies invariants 1-3; Task 5 verifies criterion 4 (full pytest green).
- Spec §9 (Rollback): covered by Task 6 step 4 (negative delta = rollback trigger).
- Spec §10 (Soak-window contract): plan's BLOCKING gate at the top.
- Spec §11 (Out of scope): plan does not touch executor `_validate()`, `BLEND_DECISION` schema, or G1 calibration.

**Type / signature consistency:** the helper signature in Task 1 step 2 matches the call site in Task 2 step 1 — same kwargs (`ticker`, `blend_result`, `readiness`, `trade_blocked_reason`, `fast_lane_result`).

**Placeholder scan:** every task carries actual code or actual commands. No "TBD" / "TODO" / "fill in details" / "implement appropriate error handling" remain. The Step 5 commit-message templates are concrete; the Task 4 bothealth invocation has a fallback path documented if the script does not support the `KALSHI_TRADES_LOG` env var.
