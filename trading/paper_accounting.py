"""Exact, default-off paper-accounting persistence contract.

This module defines schema and record boundaries only. It does not wire entry
or settlement handlers into :mod:`trading.paper_trader` and never changes the
legacy gross bankroll.
"""

from __future__ import annotations

from dataclasses import InitVar, dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
import sqlite3
from types import MappingProxyType
from typing import Callable, Mapping

from trading.fees import (
    DIRECT_ACCOUNT_PRECISION,
    FeeContext,
    FeeQuote,
    FeeRole,
    FeeScheduleId,
    FeeUnscorableError,
    NON_DIRECT_ACCOUNT_PRECISION,
    fee_coefficient_for,
    fee_type_for_schedule,
    quote_fee,
)
from trading.settlement_store import (
    SETTLEMENT_DDL_SHA256,
    SETTLEMENT_SCHEMA_VERSION,
    settlement_schema_contract_matches,
)
from trading.venue import Venue


PAPER_ACCOUNTING_SCHEMA_VERSION = 1
PAPER_ACCOUNTING_VERSION = 1

_SHA256_TEXT = re.compile(r"[0-9a-f]{64}")
_DECIMAL_DATABASE_COLUMNS = (
    "fill_quantity",
    "fill_price_dollars",
    "signed_revenue_dollars",
    "fee_multiplier",
    "fee_coefficient",
    "account_precision_dollars",
    "base_fee_dollars",
    "trade_fee_dollars",
    "rounding_adjustment_dollars",
    "balance_rounding_fee_dollars",
    "rebate_dollars",
    "net_fee_dollars",
    "accumulator_before_dollars",
    "accumulator_after_dollars",
    "gross_entry_debit_dollars",
    "net_entry_debit_dollars",
    "settlement_fee_dollars",
    "settlement_refund_dollars",
    "gross_settlement_payout_dollars",
    "net_settlement_payout_dollars",
    "fee_net_pnl_dollars",
)


class PaperAccountingAdmissionError(RuntimeError):
    """A fee-net paper entry cannot be admitted safely."""


class PaperAccountingSchemaError(RuntimeError):
    """The paper-accounting schema is absent, partial, or incompatible."""


PaperAccountingHandler = Callable[["PaperAccountingRecord"], None]


@dataclass(frozen=True)
class PaperAccountingHandlers:
    """Versioned pure-handler registry for future runtime wiring."""

    entry: Mapping[int, PaperAccountingHandler]
    settlement: Mapping[int, PaperAccountingHandler]

    def __post_init__(self) -> None:
        object.__setattr__(self, "entry", _validated_handler_map("entry", self.entry))
        object.__setattr__(
            self,
            "settlement",
            _validated_handler_map("settlement", self.settlement),
        )

    def supports(self, accounting_version: int) -> bool:
        return accounting_version in self.entry and accounting_version in self.settlement

    def dispatch_entry(self, record: "PaperAccountingRecord") -> None:
        self._dispatch("entry", self.entry, record)

    def dispatch_settlement(self, record: "PaperAccountingRecord") -> None:
        self._dispatch("settlement", self.settlement, record)

    @staticmethod
    def _dispatch(
        kind: str,
        handlers: Mapping[int, PaperAccountingHandler],
        record: "PaperAccountingRecord",
    ) -> None:
        handler = handlers.get(record.accounting_version)
        if handler is None:
            raise PaperAccountingAdmissionError(
                f"no {kind} handler for persisted accounting version {record.accounting_version}"
            )
        try:
            record.validate_record()
        except ValueError as exc:
            raise PaperAccountingAdmissionError(
                f"invalid {kind} record for persisted accounting version {record.accounting_version}"
            ) from exc
        settled = record.settled_at is not None
        if kind == "entry" and settled:
            raise PaperAccountingAdmissionError("entry handlers require an unsettled accounting record")
        if kind == "settlement" and not settled:
            raise PaperAccountingAdmissionError("settlement handlers require a complete settled accounting record")
        handler(record)


