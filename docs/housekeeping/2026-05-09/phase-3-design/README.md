# Phase-3 design pass — index

**Date:** 2026-05-09
**Phase:** Phase-3 housekeeping, Stage 3a
**Branch:** `housekeeping/phase-3`
**Test baseline:** 1626 passed, 2 skipped, 116 xfailed

Three ADR-style design docs produced via parallel ECC agents. No code changes in this stage.

## Documents

| Doc | Audit ID | Agent | Implementer stage | Scope |
|---|---|---|---|---|
| [`p1-02-signal-analyzer-decomposition.md`](./p1-02-signal-analyzer-decomposition.md) | P1-02 | code-architect | Stage 3c.1 | Decompose 2 oversized functions in `analysis/signal_analyzer.py` (224 + 263 lines) into 7 helpers |
| [`p1-03-logger-typed-params.md`](./p1-03-logger-typed-params.md) | P1-03 | type-design-analyzer | Stage 3c.2 | Replace 46-kwarg `log_signal_analysis_detail` with `@dataclass(frozen=True) SignalAnalysisDetail` in new `utils/log_records.py` |
| [`oq1-analysis-purity-rename.md`](./oq1-analysis-purity-rename.md) | OQ1 / P2-07 | code-explorer | Stage 3b | Move `analysis/source_credibility.py`, `source_stats.py`, `keyword_stats.py` to new `tasks/stats/` sub-package; one-release shim at old paths |

## Cross-references

### P1-02 ↔ P1-03 conflict (both touch `analysis/signal_analyzer.py:1144, 1193, 1236`)

P1-02 wants to extract `_emit_signal_analysis_log(*, …)` helper that wraps the three `log_signal_analysis_detail` callsites.
P1-03 wants those same callsites to construct a `SignalAnalysisDetail` dataclass and pass it to a one-arg logger.

**Recommendation: swap Stage 3c.1 and 3c.2.** Do P1-03 first.

After P1-03 lands, the three callsites become 3 dataclass-construction blocks each followed by `await write_trade_log_async(trade_log.log_signal_analysis_detail, detail)`. P1-02's helper #7 (`_emit_signal_analysis_log`) then becomes a one-line forwarder, or can be skipped entirely if dataclass construction is left inline.

If implemented in the original order (P1-02 first), helper #7 has to be touched again during P1-03 to swap its signature from 46 kwargs to a single struct — wasted work.

Both designs flag this in their "Open questions" section; the implementer should ask the user at the Stage 3b/3c gate whether to swap.

### OQ1 independence

OQ1 touches `analysis/source_credibility.py`, `source_stats.py`, `keyword_stats.py`, `main.py:68`, `main.py:71`, `trading/paper_trader.py:31`, `tests/test_source_credibility.py`. None of these files are touched by P1-02 or P1-03. OQ1 can land before, after, or interleaved with the P1-02/P1-03 work without conflict.

## Recommended Stage order

| Stage | Item | Rationale |
|---|---|---|
| 3b | OQ1 (analysis purity rename) | Independent of others; mechanical; smallest risk surface; fastest to land |
| 3c.1 (was 3c.2) | P1-03 (logger typed params) | Lands the dataclass that P1-02 will then use |
| 3c.2 (was 3c.1) | P1-02 (signal_analyzer decomposition) | Builds on the dataclass landed in P1-03; helper extractions become cleaner |
| Closure | Debt-log + SUMMARY.md update | Phase-3 closure section |

## Open questions surfaced across all three docs

1. **Stage ordering swap (P1-02 ↔ P1-03):** flagged in both relevant docs. Needs user decision before Stage 3c starts.
2. **Shim lifetime for OQ1:** add a `PROFIT-DEBT-OQ1-SHIM` debt-log entry with target-removal date during Stage 3b commit.
3. **P1-02 micro-commit sequence:** the design proposes 4 micro-commits within Stage 3c (was-3c.1); the Phase-3 prompt expected one commit. User should signal preference at the gate.
4. **P1-03 `method` literal type:** `Literal["llm", "keyword", "keyword_gate"]` annotation is optional polish; defer or include in same commit per user preference.

## Stage-3a deliverable

This README + the three design docs. Stage 3a commit will be:

```
docs(phase-3-design): design pass for P1-02, P1-03, OQ1
```

After commit, user-gate before Stage 3b. User should review all three docs and confirm the recommended stage-order swap (or override it).
