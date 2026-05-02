# CLAUDE.md

## Working Style

- Understand and honor the intent of these local instructions fully: they direct the agent back to the broader global guidance, and that guidance must be followed accordingly rather than interpreted narrowly.
- For non-trivial work, plan first and keep the user informed as scope changes.
- Prefer direct execution once the scope is clear.
- Prefer simple root-cause fixes over temporary patches.
- Use delegation only when the environment supports it and it clearly reduces risk or latency.
- Keep summaries concise and decision-oriented.

## Bug-Fixing Preference

- When given a bug report, diagnose it from concrete evidence such as logs, errors, and failing checks.
- Reduce user back-and-forth where the next safe step is clear.

## Continuous Improvement

- After repeated correction on the same pattern, capture the lesson in the project's preferred tracking system if one exists.
- This project's unified tracking system is `docs/profit_path_debt_log.md`; do not create parallel macOS, logging, S4.5, or architecture debt logs.

See `~/.claude/rules/planning.md` for planning rules.
See `~/.claude/rules/validation.md` for validation rules.
See `~/.claude/rules/git_workflow.md` for git workflow rules.

## Critical Gotchas

Non-obvious constraints that have each cost real debugging time. Treat as load-bearing.

### Kalshi API
- **Signing algorithm is RSA-PSS/SHA-256 with `salt_length=DIGEST_LENGTH`.** Required for both REST headers and the WebSocket HTTP upgrade handshake. HMAC and PKCS1v15 padding both return 401 with a generic error that does not name the algorithm. Never change the signing algorithm.
- **PEM key in `.env` is a single line with literal `\n` sequences.** Windows `.env` cannot store multi-line values reliably. `_normalize_pem()` in `kalshi/rest_client.py` and `kalshi/websocket_client.py` converts `\n` → real newlines before loading. Do not remove this.
- **Market status field is `"active"`, not `"open"`.** Executor must check for both strings. Filtering on `"open"` alone silently skips tradeable markets.

### WebSocket
- **`websockets` library custom-header kwarg alternates by version** (`extra_headers` vs `additional_headers`). `kalshi/websocket_client.py` detects the version at import and picks the correct name. Do not hardcode either — auth headers pass silently into `**kwargs` on the wrong version and never reach the HTTP upgrade.

### Signal analysis
- **Do not blend LLM probability with keyword-derived probability.** Keywords are a *gate* (does this news relate to this market?), not a probability input. Blending previously pushed bets above the edge threshold even when the LLM said `magnitude="none"`.
- **Same-signal guard must query *all* open trades for the ticker**, not just the most recent. When both YES and NO positions exist on a ticker, "most recent" is always the opposite side and allows redundant entries past the guard.
- **LLM JSON extraction must use `JSONDecoder.raw_decode()`** scanning each `{` and keeping the last valid object. Greedy `re.search(r"\{.*\}", ..., re.DOTALL)` grabs from the first `{` through the last `}` and breaks on any preambles containing braces.
- **DB transaction atomicity in `resolve_market()`**: pre-calculate all outcomes, wrap the UPDATEs in `with self._conn:`, credit bankroll exactly once at the end. A crash mid-loop without the context manager permanently corrupts bankroll, which poisons every subsequent Kelly calculation.

### Governance LLM (qwen3 family)
- **Ollama `format=json` + qwen3 thinking returns empty `{}`.** qwen3 chain-of-thought reasoning gets consumed by the JSON grammar constraint. Pass top-level `think: False` in the Ollama generate-request payload (sibling of `format`, not nested under `options`). The `/no_think` *prompt* directive does NOT fix this — only the server-side `think` parameter. `governance/llm.py:LocalQwenLLM.complete` carries this; do not remove it. Filed as `PROFIT-GOV-001` (closed 2026-05-02).

### Infrastructure
- **Python 3.14 on Windows requires `aiohttp>=3.10.0`.** `aiohttp==3.9.5` has no cp314 wheel. Do not pin aiohttp to 3.9.x.
- **Concurrent Mac + Windows instances on the same network trigger Reddit 403s.** Reddit sees the combined request rate from the shared external IP. Only one instance per external IP — stop the old before starting the new.
- **Reddit backoff must use an absolute monotonic timestamp**, not a countdown. Countdowns subtracted from by the poll interval go negative, clamp to 0, and provide zero protection. Use `_backoff[x] = time.monotonic() + delay` and compare against `time.monotonic()`.

### Config / env
- **`MAX_BET_HARD_CAP`**, not `MAX_BET_DOLLARS`. The old name silently falls back to default.
- **Bet size is dynamic** via `cfg.dynamic_max_bet(notional)`. Never hardcode a dollar cap — pass the current notional bankroll.
- **`KALSHI_GEOPOLITICAL_SERIES` allowlist is obsolete.** Kalshi retired those series; zero open markets resolve under them. Current approach: fetch all ~9k series, keyword-match titles, apply sports-prefix blocklist. Do not resurrect the allowlist.
