# Phase 2 governance shadow-soak — Day-7 mid-soak confirmation (PRE-STAGE skeleton)

**Status:** SKELETON ONLY — generated 2026-05-06 cycle 10 for fire-time fill on 2026-05-07. Numeric placeholders are `<TBD>` until operator (or fire-time agent) populates from live `logs/governance/decisions.jsonl`.
**Target generation:** 2026-05-07 13:00–18:00Z (Day-7 milestone window)
**Soak elapsed at fire:** ~158–163 h (Day 7 of 14 default; Day 7 of 7 under §8.5.1 early-close)
**Soak tracker:** `PROFIT-PHASE2-001`
**Source:** `logs/governance/decisions.jsonl`
**Reviewer:** Claude OR Codex (whoever is on cycle when fire-time arrives)
**Predecessor:** `docs/governance/2026-05-04-day-4-mid-soak-confirmation.md` (model)

## TL;DR

**Day-7 confirmation `<PASS|FAIL>`.** `<N>` fast cycles + `<M>` deep cycles landed in the day-7 UTC window (`<HH>:<MM>Z` → `<HH>:<MM>Z`), all on the 2.0 h fast / 12 h deep cadence, with `<P>` PARSE_ERROR / `<V>` VALIDATION_ERROR / `<K>` KILL_SWITCH / `<B>` batch_aborted. Soak `<healthy / degraded>`; `<close at 2026-05-08 under §8.5.1 / continue to 2026-05-15>`.

## Day-7 cycle list

| cycle_id | started_at | duration_sec | decisions |
|---|---|---:|---:|
| `gc_2026-05-07_<HHMMSS>` | 2026-05-07T<HH>:<MM>:<SS>Z | <D.DD> | <N> |
| `gc_2026-05-07_<HHMMSS>` | 2026-05-07T<HH>:<MM>:<SS>Z | <D.DD> | <N> |
| `gc_2026-05-07_<HHMMSS>` | 2026-05-07T<HH>:<MM>:<SS>Z | <D.DD> | <N> |
| `gc_2026-05-07_<HHMMSS>` | 2026-05-07T<HH>:<MM>:<SS>Z | <D.DD> | <N> |
| `gc_2026-05-07_<HHMMSS>` | 2026-05-07T<HH>:<MM>:<SS>Z | <D.DD> | <N> |
| `gc_2026-05-07_<HHMMSS>` | 2026-05-07T<HH>:<MM>:<SS>Z | <D.DD> | <N> |

Inter-cycle gaps: `<2.00 / 2.00 / 2.00 / 2.00 / 2.00 h>`. Cadence `<honoured / drift detected at <cycle>>`.

Duration outliers: `<list any > 30s, with reason if known>`.

## Cumulative metrics (day-4 confirmation → day-7 confirmation)

| metric | day-4 confirmation (13:10Z, 2026-05-04) | day-7 confirmation (`<HH>:<MM>Z`, 2026-05-07) | delta |
|---|---:|---:|---:|
| total events | 239 | <TBD> | <TBD> |
| cycle starts / ends | 36 / 36 | <TBD> / <TBD> | <TBD> |
| `GOVERNANCE_DECISION` count | 158 | <TBD> | <TBD> |
| distinct targets | 20 | <TBD> | <TBD> |
| PARSE_ERROR | 7 | <TBD> | <TBD> |
| VALIDATION_ERROR | 0 | <TBD> | <TBD> |
| KILL_SWITCH | 0 | <TBD> | <TBD> |
| `batch_aborted=True` | 0 | <TBD> | <TBD> |
| latest cycle | 2026-05-04T11:32Z | 2026-05-07T<HH>:<MM>Z | +<DD.D> h |
| elapsed soak hours | 62.7 | <~158-163> | +<~96-100> |

## Day-7 decisions distribution

`<N>` decisions across `<M>` distinct targets (`<K>` overlap with pre-day-7; `<L>` new targets):

| target | day-7 decisions | action | new vs day-4? |
|---|---:|---|:---:|
| `<target>` | <N> | <action> | <Y/N> |
| `<target>` | <N> | <action> | <Y/N> |
| ... | | | |

