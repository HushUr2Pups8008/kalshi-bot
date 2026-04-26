"""kalshi-bot diagnostic scripts.

Each module under this package has a dual life:
- `python -m scripts.<name>` for CLI use (parses argv, writes to stdout)
- `from scripts.<name> import <function>` for library use by the governance
  agent (pure functions returning structured data)

Library functions guaranteed stable for governance/evidence.py:
- scripts.source_market_alignment_audit.aggregate(...)
- scripts.keyword_feedback.summarize(...)
- scripts.reddit_source_audit.collect(...)
- scripts.freshness_diagnostics.summarize(...)
"""
