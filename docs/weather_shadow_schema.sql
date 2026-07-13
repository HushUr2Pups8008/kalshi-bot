CREATE TABLE IF NOT EXISTS research_weather_shadow_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    capture_key TEXT NOT NULL UNIQUE,
    event_ticker TEXT NOT NULL,
    target_date TEXT NOT NULL,
    capture_started_at TEXT NOT NULL,
    capture_finished_at TEXT NOT NULL,
    as_of TEXT NOT NULL,
    close_time TEXT NOT NULL,
    seconds_to_close REAL NOT NULL CHECK (seconds_to_close >= 0),
    horizon_bucket TEXT NOT NULL,
    forecast_issued_at TEXT NOT NULL,
    forecast_valid_start TEXT NOT NULL,
    forecast_valid_end TEXT NOT NULL,
    observation_measured_at TEXT NOT NULL,
    observation_coverage_start TEXT NOT NULL,
    observation_count INTEGER NOT NULL CHECK (observation_count > 0),
    weather_retrieved_at TEXT NOT NULL,
    grid_forecast_high_f REAL NOT NULL,
    hourly_forecast_high_f REAL NOT NULL,
    running_observed_high_f REAL NOT NULL,
    forecast_spread_f REAL NOT NULL,
    target_weekday INTEGER NOT NULL CHECK (target_weekday BETWEEN 0 AND 6),
    source_payload_hash TEXT NOT NULL,
    source_payload_json TEXT NOT NULL,
    quotes_hash TEXT NOT NULL,
    fee_schedule_version TEXT NOT NULL,
    model_version TEXT NOT NULL,
    shadow_only INTEGER NOT NULL DEFAULT 1 CHECK (shadow_only = 1),
    diagnostic_only INTEGER NOT NULL DEFAULT 1 CHECK (diagnostic_only = 1),
    created_ts TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS research_weather_shadow_quotes (
    snapshot_id TEXT NOT NULL,
    market_ticker TEXT NOT NULL,
    close_time TEXT NOT NULL,
    lower_bound_f INTEGER,
    upper_bound_f INTEGER,
    is_lower_tail INTEGER NOT NULL CHECK (is_lower_tail IN (0, 1)),
    is_upper_tail INTEGER NOT NULL CHECK (is_upper_tail IN (0, 1)),
    contract_fingerprint TEXT NOT NULL,
    rules_source_fingerprint TEXT NOT NULL,
    settlement_source_fingerprint TEXT NOT NULL,
    yes_bid_cents INTEGER NOT NULL CHECK (yes_bid_cents BETWEEN 0 AND 100),
    yes_ask_cents INTEGER NOT NULL CHECK (yes_ask_cents BETWEEN 0 AND 100),
    no_bid_cents INTEGER NOT NULL CHECK (no_bid_cents BETWEEN 0 AND 100),
    no_ask_cents INTEGER NOT NULL CHECK (no_ask_cents BETWEEN 0 AND 100),
    yes_bid_size_fp TEXT NOT NULL,
    yes_ask_size_fp TEXT NOT NULL,
    no_bid_size_fp TEXT NOT NULL,
    no_ask_size_fp TEXT NOT NULL,
    last_price_cents INTEGER,
    volume_fp TEXT,
    price_retrieved_at TEXT NOT NULL,
    raw_payload_hash TEXT NOT NULL,
    PRIMARY KEY(snapshot_id, market_ticker),
    FOREIGN KEY(snapshot_id)
        REFERENCES research_weather_shadow_snapshots(snapshot_id)
);

CREATE TABLE IF NOT EXISTS research_weather_shadow_outcomes (
    outcome_id TEXT PRIMARY KEY,
    outcome_batch_id TEXT NOT NULL,
    market_ticker TEXT NOT NULL,
    event_ticker TEXT NOT NULL,
    expected_sibling_count INTEGER NOT NULL CHECK (expected_sibling_count > 1),
    result TEXT NOT NULL CHECK (result IN ('yes', 'no')),
    kalshi_status TEXT NOT NULL CHECK (kalshi_status IN ('finalized', 'settled')),
    settlement_observed_at TEXT NOT NULL,
    source_payload_hash TEXT NOT NULL,
    contract_fingerprint TEXT NOT NULL,
    rules_source_fingerprint TEXT NOT NULL,
    settlement_source_fingerprint TEXT NOT NULL,
    official_high_f REAL NOT NULL,
    official_evidence_id TEXT NOT NULL,
    official_source_url TEXT NOT NULL,
    official_product_id TEXT NOT NULL,
    official_issued_at TEXT NOT NULL,
    official_retrieved_at TEXT NOT NULL,
    label_available_at TEXT NOT NULL,
    created_ts TEXT NOT NULL,
    UNIQUE(market_ticker, source_payload_hash)
);

