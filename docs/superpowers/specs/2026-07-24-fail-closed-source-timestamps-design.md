# Fail-Closed Source Timestamp Design

## Problem

RSS/search and GDELT monitors currently substitute the local current time when
an upstream article timestamp is missing or cannot be parsed. That fabricated
timestamp bypasses the central freshness gate and makes an old article appear
current.

## Decision

RSS `_parse_date` and GDELT `_parse_seendate` will return `None` when no valid
source timestamp is available. Valid timestamps retain their current parsing,
UTC normalization, and RSS `updated` fallback behavior.

Each monitor will continue to emit the `NewsItem` with `published=None`. The
existing central gate already records `missing_timestamp` and rejects such
items with the deployed default `EARLY_DROP_IF_NO_TIMESTAMP=true`.

## Scope

- RSS, including search-news because it reuses RSS `poll_feed`.
- GDELT articles.
- Direct parser and propagation regression coverage.

## Non-Goals

- Do not change source-age thresholds, source priorities, dedupe ordering, or
  retry behavior.
- Do not change paper/live mode, sizing, order submission, or market selection.
- Do not change the global `NewsItem` default timestamp because synthetic
  callers intentionally rely on it.
- Do not reject parseable timezone-less source timestamps in this patch.

## Residual Behavior

An entry with invalid metadata is still marked seen before its callback. If a
publisher later repairs the same URL/title metadata, the existing dedupe
semantics will not retry it. This change preserves that behavior rather than
silently expanding the patch's intake and replay surface.
