# CLAUDE.md — kalshi-bot

Global rules (engineering philosophy, Windows platform, versioning, task management,
collaboration style) live in `~/.claude/CLAUDE.md` and apply here automatically.
This file contains only kalshi-bot-specific rules.

---

## Plan Mode Default
- Enter plan mode for any non-trivial task (3+ steps or architectural decisions)
- If something goes wrong, STOP and re-plan immediately -- don't keep pushing
- Use plan mode for verification steps, not just building

## Subagent Strategy
- Use subagents frequently to keep the main context window clean
- Offload research, exploration, and parallel analysis to subagents
- Assign one task per subagent for focused execution

## Self-Improvement Loop
- After any correction, update `tasks/lessons.md` with the pattern
- Review `tasks/lessons.md` at the start of each session

## Verification Before Done
- Never mark a task complete without proving it works
- Ask yourself: "Would a staff engineer approve this?"
- Run tests, check logs, and demonstrate correctness

## Task Management
- Plan First: write a scoped plan in `tasks/todo.md`
- Verify Plan: re-read before starting; get explicit user go-ahead
- Track Progress: mark items complete as you go
- Maintain the Todo File: move completed work to `tasks/completed.md`; keep todo.md lean

---

## Windows Encoding — Project Reminder

Non-ASCII in log/print strings silently kills Windows logging (see global CLAUDE.md).
Pre-commit grep for this project:
```bash
grep -Pn '[^\x00-\x7F]' feeds/*.py analysis/*.py trading/*.py kalshi/*.py trading/*.py main.py
```

---

## Kalshi API

- **Signing:** RSA-PSS/SHA-256, `salt_length=DIGEST_LENGTH`. Never use HMAC or PKCS1v15.
- **REST base:** `https://api.elections.kalshi.com/trade-api/v2`
- **WebSocket:** `wss://api.elections.kalshi.com/trade-api/ws/v2`
- **Market status:** API returns `"active"` for tradeable markets, not `"open"`.
- **PEM key in .env:** Stored as single line with literal `\n`. `_normalize_pem()` converts.
  Do not remove this function or change how the key is loaded.

### WebSocket Header Kwarg — Version-Dependent
The `websockets` library has renamed the custom header kwarg across versions:
| Version | Kwarg |
|---------|-------|
| < 10 | `extra_headers` |
| 10-11 | `additional_headers` |
| 12-13 | `extra_headers` |
| 14+ | `additional_headers` |

Hardcoding either name silently drops auth headers. Version is detected at import:
```python
_ws_ver = tuple(int(x) for x in websockets.__version__.split(".")[:2])
_WS_HEADER_KWARG = "additional_headers" if _ws_ver >= (14, 0) else "extra_headers"
```
Never revert to a hardcoded name.

---

## Market Discovery

- Do NOT use `KALSHI_GEOPOLITICAL_SERIES` allowlist -- obsolete, zero open markets.
- Current approach: fetch all ~9k series, keyword-match titles, apply sports blocklist.
- Sports blocklist must check both `series_ticker` AND market `ticker` prefix.
- `KXTRUMPSAY`, `KXCBDECISION`, `KXAPRPOTUS` are blocklisted -- do not remove.

---

## LLM / Signal Analysis

- Do NOT blend LLM probability with keyword scores. LLM result is used directly.
  Keywords are a gate (does this news relate to this market?), not a probability input.
- LLM outputs categorical JSON: `relevant`, `new_info`, `direction`, `magnitude`, `confidence`.
- Multi-position guard must query ALL open trades for a ticker, not just the most recent.
- Use `json.JSONDecoder().raw_decode()` for JSON extraction -- never greedy `{.*}` regex.

---

## Go-Live Safety

- Paper trading mode is the default. Live requires `python main.py --go-live` + `CONFIRM`.
- Mac and Windows share the same Kalshi API key. Only ONE instance in live mode at a time.
- Stop the old instance before starting live on a new machine.