@dataclass(frozen=True)
class PaperAccountingRecord:
    """Complete persisted fee context and quote for one paper fill."""

    accounting_version: int
    entry_request_id: str
    trade_id: str
    order_id: str
    fill_id: str
    filled_at: datetime
    schedule_id: FeeScheduleId
    role: FeeRole
    quantity: Decimal
    price: Decimal
    signed_revenue: Decimal
    multiplier: Decimal
    fee_type: str
    fee_multiplier_provenance_sha256: str
    fee_multiplier_effective_at: datetime
    coefficient: Decimal
    account_precision: Decimal | None
    account_precision_mode: str
    quote: FeeQuote
    gross_entry_debit: Decimal
    net_entry_debit: Decimal
    recorded_at: datetime
    settlement_observation_sha256: str | None = None
    settled_at: datetime | None = None
    settlement_fee: Decimal | None = None
    settlement_refund: Decimal | None = None
    gross_settlement_payout: Decimal | None = None
    net_settlement_payout: Decimal | None = None
    fee_net_pnl: Decimal | None = None
    validate: InitVar[bool] = True

    def __post_init__(self, validate: bool) -> None:
        if validate:
            self.validate_record()

    @classmethod
    def decimal_database_columns(cls) -> tuple[str, ...]:
        return _DECIMAL_DATABASE_COLUMNS

    def validate_record(self) -> None:
        if self.accounting_version != PAPER_ACCOUNTING_VERSION:
            raise ValueError(f"unsupported accounting_version: {self.accounting_version}")
        for name in ("entry_request_id", "trade_id", "order_id", "fill_id"):
            _require_identifier(name, getattr(self, name))
        _require_aware_datetime("filled_at", self.filled_at)
        _require_aware_datetime("recorded_at", self.recorded_at)
        if self.recorded_at < self.filled_at:
            raise ValueError("recorded_at must not precede filled_at")
        _require_nonempty_text("fee_type", self.fee_type)
        _require_sha256(
            "fee_multiplier_provenance_sha256",
            self.fee_multiplier_provenance_sha256,
        )
        _require_aware_datetime(
            "fee_multiplier_effective_at",
            self.fee_multiplier_effective_at,
        )
        if self.fee_multiplier_effective_at > self.filled_at:
            raise ValueError("fee_multiplier_effective_at must not follow filled_at")
        if not isinstance(self.schedule_id, FeeScheduleId):
            raise ValueError("schedule_id must be FeeScheduleId")
        _validate_schedule(self.schedule_id)
        if self.fee_multiplier_effective_at < self.schedule_id.effective_from:
            raise ValueError("fee_multiplier_effective_at must not precede fee schedule effective_from")
        if (
            self.schedule_id.effective_to is not None
            and self.fee_multiplier_effective_at >= self.schedule_id.effective_to
        ):
            raise ValueError("fee_multiplier_effective_at must precede fee schedule effective_to")
        if not isinstance(self.role, FeeRole):
            raise ValueError("role must be FeeRole")
        expected_fee_type = fee_type_for_schedule(self.schedule_id)
        if self.fee_type != expected_fee_type:
            raise ValueError("fee_type does not match pinned fee formula")

        for name in (
            "quantity",
            "price",
            "signed_revenue",
            "multiplier",
            "coefficient",
            "gross_entry_debit",
            "net_entry_debit",
        ):
            _require_decimal(name, getattr(self, name))
        if self.account_precision is not None:
            _require_decimal("account_precision", self.account_precision)
        expected_precision_mode: str
        if self.schedule_id.venue is Venue.KALSHI:
            if self.account_precision == DIRECT_ACCOUNT_PRECISION:
                expected_precision_mode = "direct"
            elif self.account_precision == NON_DIRECT_ACCOUNT_PRECISION:
                expected_precision_mode = "non_direct"
            else:
                raise ValueError("Kalshi account_precision must use an exact supported quantum")
        elif self.schedule_id.venue is Venue.POLYMARKET_US:
            if self.account_precision is not None:
                raise ValueError("Polymarket account_precision must be absent")
            expected_precision_mode = "not_applicable"
        else:  # pragma: no cover - Venue rejects unknown values before this boundary.
            raise ValueError("unsupported fee schedule venue")
        if self.account_precision_mode != expected_precision_mode:
            raise ValueError("account_precision_mode does not match venue and exact account precision")

        if self.signed_revenue >= 0:
            raise ValueError("signed_revenue must be negative for an entry debit")

        if self.quote.schedule_id != self.schedule_id:
            raise ValueError("quote schedule_id does not match persisted schedule")
        if self.quote.role != self.role:
            raise ValueError("quote role does not match persisted role")
        expected_coefficient = fee_coefficient_for(self.schedule_id, self.role)
        if self.coefficient != expected_coefficient:
            raise ValueError("coefficient does not match pinned fee schedule")
        expected_quote = quote_fee(
            FeeContext(
                schedule_id=self.schedule_id,
                role=self.role,
                quantity=self.quantity,
                price=self.price,
                signed_revenue=self.signed_revenue,
                order_id=self.order_id,
                accumulator=self.quote.previous_accumulator,
                multiplier=self.multiplier,
                coefficient=self.coefficient,
                account_precision=self.account_precision,
                timestamp=self.filled_at,
            )
        )
        if self.quote != expected_quote:
            raise ValueError("persisted FeeQuote does not match exact fee context")
        if self.gross_entry_debit != abs(self.signed_revenue):
            raise ValueError("gross_entry_debit must equal absolute signed_revenue")
        if self.gross_entry_debit <= 0:
            raise ValueError("gross_entry_debit must be positive")
        if self.net_entry_debit != self.gross_entry_debit + self.quote.net_fee:
            raise ValueError("net_entry_debit must include exact net_fee")

        settlement_values = (
            self.settlement_observation_sha256,
            self.settled_at,
            self.settlement_fee,
            self.settlement_refund,
            self.gross_settlement_payout,
            self.net_settlement_payout,
            self.fee_net_pnl,
        )
        present = tuple(value is not None for value in settlement_values)
        if any(present) and not all(present):
            raise ValueError("settlement fields must be all null or complete")
        if all(present):
            assert self.settlement_observation_sha256 is not None
            assert self.settled_at is not None
            assert self.settlement_fee is not None
            assert self.settlement_refund is not None
            assert self.gross_settlement_payout is not None
            assert self.net_settlement_payout is not None
            assert self.fee_net_pnl is not None
            _require_sha256(
                "settlement_observation_sha256",
                self.settlement_observation_sha256,
            )
            _require_aware_datetime("settled_at", self.settled_at)
            if self.settled_at < self.recorded_at:
                raise ValueError("settled_at must not precede recorded_at")
            for name in (
                "settlement_fee",
                "settlement_refund",
                "gross_settlement_payout",
                "net_settlement_payout",
                "fee_net_pnl",
            ):
                _require_decimal(name, getattr(self, name))
            if self.settlement_fee < 0 or self.settlement_refund < 0 or self.gross_settlement_payout < 0:
                raise ValueError("settlement fee, refund, and gross payout must be non-negative")
            if self.gross_settlement_payout > self.quantity:
                raise ValueError("gross_settlement_payout must not exceed fill_quantity")
            if self.settlement_refund > self.gross_entry_debit:
                raise ValueError("settlement_refund must not exceed gross_entry_debit")
            if self.gross_settlement_payout > 0 and self.settlement_refund > 0:
                raise ValueError("settlement payout and refund are mutually exclusive")
            expected_net_payout = self.gross_settlement_payout - self.settlement_fee + self.settlement_refund
            if self.net_settlement_payout != expected_net_payout:
                raise ValueError("net_settlement_payout must equal gross payout minus fee plus refund")
            if self.net_settlement_payout < 0:
                raise ValueError("net_settlement_payout must be non-negative")
            if self.fee_net_pnl != self.net_settlement_payout - self.net_entry_debit:
                raise ValueError("fee_net_pnl must equal net payout minus net entry debit")

    def to_database_values(self) -> dict[str, object]:
        self.validate_record()
        return {
            "accounting_version": self.accounting_version,
            "entry_request_id": self.entry_request_id,
            "trade_id": self.trade_id,
            "venue": self.schedule_id.venue.value,
            "order_id": self.order_id,
            "fill_id": self.fill_id,
            "fee_role": self.role.value,
            "filled_at": _datetime_text(self.filled_at),
            "fill_quantity": _decimal_text(self.quantity),
            "fill_price_dollars": _decimal_text(self.price),
            "signed_revenue_dollars": _decimal_text(self.signed_revenue),
            "fee_schedule_name": self.schedule_id.name,
            "fee_schedule_effective_from": _datetime_text(self.schedule_id.effective_from),
            "fee_schedule_effective_to": (
                _datetime_text(self.schedule_id.effective_to) if self.schedule_id.effective_to is not None else None
            ),
            "fee_schedule_artifact_sha256": self.schedule_id.artifact_sha256,
            "fee_schedule_supporting_artifacts_json": json.dumps(
                list(self.schedule_id.supporting_artifact_sha256),
                allow_nan=False,
                ensure_ascii=True,
                separators=(",", ":"),
            ),
            "fee_multiplier": _decimal_text(self.multiplier),
            "fee_type": self.fee_type,
            "fee_multiplier_provenance_sha256": self.fee_multiplier_provenance_sha256,
            "fee_multiplier_effective_at": _datetime_text(self.fee_multiplier_effective_at),
            "fee_coefficient": _decimal_text(self.coefficient),
            "account_precision_dollars": (
                _decimal_text(self.account_precision) if self.account_precision is not None else None
            ),
            "account_precision_mode": self.account_precision_mode,
            "base_fee_dollars": _decimal_text(self.quote.base_fee),
            "trade_fee_dollars": _decimal_text(self.quote.trade_fee),
            "rounding_adjustment_dollars": _decimal_text(self.quote.rounding_adjustment),
            "balance_rounding_fee_dollars": _decimal_text(self.quote.balance_rounding_fee),
            "rebate_dollars": _decimal_text(self.quote.rebate),
            "net_fee_dollars": _decimal_text(self.quote.net_fee),
            "accumulator_before_dollars": _decimal_text(self.quote.previous_accumulator),
            "accumulator_after_dollars": _decimal_text(self.quote.next_accumulator),
            "gross_entry_debit_dollars": _decimal_text(self.gross_entry_debit),
            "net_entry_debit_dollars": _decimal_text(self.net_entry_debit),
            "settlement_observation_sha256": self.settlement_observation_sha256,
            "settled_at": (_datetime_text(self.settled_at) if self.settled_at is not None else None),
            "settlement_fee_dollars": _optional_decimal_text(self.settlement_fee),
            "settlement_refund_dollars": _optional_decimal_text(self.settlement_refund),
            "gross_settlement_payout_dollars": _optional_decimal_text(self.gross_settlement_payout),
            "net_settlement_payout_dollars": _optional_decimal_text(self.net_settlement_payout),
            "fee_net_pnl_dollars": _optional_decimal_text(self.fee_net_pnl),
            "recorded_at": _datetime_text(self.recorded_at),
        }

    @classmethod
    def from_database_row(
        cls,
        row: Mapping[str, object] | sqlite3.Row,
    ) -> "PaperAccountingRecord":
        value = lambda name: _row_value(row, name)
        venue = Venue(str(value("venue")))
        schedule_id = FeeScheduleId(
            name=_required_text("fee_schedule_name", value("fee_schedule_name")),
            venue=venue,
            effective_from=_parse_datetime_text(
                "fee_schedule_effective_from",
                value("fee_schedule_effective_from"),
            ),
            effective_to=(
                _parse_datetime_text(
                    "fee_schedule_effective_to",
                    value("fee_schedule_effective_to"),
                )
                if value("fee_schedule_effective_to") is not None
                else None
            ),
            artifact_sha256=_required_sha256(
                "fee_schedule_artifact_sha256",
                value("fee_schedule_artifact_sha256"),
            ),
            supporting_artifact_sha256=_parse_supporting_artifacts(value("fee_schedule_supporting_artifacts_json")),
        )
        role = FeeRole(str(value("fee_role")))
        quote = FeeQuote(
            schedule_id=schedule_id,
            role=role,
            base_fee=_parse_decimal_text("base_fee_dollars", value("base_fee_dollars")),
            trade_fee=_parse_decimal_text("trade_fee_dollars", value("trade_fee_dollars")),
            rounding_adjustment=_parse_decimal_text(
                "rounding_adjustment_dollars",
                value("rounding_adjustment_dollars"),
            ),
            balance_rounding_fee=_parse_decimal_text(
                "balance_rounding_fee_dollars",
                value("balance_rounding_fee_dollars"),
            ),
            rebate=_parse_decimal_text("rebate_dollars", value("rebate_dollars")),
            net_fee=_parse_decimal_text("net_fee_dollars", value("net_fee_dollars")),
            previous_accumulator=_parse_decimal_text(
                "accumulator_before_dollars",
                value("accumulator_before_dollars"),
            ),
            next_accumulator=_parse_decimal_text(
                "accumulator_after_dollars",
                value("accumulator_after_dollars"),
            ),
        )
        return cls(
            accounting_version=_required_integer("accounting_version", value("accounting_version")),
            entry_request_id=_required_text("entry_request_id", value("entry_request_id")),
            trade_id=_required_text("trade_id", value("trade_id")),
            order_id=_required_text("order_id", value("order_id")),
            fill_id=_required_text("fill_id", value("fill_id")),
            filled_at=_parse_datetime_text("filled_at", value("filled_at")),
            schedule_id=schedule_id,
            role=role,
            quantity=_parse_decimal_text("fill_quantity", value("fill_quantity")),
            price=_parse_decimal_text("fill_price_dollars", value("fill_price_dollars")),
            signed_revenue=_parse_decimal_text("signed_revenue_dollars", value("signed_revenue_dollars")),
            multiplier=_parse_decimal_text("fee_multiplier", value("fee_multiplier")),
            fee_type=_required_nonempty_text("fee_type", value("fee_type")),
            fee_multiplier_provenance_sha256=_required_sha256(
                "fee_multiplier_provenance_sha256",
                value("fee_multiplier_provenance_sha256"),
            ),
            fee_multiplier_effective_at=_parse_datetime_text(
                "fee_multiplier_effective_at",
                value("fee_multiplier_effective_at"),
            ),
            coefficient=_parse_decimal_text("fee_coefficient", value("fee_coefficient")),
            account_precision=(
                _parse_decimal_text(
                    "account_precision_dollars",
                    value("account_precision_dollars"),
                )
                if value("account_precision_dollars") is not None
                else None
            ),
            account_precision_mode=_required_nonempty_text(
                "account_precision_mode",
                value("account_precision_mode"),
            ),
            quote=quote,
            gross_entry_debit=_parse_decimal_text(
                "gross_entry_debit_dollars",
                value("gross_entry_debit_dollars"),
            ),
            net_entry_debit=_parse_decimal_text("net_entry_debit_dollars", value("net_entry_debit_dollars")),
            settlement_observation_sha256=(
                _required_sha256(
                    "settlement_observation_sha256",
                    value("settlement_observation_sha256"),
                )
                if value("settlement_observation_sha256") is not None
                else None
            ),
            settled_at=(
                _parse_datetime_text("settled_at", value("settled_at")) if value("settled_at") is not None else None
            ),
            settlement_fee=_parse_optional_decimal_text("settlement_fee_dollars", value("settlement_fee_dollars")),
            settlement_refund=_parse_optional_decimal_text(
                "settlement_refund_dollars", value("settlement_refund_dollars")
            ),
            gross_settlement_payout=_parse_optional_decimal_text(
                "gross_settlement_payout_dollars",
                value("gross_settlement_payout_dollars"),
            ),
            net_settlement_payout=_parse_optional_decimal_text(
                "net_settlement_payout_dollars",
                value("net_settlement_payout_dollars"),
            ),
            fee_net_pnl=_parse_optional_decimal_text("fee_net_pnl_dollars", value("fee_net_pnl_dollars")),
            recorded_at=_parse_datetime_text("recorded_at", value("recorded_at")),
        )


