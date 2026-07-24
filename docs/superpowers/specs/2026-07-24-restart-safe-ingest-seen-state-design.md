# Restart-Safe Ingest Seen State Design

## Problem

The RSS and search-news monitors retain deduplication IDs only in process memory. A bot restart therefore replays each feed's current backlog into the central 30-minute freshness gate. The gate correctly rejects old items, but the replay consumes intake capacity and hides the real fresh-item conversion rate.

## Goal

Preserve the monitors' existing bounded SHA-256 link-plus-title deduplication IDs across restarts without changing recency thresholds, market selection, paper/live mode, sizing, or order behavior.

## Architecture

Add feeds/seen_state.py with a small JSON checkpoint helper. Each monitor gets an independent checkpoint below STATE_ROOT / "ingest_seen":

- rss_seen_ids.json, capped at 5,000 IDs.
- search_seen_ids.json, capped at 2,000 IDs.

Each file contains a versioned JSON object with an ordered ids list. The helper accepts only 64-character lowercase SHA-256 IDs, deduplicates entries, retains the newest IDs up to the caller's cap, and atomically writes a temporary sibling file followed by os.replace.

Separate files avoid a cross-monitor read-modify-write race: RSS and search run concurrently but never write the same checkpoint. A missing, malformed, or unreadable checkpoint produces an empty in-memory cache and a warning. This may permit one duplicate replay after corruption, but must never suppress an unknown item.

## Data Flow

1. A monitor starts and loads its own bounded ID cache.
2. Existing poll_feed logic continues to mark each unseen item before invoking its callback.
3. At the successful end of every monitor poll cycle, the monitor checkpoints its cache atomically.
4. A later process starts with the checkpointed IDs, so retained old entries are suppressed before reaching the freshness gate.

The checkpoint happens after the cycle. A crash before the checkpoint can re-deliver an item, which is preferable to losing a fresh item. Existing callback-failure semantics remain unchanged.

## Non-Goals

- Do not lower or otherwise change the 1,800-second source freshness policy.
- Do not use a timestamp watermark; valid out-of-order fresh items must still be admitted.
- Do not change _parse_date fallback behavior in this slice. Missing or malformed timestamps are a distinct fail-closed follow-up.
- Do not alter generic-search provider policy, circuit mode, trading flags, order submission, or capital allocation.

## Verification

Tests must prove:

1. A checkpoint round trip retains insertion order and enforces a caller-supplied cap.
2. Corrupt or malformed persisted state fails open to an empty cache rather than raising or suppressing input.
3. An atomic-write failure leaves a prior valid checkpoint unchanged.
4. Two RSS monitor lifetimes over identical entries invoke the callback only during the first lifetime.
5. A distinct fresh ID after restart is still delivered even if it would be out of publish order.
6. The search-news monitor uses its own state path rather than the RSS path.

## Runtime Acceptance

After merge and restart, verify that the next restart no longer causes a stale-drop burst from the same retained feed items, while fresh-pass events continue and no paper or live orders are enabled.

