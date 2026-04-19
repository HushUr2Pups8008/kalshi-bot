-- Evidence store schema draft for data/evidence_store.db.
-- This database is intentionally separate from data/paper_trades.db.
-- Runtime implementation belongs to S2.2; this file is a validated schema spec.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS dossiers (
    market_ticker TEXT PRIMARY KEY,
    dossier_version INTEGER NOT NULL DEFAULT 0 CHECK (dossier_version >= 0),
    current_estimate REAL CHECK (current_estimate IS NULL OR (current_estimate >= 0.0 AND current_estimate <= 1.0)),
    confidence REAL NOT NULL DEFAULT 0.0 CHECK (confidence >= 0.0 AND confidence <= 0.95),
    prior_estimate REAL CHECK (prior_estimate IS NULL OR (prior_estimate >= 0.0 AND prior_estimate <= 1.0)),
    drift_suspect INTEGER NOT NULL DEFAULT 0 CHECK (drift_suspect IN (0, 1)),
    in_recovery INTEGER NOT NULL DEFAULT 0 CHECK (in_recovery IN (0, 1)),
    freeze_started_ts TEXT,
    recovery_started_ts TEXT,
    recovery_until_ts TEXT,
    last_cross_class_state_update_ts TEXT,
    created_ts TEXT NOT NULL,
    updated_ts TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evidence (
    evidence_id TEXT PRIMARY KEY,
    market_ticker TEXT NOT NULL,
    source TEXT NOT NULL,
    source_class TEXT NOT NULL,
    headline TEXT NOT NULL,
    url TEXT,
    published_ts TEXT,
    ingested_ts TEXT NOT NULL,
    raw_payload_json TEXT,
    content_hash TEXT NOT NULL,
    headline_ngram_fingerprint TEXT,
    correlation_cluster_id TEXT,
    is_duplicate INTEGER NOT NULL DEFAULT 0 CHECK (is_duplicate IN (0, 1)),
    correlation_discount_applied INTEGER NOT NULL DEFAULT 0 CHECK (correlation_discount_applied IN (0, 1)),
    update_type TEXT NOT NULL CHECK (update_type IN ('state', 'confidence')),
    quality_score REAL CHECK (quality_score IS NULL OR (quality_score >= 0.0 AND quality_score <= 1.0)),
    original_weight REAL CHECK (original_weight IS NULL OR original_weight >= 0.0),
    dossier_version_before INTEGER NOT NULL CHECK (dossier_version_before >= 0),
    dossier_version_after INTEGER NOT NULL CHECK (dossier_version_after >= dossier_version_before),
    FOREIGN KEY (market_ticker) REFERENCES dossiers(market_ticker)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    UNIQUE (market_ticker, evidence_id)
);

CREATE TABLE IF NOT EXISTS dossier_updates (
    market_ticker TEXT NOT NULL,
    dossier_version INTEGER NOT NULL CHECK (dossier_version >= 1),
    created_ts TEXT NOT NULL,
    trigger_evidence_id TEXT NOT NULL,
    prior_estimate REAL CHECK (prior_estimate IS NULL OR (prior_estimate >= 0.0 AND prior_estimate <= 1.0)),
    new_estimate REAL CHECK (new_estimate IS NULL OR (new_estimate >= 0.0 AND new_estimate <= 1.0)),
    update_delta REAL NOT NULL,
    confidence_before REAL NOT NULL CHECK (confidence_before >= 0.0 AND confidence_before <= 0.95),
    confidence_after REAL NOT NULL CHECK (confidence_after >= 0.0 AND confidence_after <= 0.95),
    update_type TEXT NOT NULL CHECK (update_type IN ('state', 'confidence')),
    llm_called INTEGER NOT NULL DEFAULT 0 CHECK (llm_called IN (0, 1)),
    drift_suspect INTEGER NOT NULL DEFAULT 0 CHECK (drift_suspect IN (0, 1)),
    in_recovery INTEGER NOT NULL DEFAULT 0 CHECK (in_recovery IN (0, 1)),
    PRIMARY KEY (market_ticker, dossier_version),
    FOREIGN KEY (market_ticker) REFERENCES dossiers(market_ticker)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    FOREIGN KEY (market_ticker, trigger_evidence_id) REFERENCES evidence(market_ticker, evidence_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS dossier_update_evidence (
    market_ticker TEXT NOT NULL,
    dossier_version INTEGER NOT NULL,
    evidence_id TEXT NOT NULL,
    contribution_role TEXT NOT NULL DEFAULT 'current_belief_state',
    PRIMARY KEY (market_ticker, dossier_version, evidence_id),
    FOREIGN KEY (market_ticker, dossier_version) REFERENCES dossier_updates(market_ticker, dossier_version)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    FOREIGN KEY (market_ticker, evidence_id) REFERENCES evidence(market_ticker, evidence_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_evidence_market_ingested
    ON evidence(market_ticker, ingested_ts);

CREATE INDEX IF NOT EXISTS idx_evidence_market_source_class_ingested
    ON evidence(market_ticker, source_class, ingested_ts);

CREATE INDEX IF NOT EXISTS idx_evidence_market_content_hash
    ON evidence(market_ticker, content_hash);

CREATE INDEX IF NOT EXISTS idx_evidence_market_version_after
    ON evidence(market_ticker, dossier_version_after);

CREATE INDEX IF NOT EXISTS idx_dossier_updates_market_created
    ON dossier_updates(market_ticker, created_ts);

CREATE INDEX IF NOT EXISTS idx_dossier_update_evidence_evidence
    ON dossier_update_evidence(evidence_id);

CREATE TRIGGER IF NOT EXISTS trg_evidence_identity_immutable
BEFORE UPDATE OF evidence_id, market_ticker, ingested_ts ON evidence
BEGIN
    SELECT RAISE(ABORT, 'immutable evidence identity fields cannot be updated');
END;

CREATE TRIGGER IF NOT EXISTS trg_evidence_append_only
BEFORE DELETE ON evidence
BEGIN
    SELECT RAISE(ABORT, 'evidence rows are append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_dossier_updates_identity_immutable
BEFORE UPDATE OF market_ticker, dossier_version, created_ts, trigger_evidence_id ON dossier_updates
BEGIN
    SELECT RAISE(ABORT, 'immutable dossier update identity fields cannot be updated');
END;

CREATE TRIGGER IF NOT EXISTS trg_dossier_updates_append_only
BEFORE DELETE ON dossier_updates
BEGIN
    SELECT RAISE(ABORT, 'dossier update rows are append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_dossier_update_evidence_append_only
BEFORE UPDATE ON dossier_update_evidence
BEGIN
    SELECT RAISE(ABORT, 'dossier update evidence links are append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_dossier_update_evidence_no_delete
BEFORE DELETE ON dossier_update_evidence
BEGIN
    SELECT RAISE(ABORT, 'dossier update evidence links are append-only');
END;