_TABLE_STATEMENTS: tuple[tuple[str, str], ...] = (
    (
        "paper_accounting_schema_meta",
        """
        CREATE TABLE paper_accounting_schema_meta (
            schema_version INTEGER PRIMARY KEY CHECK (schema_version = 1),
            accounting_version INTEGER NOT NULL CHECK (accounting_version = 1),
            ddl_sha256 TEXT NOT NULL UNIQUE CHECK (
                length(ddl_sha256) = 64 AND ddl_sha256 NOT GLOB '*[^0-9a-f]*'
            ),
            migration_plan_sha256 TEXT NOT NULL CHECK (
                length(migration_plan_sha256) = 64
                AND migration_plan_sha256 NOT GLOB '*[^0-9a-f]*'
            ),
            applied_at TEXT NOT NULL
        )
        """,
    ),
    (
        "paper_trade_accounting",
        """
        CREATE TABLE paper_trade_accounting (
            accounting_id INTEGER PRIMARY KEY AUTOINCREMENT,
            accounting_version INTEGER NOT NULL CHECK (accounting_version = 1),
            entry_request_id TEXT NOT NULL UNIQUE CHECK (length(trim(entry_request_id)) > 0),
            trade_id TEXT NOT NULL UNIQUE REFERENCES paper_trades(trade_id)
                ON UPDATE RESTRICT ON DELETE RESTRICT,
            venue TEXT NOT NULL CHECK (venue IN ('kalshi','polymarket_us')),
            order_id TEXT NOT NULL CHECK (length(trim(order_id)) > 0),
            fill_id TEXT NOT NULL CHECK (length(trim(fill_id)) > 0),
            fee_role TEXT NOT NULL CHECK (fee_role IN ('maker','taker')),
            filled_at TEXT NOT NULL,
            fill_quantity TEXT NOT NULL,
            fill_price_dollars TEXT NOT NULL,
            signed_revenue_dollars TEXT NOT NULL,
            fee_schedule_name TEXT NOT NULL,
            fee_schedule_effective_from TEXT NOT NULL,
            fee_schedule_effective_to TEXT,
            fee_schedule_artifact_sha256 TEXT NOT NULL CHECK (
                length(fee_schedule_artifact_sha256) = 64
                AND fee_schedule_artifact_sha256 NOT GLOB '*[^0-9a-f]*'
            ),
            fee_schedule_supporting_artifacts_json TEXT NOT NULL,
            fee_multiplier TEXT NOT NULL,
            fee_type TEXT NOT NULL CHECK (fee_type = 'quadratic'),
            fee_multiplier_provenance_sha256 TEXT NOT NULL CHECK (
                length(fee_multiplier_provenance_sha256) = 64
                AND fee_multiplier_provenance_sha256 NOT GLOB '*[^0-9a-f]*'
            ),
            fee_multiplier_effective_at TEXT NOT NULL,
            fee_coefficient TEXT NOT NULL,
            account_precision_dollars TEXT,
            account_precision_mode TEXT NOT NULL CHECK (
                account_precision_mode IN ('direct','non_direct','not_applicable')
            ),
            base_fee_dollars TEXT NOT NULL,
            trade_fee_dollars TEXT NOT NULL,
            rounding_adjustment_dollars TEXT NOT NULL,
            balance_rounding_fee_dollars TEXT NOT NULL,
            rebate_dollars TEXT NOT NULL,
            net_fee_dollars TEXT NOT NULL,
            accumulator_before_dollars TEXT NOT NULL,
            accumulator_after_dollars TEXT NOT NULL,
            gross_entry_debit_dollars TEXT NOT NULL,
            net_entry_debit_dollars TEXT NOT NULL,
            settlement_observation_sha256 TEXT REFERENCES
                paper_settlement_observations(observation_sha256)
                ON UPDATE RESTRICT ON DELETE RESTRICT,
            settled_at TEXT,
            settlement_fee_dollars TEXT,
            settlement_refund_dollars TEXT,
            gross_settlement_payout_dollars TEXT,
            net_settlement_payout_dollars TEXT,
            fee_net_pnl_dollars TEXT,
            recorded_at TEXT NOT NULL,
            UNIQUE (venue, fill_id),
            CHECK (
                (
                    settlement_observation_sha256 IS NULL
                    AND settled_at IS NULL
                    AND settlement_fee_dollars IS NULL
                    AND settlement_refund_dollars IS NULL
                    AND gross_settlement_payout_dollars IS NULL
                    AND net_settlement_payout_dollars IS NULL
                    AND fee_net_pnl_dollars IS NULL
                ) OR (
                    settlement_observation_sha256 IS NOT NULL
                    AND settled_at IS NOT NULL
                    AND settlement_fee_dollars IS NOT NULL
                    AND settlement_refund_dollars IS NOT NULL
                    AND gross_settlement_payout_dollars IS NOT NULL
                    AND net_settlement_payout_dollars IS NOT NULL
                    AND fee_net_pnl_dollars IS NOT NULL
                )
            )
        )
        """,
    ),
)

