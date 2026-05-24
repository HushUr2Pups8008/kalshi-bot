# Pre-registered Corpus Regime Labels

Per the paper-mode rapid-learning framework v3
(`docs/superpowers/specs/2026-05-23-paper-mode-rapid-learning-framework-design.md`)
§3 I-1: regime labels MUST be declared here BEFORE a corpus is built.
`scripts/edge_replay/build_corpus.py` reads this file at build time and
raises `UnregisteredRegimeError` if the caller's `--regime-label` is
absent.

Adding a label requires the same memo cadence as scenario additions
(framework v3 Q3 / Q13): the operator opens an entry in
`docs/profit_path_debt_log.md` describing the regime boundary (what
changed, what timestamp partitions before/after) and the labels in this
table are appended in the same commit.

Treat this file as part of the audit trail for which corpora were
admitted as evidence under which structural assumptions. Removing or
renaming an entry is a load-bearing change — corpora referencing the
old label become un-rebuild-able.

## Active regimes

| Label | Description | First declared |
|---|---|---|
| `pre_p0` | Bot runs before the P0 price-fix sentinel (`bot_state.p0_price_fix_deployed_ts`). Decision-time prices were sourced through the pre-fix path; replay must treat these rows as a different population from post-fix decisions. | 2026-05-23 |
| `post_p0_hotfix` | Bot runs after the v0.30.1 hotfix landed (`2026-05-13T00:02:37Z`). Covers the post-fix-but-pre-v0.30.2 lineage on `main` (hotfix `!14` restored `?status=open`; tag `v0.30.0` remains published-broken and immobile). | 2026-05-23 |
| `post_v030_2_oos_seed` | Bot runs after v0.30.2 release commit landed (`2026-05-23T21:22:16Z`) — matches the OOS corpus seed window emitted by `scripts/edge_replay/oos_corpus_seed.py`. This regime is the canonical OOS holdout the framework's I-4 gate will draw from. | 2026-05-23 |

## How to read this file when extending it

- One row per regime, one regime per row.
- The label cell must be wrapped in backticks (e.g. `` `pre_p0` ``); the
  parser only admits backtick-wrapped first-column tokens.
- Description and date columns are operator-facing; the parser ignores
  their content but reviewers should not.
- Header rows (`Label | Description | First declared`) and separator
  rows (`|---|---|---|`) are skipped by the parser; do not rename the
  header cells.
- Prose, HTML comments, and non-table blocks are ignored. Add as much
  narrative context as the regime needs.
