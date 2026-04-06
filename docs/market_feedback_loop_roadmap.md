# Market Feedback Loop — Architecture Roadmap

## Vision

The bot currently flows in one direction: news -> signal -> trade. This roadmap
closes the loop so market behavior feeds back into signal generation and learning.

This is also the architectural foundation for the multi-agent Mac Studio system.
Each loop below maps to a specialized agent in that future. Building them as asyncio
tasks now means the Mac Studio transition is a refactor, not a rewrite.

---

## The Four Feedback Loops

```
CURRENT (one-way):    News --> Signal --> Trade --> (P&L log)

PROPOSED:

Loop A: Price --> News       market price moves alert us to unprocessed news
Loop B: Resolution --> Learn  outcomes teach us which keywords were correct
Loop C: Drift --> Flag        open position prices surface position health
Loop D: New Market --> Hunt   new listings trigger immediate targeted search
```

---

## Loop A: Price-Driven News Search

**Status:** Implemented (v0.19.1)

When a geo market price moves >= PRICE_MOVE_THRESHOLD_CENTS (10c) within a
5-minute rolling window, trigger an immediate targeted Google News + GDELT search
for that market's tokens. This inverts discovery: instead of waiting for news to
find markets, volatile markets proactively hunt for news.

Rate-limited to PRICE_SEARCH_COOLDOWN_SECS (1800s = 30min) per ticker.

**Mac Studio upgrade:** Lower threshold to 5c, reduce cooldown to 600s.

---

## Loop B: Resolution -> Keyword Outcome Tracking

**Status:** Implemented (v0.19.0)

Every time a trade resolves, one row per keyword fired on that trade is written
to `paper_trades.db:keyword_outcomes`. Fields: keyword, direction, market_side,
resolved_yes, correct (1 if keyword pointed the right way).

**Current use:** Audit trail only.

**Mac Studio upgrade (Phase 3):** Signal analyzer reads this table at startup
and adjusts each keyword's `strength` multiplier based on historical accuracy.
Example: "invasion" on KXTRUMP* series has 10% correct rate -> strength halved.

Schema:
```sql
CREATE TABLE keyword_outcomes (
    id            INTEGER PRIMARY KEY,
    trade_id      TEXT NOT NULL,
    ticker        TEXT NOT NULL,
    series_ticker TEXT NOT NULL,   -- e.g. "KXTRUMPIRAN" for grouping
    keyword       TEXT NOT NULL,
    direction     TEXT NOT NULL,   -- keyword's declared direction: "yes" or "no"
    market_side   TEXT NOT NULL,   -- side we bet: "yes" or "no"
    resolved_yes  INTEGER NOT NULL,
    correct       INTEGER NOT NULL,  -- 1 if direction matched outcome
    ts            TEXT NOT NULL
)
```

---

## Loop C: Open Position Price Drift Logging

**Status:** Implemented (v0.19.0)

When a WS price update arrives for a ticker with an open paper position, compare
current mid-price to the entry price. If drift >= DRIFT_ALERT_CENTS (15c), emit
a POSITION_DRIFT event to trades.jsonl (rate-limited to DRIFT_LOG_COOLDOWN_SECS
= 3600s per ticker).

JSONL event shape:
```json
{"event": "POSITION_DRIFT", "ticker": "...", "ts": "...",
 "entry_price": 45.0, "current_price": 28.0, "drift_cents": -17.0,
 "side": "yes", "open_since": "2026-03-15T..."}
```

**Mac Studio upgrade (Phase 3):** Drift events trigger LLM re-analysis:
fetch recent news for that ticker -> re-run signal analyzer -> if estimated_prob
has flipped direction by >0.15, emit POSITION_REASSESSMENT event and optionally
close + reverse.

---

## Loop D: New Market Detection

**Status:** Implemented (v0.19.1)

At each 30-min market cache refresh, compare new ticker set vs previous. For each
new ticker, emit NEW_MARKET to trades.jsonl and immediately trigger a targeted
news search (same as Loop A). New listings get discovered within 30 minutes of
appearing on Kalshi.

JSONL event shape:
```json
{"event": "NEW_MARKET", "ticker": "...", "ts": "...",
 "title": "Will Trump sign executive order X?", "series_ticker": "..."}
```

---

## Multi-Agent Vision (Mac Studio, May 2026+)

On Mac Studio with <5s inference, these loops become three long-running agents
with dedicated asyncio queues communicating via JSONL events:

```
Resolution Agent    : reads resolution events -> updates keyword_outcomes DB
                      -> broadcasts updated keyword weights to Signal Analyzer
Drift Monitor Agent : watches open positions via WS -> triggers LLM re-analysis
                      on significant drift -> optionally closes/reverses position
Discovery Agent     : watches market cache + price velocity -> proactively hunts
                      news on new/volatile markets -> feeds news queue
```

The `keyword_outcomes` table is the shared memory between agents:
- Resolution Agent writes it
- Signal Analyzer reads it at runtime to weight keywords dynamically

Transition path:
1. asyncio Task (current) -> asyncio Task with dedicated Queue (next)
2. Dedicated Queue -> Claude Agent SDK subprocess (Mac Studio)
3. Each agent gets its own Ollama model slot (Resolution: 4B, Drift: 8B, Discovery: 4B)

---

## Phase 3 Dynamic Weighting — Implementation Sketch

```python
# analysis/keyword_weights.py (new file, Phase 3)
def load_keyword_weights(db_path: Path, min_samples: int = 10) -> dict[str, float]:
    """
    Returns {keyword: accuracy_ratio} for keywords with >= min_samples outcomes.
    Keywords with < min_samples get ratio = None (use default strength).
    """
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        "SELECT keyword, SUM(correct), COUNT(*) FROM keyword_outcomes "
        "GROUP BY keyword HAVING COUNT(*) >= ?",
        (min_samples,)
    ).fetchall()
    return {kw: wins / total for kw, wins, total in rows}

# In signal_analyzer.py, at startup:
_keyword_weights = load_keyword_weights(DB_PATH)

# When applying GEOPOLITICAL_SIGNALS strength:
base_strength = signal["strength"]
accuracy = _keyword_weights.get(keyword)
if accuracy is not None:
    # Scale: 50% accuracy (random) -> 0.5x, 80% accuracy -> 1.3x
    adjusted = base_strength * (accuracy / 0.5)
    strength = max(0.01, min(base_strength * 2, adjusted))
```

---

## File Map

| File | Role |
|------|------|
| `main.py` | Loop A (velocity tracker, targeted search), Loop C (drift logging), Loop D (new market detection) |
| `trading/paper_trader.py` | Loop B (keyword_outcomes table + resolve_market population) |
| `config.py` | All threshold constants |
| `feeds/search_news_monitor.py` | `_markets_to_queries()` reused by Loop A targeted search |
| `docs/market_feedback_loop_roadmap.md` | This file |
| `analysis/keyword_weights.py` | Phase 3 only (not yet implemented) |
