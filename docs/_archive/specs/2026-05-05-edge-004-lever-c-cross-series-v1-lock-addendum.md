# PROFIT-EDGE-004 Lever C — v1 LOCK addendum (INV-6 boundary + Codex implementation guidance)

**Status:** spec addendum (pins v1 implementation choices; fixes ambiguities surfaced by Wave-3 prep).
**Authority:** Implementation Contract §5 + §11 — adding a new gate-adjacent decision-path (BlendTask suppression by headline hash) requires explicit ambiguity resolution before Codex implements per §9.
**Parent spec:** `docs/superpowers/specs/2026-05-03-edge-004-lever-c-cross-series-headline-correlation-design.md`
**Drafted:** 2026-05-05.
**Empirical anchor:** Codex `cross_series_headline_overlap_audit.py` (commit `4f98943` series); 49.2 % normalized-hash overlap on 13-day archive.

## TL;DR

Locks v1 implementation: §3.2 normalized-string hash; `cross_series_correlation_window_seconds = 3600` default; suppression INSIDE BlendTask before executor enqueue; INV-6 boundary attested. Lifts the implicit harness-coverage gap by enumerating the 6 cases Codex's harness expansion must pin. Deploy stays Wave-3 (≥ 2026-06-06) gated on Lever A + Lever B verdicts.

## What this addendum does

| change | parent spec section | addendum action |
|---|---|---|
| hash function | §3.2 v1 | **LOCK** — exact normalization regex per parent spec §3.2; no broadening |
| window default | §2 ("default 1 h") | **LOCK at 3600 s** primary; `cross_series_correlation_window_seconds = 0` disables |
| gate placement | §2 (BlendTask) | **LOCK in BlendTask** — fires AFTER the same-series EXEC-002 check, BEFORE `_emit_blend_decision` |
| INV-6 boundary | (not addressed in parent) | **NEW: attest C does not soften INV-6** — see §1 below |
| harness coverage | parent §11 not present | **ENUMERATE** the 6 required xfail cases — see §3 |
| deploy gating | §1 ("earliest 2026-06-20") | **NO CHANGE** — still Wave-3 + Lever A/B verdicts |

## 1. INV-6 boundary attestation (Implementation Contract §1)

**INV-6 (no uncontrolled increase in trade frequency):** "Adding new signal lanes must not increase raw trade frequency without proportional increase in signal quality."

Lever C is a **suppression** lever — it strictly reduces trade frequency by emitting `SKIPPED.reason="cross_series_headline_in_window"` for candidates that would otherwise produce parallel cross-series trades on a single headline. Therefore:

- **Direction of impact:** trade frequency ↓ (monotonically). INV-6 not violated.
- **Selectivity (INV-7):** preserved. The first candidate carrying a given headline still trades; only the parallel cross-series duplicates suppress. The fast-lane signal that triggered the first trade is unmodified.
- **Trade Readiness Gate (§5):** untouched. Lever C suppresses BEFORE the candidate reaches `evaluate_readiness`; gate conditions G1-G6 unchanged.
- **Executor contract (§7):** untouched. Suppressed candidates never reach the executor; `signal_meta` shape preserved for unsuppressed candidates.

**Verdict:** Lever C lands within Implementation Contract §1 invariants. No INV revisit required.

## 2. Locked v1 implementation values

```python
# config.py — POST-Lever-C-deploy state
@dataclass
class Config:
    ...
    cross_series_correlation_window_seconds: int = field(
        default_factory=lambda: int(os.getenv("CROSS_SERIES_CORRELATION_WINDOW_SECONDS", "3600"))
    )

# tasks/blend_task.py — pseudo-flow per parent spec §2
# (Codex implements; this addendum locks the gate placement)
def _evaluate_blend(...):
    # 1. existing same-series EXEC-002 check (Wave-1)
    if _series_correlation_in_window(...): emit_skipped("series_correlation_in_window"); return
    
    # 2. Lever C cross-series headline check (NEW, Wave-3)
    headline_hash = _headline_hash(news_item.headline)  # parent spec §3.2 normalized
    last_seen = self._recent_headline_enqueues.get(headline_hash)
    if last_seen is not None:
        elapsed = time.monotonic() - last_seen
        if elapsed < cfg.cross_series_correlation_window_seconds:
            emit_skipped("cross_series_headline_in_window"); return
    
    # 3. existing readiness-gate evaluation (unchanged)
    blend_result = _build_blend_result(...)
    candidate = evaluate_readiness(blend_result)
    if candidate is None: return  # already emits BLOCKED via OBS-003 SKIPPED stream
    
    # 4. record headline-hash enqueue AFTER successful gate pass
    self._recent_headline_enqueues[headline_hash] = time.monotonic()
    submit_to_executor(candidate)
```

