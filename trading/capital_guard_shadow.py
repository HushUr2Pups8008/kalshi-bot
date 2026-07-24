"""Isolated append-only evidence for candidates blocked only by the capital guard.

The store has no constructor-time I/O and is not wired into trading. Candidate
records contain decision-time facts only; outcomes and fee-net results belong to
the observation, settlement, and evaluation records.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
from typing import Literal, TypeVar

from trading.fees import (
    DIRECT_ACCOUNT_PRECISION,
    NON_DIRECT_ACCOUNT_PRECISION,
    FeeContext,
    FeeRole,
    deserialize_fee_schedule,
    fee_coefficient_for,
    fee_type_for_schedule,
    quote_fee,
    serialize_fee_schedule,
)
from trading.settlement import (
    MarketOutcome,
    SettlementDriftError,
    SettlementObservation,
    VoidRefundContract,
    build_settlement_observation,
)
from trading.settlement_economics import (
    SettlementCashflows,
    SettlementEconomicsBinding,
    SettlementEconomicsContract,
    SettlementEconomicsUnscorableError,
    derive_settlement_cashflows,
    derive_settlement_fee_receipt,
    deserialize_settlement_economics_evidence,
    serialize_settlement_economics_evidence,
    validate_settlement_economics_contract,
)
from trading.venue import MarketRef, Venue


CAPITAL_GUARD_SHADOW_SCHEMA_VERSION = 2
CAPITAL_GUARD_CAPTURE_ATTEMPT_VERSION = 1
CAPITAL_GUARD_CANDIDATE_VERSION = 1
CAPITAL_GUARD_SETTLEMENT_ATTEMPT_VERSION = 1
CAPITAL_GUARD_SHADOW_DB = Path("data/capital_guard_shadow.db")
MAX_SETTLEMENT_MARKETS_PER_RUN = 100
MAX_SETTLEMENT_CANDIDATES_PER_MARKET = 1_000
MAX_SETTLEMENT_POLL_STATE_SCAN = MAX_SETTLEMENT_MARKETS_PER_RUN
MAX_REPLAY_SNAPSHOT_FILE_BYTES = 64 * 1024 * 1024

_BUSY_TIMEOUT_MS = 5_000
_SQLITE_CONNECT = sqlite3.connect
_SHA256_TEXT = re.compile(r"[0-9a-f]{64}")
_GATE_NAMES = tuple(f"G{number}" for number in range(1, 8))
_ELIGIBLE_FAILURE = "G7_open_exposure_drawdown"
_T = TypeVar("_T")


class CapitalGuardShadowSchemaError(RuntimeError):
    """The isolated shadow schema is partial, drifted, or corrupt."""


class CapitalGuardShadowIdentityError(ValueError):
    """A bounded candidate group cannot produce one exact market identity."""

    def __init__(
        self,
        message: str,
        *,
        market_key: SettlementMarketKey,
        reason_taxonomy: str,
        candidate_count: int,
        candidate_set_sha256: str | None,
        identity_set_sha256: str | None,
        identity_sample_sha256: str | None = None,
    ) -> None:
        super().__init__(message)
        self.market_key = market_key
        self.reason_taxonomy = reason_taxonomy
        self.candidate_count = candidate_count
        self.candidate_set_sha256 = candidate_set_sha256
        self.identity_set_sha256 = identity_set_sha256
        self.identity_sample_sha256 = identity_sample_sha256


def canonical_json(value: object) -> str:
    """Return the sole JSON representation accepted by the store."""
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _normalize_schema_sql(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.strip().rstrip(";").lower().split())


def _immutable_triggers(tables: Sequence[str]) -> tuple[tuple[str, str], ...]:
    statements: list[tuple[str, str]] = []
    for table in tables:
        for operation in ("update", "delete"):
            name = f"immutable_{table}_{operation}"
            statements.append(
                (
                    name,
                    f"""
                    CREATE TRIGGER {name}
                    BEFORE {operation.upper()} ON {table}
                    BEGIN
                        SELECT RAISE(ABORT, '{table} is append-only');
                    END
                    """,
                )
            )
    return tuple(statements)


_TABLE_STATEMENTS: tuple[tuple[str, str], ...] = (
    (
        "capital_guard_shadow_schema_meta",
        """
        CREATE TABLE capital_guard_shadow_schema_meta (
            schema_version INTEGER PRIMARY KEY,
            ddl_sha256 TEXT NOT NULL,
            applied_at TEXT NOT NULL,
            CHECK (schema_version = 2),
            CHECK (length(ddl_sha256) = 64)
        )
        """,
    ),
    (
        "capital_guard_shadow_capture_attempts",
        """
        CREATE TABLE capital_guard_shadow_capture_attempts (
            capture_attempt_id TEXT PRIMARY KEY,
            capture_version INTEGER NOT NULL CHECK (capture_version = 1),
            payload_sha256 TEXT NOT NULL,
            decision_key TEXT NOT NULL,
            lifecycle_id TEXT NOT NULL,
            decision_at TEXT NOT NULL,
            captured_at TEXT NOT NULL,
            venue TEXT NOT NULL CHECK (venue IN ('kalshi','polymarket_us')),
            venue_market_id TEXT NOT NULL,
            market_family TEXT NOT NULL,
            side TEXT NOT NULL CHECK (side IN ('yes','no')),
            ordered_failures_json TEXT NOT NULL,
            non_gate_blocker TEXT,
            claim_identity_json TEXT NOT NULL,
            gate_identity_json TEXT NOT NULL,
            target_gate TEXT NOT NULL CHECK (target_gate = 'G7'),
            target_failure TEXT NOT NULL CHECK (target_failure = 'G7_open_exposure_drawdown'),
            scorable INTEGER NOT NULL CHECK (scorable IN (0,1)),
            ordered_unscorable_reasons_json TEXT NOT NULL,
            requested_stake_dollars TEXT,
            partial_artifacts_json TEXT,
            CHECK (length(capture_attempt_id) = 64),
            CHECK (length(payload_sha256) = 64),
            UNIQUE (venue, venue_market_id, side, lifecycle_id, decision_at)
        )
        """,
    ),
    (
        "capital_guard_shadow_candidates",
        """
        CREATE TABLE capital_guard_shadow_candidates (
            candidate_id TEXT PRIMARY KEY,
            capture_attempt_id TEXT NOT NULL UNIQUE
                REFERENCES capital_guard_shadow_capture_attempts(capture_attempt_id),
            candidate_version INTEGER NOT NULL CHECK (candidate_version = 1),
            lifecycle_id TEXT NOT NULL,
            decision_key TEXT NOT NULL,
            payload_sha256 TEXT NOT NULL,
            decision_at TEXT NOT NULL,
            captured_at TEXT NOT NULL,
            venue TEXT NOT NULL CHECK (venue IN ('kalshi','polymarket_us')),
            venue_market_id TEXT NOT NULL,
            market_family TEXT NOT NULL,
            side TEXT NOT NULL CHECK (side IN ('yes','no')),
            ordered_failures_json TEXT NOT NULL,
            non_gate_blocker TEXT,
            replay_eligible INTEGER NOT NULL CHECK (replay_eligible IN (0,1)),
            gate_inputs_json TEXT NOT NULL,
            gate_results_json TEXT NOT NULL,
            identity_json TEXT NOT NULL,
            executable_book_json TEXT NOT NULL,
            book_observed_at TEXT NOT NULL,
            book_source TEXT NOT NULL,
            book_method TEXT NOT NULL,
            book_payload_sha256 TEXT NOT NULL,
            expected_probability TEXT NOT NULL,
            executable_price_dollars TEXT NOT NULL,
            executable_quantity TEXT NOT NULL,
            gross_edge TEXT NOT NULL,
            sizing_json TEXT NOT NULL,
            fill_policy_json TEXT NOT NULL,
            fee_schedule_json TEXT NOT NULL,
            fee_provenance_json TEXT NOT NULL,
            fee_provenance_sha256 TEXT NOT NULL,
            fee_formula_type TEXT NOT NULL,
            fee_role TEXT NOT NULL CHECK (fee_role IN ('maker','taker')),
            fee_multiplier TEXT NOT NULL,
            fee_coefficient TEXT NOT NULL,
            fee_account_precision_dollars TEXT,
            fee_accumulator_dollars TEXT NOT NULL,
            gross_entry_debit_dollars TEXT NOT NULL,
            entry_fee_dollars TEXT NOT NULL,
            net_entry_debit_dollars TEXT NOT NULL,
            CHECK (length(candidate_id) = 64),
            CHECK (length(payload_sha256) = 64),
            CHECK (length(book_payload_sha256) = 64),
            CHECK (length(fee_provenance_sha256) = 64),
            UNIQUE (venue, venue_market_id, side, lifecycle_id, decision_at)
        )
        """,
    ),
    (
        "capital_guard_shadow_conflicts",
        """
        CREATE TABLE capital_guard_shadow_conflicts (
            conflict_id TEXT PRIMARY KEY,
            entity_type TEXT NOT NULL,
            entity_key TEXT NOT NULL,
            existing_sha256 TEXT NOT NULL,
            incoming_sha256 TEXT NOT NULL,
            details_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            CHECK (length(conflict_id) = 64),
            CHECK (length(existing_sha256) = 64),
            CHECK (length(incoming_sha256) = 64),
            UNIQUE (entity_type, entity_key, existing_sha256, incoming_sha256)
        )
        """,
    ),
    (
        "capital_guard_shadow_observations",
        """
        CREATE TABLE capital_guard_shadow_observations (
            observation_sha256 TEXT PRIMARY KEY,
            venue TEXT NOT NULL CHECK (venue IN ('kalshi','polymarket_us')),
            venue_market_id TEXT NOT NULL,
            alias TEXT NOT NULL,
            contract_fingerprint TEXT NOT NULL,
            rules_fingerprint TEXT NOT NULL,
            settlement_fingerprint TEXT NOT NULL,
            outcome TEXT NOT NULL CHECK (outcome IN ('yes','no','void','unresolved')),
            observed_at TEXT NOT NULL,
            effective_at TEXT NOT NULL,
            source_id TEXT NOT NULL,
            rules_version TEXT NOT NULL,
            authoritative_outcome_json TEXT NOT NULL,
            source_payload_json TEXT NOT NULL,
            authoritative_payload_sha256 TEXT NOT NULL,
            authoritative_observation_sha256 TEXT NOT NULL,
            void_refund_json TEXT,
            void_refund_sha256 TEXT,
            semantic_sha256 TEXT NOT NULL UNIQUE,
            supersedes_observation_sha256 TEXT REFERENCES capital_guard_shadow_observations(observation_sha256),
            CHECK (length(observation_sha256) = 64),
            CHECK (length(authoritative_payload_sha256) = 64),
            CHECK (length(authoritative_observation_sha256) = 64),
            CHECK (void_refund_sha256 IS NULL OR length(void_refund_sha256) = 64),
            CHECK (
                (outcome = 'void' AND void_refund_json IS NOT NULL
                 AND void_refund_sha256 IS NOT NULL)
                OR
                (outcome != 'void' AND void_refund_json IS NULL
                 AND void_refund_sha256 IS NULL)
            ),
            CHECK (length(semantic_sha256) = 64),
            CHECK (supersedes_observation_sha256 IS NULL OR length(supersedes_observation_sha256) = 64),
            CHECK (observation_sha256 IS NOT supersedes_observation_sha256),
            UNIQUE (supersedes_observation_sha256)
        )
        """,
    ),
    (
        "capital_guard_shadow_candidate_observations",
        """
        CREATE TABLE capital_guard_shadow_candidate_observations (
            link_id TEXT PRIMARY KEY,
            candidate_id TEXT NOT NULL REFERENCES capital_guard_shadow_candidates(candidate_id),
            observation_sha256 TEXT NOT NULL REFERENCES capital_guard_shadow_observations(observation_sha256),
            linked_at TEXT NOT NULL,
            CHECK (length(link_id) = 64),
            UNIQUE (candidate_id, observation_sha256)
        )
        """,
    ),
    (
        "capital_guard_shadow_settlement_attempts",
        """
        CREATE TABLE capital_guard_shadow_settlement_attempts (
            attempt_id TEXT PRIMARY KEY,
            attempt_version INTEGER NOT NULL CHECK (attempt_version = 1),
            payload_sha256 TEXT NOT NULL,
            venue TEXT NOT NULL CHECK (venue IN ('kalshi','polymarket_us')),
            venue_market_id TEXT NOT NULL,
            alias TEXT,
            contract_fingerprint TEXT,
            rules_fingerprint TEXT,
            settlement_fingerprint TEXT,
            identity_set_sha256 TEXT,
            identity_sample_sha256 TEXT,
            candidate_set_sha256 TEXT,
            candidate_set_complete INTEGER NOT NULL CHECK (candidate_set_complete IN (0,1)),
            candidate_count INTEGER NOT NULL CHECK (candidate_count > 0),
            attempted_at TEXT NOT NULL,
            status TEXT NOT NULL CHECK (
                status IN ('nonterminal','not_found','transient_error',
                           'internal_error','quarantined','terminal')
            ),
            outcome TEXT CHECK (outcome IN ('yes','no','void')),
            source_id TEXT,
            rules_version TEXT,
            authoritative_outcome_json TEXT,
            authoritative_payload_sha256 TEXT,
            authoritative_observation_sha256 TEXT,
            semantic_sha256 TEXT,
            void_refund_json TEXT,
            void_refund_sha256 TEXT,
            head_before_sha256 TEXT REFERENCES capital_guard_shadow_observations(observation_sha256),
            head_after_sha256 TEXT REFERENCES capital_guard_shadow_observations(observation_sha256),
            error_taxonomy TEXT,
            error_sha256 TEXT,
            CHECK (length(attempt_id) = 64),
            CHECK (length(payload_sha256) = 64),
            CHECK (identity_set_sha256 IS NULL OR length(identity_set_sha256) = 64),
            CHECK (identity_sample_sha256 IS NULL OR length(identity_sample_sha256) = 64),
            CHECK (candidate_set_sha256 IS NULL OR length(candidate_set_sha256) = 64),
            CHECK (authoritative_payload_sha256 IS NULL OR length(authoritative_payload_sha256) = 64),
            CHECK (authoritative_observation_sha256 IS NULL OR length(authoritative_observation_sha256) = 64),
            CHECK (semantic_sha256 IS NULL OR length(semantic_sha256) = 64),
            CHECK (void_refund_sha256 IS NULL OR length(void_refund_sha256) = 64),
            CHECK (head_before_sha256 IS NULL OR length(head_before_sha256) = 64),
            CHECK (head_after_sha256 IS NULL OR length(head_after_sha256) = 64),
            CHECK (error_sha256 IS NULL OR length(error_sha256) = 64),
            CHECK (
                (void_refund_json IS NULL AND void_refund_sha256 IS NULL)
                OR
                (void_refund_json IS NOT NULL AND void_refund_sha256 IS NOT NULL)
            ),
            CHECK (
                error_taxonomy IS NULL OR error_taxonomy IN (
                    'authoritative_nonterminal','authoritative_not_found',
                    'timeout','connection','transport_os_error',
                    'internal_source_error','source_drift',
                    'identity_ambiguous','candidate_group_over_cap',
                    'missing_void_refund_contract',
                    'void_financial_economics_deferred',
                    'concurrent_state_change','observation_fork','backward_time',
                    'financial_ambiguity'
                )
            ),
            CHECK (
                (alias IS NOT NULL AND contract_fingerprint IS NOT NULL
                 AND rules_fingerprint IS NOT NULL AND settlement_fingerprint IS NOT NULL)
                OR
                (status = 'quarantined' AND alias IS NULL
                 AND contract_fingerprint IS NULL AND rules_fingerprint IS NULL
                 AND settlement_fingerprint IS NULL
                 AND error_taxonomy IN ('identity_ambiguous','candidate_group_over_cap'))
            ),
            CHECK (
                (candidate_set_complete = 1 AND candidate_set_sha256 IS NOT NULL
                 AND identity_set_sha256 IS NOT NULL AND identity_sample_sha256 IS NULL)
                OR
                (candidate_set_complete = 0 AND status = 'quarantined'
                 AND error_taxonomy = 'candidate_group_over_cap'
                 AND candidate_set_sha256 IS NULL AND identity_set_sha256 IS NULL
                 AND identity_sample_sha256 IS NOT NULL)
            ),
            CHECK (
                (status = 'terminal' AND outcome IN ('yes','no')
                 AND source_id IS NOT NULL AND rules_version IS NOT NULL
                 AND authoritative_outcome_json IS NOT NULL
                 AND authoritative_payload_sha256 IS NOT NULL
                 AND authoritative_observation_sha256 IS NOT NULL
                 AND semantic_sha256 IS NOT NULL AND head_after_sha256 IS NOT NULL
                 AND void_refund_json IS NULL AND void_refund_sha256 IS NULL
                 AND error_taxonomy IS NULL
                 AND error_sha256 IS NULL)
                OR status != 'terminal'
            ),
            CHECK (
                status NOT IN ('nonterminal','not_found','transient_error','internal_error')
                OR
                (outcome IS NULL AND source_id IS NULL AND rules_version IS NULL
                 AND authoritative_outcome_json IS NULL
                 AND authoritative_payload_sha256 IS NULL
                 AND authoritative_observation_sha256 IS NULL
                 AND semantic_sha256 IS NULL AND void_refund_json IS NULL
                 AND void_refund_sha256 IS NULL AND head_after_sha256 IS NULL)
            ),
            CHECK (
                (status = 'nonterminal' AND error_taxonomy = 'authoritative_nonterminal'
                 AND error_sha256 IS NOT NULL)
                OR (status = 'not_found' AND error_taxonomy = 'authoritative_not_found'
                    AND error_sha256 IS NOT NULL)
                OR (status = 'transient_error'
                    AND error_taxonomy IN ('timeout','connection','transport_os_error')
                    AND error_sha256 IS NOT NULL)
                OR (status = 'internal_error'
                    AND error_taxonomy = 'internal_source_error'
                    AND error_sha256 IS NOT NULL)
                OR status IN ('quarantined','terminal')
            ),
            CHECK (
                (status = 'quarantined' AND error_taxonomy IS NOT NULL
                 AND error_sha256 IS NOT NULL)
                OR status != 'quarantined'
            )
        )
        """,
    ),
    (
        "capital_guard_shadow_settlement_quarantines",
        """
        CREATE TABLE capital_guard_shadow_settlement_quarantines (
            quarantine_id TEXT PRIMARY KEY,
            attempt_id TEXT NOT NULL UNIQUE
                REFERENCES capital_guard_shadow_settlement_attempts(attempt_id),
            reason_taxonomy TEXT NOT NULL CHECK (
                reason_taxonomy IN (
                    'identity_ambiguous','candidate_group_over_cap','source_drift',
                    'missing_void_refund_contract',
                    'void_financial_economics_deferred',
                    'concurrent_state_change','observation_fork','backward_time',
                    'financial_ambiguity'
                )
            ),
            reason_sha256 TEXT NOT NULL,
            created_at TEXT NOT NULL,
            CHECK (length(quarantine_id) = 64),
            CHECK (length(reason_sha256) = 64)
        )
        """,
    ),
    (
        "capital_guard_shadow_settlements",
        """
        CREATE TABLE capital_guard_shadow_settlements (
            settlement_id TEXT PRIMARY KEY,
            candidate_id TEXT NOT NULL REFERENCES capital_guard_shadow_candidates(candidate_id),
            observation_sha256 TEXT NOT NULL REFERENCES capital_guard_shadow_observations(observation_sha256),
            outcome TEXT NOT NULL CHECK (outcome IN ('yes','no','void')),
            settled_at TEXT NOT NULL,
            gross_payout_dollars TEXT NOT NULL,
            settlement_fee_dollars TEXT NOT NULL,
            settlement_refund_dollars TEXT NOT NULL,
            net_payout_dollars TEXT NOT NULL,
            details_json TEXT NOT NULL,
            payload_sha256 TEXT NOT NULL,
            CHECK (length(settlement_id) = 64),
            CHECK (length(payload_sha256) = 64),
            UNIQUE (candidate_id, observation_sha256, payload_sha256),
            FOREIGN KEY (candidate_id, observation_sha256)
                REFERENCES capital_guard_shadow_candidate_observations(candidate_id, observation_sha256)
        )
        """,
    ),
    (
        "capital_guard_shadow_evaluations",
        """
        CREATE TABLE capital_guard_shadow_evaluations (
            evaluation_id TEXT PRIMARY KEY,
            candidate_id TEXT NOT NULL REFERENCES capital_guard_shadow_candidates(candidate_id),
            settlement_id TEXT NOT NULL REFERENCES capital_guard_shadow_settlements(settlement_id),
            evaluated_at TEXT NOT NULL,
            evaluation_kind TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status = 'settled'),
            entry_fee_dollars TEXT NOT NULL,
            gross_pnl_dollars TEXT NOT NULL,
            settlement_fee_dollars TEXT NOT NULL,
            settlement_refund_dollars TEXT NOT NULL,
            fee_net_pnl_dollars TEXT NOT NULL,
            bankroll_before_dollars TEXT NOT NULL,
            bankroll_after_dollars TEXT NOT NULL,
            open_exposure_before_dollars TEXT NOT NULL,
            open_exposure_after_dollars TEXT NOT NULL,
            high_water_mark_dollars TEXT NOT NULL,
            drawdown_after_dollars TEXT NOT NULL,
            worst_case_loss_dollars TEXT NOT NULL,
            details_json TEXT NOT NULL,
            payload_sha256 TEXT NOT NULL,
            CHECK (length(evaluation_id) = 64),
            CHECK (length(payload_sha256) = 64),
            UNIQUE (candidate_id, evaluation_kind, evaluated_at, payload_sha256)
        )
        """,
    ),
)

_INDEX_STATEMENTS: tuple[tuple[str, str], ...] = (
    (
        "idx_capital_guard_shadow_capture_attempts_market",
        "CREATE INDEX idx_capital_guard_shadow_capture_attempts_market "
        "ON capital_guard_shadow_capture_attempts"
        "(venue, venue_market_id, scorable, decision_at)",
    ),
    (
        "idx_capital_guard_shadow_candidates_market",
        "CREATE INDEX idx_capital_guard_shadow_candidates_market "
        "ON capital_guard_shadow_candidates(venue, venue_market_id, decision_at)",
    ),
    (
        "idx_capital_guard_shadow_candidates_family",
        "CREATE INDEX idx_capital_guard_shadow_candidates_family "
        "ON capital_guard_shadow_candidates(market_family, replay_eligible, decision_at)",
    ),
    (
        "idx_capital_guard_shadow_observations_market",
        "CREATE INDEX idx_capital_guard_shadow_observations_market "
        "ON capital_guard_shadow_observations(venue, venue_market_id, observed_at)",
    ),
    (
        "idx_capital_guard_shadow_observations_supersedes",
        "CREATE INDEX idx_capital_guard_shadow_observations_supersedes "
        "ON capital_guard_shadow_observations(supersedes_observation_sha256)",
    ),
    (
        "idx_capital_guard_shadow_settlement_attempts_market",
        "CREATE INDEX idx_capital_guard_shadow_settlement_attempts_market "
        "ON capital_guard_shadow_settlement_attempts"
        "(venue, venue_market_id, attempted_at, status)",
    ),
    (
        "idx_capital_guard_shadow_settlement_attempts_status",
        "CREATE INDEX idx_capital_guard_shadow_settlement_attempts_status "
        "ON capital_guard_shadow_settlement_attempts(status, attempted_at)",
    ),
    (
        "idx_capital_guard_shadow_settlement_quarantines_reason",
        "CREATE INDEX idx_capital_guard_shadow_settlement_quarantines_reason "
        "ON capital_guard_shadow_settlement_quarantines(reason_taxonomy, created_at)",
    ),
    (
        "idx_capital_guard_shadow_observations_root_market",
        "CREATE UNIQUE INDEX idx_capital_guard_shadow_observations_root_market "
        "ON capital_guard_shadow_observations(venue, venue_market_id) "
        "WHERE supersedes_observation_sha256 IS NULL",
    ),
    (
        "idx_capital_guard_shadow_candidate_observations_observation",
        "CREATE INDEX idx_capital_guard_shadow_candidate_observations_observation "
        "ON capital_guard_shadow_candidate_observations(observation_sha256, candidate_id)",
    ),
    (
        "idx_capital_guard_shadow_settlements_candidate",
        "CREATE INDEX idx_capital_guard_shadow_settlements_candidate "
        "ON capital_guard_shadow_settlements(candidate_id, settled_at)",
    ),
    (
        "idx_capital_guard_shadow_evaluations_candidate",
        "CREATE INDEX idx_capital_guard_shadow_evaluations_candidate "
        "ON capital_guard_shadow_evaluations(candidate_id, evaluated_at)",
    ),
)

_TABLE_NAMES = tuple(name for name, _statement in _TABLE_STATEMENTS)
_TRIGGER_STATEMENTS = _immutable_triggers(_TABLE_NAMES)
CAPITAL_GUARD_SHADOW_TARGET_STATEMENTS = _TABLE_STATEMENTS + _INDEX_STATEMENTS + _TRIGGER_STATEMENTS
_DDL_CONTRACT = "\n".join(
    f"-- {name}\n{statement.strip()};" for name, statement in CAPITAL_GUARD_SHADOW_TARGET_STATEMENTS
)
CAPITAL_GUARD_SHADOW_DDL_SHA256 = hashlib.sha256(_DDL_CONTRACT.encode("utf-8")).hexdigest()
_TARGET_OBJECT_TYPES = {
    **{name: "table" for name, _statement in _TABLE_STATEMENTS},
    **{name: "index" for name, _statement in _INDEX_STATEMENTS},
    **{name: "trigger" for name, _statement in _TRIGGER_STATEMENTS},
}
_TARGET_SQL = {name: _normalize_schema_sql(statement) for name, statement in CAPITAL_GUARD_SHADOW_TARGET_STATEMENTS}


@dataclass(frozen=True)
class CapitalGuardCaptureAttempt:
    """Canonical capture envelope, including attempts that cannot be scored."""

    decision_key: str
    lifecycle_id: str
    decision_at: datetime
    captured_at: datetime
    venue: Venue
    venue_market_id: str
    market_family: str
    side: Literal["yes", "no"]
    ordered_failures: tuple[str, ...]
    non_gate_blocker: str | None
    target_gate: Literal["G7"]
    target_failure: Literal["G7_open_exposure_drawdown"]
    scorable: bool
    ordered_unscorable_reasons: tuple[str, ...]
    requested_stake: Decimal | None
    partial_artifacts_json: str | None
    capture_version: int = CAPITAL_GUARD_CAPTURE_ATTEMPT_VERSION

    def __post_init__(self) -> None:
        if self.capture_version != CAPITAL_GUARD_CAPTURE_ATTEMPT_VERSION:
            raise ValueError("unsupported capture_version")
        _require_text("decision_key", self.decision_key)
        _require_text("lifecycle_id", self.lifecycle_id)
        _require_utc_datetime("decision_at", self.decision_at)
        _require_utc_datetime("captured_at", self.captured_at)
        if self.captured_at < self.decision_at:
            raise ValueError("captured_at must not precede decision_at")
        if not isinstance(self.venue, Venue):
            raise ValueError("venue must be a supported Venue")
        for name in ("venue_market_id", "market_family"):
            _require_text(name, getattr(self, name))
        if self.side not in ("yes", "no"):
            raise ValueError("side must be yes or no")
        _validate_failures(self.ordered_failures)
        if self.target_gate != "G7" or self.target_failure != _ELIGIBLE_FAILURE:
            raise ValueError("capture attempt must identify the capital guard gate")
        if self.target_failure not in self.ordered_failures:
            raise ValueError("capture attempt ordered_failures must include target failure")
        if self.non_gate_blocker is not None:
            _require_text("non_gate_blocker", self.non_gate_blocker)
            if self.non_gate_blocker in self.ordered_failures:
                raise ValueError("non_gate_blocker must not duplicate a gate failure")
        if not isinstance(self.scorable, bool):
            raise ValueError("scorable must be bool")
        _validate_unscorable_reasons(self.ordered_unscorable_reasons)
        if self.scorable is bool(self.ordered_unscorable_reasons):
            raise ValueError("scorable and ordered_unscorable_reasons disagree")
        if self.requested_stake is not None:
            _require_decimal("requested_stake", self.requested_stake)
            if self.requested_stake <= 0:
                raise ValueError("requested_stake must be positive")
        if self.partial_artifacts_json is not None:
            _validate_partial_artifacts_json(self.partial_artifacts_json)


@dataclass(frozen=True)
class CapitalGuardCandidate:
    """Frozen, decision-time contract for a shadow candidate."""

    decision_key: str
    lifecycle_id: str
    decision_at: datetime
    captured_at: datetime
    venue: Venue
    venue_market_id: str
    market_family: str
    side: Literal["yes", "no"]
    ordered_failures: tuple[str, ...]
    non_gate_blocker: str | None
    gate_inputs_json: str
    gate_results_json: str
    identity_json: str
    executable_book_json: str
    book_observed_at: datetime
    book_source: str
    book_method: str
    book_payload_sha256: str
    expected_probability: Decimal
    executable_price: Decimal
    executable_quantity: Decimal
    gross_edge: Decimal
    sizing_json: str
    fill_policy_json: str
    fee_schedule_json: str
    fee_provenance_json: str
    fee_provenance_sha256: str
    fee_formula_type: str
    fee_role: FeeRole
    fee_multiplier: Decimal
    fee_coefficient: Decimal
    fee_account_precision: Decimal | None
    fee_accumulator: Decimal
    candidate_version: int = CAPITAL_GUARD_CANDIDATE_VERSION

    def __post_init__(self) -> None:
        if self.candidate_version != CAPITAL_GUARD_CANDIDATE_VERSION:
            raise ValueError("unsupported candidate_version")
        _require_text("decision_key", self.decision_key)
        _require_text("lifecycle_id", self.lifecycle_id)
        _require_utc_datetime("decision_at", self.decision_at)
        _require_utc_datetime("captured_at", self.captured_at)
        _require_utc_datetime("book_observed_at", self.book_observed_at)
        if self.captured_at < self.decision_at:
            raise ValueError("captured_at must not precede decision_at")
        if self.book_observed_at > self.decision_at:
            raise ValueError("book_observed_at must not follow decision_at")
        if not isinstance(self.venue, Venue):
            raise ValueError("venue must be a supported Venue")
        for name in ("venue_market_id", "market_family", "book_source", "book_method"):
            _require_text(name, getattr(self, name))
        if self.side not in ("yes", "no"):
            raise ValueError("side must be yes or no")
        for name in (
            "expected_probability",
            "executable_price",
            "executable_quantity",
            "gross_edge",
            "fee_multiplier",
            "fee_coefficient",
            "fee_accumulator",
        ):
            _require_decimal(name, getattr(self, name))
        if self.fee_account_precision is not None:
            _require_decimal("fee_account_precision", self.fee_account_precision)

        _validate_failures(self.ordered_failures)
        if self.non_gate_blocker is not None:
            _require_text("non_gate_blocker", self.non_gate_blocker)
        gate_inputs = _validate_gate_inputs_json(self.gate_inputs_json, self.side)
        gate_results = _validate_gate_results_json(self.gate_results_json)
        _validate_gate_decision_consistency(gate_inputs, gate_results)
        _validate_failure_results(self.ordered_failures, gate_results)

        _validate_identity_json(self)
        _validate_book_json(self)
        _require_sha256("book_payload_sha256", self.book_payload_sha256)
        _require_sha256("fee_provenance_sha256", self.fee_provenance_sha256)
        _validate_sizing_json(self)
        _validate_fill_policy_json(self)

        if not D0 <= self.expected_probability <= D1:
            raise ValueError("expected_probability must be in [0, 1]")
        if not D0 < self.executable_price < D1:
            raise ValueError("executable_price must be in (0, 1)")
        if self.executable_quantity <= 0:
            raise ValueError("executable_quantity must be positive")
        if self.gross_edge != self.expected_probability - self.executable_price:
            raise ValueError("gross_edge must equal expected_probability minus executable_price")

        schedule = deserialize_fee_schedule(self.fee_schedule_json)
        if serialize_fee_schedule(schedule) != self.fee_schedule_json:
            raise ValueError("fee_schedule_json must be canonical JSON")
        if schedule.venue is not self.venue:
            raise ValueError("fee schedule venue does not match candidate venue")
        if self.decision_at < schedule.effective_from or (
            schedule.effective_to is not None and self.decision_at >= schedule.effective_to
        ):
            raise ValueError("fee schedule is not effective at decision_at")
        if self.fee_formula_type != fee_type_for_schedule(schedule):
            raise ValueError("fee_formula_type does not match pinned schedule")
        if not isinstance(self.fee_role, FeeRole):
            raise ValueError("fee_role must be a supported FeeRole")
        if self.fee_coefficient != fee_coefficient_for(schedule, self.fee_role):
            raise ValueError("fee_coefficient does not match pinned schedule")
        if self.fee_multiplier < 0:
            raise ValueError("fee_multiplier must be nonnegative")
        if self.fee_accumulator < 0 or self.fee_accumulator >= Decimal("0.01"):
            raise ValueError("fee_accumulator must be in [0, 0.01)")
        if self.venue is Venue.KALSHI and self.fee_account_precision not in (
            DIRECT_ACCOUNT_PRECISION,
            NON_DIRECT_ACCOUNT_PRECISION,
        ):
            raise ValueError("Kalshi fee_account_precision is not pinned")
        if self.venue is Venue.POLYMARKET_US and self.fee_account_precision is not None:
            raise ValueError("Polymarket fee_account_precision must be absent")
        if self.venue is Venue.POLYMARKET_US and (self.fee_multiplier != 1 or self.fee_accumulator != 0):
            raise ValueError("Polymarket fee provenance requires multiplier=1 and accumulator=0")
        _validate_fee_provenance_json(self)
        _entry_accounting(self)

    @property
    def replay_eligible(self) -> bool:
        gate_results = json.loads(self.gate_results_json)["gates"]
        return (
            self.ordered_failures == (_ELIGIBLE_FAILURE,)
            and self.non_gate_blocker is None
            and all(gate_results[f"G{number}"]["passed"] is True for number in range(1, 7))
            and gate_results["G7"]["passed"] is False
        )


@dataclass(frozen=True)
class SettlementMarketKey:
    venue: Venue
    venue_market_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.venue, Venue):
            raise ValueError("venue must be a supported Venue")
        _require_text("venue_market_id", self.venue_market_id)


@dataclass(frozen=True)
class CurrentAuthoritativeHead:
    observation_sha256: str
    market_ref: MarketRef
    contract_fingerprint: str
    rules_fingerprint: str
    settlement_fingerprint: str
    outcome: Literal["yes", "no", "void", "unresolved"]
    observed_at: datetime
    effective_at: datetime
    source_id: str
    rules_version: str
    authoritative_outcome_json: str
    source_payload_json: str
    authoritative_payload_sha256: str
    authoritative_observation_sha256: str
    void_refund_json: str | None
    void_refund_sha256: str | None
    semantic_sha256: str
    supersedes_observation_sha256: str | None


@dataclass(frozen=True)
class CandidateSettlementBacklog:
    market_ref: MarketRef
    contract_fingerprint: str
    rules_fingerprint: str
    settlement_fingerprint: str
    candidate_ids: tuple[str, ...]
    missing_link_candidate_ids: tuple[str, ...]
    candidate_set_sha256: str
    identity_set_sha256: str
    current_head_sha256: str | None
    prior_authoritative_observation: SettlementObservation | None
    authoritative_head_error: str | None


@dataclass(frozen=True)
class SettlementPollState:
    """Read-only durable scheduler facts for one current candidate market."""

    market_key: SettlementMarketKey
    first_decision_at: datetime
    current_candidate_count: int
    current_candidate_set_sha256: str | None
    current_candidate_set_complete: bool
    latest_attempted_at: datetime | None
    latest_status: Literal[
        "nonterminal",
        "not_found",
        "transient_error",
        "internal_error",
        "quarantined",
        "terminal",
    ] | None
    latest_snapshot_count: int | None
    latest_snapshot_sha256: str | None
    latest_snapshot_complete: bool | None
    matching_snapshot_retry_count: int


def _rehydrate_current_authoritative_observation(
    head: CurrentAuthoritativeHead,
) -> SettlementObservation:
    """Rebuild and validate the source observation stored by the current head."""
    try:
        record = SettlementObservationRecord(
            venue=head.market_ref.venue,
            venue_market_id=head.market_ref.venue_market_id,
            alias=head.market_ref.alias,
            contract_fingerprint=head.contract_fingerprint,
            rules_fingerprint=head.rules_fingerprint,
            settlement_fingerprint=head.settlement_fingerprint,
            outcome=head.outcome,
            observed_at=head.observed_at,
            effective_at=head.effective_at,
            source_id=head.source_id,
            rules_version=head.rules_version,
            authoritative_outcome_json=head.authoritative_outcome_json,
            source_payload_json=head.source_payload_json,
            authoritative_payload_sha256=head.authoritative_payload_sha256,
            authoritative_observation_sha256=head.authoritative_observation_sha256,
            semantic_sha256=head.semantic_sha256,
            void_refund_json=head.void_refund_json,
            void_refund_sha256=head.void_refund_sha256,
            supersedes_observation_sha256=head.supersedes_observation_sha256,
        )
        if _sha256(canonical_json(_observation_payload(record))) != head.observation_sha256:
            raise ValueError("authoritative head record hash is invalid")
        observation = build_settlement_observation(
            market_ref=head.market_ref,
            outcome=MarketOutcome(head.outcome),
            authoritative_outcome=json.loads(head.authoritative_outcome_json),
            authoritative_payload=json.loads(head.source_payload_json),
            observed_at=head.observed_at,
            effective_at=head.effective_at,
            rules_version=head.rules_version,
            source_id=head.source_id,
            void_refund=_void_refund_from_json(head.void_refund_json),
        )
    except (SettlementDriftError, TypeError, ValueError) as exc:
        raise ValueError("authoritative head cannot be rehydrated") from exc
    if observation.payload_sha256 != head.authoritative_payload_sha256:
        raise ValueError("authoritative head payload hash is invalid")
    if observation.observation_sha256 != head.authoritative_observation_sha256:
        raise ValueError("authoritative head source observation hash is invalid")
    return observation


@dataclass(frozen=True)
class CurrentHeadSettlement:
    settlement_id: str
    candidate_id: str
    observation_sha256: str
    outcome: Literal["yes", "no", "void"]
    settled_at: datetime


@dataclass(frozen=True)
class SettlementObservationRecord:
    """Authoritative outcome observation; corrections append and supersede."""

    venue: Venue
    venue_market_id: str
    alias: str
    contract_fingerprint: str
    rules_fingerprint: str
    settlement_fingerprint: str
    outcome: Literal["yes", "no", "void", "unresolved"]
    observed_at: datetime
    effective_at: datetime
    source_id: str
    rules_version: str
    authoritative_outcome_json: str
    source_payload_json: str
    authoritative_payload_sha256: str
    authoritative_observation_sha256: str
    semantic_sha256: str
    void_refund_json: str | None = None
    void_refund_sha256: str | None = None
    supersedes_observation_sha256: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.venue, Venue):
            raise ValueError("venue must be a supported Venue")
        for name in (
            "venue_market_id",
            "alias",
            "contract_fingerprint",
            "rules_fingerprint",
            "settlement_fingerprint",
            "source_id",
            "rules_version",
        ):
            _require_text(name, getattr(self, name))
        if self.outcome not in ("yes", "no", "void", "unresolved"):
            raise ValueError("unsupported observation outcome")
        _require_utc_datetime("observed_at", self.observed_at)
        _require_utc_datetime("effective_at", self.effective_at)
        if self.effective_at > self.observed_at:
            raise ValueError("effective_at must not follow observed_at")
        _require_canonical_json_value("authoritative_outcome_json", self.authoritative_outcome_json)
        _require_canonical_object("source_payload_json", self.source_payload_json)
        _require_sha256("authoritative_payload_sha256", self.authoritative_payload_sha256)
        if _sha256(self.source_payload_json) != self.authoritative_payload_sha256:
            raise ValueError("authoritative payload hash does not match source payload")
        _require_sha256("semantic_sha256", self.semantic_sha256)
        _require_sha256(
            "authoritative_observation_sha256",
            self.authoritative_observation_sha256,
        )
        if self.outcome == "void":
            if self.void_refund_json is None or self.void_refund_sha256 is None:
                raise ValueError("void observation requires an exact refund contract")
            _require_canonical_object("void_refund_json", self.void_refund_json)
            _require_sha256("void_refund_sha256", self.void_refund_sha256)
            if _sha256(self.void_refund_json) != self.void_refund_sha256:
                raise ValueError("void refund hash does not match contract")
        elif self.void_refund_json is not None or self.void_refund_sha256 is not None:
            raise ValueError("void refund contract is only valid for void outcome")
        if _authoritative_observation_sha256(self) != self.authoritative_observation_sha256:
            raise ValueError("authoritative observation hash does not match record")
        if _settlement_semantic_sha256(self) != self.semantic_sha256:
            raise ValueError("semantic_sha256 does not match settlement semantics")
        if self.supersedes_observation_sha256 is not None:
            _require_sha256(
                "supersedes_observation_sha256",
                self.supersedes_observation_sha256,
            )


@dataclass(frozen=True)
class ShadowSettlement:
    candidate_id: str
    observation_sha256: str
    outcome: Literal["yes", "no", "void"]
    settled_at: datetime
    economics_contract: SettlementEconomicsContract
    economics_binding: SettlementEconomicsBinding

    def __post_init__(self) -> None:
        _require_sha256("candidate_id", self.candidate_id)
        _require_sha256("observation_sha256", self.observation_sha256)
        if self.outcome not in ("yes", "no", "void"):
            raise ValueError("unsupported settlement outcome")
        _require_utc_datetime("settled_at", self.settled_at)
        if not isinstance(self.economics_contract, SettlementEconomicsContract):
            raise TypeError("economics_contract must be SettlementEconomicsContract")
        if not isinstance(self.economics_binding, SettlementEconomicsBinding):
            raise TypeError("economics_binding must be SettlementEconomicsBinding")


@dataclass(frozen=True)
class ShadowEvaluation:
    candidate_id: str
    settlement_id: str | None
    evaluated_at: datetime
    evaluation_kind: str
    status: Literal["open", "settled", "excluded"]
    entry_fee: Decimal | None
    gross_pnl: Decimal | None
    settlement_fee: Decimal | None
    settlement_refund: Decimal | None
    fee_net_pnl: Decimal | None
    bankroll_before: Decimal
    bankroll_after: Decimal
    open_exposure_before: Decimal
    open_exposure_after: Decimal
    high_water_mark: Decimal
    drawdown_after: Decimal
    worst_case_loss: Decimal
    details_json: str

    def __post_init__(self) -> None:
        _require_sha256("candidate_id", self.candidate_id)
        if self.settlement_id is not None:
            _require_sha256("settlement_id", self.settlement_id)
        _require_utc_datetime("evaluated_at", self.evaluated_at)
        _require_text("evaluation_kind", self.evaluation_kind)
        if self.status != "settled":
            raise ValueError("only settled evaluations may be persisted")
        optional_money = (
            self.entry_fee,
            self.gross_pnl,
            self.settlement_fee,
            self.settlement_refund,
            self.fee_net_pnl,
        )
        for name in (
            "entry_fee",
            "gross_pnl",
            "settlement_fee",
            "settlement_refund",
            "fee_net_pnl",
        ):
            value = getattr(self, name)
            if value is not None:
                _require_decimal(name, value)
        if self.settlement_id is None or any(value is None for value in optional_money):
            raise ValueError("settled evaluation requires settlement and complete fee-net values")
        for name in (
            "bankroll_before",
            "bankroll_after",
            "open_exposure_before",
            "open_exposure_after",
            "high_water_mark",
            "drawdown_after",
            "worst_case_loss",
        ):
            value = getattr(self, name)
            _require_decimal(name, value)
            if value < 0:
                raise ValueError(f"{name} must be nonnegative")
        _require_canonical_object("details_json", self.details_json)


@dataclass(frozen=True)
class CaptureAttemptWriteResult:
    status: Literal["inserted", "identical", "conflict"]
    capture_attempt_id: str
    payload_sha256: str
    conflict_id: str | None = None


@dataclass(frozen=True)
class CandidateWriteResult:
    status: Literal["inserted", "identical", "conflict"]
    candidate_id: str
    payload_sha256: str
    conflict_id: str | None = None


@dataclass(frozen=True)
class ObservationWriteResult:
    status: Literal["inserted", "identical", "conflict"]
    observation_sha256: str
    conflict_id: str | None = None


@dataclass(frozen=True)
class SettlementAttemptWriteResult:
    status: Literal["inserted", "identical", "conflict"]
    attempt_id: str
    attempt_status: Literal[
        "nonterminal",
        "not_found",
        "transient_error",
        "internal_error",
        "quarantined",
        "terminal",
    ]
    observation_status: Literal["inserted", "identical"] | None = None
    observation_sha256: str | None = None
    conflict_id: str | None = None


@dataclass(frozen=True)
class SettlementWriteResult:
    status: Literal["inserted", "identical"]
    settlement_id: str


@dataclass(frozen=True)
class EvaluationWriteResult:
    status: Literal["inserted", "identical"]
    evaluation_id: str


D0 = Decimal("0")
D1 = Decimal("1")


class CapitalGuardShadowStore:
    """Dedicated SQLite store with no constructor-time I/O."""

    def __init__(
        self,
        db_path: Path = CAPITAL_GUARD_SHADOW_DB,
        *,
        existing_only: bool = False,
    ) -> None:
        self.db_path = Path(db_path)
        self.existing_only = existing_only

    def initialize(self, *, applied_at: datetime | None = None) -> None:
        timestamp = applied_at or datetime.now(timezone.utc)
        _require_utc_datetime("applied_at", timestamp)
        if not self.existing_only:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing = _user_schema_objects(conn)
            if not existing:
                if self.existing_only:
                    raise CapitalGuardShadowSchemaError("capital guard shadow schema drift")
                for _name, statement in CAPITAL_GUARD_SHADOW_TARGET_STATEMENTS:
                    conn.execute(statement)
                conn.execute(
                    "INSERT INTO capital_guard_shadow_schema_meta "
                    "(schema_version, ddl_sha256, applied_at) VALUES (?, ?, ?)",
                    (
                        CAPITAL_GUARD_SHADOW_SCHEMA_VERSION,
                        CAPITAL_GUARD_SHADOW_DDL_SHA256,
                        _timestamp(timestamp),
                    ),
                )
            self._validate_schema(conn)
            conn.commit()
        except BaseException:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()

    def append_candidate(self, record: CapitalGuardCandidate) -> CandidateWriteResult:
        if not isinstance(record, CapitalGuardCandidate):
            raise TypeError("record must be CapitalGuardCandidate")
        return self._write(lambda conn: self._append_candidate_transaction(conn, record))

    def append_capture_attempt(
        self,
        record: CapitalGuardCaptureAttempt,
    ) -> CaptureAttemptWriteResult:
        if not isinstance(record, CapitalGuardCaptureAttempt):
            raise TypeError("record must be CapitalGuardCaptureAttempt")
        return self._write(lambda conn: self._append_capture_attempt_transaction(conn, record))

    def append_observation(
        self,
        record: SettlementObservationRecord,
        candidate_ids: Sequence[str],
    ) -> ObservationWriteResult:
        if not isinstance(record, SettlementObservationRecord):
            raise TypeError("record must be SettlementObservationRecord")
        normalized_ids = tuple(sorted(set(candidate_ids)))
        if not normalized_ids:
            raise ValueError("at least one candidate_id is required")
        for candidate_id in normalized_ids:
            _require_sha256("candidate_id", candidate_id)
        return self._write(lambda conn: self._append_observation_transaction(conn, record, normalized_ids))

    def settlement_market_backlog(
        self,
        *,
        limit: int = MAX_SETTLEMENT_MARKETS_PER_RUN,
    ) -> tuple[SettlementMarketKey, ...]:
        _require_bounded_limit("limit", limit, maximum=MAX_SETTLEMENT_MARKETS_PER_RUN)

        def read(conn: sqlite3.Connection) -> tuple[SettlementMarketKey, ...]:
            self._validate_schema(conn)
            rows = conn.execute(
                """
                SELECT c.venue, c.venue_market_id, MIN(c.decision_at),
                       MAX(a.attempted_at)
                FROM capital_guard_shadow_candidates c
                LEFT JOIN capital_guard_shadow_settlement_attempts a
                  ON a.venue = c.venue
                 AND a.venue_market_id = c.venue_market_id
                GROUP BY c.venue, c.venue_market_id
                ORDER BY (MAX(a.attempted_at) IS NOT NULL), MAX(a.attempted_at),
                         MIN(c.decision_at), c.venue, c.venue_market_id
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return tuple(SettlementMarketKey(Venue(str(row[0])), str(row[1])) for row in rows)

        return self._read(read)

    def settlement_poll_states(
        self,
        *,
        limit: int = MAX_SETTLEMENT_POLL_STATE_SCAN,
        terminal_correction_audit: bool = False,
        now: datetime | None = None,
    ) -> tuple[SettlementPollState, ...]:
        """Project bounded durable scheduler facts without mutable scheduler state.

        At most ``limit`` market groups are materialized before bounded candidate
        snapshot reads. When ``now`` is supplied, routine collection ranks new
        and changed snapshots, then due retries, ahead of delayed and terminal
        state. The terminal-only mode is reserved for an explicit, bounded
        terminal-correction audit.
        """
        _require_bounded_limit("limit", limit, maximum=MAX_SETTLEMENT_POLL_STATE_SCAN)
        if not isinstance(terminal_correction_audit, bool):
            raise TypeError("terminal_correction_audit must be bool")
        if now is not None:
            _require_utc_datetime("now", now)

        retry_delay_sql = """
            CASE latest.status
                WHEN 'transient_error' THEN
                    CASE
                        WHEN retry_counts.matching_retry_count <= 1 THEN 300
                        WHEN retry_counts.matching_retry_count = 2 THEN 600
                        WHEN retry_counts.matching_retry_count = 3 THEN 1200
                        WHEN retry_counts.matching_retry_count = 4 THEN 2400
                        ELSE 3600
                    END
                WHEN 'nonterminal' THEN
                    CASE
                        WHEN retry_counts.matching_retry_count <= 1 THEN 900
                        WHEN retry_counts.matching_retry_count = 2 THEN 1800
                        WHEN retry_counts.matching_retry_count = 3 THEN 3600
                        WHEN retry_counts.matching_retry_count = 4 THEN 7200
                        WHEN retry_counts.matching_retry_count = 5 THEN 14400
                        WHEN retry_counts.matching_retry_count = 6 THEN 28800
                        WHEN retry_counts.matching_retry_count = 7 THEN 57600
                        ELSE 86400
                    END
                WHEN 'internal_error' THEN
                    CASE
                        WHEN retry_counts.matching_retry_count <= 1 THEN 900
                        WHEN retry_counts.matching_retry_count = 2 THEN 1800
                        WHEN retry_counts.matching_retry_count = 3 THEN 3600
                        WHEN retry_counts.matching_retry_count = 4 THEN 7200
                        WHEN retry_counts.matching_retry_count = 5 THEN 14400
                        ELSE 21600
                    END
                WHEN 'not_found' THEN
                    CASE
                        WHEN retry_counts.matching_retry_count <= 1 THEN 3600
                        WHEN retry_counts.matching_retry_count = 2 THEN 7200
                        WHEN retry_counts.matching_retry_count = 3 THEN 14400
                        WHEN retry_counts.matching_retry_count = 4 THEN 28800
                        WHEN retry_counts.matching_retry_count = 5 THEN 57600
                        ELSE 86400
                    END
            END
        """

        def read(conn: sqlite3.Connection) -> tuple[SettlementPollState, ...]:
            self._validate_schema(conn)
            if terminal_correction_audit:
                filter_sql = """
                    WHERE projection.attempt_id IS NOT NULL
                      AND projection.status = 'terminal'
                      AND projection.latest_snapshot_count = projection.current_candidate_count
                      AND projection.latest_snapshot_complete =
                          CASE
                              WHEN projection.current_candidate_count <= ? THEN 1
                              ELSE 0
                          END
                      AND NOT EXISTS (
                          SELECT 1
                          FROM capital_guard_shadow_candidates candidate
                          JOIN capital_guard_shadow_settlements settlement
                            ON settlement.candidate_id = candidate.candidate_id
                          WHERE candidate.venue = projection.venue
                            AND candidate.venue_market_id = projection.venue_market_id
                      )
                      AND NOT EXISTS (
                          SELECT 1
                          FROM capital_guard_shadow_candidates candidate
                          JOIN capital_guard_shadow_evaluations evaluation
                            ON evaluation.candidate_id = candidate.candidate_id
                          WHERE candidate.venue = projection.venue
                            AND candidate.venue_market_id = projection.venue_market_id
                      )
                """
                order_sql = """
                    ORDER BY projection.attempted_at, projection.first_decision_at,
                             projection.venue, projection.venue_market_id
                """
                query_params: tuple[object, ...] = (MAX_SETTLEMENT_CANDIDATES_PER_MARKET, limit)
            else:
                filter_sql = ""
                if now is None:
                    order_sql = """
                        ORDER BY
                            CASE
                                WHEN projection.status = 'quarantined' THEN 3
                                WHEN projection.attempt_id IS NULL THEN 0
                                WHEN projection.latest_snapshot_count != projection.current_candidate_count THEN 0
                                WHEN projection.latest_snapshot_complete !=
                                    CASE
                                        WHEN projection.current_candidate_count <= ? THEN 1
                                        ELSE 0
                                    END THEN 0
                                WHEN projection.status IN (
                                    'nonterminal', 'not_found', 'transient_error', 'internal_error'
                                ) THEN 1
                                ELSE 2
                            END,
                            projection.attempted_at, projection.first_decision_at,
                            projection.venue, projection.venue_market_id
                    """
                    query_params = (MAX_SETTLEMENT_CANDIDATES_PER_MARKET, limit)
                else:
                    order_sql = """
                        ORDER BY
                            CASE
                                WHEN projection.status = 'quarantined' THEN 4
                                WHEN projection.attempt_id IS NULL THEN 0
                                WHEN projection.latest_snapshot_count != projection.current_candidate_count THEN 0
                                WHEN projection.latest_snapshot_complete !=
                                    CASE
                                        WHEN projection.current_candidate_count <= ? THEN 1
                                        ELSE 0
                                    END THEN 0
                                WHEN projection.status IN (
                                    'nonterminal', 'not_found', 'transient_error', 'internal_error'
                                )
                                 AND julianday(?) >=
                                     julianday(projection.attempted_at)
                                     + projection.retry_delay_seconds / 86400.0 THEN 1
                                WHEN projection.status IN (
                                    'nonterminal', 'not_found', 'transient_error', 'internal_error'
                                ) THEN 2
                                ELSE 3
                            END,
                            CASE
                                WHEN projection.status IN (
                                    'nonterminal', 'not_found', 'transient_error', 'internal_error'
                                ) THEN julianday(projection.attempted_at)
                                    + projection.retry_delay_seconds / 86400.0
                            END,
                            projection.first_decision_at,
                            projection.venue, projection.venue_market_id
                    """
                    query_params = (
                        MAX_SETTLEMENT_CANDIDATES_PER_MARKET,
                        now.isoformat(),
                        limit,
                    )
            groups = conn.execute(
                f"""
                WITH grouped AS (
                    SELECT venue, venue_market_id, MIN(decision_at) AS first_decision_at,
                           COUNT(*) AS candidate_count
                    FROM capital_guard_shadow_candidates
                    GROUP BY venue, venue_market_id
                ), latest_ranked AS (
                    SELECT attempt_id, venue, venue_market_id, attempted_at, status,
                           candidate_count, candidate_set_sha256, candidate_set_complete,
                           ROW_NUMBER() OVER (
                               PARTITION BY venue, venue_market_id
                               ORDER BY attempted_at DESC, attempt_id DESC
                           ) AS attempt_rank
                    FROM capital_guard_shadow_settlement_attempts
                ), latest AS (
                    SELECT attempt_id, venue, venue_market_id, attempted_at, status,
                           candidate_count, candidate_set_sha256, candidate_set_complete
                    FROM latest_ranked
                    WHERE attempt_rank = 1
                ), retry_counts AS (
                    SELECT attempts.venue, attempts.venue_market_id,
                           COUNT(*) AS matching_retry_count
                    FROM capital_guard_shadow_settlement_attempts attempts
                    JOIN grouped
                      ON grouped.venue = attempts.venue
                     AND grouped.venue_market_id = attempts.venue_market_id
                    WHERE attempts.status IN (
                        'nonterminal', 'not_found', 'transient_error', 'internal_error'
                    )
                      AND attempts.candidate_count = grouped.candidate_count
                      AND attempts.candidate_set_complete =
                          CASE
                              WHEN grouped.candidate_count <= {MAX_SETTLEMENT_CANDIDATES_PER_MARKET}
                              THEN 1 ELSE 0
                          END
                    GROUP BY attempts.venue, attempts.venue_market_id
                ), projection AS (
                    SELECT grouped.venue, grouped.venue_market_id, grouped.first_decision_at,
                           grouped.candidate_count AS current_candidate_count,
                           latest.attempt_id, latest.attempted_at, latest.status,
                           latest.candidate_count AS latest_snapshot_count,
                           latest.candidate_set_sha256 AS latest_snapshot_sha256,
                           latest.candidate_set_complete AS latest_snapshot_complete,
                           COALESCE(retry_counts.matching_retry_count, 0) AS matching_retry_count,
                           {retry_delay_sql} AS retry_delay_seconds
                    FROM grouped
                    LEFT JOIN latest
                      ON latest.venue = grouped.venue
                     AND latest.venue_market_id = grouped.venue_market_id
                    LEFT JOIN retry_counts
                      ON retry_counts.venue = grouped.venue
                     AND retry_counts.venue_market_id = grouped.venue_market_id
                )
                SELECT projection.venue, projection.venue_market_id,
                       projection.first_decision_at, projection.current_candidate_count,
                       projection.attempted_at, projection.status,
                       projection.latest_snapshot_count,
                       projection.latest_snapshot_sha256,
                       projection.latest_snapshot_complete,
                       projection.matching_retry_count
                FROM projection
                {filter_sql}
                {order_sql}
                LIMIT ?
                """,
                query_params,
            ).fetchall()
            states: list[SettlementPollState] = []
            for (
                venue_raw,
                market_id_raw,
                first_decision_at_raw,
                candidate_count_raw,
                latest_attempted_at_raw,
                latest_status_raw,
                latest_snapshot_count_raw,
                latest_snapshot_sha256_raw,
                latest_snapshot_complete_raw,
                retry_count_raw,
            ) in groups:
                venue = Venue(str(venue_raw))
                venue_market_id = str(market_id_raw)
                candidate_count = int(candidate_count_raw)
                complete = candidate_count <= MAX_SETTLEMENT_CANDIDATES_PER_MARKET
                candidate_set_sha256: str | None = None
                if complete:
                    candidates = conn.execute(
                        """
                        SELECT candidate_id, identity_json
                        FROM capital_guard_shadow_candidates
                        WHERE venue = ? AND venue_market_id = ?
                        ORDER BY decision_at, candidate_id
                        LIMIT ?
                        """,
                        (venue.value, venue_market_id, MAX_SETTLEMENT_CANDIDATES_PER_MARKET),
                    ).fetchall()
                    candidate_evidence = [
                        {
                            "candidate_id": str(candidate_id_raw),
                            "identity_sha256": _sha256(str(identity_json_raw)),
                        }
                        for candidate_id_raw, identity_json_raw in candidates
                    ]
                    candidate_set_sha256 = _sha256(canonical_json(candidate_evidence))

                if latest_attempted_at_raw is None:
                    latest_attempted_at = None
                    latest_status = None
                    latest_snapshot_count = None
                    latest_snapshot_sha256 = None
                    latest_snapshot_complete = None
                else:
                    latest_attempted_at = _parse_timestamp(str(latest_attempted_at_raw))
                    latest_status = str(latest_status_raw)
                    latest_snapshot_count = int(latest_snapshot_count_raw)
                    latest_snapshot_sha256 = (
                        None if latest_snapshot_sha256_raw is None else str(latest_snapshot_sha256_raw)
                    )
                    latest_snapshot_complete = bool(int(latest_snapshot_complete_raw))

                retry_count = int(retry_count_raw)
                states.append(
                    SettlementPollState(
                        market_key=SettlementMarketKey(venue, venue_market_id),
                        first_decision_at=_parse_timestamp(str(first_decision_at_raw)),
                        current_candidate_count=candidate_count,
                        current_candidate_set_sha256=candidate_set_sha256,
                        current_candidate_set_complete=complete,
                        latest_attempted_at=latest_attempted_at,
                        latest_status=latest_status,
                        latest_snapshot_count=latest_snapshot_count,
                        latest_snapshot_sha256=latest_snapshot_sha256,
                        latest_snapshot_complete=latest_snapshot_complete,
                        matching_snapshot_retry_count=retry_count,
                    )
                )
            return tuple(states)

        return self._read(read)

    def candidate_settlement_backlog(
        self,
        market_key: SettlementMarketKey,
        *,
        limit: int = MAX_SETTLEMENT_CANDIDATES_PER_MARKET,
    ) -> CandidateSettlementBacklog:
        if not isinstance(market_key, SettlementMarketKey):
            raise TypeError("market_key must be SettlementMarketKey")
        _require_bounded_limit("limit", limit, maximum=MAX_SETTLEMENT_CANDIDATES_PER_MARKET)
        return self._read(lambda conn: self._candidate_settlement_backlog_transaction(conn, market_key, limit=limit))

    def current_authoritative_head(
        self,
        market_ref: MarketRef,
    ) -> CurrentAuthoritativeHead | None:
        if not isinstance(market_ref, MarketRef):
            raise TypeError("market_ref must be MarketRef")
        return self._read(
            lambda conn: self._current_authoritative_head_transaction(
                conn,
                market_ref.venue,
                market_ref.venue_market_id,
                expected_alias=market_ref.alias,
            )
        )

    def current_head_settlements(
        self,
        market_ref: MarketRef,
        *,
        limit: int = MAX_SETTLEMENT_CANDIDATES_PER_MARKET,
    ) -> tuple[CurrentHeadSettlement, ...]:
        if not isinstance(market_ref, MarketRef):
            raise TypeError("market_ref must be MarketRef")
        _require_bounded_limit("limit", limit, maximum=MAX_SETTLEMENT_CANDIDATES_PER_MARKET)

        def read(conn: sqlite3.Connection) -> tuple[CurrentHeadSettlement, ...]:
            self._validate_schema(conn)
            head = self._current_authoritative_head_transaction(
                conn,
                market_ref.venue,
                market_ref.venue_market_id,
                expected_alias=market_ref.alias,
            )
            if head is None:
                return ()
            rows = conn.execute(
                """
                SELECT settlement_id, candidate_id, observation_sha256,
                       outcome, settled_at
                FROM capital_guard_shadow_settlements
                WHERE observation_sha256 = ?
                ORDER BY candidate_id
                LIMIT ?
                """,
                (head.observation_sha256, limit + 1),
            ).fetchall()
            if len(rows) > limit:
                raise ValueError("current-head settlement result exceeds bounded limit")
            return tuple(
                CurrentHeadSettlement(
                    settlement_id=str(row[0]),
                    candidate_id=str(row[1]),
                    observation_sha256=str(row[2]),
                    outcome=str(row[3]),
                    settled_at=_parse_timestamp(str(row[4])),
                )
                for row in rows
            )

        return self._read(read)

    def record_settlement_attempt(
        self,
        backlog: CandidateSettlementBacklog,
        *,
        attempted_at: datetime,
        status: Literal[
            "nonterminal",
            "not_found",
            "transient_error",
            "internal_error",
            "quarantined",
            "terminal",
        ],
        observation: SettlementObservation | None = None,
        error_taxonomy: str | None = None,
        error_sha256: str | None = None,
        quarantine_reason: str | None = None,
    ) -> SettlementAttemptWriteResult:
        if not isinstance(backlog, CandidateSettlementBacklog):
            raise TypeError("backlog must be CandidateSettlementBacklog")
        _require_utc_datetime("attempted_at", attempted_at)
        return self._write(
            lambda conn: self._record_settlement_attempt_transaction(
                conn,
                backlog,
                attempted_at=attempted_at,
                status=status,
                observation=observation,
                error_taxonomy=error_taxonomy,
                error_sha256=error_sha256,
                quarantine_reason=quarantine_reason,
            )
        )

    def record_settlement_identity_quarantine(
        self,
        error: CapitalGuardShadowIdentityError,
        *,
        attempted_at: datetime,
    ) -> SettlementAttemptWriteResult:
        if not isinstance(error, CapitalGuardShadowIdentityError):
            raise TypeError("error must be CapitalGuardShadowIdentityError")
        _require_utc_datetime("attempted_at", attempted_at)
        return self._write(
            lambda conn: self._record_identity_quarantine_transaction(conn, error, attempted_at=attempted_at)
        )

    def append_settlement(self, record: ShadowSettlement) -> SettlementWriteResult:
        if not isinstance(record, ShadowSettlement):
            raise TypeError("record must be ShadowSettlement")
        return self._write(lambda conn: self._append_settlement_transaction(conn, record))

    def append_evaluation(self, record: ShadowEvaluation) -> EvaluationWriteResult:
        if not isinstance(record, ShadowEvaluation):
            raise TypeError("record must be ShadowEvaluation")
        return self._write(lambda conn: self._append_evaluation_transaction(conn, record))

    def canonical_database_sha256(self) -> str:
        def read(conn: sqlite3.Connection) -> str:
            self._validate_schema(conn)
            content: dict[str, object] = {
                "ddl_sha256": CAPITAL_GUARD_SHADOW_DDL_SHA256,
                "schema_version": CAPITAL_GUARD_SHADOW_SCHEMA_VERSION,
                "tables": {},
            }
            tables = content["tables"]
            assert isinstance(tables, dict)
            for table in _TABLE_NAMES:
                if table == "capital_guard_shadow_schema_meta":
                    continue
                columns = [str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")]
                records = [dict(zip(columns, row, strict=True)) for row in conn.execute(f"SELECT * FROM {table}")]
                tables[table] = sorted(records, key=canonical_json)
            return _sha256(canonical_json(content))

        return self._read(read)

    def _connect(self) -> sqlite3.Connection:
        connect_target: str | Path
        connect_kwargs: dict[str, object] = {
            "timeout": _BUSY_TIMEOUT_MS / 1_000,
            "isolation_level": None,
        }
        if self.existing_only:
            connect_target = f"{self.db_path.resolve(strict=False).as_uri()}?mode=rw"
            connect_kwargs["uri"] = True
        else:
            connect_target = self.db_path
        conn = _SQLITE_CONNECT(connect_target, **connect_kwargs)
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
            conn.execute("PRAGMA journal_mode=WAL")
        except BaseException:
            conn.close()
            raise
        return conn

    def _read(self, operation: Callable[[sqlite3.Connection], _T]) -> _T:
        conn = self._connect()
        try:
            conn.execute("BEGIN")
            result = operation(conn)
            conn.commit()
            return result
        except BaseException:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()

    def _write(self, operation: Callable[[sqlite3.Connection], _T]) -> _T:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            self._validate_schema(conn)
            result = operation(conn)
            conn.commit()
            return result
        except BaseException:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()

    def _validate_schema(self, conn: sqlite3.Connection) -> None:
        objects = _user_schema_objects(conn)
        actual_types = {name: kind for kind, name, _sql in objects}
        if actual_types != _TARGET_OBJECT_TYPES:
            raise CapitalGuardShadowSchemaError("capital guard shadow schema drift")
        for kind, name, sql in objects:
            if kind != _TARGET_OBJECT_TYPES[name] or _normalize_schema_sql(sql) != _TARGET_SQL[name]:
                raise CapitalGuardShadowSchemaError("capital guard shadow schema drift")
        meta = conn.execute(
            "SELECT schema_version, ddl_sha256, applied_at FROM capital_guard_shadow_schema_meta"
        ).fetchall()
        if len(meta) != 1 or meta[0][0:2] != (
            CAPITAL_GUARD_SHADOW_SCHEMA_VERSION,
            CAPITAL_GUARD_SHADOW_DDL_SHA256,
        ):
            raise CapitalGuardShadowSchemaError("capital guard shadow schema drift")
        try:
            _parse_timestamp(str(meta[0][2]))
        except ValueError as exc:
            raise CapitalGuardShadowSchemaError("capital guard shadow schema drift") from exc
        if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise CapitalGuardShadowSchemaError("capital guard shadow foreign-key drift")

    def _candidate_settlement_backlog_transaction(
        self,
        conn: sqlite3.Connection,
        market_key: SettlementMarketKey,
        *,
        limit: int,
    ) -> CandidateSettlementBacklog:
        self._validate_schema(conn)
        count = int(
            conn.execute(
                "SELECT COUNT(*) FROM capital_guard_shadow_candidates WHERE venue = ? AND venue_market_id = ?",
                (market_key.venue.value, market_key.venue_market_id),
            ).fetchone()[0]
        )
        if count == 0:
            raise ValueError("candidate settlement backlog is empty")
        rows = conn.execute(
            """
            SELECT candidate_id, identity_json
            FROM capital_guard_shadow_candidates
            WHERE venue = ? AND venue_market_id = ?
            ORDER BY decision_at, candidate_id
            LIMIT ?
            """,
            (market_key.venue.value, market_key.venue_market_id, limit + 1),
        ).fetchall()
        if count > limit:
            sample = [{"candidate_id": str(row[0]), "identity_sha256": _sha256(str(row[1]))} for row in rows]
            raise CapitalGuardShadowIdentityError(
                "candidate settlement backlog exceeds bounded limit",
                market_key=market_key,
                reason_taxonomy="candidate_group_over_cap",
                candidate_count=count,
                candidate_set_sha256=None,
                identity_set_sha256=None,
                identity_sample_sha256=_sha256(canonical_json(sample)),
            )

        identities: list[dict[str, object]] = []
        candidate_ids = [str(row[0]) for row in rows]
        candidate_evidence = [
            {
                "candidate_id": str(row[0]),
                "identity_sha256": _sha256(str(row[1])),
            }
            for row in rows
        ]
        candidate_set_sha256 = _sha256(canonical_json(candidate_evidence))
        raw_identity_set_sha256 = _sha256(
            canonical_json(sorted({evidence["identity_sha256"] for evidence in candidate_evidence}))
        )
        for _candidate_id_raw, identity_json_raw in rows:
            identity_json = str(identity_json_raw)
            try:
                identity = _require_canonical_object("identity_json", identity_json)
                required = {
                    "alias",
                    "contract_fingerprint",
                    "decision_key",
                    "lifecycle_id",
                    "rules_fingerprint",
                    "schema_version",
                    "settlement_fingerprint",
                    "venue",
                    "venue_market_id",
                }
                _require_exact_keys("identity_json", identity, required)
                if (
                    identity["venue"] != market_key.venue.value
                    or identity["venue_market_id"] != market_key.venue_market_id
                ):
                    raise ValueError("identity_json does not match candidate market")
            except (KeyError, TypeError, ValueError) as exc:
                raise CapitalGuardShadowIdentityError(
                    "candidate settlement identity is ambiguous",
                    market_key=market_key,
                    reason_taxonomy="identity_ambiguous",
                    candidate_count=count,
                    candidate_set_sha256=candidate_set_sha256,
                    identity_set_sha256=raw_identity_set_sha256,
                ) from exc
            singular = {
                key: identity[key]
                for key in (
                    "alias",
                    "contract_fingerprint",
                    "rules_fingerprint",
                    "settlement_fingerprint",
                )
            }
            for name, value in singular.items():
                try:
                    _require_text(f"identity_json.{name}", value)
                except ValueError as exc:
                    raise CapitalGuardShadowIdentityError(
                        "candidate settlement identity is ambiguous",
                        market_key=market_key,
                        reason_taxonomy="identity_ambiguous",
                        candidate_count=count,
                        candidate_set_sha256=candidate_set_sha256,
                        identity_set_sha256=raw_identity_set_sha256,
                    ) from exc
            identities.append(singular)

        identity_set = sorted({canonical_json(identity) for identity in identities})
        identity_set_sha256 = _sha256(canonical_json(identity_set))
        if len(identity_set) != 1:
            raise CapitalGuardShadowIdentityError(
                "candidate settlement identity is ambiguous",
                market_key=market_key,
                reason_taxonomy="identity_ambiguous",
                candidate_count=count,
                candidate_set_sha256=candidate_set_sha256,
                identity_set_sha256=identity_set_sha256,
            )
        identity = json.loads(identity_set[0])
        alias = str(identity["alias"])
        if market_key.venue is Venue.KALSHI and alias != market_key.venue_market_id:
            raise CapitalGuardShadowIdentityError(
                "Kalshi candidate settlement alias is ambiguous",
                market_key=market_key,
                reason_taxonomy="identity_ambiguous",
                candidate_count=count,
                candidate_set_sha256=candidate_set_sha256,
                identity_set_sha256=identity_set_sha256,
            )
        market_ref = MarketRef(market_key.venue, market_key.venue_market_id, alias)
        prior_authoritative_observation = None
        authoritative_head_error = None
        try:
            head = self._current_authoritative_head_transaction(
                conn,
                market_key.venue,
                market_key.venue_market_id,
                expected_alias=alias,
            )
        except (SettlementDriftError, TypeError, ValueError):
            head = None
            authoritative_head_error = "authoritative_head_invalid"
        if head is not None:
            try:
                prior_authoritative_observation = _rehydrate_current_authoritative_observation(head)
            except (SettlementDriftError, TypeError, ValueError):
                authoritative_head_error = "authoritative_head_invalid"
        if head is None or authoritative_head_error is not None:
            missing = tuple(candidate_ids)
        else:
            linked = {
                str(row[0])
                for row in conn.execute(
                    "SELECT candidate_id FROM capital_guard_shadow_candidate_observations WHERE observation_sha256 = ?",
                    (head.observation_sha256,),
                )
            }
            missing = tuple(candidate_id for candidate_id in candidate_ids if candidate_id not in linked)
        return CandidateSettlementBacklog(
            market_ref=market_ref,
            contract_fingerprint=str(identity["contract_fingerprint"]),
            rules_fingerprint=str(identity["rules_fingerprint"]),
            settlement_fingerprint=str(identity["settlement_fingerprint"]),
            candidate_ids=tuple(candidate_ids),
            missing_link_candidate_ids=missing,
            candidate_set_sha256=candidate_set_sha256,
            identity_set_sha256=identity_set_sha256,
            current_head_sha256=(
                head.observation_sha256 if head is not None and authoritative_head_error is None else None
            ),
            prior_authoritative_observation=prior_authoritative_observation,
            authoritative_head_error=authoritative_head_error,
        )

    def _current_authoritative_head_transaction(
        self,
        conn: sqlite3.Connection,
        venue: Venue,
        venue_market_id: str,
        *,
        expected_alias: str | None = None,
    ) -> CurrentAuthoritativeHead | None:
        self._validate_schema(conn)
        rows = conn.execute(
            """
            SELECT o.observation_sha256, o.alias, o.contract_fingerprint,
                   o.rules_fingerprint, o.settlement_fingerprint, o.outcome,
                   o.observed_at, o.effective_at, o.source_id, o.rules_version,
                   o.authoritative_outcome_json, o.source_payload_json,
                   o.authoritative_payload_sha256,
                    o.authoritative_observation_sha256,
                    o.void_refund_json, o.void_refund_sha256, o.semantic_sha256,
                    o.supersedes_observation_sha256
            FROM capital_guard_shadow_observations o
            WHERE o.venue = ? AND o.venue_market_id = ?
              AND NOT EXISTS (
                  SELECT 1 FROM capital_guard_shadow_observations child
                  WHERE child.supersedes_observation_sha256 = o.observation_sha256
              )
            """,
            (venue.value, venue_market_id),
        ).fetchall()
        if not rows:
            return None
        if len(rows) != 1:
            raise ValueError("observation market has ambiguous current heads")
        row = rows[0]
        alias = str(row[1])
        if expected_alias is not None and alias != expected_alias:
            raise ValueError("authoritative head alias differs from candidate identity")
        return CurrentAuthoritativeHead(
            observation_sha256=str(row[0]),
            market_ref=MarketRef(venue, venue_market_id, alias),
            contract_fingerprint=str(row[2]),
            rules_fingerprint=str(row[3]),
            settlement_fingerprint=str(row[4]),
            outcome=str(row[5]),
            observed_at=_parse_timestamp(str(row[6])),
            effective_at=_parse_timestamp(str(row[7])),
            source_id=str(row[8]),
            rules_version=str(row[9]),
            authoritative_outcome_json=str(row[10]),
            source_payload_json=str(row[11]),
            authoritative_payload_sha256=str(row[12]),
            authoritative_observation_sha256=str(row[13]),
            void_refund_json=None if row[14] is None else str(row[14]),
            void_refund_sha256=None if row[15] is None else str(row[15]),
            semantic_sha256=str(row[16]),
            supersedes_observation_sha256=(None if row[17] is None else str(row[17])),
        )

    def _record_identity_quarantine_transaction(
        self,
        conn: sqlite3.Connection,
        error: CapitalGuardShadowIdentityError,
        *,
        attempted_at: datetime,
    ) -> SettlementAttemptWriteResult:
        try:
            head = self._current_authoritative_head_transaction(
                conn,
                error.market_key.venue,
                error.market_key.venue_market_id,
            )
            if head is not None:
                _rehydrate_current_authoritative_observation(head)
        except (SettlementDriftError, TypeError, ValueError):
            head = None
        complete = error.reason_taxonomy != "candidate_group_over_cap"
        payload = _settlement_attempt_payload(
            venue=error.market_key.venue,
            venue_market_id=error.market_key.venue_market_id,
            alias=None,
            contract_fingerprint=None,
            rules_fingerprint=None,
            settlement_fingerprint=None,
            identity_set_sha256=error.identity_set_sha256,
            identity_sample_sha256=error.identity_sample_sha256,
            candidate_set_sha256=error.candidate_set_sha256,
            candidate_set_complete=complete,
            candidate_count=error.candidate_count,
            attempted_at=attempted_at,
            status="quarantined",
            head_before_sha256=head.observation_sha256 if head is not None else None,
            head_after_sha256=head.observation_sha256 if head is not None else None,
            error_taxonomy=error.reason_taxonomy,
            error_sha256=_sha256(error.reason_taxonomy),
        )
        return self._persist_settlement_attempt_transaction(
            conn,
            payload,
            quarantine_reason=error.reason_taxonomy,
        )

    def _record_settlement_attempt_transaction(
        self,
        conn: sqlite3.Connection,
        backlog: CandidateSettlementBacklog,
        *,
        attempted_at: datetime,
        status: str,
        observation: SettlementObservation | None,
        error_taxonomy: str | None,
        error_sha256: str | None,
        quarantine_reason: str | None,
    ) -> SettlementAttemptWriteResult:
        market_key = SettlementMarketKey(backlog.market_ref.venue, backlog.market_ref.venue_market_id)
        try:
            current = self._candidate_settlement_backlog_transaction(
                conn,
                market_key,
                limit=MAX_SETTLEMENT_CANDIDATES_PER_MARKET,
            )
        except CapitalGuardShadowIdentityError:
            current = None
        state_matches = current is not None and (
            current.market_ref == backlog.market_ref
            and current.contract_fingerprint == backlog.contract_fingerprint
            and current.rules_fingerprint == backlog.rules_fingerprint
            and current.settlement_fingerprint == backlog.settlement_fingerprint
            and current.candidate_ids == backlog.candidate_ids
            and current.candidate_set_sha256 == backlog.candidate_set_sha256
            and current.identity_set_sha256 == backlog.identity_set_sha256
            and current.current_head_sha256 == backlog.current_head_sha256
            and (current.prior_authoritative_observation == backlog.prior_authoritative_observation)
            and current.authoritative_head_error == backlog.authoritative_head_error
        )
        if not state_matches:
            return self._persist_valid_identity_attempt(
                conn,
                backlog,
                attempted_at=attempted_at,
                status="quarantined",
                head_after_sha256=(current.current_head_sha256 if current is not None else None),
                error_taxonomy="concurrent_state_change",
                error_sha256=_sha256("concurrent_state_change"),
                quarantine_reason="concurrent_state_change",
            )
        assert current is not None
        if current.authoritative_head_error is not None:
            return self._persist_valid_identity_attempt(
                conn,
                backlog,
                attempted_at=attempted_at,
                status="quarantined",
                head_after_sha256=None,
                error_taxonomy="source_drift",
                error_sha256=_sha256(current.authoritative_head_error),
                quarantine_reason="source_drift",
            )

        market_conflict = _market_has_observation_conflict(conn, market_key)
        if market_conflict:
            return self._persist_valid_identity_attempt(
                conn,
                backlog,
                attempted_at=attempted_at,
                status="quarantined",
                head_after_sha256=current.current_head_sha256,
                error_taxonomy="observation_fork",
                error_sha256=_sha256("observation_fork"),
                quarantine_reason="observation_fork",
            )

        if status != "terminal":
            if observation is not None:
                raise ValueError("nonterminal attempt cannot carry an observation")
            return self._persist_valid_identity_attempt(
                conn,
                backlog,
                attempted_at=attempted_at,
                status=status,
                error_taxonomy=error_taxonomy,
                error_sha256=error_sha256,
                quarantine_reason=quarantine_reason,
            )

        if not isinstance(observation, SettlementObservation):
            return self._persist_valid_identity_attempt(
                conn,
                backlog,
                attempted_at=attempted_at,
                status="quarantined",
                error_taxonomy="source_drift",
                error_sha256=_sha256("source_drift"),
                quarantine_reason="source_drift",
            )
        if observation.market_ref != backlog.market_ref:
            return self._persist_valid_identity_attempt(
                conn,
                backlog,
                attempted_at=attempted_at,
                status="quarantined",
                error_taxonomy="source_drift",
                error_sha256=_sha256("source_drift"),
                quarantine_reason="source_drift",
            )
        try:
            _require_utc_datetime("observation.observed_at", observation.observed_at)
            _require_utc_datetime("observation.effective_at", observation.effective_at)
        except ValueError:
            return self._persist_valid_identity_attempt(
                conn,
                backlog,
                attempted_at=attempted_at,
                status="quarantined",
                error_taxonomy="source_drift",
                error_sha256=_sha256("source_drift"),
                quarantine_reason="source_drift",
            )

        void_refund_json, void_refund_sha256 = _void_refund_payload(observation.void_refund)
        semantic_sha256 = _source_settlement_semantic_sha256(
            observation,
            contract_fingerprint=backlog.contract_fingerprint,
            rules_fingerprint=backlog.rules_fingerprint,
            settlement_fingerprint=backlog.settlement_fingerprint,
        )
        authority = {
            "outcome": observation.outcome.value,
            "source_id": observation.source_id,
            "rules_version": observation.rules_version,
            "authoritative_outcome_json": observation.authoritative_outcome_json,
            "authoritative_payload_sha256": observation.payload_sha256,
            "authoritative_observation_sha256": observation.observation_sha256,
            "semantic_sha256": semantic_sha256,
            "void_refund_json": void_refund_json,
            "void_refund_sha256": void_refund_sha256,
        }
        head = self._current_authoritative_head_transaction(
            conn,
            backlog.market_ref.venue,
            backlog.market_ref.venue_market_id,
            expected_alias=backlog.market_ref.alias,
        )
        if head is None:
            source_lineage_matches = observation.supersedes_observation_sha256 is None
        elif head.semantic_sha256 == semantic_sha256:
            source_lineage_matches = observation.supersedes_observation_sha256 is None
        else:
            source_lineage_matches = observation.supersedes_observation_sha256 == head.authoritative_observation_sha256
        if not source_lineage_matches:
            return self._persist_valid_identity_attempt(
                conn,
                backlog,
                attempted_at=attempted_at,
                status="quarantined",
                head_after_sha256=(head.observation_sha256 if head is not None else None),
                error_taxonomy="source_drift",
                error_sha256=_sha256("source_drift"),
                quarantine_reason="source_drift",
                **authority,
            )
        if observation.outcome is MarketOutcome.VOID:
            return self._persist_valid_identity_attempt(
                conn,
                backlog,
                attempted_at=attempted_at,
                status="quarantined",
                head_after_sha256=current.current_head_sha256,
                error_taxonomy="void_financial_economics_deferred",
                error_sha256=_sha256("void_financial_economics_deferred"),
                quarantine_reason="void_financial_economics_deferred",
                **authority,
            )
        if observation.outcome not in (MarketOutcome.YES, MarketOutcome.NO):
            return self._persist_valid_identity_attempt(
                conn,
                backlog,
                attempted_at=attempted_at,
                status="quarantined",
                error_taxonomy="financial_ambiguity",
                error_sha256=_sha256("financial_ambiguity"),
                quarantine_reason="financial_ambiguity",
            )

        if head is not None and head.semantic_sha256 == semantic_sha256:
            attempt_payload = self._valid_identity_attempt_payload(
                backlog,
                attempted_at=attempted_at,
                status="terminal",
                head_after_sha256=head.observation_sha256,
                **authority,
            )
            preflight = self._preflight_settlement_attempt_transaction(conn, attempt_payload)
            if preflight is not None:
                return preflight
            self._link_candidates_transaction(
                conn,
                head.observation_sha256,
                current.missing_link_candidate_ids,
                linked_at=attempted_at,
            )
            persisted = self._persist_settlement_attempt_transaction(conn, attempt_payload, quarantine_reason=None)
            return SettlementAttemptWriteResult(
                status=persisted.status,
                attempt_id=persisted.attempt_id,
                attempt_status=persisted.attempt_status,
                observation_status="identical",
                observation_sha256=head.observation_sha256,
            )
        if head is not None and (
            observation.observed_at <= head.observed_at or observation.effective_at < head.effective_at
        ):
            return self._persist_valid_identity_attempt(
                conn,
                backlog,
                attempted_at=attempted_at,
                status="quarantined",
                head_after_sha256=head.observation_sha256,
                error_taxonomy="backward_time",
                error_sha256=_sha256("backward_time"),
                quarantine_reason="backward_time",
                **authority,
            )

        record = SettlementObservationRecord(
            venue=backlog.market_ref.venue,
            venue_market_id=backlog.market_ref.venue_market_id,
            alias=backlog.market_ref.alias,
            contract_fingerprint=backlog.contract_fingerprint,
            rules_fingerprint=backlog.rules_fingerprint,
            settlement_fingerprint=backlog.settlement_fingerprint,
            outcome=observation.outcome.value,
            observed_at=observation.observed_at,
            effective_at=observation.effective_at,
            source_id=observation.source_id,
            rules_version=observation.rules_version,
            authoritative_outcome_json=observation.authoritative_outcome_json,
            source_payload_json=observation.canonical_payload_json,
            authoritative_payload_sha256=observation.payload_sha256,
            authoritative_observation_sha256=observation.observation_sha256,
            semantic_sha256=semantic_sha256,
            supersedes_observation_sha256=(head.observation_sha256 if head is not None else None),
        )
        predicted_observation_sha256 = _sha256(canonical_json(_observation_payload(record)))
        attempt_payload = self._valid_identity_attempt_payload(
            backlog,
            attempted_at=attempted_at,
            status="terminal",
            head_after_sha256=predicted_observation_sha256,
            **authority,
        )
        preflight = self._preflight_settlement_attempt_transaction(conn, attempt_payload)
        if preflight is not None:
            return preflight
        observed = self._append_observation_transaction(conn, record, current.candidate_ids)
        if observed.status == "conflict":
            return self._persist_valid_identity_attempt(
                conn,
                backlog,
                attempted_at=attempted_at,
                status="quarantined",
                head_after_sha256=head.observation_sha256 if head is not None else None,
                error_taxonomy="observation_fork",
                error_sha256=_sha256("observation_fork"),
                quarantine_reason="observation_fork",
                **authority,
            )
        persisted = self._persist_settlement_attempt_transaction(conn, attempt_payload, quarantine_reason=None)
        return SettlementAttemptWriteResult(
            status=persisted.status,
            attempt_id=persisted.attempt_id,
            attempt_status=persisted.attempt_status,
            observation_status=observed.status,
            observation_sha256=observed.observation_sha256,
        )

    def _persist_valid_identity_attempt(
        self,
        conn: sqlite3.Connection,
        backlog: CandidateSettlementBacklog,
        *,
        attempted_at: datetime,
        status: str,
        head_after_sha256: str | None = None,
        error_taxonomy: str | None = None,
        error_sha256: str | None = None,
        quarantine_reason: str | None = None,
        outcome: str | None = None,
        source_id: str | None = None,
        rules_version: str | None = None,
        authoritative_outcome_json: str | None = None,
        authoritative_payload_sha256: str | None = None,
        authoritative_observation_sha256: str | None = None,
        semantic_sha256: str | None = None,
        void_refund_json: str | None = None,
        void_refund_sha256: str | None = None,
        observation_status: str | None = None,
        observation_sha256: str | None = None,
    ) -> SettlementAttemptWriteResult:
        payload = self._valid_identity_attempt_payload(
            backlog,
            attempted_at=attempted_at,
            status=status,
            head_after_sha256=head_after_sha256,
            error_taxonomy=error_taxonomy,
            error_sha256=error_sha256,
            outcome=outcome,
            source_id=source_id,
            rules_version=rules_version,
            authoritative_outcome_json=authoritative_outcome_json,
            authoritative_payload_sha256=authoritative_payload_sha256,
            authoritative_observation_sha256=authoritative_observation_sha256,
            semantic_sha256=semantic_sha256,
            void_refund_json=void_refund_json,
            void_refund_sha256=void_refund_sha256,
        )
        result = self._persist_settlement_attempt_transaction(conn, payload, quarantine_reason=quarantine_reason)
        if result.status == "conflict":
            return result
        return SettlementAttemptWriteResult(
            status=result.status,
            attempt_id=result.attempt_id,
            attempt_status=result.attempt_status,
            observation_status=observation_status,
            observation_sha256=observation_sha256,
        )

    @staticmethod
    def _valid_identity_attempt_payload(
        backlog: CandidateSettlementBacklog,
        *,
        attempted_at: datetime,
        status: str,
        head_after_sha256: str | None = None,
        error_taxonomy: str | None = None,
        error_sha256: str | None = None,
        outcome: str | None = None,
        source_id: str | None = None,
        rules_version: str | None = None,
        authoritative_outcome_json: str | None = None,
        authoritative_payload_sha256: str | None = None,
        authoritative_observation_sha256: str | None = None,
        semantic_sha256: str | None = None,
        void_refund_json: str | None = None,
        void_refund_sha256: str | None = None,
    ) -> dict[str, object]:
        return _settlement_attempt_payload(
            venue=backlog.market_ref.venue,
            venue_market_id=backlog.market_ref.venue_market_id,
            alias=backlog.market_ref.alias,
            contract_fingerprint=backlog.contract_fingerprint,
            rules_fingerprint=backlog.rules_fingerprint,
            settlement_fingerprint=backlog.settlement_fingerprint,
            identity_set_sha256=backlog.identity_set_sha256,
            candidate_set_sha256=backlog.candidate_set_sha256,
            candidate_set_complete=True,
            candidate_count=len(backlog.candidate_ids),
            attempted_at=attempted_at,
            status=status,
            outcome=outcome,
            source_id=source_id,
            rules_version=rules_version,
            authoritative_outcome_json=authoritative_outcome_json,
            authoritative_payload_sha256=authoritative_payload_sha256,
            authoritative_observation_sha256=authoritative_observation_sha256,
            semantic_sha256=semantic_sha256,
            void_refund_json=void_refund_json,
            void_refund_sha256=void_refund_sha256,
            head_before_sha256=backlog.current_head_sha256,
            head_after_sha256=head_after_sha256,
            error_taxonomy=error_taxonomy,
            error_sha256=error_sha256,
        )

    def _preflight_settlement_attempt_transaction(
        self,
        conn: sqlite3.Connection,
        payload: dict[str, object],
    ) -> SettlementAttemptWriteResult | None:
        attempt_id = _settlement_attempt_id(payload)
        payload_sha256 = _sha256(canonical_json(payload))
        existing = conn.execute(
            "SELECT payload_sha256, status FROM capital_guard_shadow_settlement_attempts WHERE attempt_id = ?",
            (attempt_id,),
        ).fetchone()
        if existing is None:
            return None
        if str(existing[0]) == payload_sha256:
            return SettlementAttemptWriteResult("identical", attempt_id, str(existing[1]))
        conflict_id = _insert_conflict(
            conn,
            entity_type="settlement_attempt",
            entity_key=attempt_id,
            existing_sha256=str(existing[0]),
            incoming_sha256=payload_sha256,
            created_at=_parse_timestamp(str(payload["attempted_at"])),
        )
        return SettlementAttemptWriteResult("conflict", attempt_id, str(existing[1]), conflict_id=conflict_id)

    def _persist_settlement_attempt_transaction(
        self,
        conn: sqlite3.Connection,
        payload: dict[str, object],
        *,
        quarantine_reason: str | None,
    ) -> SettlementAttemptWriteResult:
        attempt_id = _settlement_attempt_id(payload)
        payload_sha256 = _sha256(canonical_json(payload))
        existing = conn.execute(
            "SELECT payload_sha256, status FROM capital_guard_shadow_settlement_attempts WHERE attempt_id = ?",
            (attempt_id,),
        ).fetchone()
        if existing is not None:
            if str(existing[0]) == payload_sha256:
                return SettlementAttemptWriteResult("identical", attempt_id, str(existing[1]))
            conflict_id = _insert_conflict(
                conn,
                entity_type="settlement_attempt",
                entity_key=attempt_id,
                existing_sha256=str(existing[0]),
                incoming_sha256=payload_sha256,
                created_at=_parse_timestamp(str(payload["attempted_at"])),
            )
            return SettlementAttemptWriteResult("conflict", attempt_id, str(existing[1]), conflict_id=conflict_id)

        columns = tuple(payload)
        conn.execute(
            "INSERT INTO capital_guard_shadow_settlement_attempts "
            f"(attempt_id, payload_sha256, {', '.join(columns)}) "
            f"VALUES (?, ?, {', '.join('?' for _ in columns)})",
            (attempt_id, payload_sha256, *(payload[column] for column in columns)),
        )
        if quarantine_reason is not None:
            quarantine_id = _stable_id(
                "capital-guard-settlement-quarantine-v1",
                attempt_id,
                quarantine_reason,
            )
            conn.execute(
                "INSERT INTO capital_guard_shadow_settlement_quarantines "
                "(quarantine_id, attempt_id, reason_taxonomy, reason_sha256, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    quarantine_id,
                    attempt_id,
                    quarantine_reason,
                    _sha256(canonical_json({"reason_taxonomy": quarantine_reason})),
                    payload["attempted_at"],
                ),
            )
        return SettlementAttemptWriteResult("inserted", attempt_id, str(payload["status"]))

    def _link_candidates_transaction(
        self,
        conn: sqlite3.Connection,
        observation_sha256: str,
        candidate_ids: Sequence[str],
        *,
        linked_at: datetime,
    ) -> None:
        for candidate_id in candidate_ids:
            link_id = _stable_id(
                "capital-guard-observation-link-v1",
                candidate_id,
                observation_sha256,
            )
            conn.execute(
                "INSERT OR IGNORE INTO capital_guard_shadow_candidate_observations "
                "(link_id, candidate_id, observation_sha256, linked_at) VALUES (?, ?, ?, ?)",
                (link_id, candidate_id, observation_sha256, _timestamp(linked_at)),
            )

    def _append_candidate_transaction(
        self,
        conn: sqlite3.Connection,
        record: CapitalGuardCandidate,
    ) -> CandidateWriteResult:
        values = _candidate_payload(record)
        payload_sha256 = _sha256(canonical_json(values))
        candidate_id = _candidate_id(record)
        existing = conn.execute(
            "SELECT candidate_id, payload_sha256 FROM capital_guard_shadow_candidates WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()
        if existing is None:
            self._require_scorable_capture_attempt(conn, record)
            self._insert_candidate(conn, record)
            return CandidateWriteResult("inserted", candidate_id, payload_sha256)
        if tuple(existing) == (candidate_id, payload_sha256):
            return CandidateWriteResult("identical", candidate_id, payload_sha256)
        conflict_id = _insert_conflict(
            conn,
            entity_type="candidate",
            entity_key=candidate_id,
            existing_sha256=str(existing[1]),
            incoming_sha256=payload_sha256,
            created_at=record.captured_at,
        )
        return CandidateWriteResult("conflict", str(existing[0]), payload_sha256, conflict_id)

    def _append_capture_attempt_transaction(
        self,
        conn: sqlite3.Connection,
        record: CapitalGuardCaptureAttempt,
    ) -> CaptureAttemptWriteResult:
        values = _capture_attempt_payload(record)
        payload_sha256 = _sha256(canonical_json(values))
        capture_attempt_id = _capture_attempt_id(record)
        existing = conn.execute(
            "SELECT capture_attempt_id, payload_sha256 "
            "FROM capital_guard_shadow_capture_attempts "
            "WHERE capture_attempt_id = ?",
            (capture_attempt_id,),
        ).fetchone()
        if existing is None:
            conn.execute(
                """
                INSERT INTO capital_guard_shadow_capture_attempts (
                    capture_attempt_id, capture_version, payload_sha256,
                    decision_key, lifecycle_id, decision_at, captured_at,
                    venue, venue_market_id, market_family, side,
                    ordered_failures_json, non_gate_blocker,
                    claim_identity_json, gate_identity_json,
                    target_gate, target_failure, scorable,
                    ordered_unscorable_reasons_json, requested_stake_dollars,
                    partial_artifacts_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    capture_attempt_id,
                    record.capture_version,
                    payload_sha256,
                    record.decision_key,
                    record.lifecycle_id,
                    values["decision_at"],
                    values["captured_at"],
                    record.venue.value,
                    record.venue_market_id,
                    record.market_family,
                    record.side,
                    values["ordered_failures_json"],
                    record.non_gate_blocker,
                    values["claim_identity_json"],
                    values["gate_identity_json"],
                    record.target_gate,
                    record.target_failure,
                    int(record.scorable),
                    values["ordered_unscorable_reasons_json"],
                    values["requested_stake_dollars"],
                    record.partial_artifacts_json,
                ),
            )
            return CaptureAttemptWriteResult(
                "inserted",
                capture_attempt_id,
                payload_sha256,
            )
        if tuple(existing) == (capture_attempt_id, payload_sha256):
            return CaptureAttemptWriteResult(
                "identical",
                capture_attempt_id,
                payload_sha256,
            )
        conflict_id = _insert_conflict(
            conn,
            entity_type="capture_attempt",
            entity_key=capture_attempt_id,
            existing_sha256=str(existing[1]),
            incoming_sha256=payload_sha256,
            created_at=record.captured_at,
        )
        return CaptureAttemptWriteResult(
            "conflict",
            capture_attempt_id,
            payload_sha256,
            conflict_id,
        )

    def _require_scorable_capture_attempt(
        self,
        conn: sqlite3.Connection,
        record: CapitalGuardCandidate,
    ) -> None:
        capture_attempt_id = _capture_attempt_id(record)
        attempt = conn.execute(
            """
            SELECT decision_key, lifecycle_id, decision_at, captured_at,
                   venue, venue_market_id, market_family, side,
                   ordered_failures_json, non_gate_blocker, scorable,
                   ordered_unscorable_reasons_json, requested_stake_dollars
            FROM capital_guard_shadow_capture_attempts
            WHERE capture_attempt_id = ?
            """,
            (capture_attempt_id,),
        ).fetchone()
        if attempt is None:
            raise ValueError("candidate requires a matching capture attempt")
        expected_identity = (
            record.decision_key,
            record.lifecycle_id,
            _timestamp(record.decision_at),
            _timestamp(record.captured_at),
            record.venue.value,
            record.venue_market_id,
            record.market_family,
            record.side,
        )
        if tuple(attempt[0:8]) != expected_identity:
            raise ValueError("candidate identity does not match capture attempt")
        if str(attempt[8]) != canonical_json(list(record.ordered_failures)):
            raise ValueError("candidate failures do not match capture attempt")
        if attempt[9] != record.non_gate_blocker:
            raise ValueError("candidate blocker does not match capture attempt")
        if int(attempt[10]) != 1 or str(attempt[11]) != canonical_json([]):
            raise ValueError("candidate requires a scorable capture attempt")
        if attempt[12] is not None:
            requested_stake = _parse_decimal(
                "capture_attempt.requested_stake",
                str(attempt[12]),
            )
            gross_entry_debit, _entry_fee, _net_entry_debit = _entry_accounting(record)
            if requested_stake != gross_entry_debit:
                raise ValueError("candidate does not match capture attempt requested stake")

    def _insert_candidate(
        self,
        conn: sqlite3.Connection,
        record: CapitalGuardCandidate,
    ) -> None:
        values = _candidate_payload(record)
        payload_sha256 = _sha256(canonical_json(values))
        candidate_id = _candidate_id(record)
        conn.execute(
            """
            INSERT INTO capital_guard_shadow_candidates (
                candidate_id, capture_attempt_id, candidate_version,
                lifecycle_id, decision_key,
                payload_sha256, decision_at, captured_at,
                venue, venue_market_id, market_family, side, ordered_failures_json,
                non_gate_blocker, replay_eligible, gate_inputs_json, gate_results_json,
                identity_json, executable_book_json, book_observed_at, book_source,
                book_method, book_payload_sha256, expected_probability, executable_price_dollars,
                executable_quantity, gross_edge, sizing_json, fill_policy_json,
                fee_schedule_json, fee_provenance_json, fee_provenance_sha256,
                fee_formula_type, fee_role, fee_multiplier,
                fee_coefficient, fee_account_precision_dollars, fee_accumulator_dollars,
                gross_entry_debit_dollars, entry_fee_dollars, net_entry_debit_dollars
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                candidate_id,
                _capture_attempt_id(record),
                record.candidate_version,
                record.lifecycle_id,
                record.decision_key,
                payload_sha256,
                values["decision_at"],
                values["captured_at"],
                record.venue.value,
                record.venue_market_id,
                record.market_family,
                record.side,
                values["ordered_failures_json"],
                record.non_gate_blocker,
                int(record.replay_eligible),
                record.gate_inputs_json,
                record.gate_results_json,
                record.identity_json,
                record.executable_book_json,
                values["book_observed_at"],
                record.book_source,
                record.book_method,
                record.book_payload_sha256,
                values["expected_probability"],
                values["executable_price_dollars"],
                values["executable_quantity"],
                values["gross_edge"],
                record.sizing_json,
                record.fill_policy_json,
                record.fee_schedule_json,
                record.fee_provenance_json,
                record.fee_provenance_sha256,
                record.fee_formula_type,
                record.fee_role.value,
                values["fee_multiplier"],
                values["fee_coefficient"],
                values["fee_account_precision_dollars"],
                values["fee_accumulator_dollars"],
                values["gross_entry_debit_dollars"],
                values["entry_fee_dollars"],
                values["net_entry_debit_dollars"],
            ),
        )

    def _append_observation_transaction(
        self,
        conn: sqlite3.Connection,
        record: SettlementObservationRecord,
        candidate_ids: tuple[str, ...],
    ) -> ObservationWriteResult:
        payload = _observation_payload(record)
        observation_sha256 = _sha256(canonical_json(payload))
        for candidate_id in candidate_ids:
            candidate = conn.execute(
                "SELECT venue, venue_market_id, identity_json, decision_at "
                "FROM capital_guard_shadow_candidates "
                "WHERE candidate_id = ?",
                (candidate_id,),
            ).fetchone()
            if candidate is None:
                raise ValueError("candidate_id does not exist")
            if tuple(candidate[0:2]) != (record.venue.value, record.venue_market_id):
                raise ValueError("observation market identity does not match candidate")
            if _parse_timestamp(str(candidate[3])) >= record.observed_at:
                raise ValueError("candidate decision_at must precede observed outcome")
            identity = _require_canonical_object("identity_json", str(candidate[2]))
            expected_identity = {
                "alias": record.alias,
                "contract_fingerprint": record.contract_fingerprint,
                "rules_fingerprint": record.rules_fingerprint,
                "settlement_fingerprint": record.settlement_fingerprint,
            }
            if any(identity.get(key) != value for key, value in expected_identity.items()):
                raise ValueError("observation contract identity does not match candidate")
        market_key = _observation_market_key(record.venue, record.venue_market_id)
        root = conn.execute(
            """
            SELECT observation_sha256
            FROM capital_guard_shadow_observations
            WHERE venue = ? AND venue_market_id = ?
              AND supersedes_observation_sha256 IS NULL
            """,
            (record.venue.value, record.venue_market_id),
        ).fetchone()
        if record.supersedes_observation_sha256 is None:
            if root is not None and str(root[0]) != observation_sha256:
                conflict_id = _insert_conflict(
                    conn,
                    entity_type="observation_root",
                    entity_key=market_key,
                    existing_sha256=str(root[0]),
                    incoming_sha256=observation_sha256,
                    created_at=record.observed_at,
                )
                return ObservationWriteResult(
                    "conflict",
                    observation_sha256,
                    conflict_id,
                )
        elif _observation_root_is_ambiguous(conn, market_key):
            raise ValueError("observation market has an ambiguous observation root")
        if record.supersedes_observation_sha256 is not None:
            superseded = conn.execute(
                "SELECT venue, venue_market_id, observed_at, effective_at "
                "FROM capital_guard_shadow_observations WHERE observation_sha256 = ?",
                (record.supersedes_observation_sha256,),
            ).fetchone()
            if superseded is None:
                raise ValueError("superseded observation does not exist")
            if tuple(superseded[0:2]) != (record.venue.value, record.venue_market_id):
                raise ValueError("superseded observation market identity differs")
            if _parse_timestamp(str(superseded[2])) >= record.observed_at:
                raise ValueError("correction must follow superseded observation")
            if _parse_timestamp(str(superseded[3])) > record.effective_at:
                raise ValueError("correction effective_at must not precede superseded observation")
            successor = conn.execute(
                "SELECT observation_sha256 FROM capital_guard_shadow_observations "
                "WHERE supersedes_observation_sha256 = ?",
                (record.supersedes_observation_sha256,),
            ).fetchone()
            if successor is not None and str(successor[0]) != observation_sha256:
                conflict_id = _insert_conflict(
                    conn,
                    entity_type="observation_correction",
                    entity_key=record.supersedes_observation_sha256,
                    existing_sha256=str(successor[0]),
                    incoming_sha256=observation_sha256,
                    created_at=record.observed_at,
                )
                return ObservationWriteResult("conflict", observation_sha256, conflict_id)

        existing = conn.execute(
            "SELECT 1 FROM capital_guard_shadow_observations WHERE observation_sha256 = ?",
            (observation_sha256,),
        ).fetchone()
        if existing is None:
            conn.execute(
                """
                INSERT INTO capital_guard_shadow_observations (
                    observation_sha256, venue, venue_market_id, alias,
                    contract_fingerprint, rules_fingerprint, settlement_fingerprint,
                    outcome, observed_at, effective_at, source_id, rules_version,
                    authoritative_outcome_json, source_payload_json,
                    authoritative_payload_sha256, authoritative_observation_sha256,
                    void_refund_json, void_refund_sha256, semantic_sha256,
                    supersedes_observation_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    observation_sha256,
                    record.venue.value,
                    record.venue_market_id,
                    record.alias,
                    record.contract_fingerprint,
                    record.rules_fingerprint,
                    record.settlement_fingerprint,
                    record.outcome,
                    payload["observed_at"],
                    payload["effective_at"],
                    record.source_id,
                    record.rules_version,
                    record.authoritative_outcome_json,
                    record.source_payload_json,
                    record.authoritative_payload_sha256,
                    record.authoritative_observation_sha256,
                    record.void_refund_json,
                    record.void_refund_sha256,
                    record.semantic_sha256,
                    record.supersedes_observation_sha256,
                ),
            )
        for candidate_id in candidate_ids:
            link_id = _stable_id(
                "capital-guard-observation-link-v1",
                candidate_id,
                observation_sha256,
            )
            conn.execute(
                "INSERT OR IGNORE INTO capital_guard_shadow_candidate_observations "
                "(link_id, candidate_id, observation_sha256, linked_at) VALUES (?, ?, ?, ?)",
                (link_id, candidate_id, observation_sha256, payload["observed_at"]),
            )
        return ObservationWriteResult(
            "identical" if existing is not None else "inserted",
            observation_sha256,
        )

    def _append_settlement_transaction(
        self,
        conn: sqlite3.Connection,
        record: ShadowSettlement,
    ) -> SettlementWriteResult:
        joined = conn.execute(
            """
            SELECT c.side, c.executable_quantity, c.executable_price_dollars,
                   c.decision_at, c.fee_schedule_json, c.fee_formula_type,
                   c.fee_role, c.fee_multiplier, c.fee_coefficient,
                   c.fee_account_precision_dollars, c.fee_accumulator_dollars,
                   c.fill_policy_json, c.gross_entry_debit_dollars,
                   c.entry_fee_dollars, c.net_entry_debit_dollars, c.venue,
                   c.identity_json, o.outcome, o.effective_at, o.venue,
                    o.contract_fingerprint, o.rules_fingerprint,
                    o.settlement_fingerprint,
                    o.authoritative_observation_sha256, o.void_refund_json,
                    o.authoritative_payload_sha256, o.source_id,
                    o.source_payload_json, c.venue_market_id,
                    o.venue_market_id, o.alias
            FROM capital_guard_shadow_candidates c
            JOIN capital_guard_shadow_candidate_observations l
              ON l.candidate_id = c.candidate_id
            JOIN capital_guard_shadow_observations o
              ON o.observation_sha256 = l.observation_sha256
            WHERE c.candidate_id = ? AND o.observation_sha256 = ?
            """,
            (record.candidate_id, record.observation_sha256),
        ).fetchone()
        if joined is None:
            raise ValueError("settlement requires a linked candidate observation")
        if str(joined[17]) != record.outcome:
            raise ValueError("settlement outcome does not match observation")
        authoritative_settled_at = _parse_timestamp(str(joined[18]))
        if record.settled_at != authoritative_settled_at:
            raise ValueError("settled_at must equal authoritative effective_at")
        if _parse_timestamp(str(joined[3])) >= authoritative_settled_at:
            raise ValueError("candidate decision_at must precede authoritative effective_at")
        _require_unambiguous_observation_head(
            conn,
            record.candidate_id,
            record.observation_sha256,
        )
        binding = SettlementEconomicsBinding(
            venue=Venue(str(joined[19])),
            venue_market_id=str(joined[29]),
            account_party_id_sha256=record.economics_binding.account_party_id_sha256,
            contract_fingerprint=str(joined[20]),
            rules_fingerprint=str(joined[21]),
            settlement_fingerprint=str(joined[22]),
            authoritative_observation_sha256=str(joined[23]),
            authoritative_payload_sha256=str(joined[25]),
            source_id=str(joined[26]),
        )
        if record.economics_binding != binding:
            raise ValueError("settlement economics binding does not match linked observation")
        candidate_identity = _require_canonical_object("candidate.identity_json", str(joined[16]))
        expected_identity = {
            "alias": str(joined[30]),
            "contract_fingerprint": binding.contract_fingerprint,
            "rules_fingerprint": binding.rules_fingerprint,
            "settlement_fingerprint": binding.settlement_fingerprint,
            "venue": binding.venue.value,
            "venue_market_id": binding.venue_market_id,
        }
        if any(candidate_identity.get(key) != value for key, value in expected_identity.items()):
            raise ValueError("candidate contract identity does not match observation")
        if str(joined[15]) != binding.venue.value:
            raise ValueError("candidate venue does not match settlement observation")
        if str(joined[28]) != binding.venue_market_id:
            raise ValueError("candidate market identity does not match settlement observation")
        _gross_entry_debit, entry_fee, _net_entry_debit = _entry_accounting_from_persisted_values(
            decision_at=str(joined[3]),
            executable_price=str(joined[2]),
            executable_quantity=str(joined[1]),
            fee_schedule_json=str(joined[4]),
            fee_formula_type=str(joined[5]),
            fee_role=str(joined[6]),
            fee_multiplier=str(joined[7]),
            fee_coefficient=str(joined[8]),
            fee_account_precision=(None if joined[9] is None else str(joined[9])),
            fee_accumulator=str(joined[10]),
            fill_policy_json=str(joined[11]),
            persisted_gross_entry_debit=str(joined[12]),
            persisted_entry_fee=str(joined[13]),
            persisted_net_entry_debit=str(joined[14]),
        )
        fee_receipt = derive_settlement_fee_receipt(
            contract=record.economics_contract,
            binding=binding,
            source_payload_json=str(joined[27]),
        )
        cashflows = derive_settlement_cashflows(
            contract=record.economics_contract,
            binding=binding,
            outcome=MarketOutcome(record.outcome),
            held_side=str(joined[0]),
            quantity=_parse_decimal("candidate.executable_quantity", str(joined[1])),
            entry_price=_parse_decimal("candidate.executable_price", str(joined[2])),
            entry_fee=entry_fee,
            void_refund=_void_refund_from_json(None if joined[24] is None else str(joined[24])),
            fee_receipt=fee_receipt,
        )
        details_json = serialize_settlement_economics_evidence(
            contract=record.economics_contract,
            binding=binding,
            fee_receipt=fee_receipt,
            cashflows=cashflows,
        )
        payload = _settlement_payload(record, cashflows, details_json)
        payload_sha256 = _sha256(canonical_json(payload))
        settlement_id = _stable_id("capital-guard-settlement-v1", payload_sha256)
        existing = conn.execute(
            "SELECT settlement_id, observation_sha256, payload_sha256 "
            "FROM capital_guard_shadow_settlements WHERE candidate_id = ?",
            (record.candidate_id,),
        ).fetchone()
        if existing is not None:
            if str(existing[1]) != record.observation_sha256:
                raise ValueError("candidate settlement correction cashflow contract is required")
            if str(existing[2]) != payload_sha256:
                raise ValueError("financial settlement already exists for candidate observation")
            return SettlementWriteResult("identical", str(existing[0]))
        if existing is None:
            conn.execute(
                """
                INSERT INTO capital_guard_shadow_settlements (
                    settlement_id, candidate_id, observation_sha256, outcome,
                    settled_at, gross_payout_dollars, settlement_fee_dollars,
                    settlement_refund_dollars, net_payout_dollars, details_json,
                    payload_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    settlement_id,
                    record.candidate_id,
                    record.observation_sha256,
                    record.outcome,
                    payload["settled_at"],
                    payload["gross_payout_dollars"],
                    payload["settlement_fee_dollars"],
                    payload["settlement_refund_dollars"],
                    payload["net_payout_dollars"],
                    details_json,
                    payload_sha256,
                ),
            )
        return SettlementWriteResult("inserted", settlement_id)

    def _append_evaluation_transaction(
        self,
        conn: sqlite3.Connection,
        record: ShadowEvaluation,
    ) -> EvaluationWriteResult:
        if record.status != "settled" or record.settlement_id is None:
            raise ValueError("only settled evaluations may be persisted")
        candidate = conn.execute(
            """
            SELECT decision_at, executable_price_dollars, executable_quantity,
                   fee_schedule_json, fee_formula_type, fee_role, fee_multiplier,
                   fee_coefficient, fee_account_precision_dollars,
                   fee_accumulator_dollars, fill_policy_json,
                   gross_entry_debit_dollars, entry_fee_dollars,
                   net_entry_debit_dollars, side, venue, venue_market_id,
                   identity_json
            FROM capital_guard_shadow_candidates
            """
            "WHERE candidate_id = ?",
            (record.candidate_id,),
        ).fetchone()
        if candidate is None:
            raise ValueError("candidate_id does not exist")
        if record.evaluated_at < _parse_timestamp(str(candidate[0])):
            raise ValueError("evaluated_at must not precede decision_at")
        if record.settlement_id is not None:
            settlement = conn.execute(
                """
                SELECT s.candidate_id, s.settled_at, s.gross_payout_dollars,
                       s.settlement_fee_dollars, s.settlement_refund_dollars,
                       s.net_payout_dollars, s.observation_sha256, s.details_json,
                       o.outcome, o.venue, o.contract_fingerprint,
                       o.rules_fingerprint, o.settlement_fingerprint,
                   o.authoritative_observation_sha256, o.void_refund_json,
                   s.outcome, o.authoritative_payload_sha256, o.source_id,
                   o.source_payload_json, o.effective_at, o.venue_market_id,
                   o.alias
                FROM capital_guard_shadow_settlements AS s
                JOIN capital_guard_shadow_observations AS o
                  ON o.observation_sha256 = s.observation_sha256
                """
                "WHERE s.settlement_id = ?",
                (record.settlement_id,),
            ).fetchone()
            if settlement is None or str(settlement[0]) != record.candidate_id:
                raise ValueError("evaluation settlement does not match candidate")
            if record.evaluated_at < _parse_timestamp(str(settlement[1])):
                raise ValueError("evaluated_at must not precede settled_at")
            if _parse_timestamp(str(settlement[1])) != _parse_timestamp(str(settlement[19])):
                raise ValueError("settled_at does not match authoritative effective_at")
            if _parse_timestamp(str(candidate[0])) >= _parse_timestamp(str(settlement[19])):
                raise ValueError("candidate decision_at must precede authoritative effective_at")
            _require_unambiguous_observation_head(
                conn,
                record.candidate_id,
                str(settlement[6]),
            )
            if str(settlement[15]) != str(settlement[8]):
                raise ValueError("settlement outcome does not match observation")
        if record.status == "settled":
            assert record.entry_fee is not None
            assert record.gross_pnl is not None
            assert record.settlement_fee is not None
            assert record.settlement_refund is not None
            assert record.fee_net_pnl is not None
            assert settlement is not None
            gross_entry_debit, entry_fee, net_entry_debit = _entry_accounting_from_persisted_values(
                decision_at=str(candidate[0]),
                executable_price=str(candidate[1]),
                executable_quantity=str(candidate[2]),
                fee_schedule_json=str(candidate[3]),
                fee_formula_type=str(candidate[4]),
                fee_role=str(candidate[5]),
                fee_multiplier=str(candidate[6]),
                fee_coefficient=str(candidate[7]),
                fee_account_precision=(None if candidate[8] is None else str(candidate[8])),
                fee_accumulator=str(candidate[9]),
                fill_policy_json=str(candidate[10]),
                persisted_gross_entry_debit=str(candidate[11]),
                persisted_entry_fee=str(candidate[12]),
                persisted_net_entry_debit=str(candidate[13]),
            )
            try:
                evidence = deserialize_settlement_economics_evidence(str(settlement[7]))
                binding = SettlementEconomicsBinding(
                    venue=Venue(str(settlement[9])),
                    venue_market_id=str(settlement[20]),
                    account_party_id_sha256=evidence.binding.account_party_id_sha256,
                    contract_fingerprint=str(settlement[10]),
                    rules_fingerprint=str(settlement[11]),
                    settlement_fingerprint=str(settlement[12]),
                    authoritative_observation_sha256=str(settlement[13]),
                    authoritative_payload_sha256=str(settlement[16]),
                    source_id=str(settlement[17]),
                )
                if evidence.binding != binding:
                    raise SettlementEconomicsUnscorableError("settlement economics binding does not match observation")
                if str(candidate[15]) != binding.venue.value:
                    raise SettlementEconomicsUnscorableError("candidate venue does not match settlement observation")
                if str(candidate[16]) != binding.venue_market_id:
                    raise SettlementEconomicsUnscorableError(
                        "candidate market identity does not match settlement observation"
                    )
                candidate_identity = _require_canonical_object("candidate.identity_json", str(candidate[17]))
                expected_identity = {
                    "alias": str(settlement[21]),
                    "contract_fingerprint": binding.contract_fingerprint,
                    "rules_fingerprint": binding.rules_fingerprint,
                    "settlement_fingerprint": binding.settlement_fingerprint,
                    "venue": binding.venue.value,
                    "venue_market_id": binding.venue_market_id,
                }
                if any(candidate_identity.get(key) != value for key, value in expected_identity.items()):
                    raise SettlementEconomicsUnscorableError(
                        "candidate contract identity does not match settlement observation"
                    )
                validate_settlement_economics_contract(
                    evidence.contract,
                    venue=binding.venue,
                )
                fee_receipt = derive_settlement_fee_receipt(
                    contract=evidence.contract,
                    binding=binding,
                    source_payload_json=str(settlement[18]),
                )
                cashflows = derive_settlement_cashflows(
                    contract=evidence.contract,
                    binding=binding,
                    outcome=MarketOutcome(str(settlement[8])),
                    held_side=str(candidate[14]),
                    quantity=_parse_decimal("candidate.executable_quantity", str(candidate[2])),
                    entry_price=_parse_decimal("candidate.executable_price", str(candidate[1])),
                    entry_fee=entry_fee,
                    void_refund=_void_refund_from_json(None if settlement[14] is None else str(settlement[14])),
                    fee_receipt=fee_receipt,
                )
                persisted_cashflows = SettlementCashflows(
                    outcome=str(settlement[8]),
                    gross_payout=_parse_decimal("settlement.gross_payout", str(settlement[2])),
                    settlement_fee=_parse_decimal("settlement.settlement_fee", str(settlement[3])),
                    settlement_refund=_parse_decimal("settlement.settlement_refund", str(settlement[4])),
                    net_payout=_parse_decimal("settlement.net_payout", str(settlement[5])),
                )
            except SettlementEconomicsUnscorableError as exc:
                raise ValueError("settlement economics evidence is unscorable") from exc
            if (
                evidence.fee_receipt != fee_receipt
                or evidence.cashflows != cashflows
                or cashflows != persisted_cashflows
            ):
                raise ValueError("settlement economics cashflows do not match pinned inputs")
            if record.entry_fee != entry_fee:
                raise ValueError("evaluation entry_fee does not match pinned fee computation")
            if record.settlement_fee != cashflows.settlement_fee:
                raise ValueError("evaluation settlement_fee does not match cited settlement")
            if record.settlement_refund != cashflows.settlement_refund:
                raise ValueError("evaluation settlement_refund does not match cited settlement")
            expected_gross_pnl = cashflows.gross_payout + cashflows.settlement_refund - gross_entry_debit
            if record.gross_pnl != expected_gross_pnl:
                raise ValueError("evaluation gross_pnl does not reconcile")
            expected_fee_net_pnl = cashflows.net_payout - net_entry_debit
            if record.fee_net_pnl != expected_fee_net_pnl:
                raise ValueError("evaluation fee_net_pnl does not reconcile")
            if record.bankroll_after != record.bankroll_before + record.fee_net_pnl:
                raise ValueError("bankroll_after does not reconcile")
            if record.open_exposure_before < net_entry_debit or record.open_exposure_after != (
                record.open_exposure_before - net_entry_debit
            ):
                raise ValueError("open exposure conservation does not reconcile")
            if record.high_water_mark < max(record.bankroll_before, record.bankroll_after):
                raise ValueError("high water mark does not reconcile")
            if record.drawdown_after != max(D0, record.high_water_mark - record.bankroll_after):
                raise ValueError("drawdown conservation does not reconcile")
            if record.worst_case_loss != net_entry_debit:
                raise ValueError("worst-case risk does not match immutable entry debit")
            # Shadow candidates are counterfactual and have no immutable venue
            # order/fill receipt. Never persist their modeled cashflow as fee-net P&L.
            raise ValueError("counterfactual shadow candidate lacks attributed execution receipt")
        payload = _evaluation_payload(record)
        payload_sha256 = _sha256(canonical_json(payload))
        evaluation_id = _stable_id("capital-guard-evaluation-v1", payload_sha256)
        existing = conn.execute(
            "SELECT payload_sha256 FROM capital_guard_shadow_evaluations WHERE evaluation_id = ?",
            (evaluation_id,),
        ).fetchone()
        if existing is None:
            conn.execute(
                """
                INSERT INTO capital_guard_shadow_evaluations (
                    evaluation_id, candidate_id, settlement_id, evaluated_at,
                    evaluation_kind, status, entry_fee_dollars, gross_pnl_dollars,
                    settlement_fee_dollars, settlement_refund_dollars,
                    fee_net_pnl_dollars, bankroll_before_dollars,
                    bankroll_after_dollars, open_exposure_before_dollars,
                    open_exposure_after_dollars, high_water_mark_dollars,
                    drawdown_after_dollars,
                    worst_case_loss_dollars, details_json, payload_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    evaluation_id,
                    record.candidate_id,
                    record.settlement_id,
                    payload["evaluated_at"],
                    record.evaluation_kind,
                    record.status,
                    payload["entry_fee_dollars"],
                    payload["gross_pnl_dollars"],
                    payload["settlement_fee_dollars"],
                    payload["settlement_refund_dollars"],
                    payload["fee_net_pnl_dollars"],
                    payload["bankroll_before_dollars"],
                    payload["bankroll_after_dollars"],
                    payload["open_exposure_before_dollars"],
                    payload["open_exposure_after_dollars"],
                    payload["high_water_mark_dollars"],
                    payload["drawdown_after_dollars"],
                    payload["worst_case_loss_dollars"],
                    record.details_json,
                    payload_sha256,
                ),
            )
        return EvaluationWriteResult("identical" if existing is not None else "inserted", evaluation_id)


def _claim_identity_json(
    record: CapitalGuardCaptureAttempt | CapitalGuardCandidate,
) -> str:
    return canonical_json(
        {
            "decision_at": _timestamp(record.decision_at),
            "lifecycle_id": record.lifecycle_id,
            "schema_version": 1,
            "side": record.side,
            "venue": record.venue.value,
            "venue_market_id": record.venue_market_id,
        }
    )


def _capture_attempt_id(
    record: CapitalGuardCaptureAttempt | CapitalGuardCandidate,
) -> str:
    return _stable_id("capital-guard-capture-claim-v1", _claim_identity_json(record))


def _capture_attempt_payload(
    record: CapitalGuardCaptureAttempt,
) -> dict[str, object]:
    return {
        "capture_version": record.capture_version,
        "captured_at": _timestamp(record.captured_at),
        "claim_identity_json": _claim_identity_json(record),
        "decision_at": _timestamp(record.decision_at),
        "decision_key": record.decision_key,
        "gate_identity_json": canonical_json(
            {
                "failure_reason": record.target_failure,
                "gate": record.target_gate,
                "non_gate_blocker": record.non_gate_blocker,
                "ordered_failures": list(record.ordered_failures),
                "schema_version": 1,
            }
        ),
        "lifecycle_id": record.lifecycle_id,
        "market_family": record.market_family,
        "non_gate_blocker": record.non_gate_blocker,
        "ordered_failures_json": canonical_json(list(record.ordered_failures)),
        "ordered_unscorable_reasons_json": canonical_json(list(record.ordered_unscorable_reasons)),
        "partial_artifacts_json": record.partial_artifacts_json,
        "requested_stake_dollars": _optional_decimal_text(record.requested_stake),
        "scorable": record.scorable,
        "side": record.side,
        "target_failure": record.target_failure,
        "target_gate": record.target_gate,
        "venue": record.venue.value,
        "venue_market_id": record.venue_market_id,
    }


def _candidate_payload(record: CapitalGuardCandidate) -> dict[str, object]:
    gross_entry_debit, entry_fee, net_entry_debit = _entry_accounting(record)
    return {
        "book_observed_at": _timestamp(record.book_observed_at),
        "book_method": record.book_method,
        "book_payload_sha256": record.book_payload_sha256,
        "book_source": record.book_source,
        "captured_at": _timestamp(record.captured_at),
        "capture_attempt_id": _capture_attempt_id(record),
        "candidate_version": record.candidate_version,
        "decision_at": _timestamp(record.decision_at),
        "decision_key": record.decision_key,
        "executable_book_json": record.executable_book_json,
        "executable_price_dollars": _decimal_text(record.executable_price),
        "executable_quantity": _decimal_text(record.executable_quantity),
        "expected_probability": _decimal_text(record.expected_probability),
        "fee_account_precision_dollars": _optional_decimal_text(record.fee_account_precision),
        "fee_accumulator_dollars": _decimal_text(record.fee_accumulator),
        "fee_coefficient": _decimal_text(record.fee_coefficient),
        "fee_formula_type": record.fee_formula_type,
        "fee_multiplier": _decimal_text(record.fee_multiplier),
        "fee_role": record.fee_role.value,
        "fee_schedule_json": record.fee_schedule_json,
        "fee_provenance_json": record.fee_provenance_json,
        "fee_provenance_sha256": record.fee_provenance_sha256,
        "fill_policy_json": record.fill_policy_json,
        "gate_inputs_json": record.gate_inputs_json,
        "gate_results_json": record.gate_results_json,
        "gross_edge": _decimal_text(record.gross_edge),
        "gross_entry_debit_dollars": _decimal_text(gross_entry_debit),
        "identity_json": record.identity_json,
        "entry_fee_dollars": _decimal_text(entry_fee),
        "lifecycle_id": record.lifecycle_id,
        "market_family": record.market_family,
        "net_entry_debit_dollars": _decimal_text(net_entry_debit),
        "non_gate_blocker": record.non_gate_blocker,
        "ordered_failures_json": canonical_json(list(record.ordered_failures)),
        "replay_eligible": record.replay_eligible,
        "side": record.side,
        "sizing_json": record.sizing_json,
        "venue": record.venue.value,
        "venue_market_id": record.venue_market_id,
    }


def _candidate_id(record: CapitalGuardCandidate) -> str:
    return _stable_id("capital-guard-candidate-claim-v1", _claim_identity_json(record))


def _settlement_attempt_payload(
    *,
    venue: Venue,
    venue_market_id: str,
    alias: str | None,
    contract_fingerprint: str | None,
    rules_fingerprint: str | None,
    settlement_fingerprint: str | None,
    identity_set_sha256: str | None,
    candidate_set_sha256: str | None,
    candidate_set_complete: bool,
    candidate_count: int,
    attempted_at: datetime,
    status: str,
    identity_sample_sha256: str | None = None,
    outcome: str | None = None,
    source_id: str | None = None,
    rules_version: str | None = None,
    authoritative_outcome_json: str | None = None,
    authoritative_payload_sha256: str | None = None,
    authoritative_observation_sha256: str | None = None,
    semantic_sha256: str | None = None,
    void_refund_json: str | None = None,
    void_refund_sha256: str | None = None,
    head_before_sha256: str | None = None,
    head_after_sha256: str | None = None,
    error_taxonomy: str | None = None,
    error_sha256: str | None = None,
) -> dict[str, object]:
    return {
        "attempt_version": CAPITAL_GUARD_SETTLEMENT_ATTEMPT_VERSION,
        "venue": venue.value,
        "venue_market_id": venue_market_id,
        "alias": alias,
        "contract_fingerprint": contract_fingerprint,
        "rules_fingerprint": rules_fingerprint,
        "settlement_fingerprint": settlement_fingerprint,
        "identity_set_sha256": identity_set_sha256,
        "identity_sample_sha256": identity_sample_sha256,
        "candidate_set_sha256": candidate_set_sha256,
        "candidate_set_complete": int(candidate_set_complete),
        "candidate_count": candidate_count,
        "attempted_at": _timestamp(attempted_at),
        "status": status,
        "outcome": outcome,
        "source_id": source_id,
        "rules_version": rules_version,
        "authoritative_outcome_json": authoritative_outcome_json,
        "authoritative_payload_sha256": authoritative_payload_sha256,
        "authoritative_observation_sha256": authoritative_observation_sha256,
        "semantic_sha256": semantic_sha256,
        "void_refund_json": void_refund_json,
        "void_refund_sha256": void_refund_sha256,
        "head_before_sha256": head_before_sha256,
        "head_after_sha256": head_after_sha256,
        "error_taxonomy": error_taxonomy,
        "error_sha256": error_sha256,
    }


def _settlement_attempt_id(payload: Mapping[str, object]) -> str:
    key = {
        name: payload[name]
        for name in (
            "attempt_version",
            "venue",
            "venue_market_id",
            "identity_set_sha256",
            "identity_sample_sha256",
            "candidate_set_sha256",
            "candidate_set_complete",
            "candidate_count",
            "attempted_at",
            "head_before_sha256",
        )
    }
    return _sha256(canonical_json(key))


def _source_settlement_semantic_sha256(
    observation: SettlementObservation,
    *,
    contract_fingerprint: str,
    rules_fingerprint: str,
    settlement_fingerprint: str,
) -> str:
    void_refund_json, void_refund_sha256 = _void_refund_payload(observation.void_refund)
    return _sha256(
        canonical_json(
            {
                "venue": observation.market_ref.venue.value,
                "venue_market_id": observation.market_ref.venue_market_id,
                "alias": observation.market_ref.alias,
                "contract_fingerprint": contract_fingerprint,
                "rules_fingerprint": rules_fingerprint,
                "settlement_fingerprint": settlement_fingerprint,
                "outcome": observation.outcome.value,
                "authoritative_outcome_json": observation.authoritative_outcome_json,
                "authoritative_payload_sha256": observation.payload_sha256,
                "rules_version": observation.rules_version,
                "source_id": observation.source_id,
                "void_refund_json": void_refund_json,
                "void_refund_sha256": void_refund_sha256,
            }
        )
    )


def _settlement_semantic_sha256(record: SettlementObservationRecord) -> str:
    return _sha256(
        canonical_json(
            {
                "venue": record.venue.value,
                "venue_market_id": record.venue_market_id,
                "alias": record.alias,
                "contract_fingerprint": record.contract_fingerprint,
                "rules_fingerprint": record.rules_fingerprint,
                "settlement_fingerprint": record.settlement_fingerprint,
                "outcome": record.outcome,
                "authoritative_outcome_json": record.authoritative_outcome_json,
                "authoritative_payload_sha256": record.authoritative_payload_sha256,
                "rules_version": record.rules_version,
                "source_id": record.source_id,
                "void_refund_json": record.void_refund_json,
                "void_refund_sha256": record.void_refund_sha256,
            }
        )
    )


def _void_refund_payload(
    refund: VoidRefundContract | None,
) -> tuple[str | None, str | None]:
    if refund is None:
        return None, None
    payload = canonical_json(
        {
            "refund_cents_per_contract": _decimal_text(refund.refund_cents_per_contract),
            "refunds_entry_fee": refund.refunds_entry_fee,
            "schema_version": 1,
        }
    )
    return payload, _sha256(payload)


def _void_refund_from_json(value: str | None) -> VoidRefundContract | None:
    if value is None:
        return None
    payload = _require_canonical_object("void_refund_json", value)
    _require_exact_keys(
        "void_refund_json",
        payload,
        {"refund_cents_per_contract", "refunds_entry_fee", "schema_version"},
    )
    if payload["schema_version"] != 1 or not isinstance(payload["refunds_entry_fee"], bool):
        raise ValueError("void_refund_json has an unsupported contract")
    return VoidRefundContract(
        refund_cents_per_contract=_require_decimal_text_value(
            "void_refund_json.refund_cents_per_contract",
            payload["refund_cents_per_contract"],
        ),
        refunds_entry_fee=payload["refunds_entry_fee"],
    )


def _authoritative_observation_sha256(record: SettlementObservationRecord) -> str:
    try:
        outcome = MarketOutcome(record.outcome)
    except ValueError as exc:
        raise ValueError("unresolved observations cannot be authoritative") from exc
    rebuilt = build_settlement_observation(
        market_ref=MarketRef(record.venue, record.venue_market_id, record.alias),
        outcome=outcome,
        authoritative_outcome=json.loads(record.authoritative_outcome_json),
        authoritative_payload=json.loads(record.source_payload_json),
        observed_at=record.observed_at,
        effective_at=record.effective_at,
        rules_version=record.rules_version,
        source_id=record.source_id,
        void_refund=_void_refund_from_json(record.void_refund_json),
    )
    return rebuilt.observation_sha256


def _observation_payload(record: SettlementObservationRecord) -> dict[str, object]:
    return {
        "alias": record.alias,
        "authoritative_observation_sha256": record.authoritative_observation_sha256,
        "authoritative_outcome_json": record.authoritative_outcome_json,
        "authoritative_payload_sha256": record.authoritative_payload_sha256,
        "contract_fingerprint": record.contract_fingerprint,
        "effective_at": _timestamp(record.effective_at),
        "observed_at": _timestamp(record.observed_at),
        "outcome": record.outcome,
        "rules_fingerprint": record.rules_fingerprint,
        "rules_version": record.rules_version,
        "semantic_sha256": record.semantic_sha256,
        "settlement_fingerprint": record.settlement_fingerprint,
        "source_id": record.source_id,
        "source_payload_json": record.source_payload_json,
        "supersedes_observation_sha256": record.supersedes_observation_sha256,
        "venue": record.venue.value,
        "venue_market_id": record.venue_market_id,
        "void_refund_json": record.void_refund_json,
        "void_refund_sha256": record.void_refund_sha256,
    }


def _observation_market_key(venue: Venue, venue_market_id: str) -> str:
    return canonical_json(
        {
            "venue": venue.value,
            "venue_market_id": venue_market_id,
        }
    )


def _observation_root_is_ambiguous(
    conn: sqlite3.Connection,
    market_key: str,
) -> bool:
    return (
        conn.execute(
            """
            SELECT 1
            FROM capital_guard_shadow_conflicts
            WHERE entity_type = 'observation_root' AND entity_key = ?
            LIMIT 1
            """,
            (market_key,),
        ).fetchone()
        is not None
    )


def _market_has_observation_conflict(
    conn: sqlite3.Connection,
    market_key: SettlementMarketKey,
) -> bool:
    key = _observation_market_key(market_key.venue, market_key.venue_market_id)
    if _observation_root_is_ambiguous(conn, key):
        return True
    return (
        conn.execute(
            """
            SELECT 1
            FROM capital_guard_shadow_conflicts conflict
            JOIN capital_guard_shadow_observations observation
              ON observation.observation_sha256 = conflict.entity_key
            WHERE conflict.entity_type = 'observation_correction'
              AND observation.venue = ? AND observation.venue_market_id = ?
            LIMIT 1
            """,
            (market_key.venue.value, market_key.venue_market_id),
        ).fetchone()
        is not None
    )


def _require_unambiguous_observation_head(
    conn: sqlite3.Connection,
    candidate_id: str,
    observation_sha256: str,
) -> None:
    identity = conn.execute(
        """
        SELECT c.venue, c.venue_market_id
        FROM capital_guard_shadow_candidates c
        JOIN capital_guard_shadow_candidate_observations link
          ON link.candidate_id = c.candidate_id
        WHERE c.candidate_id = ? AND link.observation_sha256 = ?
        """,
        (candidate_id, observation_sha256),
    ).fetchone()
    if identity is None:
        raise ValueError("settlement requires a linked candidate observation")
    market_key = canonical_json(
        {
            "venue": str(identity[0]),
            "venue_market_id": str(identity[1]),
        }
    )
    if _observation_root_is_ambiguous(conn, market_key):
        raise ValueError("settlement has an ambiguous observation root")
    successor = conn.execute(
        "SELECT 1 FROM capital_guard_shadow_observations WHERE supersedes_observation_sha256 = ? LIMIT 1",
        (observation_sha256,),
    ).fetchone()
    if successor is not None:
        raise ValueError("settlement requires the current observation head")
    correction_conflict = conn.execute(
        """
        WITH RECURSIVE chain(observation_sha256, supersedes_observation_sha256) AS (
            SELECT observation_sha256, supersedes_observation_sha256
            FROM capital_guard_shadow_observations
            WHERE observation_sha256 = ?
            UNION ALL
            SELECT parent.observation_sha256, parent.supersedes_observation_sha256
            FROM capital_guard_shadow_observations parent
            JOIN chain child
              ON parent.observation_sha256 = child.supersedes_observation_sha256
        )
        SELECT 1
        FROM capital_guard_shadow_conflicts
        WHERE entity_type = 'observation_correction'
          AND entity_key IN (SELECT observation_sha256 FROM chain)
        LIMIT 1
        """,
        (observation_sha256,),
    ).fetchone()
    if correction_conflict is not None:
        raise ValueError("settlement has an ambiguous correction chain")


def _settlement_payload(
    record: ShadowSettlement,
    cashflows: SettlementCashflows,
    details_json: str,
) -> dict[str, object]:
    return {
        "candidate_id": record.candidate_id,
        "details_json": details_json,
        "gross_payout_dollars": _decimal_text(cashflows.gross_payout),
        "net_payout_dollars": _decimal_text(cashflows.net_payout),
        "observation_sha256": record.observation_sha256,
        "outcome": record.outcome,
        "settled_at": _timestamp(record.settled_at),
        "settlement_fee_dollars": _decimal_text(cashflows.settlement_fee),
        "settlement_refund_dollars": _decimal_text(cashflows.settlement_refund),
    }


def _evaluation_payload(record: ShadowEvaluation) -> dict[str, object]:
    return {
        "bankroll_after_dollars": _decimal_text(record.bankroll_after),
        "bankroll_before_dollars": _decimal_text(record.bankroll_before),
        "candidate_id": record.candidate_id,
        "details_json": record.details_json,
        "drawdown_after_dollars": _decimal_text(record.drawdown_after),
        "entry_fee_dollars": _optional_decimal_text(record.entry_fee),
        "evaluated_at": _timestamp(record.evaluated_at),
        "evaluation_kind": record.evaluation_kind,
        "fee_net_pnl_dollars": _optional_decimal_text(record.fee_net_pnl),
        "gross_pnl_dollars": _optional_decimal_text(record.gross_pnl),
        "high_water_mark_dollars": _decimal_text(record.high_water_mark),
        "open_exposure_after_dollars": _decimal_text(record.open_exposure_after),
        "open_exposure_before_dollars": _decimal_text(record.open_exposure_before),
        "settlement_fee_dollars": _optional_decimal_text(record.settlement_fee),
        "settlement_id": record.settlement_id,
        "settlement_refund_dollars": _optional_decimal_text(record.settlement_refund),
        "status": record.status,
        "worst_case_loss_dollars": _decimal_text(record.worst_case_loss),
    }


def _insert_conflict(
    conn: sqlite3.Connection,
    *,
    entity_type: str,
    entity_key: str,
    existing_sha256: str,
    incoming_sha256: str,
    created_at: datetime,
) -> str:
    conflict_id = _stable_id(
        "capital-guard-conflict-v1",
        entity_type,
        entity_key,
        existing_sha256,
        incoming_sha256,
    )
    details_json = canonical_json({"reason": "immutable_key_collision"})
    conn.execute(
        "INSERT OR IGNORE INTO capital_guard_shadow_conflicts "
        "(conflict_id, entity_type, entity_key, existing_sha256, incoming_sha256, "
        "details_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            conflict_id,
            entity_type,
            entity_key,
            existing_sha256,
            incoming_sha256,
            details_json,
            _timestamp(created_at),
        ),
    )
    return conflict_id


def _require_exact_keys(
    name: str,
    value: Mapping[str, object],
    expected: set[str],
) -> None:
    if set(value) != expected:
        raise ValueError(f"{name} is not a complete versioned schema")


def _require_schema_version(name: str, value: Mapping[str, object]) -> None:
    if value.get("schema_version") != CAPITAL_GUARD_CANDIDATE_VERSION:
        raise ValueError(f"{name} has unsupported schema_version")


def _require_decimal_text_value(name: str, value: object) -> Decimal:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be canonical Decimal text")
    return _parse_decimal(name, value)


def _optional_decimal_text_value(name: str, value: object) -> Decimal | None:
    if value is None:
        return None
    return _require_decimal_text_value(name, value)


def _validate_gate_inputs_json(value: str, side: str) -> dict[str, object]:
    root = _require_canonical_object("gate_inputs_json", value)
    _require_exact_keys("gate_inputs_json", root, {"schema_version", "gates"})
    _require_schema_version("gate_inputs_json", root)
    gates = root.get("gates")
    if not isinstance(gates, dict) or set(gates) != set(_GATE_NAMES):
        raise ValueError("gate_inputs_json is not a complete G1-G7 schema")
    expected_keys = {
        "G1": {"blended_confidence", "regime_confidence", "scaled_confidence", "threshold"},
        "G2": {"evidence_source_classes", "minimum_source_classes", "source_lane"},
        "G3": {
            "default_min_edge",
            "disagreement_score",
            "override_band_start",
            "override_multiplier",
            "threshold",
        },
        "G4": {"regime_confidence", "threshold"},
        "G5": {"drift_suspect", "in_recovery", "source_lane"},
        "G6": {
            "recency_score",
            "recency_threshold",
            "settlement_source_relevant",
            "source_lane",
            "time_to_close_seconds",
        },
        "G7": {
            "intended_side",
            "market_liquidity_dollars",
            "market_price_momentum_cents",
            "max_open_exposure_drawdown_pct",
            "minimum_market_liquidity_dollars",
            "open_exposure_drawdown_pct",
        },
    }
    decimal_fields = {
        "G1": expected_keys["G1"],
        "G3": expected_keys["G3"],
        "G4": expected_keys["G4"],
        "G6": {"recency_score", "recency_threshold", "time_to_close_seconds"},
        "G7": {
            "max_open_exposure_drawdown_pct",
            "minimum_market_liquidity_dollars",
            "open_exposure_drawdown_pct",
        },
    }
    for gate in _GATE_NAMES:
        gate_input = gates[gate]
        if not isinstance(gate_input, dict):
            raise ValueError(f"gate_inputs_json.{gate} is not a typed object")
        _require_exact_keys(f"gate_inputs_json.{gate}", gate_input, expected_keys[gate])
        for field in decimal_fields.get(gate, set()):
            _require_decimal_text_value(f"gate_inputs_json.{gate}.{field}", gate_input[field])
    g2 = gates["G2"]
    assert isinstance(g2, dict)
    source_classes = g2["evidence_source_classes"]
    if (
        not isinstance(source_classes, list)
        or any(not isinstance(item, str) or not item.strip() for item in source_classes)
        or not isinstance(g2["minimum_source_classes"], int)
        or isinstance(g2["minimum_source_classes"], bool)
        or g2["minimum_source_classes"] < 1
    ):
        raise ValueError("gate_inputs_json.G2 is not complete typed evidence")
    _require_text("gate_inputs_json.G2.source_lane", g2["source_lane"])
    g5 = gates["G5"]
    assert isinstance(g5, dict)
    if not isinstance(g5["drift_suspect"], bool) or not isinstance(g5["in_recovery"], bool):
        raise ValueError("gate_inputs_json.G5 is not complete typed evidence")
    _require_text("gate_inputs_json.G5.source_lane", g5["source_lane"])
    g6 = gates["G6"]
    assert isinstance(g6, dict)
    if not isinstance(g6["settlement_source_relevant"], bool):
        raise ValueError("gate_inputs_json.G6 is not complete typed evidence")
    _require_text("gate_inputs_json.G6.source_lane", g6["source_lane"])
    g7 = gates["G7"]
    assert isinstance(g7, dict)
    if g7["intended_side"] != side:
        raise ValueError("gate_inputs_json.G7 intended_side does not match candidate")
    for field in ("market_liquidity_dollars", "market_price_momentum_cents"):
        _optional_decimal_text_value(f"gate_inputs_json.G7.{field}", g7[field])
    return gates


def _validate_gate_results_json(value: str) -> dict[str, object]:
    root = _require_canonical_object("gate_results_json", value)
    _require_exact_keys("gate_results_json", root, {"schema_version", "gates"})
    _require_schema_version("gate_results_json", root)
    gates = root.get("gates")
    if not isinstance(gates, dict) or set(gates) != set(_GATE_NAMES):
        raise ValueError("gate_results_json is not a complete G1-G7 schema")
    for gate in _GATE_NAMES:
        result = gates[gate]
        if not isinstance(result, dict):
            raise ValueError(f"gate_results_json.{gate} is not a typed object")
        _require_exact_keys(
            f"gate_results_json.{gate}",
            result,
            {"applied", "failure_reasons", "passed"},
        )
        if not isinstance(result["applied"], bool) or not isinstance(result["passed"], bool):
            raise ValueError(f"gate_results_json.{gate} is not complete typed evidence")
        failure_reasons = result["failure_reasons"]
        if not isinstance(failure_reasons, list) or len(set(failure_reasons)) != len(failure_reasons):
            raise ValueError("gate result failure_reasons are not a typed unique list")
        for failure_reason in failure_reasons:
            _require_text(f"gate_results_json.{gate}.failure_reasons item", failure_reason)
            if not (failure_reason == gate or failure_reason.startswith(f"{gate}_")):
                raise ValueError("gate result failure_reason identifies the wrong gate")
        if result["passed"] is bool(failure_reasons):
            raise ValueError("gate result passed and failure_reasons disagree")
        if not result["applied"] and (not result["passed"] or failure_reasons):
            raise ValueError("unapplied gate cannot fail")
    return gates


def _validate_gate_decision_consistency(
    inputs: Mapping[str, object],
    results: Mapping[str, object],
) -> None:
    typed_inputs = {gate: inputs[gate] for gate in _GATE_NAMES}
    typed_results = {gate: results[gate] for gate in _GATE_NAMES}
    if any(
        not isinstance(typed_inputs[gate], dict) or not isinstance(typed_results[gate], dict) for gate in _GATE_NAMES
    ):
        raise ValueError("gate evidence is not typed")
    gate_inputs = {gate: typed_inputs[gate] for gate in _GATE_NAMES}
    gate_results = {gate: typed_results[gate] for gate in _GATE_NAMES}

    g1 = gate_inputs["G1"]
    scaled = _require_decimal_text_value("G1.scaled_confidence", g1["scaled_confidence"])
    blended = _require_decimal_text_value("G1.blended_confidence", g1["blended_confidence"])
    regime = _require_decimal_text_value("G1.regime_confidence", g1["regime_confidence"])
    if scaled != blended * regime:
        raise ValueError("G1 scaled confidence does not reconcile")
    expected_failures: dict[str, list[str]] = {
        "G1": (
            [] if scaled >= _require_decimal_text_value("G1.threshold", g1["threshold"]) else ["G1_blended_confidence"]
        ),
        "G2": [],
        "G3": [],
        "G4": [],
        "G5": [],
        "G6": [],
        "G7": [],
    }
    g2 = gate_inputs["G2"]
    source_lane = str(g2["source_lane"])
    accumulation = source_lane != "fast"
    if accumulation and len(set(g2["evidence_source_classes"])) < int(g2["minimum_source_classes"]):
        expected_failures["G2"].append("G2_evidence_source_class_diversity")
    g3 = gate_inputs["G3"]
    if _require_decimal_text_value("G3.disagreement_score", g3["disagreement_score"]) > _require_decimal_text_value(
        "G3.threshold", g3["threshold"]
    ):
        expected_failures["G3"].append("G3_disagreement_score")
    g4 = gate_inputs["G4"]
    if _require_decimal_text_value("G4.regime_confidence", g4["regime_confidence"]) < _require_decimal_text_value(
        "G4.threshold", g4["threshold"]
    ):
        expected_failures["G4"].append("G4_regime_confidence")
    g5 = gate_inputs["G5"]
    if accumulation and g5["drift_suspect"] and not g5["in_recovery"]:
        expected_failures["G5"].append("G5_dossier_drift_suspect")
    g6 = gate_inputs["G6"]
    if accumulation and _require_decimal_text_value(
        "G6.recency_score", g6["recency_score"]
    ) < _require_decimal_text_value("G6.recency_threshold", g6["recency_threshold"]):
        expected_failures["G6"].append("G6_recency_score")
    g7 = gate_inputs["G7"]
    if _require_decimal_text_value(
        "G7.open_exposure_drawdown_pct", g7["open_exposure_drawdown_pct"]
    ) > _require_decimal_text_value("G7.max_open_exposure_drawdown_pct", g7["max_open_exposure_drawdown_pct"]):
        expected_failures["G7"].append("G7_open_exposure_drawdown")
    liquidity = _optional_decimal_text_value(
        "G7.market_liquidity_dollars",
        g7["market_liquidity_dollars"],
    )
    if liquidity is not None and liquidity < _require_decimal_text_value(
        "G7.minimum_market_liquidity_dollars",
        g7["minimum_market_liquidity_dollars"],
    ):
        expected_failures["G7"].append("G7_zero_liquidity")
    momentum = _optional_decimal_text_value("G7.market_price_momentum_cents", g7["market_price_momentum_cents"])
    if momentum is not None and (
        (g7["intended_side"] == "yes" and momentum < 0) or (g7["intended_side"] == "no" and momentum > 0)
    ):
        expected_failures["G7"].append("G7_adverse_price_momentum")

    for gate in _GATE_NAMES:
        expected_applied = accumulation or gate not in {"G2", "G5", "G6"}
        if gate_results[gate]["applied"] is not expected_applied:
            raise ValueError(f"{gate} applied status does not match source lane")
        if gate_results[gate]["failure_reasons"] != expected_failures[gate]:
            raise ValueError(f"{gate} result does not reconcile with typed inputs")


def _validate_identity_json(record: CapitalGuardCandidate) -> None:
    identity = _require_canonical_object("identity_json", record.identity_json)
    _require_exact_keys(
        "identity_json",
        identity,
        {
            "alias",
            "contract_fingerprint",
            "decision_key",
            "lifecycle_id",
            "rules_fingerprint",
            "schema_version",
            "settlement_fingerprint",
            "venue",
            "venue_market_id",
        },
    )
    _require_schema_version("identity_json", identity)
    expected = {
        "decision_key": record.decision_key,
        "lifecycle_id": record.lifecycle_id,
        "venue": record.venue.value,
        "venue_market_id": record.venue_market_id,
    }
    if any(identity[key] != expected_value for key, expected_value in expected.items()):
        raise ValueError("identity_json does not match canonical candidate identity")
    for key in (
        "alias",
        "contract_fingerprint",
        "rules_fingerprint",
        "settlement_fingerprint",
    ):
        _require_text(f"identity_json.{key}", identity[key])


def _validate_book_json(record: CapitalGuardCandidate) -> None:
    book = _require_canonical_object("executable_book_json", record.executable_book_json)
    _require_exact_keys("executable_book_json", book, {"asks", "bids", "schema_version", "side"})
    _require_schema_version("executable_book_json", book)
    if book["side"] != record.side:
        raise ValueError("executable_book_json side does not match candidate")
    depths: dict[str, list[tuple[Decimal, Decimal]]] = {}
    for side_name in ("bids", "asks"):
        raw_levels = book[side_name]
        if not isinstance(raw_levels, list) or not raw_levels:
            raise ValueError("executable_book_json requires nonempty bid and ask depth")
        levels: list[tuple[Decimal, Decimal]] = []
        for index, level in enumerate(raw_levels):
            if not isinstance(level, dict):
                raise ValueError("executable_book_json depth level is not typed")
            _require_exact_keys(
                f"executable_book_json.{side_name}[{index}]",
                level,
                {"price_dollars", "quantity"},
            )
            price = _require_decimal_text_value(
                f"executable_book_json.{side_name}[{index}].price_dollars",
                level["price_dollars"],
            )
            quantity = _require_decimal_text_value(
                f"executable_book_json.{side_name}[{index}].quantity",
                level["quantity"],
            )
            if not D0 < price < D1 or quantity <= 0:
                raise ValueError("executable_book_json depth is outside contractual bounds")
            levels.append((price, quantity))
        depths[side_name] = levels
    bids = depths["bids"]
    asks = depths["asks"]
    if bids != sorted(bids, key=lambda item: item[0], reverse=True):
        raise ValueError("executable_book_json bids are not deterministically ordered")
    if asks != sorted(asks, key=lambda item: item[0]):
        raise ValueError("executable_book_json asks are not deterministically ordered")
    if bids[0][0] >= asks[0][0]:
        raise ValueError("executable_book_json is crossed")
    if asks[0][0] != record.executable_price:
        raise ValueError("executable price does not match best executable ask")
    executable_depth = sum(
        (quantity for price, quantity in asks if price <= record.executable_price),
        D0,
    )
    if executable_depth < record.executable_quantity:
        raise ValueError("executable quantity exceeds recorded ask depth")


def _validate_sizing_json(record: CapitalGuardCandidate) -> None:
    sizing = _require_canonical_object("sizing_json", record.sizing_json)
    _require_exact_keys(
        "sizing_json",
        sizing,
        {
            "bankroll_dollars",
            "capital_at_risk_dollars",
            "capped_dollars",
            "kelly_dollars",
            "kelly_fraction",
            "max_position_dollars",
            "max_ticker_exposure_dollars",
            "quantity_method",
            "quantity_step",
            "requested_quantity",
            "schema_version",
        },
    )
    _require_schema_version("sizing_json", sizing)
    values = {
        name: _require_decimal_text_value(f"sizing_json.{name}", sizing[name])
        for name in (
            "bankroll_dollars",
            "capital_at_risk_dollars",
            "capped_dollars",
            "kelly_dollars",
            "kelly_fraction",
            "max_position_dollars",
            "max_ticker_exposure_dollars",
            "quantity_step",
            "requested_quantity",
        )
    }
    if values["bankroll_dollars"] <= 0 or any(values[name] < 0 for name in values if name != "bankroll_dollars"):
        raise ValueError("sizing_json values are outside contractual bounds")
    gross_debit = record.executable_price * record.executable_quantity
    if values["requested_quantity"] != record.executable_quantity:
        raise ValueError("sizing_json requested_quantity does not match candidate")
    if values["capital_at_risk_dollars"] != gross_debit:
        raise ValueError("sizing_json capital_at_risk does not match entry cost")
    if not D0 <= values["kelly_fraction"] <= D1:
        raise ValueError("sizing_json kelly_fraction must be in [0, 1]")
    if values["kelly_dollars"] > values["bankroll_dollars"]:
        raise ValueError("sizing_json kelly_dollars exceeds bankroll")
    if values["capped_dollars"] > values["kelly_dollars"]:
        raise ValueError("sizing_json capped_dollars exceeds Kelly stake")
    if values["capped_dollars"] < gross_debit:
        raise ValueError("sizing_json capped_dollars cannot fund the candidate")
    if min(values["max_position_dollars"], values["max_ticker_exposure_dollars"]) < gross_debit:
        raise ValueError("sizing_json caps cannot fund the candidate")
    if sizing["quantity_method"] != "floor_to_step":
        raise ValueError("sizing_json quantity_method is unsupported")
    if values["quantity_step"] <= 0:
        raise ValueError("sizing_json quantity_step must be positive")
    if values["requested_quantity"] % values["quantity_step"] != 0:
        raise ValueError("sizing_json requested_quantity is not aligned to quantity_step")


def _validate_fee_provenance_json(record: CapitalGuardCandidate) -> None:
    provenance = _require_canonical_object("fee_provenance_json", record.fee_provenance_json)
    _require_exact_keys(
        "fee_provenance_json",
        provenance,
        {
            "account_precision_dollars",
            "accumulator_dollars",
            "coefficient",
            "effective_at",
            "fee_multiplier",
            "fee_role",
            "fee_schedule",
            "fee_type",
            "schema_version",
            "source_payload_sha256",
            "venue",
        },
    )
    _require_schema_version("fee_provenance_json", provenance)
    if _sha256(record.fee_provenance_json) != record.fee_provenance_sha256:
        raise ValueError("fee_provenance_sha256 does not bind fee_provenance_json")
    if provenance["venue"] != record.venue.value:
        raise ValueError("fee provenance venue does not match candidate")
    if provenance["fee_schedule"] != json.loads(record.fee_schedule_json):
        raise ValueError("fee provenance schedule does not match pinned schedule")
    if provenance["fee_type"] != record.fee_formula_type:
        raise ValueError("fee provenance type does not match candidate")
    if provenance["fee_role"] != record.fee_role.value:
        raise ValueError("fee provenance role does not match candidate")
    if (
        _require_decimal_text_value("fee_provenance_json.fee_multiplier", provenance["fee_multiplier"])
        != record.fee_multiplier
    ):
        raise ValueError("fee provenance multiplier does not match candidate")
    if (
        _require_decimal_text_value("fee_provenance_json.coefficient", provenance["coefficient"])
        != record.fee_coefficient
    ):
        raise ValueError("fee provenance coefficient does not match candidate")
    if (
        _require_decimal_text_value(
            "fee_provenance_json.accumulator_dollars",
            provenance["accumulator_dollars"],
        )
        != record.fee_accumulator
    ):
        raise ValueError("fee provenance accumulator does not match candidate")
    account_precision = provenance["account_precision_dollars"]
    if account_precision is None:
        parsed_precision = None
    else:
        parsed_precision = _require_decimal_text_value(
            "fee_provenance_json.account_precision_dollars", account_precision
        )
    if parsed_precision != record.fee_account_precision:
        raise ValueError("fee provenance account precision does not match candidate")
    effective_at_text = provenance["effective_at"]
    _require_text("fee_provenance_json.effective_at", effective_at_text)
    try:
        effective_at = datetime.fromisoformat(effective_at_text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("fee provenance effective_at is not an ISO timestamp") from exc
    if effective_at.tzinfo is None or effective_at.utcoffset() is None:
        raise ValueError("fee provenance effective_at must be timezone-aware")
    schedule = deserialize_fee_schedule(record.fee_schedule_json)
    if effective_at != schedule.effective_from or effective_at > record.decision_at:
        raise ValueError("fee provenance effective_at does not identify pinned schedule")
    _require_sha256(
        "fee_provenance_json.source_payload_sha256",
        provenance["source_payload_sha256"],
    )


def _validate_fill_policy_json(record: CapitalGuardCandidate) -> None:
    fill = _require_canonical_object("fill_policy_json", record.fill_policy_json)
    _require_exact_keys(
        "fill_policy_json",
        fill,
        {
            "allow_partial",
            "book_payload_sha256",
            "entry_request_id",
            "order_id",
            "order_type",
            "policy_id",
            "price_limit_dollars",
            "quantity",
            "schema_version",
            "source_code_sha256",
            "time_in_force",
        },
    )
    _require_schema_version("fill_policy_json", fill)
    if not isinstance(fill["allow_partial"], bool):
        raise ValueError("fill_policy_json allow_partial is not typed")
    for key in (
        "entry_request_id",
        "order_id",
        "order_type",
        "policy_id",
        "time_in_force",
    ):
        _require_text(f"fill_policy_json.{key}", fill[key])
    for key in ("book_payload_sha256", "source_code_sha256"):
        _require_sha256(f"fill_policy_json.{key}", fill[key])
    if fill["book_payload_sha256"] != record.book_payload_sha256:
        raise ValueError("fill_policy_json book provenance does not match candidate")
    if (
        _require_decimal_text_value("fill_policy_json.price_limit_dollars", fill["price_limit_dollars"])
        != record.executable_price
    ):
        raise ValueError("fill_policy_json price limit does not match candidate")
    if _require_decimal_text_value("fill_policy_json.quantity", fill["quantity"]) != record.executable_quantity:
        raise ValueError("fill_policy_json quantity does not match candidate")


def _entry_accounting(
    record: CapitalGuardCandidate,
) -> tuple[Decimal, Decimal, Decimal]:
    schedule = deserialize_fee_schedule(record.fee_schedule_json)
    fill = _require_canonical_object("fill_policy_json", record.fill_policy_json)
    gross_entry_debit = record.executable_quantity * record.executable_price
    quote = quote_fee(
        FeeContext(
            schedule_id=schedule,
            role=record.fee_role,
            quantity=record.executable_quantity,
            price=record.executable_price,
            signed_revenue=-gross_entry_debit,
            order_id=str(fill["order_id"]),
            accumulator=record.fee_accumulator,
            multiplier=record.fee_multiplier,
            coefficient=record.fee_coefficient,
            account_precision=record.fee_account_precision,
            timestamp=record.decision_at,
        )
    )
    net_entry_debit = gross_entry_debit + quote.net_fee
    if net_entry_debit < 0:
        raise ValueError("net entry debit must be nonnegative")
    return gross_entry_debit, quote.net_fee, net_entry_debit


def _entry_accounting_from_persisted_values(
    *,
    decision_at: str,
    executable_price: str,
    executable_quantity: str,
    fee_schedule_json: str,
    fee_formula_type: str,
    fee_role: str,
    fee_multiplier: str,
    fee_coefficient: str,
    fee_account_precision: str | None,
    fee_accumulator: str,
    fill_policy_json: str,
    persisted_gross_entry_debit: str,
    persisted_entry_fee: str,
    persisted_net_entry_debit: str,
) -> tuple[Decimal, Decimal, Decimal]:
    """Re-quote the candidate's immutable entry before any financial settlement."""

    schedule = deserialize_fee_schedule(fee_schedule_json)
    if fee_type_for_schedule(schedule) != fee_formula_type:
        raise ValueError("candidate pinned fee formula does not match")
    price = _parse_decimal("candidate.executable_price", executable_price)
    quantity = _parse_decimal("candidate.executable_quantity", executable_quantity)
    multiplier = _parse_decimal("candidate.fee_multiplier", fee_multiplier)
    coefficient = _parse_decimal("candidate.fee_coefficient", fee_coefficient)
    precision = (
        None
        if fee_account_precision is None
        else _parse_decimal("candidate.fee_account_precision", fee_account_precision)
    )
    accumulator = _parse_decimal("candidate.fee_accumulator", fee_accumulator)
    fill_policy = _require_canonical_object("candidate.fill_policy_json", fill_policy_json)
    gross_entry_debit = quantity * price
    quote = quote_fee(
        FeeContext(
            schedule_id=schedule,
            role=FeeRole(fee_role),
            quantity=quantity,
            price=price,
            signed_revenue=-gross_entry_debit,
            order_id=str(fill_policy["order_id"]),
            accumulator=accumulator,
            multiplier=multiplier,
            coefficient=coefficient,
            account_precision=precision,
            timestamp=_parse_timestamp(decision_at),
        )
    )
    net_entry_debit = gross_entry_debit + quote.net_fee
    persisted = (
        _parse_decimal("candidate.gross_entry_debit", persisted_gross_entry_debit),
        _parse_decimal("candidate.entry_fee", persisted_entry_fee),
        _parse_decimal("candidate.net_entry_debit", persisted_net_entry_debit),
    )
    if persisted != (gross_entry_debit, quote.net_fee, net_entry_debit):
        raise ValueError("candidate pinned fee computation does not match")
    return gross_entry_debit, quote.net_fee, net_entry_debit


def _validate_failures(failures: tuple[str, ...]) -> None:
    if not isinstance(failures, tuple):
        raise ValueError("ordered_failures must be a tuple")
    if len(set(failures)) != len(failures):
        raise ValueError("ordered_failures must not contain duplicates")
    for failure in failures:
        _require_text("ordered_failures item", failure)
        if not any(failure == gate or failure.startswith(f"{gate}_") for gate in _GATE_NAMES):
            raise ValueError("ordered_failures item must identify G1 through G7")


def _validate_unscorable_reasons(reasons: tuple[str, ...]) -> None:
    if not isinstance(reasons, tuple):
        raise ValueError("ordered_unscorable_reasons must be a tuple")
    if len(set(reasons)) != len(reasons):
        raise ValueError("ordered_unscorable_reasons must not contain duplicates")
    for reason in reasons:
        _require_text("ordered_unscorable_reasons item", reason)


def _validate_partial_artifacts_json(value: str) -> None:
    root = _require_canonical_object("partial_artifacts_json", value)
    _require_exact_keys(
        "partial_artifacts_json",
        root,
        {"artifacts", "schema_version"},
    )
    _require_schema_version("partial_artifacts_json", root)
    artifacts = root["artifacts"]
    if not isinstance(artifacts, dict) or not artifacts:
        raise ValueError("partial_artifacts_json requires typed artifact metadata")
    for artifact_name, metadata in artifacts.items():
        _require_text("partial_artifacts_json artifact name", artifact_name)
        if not isinstance(metadata, dict):
            raise ValueError("partial_artifacts_json artifact metadata is not typed")
        _require_exact_keys(
            f"partial_artifacts_json.{artifact_name}",
            metadata,
            {"available", "payload_sha256"},
        )
        available = metadata["available"]
        payload_sha256 = metadata["payload_sha256"]
        if not isinstance(available, bool):
            raise ValueError("partial_artifacts_json available is not bool")
        if available:
            _require_sha256(
                f"partial_artifacts_json.{artifact_name}.payload_sha256",
                payload_sha256,
            )
        elif payload_sha256 is not None:
            raise ValueError("unavailable partial artifact cannot carry a payload hash")


def _validate_failure_results(failures: tuple[str, ...], gate_results: Mapping[str, object]) -> None:
    actual_failures = tuple(failure for gate in _GATE_NAMES for failure in gate_results[gate]["failure_reasons"])
    if failures != actual_failures:
        raise ValueError("ordered_failures and gate_results_json disagree in content or order")


def _require_text(name: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ValueError(f"{name} must be nonempty text")


def _require_sha256(name: str, value: object) -> None:
    if not isinstance(value, str) or _SHA256_TEXT.fullmatch(value) is None:
        raise ValueError(f"{name} must be lowercase SHA-256 text")


def _require_decimal(name: str, value: object) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{name} must be a finite Decimal")


def _require_utc_datetime(name: str, value: object) -> None:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
        or value.utcoffset() != timezone.utc.utcoffset(value)
    ):
        raise ValueError(f"{name} must be a UTC timezone-aware datetime")


def _require_canonical_object(name: str, value: object) -> dict[str, object]:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be canonical JSON")
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"{name} must be canonical JSON") from exc
    if not isinstance(parsed, dict) or canonical_json(parsed) != value:
        raise ValueError(f"{name} must be canonical JSON object text")
    return parsed


def _require_canonical_json_value(name: str, value: object) -> object:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be canonical JSON")
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"{name} must be canonical JSON") from exc
    if canonical_json(parsed) != value:
        raise ValueError(f"{name} must be canonical JSON text")
    return parsed


