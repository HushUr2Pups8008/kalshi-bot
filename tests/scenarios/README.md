# I-6 Scenario Suite — Adversarial Regression Catalog

Per `docs/superpowers/specs/2026-05-23-paper-mode-rapid-learning-framework-design.md` §3 I-6.

This directory contains hand-curated JSONL scenarios that exercise the bot's
decision pipeline against known regression risks. Each scenario carries an
expected `(decision, side, magnitude)` triplet that a future T1 deploy MUST
preserve.

## Categories

| Category | File | Coverage |
|---|---|---|
| FISA burst | `fisa_burst.jsonl` | Same-series rapid-fire events; exercises EXEC-002 series-correlation guard (`tasks/blend_task.py` + `config.py:series_correlation_window_seconds`). |
| Suppression edge cases | `suppression_edge_cases.jsonl` | Negation, hedging, ambiguity, tense mismatch, conditional clauses; exercises MATCH-001 (B') token-guard refinement and signal_analyzer downgrade logic. |
| Keyword direction flip | `keyword_direction_flip.jsonl` | Reversal verbs, double negatives, polarity invariants — entities whose keyword map yields opposite direction vs the headline content. |
| Anchor-rate polarity | `anchor_rate_polarity.jsonl` | qwen3 governance LLM inputs that pin the `governance/prompts.py:27-31` polarity block. Filed under PROFIT-GOV-002. Also pins the PROFIT-GOV-001 `think=False` boundary. |

## Scenario row schema (JSONL)

Each line is a JSON object with the following fields:

| Field | Required | Description |
|---|---|---|
| `scenario_id` | yes | Globally unique, lowercase-snake. Prefix matches category. |
| `category` | yes | One of `fisa_burst`, `suppression_edge_cases`, `keyword_direction_flip`, `anchor_rate_polarity`. |
| `description` | yes | Single-sentence summary of what this scenario asserts. |
| `pipeline` | yes | One of `blend_task`, `signal_analyzer`, `governance`, `matcher`. Routes the scenario to the right runner stub. |
| `inputs` | yes | Object — scenario-specific. See per-category notes below. |
| `expected` | yes | Object with `decision`, `side`, `magnitude`. Use `null` for fields not under test. |
| `notes` | no | Free-form operator memo. Cite gotchas, ticket IDs, or related cycles. |
| `added_at_utc` | yes | ISO 8601 UTC timestamp at addition. |
| `added_by` | yes | `operator` or named agent (e.g. `codex-i6-implementer`). |
| `memo_ref` | yes | Free-form reference to the change packet that justified the addition. |
| `xfail_reason` | yes | `null` if the scenario asserts current production correctness; non-null string if the assertion is aspirational and the test should be `xfail-strict`. Deprecated scenarios use `"DEPRECATED: <reason>"`. |

## Governance (per framework v3 Q3 stamped default)

- **Append-only.** New scenarios are added with an operator memo per addition.
  The `memo_ref` field captures the justifying change packet, cycle, or ticket.
- **Never deleted.** Deprecated scenarios are marked `xfail_reason: "DEPRECATED: <reason>"`
  and left in place. The audit trail must show why a scenario was once thought to matter.
- **Reproducibility.** Every scenario must specify `pipeline`, deterministic `inputs`,
  and an exact `expected` triplet. No randomness, no wall-clock dependency, no
  network calls in scenario inputs.
- **Schema validation is enforced at test discovery.** Missing required fields
  cause `tests/test_scenario_suite.py` to fail fast at collection time.

## Per-pipeline scenario notes

### `blend_task`

Inputs: `events: list[{ts_offset_seconds, headline, ticker, [upstream_blocked]}]`,
optional `window_seconds` overriding `cfg.series_correlation_window_seconds`.
Runner replays events in order against a stub of `_recent_series_enqueues` using
the production `_series_prefix()` helper from `tasks/blend_task.py`. Expected
decision is a string predicate over `enqueued_count`.

### `signal_analyzer`

Inputs: `headline`, optional `market_question`, optional `market_price_yes`,
optional `keyword_hint_direction`. Most rows are currently `xfail` pending
I-4 (replay-as-CI gate) so the full prompt → LLM → output pipeline can be
exercised. Non-xfail rows assert structural properties that can be verified
without a live LLM call (e.g. negation token present in headline forces
magnitude downgrade in the stub interpreter).

### `governance`

Inputs: `action_under_consideration`, `target`, `anchor_rate`, or
`required_literals` / `check` for prompt-text invariants.
Runner inspects `governance/prompts.py` source text to assert the
polarity block at lines 27-31 contains the required literals and
direction mappings. Aspirational live-LLM rows are deferred to I-4.

### `matcher`

Inputs: `headline`, `market_question`, `candidate_keywords`. Asserts
whether MATCH-001 (B') token-guard would refuse or accept the pairing.
Currently all rows are `xfail` pending I-4 wiring to the live
`analysis/market_matcher.py` token-guard.

## Minimum coverage

Per spec: 3-5 scenarios per category, ≥1 actively-asserted (xfail_reason null)
and ≥1 aspirational allowed per category. Current count:

| Category | Total | Asserting | Aspirational |
|---|---|---|---|
| fisa_burst | 5 | 4 | 1 |
| suppression_edge_cases | 5 | 3 | 2 |
| keyword_direction_flip | 4 | 1 | 3 |
| anchor_rate_polarity | 5 | 5 | 0 |

## Forward integration

Once I-4 (replay-as-CI gate) ships, the `xfail` rows in this directory
become real gates: each T1 deploy candidate must pass the full scenario
suite as a precondition. The aspirational rows pin the post-fix expected
behavior so the assertions stay correct across the gate flip.
