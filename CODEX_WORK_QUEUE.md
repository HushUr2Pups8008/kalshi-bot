# Codex Work Queue

Ready-to-start tasks with clear specifications. No architectural decisions or ambiguity resolution needed.

---

## Priority 1: P0.1 — Tag Startup Probe (IN_PROGRESS)

**Status:** IN_PROGRESS (filtering side complete; tagging side pending)

**What:** Add `is_synthetic_probe=true` field to `SIGNAL_ANALYSIS_DETAIL` events emitted by the startup probe.

**Context:**
- Startup probe emits synthetic `(0.38, 0.82)` output on every bot start
- This contaminates all signal-quality statistics
- Filtering logic already exists in `scripts/signal_edge_diagnostics.py` (expects `is_synthetic_probe` or `is_startup_probe` field)
- Need to write the field when the probe event is created

**Specification:**
- Locate where `SIGNAL_ANALYSIS_DETAIL` events are created for startup probe
- Add `is_synthetic_probe=True` to the event dict
- Ensure the field appears in all startup probe events
- Verify filtering in `signal_edge_diagnostics.py` works once field is present

**Success:** `scripts/signal_edge_diagnostics.py` reports "Startup probes (excluded): 1" (or higher) on a fresh run.

---

## Priority 2: P0.2 — Log Prompt and Raw LLM Response (NOT_STARTED)

**Status:** NOT_STARTED

**Depends on:** P0.1 complete

**What:** Add DEBUG-level logging of full LLM prompt and raw response for all non-probe analysis calls.

**Context:**
- Production analysis returns `est = 0.5000` on 40+ non-probe calls (audit 2026-04-17 to 2026-04-21)
- Need to inspect actual prompt and raw LLM response to diagnose why
- Will feed into P0.3 (manual verdict on root cause)

**Specification:**
- Log level: DEBUG only (no impact on production unless DEBUG enabled)
- Log must include: (a) full prompt text, (b) raw LLM response text
- Trigger: every `estimate()` or equivalent call in `/analysis/signal_analyzer.py` that is not marked as a probe
- Format: structured log (JSON) with fields `prompt`, `raw_response`, optionally `market_ticker`, `event_id`

**Success:** DEBUG logs show prompt + raw response for each real (non-probe) LLM call.

---

## Priority 3: P1.5.2 — Audit Reddit Sources (NOT_STARTED)

**Status:** NOT_STARTED

**Depends on:** None (independent)

**What:** Extract and document freshness and match-to-analysis conversion rates for each Reddit source.

**Context:**
- Reddit sources have high ingestion volume but unclear signal value
- Need metrics to determine which Reddit sources are worth keeping
- Findings will feed into P1.5.1 (investigation of Politics source) and P1.5.3 (disable dead sources)

**Specification:**
- Query evidence store or logs for all matches originating from Reddit sources
- For each Reddit source, calculate:
  - Freshness rate: (fresh matches) / (total matches) where fresh = published within last 7 days
  - Match-to-analysis rate: (matches that reached LLM) / (total matches)
  - Total volume: count of matches
- Output: table or CSV with columns: [source_name, total_matches, fresh_count, freshness_rate, llm_reached, conversion_rate]
- Audit period: trailing 30 days from bot logs

**Success:** Documented freshness and conversion rates for each Reddit source; enough data to identify high-value vs low-value sources.

---

## Notes for Codex

- **No architectural decisions:** Specs above are complete and require no further ambiguity resolution.
- **No invented behavior:** Implement exactly as specified, no more.
- **Blockers:** Report immediately if you find something in the spec is impossible or contradicts current code.
- **Order:** P0.1 → P0.2 → P1.5.2 (P0.2 depends on P0.1; P1.5.2 is independent).
