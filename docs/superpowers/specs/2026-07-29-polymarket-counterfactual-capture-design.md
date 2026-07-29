# Polymarket Counterfactual Capture Design

## Goal

Persist bounded, event-scoped evidence for post-admission Polymarket match
rejections so later conversion work can distinguish no token overlap from
weak, downweighted overlap without changing execution behavior.

## Scope

- Attach one optional nested snapshot to the existing `MATCH_NO_CANDIDATE`
  JSONL event.
- Capture only when `candidate_pool_stage=post_admission_no_match` and the
  matcher found no qualifying candidate before `max_results` slicing.
- Capture up to four admitted rejected markets. Each candidate records public
  identity/title, rejection reason, token counts, and atomic pre/post-weight
  scores when overlap exists. Only counts are retained for news/body-derived
  matching tokens.
- Persist snapshot coverage aggregates in funnel and daily reports.

## Non-Goals

- No change to matching, scoring, token weights, admission horizon, candidate
  limit, routing, paper trading, live trading, or sizing.
- No refetch, second matcher pass, new clock, or duplicate weight-log call.
- No article body, URL, query string, market description, public comments, or
  raw news/body-derived token values in telemetry.

## Event Shape

`post_admission_counterfactual_shadow` is optional and additive:

```json
{
  "schema_version": 1,
  "match_clock_utc": "2026-07-29T09:08:36+00:00",
  "news_headline_token_count": 8,
  "news_match_token_count": 14,
  "candidate_count_total": 2,
  "captured_market_count": 2,
  "omitted_market_count": 0,
  "truncated": false,
  "candidates": [
    {
      "ticker": "...",
      "market_title": "...",
      "rejection_reason": "below_min_post_weight_score",
      "market_token_count": 11,
      "matched_token_count": 1,
      "pre_weight_score": 0.05,
      "post_weight_score": 0.01
    }
  ]
}
```

For `no_token_overlap`, `matched_token_count` is zero and score/weight fields
are absent. `market_without_match_tokens` has zero market and matched-token
counts and no score fields. Candidate titles are optional and, when present,
come only from sanitized `market.title`, never composite market-match text.
Titles are capped at 160 ASCII-safe characters; candidates are capped at four.
Candidate ordering is deterministic by rejection reason, descending post-weight
score where present, then ticker.

## Integrity Rules

- Snapshot does not exist when a qualifying match was found before result
  slicing, including `max_candidates=0` truncation.
- `captured_market_count + omitted_market_count` equals
  `candidate_count_total`. This local denominator is all admitted
  within-horizon markets, including `market_without_match_tokens`, and equals
  the top-level `within_admission_horizon_market_count`.
- Candidate reasons and scores must be consistent with the existing flat
  rejection counts and threshold where applicable. The fixed reason set is
  `market_without_match_tokens`, `no_token_overlap`,
  `below_min_post_weight_score`, and `weight_demoted_below_min_score`.
  `market_without_match_tokens` is a granular subset of the flat
  `no_token_overlap` count, preserving the legacy bucket partition.
- Captured no-overlap reason counts cannot exceed the flat no-overlap count;
  captured below-score and weight-demoted counts cannot exceed their matching
  flat buckets. Score-bearing candidates have post-weight score below the flat
  threshold; weight-demoted candidates have pre-weight score at or above it.
- The builder and logger use a strict field allowlist and reject non-finite
  numeric values before serialization.
- Candidate tickers are at most 128 printable-ASCII bytes. Titles are optional
  and at most 160 printable-ASCII bytes after canonicalization; control and
  bidi characters are stripped. The complete serialized snapshot is at most
  4 KiB UTF-8.
- Invalid optional snapshots are counted as unavailable; they never invalidate
  the existing rejection aggregate or alter routing.

## Validation

Runtime/logger tests cover no-overlap, below-score, weight-demoted, and
candidate-limit truncation. Report tests cover valid snapshots, legacy absence,
and malformed snapshots. The deployed runtime must remain shadow-only with no
orders or reservations after restart.
