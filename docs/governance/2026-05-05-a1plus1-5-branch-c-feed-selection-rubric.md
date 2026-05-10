# A.1+1.5 Branch C feed-selection rubric

**Type:** ambiguity resolution (Claude task per Implementation Contract §9 — operator decision input).
**Drafted:** 2026-05-05.
**Audience:** operator picking 1-2 open-RSS legal-analyst feeds at Branch C deploy time (≥ 2026-05-29 if Branch A produces 0 legal-niche PAPER_TRADE in 14 d).
**Companion:** `docs/superpowers/specs/2026-05-04-edge-004-lever-a1plus1-5-legal-analyst-design.md`; `docs/_archive/governance/2026-05-04-lever-a1-plus-1-5-legal-analyst-feed-sizing.md` (Codex's probe-order audit `5e5849a`, ARCHIVED Stream G R6); `docs/governance/2026-05-05-wave-2-a1plus-branch-decision-table.md`.

## TL;DR

Operator picks 1-2 of 4 candidate feeds at Branch C deploy. The rubric below scores each on RSS feasibility, paywall friction, classifier-bucket fit, and historical-overlap with Kalshi market mix. **Recommended primary: Just Security; recommended secondary: Lawfare.** SCOTUSblog and Politico Legal as fallbacks if the primaries fail RSS-probe at deploy time.

## Branch C feed candidates (per Codex's 2026-05-04 probe-order audit `5e5849a`)

The audit ranks legal-analyst feeds by historical PAPER_TRADE concentration on the 13-day Mac archive:

1. **VitalLaw.com** (load-bearing source; 3/3 historical PAPER_TRADE) — **infeasible** per Codex 2026-05-05 direct-RSS probe `a45c06c`. Drop from candidate set.
2. **Just Security** (`https://www.justsecurity.org/feed/`)
3. **Lawfare** (`https://www.lawfaremedia.org/feed/` or `https://www.lawfareblog.com/feeds/all`)
4. **SCOTUSblog** (`https://www.scotusblog.com/feed/`)
5. **Politico Legal** (`https://rss.politico.com/...legal...` — exact URL TBD per probe)
6. **Reuters Legal** (`https://www.reuters.com/legal/rss/` or similar) — last-resort fallback

## Scoring rubric (per candidate)

Each candidate scores 0-3 on six dimensions. Higher = better. Total max: 18.

| dimension | weight | scoring criteria |
|---|---|---|
| **D1 RSS feasibility** | x1 | 3 = `curl -I` returns 200 + RSS content-type at deploy time; 2 = returns 200 + HTML (RSS may need scraping); 1 = returns 401/403 (gated); 0 = dead URL |
| **D2 Paywall friction** | x1 | 3 = full content open; 2 = headline+lede open, body paywalled (still extractable for matcher); 1 = headline-only open; 0 = login-required |
| **D3 Classifier bucket** | x1 | 3 = bucket as `analysis` post-A.1 token addition; 2 = bucket as `news`; 1 = bucket as `other` (worse weight); 0 = mis-bucketed |
| **D4 Kalshi market overlap** | x2 | 3 = high overlap with current Kalshi market mix per domain-overlap audit; 2 = moderate; 1 = low; 0 = none |
| **D5 Historical PAPER_TRADE** | x2 | 3 = any historical PAPER_TRADE on archive; 2 = OPP archive evidence; 1 = MATCH evidence only; 0 = no archive evidence |
| **D6 Operational stability** | x1 | 3 = high-frequency stable site; 2 = moderate frequency; 1 = sparse; 0 = uncertain stability |

Total weighted max: 3×4 + 6×2 = 12 + 6 = **18 points** (D4 + D5 each weighted 2x because they directly drive expected PAPER_TRADE conversion).

## Pre-deploy scoring (operator runs at fire-time)

Per-feed scoring procedure on Branch C deploy day:

```bash
# Step 1: D1 RSS feasibility — probe each URL
for url in \
    "https://www.justsecurity.org/feed/" \
    "https://www.lawfaremedia.org/feed/" \
    "https://www.scotusblog.com/feed/" \
    "https://rss.politico.com/legal.xml"; do
    echo "=== $url ==="
    curl -I -s -o /dev/null -w "HTTP %{http_code} | %{content_type}\n" "$url"
done
```

**Expected output:** at least 2 of the 4 return HTTP 200 + RSS-flavored content-type. If fewer: fall through to Reuters Legal probe + manual feed search.

```bash
# Step 2: D2 paywall friction — fetch first 5KB of feed and check for paywall sentinels
for url in <feeds-from-step-1>; do
    body=$(curl -s "$url" | head -c 5000)
    if echo "$body" | grep -qiE "subscribe|sign in to read|premium content"; then
        echo "$url: paywall sentinel found"
    else
        echo "$url: open"
    fi
done
```

```bash
# Step 3: D3 classifier bucket — verify the post-A.1+ token list buckets each source's title
.venv/bin/python -c "
import main
for title in ['Just Security', 'Lawfare', 'SCOTUSblog', 'Politico Legal']:
    print(title, '→', main._source_class_for_evidence({'source': title}))
"
```

(Re-runs after the operator decides whether to extend `_source_class_for_evidence` token list at Branch C deploy time per A.1+ spec §3.2.)

**Step 4: D4 + D5** — read Codex's `docs/_archive/governance/2026-05-04-legal-niche-probe-order-domain-overlap.md` for domain-overlap rankings; check `docs/_archive/governance/2026-05-04-lever-a1-plus-1-5-legal-analyst-feed-sizing.md` for archive evidence (both ARCHIVED Stream G R6).

**Step 5: D6** — operator judgment based on RSS feed timestamps + post-frequency observation.

## Recommended primary picks (pre-deploy, subject to fire-time validation)

Based on `docs/_archive/governance/2026-05-04-lever-a1-plus-1-5-legal-analyst-feed-sizing.md` Codex audit:

| feed | D1 | D2 | D3 | D4 | D5 | D6 | total |
|---|---:|---:|---:|---:|---:|---:|---:|
| **Just Security** | TBD | 3 (open content) | 3 (analysis bucket post-token-add) | 4 (high overlap; political/legal analyst commentary close to Kalshi market topics) | 4 (OPP archive evidence; some headline matches) | 3 (stable; daily posts) | **17 max conditional on D1=3** |
| **Lawfare** | TBD | 3 | 3 | 4 | 2 (less archive evidence than Just Security) | 3 (stable; daily) | **15** |
| **SCOTUSblog** | TBD | 3 | 3 | 2 (lower overlap; Supreme-Court-specific) | 2 | 3 | **13** |
| **Politico Legal** | TBD | 3 | 2 (more news than analysis bucket) | 4 | 2 | 2 (Politico's RSS structure varies) | **13** |
| Reuters Legal (fallback) | TBD | 3 | 2 | 3 | 1 | 3 | **12** |

**Recommended primary: Just Security.** Highest score conditional on RSS-feasibility validation. Meaningful archive evidence + high topical overlap.

**Recommended secondary: Lawfare.** Strong on overlap; less archive evidence but still in the legal-analyst sub-niche.

## Tie-breakers

If Just Security's D1 returns < 3 (RSS dead or 401-gated):

1. Try Lawfare as primary; SCOTUSblog as secondary.
2. If both fail RSS probe: drop to Reuters Legal + Politico Legal pair.
3. If 3+ of 4 fail RSS probe: **abort Branch C deploy.** Document and escalate to Branch D earlier than spec'd. Single-feed Branch C deploy is permitted but not recommended.

## Deploy procedure (post-selection)

```bash
# Step 1: edit config.py
# Add the 1-2 selected URLs to RSS_FEEDS list
# (Per the A.1+1.5 spec §3.1 pattern)

# Step 2: extend _source_class_for_evidence token list (main.py)
# Add tokens from selected sources' titles per A.1+ spec §3.2 expansion

# Step 3: confirm xfail harness flips xpass
.venv/bin/python -m pytest tests/test_lever_a1plus_feed_config.py -k "vital_law_or_legal_analyst"

# Step 4: VERSION bump (per CLAUDE.md release-versioning rules)
echo "0.31.0" > VERSION  # Wave-2 minor bump

# Step 5: commit (xfail removal in same hunk per project pattern)
# Step 6: tag
# Step 7: deploy + 14 d post-deploy observation per A.1+1.5 spec §6
```

## Acceptance criteria (Branch C deploy success)

Per Wave-2 A.1+ branch decision table:

- 14 d post-deploy: ≥ 1 PAPER_TRADE from the new feed
- Aggregate realized P&L on those trades is non-negative
- Per-lane attribution (post-OBS-003 SKIPPED stream) confirms the lift came from the new feed, not background noise

If both feeds (primary + secondary) deploy and 14 d returns 0 PAPER_TRADE: Branch D fires per Lever-D escalation criteria spec §2.1.

If 1+ PAPER_TRADE materializes with positive P&L: EDGE-004 closes via Branch C.

## Out of scope

- VitalLaw.com direct-RSS retry (closed per Codex 2026-05-05 `a45c06c`).
- Reddit-based legal-analyst feeds (subreddit-class evidence; out of A.1+1.5 spec scope).
- Custom feed scraper for HTML-only sites (out of Wave-2 scope).
- Per-feed budget/cost analysis (RSS feeds are free; LLM-call cost is the real cost; covered in EXEC-001).

## Cross-links

- `docs/superpowers/specs/2026-05-04-edge-004-lever-a1plus1-5-legal-analyst-design.md` — A.1+1.5 spec
- `docs/_archive/governance/2026-05-04-lever-a1-plus-1-5-legal-analyst-feed-sizing.md` — Codex's probe-order audit `5e5849a` (ARCHIVED Stream G R6)
- `docs/_archive/governance/2026-05-04-legal-niche-probe-order-domain-overlap.md` — domain-overlap empirical anchor (ARCHIVED Stream G R6)
- `docs/governance/2026-05-05-vitallaw-direct-rss-probe.md` — VitalLaw.com Branch B kill (`a45c06c`)
- `docs/governance/2026-05-05-wave-2-a1plus-branch-decision-table.md` — Branch A → C → D sequence
- `tests/test_lever_a1plus_feed_config.py` — pre-loaded xfail harness (covers both option-A and option-B/Branch-C)