_INDEX_STATEMENTS: tuple[tuple[str, str], ...] = (
    (
        "paper_trade_accounting_accumulator_idx",
        "CREATE INDEX paper_trade_accounting_accumulator_idx "
        "ON paper_trade_accounting(venue, account_precision_dollars, accounting_id)",
    ),
)

_TRIGGER_STATEMENTS: tuple[tuple[str, str], ...] = (
    (
        "immutable_paper_trade_accounting_version",
        """
        CREATE TRIGGER immutable_paper_trade_accounting_version
        BEFORE UPDATE OF accounting_version ON paper_trade_accounting
        WHEN NEW.accounting_version IS NOT OLD.accounting_version
        BEGIN
            SELECT RAISE(ABORT, 'accounting_version is immutable');
        END
        """,
    ),
    (
        "immutable_paper_trade_accounting_entry",
        """
        CREATE TRIGGER immutable_paper_trade_accounting_entry
        BEFORE UPDATE ON paper_trade_accounting
        WHEN NEW.accounting_id IS NOT OLD.accounting_id
          OR NEW.entry_request_id IS NOT OLD.entry_request_id
          OR NEW.trade_id IS NOT OLD.trade_id
          OR NEW.venue IS NOT OLD.venue
          OR NEW.order_id IS NOT OLD.order_id
          OR NEW.fill_id IS NOT OLD.fill_id
          OR NEW.fee_role IS NOT OLD.fee_role
          OR NEW.filled_at IS NOT OLD.filled_at
          OR NEW.fill_quantity IS NOT OLD.fill_quantity
          OR NEW.fill_price_dollars IS NOT OLD.fill_price_dollars
          OR NEW.signed_revenue_dollars IS NOT OLD.signed_revenue_dollars
          OR NEW.fee_schedule_name IS NOT OLD.fee_schedule_name
          OR NEW.fee_schedule_effective_from IS NOT OLD.fee_schedule_effective_from
          OR NEW.fee_schedule_effective_to IS NOT OLD.fee_schedule_effective_to
          OR NEW.fee_schedule_artifact_sha256 IS NOT OLD.fee_schedule_artifact_sha256
          OR NEW.fee_schedule_supporting_artifacts_json
             IS NOT OLD.fee_schedule_supporting_artifacts_json
          OR NEW.fee_multiplier IS NOT OLD.fee_multiplier
          OR NEW.fee_type IS NOT OLD.fee_type
          OR NEW.fee_multiplier_provenance_sha256
             IS NOT OLD.fee_multiplier_provenance_sha256
          OR NEW.fee_multiplier_effective_at IS NOT OLD.fee_multiplier_effective_at
          OR NEW.fee_coefficient IS NOT OLD.fee_coefficient
          OR NEW.account_precision_dollars IS NOT OLD.account_precision_dollars
          OR NEW.account_precision_mode IS NOT OLD.account_precision_mode
          OR NEW.base_fee_dollars IS NOT OLD.base_fee_dollars
          OR NEW.trade_fee_dollars IS NOT OLD.trade_fee_dollars
          OR NEW.rounding_adjustment_dollars IS NOT OLD.rounding_adjustment_dollars
          OR NEW.balance_rounding_fee_dollars IS NOT OLD.balance_rounding_fee_dollars
          OR NEW.rebate_dollars IS NOT OLD.rebate_dollars
          OR NEW.net_fee_dollars IS NOT OLD.net_fee_dollars
          OR NEW.accumulator_before_dollars IS NOT OLD.accumulator_before_dollars
          OR NEW.accumulator_after_dollars IS NOT OLD.accumulator_after_dollars
          OR NEW.gross_entry_debit_dollars IS NOT OLD.gross_entry_debit_dollars
          OR NEW.net_entry_debit_dollars IS NOT OLD.net_entry_debit_dollars
          OR NEW.recorded_at IS NOT OLD.recorded_at
        BEGIN
            SELECT RAISE(ABORT, 'paper-accounting entry fields are immutable');
        END
        """,
    ),
    (
        "immutable_paper_trade_accounting_settlement",
        """
        CREATE TRIGGER immutable_paper_trade_accounting_settlement
        BEFORE UPDATE ON paper_trade_accounting
        WHEN OLD.settled_at IS NOT NULL AND (
            NEW.settlement_observation_sha256
                IS NOT OLD.settlement_observation_sha256
            OR NEW.settled_at IS NOT OLD.settled_at
            OR NEW.settlement_fee_dollars IS NOT OLD.settlement_fee_dollars
            OR NEW.settlement_refund_dollars IS NOT OLD.settlement_refund_dollars
            OR NEW.gross_settlement_payout_dollars
                IS NOT OLD.gross_settlement_payout_dollars
            OR NEW.net_settlement_payout_dollars
                IS NOT OLD.net_settlement_payout_dollars
            OR NEW.fee_net_pnl_dollars IS NOT OLD.fee_net_pnl_dollars
        )
        BEGIN
            SELECT RAISE(ABORT, 'paper-accounting settlement is immutable');
        END
        """,
    ),
    (
        "immutable_paper_trade_accounting_delete",
        """
        CREATE TRIGGER immutable_paper_trade_accounting_delete
        BEFORE DELETE ON paper_trade_accounting
        BEGIN
            SELECT RAISE(ABORT, 'paper-accounting ledger is append-only');
        END
        """,
    ),
    (
        "immutable_paper_accounting_schema_meta_update",
        """
        CREATE TRIGGER immutable_paper_accounting_schema_meta_update
        BEFORE UPDATE ON paper_accounting_schema_meta
        BEGIN
            SELECT RAISE(ABORT, 'paper-accounting schema meta is immutable');
        END
        """,
    ),
    (
        "immutable_paper_accounting_schema_meta_delete",
        """
        CREATE TRIGGER immutable_paper_accounting_schema_meta_delete
        BEFORE DELETE ON paper_accounting_schema_meta
        BEGIN
            SELECT RAISE(ABORT, 'paper-accounting schema meta is immutable');
        END
        """,
    ),
)

