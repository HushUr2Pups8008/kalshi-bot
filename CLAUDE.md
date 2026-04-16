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

## Portability Guardrail

Before finalizing any infrastructure/runtime change, explicitly check:
- Windows behavior
- macOS behavior
- path handling
- process/locking semantics
- shell/CLI assumptions

Rules:
- Engineering changes must work on both Windows and macOS by default unless the task explicitly scopes otherwise.
- Hide OS-specific behavior behind one platform-aware abstraction with the same external behavior and logs on both platforms.
- Windows-only (`msvcrt`) or Unix/macOS-only (`fcntl`) code is not acceptable unless both paths are implemented.
- Prefer standard-library cross-platform approaches where practical.
- Add tests around platform-boundary logic when feasible.

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

## Git Workflow — Safe Commits

**Always use a review-first, logically-grouped workflow:**

1. Run `git status`, `git diff`, `git diff --staged` before committing
2. Validate changes against project constraints before staging or committing
3. Look for unrelated edits, temp artifacts, debug files, generated files, and suspicious changes
4. Stage files intentionally by logical group (never `git add .` blindly)
5. Verify `git diff --staged` before each commit
6. Write clear commit messages with rationale, not just filenames
7. Run relevant tests or validation before pushing
8. Confirm the working tree is clean and commit history is sensible before push
9. Push only after review is complete

**Safety gates:**
- NO changes to `analysis/`, `trading/`, or core decision logic unless explicitly intentional
- NO temporary files, debug artifacts, or credentials
- NO monolithic commits mixing unrelated concerns unless clearly justified
- NO push without a clean review of `git log`

See global `~/.claude/rules/version_control.md` for detailed workflow and examples.

---

## Changelog

Every version bump requires a `CHANGELOG.md` entry in the same commit. No exceptions.

- Add the new version block at the top, above all prior entries.
- Use the existing format: `## [X.Y.Z] - YYYY-MM-DD` with `### Added / Changed / Fixed / Removed` subsections.
- Write one bullet per logical change: what changed, which file(s), and why (the reasoning that
  drove the version bump). A reader should be able to understand what happened and why without
  reading the diff.
- Never batch multiple version bumps into one entry -- each version gets its own block.

---

## Cross-Platform Workflow

- Prefer Python for operational scripts and report runners
- Do not introduce new `.ps1`-only workflows when a Python entrypoint is practical
- Treat PowerShell wrappers as optional Windows conveniences, not the canonical interface
- When adding or updating tooling, consider the macOS runtime path first
- If a script is currently PowerShell-only but is operationally important, prefer migrating the core logic to Python

--- 

## Time Handling (CRITICAL)

**All system time must be handled in UTC.**

Requirements:
* All timestamps in logs, events, and persisted data must be UTC
* Use ISO-8601 or explicitly marked UTC timestamps
* Do not use local system time for:
  * logging
  * event timestamps
  * comparisons
  * freshness calculations

Logging:
* Application logs (e.g. `bot.log`) must use UTC timestamps
* Structured logs (e.g. `trades.jsonl`) must use UTC timestamps
* If using Python logging, set:
  ```python
  logging.Formatter.converter = time.gmtime
  ```
Conversions:
* Only convert to local time at presentation layer (if ever needed)
* Internal logic must remain UTC-only

Enforcement:
* Mixed timezones are considered a defect
* Any component emitting non-UTC timestamps must be corrected

--- 

## Plan Archiving
After every non-trivial implementation (new feature, architectural change, version bump):
- Archive the plan to docs/plans/vX.Y.Z_short_description.md in the repo
- Include the full plan: Context, Non-Obvious Design Decisions, Verification sections
- Commit the archive in the same commit as the implementation
- These are Architectural Decision Records -- they prevent re-litigating design choices
- Naming convention: docs/plans/vX.Y.Z_short_description_with_underscores.md

---

## Go-Live Safety

- Paper trading mode is the default. Live requires `python main.py --go-live` + `CONFIRM`.
- Mac and Windows share the same Kalshi API key. Only ONE instance in live mode at a time.
- Stop the old instance before starting live on a new machine.