def _require_bounded_limit(name: str, value: object, *, maximum: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    if value > maximum:
        raise ValueError(f"{name} exceeds hard bounded maximum {maximum}")


def _timestamp(value: datetime) -> str:
    _require_utc_datetime("timestamp", value)
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    _require_utc_datetime("timestamp", parsed)
    if _timestamp(parsed) != value:
        raise ValueError("timestamp is not canonical UTC text")
    return parsed


def _decimal_text(value: Decimal) -> str:
    _require_decimal("decimal", value)
    if value == 0:
        return "0"
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _optional_decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else _decimal_text(value)


def _parse_decimal(name: str, value: str) -> Decimal:
    parsed = Decimal(value)
    _require_decimal(name, parsed)
    if _decimal_text(parsed) != value:
        raise ValueError(f"{name} is not canonical Decimal text")
    return parsed


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _stable_id(namespace: str, *parts: str) -> str:
    return _sha256("\x00".join((namespace, *parts)))


def _user_schema_objects(
    conn: sqlite3.Connection,
) -> tuple[tuple[str, str, str], ...]:
    return tuple(
        (str(kind), str(name), str(sql))
        for kind, name, sql in conn.execute(
            "SELECT type, name, sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
        )
    )


def capital_guard_shadow_schema_contract_matches(
    conn: sqlite3.Connection,
) -> bool:
    """Return whether ``conn`` exactly matches the isolated shadow contract."""
    try:
        CapitalGuardShadowStore()._validate_schema(conn)
    except (CapitalGuardShadowSchemaError, sqlite3.Error, TypeError, ValueError):
        return False
    return True


CAPITAL_GUARD_SHADOW_REPLAY_SNAPSHOT_VERSION = 2


class CapitalGuardShadowReplaySnapshotError(RuntimeError):
    """A read-only shadow replay snapshot cannot be formed safely."""


@dataclass(frozen=True)
class CapitalGuardShadowReplayObservation:
    observation_sha256: str
    venue: Venue
    venue_market_id: str
    alias: str
    contract_fingerprint: str
    rules_fingerprint: str
    settlement_fingerprint: str
    outcome: Literal["yes", "no", "void", "unresolved"]
    observed_at: datetime
    effective_at: datetime
    source_id: str
    rules_version: str
    source_payload_json: str
    authoritative_payload_sha256: str
    authoritative_observation_sha256: str
    void_refund_json: str | None


@dataclass(frozen=True)
class CapitalGuardShadowReplaySettlement:
    settlement_id: str
    observation_sha256: str
    outcome: Literal["yes", "no", "void"]
    settled_at: datetime
    gross_payout: Decimal
    settlement_fee: Decimal
    settlement_refund: Decimal
    net_payout: Decimal
    details_json: str
    economics_contract_sha256: str | None
    economics_unscorable_reason: str | None


@dataclass(frozen=True)
class CapitalGuardShadowReplayCandidate:
    candidate_id: str
    decision_at: datetime
    venue: Venue
    venue_market_id: str
    market_family: str
    side: Literal["yes", "no"]
    replay_eligible: bool
    executable_price: Decimal
    executable_quantity: Decimal
    gross_entry_debit: Decimal
    entry_fee: Decimal
    net_entry_debit: Decimal
    fee_schedule_json: str
    fee_provenance_sha256: str
    fee_role: FeeRole
    fee_multiplier: Decimal
    fee_coefficient: Decimal
    fee_account_precision: Decimal | None
    fee_accumulator: Decimal
    fill_policy_json: str
    current_observation: CapitalGuardShadowReplayObservation | None
    current_settlement: CapitalGuardShadowReplaySettlement | None
    latest_settlement_attempt_status: str | None
    latest_quarantine_reason: str | None


@dataclass(frozen=True)
class CapitalGuardShadowReplaySnapshot:
    snapshot_version: int
    schema_version: int
    snapshot_sha256: str
    conflict_count: int
    settlement_quarantine_count: int
    candidates: tuple[CapitalGuardShadowReplayCandidate, ...]


def read_capital_guard_shadow_replay_snapshot(
    db_path: Path | str = CAPITAL_GUARD_SHADOW_DB,
) -> CapitalGuardShadowReplaySnapshot:
    """Read one validated replay snapshot without opening a writable SQLite handle."""
    path = Path(db_path)
    if not path.is_file():
        raise FileNotFoundError(path)
    source_signature = _replay_source_signature(path)
    source_paths = _replay_source_paths(path)
    with tempfile.TemporaryDirectory(prefix="capital-guard-shadow-replay-") as directory:
        snapshot_path = Path(directory) / source_paths[0].name
        for source in source_paths:
            shutil.copyfile(source, Path(directory) / source.name)
        if _replay_source_signature(path) != source_signature:
            raise CapitalGuardShadowReplaySnapshotError(
                "capital guard shadow ledger changed while replay snapshot was copied"
            )
        snapshot = _read_replay_snapshot_copy(snapshot_path)
    if _replay_source_signature(path) != source_signature:
        raise CapitalGuardShadowReplaySnapshotError(
            "capital guard shadow ledger changed while replay snapshot was read"
        )
    return snapshot


def _read_replay_snapshot_copy(path: Path) -> CapitalGuardShadowReplaySnapshot:
    try:
        conn = _SQLITE_CONNECT(
            f"{path.as_uri()}?mode=ro",
            uri=True,
            timeout=_BUSY_TIMEOUT_MS / 1_000,
            isolation_level=None,
        )
    except (OSError, sqlite3.Error) as exc:
        raise CapitalGuardShadowReplaySnapshotError(
            "capital guard shadow replay snapshot copy is not readable"
        ) from exc
    try:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        CapitalGuardShadowStore()._validate_schema(conn)
        snapshot = _read_replay_snapshot_transaction(conn)
        conn.commit()
        return snapshot
    except CapitalGuardShadowSchemaError:
        raise
    except (sqlite3.Error, TypeError, ValueError, KeyError) as exc:
        raise CapitalGuardShadowReplaySnapshotError("capital guard shadow replay snapshot is invalid") from exc
    finally:
        if conn.in_transaction:
            conn.rollback()
        conn.close()


def _replay_source_paths(path: Path) -> tuple[Path, ...]:
    source = path.resolve(strict=True)
    journal = source.with_name(source.name + "-journal")
    if journal.exists():
        raise CapitalGuardShadowReplaySnapshotError(
            "capital guard shadow replay cannot snapshot an active rollback journal"
        )
    paths = [source]
    wal = source.with_name(source.name + "-wal")
    if wal.exists():
        paths.append(wal)
    for item in paths:
        if item.stat().st_size > MAX_REPLAY_SNAPSHOT_FILE_BYTES:
            raise CapitalGuardShadowReplaySnapshotError(
                "capital guard shadow replay input exceeds the bounded snapshot size"
            )
    return tuple(paths)


def _replay_source_signature(path: Path) -> tuple[tuple[str, int, str], ...]:
    return tuple((item.name, item.stat().st_size, _file_sha256(item)) for item in _replay_source_paths(path))


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(64 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _read_replay_snapshot_transaction(
    conn: sqlite3.Connection,
) -> CapitalGuardShadowReplaySnapshot:
    current_heads = _read_current_replay_heads(conn)
    candidate_links = _read_current_replay_candidate_links(conn, current_heads)
    settlements = _read_current_replay_settlements(conn, current_heads)
    latest_attempts = _read_latest_replay_attempts(conn)

    rows = conn.execute(
        """
        SELECT candidate_id, decision_at, venue, venue_market_id, market_family,
               side, replay_eligible, executable_price_dollars,
               executable_quantity, gross_entry_debit_dollars, entry_fee_dollars,
               net_entry_debit_dollars, fee_schedule_json, fee_provenance_sha256,
               fee_role, fee_multiplier, fee_coefficient,
               fee_account_precision_dollars, fee_accumulator_dollars,
               fill_policy_json
        FROM capital_guard_shadow_candidates
        ORDER BY decision_at, candidate_id
        """
    ).fetchall()
    candidates: list[CapitalGuardShadowReplayCandidate] = []
    for row in rows:
        candidate_id = str(row[0])
        market_key = (str(row[2]), str(row[3]))
        head = current_heads.get(market_key)
        if head is not None and candidate_links.get(candidate_id) != head.observation_sha256:
            head = None
        settlement = settlements.get((candidate_id, head.observation_sha256)) if head is not None else None
        attempt_status, quarantine_reason = latest_attempts.get(market_key, (None, None))
        candidates.append(
            CapitalGuardShadowReplayCandidate(
                candidate_id=candidate_id,
                decision_at=_parse_timestamp(str(row[1])),
                venue=Venue(str(row[2])),
                venue_market_id=str(row[3]),
                market_family=str(row[4]),
                side=_replay_candidate_side(str(row[5])),
                replay_eligible=bool(row[6]),
                executable_price=_parse_decimal("executable_price", str(row[7])),
                executable_quantity=_parse_decimal("executable_quantity", str(row[8])),
                gross_entry_debit=_parse_decimal("gross_entry_debit", str(row[9])),
                entry_fee=_parse_decimal("entry_fee", str(row[10])),
                net_entry_debit=_parse_decimal("net_entry_debit", str(row[11])),
                fee_schedule_json=str(row[12]),
                fee_provenance_sha256=str(row[13]),
                fee_role=FeeRole(str(row[14])),
                fee_multiplier=_parse_decimal("fee_multiplier", str(row[15])),
                fee_coefficient=_parse_decimal("fee_coefficient", str(row[16])),
                fee_account_precision=(
                    None if row[17] is None else _parse_decimal("fee_account_precision", str(row[17]))
                ),
                fee_accumulator=_parse_decimal("fee_accumulator", str(row[18])),
                fill_policy_json=str(row[19]),
                current_observation=head,
                current_settlement=settlement,
                latest_settlement_attempt_status=attempt_status,
                latest_quarantine_reason=quarantine_reason,
            )
        )

    conflict_count = int(conn.execute("SELECT COUNT(*) FROM capital_guard_shadow_conflicts").fetchone()[0])
    settlement_quarantine_count = int(
        conn.execute("SELECT COUNT(*) FROM capital_guard_shadow_settlement_quarantines").fetchone()[0]
    )
    payload = {
        "snapshot_version": CAPITAL_GUARD_SHADOW_REPLAY_SNAPSHOT_VERSION,
        "schema_version": CAPITAL_GUARD_SHADOW_SCHEMA_VERSION,
        "conflict_count": conflict_count,
        "settlement_quarantine_count": settlement_quarantine_count,
        "candidates": [_replay_candidate_payload(candidate) for candidate in candidates],
    }
    return CapitalGuardShadowReplaySnapshot(
        snapshot_version=CAPITAL_GUARD_SHADOW_REPLAY_SNAPSHOT_VERSION,
        schema_version=CAPITAL_GUARD_SHADOW_SCHEMA_VERSION,
        snapshot_sha256=_sha256(canonical_json(payload)),
        conflict_count=conflict_count,
        settlement_quarantine_count=settlement_quarantine_count,
        candidates=tuple(candidates),
    )


def _read_current_replay_heads(
    conn: sqlite3.Connection,
) -> dict[tuple[str, str], CapitalGuardShadowReplayObservation]:
    rows = conn.execute(
        """
        SELECT o.observation_sha256, o.venue, o.venue_market_id,
               o.contract_fingerprint, o.rules_fingerprint,
               o.settlement_fingerprint, o.outcome, o.observed_at,
               o.effective_at, o.source_id, o.rules_version,
               o.source_payload_json, o.authoritative_payload_sha256,
               o.authoritative_observation_sha256, o.void_refund_json,
               o.alias
        FROM capital_guard_shadow_observations AS o
        LEFT JOIN capital_guard_shadow_observations AS successor
          ON successor.supersedes_observation_sha256 = o.observation_sha256
        WHERE successor.observation_sha256 IS NULL
        ORDER BY o.venue, o.venue_market_id, o.observation_sha256
        """
    ).fetchall()
    heads: dict[tuple[str, str], CapitalGuardShadowReplayObservation] = {}
    for row in rows:
        key = (str(row[1]), str(row[2]))
        if key in heads:
            raise CapitalGuardShadowReplaySnapshotError("capital guard shadow has multiple current observation heads")
        heads[key] = CapitalGuardShadowReplayObservation(
            observation_sha256=str(row[0]),
            venue=Venue(str(row[1])),
            venue_market_id=str(row[2]),
            alias=str(row[15]),
            contract_fingerprint=str(row[3]),
            rules_fingerprint=str(row[4]),
            settlement_fingerprint=str(row[5]),
            outcome=_replay_observation_outcome(str(row[6])),
            observed_at=_parse_timestamp(str(row[7])),
            effective_at=_parse_timestamp(str(row[8])),
            source_id=str(row[9]),
            rules_version=str(row[10]),
            source_payload_json=str(row[11]),
            authoritative_payload_sha256=str(row[12]),
            authoritative_observation_sha256=str(row[13]),
            void_refund_json=None if row[14] is None else str(row[14]),
        )
    return heads


def _read_current_replay_candidate_links(
    conn: sqlite3.Connection,
    current_heads: Mapping[tuple[str, str], CapitalGuardShadowReplayObservation],
) -> dict[str, str]:
    if not current_heads:
        return {}
    head_ids = {head.observation_sha256 for head in current_heads.values()}
    rows = conn.execute(
        """
        SELECT candidate_id, observation_sha256
        FROM capital_guard_shadow_candidate_observations
        ORDER BY candidate_id, observation_sha256
        """
    ).fetchall()
    links: dict[str, str] = {}
    for candidate_id_raw, observation_sha256_raw in rows:
        candidate_id = str(candidate_id_raw)
        observation_sha256 = str(observation_sha256_raw)
        if observation_sha256 not in head_ids:
            continue
        previous = links.setdefault(candidate_id, observation_sha256)
        if previous != observation_sha256:
            raise CapitalGuardShadowReplaySnapshotError("candidate is linked to multiple current observation heads")
    return links


def _read_current_replay_settlements(
    conn: sqlite3.Connection,
    current_heads: Mapping[tuple[str, str], CapitalGuardShadowReplayObservation],
) -> dict[tuple[str, str], CapitalGuardShadowReplaySettlement]:
    if not current_heads:
        return {}
    head_ids = {head.observation_sha256 for head in current_heads.values()}
    cursor = conn.execute(
        """
        SELECT s.settlement_id AS settlement_id,
               s.candidate_id AS candidate_id,
               s.observation_sha256 AS observation_sha256,
               s.outcome AS settlement_outcome,
               s.settled_at AS settled_at,
               s.gross_payout_dollars AS gross_payout_dollars,
               s.settlement_fee_dollars AS settlement_fee_dollars,
               s.settlement_refund_dollars AS settlement_refund_dollars,
               s.net_payout_dollars AS net_payout_dollars,
               s.details_json AS details_json,
               s.payload_sha256 AS payload_sha256,
               o.venue AS observation_venue,
               o.venue_market_id AS observation_venue_market_id,
               o.alias AS observation_alias,
               o.contract_fingerprint AS contract_fingerprint,
               o.rules_fingerprint AS rules_fingerprint,
               o.settlement_fingerprint AS settlement_fingerprint,
               o.authoritative_observation_sha256 AS authoritative_observation_sha256,
               o.authoritative_payload_sha256 AS authoritative_payload_sha256,
               o.source_id AS source_id,
               o.source_payload_json AS source_payload_json,
               o.outcome AS observation_outcome,
               o.effective_at AS observation_effective_at,
               o.observed_at AS observation_observed_at,
               o.void_refund_json AS void_refund_json,
               c.side AS candidate_side,
               c.venue AS candidate_venue,
               c.venue_market_id AS candidate_venue_market_id,
               c.identity_json AS candidate_identity_json,
               c.decision_at AS candidate_decision_at,
               c.executable_quantity AS executable_quantity,
               c.executable_price_dollars AS executable_price_dollars,
               c.fee_schedule_json AS fee_schedule_json,
               c.fee_formula_type AS fee_formula_type,
               c.fee_role AS fee_role,
               c.fee_multiplier AS fee_multiplier,
               c.fee_coefficient AS fee_coefficient,
               c.fee_account_precision_dollars AS fee_account_precision_dollars,
               c.fee_accumulator_dollars AS fee_accumulator_dollars,
               c.fill_policy_json AS fill_policy_json,
               c.gross_entry_debit_dollars AS gross_entry_debit_dollars,
               c.entry_fee_dollars AS entry_fee_dollars,
               c.net_entry_debit_dollars AS net_entry_debit_dollars
        FROM capital_guard_shadow_settlements AS s
        JOIN capital_guard_shadow_observations AS o
          ON o.observation_sha256 = s.observation_sha256
        JOIN capital_guard_shadow_candidates AS c
          ON c.candidate_id = s.candidate_id
        ORDER BY s.candidate_id, s.observation_sha256, s.settlement_id
        """
    )
    columns = tuple(str(item[0]) for item in cursor.description)
    rows = tuple(dict(zip(columns, row, strict=True)) for row in cursor.fetchall())
    settlement_count_by_candidate: dict[str, int] = {}
    for row in rows:
        candidate_id = str(row["candidate_id"])
        settlement_count_by_candidate[candidate_id] = settlement_count_by_candidate.get(candidate_id, 0) + 1
        if settlement_count_by_candidate[candidate_id] > 1:
            raise CapitalGuardShadowReplaySnapshotError("candidate has multiple financial settlements")
    settlements: dict[tuple[str, str], CapitalGuardShadowReplaySettlement] = {}
    for row in rows:
        candidate_id = str(row["candidate_id"])
        observation_sha256 = str(row["observation_sha256"])
        if observation_sha256 not in head_ids:
            continue
        key = (candidate_id, observation_sha256)
        if key in settlements:
            raise CapitalGuardShadowReplaySnapshotError("candidate has multiple financial settlements for current head")
        settled_at = _parse_timestamp(str(row["settled_at"]))
        outcome = _replay_settlement_outcome(str(row["settlement_outcome"]))
        gross_payout = _parse_decimal("gross_payout", str(row["gross_payout_dollars"]))
        settlement_fee = _parse_decimal("settlement_fee", str(row["settlement_fee_dollars"]))
        settlement_refund = _parse_decimal("settlement_refund", str(row["settlement_refund_dollars"]))
        net_payout = _parse_decimal("net_payout", str(row["net_payout_dollars"]))
        details_json = str(row["details_json"])
        economics_contract_sha256, economics_unscorable_reason = _replay_settlement_economics_status(
            settlement_id=str(row["settlement_id"]),
            candidate_id=candidate_id,
            observation_sha256=observation_sha256,
            details_json=details_json,
            payload_sha256=str(row["payload_sha256"]),
            settled_at=settled_at,
            outcome=outcome,
            gross_payout=gross_payout,
            settlement_fee=settlement_fee,
            settlement_refund=settlement_refund,
            net_payout=net_payout,
            observation_venue=Venue(str(row["observation_venue"])),
            observation_venue_market_id=str(row["observation_venue_market_id"]),
            observation_alias=str(row["observation_alias"]),
            contract_fingerprint=str(row["contract_fingerprint"]),
            rules_fingerprint=str(row["rules_fingerprint"]),
            settlement_fingerprint=str(row["settlement_fingerprint"]),
            authoritative_observation_sha256=str(row["authoritative_observation_sha256"]),
            authoritative_payload_sha256=str(row["authoritative_payload_sha256"]),
            source_id=str(row["source_id"]),
            source_payload_json=str(row["source_payload_json"]),
            observation_outcome=str(row["observation_outcome"]),
            observation_effective_at=_parse_timestamp(str(row["observation_effective_at"])),
            observation_observed_at=_parse_timestamp(str(row["observation_observed_at"])),
            void_refund_json=(None if row["void_refund_json"] is None else str(row["void_refund_json"])),
            candidate_side=str(row["candidate_side"]),
            candidate_venue=Venue(str(row["candidate_venue"])),
            candidate_venue_market_id=str(row["candidate_venue_market_id"]),
            candidate_identity_json=str(row["candidate_identity_json"]),
            candidate_decision_at=_parse_timestamp(str(row["candidate_decision_at"])),
            executable_quantity=_parse_decimal("candidate.executable_quantity", str(row["executable_quantity"])),
            executable_price=_parse_decimal("candidate.executable_price", str(row["executable_price_dollars"])),
            fee_schedule_json=str(row["fee_schedule_json"]),
            fee_formula_type=str(row["fee_formula_type"]),
            fee_role=str(row["fee_role"]),
            fee_multiplier=str(row["fee_multiplier"]),
            fee_coefficient=str(row["fee_coefficient"]),
            fee_account_precision=(
                None if row["fee_account_precision_dollars"] is None else str(row["fee_account_precision_dollars"])
            ),
            fee_accumulator=str(row["fee_accumulator_dollars"]),
            fill_policy_json=str(row["fill_policy_json"]),
            persisted_gross_entry_debit=str(row["gross_entry_debit_dollars"]),
            persisted_entry_fee=str(row["entry_fee_dollars"]),
            persisted_net_entry_debit=str(row["net_entry_debit_dollars"]),
        )
        settlements[key] = CapitalGuardShadowReplaySettlement(
            settlement_id=str(row["settlement_id"]),
            observation_sha256=observation_sha256,
            outcome=outcome,
            settled_at=settled_at,
            gross_payout=gross_payout,
            settlement_fee=settlement_fee,
            settlement_refund=settlement_refund,
            net_payout=net_payout,
            details_json=details_json,
            economics_contract_sha256=economics_contract_sha256,
            economics_unscorable_reason=economics_unscorable_reason,
        )
    return settlements


def _replay_settlement_economics_status(
    *,
    settlement_id: str,
    candidate_id: str,
    observation_sha256: str,
    details_json: str,
    payload_sha256: str,
    settled_at: datetime,
    outcome: Literal["yes", "no", "void"],
    gross_payout: Decimal,
    settlement_fee: Decimal,
    settlement_refund: Decimal,
    net_payout: Decimal,
    observation_venue: Venue,
    observation_venue_market_id: str,
    observation_alias: str,
    contract_fingerprint: str,
    rules_fingerprint: str,
    settlement_fingerprint: str,
    authoritative_observation_sha256: str,
    authoritative_payload_sha256: str,
    source_id: str,
    source_payload_json: str,
    observation_outcome: str,
    observation_effective_at: datetime,
    observation_observed_at: datetime,
    void_refund_json: str | None,
    candidate_side: str,
    candidate_venue: Venue,
    candidate_venue_market_id: str,
    candidate_identity_json: str,
    candidate_decision_at: datetime,
    executable_quantity: Decimal,
    executable_price: Decimal,
    fee_schedule_json: str,
    fee_formula_type: str,
    fee_role: str,
    fee_multiplier: str,
    fee_coefficient: str,
    fee_account_precision: str | None,
    fee_accumulator: str,
    fill_policy_json: str,
    persisted_gross_entry_debit: str,
    persisted_entry_fee: str,
    persisted_net_entry_debit: str,
) -> tuple[str | None, str | None]:
    """Revalidate rows without treating a counterfactual shadow fill as realized P&L."""

    try:
        if outcome != observation_outcome:
            raise SettlementEconomicsUnscorableError("settlement outcome does not match observation")
        if settled_at != observation_effective_at:
            raise SettlementEconomicsUnscorableError("settled_at does not match authoritative effective_at")
        if candidate_decision_at >= observation_observed_at:
            raise SettlementEconomicsUnscorableError("candidate decision does not precede observed outcome")
        if candidate_decision_at >= observation_effective_at:
            raise SettlementEconomicsUnscorableError("candidate decision does not precede authoritative effective_at")
        if candidate_venue is not observation_venue:
            raise SettlementEconomicsUnscorableError("candidate venue does not match observation")
        if candidate_venue_market_id != observation_venue_market_id:
            raise SettlementEconomicsUnscorableError("candidate market identity does not match observation")
        candidate_identity = _require_canonical_object("candidate.identity_json", candidate_identity_json)
        expected_identity = {
            "alias": observation_alias,
            "contract_fingerprint": contract_fingerprint,
            "rules_fingerprint": rules_fingerprint,
            "settlement_fingerprint": settlement_fingerprint,
            "venue": observation_venue.value,
            "venue_market_id": observation_venue_market_id,
        }
        if any(candidate_identity.get(key) != value for key, value in expected_identity.items()):
            raise SettlementEconomicsUnscorableError("candidate contract identity does not match observation")
        evidence = deserialize_settlement_economics_evidence(details_json)
        binding = SettlementEconomicsBinding(
            venue=observation_venue,
            venue_market_id=observation_venue_market_id,
            account_party_id_sha256=evidence.binding.account_party_id_sha256,
            contract_fingerprint=contract_fingerprint,
            rules_fingerprint=rules_fingerprint,
            settlement_fingerprint=settlement_fingerprint,
            authoritative_observation_sha256=authoritative_observation_sha256,
            authoritative_payload_sha256=authoritative_payload_sha256,
            source_id=source_id,
        )
        if evidence.binding != binding:
            raise SettlementEconomicsUnscorableError("settlement economics binding does not match observation")
        validate_settlement_economics_contract(
            evidence.contract,
            venue=observation_venue,
        )
        fee_receipt = derive_settlement_fee_receipt(
            contract=evidence.contract,
            binding=binding,
            source_payload_json=source_payload_json,
        )
        _gross_entry_debit, entry_fee, _net_entry_debit = _entry_accounting_from_persisted_values(
            decision_at=_timestamp(candidate_decision_at),
            executable_price=_decimal_text(executable_price),
            executable_quantity=_decimal_text(executable_quantity),
            fee_schedule_json=fee_schedule_json,
            fee_formula_type=fee_formula_type,
            fee_role=fee_role,
            fee_multiplier=fee_multiplier,
            fee_coefficient=fee_coefficient,
            fee_account_precision=fee_account_precision,
            fee_accumulator=fee_accumulator,
            fill_policy_json=fill_policy_json,
            persisted_gross_entry_debit=persisted_gross_entry_debit,
            persisted_entry_fee=persisted_entry_fee,
            persisted_net_entry_debit=persisted_net_entry_debit,
        )
        cashflows = derive_settlement_cashflows(
            contract=evidence.contract,
            binding=binding,
            outcome=MarketOutcome(outcome),
            held_side=candidate_side,
            quantity=executable_quantity,
            entry_price=executable_price,
            entry_fee=entry_fee,
            void_refund=_void_refund_from_json(void_refund_json),
            fee_receipt=fee_receipt,
        )
        persisted_cashflows = SettlementCashflows(
            outcome=outcome,
            gross_payout=gross_payout,
            settlement_fee=settlement_fee,
            settlement_refund=settlement_refund,
            net_payout=net_payout,
        )
        if evidence.fee_receipt != fee_receipt or evidence.cashflows != cashflows or cashflows != persisted_cashflows:
            raise SettlementEconomicsUnscorableError("settlement economics cashflows do not match persisted settlement")
        record = ShadowSettlement(
            candidate_id=candidate_id,
            observation_sha256=observation_sha256,
            outcome=outcome,
            settled_at=settled_at,
            economics_contract=evidence.contract,
            economics_binding=binding,
        )
        payload = _settlement_payload(record, cashflows, details_json)
        expected_payload_sha256 = _sha256(canonical_json(payload))
        if payload_sha256 != expected_payload_sha256:
            raise SettlementEconomicsUnscorableError("settlement payload hash does not match immutable settlement")
        expected_settlement_id = _stable_id("capital-guard-settlement-v1", expected_payload_sha256)
        if settlement_id != expected_settlement_id:
            raise SettlementEconomicsUnscorableError("settlement id does not match immutable settlement")
    except (SettlementEconomicsUnscorableError, ValueError, TypeError, KeyError):
        return None, "legacy_or_invalid_settlement_economics"
    # A G7 shadow candidate has no venue order/fill identity. A market/account
    # settlement report can validate market-level fee data, but cannot attribute
    # it to this hypothetical fill. Keep the row visible and unscorable until an
    # authenticated candidate-to-fill-to-settlement adapter exists.
    return None, "counterfactual_shadow_candidate_lacks_attributed_execution_receipt"


def _read_latest_replay_attempts(
    conn: sqlite3.Connection,
) -> dict[tuple[str, str], tuple[str | None, str | None]]:
    rows = conn.execute(
        """
        SELECT a.venue, a.venue_market_id, a.status, a.attempted_at, a.attempt_id,
               q.reason_taxonomy
        FROM capital_guard_shadow_settlement_attempts AS a
        LEFT JOIN capital_guard_shadow_settlement_quarantines AS q
          ON q.attempt_id = a.attempt_id
        ORDER BY a.venue, a.venue_market_id, a.attempted_at DESC, a.attempt_id DESC
        """
    ).fetchall()
    attempts: dict[tuple[str, str], tuple[str | None, str | None]] = {}
    for row in rows:
        key = (str(row[0]), str(row[1]))
        attempts.setdefault(
            key,
            (str(row[2]), None if row[5] is None else str(row[5])),
        )
    return attempts


def _replay_candidate_side(value: str) -> Literal["yes", "no"]:
    if value not in {"yes", "no"}:
        raise CapitalGuardShadowReplaySnapshotError("invalid replay candidate side")
    return value  # type: ignore[return-value]


def _replay_observation_outcome(
    value: str,
) -> Literal["yes", "no", "void", "unresolved"]:
    if value not in {"yes", "no", "void", "unresolved"}:
        raise CapitalGuardShadowReplaySnapshotError("invalid replay observation outcome")
    return value  # type: ignore[return-value]


def _replay_settlement_outcome(value: str) -> Literal["yes", "no", "void"]:
    if value not in {"yes", "no", "void"}:
        raise CapitalGuardShadowReplaySnapshotError("invalid replay settlement outcome")
    return value  # type: ignore[return-value]


def _replay_candidate_payload(
    candidate: CapitalGuardShadowReplayCandidate,
) -> dict[str, object]:
    return {
        "candidate_id": candidate.candidate_id,
        "decision_at": _timestamp(candidate.decision_at),
        "venue": candidate.venue.value,
        "venue_market_id": candidate.venue_market_id,
        "market_family": candidate.market_family,
        "side": candidate.side,
        "replay_eligible": candidate.replay_eligible,
        "executable_price": _decimal_text(candidate.executable_price),
        "executable_quantity": _decimal_text(candidate.executable_quantity),
        "gross_entry_debit": _decimal_text(candidate.gross_entry_debit),
        "entry_fee": _decimal_text(candidate.entry_fee),
        "net_entry_debit": _decimal_text(candidate.net_entry_debit),
        "fee_schedule_json": candidate.fee_schedule_json,
        "fee_provenance_sha256": candidate.fee_provenance_sha256,
        "fee_role": candidate.fee_role.value,
        "fee_multiplier": _decimal_text(candidate.fee_multiplier),
        "fee_coefficient": _decimal_text(candidate.fee_coefficient),
        "fee_account_precision": _optional_decimal_text(candidate.fee_account_precision),
        "fee_accumulator": _decimal_text(candidate.fee_accumulator),
        "fill_policy_json": candidate.fill_policy_json,
        "current_observation": _replay_observation_payload(candidate.current_observation),
        "current_settlement": _replay_settlement_payload(candidate.current_settlement),
        "latest_settlement_attempt_status": candidate.latest_settlement_attempt_status,
        "latest_quarantine_reason": candidate.latest_quarantine_reason,
    }


def _replay_observation_payload(
    observation: CapitalGuardShadowReplayObservation | None,
) -> dict[str, object] | None:
    if observation is None:
        return None
    return {
        "authoritative_observation_sha256": observation.authoritative_observation_sha256,
        "authoritative_payload_sha256": observation.authoritative_payload_sha256,
        "contract_fingerprint": observation.contract_fingerprint,
        "effective_at": _timestamp(observation.effective_at),
        "observation_sha256": observation.observation_sha256,
        "outcome": observation.outcome,
        "observed_at": _timestamp(observation.observed_at),
        "rules_fingerprint": observation.rules_fingerprint,
        "source_id": observation.source_id,
        "source_payload_json": observation.source_payload_json,
        "settlement_fingerprint": observation.settlement_fingerprint,
        "venue": observation.venue.value,
        "venue_market_id": observation.venue_market_id,
        "alias": observation.alias,
        "rules_version": observation.rules_version,
        "void_refund_json": observation.void_refund_json,
    }


def _replay_settlement_payload(
    settlement: CapitalGuardShadowReplaySettlement | None,
) -> dict[str, object] | None:
    if settlement is None:
        return None
    return {
        "settlement_id": settlement.settlement_id,
        "observation_sha256": settlement.observation_sha256,
        "outcome": settlement.outcome,
        "settled_at": _timestamp(settlement.settled_at),
        "gross_payout": _decimal_text(settlement.gross_payout),
        "settlement_fee": _decimal_text(settlement.settlement_fee),
        "settlement_refund": _decimal_text(settlement.settlement_refund),
        "net_payout": _decimal_text(settlement.net_payout),
        "details_json": settlement.details_json,
    }