**Critical placement detail:** record the headline-hash AFTER the readiness gate passes (step 4), NOT at step 2 entry. Otherwise a candidate that fails G1-G6 still pollutes the `_recent_headline_enqueues` dict and suppresses subsequent legitimate cross-series candidates that WOULD have passed the gate. Parent spec §2 is silent on this; this addendum locks it.

**Window default 3600 s justification:** matches EXEC-002 same-series window; minimizes operator cognitive load (one window value across both correlation gates). If post-deploy attribution shows 3600 s is wrong, follow-up spec adjusts.

## 3. Required xfail-strict harness cases (Codex implementation gate)

`tests/test_lever_c_cross_series_correlation.py` already pre-loaded (per existing harness inventory). Codex's harness expansion must add the following xfail-strict cases that pin the LOCK invariants:

```python
# test 1 (already present): cfg.cross_series_correlation_window_seconds exists, default 3600

# test 2 (NEW): hash function matches parent spec §3.2 exact normalization
@pytest.mark.xfail(strict=True, reason="LOCK: §3.2 normalized hash lands in Wave-3")
def test_headline_hash_normalizes_per_parent_spec_section_3_2():
    """Hash matches parent spec §3.2 regex set; cosmetic variants collide."""
    from tasks.blend_task import _headline_hash
    h1 = _headline_hash("Trump signs bill - VitalLaw.com")
    h2 = _headline_hash("trump signs bill")  # case + suffix-strip + punct-off
    assert h1 == h2

# test 3 (NEW): suppression fires within window
@pytest.mark.xfail(strict=True)
def test_cross_series_suppression_within_window():
    """Two candidates with same headline_hash within 3600 s: 2nd suppressed."""
    # constructs candidate A (KXTRUMPIRAN), enqueues; candidate B (KXMOCTRUMP25) at +30 min same headline; assert B emits SKIPPED.reason="cross_series_headline_in_window"

# test 4 (NEW): no suppression past window
@pytest.mark.xfail(strict=True)
def test_cross_series_suppression_releases_after_window():
    """Past 3600 s: 2nd candidate same headline passes."""

# test 5 (NEW): hash recorded only after gate pass (LOCK §2 placement detail)
@pytest.mark.xfail(strict=True)
def test_headline_hash_recorded_after_gate_pass_not_at_entry():
    """Candidate A fails G1; candidate B same headline 5 min later must NOT suppress."""

# test 6 (NEW): window=0 disables (env-revert path per parent spec §6)
@pytest.mark.xfail(strict=True)
def test_window_zero_disables_suppression():
    """cfg.cross_series_correlation_window_seconds = 0: gate fires never."""
```

**Strict-xfail rationale:** all 6 cases must xpass simultaneously on the Wave-3 deploy commit. Strict-xfail catches partial deploys (e.g., gate logic landed but window-zero disable forgotten).

## 4. Acceptance criteria (this addendum)

This addendum is satisfied when:

1. The 5 NEW xfail cases (test 2-6 above) land in `tests/test_lever_c_cross_series_correlation.py` as strict-xfail. Codex task per next 10+10 cycle.
2. `wave-2-wave-3-changelog-entries-prestaged.md` Wave-3 changelog block lists the harness file under "Removed `pytest.mark.xfail` markers (deploy commit)".
3. Parent spec `2026-05-03-edge-004-lever-c-cross-series-headline-correlation-design.md` §11 (if present) or a new §11 is updated with: "v1 implementation choices LOCKED 2026-05-05 per `2026-05-05-edge-004-lever-c-cross-series-v1-lock-addendum.md`."

This addendum does NOT authorize the prod-code change. Wave-3 territory.

## 5. Out of scope

- **§3.3 token-Jaccard hash.** Re-evaluated only if §3.2 over-collapses in production. Separate spec.
- **Per-market-class window tuning.** Single global 3600 s default; per-class is over-engineering pre-evidence.
- **Cross-series CALIBRATION_CHECK consumer.** Suppressed candidates don't produce trades, so no calibration record. If suppression-rate analysis is needed, separate observability event (`CROSS_SERIES_SUPPRESSED`) — not part of v1.

## 6. Cross-links

- `docs/superpowers/specs/2026-05-03-edge-004-lever-c-cross-series-headline-correlation-design.md` — parent spec
- `docs/_archive/governance/2026-05-03-cross-series-headline-overlap-audit.md` — Codex's 49.2 % empirical anchor
- `docs/superpowers/specs/2026-05-03-exec-002-series-correlation-guard-design.md` — Wave-1 same-series guard (Lever C builds on this)
- `tests/test_lever_c_cross_series_correlation.py` — pre-loaded harness (Codex expands per §3 above)
- `docs/IMPLEMENTATION_CONTRACT.md` §1 (INV-6) + §5 + §11 — authority basis
