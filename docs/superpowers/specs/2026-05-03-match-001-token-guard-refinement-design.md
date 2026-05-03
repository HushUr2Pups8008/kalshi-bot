# MATCH-001 (B′) — `_token_not_in_ticker` guard refinement

**Status:** design (post-soak implementation; do not land before PROFIT-PHASE2-001 closes ≥ 2026-05-09)
**Tracker:** `PROFIT-MATCH-001` in `docs/profit_path_debt_log.md`
**Owner:** Claude
**Severity:** MEDIUM
**Dependencies:** none (independent of OBS-003 / OBS-005 / EXEC-002 specs)

## 1. Problem

The matcher's low-quality-match suppression at `analysis/market_matcher.py:626–649` uses two predicate paths joined by AND with a ticker-guard:

```python
_meets_suppression_criteria = (
    bool(heuristic_flags)
    and _token_not_in_ticker
    and (_near_threshold_weak or _pure_single_entity)
)
```

`_token_not_in_ticker` returns True when **none** of the matched-overlap tokens appear in the market ticker. The guard's stated purpose is to preserve "topic-aligned" matches (`iran` ↔ `KXTRUMPIRAN`), but the asymmetric all-tokens-not-in-ticker check actually preserves **every** entity-prefix-ticker match — including pure noise like `trump` ↔ `KXMOCTRUMP25`, `pakistan` ↔ `KXVANCEPAKISTAN`, `iran` ↔ `KXTRUMPIRAN-26MAY01` from off-topic headlines.

Empirical impact, per the 2026-05-02 audit (PROFIT-MATCH-001 entry, full archive):

- 2,880 `MATCH_DIAGNOSTIC` events
- 1,705 carry both `single_named_entity_only` + `minimal_overlap` flags
- ~498 `MATCH_SUPPRESSED` events (Path A + B fire correctly)
- ~1,207 single-entity matches **survive into the LLM analysis stage** because the matched token is in the ticker prefix, hitting `_token_not_in_ticker = False`

Each surviving low-quality match consumes a qwen2.5:7b LLM call (~5–8 s on Mac Studio T6041) and produces, in most cases, a `magnitude=none` non-actionable response. Wasted compute, no corruption — but the matcher feeds the LLM, so noisy matches dilute the bot's signal density at the expense of feed-mix expansion (Appendix A sources, etc.).

## 2. The fix (Option B′)

Replace the binary all-tokens-not-in-ticker guard with a stricter predicate: the guard passes (i.e., suppression is *blocked*) only when **at least one** matched token is *not* in the ticker.

```python
ticker_tokens = _tokenize(market.ticker)
non_ticker_overlap = overlap - ticker_tokens
_has_supporting_non_ticker_token = bool(non_ticker_overlap)
```

The new suppression predicate becomes:

```python
_meets_suppression_criteria = (
    bool(heuristic_flags)
    and not _has_supporting_non_ticker_token
    and (_near_threshold_weak or _pure_single_entity)
)
```

**Semantic difference:**

- Pure entity-token-only match where the entity is in the ticker (`trump` ↔ `KXMOCTRUMP25` from "King Charles Visits US as Britain Seeks to Steady Ties With Trump"): `non_ticker_overlap = ∅` → guard fails → suppression fires. ✅
- Coherent topic match (`trump`, `iran`, `witkoff`, `kushner` ↔ `KXTRUMPIRAN` from "Trump dispatching Witkoff, Kushner for talks with Iran FM"): `non_ticker_overlap ⊇ {witkoff, kushner}` → guard passes → suppression does NOT fire. ✅

The fix is symmetric with the existing `_pure_single_entity` Path B (commit `825a065`, 2026-04-16): both paths recognize that entity-prefix tickers need the matcher to look beyond the prefix.

## 3. Components touched

Single file:

- `analysis/market_matcher.py` lines ~626–649. The change is local to the suppression-predicate block; no other call sites move.

The `_tokenize` helper already exists in the same module (used at line ~712 for fade pipeline). Reusing it for ticker-tokenization keeps the refactor minimal.

## 4. Data flow

No flow change. The matcher still:

1. Runs Jaccard + named-entity overlap scoring (unchanged).
2. Emits `MATCH_DIAGNOSTIC` for every candidate (unchanged).
3. Computes suppression predicate (this change: predicate becomes stricter).
4. Either continues past the candidate (if suppressed) or appends it to `scored` for downstream LLM analysis (unchanged).

