"""
Paper trading engine.

Stores simulated trades in SQLite (data/paper_trades.db).

Key additions vs. original:
  - Notional bankroll tracked in bot_state, debited on entry, credited on resolution
  - Source credibility updated on every resolution
  - go_live_confirmed flag in bot_state controls paper vs. live mode
  - Weekly review reminder emitted in daily_summary()
"""

import asyncio
import dataclasses
import hashlib
import json
import logging
import math
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable

if TYPE_CHECKING:
    from tasks.calibration_task import CalibrationTask

from tabulate import tabulate

import config as config_module
from analysis import SignalAnalysis
from analysis.match_feedback import matcher_weights_status
from kalshi.public_market_data import is_safe_kalshi_identifier
from tasks.stats.source_credibility import SourceCredibility
from config import cfg, DATA_DIR
from trading.fees import FeeUnscorableError, fee_schedule_at
from trading.execution_terms import FinalExecutionTerms, final_execution_terms
from trading.paper_cohorts import (
    assert_initialized_paper_cohort_schema,
    immutable_paper_database_block_reason,
    mark_paper_cohort_database_initialized,
    paper_cohort_binding_for_db,
    paper_runtime_database_topology_block_reason,
    unbound_paper_runtime_database_block_reason,
)
from trading.paper_accounting import (
    PAPER_ACCOUNTING_FRESH_PLAN_SHA256,
    PAPER_ACCOUNTING_VERSION,
    PaperAccountingAdmissionError,
    PaperAccountingHandlers,
    initialize_fresh_paper_accounting_schema,
    paper_accounting_schema_contract_matches,
    require_paper_accounting_admission,
)
from trading.portfolio import Portfolio, Position
from trading.profit_evidence import independent_realized_profit_evidence_available
from trading.sizing_guard import effective_sizing_bankroll
from trading.settlement import (
    MarketOutcome,
    SettlementDriftError,
    SettlementObservation,
    VoidRefundContract,
    validate_observation_transition,
)
from trading.settlement_store import (
    SETTLEMENT_PAPER_TRADE_COLUMNS,
    SETTLEMENT_PAPER_TRADE_COLUMNS_SQL,
    StoreCheck,
    canonical_entry_schema_ready,
    enable_and_verify_foreign_keys,
    initialize_fresh_settlement_schema,
    paper_trade_settled_outbox_contract,
    settlement_keyword_directions,
    settlement_schema_contract_matches,
)
from trading.venue import MarketRef, Venue, normalize_venue
from utils.logger import get_logger, trade_log, TRADE_LOG_FILE

log = get_logger("paper_trader")

DB_PATH = DATA_DIR / "paper_trades.db"

_ACTIVE_COHORT_LIVE_TRANSITION_BLOCK = (
    "active paper cohort remains isolated from live trading"
)
_LEGACY_PENDING_COHORT_LIVE_TRANSITION_BLOCK = (
    "legacy-pending paper cohort remains permanently isolated from live trading"
)


def _resolve_starting_bankroll(value: float | None) -> float:
    """Keep a cohort's initial paper account separate from process-wide config."""

    candidate = cfg.bankroll if value is None else value
    if isinstance(candidate, bool):
        raise ValueError("starting_bankroll must be a positive finite number")
    try:
        bankroll = float(candidate)
    except (TypeError, ValueError) as exc:
        raise ValueError("starting_bankroll must be a positive finite number") from exc
    if not math.isfinite(bankroll) or bankroll <= 0:
        raise ValueError("starting_bankroll must be a positive finite number")
    return bankroll


class _SettlementQuarantineRequired(RuntimeError):
    def __init__(self, reason_code: str, details: dict[str, object]):
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.details = details


class _SettlementObservationAlreadyApplied(RuntimeError):
    pass


def _settlement_json(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _settlement_sha256(value: object) -> str:
    return hashlib.sha256(_settlement_json(value).encode("utf-8")).hexdigest()


def _settlement_decimal(value: object, field: str) -> Decimal:
    if value is None or isinstance(value, bool):
        raise _SettlementQuarantineRequired(
            "invalid_financial_state",
            {"field": field, "value": repr(value)},
        )
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise _SettlementQuarantineRequired(
            "invalid_financial_state",
            {"field": field, "value": repr(value)},
        ) from exc
    if not result.is_finite():
        raise _SettlementQuarantineRequired(
            "invalid_financial_state",
            {"field": field, "value": repr(value)},
        )
    return result


def _settlement_decimal_text(value: Decimal) -> str:
    if value == 0:
        return "0"
    return format(value.normalize(), "f")

_DDL = f"""
CREATE TABLE IF NOT EXISTS keyword_outcomes (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id      TEXT    NOT NULL,
    ticker        TEXT    NOT NULL,
    series_ticker TEXT    NOT NULL,
    keyword       TEXT    NOT NULL,
    direction     TEXT    NOT NULL,
    market_side   TEXT    NOT NULL,
    resolved_yes  INTEGER NOT NULL,
    correct       INTEGER NOT NULL,
    ts            TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_trades (
    trade_id                TEXT PRIMARY KEY,
    ts                      TEXT NOT NULL,
    ticker                  TEXT NOT NULL,
    venue                   TEXT NOT NULL DEFAULT 'kalshi',
    venue_market_id         TEXT,
    identity_status         TEXT CHECK (
        identity_status IS NULL OR identity_status IN ('mapped', 'quarantined')
    ),
    quarantine_reason       TEXT,
{SETTLEMENT_PAPER_TRADE_COLUMNS_SQL}    market_title            TEXT NOT NULL,
    side                    TEXT NOT NULL,
    contracts               INTEGER NOT NULL,
    price_cents             INTEGER NOT NULL,
    cost_dollars            REAL NOT NULL,
    estimated_prob          REAL NOT NULL,
    entry_price_cents       REAL NOT NULL,
    edge                    REAL NOT NULL,
    kelly_dollars           REAL NOT NULL,
    capped_dollars          REAL NOT NULL,
    signal_headline         TEXT NOT NULL,
    signal_source           TEXT NOT NULL,
    keywords_matched        TEXT NOT NULL,
    reasoning               TEXT NOT NULL,
    source_multiplier       REAL DEFAULT 1.0,
    notional_bankroll_before REAL,
    notional_bankroll_after  REAL,
    resolved                INTEGER DEFAULT 0,
    resolved_yes            INTEGER,
    pnl_dollars             REAL,
    market_snapshot         TEXT,
    -- P-6 / P0-PROV-019 pricing provenance carried alongside the executed-side entry
    price_source            TEXT DEFAULT 'unavailable',
    price_method            TEXT DEFAULT 'none',
    price_retrieved_at      TEXT,
    raw_payload_hash        TEXT,
    p0_contract_version     INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS bot_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_credibility (
    source        TEXT PRIMARY KEY,
    wins          INTEGER DEFAULT 0,
    losses        INTEGER DEFAULT 0,
    total         INTEGER DEFAULT 0,
    accuracy      REAL    DEFAULT 0.5,
    multiplier    REAL    DEFAULT 1.0,
    last_updated  TEXT
);

CREATE TABLE IF NOT EXISTS source_stats (
    source        TEXT PRIMARY KEY,
    posts_seen    INTEGER DEFAULT 0,
    signals       INTEGER DEFAULT 0,
    opportunities INTEGER DEFAULT 0,
    trades        INTEGER DEFAULT 0,
    last_signal   TEXT,
    last_updated  TEXT
);

CREATE TABLE IF NOT EXISTS subreddit_candidates (
    sub            TEXT PRIMARY KEY,
    discovered_ts  TEXT NOT NULL,
    discovered_via TEXT NOT NULL,
    probe_count    INTEGER DEFAULT 0,
    last_probed    TEXT,
    status         TEXT DEFAULT 'candidate'
);
"""


@dataclasses.dataclass(frozen=True)
class _StartupStateSnapshot:
    existing_keys: set[str]
    paper_start_inserted: bool
    bankroll_inserted: bool


# P-6 / P0-PROV-019: forward-only additive provenance columns. Added to
# pre-existing DBs at PaperTrader init via idempotent ALTER TABLE so the
# CREATE-TABLE-IF-NOT-EXISTS schema and live schema converge over time.
_P0_PROVENANCE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("price_source", "TEXT DEFAULT 'unavailable'"),
    ("price_method", "TEXT DEFAULT 'none'"),
    ("price_retrieved_at", "TEXT"),
    ("raw_payload_hash", "TEXT"),
    ("p0_contract_version", "INTEGER DEFAULT 1"),
)

_VENUE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("venue", "TEXT NOT NULL DEFAULT 'kalshi'"),
)

_SETTLEMENT_SCHEMA_SENTINEL_COLUMNS = frozenset(
    {
        "venue_market_id",
        "identity_status",
        "quarantine_reason",
        *(name for name, _definition in SETTLEMENT_PAPER_TRADE_COLUMNS),
    }
)


def _market_to_jsonable(market: Any) -> dict:
    """JSON-serializable view of a KalshiMarket for ``paper_trades.market_snapshot``.

    P-6 / CR-E: replaces the legacy ``dataclasses.asdict(market)`` path so we
    can persist snapshots even when the post-P0 ``__getattribute__`` guard on
    ``yes_price`` / ``yes_bid`` / ``yes_ask`` would otherwise raise
    ``ValueError`` (legacy fields are read via ``object.__getattribute__`` to
    bypass the guard). Also converts ``Decimal`` -> ``str`` (precision-preserving)
    and ``datetime`` -> ISO-8601 UTC string so the resulting dict is safe to
    feed straight into ``json.dumps``.

    Falls back to an empty snapshot for non-dataclass inputs (test mocks);
    callers that need richer payloads should pass a real dataclass instance.
    """
    if not dataclasses.is_dataclass(market):
        return {}
    snapshot: dict[str, Any] = {}
    _legacy_guarded = ("yes_price", "yes_bid", "yes_ask")
    for f in dataclasses.fields(market):
        if f.name in _legacy_guarded:
            # CR-C guard raises on read when price_available=False; bypass
            # the guard for serialization so non-tradeable snapshots persist.
            value = object.__getattribute__(market, f.name)
        else:
            value = getattr(market, f.name)
        if isinstance(value, Decimal):
            snapshot[f.name] = str(value)
        elif isinstance(value, datetime):
            snapshot[f.name] = value.isoformat()
        else:
            snapshot[f.name] = value
    return snapshot


