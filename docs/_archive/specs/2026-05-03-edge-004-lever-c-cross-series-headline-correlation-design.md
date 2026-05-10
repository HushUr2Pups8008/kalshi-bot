# PROFIT-EDGE-004 Lever C — cross-series headline correlation (EXEC-002 Approach 2)

> **🛑 BLOCKED PER IC §16 (cycle-11.5 strategic redirect, 2026-05-06).** Wave-3 deploy is HALTED pending Cycle-12 replay harness output. Lever C is a SUPPRESSION lever (risk control, not edge production); its value preserves only IF the bot has positive-EV trading to suppress. With current data (3/3 lifetime trades lost, 89 % zero-edge SKIPPEDs), suppressing more trades reduces variance but the underlying expectation is already negative. Replay must establish a positive-EV baseline before this guard's variance reduction is meaningful. See `docs/governance/2026-05-06-strategic-redirect-edge-replay-priority.md` and IC §16.

**Status:** BLOCKED PER IC §16 (was: design; Wave 3 of post-soak landing — earliest deploy gated on Lever A + Lever B verdicts AND on Codex's cross-series-single-headline overlap audit; earliest implementation 2026-06-20+)
**Tracker:** `PROFIT-EDGE-004` (Lever C entry from `2026-05-03-edge-004-lever-menu-design.md`); also referenced in `PROFIT-EXEC-002` spec §11 as "Approach 2"
**Owner:** Claude (design) + Codex (overlap-rate sizing audit — see §4)
**Severity:** MED-HIGH (parent EDGE-004 closure path; 4th lever in the revised A → B → E → C → D sequence)
**Drafted:** 2026-05-03

## 1. Why this lever, and why now

EXEC-002 (Approach 1, landing in the post-soak base stack) suppresses *same-series-prefix* candidate bursts within a 1 h window. The 2026-05-01 FISA replay (KXFISAEXTEND-26APR-MAY01 / -MAY02 / -MAY03) is the canonical case — 3 trades on the same series prefix from a single headline, all losses. EXEC-002 collapses that to 1 trade.

**Approach 2 (Lever C) extends the same logic across series prefixes.** A single headline naming Trump can fire on `KXTRUMPIRAN`, `KXMOCTRUMP25`, `KXPARDONSTRUMP`, `KXTRUMPENDORSE` simultaneously — each is a *different* series prefix but the underlying conviction (LLM verdict on the headline) is shared. Three or four trades on the same headline = oversized risk vs the single LLM call's confidence interval.

EXEC-002 §11 explicitly defers Approach 2 with the rationale: *"defer until EDGE-004's matcher-quality work quantifies the cross-series-headline overlap rate. Filed as a future follow-up if Approach 1's empirical impact is insufficient."*

This spec moves Approach 2 from a deferred-followup paragraph to a designed-but-empirics-gated lever. Implementation depends on Codex's overlap-rate sizing (§4); if rate < 5 %, Lever C dies and gets formally closed as out-of-scope. If rate ≥ 5 %, the spec below becomes implementation-ready.

## 2. The fix

When BlendTask is about to enqueue a candidate, check whether another candidate with a sufficiently similar *headline* (independent of series prefix) has been enqueued in the last `cross_series_correlation_window` (default 1 h). If so, mark the current candidate as `trade_blocked_reason="cross_series_headline_in_window"` and route through the OBS-003 SKIPPED-emission path. The first candidate carrying a given headline still trades; subsequent candidates within the window are suppressed.

```python
# tasks/blend_task.py — pseudo-flow inserted between same-series check and _emit_blend_decision
headline_hash = _headline_hash(fast_lane_result.news_item.headline)
last_cross_series = self._recent_headline_enqueues.get(headline_hash)
if last_cross_series is not None:
    elapsed = time.monotonic() - last_cross_series
    if elapsed < cfg.cross_series_correlation_window_seconds:
        blocked_reason = "cross_series_headline_in_window"
```

## 3. Headline-hash design

The hash function determines whether two headlines are "the same." Three candidate approaches with sharply different blast radii:

### 3.1 Exact-string hash (simplest)

```python
def _headline_hash(headline: str) -> str:
    return hashlib.sha256(headline.encode("utf-8")).hexdigest()[:16]
```

- **Catches:** identical headlines (the FISA case shape).
- **Misses:** semantically-identical headlines with different punctuation, case, or trailing source-suffix (e.g., `"... - VitalLaw.com"` vs `"...   -   VitalLaw.com"` vs no suffix).
- **Risk:** zero false-positives (two distinct headlines never collide).
- **Verdict (revised post-Codex audit):** **fallback — not v1.** Codex's 2026-05-03 audit validated the §3.2 normalized surface at 49.2 % overlap; §3.1 is a strict subset of §3.2 and would understate the catch. §3.1 stays documented here as the conservative fallback if §3.2's normalized regex turns out to over-collapse in production.

### 3.2 Normalized-string hash (moderate) — **v1 implementation**

```python
def _headline_hash(headline: str) -> str:
    norm = re.sub(r"\s+", " ", headline.lower().strip())
    norm = re.sub(r"\s*[-–]\s*[A-Za-z0-9.\s]+\.com.*$", "", norm)  # strip source suffix
    norm = re.sub(r"[^\w\s]", "", norm)  # punctuation off
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]
```

- **Catches:** the §3.1 set + cosmetic variants (case, punctuation, trailing source-suffix, whitespace).
- **Misses:** semantically-identical headlines with rewordings (paraphrases).
- **Risk:** small false-positive rate (regex-based normalization can collide on edge cases).
- **Verdict (revised post-Codex audit):** **v1 — Codex's 2026-05-03 audit (`docs/governance/2026-05-03-cross-series-headline-overlap-audit.md` + `scripts/simulations/cross_series_headline_overlap_audit.py`) validated this surface at 49.2 % overlap on the 13-day archive.** Use exactly the normalization rules in Codex's audit script so the production gate matches the empirical sizing.

### 3.3 Token-overlap-Jaccard hash (semantic)

Two headlines are "the same" if their token-set Jaccard similarity exceeds a threshold (e.g., 0.85). Implementation: maintain a sliding window of recent (token_set, monotonic_ts) pairs; on new candidate, compute Jaccard against all in-window entries; suppress if any matches.

- **Catches:** paraphrases, reordered phrases, partial rewordings.
- **Misses:** very-rephrased headlines or different angles on the same event.
- **Risk:** false-positive rate scales with threshold; below 0.85 starts collapsing distinct news.
- **Verdict:** out of scope for v1 / v2. Re-evaluate only if 3.2 is insufficient.

**Recommendation (revised post-Codex 2026-05-03 audit): ship v1 with §3.2 normalized-string hash.** Codex's empirical audit at 49.2 % overlap was computed against §3.2; that's the validated surface. §3.1 is a strict subset and would understate the catch. §3.3 token-Jaccard remains out of scope (see §3.3's verdict line below).

## 4. Sizing — DONE (Codex 2026-05-03 audit)

`docs/governance/2026-05-03-cross-series-headline-overlap-audit.md` + `scripts/simulations/cross_series_headline_overlap_audit.py` (in the same Codex push as this spec). Replays the 13-day MacBook archive against §3.2 normalized-string hash:

| metric | value |
|---|---:|
| OPPORTUNITY total | 260 |
| Normalized headline groups | 138 |
| Cross-series headline groups | 39 |
| Cross-series OPPORTUNITY count | **128 (49.2 %)** |
| Verdict | **`supports_lever_c`** |

The 49.2 % overlap is **far above the 15 % "necessary" threshold** in the original §4 decision criteria. Lever C's empirical justification is overwhelming on the archive's market mix.

**Top series-pair overlaps:**

| pair | overlapped OPP records |
|---|---:|
| `KXMOCTRUMP25` / `KXTRUMPIRAN` | 79 |
| `KXTRUMPIRAN` / `KXVANCEPAKISTAN` | 27 |
| `KXTRUMPCHINA` / `KXTRUMPIRAN` | 12 |
| `KXTRUMPENDORSE` / `KXTRUMPIRAN` | 9 |

The `KXMOCTRUMP25` ↔ `KXTRUMPIRAN` lane alone accounts for 79 cross-series correlations — Trump-era political news routinely hits both prefixes. The audit's top-cross-series-groups table shows headlines like *"Iran War Live Updates: Iranian Forces Claim Seizure of 2 Ships After Trump Extends Truce"* firing on 9 OPPORTUNITY events across 3 distinct tickers from 2 series prefixes.

**Implication for §3 hash choice:** Codex's audit used §3.2 normalized-string hash (case + punctuation + URL + whitespace ignored). The 49.2 % rate captures only exact-after-normalization matches; §3.1 raw-exact would catch a strict subset. **v1 should ship §3.2 normalized**, not §3.1, because (a) the empirical surface is normalized-equivalent and (b) Codex's archive harness already validates the normalization rules.

## 5. Components touched

If Codex's audit greenlights the lever, implementation is:

- `tasks/blend_task.py`:
  - New instance attribute `self._recent_headline_enqueues: dict[str, float]` (mirroring EXEC-002's `_recent_series_enqueues`).
  - New helper `_headline_hash(headline: str) -> str` (per §3.2 normalized-string v1 — this is the surface Codex's 49.2 % overlap audit validated).
  - Cross-series check inserted *after* EXEC-002's same-series check, *before* `_emit_blend_decision`.
  - On successful enqueue, update both `_recent_series_enqueues` and `_recent_headline_enqueues`.
- `config.py`:
  - New `CROSS_SERIES_CORRELATION_WINDOW_SECONDS` env-var-driven config knob, default 3600 (1 h). Loaded into `cfg.cross_series_correlation_window_seconds`.
- `tests/test_blend_task.py`:
  - 4 strict-xfail tests pre-loaded as a follow-up commit if the lever lands:
    - `test_cross_series_same_headline_only_first_enqueues` — 3 candidates with distinct series prefixes but identical headlines fire within window; assert exactly 1 enqueues.
    - `test_cross_series_different_headlines_both_enqueue` — 2 candidates with different headlines (and different prefixes) both enqueue.
    - `test_cross_series_headline_window_expiry_allows_second` — same headline, second candidate arriving past window; both enqueue.
    - `test_cross_series_headline_window_zero_disables_guard` — `cfg.cross_series_correlation_window_seconds=0` disables.

**No changes to** `analysis/`, `trading/`, `feeds/`. The fix is purely BlendTask + config.

## 6. Risk

- **Composition with EXEC-002 (same-series guard).** EXEC-002 fires first; for FISA-shape bursts (same series + same headline), EXEC-002 already catches them. Lever C only adds value for *different-series + same-headline* bursts. Composition is straightforward — both checks are instance-state-mutation-on-enqueue patterns that don't interact.
- **False-positive risk on identical generic headlines.** Wire services can emit a single short headline on multiple distinct topics ("Trump signs order"). v1 §3.2 normalized-string would collapse them — but they're typically routed to different series prefixes anyway, and EXEC-002's 1 h window means a short coincidental collision is bounded. Mitigation: keep the conservative 1 h window default; don't tighten without empirical justification.
- **Restart resets the tracker.** Same shape as EXEC-002 §6 — `_recent_headline_enqueues` is in-memory; bot restart clears it. EXEC-002's mitigation pattern (seed from `paper_trades.db` on construction) extends naturally; seed `_recent_headline_enqueues` from `paper_trades.headline` (joined with `_headline_hash` post-fact) for entries newer than `now - window`. If the table doesn't carry headline columns, fall back to a no-seed strategy and accept the post-restart blind window.
- **Soak invariant.** Lever C is a decision-path edit on `tasks/blend_task.py`. Cannot land mid-soak. Must wait until base stack stabilises + Lever A/B verdicts.

## 7. Acceptance criteria

- Codex's cross-series overlap audit (§4) confirms rate ≥ 5 %.
- `tasks/blend_task.py` and `config.py` updated per §5.
- 4 new strict-xfail tests pre-loaded against `tests/test_blend_task.py` (if the lever lands).
- 24 h post-deploy SQL audit returns zero rows from a `SELECT MIN(ts), MAX(ts), COUNT(DISTINCT series_ticker), headline_hash FROM paper_trades WHERE ts >= datetime('now','-1 day') GROUP BY headline_hash HAVING COUNT(*) > 1 AND COUNT(DISTINCT series_ticker) > 1 AND (MAX(ts) - MIN(ts)) < <window>` query (assuming a `headline_hash` column or join to a derived view).
- 14 d post-deploy realized P&L on candidates suppressed by the new gate is **flat or improved** vs the matched-window pre-deploy baseline. Same logic as EXEC-002: cross-series bursts on hot news are over-sized risk; suppressing them right-sizes the bet.
- Full pytest suite green.

## 8. Rollback

Revert is the BlendTask + config diff. Operator-side fast revert: `CROSS_SERIES_CORRELATION_WINDOW_SECONDS=0` in env + restart. Disables without code change.

Trigger to revert: post-deploy 14 d realized P&L on suppressed candidates is *worse* than baseline (gate is suppressing wins, not losses), OR `cross_series_headline_in_window` SKIPPED records fire on candidates with provably distinct underlying news (false-positive rate > 10 %).

## 9. Soak-window contract

Documentation only pre-deploy. No code changes during the active `PROFIT-PHASE2-001` soak. Lands in **Wave 3** of post-soak landing — after base stack (Wave 1: OBS-005 / MATCH-001 / OBS-003 / EXEC-002 + governance_monitor fix), after Lever A intake-diversification (Wave 2 first try), and after Lever B G1 calibration (Wave 2 second try if Lever A stalls).

Earliest implementation: 2026-06-20 (assuming base stack + Lever A close cleanly by 2026-05-30 and Lever B's 14 d window runs through 2026-06-13).

## 10. xfail harness pre-load decision

**Defer.** Codex's overlap audit greenlit Lever C empirically (49.2 % rate; verdict `supports_lever_c`), but landing remains gated on Lever A + Lever B failing to close EDGE-004. If Lever A's first-feed verdict (~2026-05-29) closes EDGE-004, Lever C never lands and the harness is dead code.

If after Lever B's verdict (~2026-06-13) Lever C becomes the chosen path, draft the harness pre-load with the §3.2 normalized hash function at that time. Codex's audit script (`scripts/simulations/cross_series_headline_overlap_audit.py`) provides the canonical normalization rules for that pre-load.

## 11. Out of scope

- **§3.1 / §3.3 hash functions.** §3.2 normalized-string is v1 (per the post-Codex-audit recommendation in §3 and §5). §3.1 raw-exact stays documented as the fallback if §3.2's regex over-collapses in production. §3.3 token-Jaccard remains out of scope until/unless §3.2 is insufficient.
- **Combined headline + same-series logic.** EXEC-002 already handles same-series; Lever C is strictly the cross-series complement. No interaction logic beyond ordering the two checks.
- **`_headline_hash` reuse for upstream dedup.** Different concern (upstream feed dedup is in `feeds/dedup.py`); don't conflate decision-path correlation with intake-path dedup.
- **Per-source headline-correlation windows.** Single global window is sufficient. Per-source windows are over-engineering.
- **Cross-series-different-headline correlation** (e.g., two distinct headlines about the same event firing on different series). Out of scope; addressing it requires semantic similarity, which is §3.3 territory and beyond Lever C's design space.
- **`PROFIT-LLM-001`** signal-analyzer LLM unification. Outside EDGE-004 entirely.