`<Pattern note: e.g. "All actions are disable_source against persistent low-engagement Reddit sources, consistent with day-4 pattern.">`

## §8.5.1 close-gate status (8 gates — Day-7 evaluation per `PROFIT-PHASE2-001-early-close-criteria.md`)

| # | gate | mechanism | status |
|---|---|---|---|
| 1 | ≥ 30 GOVERNANCE_DECISION records | `grep -c GOVERNANCE_DECISION logs/governance/decisions.jsonl` | `<PASS / FAIL>` (`<N>` records) |
| 2 | ≥ 7 days continuous shadow-mode runtime | first cycle 2026-05-01T19:01Z → close ≥ 2026-05-08T19:01Z | `<PASS / IN_PROGRESS / FAIL>` (`<DD.D>` h elapsed) |
| 3 | 0 KILL_SWITCH / batch_aborted / VALIDATION_ERROR | full-window grep | `<PASS / FAIL>` |
| 4 | 0 PARSE_ERROR in trailing 72 h | filter `decided_at >= close_ts - 72h` | `<PASS / FAIL>` (`<N>` errors in trailing 72 h) |
| 5 | Cadence stability ±10 % | inter-cycle-gap audit; no gap > 3 h | `<PASS / FAIL>` (max gap `<H.HH h>`) |
| 6 | ≥ 85 % reasonable on manual review | operator decision review | `<PASS / FAIL>` (`<N>/<M>` reasonable) |
| 7 | No mid-soak code change OR §8.5.2 carve-out invoked | `bash scripts/check_soak_invariant.sh` returns 0 OR each surfaced commit has §8.5.2 attestation | `<PASS / FAIL>` (`<N>` commits surfaced; all carved out per attestation file) |
| 8 | Written attestation | `docs/governance/PROFIT-PHASE2-001-early-close-attestation.md` populated + signed | `<PASS / PENDING>` |

`<Overall: all 8 PASS → §8.5.1 close criteria met → close authorised for 2026-05-08. Otherwise: continue to 2026-05-15 (default 14 d floor).>`

## Recommendation

`<Close at 2026-05-08 (§8.5.1 path) / Continue to 2026-05-15 (default path) / Operator decision required>` per the gate-status table above.

Next step:
- If close authorised: operator runs `scripts/pre_soak_close_branch_backup.sh` for rollback anchor; populates attestation file; proceeds to Wave-1 deploy per `post-soak-close-rehearsal-checklist.md` §1.
- If continuing to default 14 d: next mid-soak deliverable is the day-10 / day-13 milestone confirmation.

## Cross-links

- `docs/governance/2026-05-04-day-4-mid-soak-confirmation.md` — day-4 predecessor
- `docs/governance/PROFIT-PHASE2-001-early-close-criteria.md` — §8.5.1 close gates
- `docs/governance/PROFIT-PHASE2-001-early-close-attestation.md` — attestation template (gate 8)
- `docs/governance/post-soak-close-rehearsal-checklist.md` — post-close deploy guide
- `scripts/check_soak_invariant.sh` — gate 7 mechanism
- `scripts/pre_soak_close_branch_backup.sh` — rollback anchor

## Fire-time agent instructions

When this skeleton fires (2026-05-07 13:00–18:00Z window):

1. Read `logs/governance/decisions.jsonl` for the day-7 UTC window (00:00Z → fire-time).
2. Populate every `<TBD>` placeholder with live counts.
3. Run `bash scripts/check_soak_invariant.sh --json` and capture output for gate 7.
4. Run trailing-72h PARSE_ERROR count for gate 4.
5. Manual-review subset (gate 6): per `feedback_soak_confirmation_cadence.md` (cap at 1 confirmation per UTC day per agent), use whatever sample size is honest given the day's review budget.
6. Update TL;DR with PASS/FAIL summary + close recommendation.
7. Rename file from `2026-05-07-day-7-pre-soak-confirmation.md` → `2026-05-07-day-7-mid-soak-confirmation.md` (drop `pre-` prefix once live data lands).
8. If recommendation = close: also populate `PROFIT-PHASE2-001-early-close-attestation.md`.
