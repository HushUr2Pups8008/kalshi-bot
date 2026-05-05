# Network / Kalshi API / Ollama outage runbook

**Type:** operator runbook (Claude task per Implementation Contract §9 — operator decision input).
**Drafted:** 2026-05-05.
**Audience:** operator when the bot logs network errors, Kalshi API authentication failures, Ollama connection refused, or RSS feed fetch errors.
**Companion:** `2026-05-05-kill-switch-fire-procedure-runbook.md`; `2026-05-05-mac-studio-dead-bot-reboot-runbook.md`.
**Wall-clock target:** 5-30 min from detection to bot-stable OR confirmed-quarantined.

## Detection

Operator sees one of:

- `tail -50 logs/app/bot.log` shows repeated `aiohttp.ClientConnectorError` / `ConnectionRefused` / `OSError: [Errno 61]`
- `launchctl list | grep com.jake.kalshi-bot` shows non-zero last-exit-code with bot.log ending in network errors
- `bash scripts/bothealth.sh` reports network-side regressions (RSS feed dead; Kalshi API 5xx)
- Operator alert routing surfaces an outage signal (per Codex's `scripts/operator_alert_routing_audit.sh`)

## §1 — Categorize the outage

### Category A: Internet outage (ISP / Mac Studio Wi-Fi / etc.)

Symptoms: ALL outbound network errors. RSS feeds + Kalshi API + Ollama (if remote) all fail.

```bash
# Diagnostic
ping -c 3 1.1.1.1                              # external connectivity
ping -c 3 kalshi.com                           # DNS + Kalshi reachable
ping -c 3 reddit.com                           # alternate destination
```

**Action:** wait for ISP recovery; bot will auto-reconnect when network returns (per `KeepAlive=SuccessfulExit:false` semantics in launchd plist; per `aiohttp` retry logic in feed monitors). Document the outage window in operator log; no code action.

### Category B: Kalshi API specific outage

Symptoms: kalshi.com unreachable OR 5xx responses; other endpoints (1.1.1.1, reddit.com) respond.

```bash
# Diagnostic
curl -I https://api.kalshi.com/trade-api/v2/markets 2>&1 | head -5
curl -I https://kalshi.com 2>&1 | head -5
```

**Action:**
- If 5xx: Kalshi-side issue; wait for recovery
- If 401: signing failure (per CLAUDE.md gotchas — RSA-PSS/SHA-256 with `salt_length=DIGEST_LENGTH`). Investigate `kalshi/rest_client.py` + `kalshi/websocket_client.py`. **Do NOT change signing algorithm; that's a known load-bearing detail.**
- If 403: rate limiting or geo-block; check ISP outbound IP

### Category C: Ollama connection refused

Symptoms: `aiohttp.ClientConnectorError: Cannot connect to host localhost:11434` or similar.

```bash
# Diagnostic
ps aux | grep ollama
launchctl list | grep ollama 2>&1               # if Ollama is launchd-managed
curl -s http://localhost:11434/api/tags         # Ollama reachable?
```

**Action:**
- If Ollama not running: start it (`ollama serve &` or whatever the operator's startup pattern is)
- If running but model not loaded: warm the model (`ollama run qwen3:14b "test"`)
- If model loaded but slow: check Mac Studio memory pressure (Activity Monitor or `vm_stat`)

**Important per cycle-3 baseline:** Codex's LLM throughput audit captured median 1063ms / p90 1986ms. If post-recovery latency is much higher, Ollama may need restart even if alive.

### Category D: RSS feed source outage

Symptoms: specific source (NYT / Reuters / VitalLaw etc.) dead but other sources work.

```bash
# Diagnostic
curl -I -s https://www.nytimes.com/svc/collections/v1/publish/...rss 2>&1 | head -5
# Test each suspect source per config.py:RSS_FEEDS
```

**Action:**
- Single source dead: bot's `feeds/rss_monitor.py` should auto-skip; confirm with `tail logs/app/bot.log` — should log "RSS source X failed; continuing"
- Multiple sources dead: likely Category A (ISP) — re-categorize
- Source returns malformed XML: `feeds/rss_monitor.py` should handle parse errors; investigate if not

### Category E: Reddit 403 (per CLAUDE.md gotcha)

Symptoms: Reddit feeds return 403 across multiple subs.

**Action:** check whether MacBook + Mac Studio bots are running concurrently on same external IP. **Per CLAUDE.md:** "Concurrent Mac + Windows instances on the same network trigger Reddit 403s. Only one instance per external IP — stop the old before starting the new." MacBook should be archive-only post-2026-05-01 cutover; if a stray bot process is running there, kill it.

```bash
# On MacBook (if accessible)
ps aux | grep -i kalshi-bot                    # should be 0 results post-cutover
launchctl list | grep com.jake.kalshi-bot     # should not be loaded
```

## §2 — Decide: WAIT vs INTERVENE vs STOP

| category | recommended action | rationale |
|---|---|---|
| A (Internet outage) | WAIT — bot auto-reconnects | ISP recovery is operator-passive |
| B (Kalshi API 5xx) | WAIT | Kalshi-side issue; bot retries |
| B (Kalshi API 401) | INTERVENE — investigate signing | bug-shape; needs operator |
| B (Kalshi API 403 rate-limit) | INTERVENE — back off / reduce request rate | reactive operator change |
| C (Ollama down) | INTERVENE — start Ollama / warm model | bot can't function without LLM |
| D (single RSS dead) | WAIT — bot should auto-skip | non-load-bearing per source |
| D (multiple RSS dead) | re-categorize to A | likely network issue |
| E (Reddit 403) | INTERVENE — kill stray bot | per CLAUDE.md gotcha |

If outage persists > 4 h with no recovery in sight: STOP the bot per `kill-switch-fire-procedure-runbook.md` §1 to prevent error-spam in logs and to set a clear restart point.

## §3 — Document the incident

Append to operator log (suggest `docs/profit_path_debt_log.md` PROFIT-PHASE2-001 entry if mid-soak; new debt entry if post-Wave-1):

```markdown
##### Network/API outage log — ${UTC_DATE}

**Category:** [A/B/C/D/E]
**Duration:** ${start_UTC} → ${end_UTC}
**Symptoms:** ...
**Action taken:** [WAIT / INTERVENE description / STOP]
**Bot state through outage:** [continued running / dead / quarantined]
**Recovery verified:** ${recovery_UTC}; bothealth.sh clean
```

## §4 — Special cases

### §4.1 Outage during Day-7 close (rare, severe)

If outage fires between Day-7 fire-time pre-flight and `phase2-soak-closed` tag (~30-45 min window):

- **Abort the Day-7 close.** Don't tag.
- Categorize per §1.
- Wait or intervene per §2.
- After bot recovers + is stable for ≥ 30 min: re-attempt Day-7 close per `2026-05-05-day-7-fire-time-compact-checklist.md`.
- If gate-5 (cadence stability max gap ≤ 3h) is now violated due to outage: outage caused soak invariant breach; investigate per §8.5 spec; consider falling through to default 14-day close.

### §4.2 Outage during Wave-1 deploy

If outage fires during Wave-1 commit deploy:

- **Don't restart the bot.** The current commit may be mid-deploy state.
- Categorize per §1; wait for recovery.
- After recovery: re-run smoke wrapper (`bash scripts/wave1_post_deploy_smoke.sh`); if clean, proceed to next commit cadence; if regression, treat as Wave-1 incident per `post-soak-rollback-runbook.md`.

### §4.3 Outage during Wave-2 14-day acceptance window

If outage fires during Branch C 14-day window:

- Document as part of the window's data
- May extend window by outage-duration (operator-discretion)
- Acceptance criteria still apply: ≥ 1 PAPER_TRADE w/ +P&L

## What NOT to do

- **DON'T edit signing logic to "fix" 401s.** RSA-PSS/SHA-256 with `salt_length=DIGEST_LENGTH` is the load-bearing detail per CLAUDE.md.
- **DON'T disable RSS sources permanently** based on a single transient failure. Use `feeds/rss_monitor.py`'s auto-skip semantics; reactive disable is for repeat offenders only.
- **DON'T run two bots on same external IP** to "fail over." Per CLAUDE.md: Reddit 403 cascade.
- **DON'T `git push --force` to revert "outage symptoms."** Outages aren't code-state issues.

## Cross-links

- `CLAUDE.md` §"Critical Gotchas" — Kalshi RSA-PSS signing, websockets header, market status active
- `2026-05-05-kill-switch-fire-procedure-runbook.md` — sibling incident shape
- `2026-05-05-mac-studio-dead-bot-reboot-runbook.md` — sibling incident shape
- `scripts/bothealth.sh` — daily health check (cycle-3 expanded to surface VALIDATION_ERROR + batch_aborted)
- `scripts/operator_alert_routing_audit.sh` — cycle 3 Codex; verifies alert routing
- `kalshi/rest_client.py`, `kalshi/websocket_client.py` — Kalshi API code paths
- `feeds/rss_monitor.py`, `feeds/search_news_monitor.py` — RSS code paths
- `governance/llm.py` — Ollama LLM calls