def _match_quality_report_section() -> list[str]:
    """
    Return a compact MATCH QUALITY section for the daily report.

    Reads MATCH_DIAGNOSTIC records from trades.jsonl (read-only).
    Diagnostic only -- no runtime behavior is affected by these metrics.
    """
    if not TRADE_LOG_FILE.exists():
        return ["MATCH QUALITY", "  No trades.jsonl found.", ""]

    from collections import Counter
    total = 0
    low = 0
    flag_counts: Counter[str] = Counter()
    ticker_low: Counter[str] = Counter()
    try:
        with TRADE_LOG_FILE.open("r", encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    ev = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if ev.get("type") != "MATCH_DIAGNOSTIC":
                    continue
                total += 1
                if ev.get("low_match_quality"):
                    low += 1
                    ticker = str(ev.get("ticker") or "").strip()
                    if ticker:
                        ticker_low[ticker] += 1
                    for f in (ev.get("heuristic_flags") or []):
                        flag_counts[str(f)] += 1
    except OSError:
        return ["MATCH QUALITY", "  Could not read trades.jsonl.", ""]

    if total == 0:
        weights_status = matcher_weights_status()
        if weights_status.get("status") == "unverified":
            return [
                "MATCH QUALITY",
                "  MATCHER FAIL-CLOSED: token weights are unverified.",
                f"  Reason: {weights_status.get('reason') or 'unknown'}",
                "",
            ]
        return ["MATCH QUALITY", "  No MATCH_DIAGNOSTIC records yet.", ""]

    low_pct = 100 * low / total
    lines = [
        "MATCH QUALITY  (approximate heuristics; diagnostic only -- not suppressed)",
        f"  Matched candidates:       {total}",
        f"  Low-quality flagged:      {low} ({low_pct:.0f}%)",
    ]
    if flag_counts:
        top_flags = ", ".join(f"{f}({c})" for f, c in flag_counts.most_common(3))
        lines.append(f"  Top flag reasons:         {top_flags}")
    if ticker_low:
        top_tickers = ", ".join(f"{t}({c})" for t, c in ticker_low.most_common(5))
        lines.append(f"  Top flagged tickers:      {top_tickers}")
    lines.append("  Full analysis: python scripts/match_quality_diagnostics.py --exclude-test")
    lines.append("")
    return lines


def _market_source_hints_report_section() -> list[str]:
    """Return a compact MarketSourceHints section for the daily report.

    Reads MARKET_SOURCE_HINT_DIAGNOSTIC records from trades.jsonl (read-only).
    Diagnostic only -- no readiness/admission/scoring/routing/trading behavior
    is affected by these metrics.
    """
    heading = "MARKET SOURCE HINTS  (shadow-only diagnostics)"
    if not TRADE_LOG_FILE.exists():
        return [heading, "  No trades.jsonl found.", ""]

    from collections import Counter

    total = 0
    shadow_only = 0
    child_shadow_records = 0
    rejected_label_count = 0
    mode_counts: Counter[str] = Counter()
    source_counts: Counter[str] = Counter()
    ticker_counts: Counter[str] = Counter()
    try:
        with TRADE_LOG_FILE.open("r", encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    ev = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if ev.get("type") != "MARKET_SOURCE_HINT_DIAGNOSTIC":
                    continue
                total += 1
                if ev.get("shadow_only") is True:
                    shadow_only += 1
                mode = str(ev.get("mode") or "unknown").strip() or "unknown"
                mode_counts[mode] += 1
                ticker = str(ev.get("ticker") or "").strip()
                if ticker:
                    ticker_counts[ticker] += 1
                for target in ev.get("targets") or []:
                    if isinstance(target, dict):
                        source = str(target.get("source") or "").strip()
                        if source:
                            source_counts[source] += 1
                rejected_labels = ev.get("rejected_labels") or {}
                if isinstance(rejected_labels, dict):
                    rejected_label_count += len(rejected_labels)
                for record in ev.get("log_records") or []:
                    if isinstance(record, dict) and record.get("shadow_only") is True:
                        child_shadow_records += 1
    except OSError:
        return [heading, "  Could not read trades.jsonl.", ""]

    if total == 0:
        return [heading, "  No MARKET_SOURCE_HINT_DIAGNOSTIC records yet.", ""]

    shadow_pct = 100 * shadow_only / total
    lines = [
        heading,
        "  diagnostic only -- not consumed by readiness/admission/trading",
        f"  Diagnostic records:       {total}",
        f"  Shadow-only records:      {shadow_only} ({shadow_pct:.0f}%)",
    ]
    if mode_counts:
        modes = ", ".join(f"{mode}({count})" for mode, count in sorted(mode_counts.items()))
        lines.append(f"  Modes observed:           {modes}")
    if source_counts:
        sources = ", ".join(f"{source}({count})" for source, count in source_counts.most_common(5))
        lines.append(f"  Top hinted sources:       {sources}")
    if ticker_counts:
        tickers = ", ".join(f"{ticker}({count})" for ticker, count in ticker_counts.most_common(5))
        lines.append(f"  Top tickers:              {tickers}")
    lines.append(f"  Rejected labels:          {rejected_label_count}")
    lines.append(f"  Child shadow records:     {child_shadow_records}")
    lines.append("")
    return lines


class PaperTrader:
    """Paper trading engine backed by SQLite."""

    _runtime_owner_pid: int | None = None

    def __init__(
        self,
        db_path: Path = DB_PATH,
        *,
        startup_context: str = "runtime",
        calibration_task: "CalibrationTask | None" = None,
        paper_accounting_handlers: PaperAccountingHandlers | None = None,
        starting_bankroll: float | None = None,
        cohort_id: str | None = None,
        live_transition_block_reason: str | None = None,
        paper_cohort_storage_root: Path | None = None,
    ):
        self._db_path = Path(db_path)
        self._paper_cohort_storage_root = (
            Path(paper_cohort_storage_root) if paper_cohort_storage_root is not None else None
        )
        self._startup_context = startup_context
        self._validate_startup_context()
        topology_block = paper_runtime_database_topology_block_reason(self._db_path)
        if topology_block is not None:
            raise RuntimeError(f"immutable paper cohort database is blocked: {topology_block}")
        if (
            self._startup_context in {"runtime", "cli"}
            and not self._is_in_memory_db_path()
            and self._paper_cohort_storage_root is None
        ):
            raise RuntimeError(
                "PaperTrader runtime/CLI requires paper_cohort_storage_root"
            )
        paper_cohort_binding = paper_cohort_binding_for_db(
            self._db_path,
            cohort_id=cohort_id,
        )
        requested_starting_bankroll = (
            _resolve_starting_bankroll(starting_bankroll)
            if starting_bankroll is not None
            else None
        )
        if paper_cohort_binding is not None:
            if (
                requested_starting_bankroll is not None
                and requested_starting_bankroll != paper_cohort_binding.cohort.starting_bankroll
            ):
                raise ValueError("paper cohort starting bankroll does not match its manifest")
            self._starting_bankroll = paper_cohort_binding.cohort.starting_bankroll
            self._legacy_runtime_write_block_reason = None
        else:
            immutable_database_block_reason = immutable_paper_database_block_reason(
                self._db_path,
                storage_root=self._paper_cohort_storage_root,
            )
            if immutable_database_block_reason is not None:
                raise RuntimeError(
                    "immutable paper cohort database is blocked: "
                    f"{immutable_database_block_reason}"
                )
            if startup_context in {"runtime", "cli"}:
                unbound_runtime_block_reason = unbound_paper_runtime_database_block_reason(
                    self._db_path,
                    storage_root=self._paper_cohort_storage_root,
                )
                if unbound_runtime_block_reason is not None:
                    raise RuntimeError(
                        "unbound paper runtime database is blocked: "
                        f"{unbound_runtime_block_reason}"
                    )
            self._starting_bankroll = _resolve_starting_bankroll(starting_bankroll)
            self._legacy_runtime_write_block_reason = None
        requested_live_transition_block = (
            str(live_transition_block_reason).strip() or None
            if live_transition_block_reason is not None
            else None
        )
        self._paper_cohort_binding = paper_cohort_binding
        self._live_transition_block_reason = (
            _LEGACY_PENDING_COHORT_LIVE_TRANSITION_BLOCK
            if getattr(paper_cohort_binding, "cohort_type", None) == "legacy_pending"
            else _ACTIVE_COHORT_LIVE_TRANSITION_BLOCK
            if paper_cohort_binding is not None
            else self._legacy_runtime_write_block_reason or requested_live_transition_block
        )
        self._initialized = False
        self._transaction_lock = threading.RLock()
        self._settlement_schema_present = False
        self._paper_accounting_schema_present = False
        self._paper_accounting_handlers = paper_accounting_handlers
        self._calibration_task = calibration_task
        self._enforce_runtime_guards()
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False, timeout=30.0)
        try:
            self._conn.row_factory = sqlite3.Row
            enable_and_verify_foreign_keys(self._conn)
            self.credibility: SourceCredibility
            self.portfolio: Portfolio
            self.initialize()
        except BaseException:
            self._conn.close()
            raise

    def initialize(self) -> None:
        """Explicit bootstrap hook kept idempotent for repeated construction safety.

        The constructor still auto-calls this so existing runtime and CLI call
        sites keep working. That is the safer option here: the app assumes a
        ready-to-use PaperTrader in many places, and `initialize()` exists to
        make repeated construction/tests safe rather than to force a broad API
        change through startup code.
        """
        if self._initialized:
            return
        if self._paper_cohort_binding is not None:
            assert_initialized_paper_cohort_schema(
                self._conn,
                self._paper_cohort_binding,
            )
        fresh_database = self._conn.execute(
            "SELECT 1 FROM sqlite_schema WHERE type='table' AND name='paper_trades'"
        ).fetchone() is None
        with self._conn:
            for _stmt in _DDL.split(";"):
                _stmt = _stmt.strip()
                if _stmt:
                    self._conn.execute(_stmt)
            if fresh_database:
                initialize_fresh_settlement_schema(self._conn)
                initialize_fresh_paper_accounting_schema(
                    self._conn,
                    migration_plan_sha256=PAPER_ACCOUNTING_FRESH_PLAN_SHA256,
                )
            if self._paper_cohort_binding is not None:
                # This update shares the first durable schema transaction. A
                # later crash can resume migrations, but it can never reset a
                # cohort whose initial schema has already committed.
                mark_paper_cohort_database_initialized(
                    self._conn,
                    self._paper_cohort_binding,
                    commit=False,
                )
        self._migrate_db()
        self._paper_accounting_schema_present = (
            paper_accounting_schema_contract_matches(self._conn)
        )
        self._validate_paper_accounting_startup()
        self.credibility = SourceCredibility(self._db_path)
        self.portfolio = Portfolio()
        self.portfolio.load_from_db(self._conn)
        startup_state = self._load_state()
        self._log_startup_diagnostics(startup_state)
        self._log_state_initialization(startup_state)
        self._ensure_p0_cohort_sentinel()
        self._ensure_sizing_regime_kelly_sentinel()
        if self._startup_context == "runtime":
            type(self)._runtime_owner_pid = os.getpid()
        self._initialized = True

    @property
    def db_path(self) -> Path:
        return self._db_path

    @property
    def starting_bankroll(self) -> float:
        return self._starting_bankroll

    @property
    def settlement_schema_present(self) -> bool:
        return self._settlement_schema_present

    @property
    def paper_accounting_schema_present(self) -> bool:
        return self._paper_accounting_schema_present

    def _validate_paper_accounting_startup(self) -> None:
        if not config_module.cfg.enable_fee_net_paper_accounting:
            return
        if not self._paper_accounting_schema_present:
            raise PaperAccountingAdmissionError(
                "fee-net paper accounting requires the exact migrated schema"
            )
        if (
            self._paper_accounting_handlers is None
            or not self._paper_accounting_handlers.supports(PAPER_ACCOUNTING_VERSION)
        ):
            raise PaperAccountingAdmissionError(
                "fee-net paper accounting requires both entry and settlement "
                f"handlers for version {PAPER_ACCOUNTING_VERSION}"
            )

    def _ensure_p0_cohort_sentinel(self) -> None:
        """Idempotent insert of bot_state.p0_price_fix_deployed_ts (P-9 / LD-7).

        Records the timestamp when the post-P0 pricing fix went live. P-8
        replay cohort filter (``filter_post_p0_rows``) uses this value as
        the authoritative ts boundary. Once inserted, the value is NEVER
        updated on subsequent runs — pre-P0 contaminated rows are gated
        by this sentinel forever.
        """
        sentinel_key = "p0_price_fix_deployed_ts"
        existing = self._conn.execute(
            "SELECT value FROM bot_state WHERE key = ?",
            (sentinel_key,),
        ).fetchone()
        if existing is not None:
            return  # already set; never overwrite

        deployed_ts = datetime.now(timezone.utc).isoformat()
        try:
            with self._conn:
                self._conn.execute(
                    "INSERT INTO bot_state(key, value) VALUES (?, ?)",
                    (sentinel_key, deployed_ts),
                )
        except sqlite3.IntegrityError:
            # Race window: another PaperTrader instance inserted between our
            # SELECT and INSERT. The other process won; contract honored —
            # never overwrite the historical boundary (LD-7).
            return
        if self._startup_context != "test":
            log.info(
                "[P-9 / LD-7] Inserted bot_state.p0_price_fix_deployed_ts=%s",
                deployed_ts,
            )

    def _ensure_sizing_regime_kelly_sentinel(self) -> None:
        """Idempotent insert of bot_state.sizing_regime_kelly_deployed_ts.

        Paper sizing switched flat-5 -> Kelly in v0.33.6 (PROFIT-SIZING-001b).
        Resolved trades from the two sizing regimes are not comparable, so the
        performance report needs a boundary to split the flat-5-era cohort from
        the Kelly-era cohort (mirroring the P0 pricing-fix sentinel).

        The bot stamps this automatically at the first startup that runs the
        Kelly-sizing code -- there is NO operator action and NO .env variable
        (an explicit decision: the operator should not have to set deploy
        markers by hand). Like the P0 sentinel, the value is written once and
        NEVER overwritten, so the boundary is anchored permanently. Stamping at
        first-startup-under-this-code means the recorded instant can trail the
        actual v0.33.6 code deploy slightly; this is harmless because the
        Kelly-era resolved cohort is empty at planting time, so no resolved
        trade is mis-attributed across the boundary.
        """
        sentinel_key = "sizing_regime_kelly_deployed_ts"
        existing = self._conn.execute(
            "SELECT value FROM bot_state WHERE key = ?",
            (sentinel_key,),
        ).fetchone()
        if existing is not None:
            return  # already set; never overwrite the historical boundary

        deployed_ts = datetime.now(timezone.utc).isoformat()
        try:
            with self._conn:
                self._conn.execute(
                    "INSERT INTO bot_state(key, value) VALUES (?, ?)",
                    (sentinel_key, deployed_ts),
                )
        except sqlite3.IntegrityError:
            # Race: another PaperTrader inserted between our SELECT and INSERT.
            # The other process won; never overwrite the boundary.
            return
        if self._startup_context != "test":
            log.info(
                "Inserted bot_state.sizing_regime_kelly_deployed_ts=%s",
                deployed_ts,
            )

    def _validate_startup_context(self) -> None:
        allowed = {"runtime", "cli", "test"}
        # P-6: also accept ``p0-*`` test-bench contexts. Tests construct
        # PaperTrader with descriptive labels (e.g. ``p0-paper-yes``) to
        # avoid colliding with the runtime PID guard; treat them as test
        # contexts for diagnostic-logging purposes.
        if self._startup_context in allowed:
            return
        if self._startup_context.startswith("p0-"):
            return
        raise ValueError(f"Unsupported startup_context: {self._startup_context}")

    def _enforce_runtime_guards(self) -> None:
        if self._legacy_runtime_write_block_reason is not None and (
            self._startup_context in {"runtime", "cli"}
        ):
            context = "runtime" if self._startup_context == "runtime" else "CLI"
            raise RuntimeError(
                f"legacy paper {context} is blocked: "
                f"{self._legacy_runtime_write_block_reason}"
            )
        if self._startup_context != "runtime":
            return
        if self._is_in_memory_db_path():
            log.error(
                "[PAPER_INIT_GUARD] runtime_in_memory_db_blocked=true pid=%s db=%s",
                os.getpid(),
                self._db_path_for_logging(),
            )
            raise ValueError("PaperTrader runtime context cannot use an in-memory SQLite database")
        if type(self)._runtime_owner_pid == os.getpid():
            log.error(
                "[PAPER_INIT_GUARD] duplicate_runtime_init_blocked=true pid=%s db=%s",
                os.getpid(),
                self._db_path_for_logging(),
            )
            raise RuntimeError("PaperTrader runtime initialization attempted more than once in the same process")

    # ── Schema migrations ─────────────────────────────────────────────────────

    def _migrate_db(self) -> None:
        """Add columns introduced after initial schema without dropping existing data."""
        cols = self._paper_trades_columns()
        added_cols: list[str] = []
        # F-11 P1-B: carry the canonical executed-entry field name through
        # storage while keeping direct legacy fixture loads survivable.
        if "market_yes_price" in cols and "entry_price_cents" not in cols:
            self._conn.execute(
                "ALTER TABLE paper_trades RENAME COLUMN market_yes_price TO entry_price_cents"
            )
            self._conn.commit()
            cols = self._paper_trades_columns()
            added_cols.append("entry_price_cents")
        elif "market_yes_price" in cols and "entry_price_cents" in cols:
            self._conn.execute(
                """UPDATE paper_trades
                   SET entry_price_cents = market_yes_price
                   WHERE entry_price_cents IS NULL"""
            )
            self._conn.commit()

        if "market_snapshot" not in cols and self._ensure_paper_trades_column("market_snapshot", "TEXT", cols):
            added_cols.append("market_snapshot")

        for name, ddl in _VENUE_COLUMNS:
            if name not in cols and self._ensure_paper_trades_column(name, ddl, cols):
                added_cols.append(name)

        # v0.22.0: feedback-loop columns
        new_cols = [
            ("series_ticker",  "TEXT"),
            ("resolved_ts",    "TEXT"),
            ("signal_type",    "TEXT DEFAULT 'news'"),
            ("match_score",    "REAL"),
            ("llm_direction",  "TEXT"),
            ("llm_magnitude",  "TEXT"),
            ("llm_confidence", "REAL"),
            # v0.23.0: Kelly shadow sizing -- what Kelly would have done in paper mode
            # Lets analytics compare flat-5 vs Kelly trajectory before going live
            ("kelly_contracts", "INTEGER"),
            # v0.29.47 (PROFIT-CAL-001): per-lane estimates captured at trade time.
            # Populated from analysis.signal_meta (propagated via TradeCandidate).
            # Nullable so historical rows do not require backfill; null lanes are
            # skipped at resolve_market emission time.
            ("fast_lane_p",             "REAL"),
            ("fast_lane_confidence",    "REAL"),
            ("accumulation_p",          "REAL"),
            ("accumulation_confidence", "REAL"),
            ("structural_p",            "REAL"),
            ("structural_confidence",   "REAL"),
            # I-10 (framework v3, closes Codex blocker E): cohort_extension
            # holds the contamination-window tag
            # ``contamination_window:<change_id>:<window_id>`` for paper
            # trades written while a T1/T2 observation window is active
            # (see scripts/edge_replay/contamination_corpus_manager.py).
            # Nullable: a NULL value means the trade is part of the normal
            # corpus and is eligible for pre-deploy gates; a non-NULL value
            # means the I-1 corpus builder must exclude the row by default
            # but the I-11 retrospective consumes it as the gold-standard
            # OOS measurement for the named change.
            ("cohort_extension",        "TEXT"),
            # PROFIT-PHASE3 I-1: durable join key linking this trade to its
            # captured LLM decision (llm_capture.jsonl row_id). Lets the corpus
            # builder stamp the 13-field cache key onto the corpus row so the
            # replay gate's cache-coverage check can resolve it. Nullable:
            # historical rows (and non-LLM signals) have no captured decision
            # and join lossily / not at all.
            ("llm_capture_row_id",      "TEXT"),
        ]
        for col, col_type in new_cols:
            if col not in cols and self._ensure_paper_trades_column(col, col_type, cols):
                added_cols.append(col)

        # P-6 / P0-PROV-019: idempotent forward-only column-add for provenance
        # fields. Safe to run on every PaperTrader init.
        for name, ddl in _P0_PROVENANCE_COLUMNS:
            if name not in cols and self._ensure_paper_trades_column(name, ddl, cols):
                added_cols.append(name)

        self._settlement_schema_present = (
            _SETTLEMENT_SCHEMA_SENTINEL_COLUMNS <= cols
        )

        # Backfill series_ticker from market_snapshot JSON for historical rows
        if "series_ticker" in added_cols:
            try:
                cur = self._conn.execute(
                    """UPDATE paper_trades
                       SET series_ticker = json_extract(market_snapshot, '$.series_ticker')
                       WHERE series_ticker IS NULL AND market_snapshot IS NOT NULL"""
                )
                self._conn.commit()
                if cur.rowcount:
                    log.info(
                        "DB backfilled series_ticker for %d historical trades", cur.rowcount
                    )
            except Exception as exc:
                log.warning("DB backfill of series_ticker failed: %s", exc)

        self._repair_executed_side_edges()

        if added_cols:
            log.info("DB migration complete (added: %s)", ", ".join(added_cols))

    def _paper_trades_columns(self) -> set[str]:
        return {row[1] for row in self._conn.execute("PRAGMA table_info(paper_trades)")}

    def _repair_executed_side_edges(self) -> None:
        """Canonicalize stored trade edge to the executed side."""
        try:
            cur = self._conn.execute(
                """UPDATE paper_trades
                   SET edge = CASE lower(side)
                       WHEN 'yes' THEN estimated_prob - (price_cents / 100.0)
                       WHEN 'no' THEN (1.0 - estimated_prob) - (price_cents / 100.0)
                       ELSE edge
                   END
                   WHERE side IS NOT NULL
                     AND edge IS NOT NULL
                     AND estimated_prob IS NOT NULL
                     AND price_cents IS NOT NULL
                     AND lower(side) IN ('yes', 'no')
                     AND abs(edge - CASE lower(side)
                       WHEN 'yes' THEN estimated_prob - (price_cents / 100.0)
                       WHEN 'no' THEN (1.0 - estimated_prob) - (price_cents / 100.0)
                       ELSE edge
                     END) > 0.0000001"""
            )
            self._conn.commit()
            if cur.rowcount:
                log.info("DB repaired executed-side edge for %d paper trades", cur.rowcount)
        except Exception as exc:
            log.warning("DB executed-side edge repair failed: %s", exc)

    @staticmethod
    def _resolve_cohort_extension() -> str | None:
        """Return the active contamination-window tag, or ``None``.

        I-10 (framework v3, closes Codex blocker E). Imports the
        contamination manager lazily so a missing or broken module never
        blocks the trade write. The entire body is wrapped in a single
        ``try/except Exception`` -> ``None`` for the same reason: this
        helper is observational machinery and must never break the
        money-path INSERT. See ``rules/risk_review.md``: observability
        write paths should not silently swallow exceptions, but here we
        explicitly accept the silent-skip tradeoff because the alternative
        (raising) would block paper-trade persistence. The contamination
        machinery is best-effort label propagation, not a safety gate.
        """
        try:
            from scripts.edge_replay.contamination_corpus_manager import (
                cohort_extension_tag,
                get_active_window,
            )

            window = get_active_window()
            if window is None:
                return None
            return cohort_extension_tag(window)
        except Exception:
            # Per python-reviewer MEDIUM: warn on swallow so a corrupt
            # sentinel or import failure is distinguishable from
            # "no active window" in production logs. The paper-trade
            # persistence guarantee is unaffected; only observability
            # improves. Using the module-level `log` keeps the message
            # in the trade-path log stream where operators look first.
            log.warning(
                "[I-10] _resolve_cohort_extension failed; labeling skipped",
                exc_info=True,
            )
            return None

    def _ensure_paper_trades_column(self, col: str, col_type: str, cols: set[str]) -> bool:
        try:
            self._conn.execute(f"ALTER TABLE paper_trades ADD COLUMN {col} {col_type}")
            self._conn.commit()
            cols.add(col)
            return True
        except sqlite3.OperationalError as exc:
            if "duplicate column name" in str(exc).lower():
                cols.update(self._paper_trades_columns())
                return False
            log.warning("DB migration failed for column %s: %s", col, exc)
            return False

    # ── State management ──────────────────────────────────────────────────────

    def _load_state(self) -> _StartupStateSnapshot:
        """Read persisted state; initialise defaults atomically on first run."""
        existing_keys = self._existing_state_keys()
        paper_start_inserted = False
        bankroll_inserted = False

        if "paper_start_time" not in existing_keys:
            paper_start_inserted = self._ensure_state_default(
                "paper_start_time",
                datetime.now(timezone.utc).isoformat(),
            )

        if "notional_bankroll" not in existing_keys:
            bankroll_inserted = self._ensure_state_default(
                "notional_bankroll",
                str(self._starting_bankroll),
            )

        # A persisted flag is insufficient: startup requires both the kill-switch
        # and independent realized-profit evidence before it can leave paper mode.
        row = self._conn.execute(
            "SELECT value FROM bot_state WHERE key = 'go_live_confirmed'"
        ).fetchone()
        if row and row["value"] == "true":
            if self._live_transition_block_reason is not None:
                log.warning(
                    "LIVE TRADING BLOCKED -- go_live_confirmed=true in DB but %s. "
                    "Staying in paper mode.",
                    self._live_transition_block_reason,
                )
                cfg.set_paper_mode(True)
            elif (
                cfg.live_trading_enabled
                and independent_realized_profit_evidence_available(db_path=self._db_path)
            ):
                cfg.set_paper_mode(False)
                log.warning("GO-LIVE confirmed -- bot is in LIVE TRADING mode.")
            else:
                if not cfg.live_trading_enabled:
                    log.warning(
                        "LIVE TRADING BLOCKED -- go_live_confirmed=true in DB but "
                        "LIVE_TRADING_ENABLED is not set in .env. "
                        "Staying in paper mode. Set LIVE_TRADING_ENABLED=true to unlock."
                    )
                else:
                    log.warning(
                        "LIVE TRADING BLOCKED -- go_live_confirmed=true in DB but "
                        "independent realized-profit evidence is unavailable. "
                        "Staying in paper mode."
                    )
                cfg.set_paper_mode(True)
        else:
            cfg.set_paper_mode(True)

        return _StartupStateSnapshot(
            existing_keys=existing_keys,
            paper_start_inserted=paper_start_inserted,
            bankroll_inserted=bankroll_inserted,
        )

    def _existing_state_keys(self) -> set[str]:
        rows = self._conn.execute(
            "SELECT key FROM bot_state WHERE key IN ('paper_start_time', 'notional_bankroll', 'go_live_confirmed')"
        ).fetchall()
        return {str(row["key"]) for row in rows}

    def _ensure_state_default(self, key: str, value: str) -> bool:
        before = self._conn.total_changes
        self._conn.execute(
            """
            INSERT INTO bot_state (key, value)
            SELECT ?, ?
            WHERE NOT EXISTS (
                SELECT 1 FROM bot_state WHERE key = ?
            )
            """,
            (key, value, key),
        )
        self._conn.commit()
        return self._conn.total_changes > before

    def _log_startup_diagnostics(self, startup_state: _StartupStateSnapshot) -> None:
        if self._startup_context == "test" or self._startup_context.startswith("p0-"):
            return
        level = logging.INFO if self._startup_context == "runtime" else logging.DEBUG
        config_path = getattr(config_module, "__file__", "unknown")
        log.log(
            level,
            "[PAPER_INIT] pid=%s ctx=%s cwd=%s db=%s config=%s bankroll=%.2f "
            "paper_start_exists=%s bankroll_exists=%s go_live_exists=%s",
            os.getpid(),
            self._startup_context,
            os.getcwd(),
            self._db_path_for_logging(),
            Path(config_path).resolve() if config_path != "unknown" else "unknown",
            self._starting_bankroll,
            "paper_start_time" in startup_state.existing_keys,
            "notional_bankroll" in startup_state.existing_keys,
            "go_live_confirmed" in startup_state.existing_keys,
        )

    def _log_state_initialization(self, startup_state: _StartupStateSnapshot) -> None:
        if self._startup_context == "test" or self._startup_context.startswith("p0-"):
            return
        if startup_state.paper_start_inserted:
            log.info("Paper trading phase started -- runs until you confirm --go-live.")
        elif "paper_start_time" not in startup_state.existing_keys:
            log.warning("Paper trading phase state row was created concurrently during startup.")

        if startup_state.bankroll_inserted:
            log.info("Notional bankroll initialised at $%.2f", self._starting_bankroll)
        elif "notional_bankroll" not in startup_state.existing_keys:
            log.warning(
                "Notional bankroll state row was created concurrently during startup; existing value preserved at $%.2f",
                self.get_notional_bankroll(),
            )

    def _db_path_for_logging(self) -> str:
        raw = str(self._db_path)
        if self._is_in_memory_db_path():
            return raw
        return str(Path(raw).resolve())

    def _is_in_memory_db_path(self) -> bool:
        raw = str(self._db_path)
        return raw == ":memory:" or (raw.startswith("file:") and "mode=memory" in raw)

    def _set_state(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO bot_state (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self._conn.commit()

    # ── Notional bankroll ─────────────────────────────────────────────────────

    def get_notional_bankroll(self) -> float:
        row = self._conn.execute(
            "SELECT value FROM bot_state WHERE key = 'notional_bankroll'"
        ).fetchone()
        return float(row["value"]) if row else self._starting_bankroll

    def get_effective_sizing_bankroll(self) -> float:
        """Return the bankroll that may safely expand paper or live sizing."""

        return effective_sizing_bankroll(
            self._db_path,
            notional_bankroll=self.get_notional_bankroll(),
            configured_starting_bankroll=self._starting_bankroll,
        )

    def _debit_bankroll(self, amount: float) -> float:
        """Subtract amount from notional bankroll. Returns new balance."""
        current = self.get_notional_bankroll()
        new_val = current - amount
        self._set_state("notional_bankroll", str(round(new_val, 4)))
        return new_val

    def _credit_bankroll(self, amount: float) -> float:
        """Add amount to notional bankroll. Returns new balance."""
        current = self.get_notional_bankroll()
        new_val = current + amount
        self._set_state("notional_bankroll", str(round(new_val, 4)))
        return new_val

    # ── Go-live gate ──────────────────────────────────────────────────────────

    def confirm_go_live(self) -> None:
        """
        Flip the go_live_confirmed flag in the DB and update cfg.
        Called only from main.py --go-live after human confirmation.
        """
        if self._live_transition_block_reason is not None:
            raise RuntimeError(
                "Live trading remains blocked: "
                f"{self._live_transition_block_reason}"
            )
        if not independent_realized_profit_evidence_available(db_path=self._db_path):
            raise RuntimeError(
                "Live trading remains blocked: independent realized-profit evidence "
                "is unavailable"
            )
        self._set_state("go_live_confirmed", "true")
        cfg.set_paper_mode(False)
        log.warning(
            "GO-LIVE CONFIRMED. Bot will now place real orders. "
            "Notional bankroll at time of switch: $%.2f",
            self.get_notional_bankroll(),
        )

    # ── Trade recording ───────────────────────────────────────────────────────

    def record_trade(
        self,
        analysis: SignalAnalysis,
        *,
        entry_request_id: str | None = None,
        execution_terms: FinalExecutionTerms | None = None,
    ) -> str:
        if config_module.cfg.enable_fee_net_paper_accounting:
            self._require_fee_net_entry_contract(analysis, entry_request_id)
            raise PaperAccountingAdmissionError(
                "fee-net paper entry execution is not installed"
            )
        with self._transaction_lock:
            return self._record_trade_locked(
                analysis,
                execution_terms=execution_terms,
            )

    def _require_fee_net_entry_contract(
        self,
        analysis: SignalAnalysis,
        entry_request_id: str | None,
    ) -> None:
        venue_value = (
            getattr(analysis, "venue", None)
            or getattr(analysis.market, "venue", None)
            or Venue.KALSHI.value
        )
        try:
            venue = normalize_venue(venue_value)
            schedule_id = fee_schedule_at(
                venue=venue,
                timestamp=datetime.now(timezone.utc),
            )
        except (ValueError, FeeUnscorableError) as exc:
            raise PaperAccountingAdmissionError(
                "fee-net paper entry requires a pinned effective fee schedule"
            ) from exc
        require_paper_accounting_admission(
            self._conn,
            self._paper_accounting_handlers,
            entry_request_id,
            schedule_id,
        )

    def _record_trade_locked(
        self,
        analysis: SignalAnalysis,
        *,
        execution_terms: FinalExecutionTerms | None = None,
    ) -> str:
        # P-6 / LD-2 / CR-C: fail-closed when price_available=False. Past the
        # executor gate so an unavailable market here means an upstream
        # contract violation; persist nothing and emit a structured warning
        # so the test bench + operators can see it. We gate on
        # ``price_available`` rather than full ``is_tradeable()`` so a
        # tradeable-but-borderline-invariant market still records (the
        # invariant check is the normalizer's job, not paper_trader's).
        price_available = bool(getattr(analysis.market, "price_available", True))
        if not price_available:
            log.warning(
                "[PAPER] Skipping record_trade for %s: market not tradeable "
                "(price_available=%s, skip_reason=price_unavailable). "
                "LD-2/CR-C guard.",
                getattr(analysis.market, "ticker", "<unknown>"),
                getattr(analysis.market, "price_available", None),
            )
            return ""

        # P-6 / LD-10 / DT-1b: paper fill uses executed_price_cents. Each side
        # already holds its own executable ask; midpoint synthesis
        # (``100 - yes_price``) is removed as a load-bearing pricing path.
        if analysis.executed_price_cents is None:
            log.warning(
                "[PAPER] Skipping record_trade for %s: executed_price_cents is "
                "None (price_available=%s, skip_reason=price_unavailable). "
                "LD-10 contract violation by upstream.",
                getattr(analysis.market, "ticker", "<unknown>"),
                getattr(analysis.market, "price_available", None),
            )
            return ""

        def _venue_string(value: Any) -> str | None:
            if isinstance(value, str):
                stripped = value.strip()
                if stripped:
                    return stripped
            return None

        venue = (
            _venue_string(getattr(analysis, "venue", None))
            or _venue_string(getattr(analysis.market, "venue", None))
            or Venue.KALSHI.value
        )
        settlement_meta_present = self._conn.execute(
            """
            SELECT 1 FROM sqlite_schema
            WHERE type='table' AND name='paper_settlement_schema_meta'
            """
        ).fetchone() is not None
        canonical_identity = settlement_meta_present
        venue_market_id: str | None = None
        if canonical_identity:
            if not canonical_entry_schema_ready(self._conn):
                log.error(
                    "[PAPER] Skipping record_trade for %s: canonical entry "
                    "schema is incomplete",
                    getattr(analysis.market, "ticker", "<unknown>"),
                )
                return ""
            try:
                normalized_venue = normalize_venue(venue)
            except ValueError:
                log.error(
                    "[PAPER] Skipping record_trade for %s: unsupported venue %r",
                    getattr(analysis.market, "ticker", "<unknown>"),
                    venue,
                )
                return ""
            venue = normalized_venue.value
            if normalized_venue is Venue.KALSHI:
                venue_market_id = str(
                    getattr(analysis.market, "ticker", "") or ""
                ).strip()
                if (
                    not is_safe_kalshi_identifier(venue_market_id)
                    or not venue_market_id.startswith("KX")
                    or len(venue_market_id) <= 2
                ):
                    venue_market_id = ""
            else:
                raw_market_id = getattr(analysis.market, "venue_market_id", None)
                venue_market_id = str(raw_market_id or "").strip()
                if not venue_market_id.isascii() or not venue_market_id.isdigit():
                    venue_market_id = ""
            if not venue_market_id:
                log.error(
                    "[PAPER] Skipping record_trade for %s: missing canonical "
                    "market identity for venue=%s",
                    getattr(analysis.market, "ticker", "<unknown>"),
                    venue,
                )
                return ""

        capped_dollars = float(analysis.capped_dollars)
        if capped_dollars <= 0:
            log.warning(
                "[PAPER] Skipping record_trade for %s: capped dollars %.2f "
                "must be positive",
                getattr(analysis.market, "ticker", "<unknown>"),
                capped_dollars,
            )
            return ""

        trade_id    = str(uuid.uuid4())[:12]
        try:
            derived_terms = final_execution_terms(
                capped_dollars=capped_dollars,
                executed_price_cents=analysis.executed_price_cents,
            )
        except ValueError:
            log.error(
                "[PAPER] Skipping record_trade for %s: invalid final execution terms",
                getattr(analysis.market, "ticker", "<unknown>"),
            )
            return ""
        if execution_terms is not None and execution_terms != derived_terms:
            log.error(
                "[PAPER] Skipping record_trade for %s: final execution terms do not match analysis",
                getattr(analysis.market, "ticker", "<unknown>"),
            )
            return ""
        terms = execution_terms or derived_terms
        price_cents = terms.price_cents
        # kelly_contracts: what Kelly sizes for this trade. Retained as a stored
        # column for analytics (and the §7e flat-vs-Kelly history pre-cutover).
        kelly_contracts = derived_terms.contracts
        # PROFIT-SIZING-001b: paper now MIRRORS live -- size by Kelly (not flat-5)
        # so the paper cohort predicts live behaviour. With the min-bet floor
        # removed (PROFIT-SIZING-001) trades size down to the natural 1-contract
        # floor in contracts_from_dollars. Both modes now use kelly_contracts.
        contracts = terms.contracts
        cost_dollars = contracts * price_cents / 100.0
        if cost_dollars > capped_dollars:
            log.warning(
                "[PAPER] Skipping record_trade for %s: contract cost $%.2f "
                "exceeds capped dollars $%.2f",
                getattr(analysis.market, "ticker", "<unknown>"),
                cost_dollars,
                capped_dollars,
            )
            return ""

        bankroll_before = self.get_notional_bankroll()
        sizing_bankroll = self.get_effective_sizing_bankroll()
        if cost_dollars > sizing_bankroll:
            log.warning(
                "[PAPER] Skipping record_trade for %s: contract cost $%.2f "
                "exceeds effective sizing bankroll $%.2f",
                getattr(analysis.market, "ticker", "<unknown>"),
                cost_dollars,
                sizing_bankroll,
            )
            return ""
        bankroll_after = bankroll_before - cost_dollars
        if not canonical_identity:
            bankroll_after = self._debit_bankroll(cost_dollars)

        source_mult = self.credibility.get_multiplier(analysis.news_item.source)
        # P-6 / CR-E: custom encoder routes around the LD-2/CR-C legacy guard
        # and serializes Decimal / datetime fields safely.
        market_snapshot = json.dumps(
            _market_to_jsonable(analysis.market), ensure_ascii=False
        )

        # P-6 / P0-PROV-019: pull provenance from the market and persist alongside
        # the executed-side entry so analytics can attribute every row to its
        # source surface (rest_list/rest_get/ws_book/...) and method.
        _market = analysis.market
        provenance_source = getattr(_market, "price_source", None) or "unavailable"
        provenance_method = getattr(_market, "price_method", None) or "none"
        provenance_retrieved = getattr(_market, "price_retrieved_at", None)
        if isinstance(provenance_retrieved, datetime):
            provenance_retrieved_str: str | None = provenance_retrieved.isoformat()
        elif isinstance(provenance_retrieved, str):
            provenance_retrieved_str = provenance_retrieved
        else:
            provenance_retrieved_str = None
        provenance_hash = getattr(_market, "raw_payload_hash", None)

        # PROFIT-VENUE-PARITY V12: the 'kalshi' default is correct for legacy
        # pre-venue rows and native Kalshi objects (which carry no venue at all).
        # But a NON-KX ticker landing on the default means a venue-stamped
        # (e.g. Polymarket) object lost its venue upstream and would be misrouted
        # to the Kalshi REST resolution path. Surface it instead of silently
        # misfiling the trade. Observability-only; does not change the recorded
        # venue or any decision.
        if venue == "kalshi":
            _ticker = str(getattr(analysis.market, "ticker", "") or "")
            if _ticker and not _ticker.upper().startswith("KX"):
                log.warning(
                    "[VENUE_DEFAULT] non-KX ticker %s defaulted to venue='kalshi' "
                    "(venue stamp missing upstream?) -- may misroute resolution",
                    _ticker,
                )

        side = str(analysis.side).lower()
        # Persist the executable edge for the side actually traded. Production
        # analysis.edge is already chosen-side, while older replay/test paths may
        # still carry YES-side edge, so derive from probability + entry price.
        entry_prob = (
            analysis.estimated_probability
            if side == "yes"
            else 1.0 - analysis.estimated_probability
        )
        executed_edge = entry_prob - (price_cents / 100.0)

        # PROFIT-CAL-001: per-lane estimates arrive via signal_meta, populated by
        # BlendTask when the candidate is built. Fast-lane-only paths leave these
        # as None; resolve_market skips CALIBRATION_CHECK emission for null lanes.
        # Guard against non-dict signal_meta (test mocks, older adapters).
        signal_meta = getattr(analysis, "signal_meta", None)
        if not isinstance(signal_meta, dict):
            signal_meta = {}

        def _lane_float(key: str) -> float | None:
            value = signal_meta.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return float(value)
            return None

        lane_fast_p      = _lane_float("fast_lane_p")
        lane_fast_conf   = _lane_float("fast_lane_confidence")
        lane_acc_p       = _lane_float("accumulation_p")
        lane_acc_conf    = _lane_float("accumulation_confidence")
        lane_struct_p    = _lane_float("structural_p")
        lane_struct_conf = _lane_float("structural_confidence")

        # I-10 (framework v3, closes Codex blocker E): stamp the contamination
        # tag on every paper trade taken under an active observation window.
        # Additive label only — does not change any decision logic. Fail-soft:
        # the helper swallows every exception and returns None so trade
        # persistence is never blocked by sentinel-file machinery.
        cohort_extension = self._resolve_cohort_extension()

        # PROFIT-PHASE3 I-1: persist the capture join key so a resolved trade can
        # be linked to its captured LLM decision (the corpus builder keys on
        # this). Built via the SAME shared helper the signal-analyzer capture
        # site uses, so the keys match exactly. Lazy import + None fallback keeps
        # trade persistence fail-soft (a missing helper loses only the corpus
        # join for these rows, never the trade). Non-LLM signals have an empty
        # item_id -> a degenerate key with no matching capture (joins to nothing,
        # which is correct).
        try:
            from scripts.edge_replay.llm_capture import signal_capture_row_id

            llm_capture_row_id = signal_capture_row_id(
                analysis.market.ticker,
                getattr(analysis.news_item, "item_id", "") or "",
            )
        except Exception:  # noqa: BLE001 — additive join key, never block a trade
            llm_capture_row_id = None

        trade_ts = datetime.now(timezone.utc).isoformat()
        remaining_values = (
            analysis.market.title,
            side,
            contracts,
            price_cents,
            cost_dollars,
            analysis.estimated_probability,
            float(analysis.executed_price_cents),
            executed_edge,
            analysis.kelly_dollars,
            analysis.capped_dollars,
            analysis.news_item.headline,
            analysis.news_item.source,
            json.dumps(analysis.keywords_matched),
            analysis.reasoning,
            source_mult,
            bankroll_before,
            bankroll_after,
            market_snapshot,
            analysis.market.series_ticker,
            getattr(analysis, "signal_type", "news"),
            getattr(analysis, "match_score", None),
            getattr(analysis, "llm_direction", None),
            getattr(analysis, "llm_magnitude", None),
            getattr(analysis, "llm_confidence", None),
            kelly_contracts,
            lane_fast_p,
            lane_fast_conf,
            lane_acc_p,
            lane_acc_conf,
            lane_struct_p,
            lane_struct_conf,
            provenance_source,
            provenance_method,
            provenance_retrieved_str,
            provenance_hash,
            1,
            cohort_extension,
            llm_capture_row_id,
        )
        if canonical_identity:
            columns = """
                (trade_id, ts, ticker, venue, venue_market_id, identity_status,
                 market_title, side, contracts, price_cents, cost_dollars,
                 estimated_prob, entry_price_cents, edge, kelly_dollars,
                 capped_dollars, signal_headline, signal_source, keywords_matched,
                 reasoning, source_multiplier, notional_bankroll_before,
                 notional_bankroll_after, market_snapshot, series_ticker,
                 signal_type, match_score, llm_direction, llm_magnitude,
                 llm_confidence, kelly_contracts, fast_lane_p,
                 fast_lane_confidence, accumulation_p, accumulation_confidence,
                 structural_p, structural_confidence, price_source, price_method,
                 price_retrieved_at, raw_payload_hash, p0_contract_version,
                  cohort_extension, llm_capture_row_id)
            """
            transaction_started = False
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                transaction_started = True
                settled_identity = self._conn.execute(
                    """
                    SELECT 1
                    FROM paper_settlement_observations
                    WHERE venue=? AND venue_market_id=?
                    LIMIT 1
                    """,
                    (venue, venue_market_id),
                ).fetchone()
                if settled_identity is not None:
                    raise RuntimeError(
                        "canonical market already has a durable settlement "
                        f"observation: {venue}:{venue_market_id}"
                    )
                bankroll_row = self._conn.execute(
                    "SELECT value FROM bot_state WHERE key='notional_bankroll'"
                ).fetchone()
                if bankroll_row is None:
                    raise RuntimeError("notional_bankroll state is missing")
                bankroll_before = float(bankroll_row[0])
                if cost_dollars > bankroll_before:
                    self._conn.rollback()
                    transaction_started = False
                    log.warning(
                        "[PAPER] Skipping canonical record_trade for %s: contract "
                        "cost $%.2f exceeds current notional bankroll $%.2f",
                        getattr(analysis.market, "ticker", "<unknown>"),
                        cost_dollars,
                        bankroll_before,
                    )
                    return ""
                bankroll_after = bankroll_before - cost_dollars
                canonical_remaining_values = (
                    *remaining_values[:15],
                    bankroll_before,
                    bankroll_after,
                    *remaining_values[17:],
                )
                insert_values = (
                    trade_id,
                    trade_ts,
                    analysis.market.ticker,
                    venue,
                    venue_market_id,
                    "mapped",
                    *canonical_remaining_values,
                )
                placeholders = ",".join("?" for _value in insert_values)
                self._conn.execute(
                    f"INSERT INTO paper_trades {columns} VALUES ({placeholders})",
                    insert_values,
                )
                bankroll_cursor = self._conn.execute(
                    "UPDATE bot_state SET value=? WHERE key='notional_bankroll'",
                    (str(round(bankroll_after, 4)),),
                )
                if bankroll_cursor.rowcount != 1:
                    raise RuntimeError("notional_bankroll update did not affect one row")
                self._conn.commit()
            except Exception:  # noqa: BLE001 - canonical entry must fail closed
                if transaction_started:
                    self._conn.rollback()
                log.exception(
                    "[PAPER] Canonical trade entry failed for %s",
                    getattr(analysis.market, "ticker", "<unknown>"),
                )
                return ""
        else:
            columns = """
                (trade_id, ts, ticker, venue, market_title, side, contracts,
                 price_cents, cost_dollars, estimated_prob, entry_price_cents,
                 edge, kelly_dollars, capped_dollars, signal_headline,
                 signal_source, keywords_matched, reasoning, source_multiplier,
                 notional_bankroll_before, notional_bankroll_after,
                 market_snapshot, series_ticker, signal_type, match_score,
                 llm_direction, llm_magnitude, llm_confidence, kelly_contracts,
                 fast_lane_p, fast_lane_confidence, accumulation_p,
                 accumulation_confidence, structural_p, structural_confidence,
                 price_source, price_method, price_retrieved_at, raw_payload_hash,
                 p0_contract_version, cohort_extension, llm_capture_row_id)
            """
            insert_values = (
                trade_id,
                trade_ts,
                analysis.market.ticker,
                venue,
                *remaining_values,
            )
            placeholders = ",".join("?" for _value in insert_values)
            self._conn.execute(
                f"INSERT INTO paper_trades {columns} VALUES ({placeholders})",
                insert_values,
            )
            self._conn.commit()

        # Keep portfolio in sync with DB
        self.portfolio.add(Position(
            trade_id=trade_id,
            ticker=analysis.market.ticker,
            venue=venue,
            venue_market_id=venue_market_id,
            side=side,
            contracts=contracts,
            cost_dollars=cost_dollars,
            price_cents=price_cents,
            estimated_prob=analysis.estimated_probability,
            entry_price_cents=float(analysis.executed_price_cents) if analysis.executed_price_cents is not None else 0.0,
            ts=trade_ts,
            price_source=provenance_source,
            price_method=provenance_method,
        ))

        paper_trade_kwargs = {
            "trade_id": trade_id,
            "ticker": analysis.market.ticker,
            "venue": venue,
            "market_title": analysis.market.title,
            "side": side,
            "contracts": contracts,
            "price_cents": price_cents,
            "cost_dollars": cost_dollars,
            "estimated_probability": analysis.estimated_probability,
            "entry_price_cents": float(analysis.executed_price_cents) if analysis.executed_price_cents is not None else 0.0,
            "edge": executed_edge,
            "kelly_dollars": analysis.kelly_dollars,
            "reasoning": analysis.reasoning,
            "signal_headline": analysis.news_item.headline,
            "signal_source": analysis.news_item.source,
            "keywords_matched": analysis.keywords_matched,
            "bankroll_delta_dollars": bankroll_after - bankroll_before,
        }
        signal_meta = vars(analysis).get("signal_meta")
        if signal_meta:
            paper_trade_kwargs["signal_meta"] = signal_meta
        trade_log.log_paper_trade(**paper_trade_kwargs)

        kelly_note = f" (kelly_shadow={kelly_contracts})" if cfg.is_paper_trading else ""
        log.info(
            "[PAPER] %s: BUY %d%s %s @ %dc | cost=$%.2f | edge=%+.3f | "
            "bankroll=$%.2f->$%.2f | src_mult=%.2fx | %s",
            trade_id, contracts, kelly_note, side.upper(), price_cents,
            cost_dollars, executed_edge,
            bankroll_before, bankroll_after,
            source_mult, analysis.market.ticker,
        )
        log.info("        Reasoning: %s", analysis.reasoning[:120])

        return trade_id

    def resolve_observation(self, observation: SettlementObservation) -> bool:
        """Apply one canonical observation without wiring runtime callers."""
        with self._transaction_lock:
            return self._resolve_observation_locked(observation)

    def _resolve_observation_locked(
        self,
        observation: SettlementObservation,
    ) -> bool:
        if not isinstance(observation, SettlementObservation):
            raise TypeError("observation must be a SettlementObservation")
        if not settlement_schema_contract_matches(self._conn):
            raise RuntimeError("settlement schema does not match the durable contract")

        open_row_set_sha256 = _settlement_sha256([])
        transaction_started = False
        observation_already_applied = False
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            transaction_started = True
            candidate_rows = self._canonical_candidate_rows(observation)
            open_row_set_sha256 = self._canonical_open_row_set_sha256(candidate_rows)

            prior = self._canonical_prior_observation(observation)
            try:
                validate_observation_transition(prior, observation)
            except SettlementDriftError as exc:
                raise _SettlementQuarantineRequired(
                    "invalid_supersession",
                    {
                        "error": str(exc),
                        "supersedes_observation_sha256": (
                            observation.supersedes_observation_sha256
                        ),
                    },
                ) from exc

            duplicate = self._conn.execute(
                """
                SELECT 1 FROM paper_settlement_observations
                WHERE observation_sha256=?
                """,
                (observation.observation_sha256,),
            ).fetchone()
            if duplicate is not None:
                raise _SettlementObservationAlreadyApplied

            if prior is not None:
                raise _SettlementQuarantineRequired(
                    "terminal_correction",
                    {
                        "prior_observation_sha256": prior.observation_sha256,
                        "supersedes_observation_sha256": (
                            observation.supersedes_observation_sha256
                        ),
                    },
                )

            legacy_rows = [
                row
                for row in candidate_rows
                if row["identity_status"] != "mapped"
                or row["venue_market_id"] is None
                or not str(row["venue_market_id"]).strip()
            ]
            if legacy_rows:
                raise _SettlementQuarantineRequired(
                    "legacy_null_identity",
                    {"trade_ids": [str(row["trade_id"]) for row in legacy_rows]},
                )

            trades = [
                row
                for row in candidate_rows
                if row["identity_status"] == "mapped"
                and row["venue_market_id"]
                == observation.market_ref.venue_market_id
            ]
            if not trades:
                self._conn.rollback()
                return False

            outcomes, total_payout_cents = self._canonical_trade_outcomes(
                trades,
                observation,
            )
            bankroll_row = self._conn.execute(
                "SELECT value FROM bot_state WHERE key='notional_bankroll'"
            ).fetchone()
            if bankroll_row is None:
                raise _SettlementQuarantineRequired(
                    "invalid_financial_state",
                    {"field": "notional_bankroll", "value": None},
                )
            bankroll_before_cents = (
                _settlement_decimal(bankroll_row[0], "notional_bankroll")
                * Decimal("100")
            )
            bankroll_after_cents = bankroll_before_cents + total_payout_cents
            applied_at = datetime.now(timezone.utc).isoformat()

            self._insert_canonical_observation(
                observation,
                applied_trade_count=len(outcomes),
                bankroll_before_cents=bankroll_before_cents,
                total_payout_cents=total_payout_cents,
                bankroll_after_cents=bankroll_after_cents,
                applied_at=applied_at,
            )
            for outcome in outcomes:
                cursor = self._conn.execute(
                    """
                    UPDATE paper_trades
                    SET resolved=1, resolved_yes=?, pnl_dollars=?, resolved_ts=?,
                        terminal_state=?, settlement_observation_sha256=?,
                        settled_at=?, gross_payout_cents=?, gross_pnl_cents=?
                    WHERE trade_id=? AND venue=? AND venue_market_id=?
                      AND identity_status='mapped' AND resolved=0
                    """,
                    (
                        outcome["resolved_yes"],
                        float(outcome["gross_pnl_cents"] / Decimal("100")),
                        applied_at,
                        outcome["terminal_state"],
                        observation.observation_sha256,
                        applied_at,
                        _settlement_decimal_text(outcome["gross_payout_cents"]),
                        _settlement_decimal_text(outcome["gross_pnl_cents"]),
                        outcome["trade_id"],
                        observation.market_ref.venue.value,
                        observation.market_ref.venue_market_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise _SettlementQuarantineRequired(
                        "trade_row_count_drift",
                        {
                            "trade_id": outcome["trade_id"],
                            "updated_rows": cursor.rowcount,
                        },
                    )

            bankroll_cursor = self._conn.execute(
                """
                UPDATE bot_state
                SET value=?
                WHERE key='notional_bankroll'
                """,
                (
                    _settlement_decimal_text(
                        bankroll_after_cents / Decimal("100")
                    ),
                ),
            )
            if bankroll_cursor.rowcount != 1:
                raise _SettlementQuarantineRequired(
                    "invalid_financial_state",
                    {
                        "field": "notional_bankroll",
                        "updated_rows": bankroll_cursor.rowcount,
                    },
                )

            for outcome in outcomes:
                self._insert_canonical_outbox(
                    observation,
                    outcome,
                    created_at=applied_at,
                )
            self._conn.commit()
        except _SettlementObservationAlreadyApplied:
            if transaction_started:
                self._conn.rollback()
            observation_already_applied = True
        except _SettlementQuarantineRequired as exc:
            if transaction_started:
                self._conn.rollback()
            self._append_settlement_quarantine(
                observation,
                reason_code=exc.reason_code,
                details=exc.details,
                open_row_set_sha256=open_row_set_sha256,
            )
            return False
        except Exception as exc:  # noqa: BLE001 - failures must quarantine after rollback
            if transaction_started:
                self._conn.rollback()
            self._append_settlement_quarantine(
                observation,
                reason_code="settlement_transaction_failure",
                details={"error": type(exc).__name__, "message": str(exc)},
                open_row_set_sha256=open_row_set_sha256,
            )
            return False

        self._synchronize_portfolio_after_canonical_settlement(
            observation.market_ref
        )
        return not observation_already_applied

    def _synchronize_portfolio_after_canonical_settlement(
        self,
        market_ref: MarketRef,
    ) -> None:
        try:
            self.portfolio.resolve(market_ref)
        except Exception as exc:  # noqa: BLE001 - committed DB state is authoritative
            log.warning(
                "Canonical settlement committed for %s:%s but portfolio closure "
                "failed; rebuilding from committed open rows: %s",
                market_ref.venue.value,
                market_ref.venue_market_id,
                exc,
                exc_info=True,
            )
            replacement = Portfolio()
            try:
                replacement.load_from_db(self._conn)
            except Exception as rebuild_exc:
                raise SettlementDriftError(
                    "canonical settlement committed but portfolio recovery failed "
                    f"for {market_ref.venue.value}:{market_ref.venue_market_id}"
                ) from rebuild_exc
            self.portfolio = replacement
            log.warning(
                "Canonical settlement portfolio recovery completed for %s:%s",
                market_ref.venue.value,
                market_ref.venue_market_id,
            )

    def _canonical_candidate_rows(
        self,
        observation: SettlementObservation,
    ) -> list[sqlite3.Row]:
        return self._conn.execute(
            """
            SELECT *
            FROM paper_trades
            WHERE resolved=0 AND venue=?
              AND (venue_market_id=? OR ticker=?)
            ORDER BY trade_id
            """,
            (
                observation.market_ref.venue.value,
                observation.market_ref.venue_market_id,
                observation.market_ref.alias,
            ),
        ).fetchall()

    def _canonical_open_row_set_sha256(
        self,
        rows: Iterable[sqlite3.Row],
    ) -> str:
        fields = (
            "trade_id",
            "ticker",
            "venue",
            "venue_market_id",
            "identity_status",
            "side",
            "contracts",
            "price_cents",
            "cost_dollars",
        )
        payload = [
            {key: row[key] for key in fields}
            for row in rows
        ]
        return _settlement_sha256(payload)

    def _canonical_prior_observation(
        self,
        observation: SettlementObservation,
    ) -> SettlementObservation | None:
        row = self._conn.execute(
            """
            SELECT *
            FROM paper_settlement_observations
            WHERE venue=? AND venue_market_id=?
            ORDER BY applied_at DESC, observation_sha256 DESC
            LIMIT 1
            """,
            (
                observation.market_ref.venue.value,
                observation.market_ref.venue_market_id,
            ),
        ).fetchone()
        if row is None:
            return None

        refund = None
        if row["refund_cents_per_contract"] is not None:
            refund = VoidRefundContract(
                refund_cents_per_contract=Decimal(
                    str(row["refund_cents_per_contract"])
                ),
                refunds_entry_fee=bool(row["refunds_entry_fee"]),
            )
        return SettlementObservation(
            market_ref=MarketRef(
                normalize_venue(str(row["venue"])),
                str(row["venue_market_id"]),
                str(row["alias"]),
            ),
            outcome=MarketOutcome(str(row["outcome"])),
            authoritative_outcome_json=str(row["authoritative_outcome_json"]),
            canonical_payload_json=str(row["canonical_payload_json"]),
            observed_at=datetime.fromisoformat(str(row["observed_at"])),
            effective_at=datetime.fromisoformat(str(row["effective_at"])),
            rules_version=str(row["rules_version"]),
            source_id=str(row["source_id"]),
            payload_sha256=str(row["payload_sha256"]),
            observation_sha256=str(row["observation_sha256"]),
            void_refund=refund,
            supersedes_observation_sha256=(
                str(row["supersedes_observation_sha256"])
                if row["supersedes_observation_sha256"] is not None
                else None
            ),
        )

    def _canonical_trade_outcomes(
        self,
        trades: Iterable[sqlite3.Row],
        observation: SettlementObservation,
    ) -> tuple[list[dict[str, Any]], Decimal]:
        outcomes: list[dict[str, Any]] = []
        total_payout_cents = Decimal("0")
        for trade in trades:
            contracts_decimal = _settlement_decimal(
                trade["contracts"],
                f"{trade['trade_id']}.contracts",
            )
            if (
                contracts_decimal != contracts_decimal.to_integral_value()
                or contracts_decimal <= 0
            ):
                raise _SettlementQuarantineRequired(
                    "invalid_financial_state",
                    {
                        "field": f"{trade['trade_id']}.contracts",
                        "value": str(contracts_decimal),
                    },
                )
            contracts = int(contracts_decimal)
            price_cents = _settlement_decimal(
                trade["price_cents"],
                f"{trade['trade_id']}.price_cents",
            )
            if (
                price_cents != price_cents.to_integral_value()
                or price_cents < 1
                or price_cents > 99
            ):
                raise _SettlementQuarantineRequired(
                    "invalid_financial_state",
                    {
                        "field": f"{trade['trade_id']}.price_cents",
                        "value": str(price_cents),
                    },
                )
            cost_cents = (
                _settlement_decimal(
                    trade["cost_dollars"],
                    f"{trade['trade_id']}.cost_dollars",
                )
                * Decimal("100")
            )
            expected_cost_cents = Decimal(contracts) * price_cents
            if cost_cents != expected_cost_cents:
                raise _SettlementQuarantineRequired(
                    "invalid_financial_state",
                    {
                        "field": f"{trade['trade_id']}.cost_dollars",
                        "expected_cents": _settlement_decimal_text(
                            expected_cost_cents
                        ),
                        "value_cents": _settlement_decimal_text(cost_cents),
                    },
                )

            side = str(trade["side"]).strip().lower()
            if side not in {"yes", "no"}:
                raise _SettlementQuarantineRequired(
                    "invalid_financial_state",
                    {"field": f"{trade['trade_id']}.side", "value": side},
                )
            if observation.outcome is MarketOutcome.VOID:
                if observation.void_refund is None:
                    raise _SettlementQuarantineRequired(
                        "unsupported_void",
                        {"observation_sha256": observation.observation_sha256},
                    )
                won: bool | None = None
                resolved_yes: int | None = None
                terminal_state = "void"
                gross_payout_cents = (
                    Decimal(contracts)
                    * observation.void_refund.refund_cents_per_contract
                )
            else:
                resolved_yes = int(observation.outcome is MarketOutcome.YES)
                won = (side == "yes") == bool(resolved_yes)
                terminal_state = "won" if won else "lost"
                gross_payout_cents = (
                    Decimal(contracts) * Decimal("100")
                    if won
                    else Decimal("0")
                )
            gross_pnl_cents = gross_payout_cents - cost_cents
            total_payout_cents += gross_payout_cents
            outcomes.append(
                {
                    "trade_id": str(trade["trade_id"]),
                    "ticker": trade["ticker"],
                    "side": side,
                    "resolved_yes": resolved_yes,
                    "won": won,
                    "terminal_state": terminal_state,
                    "gross_payout_cents": gross_payout_cents,
                    "gross_pnl_cents": gross_pnl_cents,
                    "entry_ts": trade["ts"],
                    "estimated_prob": trade["estimated_prob"],
                    "entry_price_cents": trade["entry_price_cents"],
                    "cost_dollars": trade["cost_dollars"],
                    "signal_source": trade["signal_source"],
                    "series_ticker": trade["series_ticker"],
                    "llm_magnitude": trade["llm_magnitude"],
                    "llm_confidence": trade["llm_confidence"],
                    "keywords_matched": trade["keywords_matched"],
                    "fast_lane_p": trade["fast_lane_p"],
                    "accumulation_p": trade["accumulation_p"],
                    "structural_p": trade["structural_p"],
                }
            )
        return outcomes, total_payout_cents

    def _insert_canonical_observation(
        self,
        observation: SettlementObservation,
        *,
        applied_trade_count: int,
        bankroll_before_cents: Decimal,
        total_payout_cents: Decimal,
        bankroll_after_cents: Decimal,
        applied_at: str,
    ) -> None:
        void_refund = observation.void_refund
        self._conn.execute(
            """
            INSERT INTO paper_settlement_observations (
                observation_sha256, venue, venue_market_id, alias, outcome,
                authoritative_outcome_json, canonical_payload_json,
                payload_sha256, observed_at, effective_at, rules_version,
                source_id, refund_cents_per_contract, refunds_entry_fee,
                supersedes_observation_sha256, applied_trade_count,
                bankroll_before_cents, gross_payout_cents,
                bankroll_after_cents, applied_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                observation.observation_sha256,
                observation.market_ref.venue.value,
                observation.market_ref.venue_market_id,
                observation.market_ref.alias,
                observation.outcome.value,
                observation.authoritative_outcome_json,
                observation.canonical_payload_json,
                observation.payload_sha256,
                observation.observed_at.isoformat(),
                observation.effective_at.isoformat(),
                observation.rules_version,
                observation.source_id,
                (
                    _settlement_decimal_text(
                        void_refund.refund_cents_per_contract
                    )
                    if void_refund is not None
                    else None
                ),
                int(void_refund.refunds_entry_fee) if void_refund is not None else None,
                observation.supersedes_observation_sha256,
                applied_trade_count,
                _settlement_decimal_text(bankroll_before_cents),
                _settlement_decimal_text(total_payout_cents),
                _settlement_decimal_text(bankroll_after_cents),
                applied_at,
            ),
        )

    def _insert_canonical_outbox(
        self,
        observation: SettlementObservation,
        outcome: dict[str, Any],
        *,
        created_at: str,
    ) -> None:
        contract = paper_trade_settled_outbox_contract(
            observation,
            outcome,
            created_at=created_at,
            keyword_directions=settlement_keyword_directions(),
        )
        self._conn.execute(
            """
            INSERT INTO paper_settlement_outbox (
                outbox_id, event_version, event_kind, observation_sha256,
                trade_id, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                contract.outbox_id,
                contract.event_version,
                contract.event_kind,
                contract.observation_sha256,
                contract.trade_id,
                contract.payload_json,
                contract.created_at,
            ),
        )
        for consumer_name in contract.requirements:
            self._conn.execute(
                """
                INSERT INTO paper_settlement_outbox_requirements (
                    outbox_id, consumer_name
                ) VALUES (?, ?)
                """,
                (contract.outbox_id, consumer_name),
            )

    def _append_settlement_quarantine(
        self,
        observation: SettlementObservation,
        *,
        reason_code: str,
        details: dict[str, object],
        open_row_set_sha256: str,
    ) -> None:
        quarantine_id = _settlement_sha256(
            {
                "observation_sha256": observation.observation_sha256,
                "open_row_set_sha256": open_row_set_sha256,
                "reason_code": reason_code,
            }
        )
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            existing = self._conn.execute(
                """
                SELECT 1 FROM paper_settlement_quarantine
                WHERE observation_sha256=? AND reason_code=?
                  AND open_row_set_sha256=?
                """,
                (
                    observation.observation_sha256,
                    reason_code,
                    open_row_set_sha256,
                ),
            ).fetchone()
            if existing is None:
                self._conn.execute(
                    """
                    INSERT INTO paper_settlement_quarantine (
                        quarantine_id, observation_sha256, payload_sha256,
                        venue, venue_market_id, alias, reason_code,
                        details_json, open_row_set_sha256, detected_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        quarantine_id,
                        observation.observation_sha256,
                        observation.payload_sha256,
                        observation.market_ref.venue.value,
                        observation.market_ref.venue_market_id,
                        observation.market_ref.alias,
                        reason_code,
                        _settlement_json(details),
                        open_row_set_sha256,
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    # ── Market resolution ─────────────────────────────────────────────────────

    # PROFIT-CAL-001 (v0.29.47): resolve_market is now async so it can await
    # CalibrationTask.record_calibration_check. The blocking DB work is still
    # synchronous internally; the async shell exists to emit one
    # CALIBRATION_CHECK per populated lane per resolved trade, both to the
    # structured trade log and to the in-process CalibrationTask state machine
    # that BlendTask.get_scaling_factor() consumes.

    async def resolve_market(self, ticker: str, resolved_yes: bool) -> None:
        """Async wrapper: run the sync resolution, then emit calibration events."""
        lane_events = await asyncio.to_thread(
            self._resolve_market_sync, ticker, resolved_yes
        )
        if not lane_events:
            return
        await self.record_resolution_calibration_events(
            (
                (ticker, resolved_yes, trade_id, lane_name, lane_estimate)
                for trade_id, lane_name, lane_estimate in lane_events
            )
        )

    async def record_resolution_calibration_events(
        self,
        lane_events: Iterable[tuple[str, bool, str, str, float]],
    ) -> None:
        """Emit calibration feedback for already-committed resolved trades."""
        for ticker, resolved_yes, trade_id, lane_name, lane_estimate in lane_events:
            venue_row = self._conn.execute(
                "SELECT venue FROM paper_trades WHERE trade_id = ?",
                (trade_id,),
            ).fetchone()
            venue = (
                str(venue_row["venue"]).strip()
                if venue_row is not None
                and "venue" in venue_row.keys()
                and str(venue_row["venue"] or "").strip()
                else None
            )
            final_resolution = 1.0 if resolved_yes else 0.0
            error = abs(lane_estimate - final_resolution)
            trade_log.log_calibration_check(
                market_ticker=ticker,
                lane=lane_name,
                lane_estimate=lane_estimate,
                final_resolution=final_resolution,
                error=error,
                venue=venue,
            )
            if self._calibration_task is not None:
                try:
                    await self._calibration_task.record_calibration_check(
                        market_ticker=ticker,
                        lane=lane_name,
                        lane_estimate=lane_estimate,
                        final_resolution=final_resolution,
                        error=error,
                    )
                except Exception as exc:
                    # Calibration-task updates are non-critical relative to the
                    # already-committed resolution. Log and continue.
                    log.warning(
                        "CalibrationTask.record_calibration_check failed for "
                        "trade=%s lane=%s: %s",
                        trade_id, lane_name, exc,
                    )

    def _resolve_market_sync(
        self, ticker: str, resolved_yes: bool
    ) -> list[tuple[str, str, float]]:
        """
        Mark all open paper trades for a ticker as resolved, compute P&L,
        update notional bankroll, and record source credibility outcomes.

        Returns a list of (trade_id, lane_name, lane_estimate) tuples for the
        async shell to emit as CALIBRATION_CHECK events. Returns an empty list
        if no trades are resolved or no per-lane estimates are populated.
        """
        with self._transaction_lock:
            return self._resolve_market_sync_locked(ticker, resolved_yes)

    def _resolve_market_sync_locked(
        self, ticker: str, resolved_yes: bool
    ) -> list[tuple[str, str, float]]:
        with self._conn:
            if self.settlement_schema_present:
                self._conn.execute("BEGIN IMMEDIATE")
            trades = self._conn.execute(
                "SELECT * FROM paper_trades WHERE ticker = ? AND resolved = 0",
                (ticker,),
            ).fetchall()

            if not trades:
                log.debug("No open paper trades for %s", ticker)
                return []
            if self.settlement_schema_present:
                self._require_legacy_alias_unique(trades, ticker=ticker)

            # Pre-calculate all outcomes before any DB writes.
            outcomes: list[tuple] = []
            total_pnl = 0.0
            total_payout = 0.0
            for t in trades:
                won = (t["side"] == "yes" and resolved_yes) or (
                    t["side"] == "no" and not resolved_yes
                )
                payout = float(t["contracts"]) if won else 0.0
                pnl = payout - t["cost_dollars"]
                outcomes.append((t, won, payout, pnl))
                total_pnl += pnl
                total_payout += payout

            now_ts = datetime.now(timezone.utc).isoformat()
            series_ticker = next(
                (
                    str(t["series_ticker"]).strip()
                    for t, _won, _payout, _pnl in outcomes
                    if "series_ticker" in t.keys()
                    and str(t["series_ticker"] or "").strip()
                ),
                ticker.split("-")[0],
            )

            # Mark all trades and credit bankroll under the same writer lock.
            for t, won, payout, pnl in outcomes:
                self._conn.execute(
                    "UPDATE paper_trades SET resolved=1, resolved_yes=?, pnl_dollars=?, "
                    "resolved_ts=? WHERE trade_id=?",
                    (int(resolved_yes), pnl, now_ts, t["trade_id"]),
                )
            # Credit bankroll once for total payout (cost was debited on entry per-trade)
            self._credit_bankroll(total_payout)

        # Keep portfolio in sync — all positions for this ticker are now closed
        self.portfolio.resolve(ticker)

        # Non-critical side effects: credibility + audit log + keyword outcomes
        # (DB already committed above)
        from config import GEOPOLITICAL_SIGNALS
        _kw_direction: dict[str, str] = {}
        for sig in GEOPOLITICAL_SIGNALS:
            for kw in sig["keywords"]:
                _kw_direction[kw] = sig["direction"]

        for t, won, payout, pnl in outcomes:
            self.credibility.record_outcome(t["signal_source"], was_correct=won)
            trade_log.log_paper_resolution(
                trade_id=t["trade_id"],
                ticker=ticker,
                resolved_yes=resolved_yes,
                pnl_dollars=pnl,
                bankroll_delta_dollars=payout,
                venue=t["venue"] if "venue" in t.keys() else None,
            )
            # PROFIT-ALIGN-002 (2026-05-25): per-resolved-trade calibration
            # observation. The biggest missing-piece flagged in the
            # 2026-05-25 architecture review: bot has been paper-trading
            # for weeks but we have no calibration curve / Brier score.
            # Emit one observation row per resolved trade; downstream
            # aggregator (scripts/calibration_aggregator.py, follow-on)
            # rolls these into per-(market_prefix × magnitude_bucket)
            # calibration metrics.
            #
            # realized_outcome = 1 iff the bot's chosen side won.
            # estimated_probability is the bot's stated probability for
            # its chosen side (already conditional on side in
            # paper_trades.estimated_prob).
            try:
                # paper_trades.estimated_prob stores the bot's YES-side
                # probability estimate; the chosen-side probability is
                # estimated_prob for side=yes, (1 - estimated_prob) for
                # side=no. Mirror that convention so the calibration
                # tracker's "p̂ for our bet" semantics are clean.
                est_yes_prob = float(t["estimated_prob"])
                est_chosen_side_prob = (
                    est_yes_prob if t["side"] == "yes" else (1.0 - est_yes_prob)
                )
                trade_log.log_calibration_observation(
                    trade_id=t["trade_id"],
                    ticker=ticker,
                    market_prefix=series_ticker,
                    side=t["side"],
                    estimated_probability=est_chosen_side_prob,
                    realized_outcome=int(won),
                    entry_price_cents=float(t["entry_price_cents"] or 0.0),
                    pnl_dollars=pnl,
                    cost_dollars=float(t["cost_dollars"] or 0.0),
                    llm_magnitude=t["llm_magnitude"] if "llm_magnitude" in t.keys() else None,
                    llm_confidence=(
                        t["llm_confidence"] if "llm_confidence" in t.keys() else None
                    ),
                    signal_source=t["signal_source"] or "",
                    ts_entry=t["ts"] or "",
                    ts_resolved=now_ts,
                )
            except Exception as exc:  # noqa: BLE001 — observability must not break resolve
                log.warning(
                    "PROFIT-ALIGN-002: calibration observation emit failed for %s: %s",
                    t["trade_id"], exc,
                )
            log.info(
                "[RESOLVED] %s %s YES=%s | pnl=$%+.2f | bankroll=$%.2f",
                t["trade_id"], ticker, resolved_yes, pnl,
                self.get_notional_bankroll(),
            )
            # Record per-keyword outcomes for feedback loop learning.
            # Each resolved trade writes one row per keyword that fired on it.
            # Accumulated over time this teaches us which keywords are predictive
            # on which series -- the foundation for Phase 3 dynamic weighting.
            try:
                keywords = json.loads(t["keywords_matched"] or "[]")
                for kw in keywords:
                    direction = _kw_direction.get(kw, t["side"])
                    correct = int(
                        (direction == "yes" and resolved_yes) or
                        (direction == "no"  and not resolved_yes)
                    )
                    self._conn.execute(
                        "INSERT INTO keyword_outcomes "
                        "(trade_id, ticker, series_ticker, keyword, direction, "
                        " market_side, resolved_yes, correct, ts) "
                        "VALUES (?,?,?,?,?,?,?,?,?)",
                        (t["trade_id"], ticker, series_ticker, kw, direction,
                         t["side"], int(resolved_yes), correct, now_ts),
                    )
                if keywords:
                    self._conn.commit()
                    log.debug(
                        "[RESOLVED] %s: recorded %d keyword outcome(s)",
                        t["trade_id"], len(keywords),
                    )
            except Exception as exc:
                log.warning("Failed to record keyword outcomes for %s: %s", t["trade_id"], exc)

        log.info(
            "[RESOLVED] %s: %d trades | net P&L $%+.2f | notional bankroll $%.2f",
            ticker, len(trades), total_pnl, self.get_notional_bankroll(),
        )

        # PROFIT-CAL-001: collect per-lane estimates for the async shell to
        # emit as CALIBRATION_CHECK events. Rows without per-lane data (pre-
        # v0.29.47 history, fast-lane-only paths) are skipped implicitly.
        lane_events: list[tuple[str, str, float]] = []
        _LANES = (
            ("fast",         "fast_lane_p"),
            ("accumulation", "accumulation_p"),
            ("structural",   "structural_p"),
        )
        for t, _won, _payout, _pnl in outcomes:
            trade_id = t["trade_id"]
            for lane_name, lane_col in _LANES:
                estimate = t[lane_col]
                if estimate is None:
                    continue
                lane_events.append((trade_id, lane_name, float(estimate)))
        return lane_events

    def _require_legacy_alias_unique(
        self,
        trades: list[sqlite3.Row],
        *,
        ticker: str,
    ) -> None:
        market_refs = {self._mapped_market_ref_from_row(row) for row in trades}
        if len(market_refs) != 1:
            raise SettlementDriftError(
                f"legacy settlement alias {ticker!r} maps to multiple identities"
            )

    # ── Report ────────────────────────────────────────────────────────────────

    def get_all_open_trades(self, ticker: str) -> list[sqlite3.Row]:
        """Return all unresolved trades for ticker (oldest first)."""
        return self._conn.execute(
            "SELECT * FROM paper_trades WHERE ticker = ? AND resolved = 0 "
            "ORDER BY ts ASC",
            (ticker,),
        ).fetchall()

    def mapped_open_market_refs(self) -> tuple[MarketRef, ...]:
        """Return unresolved canonical identities once, failing closed on drift."""
        market_refs: list[MarketRef] = []
        seen: set[MarketRef] = set()
        for row in self._open_settlement_identity_rows():
            market_ref = self._mapped_market_ref_from_row(row)
            if market_ref not in seen:
                seen.add(market_ref)
                market_refs.append(market_ref)
        return tuple(market_refs)

    def legacy_settlement_alias_check(self) -> StoreCheck:
        """Prove alias-only rollback settlement cannot span canonical identities."""
        refs_by_alias: dict[str, set[tuple[str, str]]] = {}
        invalid_aliases: list[str] = []
        for row in self._open_settlement_identity_rows():
            alias = str(row["ticker"] or "").strip() or "<empty>"
            try:
                market_ref = self._mapped_market_ref_from_row(row)
            except SettlementDriftError:
                invalid_aliases.append(alias)
                continue
            refs_by_alias.setdefault(market_ref.alias, set()).add(
                (market_ref.venue.value, market_ref.venue_market_id)
            )

        collisions = sorted(
            alias for alias, refs in refs_by_alias.items() if len(refs) > 1
        )
        failures = tuple(
            [f"invalid_identity:{alias}" for alias in sorted(set(invalid_aliases))]
            + [f"alias_collision:{alias}" for alias in collisions]
        )
        return StoreCheck(
            ok=not failures,
            failures=failures,
            metrics={
                "open_alias_count": len(refs_by_alias),
                "invalid_identity_count": len(invalid_aliases),
                "alias_collision_count": len(collisions),
            },
        )

    def _open_settlement_identity_rows(self) -> list[sqlite3.Row]:
        return self._conn.execute(
            """
            SELECT trade_id, ticker, venue, venue_market_id, identity_status,
                   quarantine_reason
            FROM paper_trades
            WHERE resolved=0
            ORDER BY ts, trade_id
            """
        ).fetchall()

    @staticmethod
    def _mapped_market_ref_from_row(row: sqlite3.Row) -> MarketRef:
        alias = str(row["ticker"] or "").strip()
        venue_market_id = str(row["venue_market_id"] or "").strip()
        identity_status = str(row["identity_status"] or "").strip()
        try:
            venue = normalize_venue(str(row["venue"] or "").strip())
        except (TypeError, ValueError) as exc:
            raise SettlementDriftError(
                f"open trade {row['trade_id']} is not a valid mapped identity"
            ) from exc
        if identity_status != "mapped" or not alias or not venue_market_id:
            raise SettlementDriftError(
                f"open trade {row['trade_id']} is not a complete mapped identity"
            )
        return MarketRef(
            venue=venue,
            venue_market_id=venue_market_id,
            alias=alias,
        )

    def get_all_trades(self) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM paper_trades ORDER BY ts DESC"
        ).fetchall()

    def generate_report(self) -> str:
        trades    = self.get_all_trades()
        resolved  = [t for t in trades if t["resolved"]]
        open_t    = [t for t in trades if not t["resolved"]]
        notional  = self.get_notional_bankroll()
        start_row = self._conn.execute(
            "SELECT value FROM bot_state WHERE key = 'paper_start_time'"
        ).fetchone()
        start_ts  = start_row["value"] if start_row else "unknown"

        total_cost = sum(t["cost_dollars"] for t in trades)
        total_pnl  = sum(t["pnl_dollars"] for t in resolved if t["pnl_dollars"] is not None)
        wins       = [t for t in resolved if (t["pnl_dollars"] or 0) > 0]
        losses     = [t for t in resolved if (t["pnl_dollars"] or 0) <= 0]
        win_rate   = len(wins) / len(resolved) if resolved else 0.0
        avg_win    = sum(t["pnl_dollars"] for t in wins) / len(wins) if wins else 0
        avg_loss   = sum(t["pnl_dollars"] for t in losses) / len(losses) if losses else 0
        avg_edge   = sum(t["edge"] for t in trades) / len(trades) if trades else 0
        roi        = (total_pnl / total_cost * 100) if total_cost > 0 else 0
        drawdown   = self._starting_bankroll - notional

        lines = [
            "=" * 70,
            "PAPER TRADING PERFORMANCE REPORT",
            "=" * 70,
            f"  Paper trading since:    {start_ts}",
            f"  Report generated:       {datetime.now(timezone.utc).isoformat()}",
            f"  Mode:                   {'PAPER' if cfg.is_paper_trading else 'LIVE'}",
            "",
            "NOTIONAL BANKROLL",
            f"  Starting bankroll:      ${self._starting_bankroll:.2f}",
            f"  Current notional:       ${notional:.2f}",
            f"  Total drawdown:         ${drawdown:+.2f}",
            f"  Capital deployed:       ${total_cost:.2f}",
            "",
            "TRADE SUMMARY",
            f"  Total trades:           {len(trades)}",
            f"  Resolved:               {len(resolved)}",
            f"  Open (pending):         {len(open_t)}",
            "",
            "P&L  (resolved trades)",
            f"  Net P&L:                ${total_pnl:+.2f}",
            f"  ROI on deployed capital:{roi:+.1f}%",
            f"  Win rate:               {win_rate:.1%}  ({len(wins)}W / {len(losses)}L)",
            f"  Avg win:                ${avg_win:+.2f}",
            f"  Avg loss:               ${avg_loss:+.2f}",
        ]

        if avg_loss:
            lines.append(f"  Profit factor:          {abs(avg_win / avg_loss):.2f}x")

        lines += [
            "",
            "SIGNAL QUALITY",
            f"  Average edge:           {avg_edge:+.4f}",
            "",
        ]

        lines += _match_quality_report_section()
        lines += _market_source_hints_report_section()

        # Resolved trades table (last 20)
        if resolved:
            lines.append("RESOLVED TRADES  (most recent 20)")
            table_data = []
            for t in resolved[-20:]:
                table_data.append([
                    t["trade_id"],
                    t["ticker"][:18],
                    t["side"].upper(),
                    t["contracts"],
                    f"{t['price_cents']}c",
                    f"${t['cost_dollars']:.2f}",
                    f"{t['estimated_prob']:.3f}",
                    f"{t['edge']:+.3f}",
                    f"{t['source_multiplier']:.2f}x",
                    "YES" if t["resolved_yes"] else "NO",
                    f"${t['pnl_dollars']:+.2f}",
                ])
            lines.append(tabulate(
                table_data,
                headers=["ID", "Ticker", "Side", "Qty", "Price", "Cost",
                         "Est.P", "Edge", "SrcMult", "Result", "P&L"],
                tablefmt="simple",
            ))
            lines.append("")

        # Open trades table
        if open_t:
            lines.append("OPEN PAPER TRADES")
            table_data = []
            for t in open_t:
                table_data.append([
                    t["trade_id"],
                    t["ticker"][:18],
                    t["side"].upper(),
                    t["contracts"],
                    f"{t['price_cents']}c",
                    f"${t['cost_dollars']:.2f}",
                    f"{t['estimated_prob']:.3f}",
                    f"{t['edge']:+.3f}",
                    t["ts"][:10],
                ])
            lines.append(tabulate(
                table_data,
                headers=["ID", "Ticker", "Side", "Qty", "Price", "Cost", "Est.P", "Edge", "Date"],
                tablefmt="simple",
            ))
            lines.append("")

        # Best/worst
        if resolved:
            best  = max(resolved, key=lambda t: t["pnl_dollars"] or 0)
            worst = min(resolved, key=lambda t: t["pnl_dollars"] or 0)
            lines += [
                "BEST TRADE",
                f"  {best['trade_id']} | {best['ticker']} | {best['side'].upper()} | P&L: ${best['pnl_dollars']:+.2f}",
                f"  Signal: \"{best['signal_headline'][:80]}\"",
                f"  Reasoning: {best['reasoning'][:120]}",
                "",
                "WORST TRADE",
                f"  {worst['trade_id']} | {worst['ticker']} | {worst['side'].upper()} | P&L: ${worst['pnl_dollars']:+.2f}",
                f"  Signal: \"{worst['signal_headline'][:80]}\"",
                f"  Reasoning: {worst['reasoning'][:120]}",
                "",
            ]

        # Source credibility table
        lines.append("SOURCE CREDIBILITY")
        lines.append(self.credibility.format_table())
        lines.append("")

        # Go-live assessment
        lines.append("GO-LIVE ASSESSMENT")
        if len(resolved) < 10:
            lines.append(f"  INSUFFICIENT DATA --{len(resolved)}/10 resolved trades. Keep running.")
        elif win_rate >= 0.55 and total_pnl > 0 and avg_edge > 0.03:
            lines.append("  POSITIVE --Strategy shows edge. Run `python main.py --go-live` to confirm.")
        elif total_pnl < -50:
            lines.append("  NEGATIVE --Significant losses. Review strategy before considering live trading.")
        else:
            lines.append("  NEUTRAL --Mixed results. Continue paper trading and review next week.")

        lines += ["", "Run `python main.py --go-live` only after you are satisfied with these results.", "=" * 70]
        return "\n".join(lines)

    def daily_summary(self) -> None:
        trades   = self.get_all_trades()
        resolved = [t for t in trades if t["resolved"]]
        pnl      = sum(t["pnl_dollars"] for t in resolved if t["pnl_dollars"] is not None)
        open_cnt = len([t for t in trades if not t["resolved"]])
        notional = self.get_notional_bankroll()

        log.info(
            "[DAILY SUMMARY] trades=%d resolved=%d open=%d | "
            "net P&L=$%+.2f | notional=$%.2f",
            len(trades), len(resolved), open_cnt, pnl, notional,
        )
        log.info(
            "[WEEKLY REMINDER] Review with: python main.py --report  "
            "| Go live with: python main.py --go-live"
        )
