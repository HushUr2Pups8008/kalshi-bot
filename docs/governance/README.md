# Governance Operator Manual

This manual documents the runtime-overrides plumbing built in Phase 1 of
the LLM governance agent project. Phase 1 ships *infrastructure*; the
agent itself comes in Phase 2+. Until then, this file is a guide for
**human-edited overrides**: how to disable a source or keyword on a
running bot without restarting it.

## What this is

`data/runtime_overrides.yaml` is a YAML file the bot reads every 10
minutes (currently hardcoded; future versions may expose an env var).
Anything in its `applied` section overrides or augments the static
config in `config.py`.

The bot reads ONLY the `applied` section. The `proposed` section is a
human-review queue used by the future agent (Phase 2+) to write
shadow-mode decisions.

## Schema

Full reference: `docs/superpowers/specs/2026-04-24-llm-governance-agent-design.md` §6.

Top-level fields: `version` (int, must be 1), `updated_at` (ISO 8601),
`updated_by` (string), `mode` (`shadow` or `real`).

Within `applied`:
- `disabled_sources` — list of source-name overrides
- `disabled_keywords` — list of keyword overrides
- `threshold_overrides` — list of (path, value) tuples

Each entry has: `reason`, `confidence`, `decided_at`, `decided_by`,
`decision_id`, `expires_at` (or null), `predicted_effect` (mandatory).

For human-edited entries, use:
- `decided_by: "human-edit-by-jake"` (or your name)
- `decision_id: "gd_YYYY-MM-DD_HHMM"` — must match regex `^gd_\d{4}-\d{2}-\d{2}_\d{4}$`

## How to disable a source manually

1. Open `data/runtime_overrides.yaml` (create if missing).
2. Add an entry to `applied.disabled_sources`:

   ```yaml
   applied:
     disabled_sources:
       - source: "r/SomeSubreddit"
         reason: "stalling the pipeline; revisit after Phase 2"
         confidence: 1.0
         decided_at: "2026-04-24T22:30:00+00:00"
         decided_by: "human-edit-by-jake"
         decision_id: "gd_2026-04-24_2230"
         expires_at: null
         predicted_effect:
           metric: "manual_intervention"
           baseline: 0
           predicted_post_change: 0
           evaluate_at: "2026-05-01T22:30:00+00:00"
   ```

3. Save. Within 10 minutes, the bot's poll task will reload the file,
   log the diff to `bot.log`, and stop polling that source on the next
   cycle.

## How to disable a keyword

Same pattern, but in `applied.disabled_keywords`. The runtime entry's
`keyword` value matches against the bot's `GEOPOLITICAL_SIGNALS`
keyword lists (case-sensitive). Disabling here means the keyword is
skipped during scoring even though it remains in the static config.

## How to override a per-source freshness threshold

The bot's static `EARLY_MAX_NEWS_AGE_BY_SOURCE` map sets a per-source
"max age" that drops stale items at ingestion. Runtime threshold
overrides take precedence:

```yaml
applied:
  threshold_overrides:
    - path: "EARLY_MAX_NEWS_AGE_BY_SOURCE.IAEA"
      value: 21600  # seconds; 6h
      reason: "IAEA cadence is multi-hour; 5-min window starves the source"
      confidence: 0.71
      decided_at: "2026-04-24T22:30:00+00:00"
      decided_by: "human-edit-by-jake"
      decision_id: "gd_2026-04-24_2231"
      expires_at: null
      predicted_effect:
        metric: "iaea_match_passthrough_rate"
        baseline: 0.0
        predicted_post_change: 0.30
        evaluate_at: "2026-05-08T22:30:00+00:00"
```

The path uses dotted notation: `<MAP_NAME>.<SOURCE_NAME>`.

## How to verify

Run: `python -m utils.runtime_overrides --status`

This prints the currently-loaded state from `data/runtime_overrides.yaml`.

To validate a YAML file before saving (without affecting the live bot):

```
python -m utils.runtime_overrides --validate /path/to/edited.yaml
```

This loads the file in isolation, validates schema, and reports
"valid: ..." on success or "INVALID: ..." with a path-prefixed error
on failure. Exit code 2 on failure makes it scriptable.

## Emergency intervention

### Kill switches

Two env vars halt the (future) governance agent:

- `GOVERNANCE_DISABLED=true` — agent exits cleanly, writes nothing.
- `GOVERNANCE_READONLY=true` — agent runs but does not write to the
  overrides file.

Set in your shell or in `.env` as needed. **Bot's behavior is not
affected by these env vars** (the bot just reads whatever is in the
YAML file). The kill switches will become operational when Phase 2+
ships the agent itself.

### Reverting an agent batch

When the Phase 2+ agent writes a batch, it records the `batch_id` in
`last_applied_batch`. To roll back the entire batch:

```
python -m utils.runtime_overrides --revert-batch gb_YYYY-MM-DD_NNNN
```

This drops every override in that batch and clears the
`last_applied_batch` field. Effective on the next bot poll cycle.

### Manual edit during emergency

Editing `data/runtime_overrides.yaml` directly is fully supported. The
bot's reader treats human-written entries identically to agent-written
entries. The atomic-rename semantics in `atomic_write_state()` protect
you from half-written-file races even if you save while the bot is
reading; if you edit by hand with a normal editor, save the file
atomically (most editors do this; vim and emacs do).

## Compatibility with observation windows

During any active P2.x or S4.5x observation window in `docs/ROADMAP.md`
that has a no-change-scope discipline, **do not** edit
`runtime_overrides.yaml`. The runtime overrides count as runtime
behavior changes for the purposes of those windows.

When governance Phase 2+ is operational, set `GOVERNANCE_DISABLED=true`
for the duration of the observation window so the agent doesn't
accidentally invalidate the measurement.

## What was wired in Phase 1

Production-path call sites that now consult the runtime reader:

- `main.py:_is_disabled_news_source()` — feeds source filter at ingestion
- `main.py:_early_max_news_age_seconds_for_source()` — per-source freshness threshold
- `feeds/subreddit_selector.py:_is_disabled_reddit_source()` — subreddit filter
- `feeds/gdelt_monitor.py` — GDELT-feed start gate
- `analysis/signal_analyzer.py` — three keyword-iteration sites
  (`_count_matched_signal_groups`, `_keyword_score`, `_keyword_contributions`)

Without a `data/runtime_overrides.yaml` file present, all of these
fall back to static-config-only behavior — i.e., identical to
pre-Phase-1.