PAPER_ACCOUNTING_TARGET_STATEMENTS = _TABLE_STATEMENTS + _INDEX_STATEMENTS + _TRIGGER_STATEMENTS
_DDL_CONTRACT = "\n".join(f"-- {name}\n{statement.strip()};" for name, statement in PAPER_ACCOUNTING_TARGET_STATEMENTS)
PAPER_ACCOUNTING_DDL_SHA256 = hashlib.sha256(_DDL_CONTRACT.encode("utf-8")).hexdigest()
PAPER_ACCOUNTING_FRESH_PLAN_SHA256 = hashlib.sha256(b"paper-accounting-schema:fresh-database:v1").hexdigest()
_ACCOUNTING_OBJECT_NAMES = frozenset(name for name, _statement in PAPER_ACCOUNTING_TARGET_STATEMENTS)


def initialize_fresh_paper_accounting_schema(
    conn: sqlite3.Connection,
    *,
    migration_plan_sha256: str = PAPER_ACCOUNTING_FRESH_PLAN_SHA256,
    applied_at: str | None = None,
    fault_hook: Callable[[str], None] | None = None,
) -> None:
    """Install accounting v1 in the caller's transaction without committing."""

    _require_valid_gross_settlement_v1(conn)
    _require_sha256("migration_plan_sha256", migration_plan_sha256)
    canonical_applied_at = (
        datetime.now(timezone.utc).isoformat()
        if applied_at is None
        else _datetime_text(_parse_datetime_text("applied_at", applied_at))
    )
    artifacts = _present_accounting_artifacts(conn)
    if artifacts:
        raise PaperAccountingSchemaError("partial or existing paper-accounting schema; explicit no-op required")
    hook = fault_hook or (lambda _stage: None)
    for name, statement in PAPER_ACCOUNTING_TARGET_STATEMENTS:
        conn.execute(statement)
        hook(f"after_ddl:{name}")
    conn.execute(
        """
        INSERT INTO paper_accounting_schema_meta (
            schema_version, accounting_version, ddl_sha256,
            migration_plan_sha256, applied_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (
            PAPER_ACCOUNTING_SCHEMA_VERSION,
            PAPER_ACCOUNTING_VERSION,
            PAPER_ACCOUNTING_DDL_SHA256,
            migration_plan_sha256,
            canonical_applied_at,
        ),
    )
    hook("after_meta")
    if not paper_accounting_schema_contract_matches(conn):
        raise PaperAccountingSchemaError("paper-accounting schema verification failed after installation")


def paper_accounting_schema_contract_matches(conn: sqlite3.Connection) -> bool:
    """Verify exact accounting objects, meta, foreign key, and gross v1."""

    try:
        _require_valid_gross_settlement_v1(conn)
        if _present_accounting_artifacts(conn) != _ACCOUNTING_OBJECT_NAMES:
            return False
        expected = {
            name: (_object_type(name), _normalize_sql(statement))
            for name, statement in PAPER_ACCOUNTING_TARGET_STATEMENTS
        }
        for name, (object_type, sql) in expected.items():
            rows = conn.execute("SELECT type, sql FROM sqlite_schema WHERE name=?", (name,)).fetchall()
            if len(rows) != 1:
                return False
            if str(rows[0][0]) != object_type or _normalize_sql(rows[0][1]) != sql:
                return False
        meta = conn.execute(
            """
            SELECT schema_version, accounting_version, ddl_sha256,
                   migration_plan_sha256, applied_at
            FROM paper_accounting_schema_meta
            """
        ).fetchall()
        if len(meta) != 1:
            return False
        if (
            int(meta[0][0]) != PAPER_ACCOUNTING_SCHEMA_VERSION
            or int(meta[0][1]) != PAPER_ACCOUNTING_VERSION
            or str(meta[0][2]) != PAPER_ACCOUNTING_DDL_SHA256
            or _SHA256_TEXT.fullmatch(str(meta[0][3])) is None
        ):
            return False
        _parse_datetime_text("paper_accounting_schema_meta.applied_at", meta[0][4])
        foreign_keys = conn.execute("PRAGMA foreign_key_list(paper_trade_accounting)").fetchall()
        observed_foreign_keys = {
            (
                int(foreign_key[1]),
                str(foreign_key[2]),
                str(foreign_key[3]),
                str(foreign_key[4]),
                str(foreign_key[5]).upper(),
                str(foreign_key[6]).upper(),
                str(foreign_key[7]).upper(),
            )
            for foreign_key in foreign_keys
        }
        expected_foreign_keys = {
            (
                0,
                "paper_trades",
                "trade_id",
                "trade_id",
                "RESTRICT",
                "RESTRICT",
                "NONE",
            ),
            (
                0,
                "paper_settlement_observations",
                "settlement_observation_sha256",
                "observation_sha256",
                "RESTRICT",
                "RESTRICT",
                "NONE",
            ),
        }
        if observed_foreign_keys != expected_foreign_keys:
            return False
        return not conn.execute("PRAGMA foreign_key_check").fetchone()
    except (PaperAccountingSchemaError, sqlite3.DatabaseError, TypeError, ValueError):
        return False


def require_paper_accounting_admission(
    conn: sqlite3.Connection,
    handlers: PaperAccountingHandlers | None,
    entry_request_id: str,
    schedule_id: FeeScheduleId,
) -> None:
    """Fail closed unless schema, identity, schedule, and both handlers are ready."""

    if not paper_accounting_schema_contract_matches(conn):
        raise PaperAccountingAdmissionError("paper-accounting schema is absent or incompatible")
    try:
        _require_identifier("entry_request_id", entry_request_id)
    except ValueError as exc:
        raise PaperAccountingAdmissionError(str(exc)) from exc
    if handlers is None or not handlers.supports(PAPER_ACCOUNTING_VERSION):
        raise PaperAccountingAdmissionError(
            f"both entry and settlement handlers must be registered for accounting version {PAPER_ACCOUNTING_VERSION}"
        )
    try:
        _validate_schedule(schedule_id)
    except (FeeUnscorableError, ValueError, TypeError) as exc:
        raise PaperAccountingAdmissionError("entry requires a supported pinned fee schedule") from exc


def accounting_schema_state(conn: sqlite3.Connection) -> str:
    """Return ``apply`` or ``noop``; reject every partial/incompatible state."""

    _require_valid_gross_settlement_v1(conn)
    artifacts = _present_accounting_artifacts(conn)
    if not artifacts:
        return "apply"
    if artifacts != _ACCOUNTING_OBJECT_NAMES:
        raise PaperAccountingSchemaError("partial paper-accounting schema")
    if not paper_accounting_schema_contract_matches(conn):
        raise PaperAccountingSchemaError("paper-accounting schema does not match target contract")
    return "noop"


def _require_valid_gross_settlement_v1(conn: sqlite3.Connection) -> None:
    if not settlement_schema_contract_matches(conn):
        raise PaperAccountingSchemaError("valid gross settlement v1 is required")
    try:
        rows = conn.execute("SELECT schema_version, ddl_sha256 FROM paper_settlement_schema_meta").fetchall()
    except sqlite3.DatabaseError as exc:
        raise PaperAccountingSchemaError("valid gross settlement v1 is required") from exc
    if len(rows) != 1 or int(rows[0][0]) != SETTLEMENT_SCHEMA_VERSION or str(rows[0][1]) != SETTLEMENT_DDL_SHA256:
        raise PaperAccountingSchemaError("valid gross settlement v1 is required")


def _present_accounting_artifacts(conn: sqlite3.Connection) -> frozenset[str]:
    rows = conn.execute(
        """
        SELECT name
        FROM sqlite_schema
        WHERE name NOT GLOB 'sqlite_autoindex_*'
          AND (
              tbl_name IN ('paper_accounting_schema_meta', 'paper_trade_accounting')
              OR name GLOB 'paper_accounting_*'
              OR name GLOB 'paper_trade_accounting_*'
          )
        """
    ).fetchall()
    return frozenset(str(row[0]) for row in rows)


def _object_type(name: str) -> str:
    if name in {item[0] for item in _TABLE_STATEMENTS}:
        return "table"
    if name in {item[0] for item in _INDEX_STATEMENTS}:
        return "index"
    return "trigger"


def _normalize_sql(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.strip().rstrip(";").lower().split())


def _validated_handler_map(
    kind: str,
    value: Mapping[int, PaperAccountingHandler],
) -> Mapping[int, PaperAccountingHandler]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{kind} handlers must be a mapping")
    result: dict[int, PaperAccountingHandler] = {}
    for version, handler in value.items():
        if isinstance(version, bool) or not isinstance(version, int) or version <= 0:
            raise ValueError(f"{kind} handler version must be a positive integer")
        if not callable(handler):
            raise TypeError(f"{kind} handler for version {version} is not callable")
        result[version] = handler
    return MappingProxyType(result)


def _validate_schedule(schedule_id: FeeScheduleId) -> None:
    if not isinstance(schedule_id, FeeScheduleId):
        raise ValueError("schedule_id must be FeeScheduleId")
    _require_identifier("fee schedule name", schedule_id.name)
    if not isinstance(schedule_id.venue, Venue):
        raise ValueError("fee schedule venue must be Venue")
    _require_aware_datetime("fee schedule effective_from", schedule_id.effective_from)
    if schedule_id.effective_to is not None:
        _require_aware_datetime("fee schedule effective_to", schedule_id.effective_to)
        if schedule_id.effective_to <= schedule_id.effective_from:
            raise ValueError("fee schedule effective_to must follow effective_from")
    _require_sha256("fee schedule artifact_sha256", schedule_id.artifact_sha256)
    for digest in schedule_id.supporting_artifact_sha256:
        _require_sha256("fee schedule supporting artifact", digest)
    fee_coefficient_for(schedule_id, FeeRole.TAKER)


def _require_identifier(name: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required")
    if value != value.strip() or not value.isascii():
        raise ValueError(f"{name} must be trimmed ASCII text")


def _require_nonempty_text(name: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required")
    if value != value.strip():
        raise ValueError(f"{name} must be trimmed text")


def _require_decimal(name: str, value: object) -> Decimal:
    if type(value) is not Decimal:
        raise ValueError(f"{name} must be Decimal")
    if not value.is_finite():
        raise ValueError(f"{name} must be finite")
    return value


def _decimal_text(value: Decimal) -> str:
    _require_decimal("value", value)
    if value == 0:
        return "0"
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _optional_decimal_text(value: Decimal | None) -> str | None:
    return _decimal_text(value) if value is not None else None


def _parse_decimal_text(name: str, value: object) -> Decimal:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be canonical Decimal text")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{name} must be canonical Decimal text") from exc
    if not parsed.is_finite() or _decimal_text(parsed) != value:
        raise ValueError(f"{name} must be canonical Decimal text")
    return parsed


def _parse_optional_decimal_text(name: str, value: object) -> Decimal | None:
    return _parse_decimal_text(name, value) if value is not None else None


def _require_aware_datetime(name: str, value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware datetime")
    return value


def _datetime_text(value: datetime) -> str:
    _require_aware_datetime("datetime", value)
    return value.isoformat()


def _parse_datetime_text(name: str, value: object) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be timezone-aware ISO datetime text")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be timezone-aware ISO datetime text") from exc
    _require_aware_datetime(name, parsed)
    if parsed.isoformat() != value:
        raise ValueError(f"{name} must be canonical ISO datetime text")
    return parsed


def _require_sha256(name: str, value: object) -> None:
    if not isinstance(value, str) or _SHA256_TEXT.fullmatch(value) is None:
        raise ValueError(f"{name} must be lowercase SHA-256 text")


def _required_sha256(name: str, value: object) -> str:
    _require_sha256(name, value)
    assert isinstance(value, str)
    return value


def _required_text(name: str, value: object) -> str:
    _require_identifier(name, value)
    assert isinstance(value, str)
    return value


def _required_nonempty_text(name: str, value: object) -> str:
    _require_nonempty_text(name, value)
    assert isinstance(value, str)
    return value


def _required_integer(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be integer")
    return value


def _parse_supporting_artifacts(value: object) -> tuple[str, ...]:
    if not isinstance(value, str):
        raise ValueError("fee schedule supporting artifacts must be canonical JSON")
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError("fee schedule supporting artifacts must be canonical JSON") from exc
    if not isinstance(decoded, list) or any(not isinstance(item, str) for item in decoded):
        raise ValueError("fee schedule supporting artifacts must be a string list")
    canonical = json.dumps(
        decoded,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
    )
    if canonical != value:
        raise ValueError("fee schedule supporting artifacts must be canonical JSON")
    for digest in decoded:
        _require_sha256("fee schedule supporting artifact", digest)
    return tuple(decoded)


def _row_value(row: Mapping[str, object] | sqlite3.Row, name: str) -> object:
    try:
        return row[name]
    except (IndexError, KeyError) as exc:
        raise ValueError(f"missing paper-accounting column: {name}") from exc
