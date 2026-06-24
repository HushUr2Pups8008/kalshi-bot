# Polymarket Discovery Strategy

This strategy replaces politics-only discovery with a conservative public-context filter.

## Default Scope

- Keep paper-only Polymarket runtime gated by existing Polymarket enablement and paper-mode flags.
- Keep `politics` category markets eligible as the cold-start lane.
- For non-politics categories, require all of:
  - active/open binary market with executable Yes/No prices,
  - nonzero liquidity signal from volume or open interest,
  - relevant public tags, event title, series title, or category terms,
  - explicit resolution source.

## Public Context Used

- Market title, question, subtitle, and description.
- Event title/slug and series title/slug.
- Public tags.
- Resolution source.
- Public comments when present in the payload.

## Excluded By Default

- Sports, culture, macro, and generic trend markets without a relevant event/tag/series signal.
- Any non-politics market without a resolution source.
- Any non-politics market with no volume/open-interest signal.

## Verification

- `tests/polymarket/test_paper_runtime.py::test_cached_candidate_markets_excludes_suppressed_polymarket_categories`
- `tests/polymarket/test_paper_runtime.py::test_market_match_text_includes_polymarket_public_context_fields`
- `tests/test_polymarket_normalizer.py::test_normalizes_binary_market_payload`