The downstream effect is that the `scored` list shrinks: ~1,207 matches/13d that previously reached the LLM now stop at `MATCH_SUPPRESSED` instead.

## 5. Risk: canonical regression-anchor coverage

The 5 canonical LLM-positive events in `scripts/simulations/_common.py:LLM_POSITIVE_EVENTS_2026_04_26` decompose against this fix as follows:

| Event | Ticker | Headline tokens | Overlap with ticker | Non-ticker overlap | Survives B′? |
|---|---|---|---|---|---|
| 1 — KXSBUDGETRES-APR28 (ICE funding) | KXSBUDGETRES-26APR-APR28 | senate, ICE, resolution, budget, vote-a-rama | none likely | full | ✅ |
| 2 — KXSBUDGETRES-APR25 | same | same | none likely | full | ✅ |
| 3 — KXTRUMPIRAN (dispatching) | KXTRUMPIRAN-26MAY01 | trump, witkoff, kushner, iran, pakistan | {trump, iran} | {witkoff, kushner, pakistan} | ✅ |
| 4 — KXPSL-PZA (cricket) | KXPSL-26-PZA | psl, babar, zalmi, qalandars | {psl} | {babar, zalmi, qalandars} | n/a (sport-blocklist filters upstream) |
| 5 — KXTRUMPIRAN (talks stall) | KXTRUMPIRAN-26MAY01 | iran, trump, talks | {iran, trump} | {talks} | ✅ |

Events 3 and 5 are the load-bearing safety case. Both retain non-ticker support tokens that pass the new guard. The fix preserves every canonical event's anchor in the top-3 at score ≥ 0.06.

## 6. Implementation plan

Single-PR-equivalent change, lands as one commit:

