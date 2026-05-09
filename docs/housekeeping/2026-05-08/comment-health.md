# Comment Health Audit — 2026-05-08

**Scope:** `analysis/` and `feeds/` (all `.py` files)  
**Excluded:** `/trading`, `/tests`, archives  
**Auditor:** Comment Analyzer Agent  
**Audit date:** 2026-05-08  

---

## Inaccurate

Comments or docstrings whose stated behavior contradicts the implementation.

| File:Line | Excerpt | Severity | Recommended Action |
|-----------|---------|----------|--------------------|
| `analysis/evidence_scorer.py:48` | `"""Jaccard similarity over word trigrams …"""` | **High** | Change "trigrams" to "bigrams". `_NGRAM_SIZE = 2` — the function computes **bigrams**. Anyone reading this docstring to understand the dedup threshold (`NGRAM_OVERLAP_THRESHOLD`) will calibrate expectations for the wrong n-gram size. |
| `analysis/__init__.py:19` | `capped_dollars: float  # after $50 hard cap` | **High** | Remove the dollar figure. The cap is not fixed — `kelly.py` applies `cfg.dynamic_max_bet(notional)` (percentage of bankroll). A hardcoded "$50" will diverge from reality every time bankroll or config changes, and the CLAUDE.md critical-gotchas section explicitly calls out `MAX_BET_HARD_CAP` / `dynamic_max_bet` as load-bearing. |
| `analysis/kelly.py:159` | `Rounds down to stay within budget.` | **High** | Fix docstring to reflect actual behavior: `max(1, int(...))` returns 1 when `dollars < price_dollars`, which **exceeds** the budget rather than rounding down to zero. Accurate description: "Returns at least 1 contract; may exceed `dollars` when a single contract costs more than the budget." |

---

## Stale

Comments that were accurate at the time of writing but now contradict current state.

| File:Line | Excerpt | Severity | Recommended Action |
|-----------|---------|----------|--------------------|
| `analysis/signal_analyzer.py:547–552` | `v0.29.48 (P0-GATE / P0.4 experiment) … Revert if the 12h re-run … shows no drop from the 98.99% baseline anchor rate.` | **Medium** | The P0.4 experiment was committed 2026-04-24 and ROADMAP records it as closed. The "revert if" clause implies a pending decision that was already made. Either delete the comment block entirely (the implementation is now permanent), or replace it with a short rationale note: "Price removed from LLM prompt per P0.4 verdict (2026-04-24): anchoring bias exceeded utility." |
| `analysis/fade_signal.py:10–13` | `2. Price fade … — WebSocket-based **replacement**. … no Twitter dependency required.` | **Medium** | Both strategies are active simultaneously in `main.py` (lines 876–1008). "Replacement" is wrong — tweet fade runs via `FADE_TWEET_FEED_URLS` in parallel with price fade. Change "replacement" to "complement" or "additional strategy" and remove the "no Twitter dependency required" phrase, which implies tweet fade was retired. |
| `analysis/regime_classifier.py:178` | `series_ticker can be empty … — see lessons.md` | **Medium** | `lessons.md` does not exist anywhere in the repository (confirmed via `find`). The cross-reference is a dead link. Either inline the one-line rationale here or update the reference to the correct document (likely `docs/IMPLEMENTATION_CONTRACT.md` or a CLAUDE.md critical-gotcha entry). |
| `analysis/signal_analyzer.py:37` | `"""Optional debug-only extraction trace hook for Cycle-15B diagnostics."""` | **Low** | "Cycle-15B" is two cycles stale (current: 17C). The function remains useful but the cycle label makes it look like abandoned diagnostic scaffolding. Replace with a timeless description, e.g., `"""Extraction trace hook: logs intermediate state when KALSHI_EXTRACTION_TRACE=1."""` |

---

## Incomplete

Comments that omit important behavior, edge cases, or side effects a caller needs to know.

| File:Line | Excerpt | Severity | Recommended Action |
|-----------|---------|----------|--------------------|
| `analysis/market_specificity.py:71` | `# Snapshot of analysis/market_matcher._GEO_NAMED_ENTITIES at 2026-04-24.` | **Low** | The comment acknowledges the snapshot but there is no mechanism to detect drift. Add a note stating the maintenance obligation: "Must be kept in sync manually when `market_matcher._GEO_NAMED_ENTITIES` changes." Consider adding a `# TODO: replace with import or assertion` if the duplication is intended to be temporary. |

---

## Low-value

Comments that only restate the code without adding context, rationale, or caveats.

No low-value block comments were found in scope that met the threshold for a finding. The files in `feeds/` and the remaining `analysis/` modules contain comments that describe rationale rather than restating syntax.

---

## Patterns Observed

1. **Stale implementation snapshot (3 of 8 findings):** A comment accurately described the code at commit time but was not updated when behavior changed — a fixed dollar cap became dynamic, a trigram function became bigram, and an experimental removal became permanent. The common failure mode: implementation changes without a co-located comment update.

2. **Dead cross-references (2 of 8 findings):** `lessons.md` (does not exist) and the P0.4 "revert if" condition (decision already made) both reference external state that is gone. Neither causes a runtime error, so they survive indefinitely unless a reader actually follows them.

3. **No formal TODO/FIXME debt older than 90 days.** Zero `# TODO` or `# FIXME` markers were found in the audited files. The P0.4 experiment comment (lines 547–552) is the closest analog — it implies a pending decision — but it is not a formal marker and is only ~14 days old.

---

## Summary

- **Highest-severity finding:** `analysis/evidence_scorer.py:48` — docstring says "word trigrams" but `_NGRAM_SIZE = 2` means the function computes bigrams. Any caller reasoning about dedup sensitivity from the docstring will calibrate for the wrong n-gram size.
- **Most common pattern:** Inaccurate docstrings left behind when implementations changed — the $50 hard cap, the "rounds down" contract sizing, and the trigram similarity were all accurate at some earlier commit and never updated.
- **Stale TODOs older than 90 days:** 0. No formal `TODO`/`FIXME` markers exist in the audited scope. The P0.4 experiment comment is the closest analog but is informal and under 90 days old.