CREATE TABLE IF NOT EXISTS research_weather_shadow_conflicts (
    conflict_id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL CHECK (entity_type IN ('snapshot', 'outcome')),
    entity_key TEXT NOT NULL,
    existing_hash TEXT NOT NULL,
    incoming_hash TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    details_json TEXT NOT NULL,
    created_ts TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS research_weather_shadow_outcome_checks (
    check_id TEXT PRIMARY KEY,
    event_ticker TEXT NOT NULL,
    check_date_utc TEXT NOT NULL,
    checked_at TEXT NOT NULL,
    check_kind TEXT NOT NULL CHECK (check_kind IN ('daily', 'seal')),
    observed_batch_hash TEXT NOT NULL,
    baseline_batch_hash TEXT NOT NULL,
    agrees_with_baseline INTEGER NOT NULL CHECK (agrees_with_baseline IN (0, 1)),
    details_json TEXT NOT NULL,
    created_ts TEXT NOT NULL,
    UNIQUE(event_ticker, check_date_utc, check_kind)
);

CREATE INDEX IF NOT EXISTS idx_weather_shadow_snapshots_event
    ON research_weather_shadow_snapshots(event_ticker);
CREATE INDEX IF NOT EXISTS idx_weather_shadow_quotes_market
    ON research_weather_shadow_quotes(market_ticker);
CREATE INDEX IF NOT EXISTS idx_weather_shadow_outcomes_event
    ON research_weather_shadow_outcomes(event_ticker);
CREATE INDEX IF NOT EXISTS idx_weather_shadow_outcomes_market
    ON research_weather_shadow_outcomes(market_ticker);
CREATE INDEX IF NOT EXISTS idx_weather_shadow_checks_event_date
    ON research_weather_shadow_outcome_checks(event_ticker, check_date_utc);
CREATE INDEX IF NOT EXISTS idx_weather_shadow_checks_check_date
    ON research_weather_shadow_outcome_checks(check_date_utc);

CREATE TRIGGER IF NOT EXISTS weather_shadow_snapshots_no_update
BEFORE UPDATE ON research_weather_shadow_snapshots
BEGIN SELECT RAISE(ABORT, 'append-only: research_weather_shadow_snapshots'); END;
CREATE TRIGGER IF NOT EXISTS weather_shadow_snapshots_no_delete
BEFORE DELETE ON research_weather_shadow_snapshots
BEGIN SELECT RAISE(ABORT, 'append-only: research_weather_shadow_snapshots'); END;

CREATE TRIGGER IF NOT EXISTS weather_shadow_quotes_no_update
BEFORE UPDATE ON research_weather_shadow_quotes
BEGIN SELECT RAISE(ABORT, 'append-only: research_weather_shadow_quotes'); END;
CREATE TRIGGER IF NOT EXISTS weather_shadow_quotes_no_delete
BEFORE DELETE ON research_weather_shadow_quotes
BEGIN SELECT RAISE(ABORT, 'append-only: research_weather_shadow_quotes'); END;

CREATE TRIGGER IF NOT EXISTS weather_shadow_outcomes_no_update
BEFORE UPDATE ON research_weather_shadow_outcomes
BEGIN SELECT RAISE(ABORT, 'append-only: research_weather_shadow_outcomes'); END;
CREATE TRIGGER IF NOT EXISTS weather_shadow_outcomes_no_delete
BEFORE DELETE ON research_weather_shadow_outcomes
BEGIN SELECT RAISE(ABORT, 'append-only: research_weather_shadow_outcomes'); END;

CREATE TRIGGER IF NOT EXISTS weather_shadow_conflicts_no_update
BEFORE UPDATE ON research_weather_shadow_conflicts
BEGIN SELECT RAISE(ABORT, 'append-only: research_weather_shadow_conflicts'); END;
CREATE TRIGGER IF NOT EXISTS weather_shadow_conflicts_no_delete
BEFORE DELETE ON research_weather_shadow_conflicts
BEGIN SELECT RAISE(ABORT, 'append-only: research_weather_shadow_conflicts'); END;

CREATE TRIGGER IF NOT EXISTS weather_shadow_checks_no_update
BEFORE UPDATE ON research_weather_shadow_outcome_checks
BEGIN SELECT RAISE(ABORT, 'append-only: research_weather_shadow_outcome_checks'); END;
CREATE TRIGGER IF NOT EXISTS weather_shadow_checks_no_delete
BEFORE DELETE ON research_weather_shadow_outcome_checks
BEGIN SELECT RAISE(ABORT, 'append-only: research_weather_shadow_outcome_checks'); END;