1. **Source change** (`analysis/market_matcher.py`):
   - Tokenize the market ticker via the existing `_tokenize` helper (lowercase, drop non-alphanum, drop short tokens per the helper's existing rules).
   - Compute `non_ticker_overlap = overlap - ticker_tokens`.
   - Replace `_token_not_in_ticker = not any(token in ticker_lower for token in overlap)` with `_has_supporting_non_ticker_token = bool(non_ticker_overlap)`, and update the `_meets_suppression_criteria` conjunction to use `not _has_supporting_non_ticker_token`.
   - Preserve the `# Path A` and `# Path B` inline-comment headers; add a one-line docstring update on the new guard explaining the asymmetry fix.
2. **Smoke test** (`tests/test_market_matcher.py`):
   - Add `test_b_prime_token_in_ticker_with_supporting_non_ticker_token_survives` — POS_event_3 / POS_event_5 shape: matched tokens include both ticker entity AND non-ticker support tokens; assert match is *not* suppressed.
   - Add `test_b_prime_token_in_ticker_only_with_no_support_suppresses` — pure noise shape: only matched token is the ticker entity; assert match *is* suppressed (suppression fires where it didn't before).
   - Update any existing test that relied on the binary `_token_not_in_ticker` semantics; explicit listing of pinned cases is cheap insurance against silent suppression-rate regressions.
3. **Harness regression** (`scripts/simulations/match_score_audit.py`):
   - Re-run against the 5 canonical events.
   - Acceptance: every event's anchor ticker still surfaces in the top-3 at score ≥ 0.06.
   - Acceptance: the threshold-sweep table remains unchanged or improves (no canonical event drops off at any threshold ≤ `PAPER_MIN_MATCH_SCORE`).
4. **Pre-deploy archive replay**:
   - One-shot script (committed under `scripts/simulations/match_b_prime_dry_run.py` or similar — soak-safe naming pattern) that replays the full `MATCH_DIAGNOSTIC` archive against the new predicate locally, counting how many records would flip from `survived → suppressed`.
   - Acceptance: post-fix flip count is in the 600–1,300 range (consistent with the 2026-05-02 forensic addendum's revised 1,207 estimate; the wider band tolerates archive-window drift).
   - Acceptance: zero canonical-event *headlines* (paired with their canonical tickers KXSBUDGETRES-26APR-APR28, KXSBUDGETRES-26APR-APR25, KXTRUMPIRAN-26MAY01, KXPSL-26-PZA, KXTXRUNOFFENDORSE-26MAY26-DJT-BOTH from PROFIT-OBS-003's positive-edge silent-exit) appear in the flip set. **Important:** the canonical *tickers* themselves WILL appear in `MATCH_SUPPRESSED` for non-canonical low-quality headlines, and that is correct behavior — Codex's 2026-05-03 anchor audit (`docs/governance/2026-05-03-match001-bprime-anchor-sizing.md`) found 399 legitimate low-quality suppressions on `KXTRUMPIRAN-26MAY01` alone. The guard is headline-level, not ticker-level.
5. **Post-deploy 24-hour monitoring**:
   - `MATCH_SUPPRESSED` event count over the first 24 h post-deploy. Expected: ~1.5–2.5× the prior 24h rate (proportional to the ~498 → ~1,200–1,700 archive-rate scaling).
   - Confirm no canonical-event *headline* (paired with its canonical ticker) appears in `MATCH_SUPPRESSED` records. Any false-positive on a canonical *headline* (not just the ticker) is grounds for immediate revert.
6. **Closure**:
   - Update PROFIT-MATCH-001 entry: status OPEN → COMPLETE, citing harness pass + archive-replay range + 24h `MATCH_SUPPRESSED` count.
   - Update top-of-file counters: Open MEDIUM 1 → 0; Items COMPLETE += 1.
   - Add a one-line cross-reference from `scripts/simulations/match_score_audit.py` docstring to PROFIT-MATCH-001's closure note.

## 7. Acceptance criteria (lifted from the existing entry, refined for B′)

- `analysis/market_matcher.py:_meets_suppression_criteria` updated; `_token_not_in_ticker` replaced with `_has_supporting_non_ticker_token` (the asymmetry fix).
- `scripts/simulations/match_score_audit.py` re-run post-fix; all 5 canonical events still surface their anchor in top-3 at score ≥ 0.06.
- Pre-deploy archive replay confirms ~600–1,300 records flip from survived → suppressed; zero canonical-event *headlines* (paired with their canonical tickers) in the flip set. Codex's 2026-05-03 anchor audit landed the empirical: 1,076 keys flip; 0/5 canonical headlines suppressed; canonical tickers do legitimately appear in non-canonical low-quality suppressions and that is correct.
- Post-deploy 24-hour `MATCH_SUPPRESSED` count rises proportionally; no canonical-event *headline* (paired with its canonical ticker) appears in `MATCH_SUPPRESSED`. Headline-level guard, not ticker-level.
- Two new smoke tests in `tests/test_market_matcher.py` pin the new predicate. Full pytest suite green.

## 8. Rollback

The change is a single conjunction in one file. Revert is trivial:

```python
# revert hunk
- _has_supporting_non_ticker_token = bool(overlap - ticker_tokens)
+ _token_not_in_ticker = not any(token in ticker_lower for token in overlap)
- and not _has_supporting_non_ticker_token
+ and _token_not_in_ticker
```

Trigger to revert: any canonical-event *headline* (paired with its canonical ticker) shows up in `MATCH_SUPPRESSED` records during the post-deploy 24-hour window, OR `MATCH_SUPPRESSED` count drops below the prior baseline (suggesting the predicate broke the other direction and now suppresses *less* than before, which would mean the refactor inverted a logical operator). The bare canonical ticker appearing in `MATCH_SUPPRESSED` is *not* a revert trigger — the guard is headline-level.

## 9. Soak-window contract

This spec is pre-loaded during `PROFIT-PHASE2-001` soak (drafted 2026-05-03; do not implement before 2026-05-09 organic close or 2026-05-16 hard ceiling). Implementation is a decision-path edit and violates the CLAUDE.md `decision consistency = high-risk during soak` rule if landed mid-soak. The day the soak closes, this spec becomes implementation-ready and lands as the first item in the post-soak queue (per the recommendation in the 2026-05-03 brainstorming session that produced this spec).

## 10. Out of scope

- **PROFIT-OBS-003** — silent-exit logging gap. Independent fix; separate spec.
- **PROFIT-OBS-005** — cooldown trip after restart. Independent fix; separate spec.
- **PROFIT-EDGE-004** — no-edge investigation. Already mostly closed; matcher quality is its primary remaining lever, but the fix here is bounded to the specific suppression-predicate asymmetry.
- **Series-aware token weighting** (the original entry's "Option C" / hypothetical PROFIT-MATCH-002). Larger refactor; only file if (B′)'s empirical impact is insufficient.
- **Sport-prefix blocklist maintenance** — separate concern; PROFIT-EDGE-002 already handled the immediate-leak set.
