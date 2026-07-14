"""Unwired worker for durable settlement outbox database consumers."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import logging
import math
from numbers import Real
from pathlib import Path
from typing import Any

from tasks.stats.source_credibility import record_outcome_in_transaction
from trading.settlement_store import PendingRequirement, SettlementStore


log = logging.getLogger(__name__)

_EVENT_KIND = "paper_trade_settled"
_EVENT_VERSION = 1
_KNOWN_CONSUMERS = frozenset(
    {
        "paper_trade_log",
        "source_credibility",
        "calibration_state",
        "keyword_outcomes",
    }
)
_SUPPORTED_CONSUMERS = frozenset(
    {"paper_trade_log", "source_credibility", "keyword_outcomes"}
)
_CALIBRATION_CONSUMER = "calibration_state"
_CALIBRATION_LANES = ("fast", "accumulation", "structural")
_SHA256_LENGTH = 64
_REQUIRED_FIELDS = frozenset(
    {
        "outbox_id",
        "event_version",
        "event_kind",
        "observation_sha256",
        "trade_id",
        "ticker",
        "venue",
        "venue_market_id",
        "alias",
        "outcome",
        "side",
        "resolved_yes",
        "terminal_state",
        "won",
        "settled_at",
        "signal_source",
        "series_ticker",
        "entry_ts",
        "estimated_prob",
        "entry_price_cents",
        "cost_dollars",
        "llm_magnitude",
        "llm_confidence",
        "keyword_outcomes",
        "lane_estimates",
        "gross_payout_cents",
        "gross_pnl_cents",
    }
)


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _event_outbox_id(
    event_version: int,
    event_kind: str,
    observation_sha256: str,
    trade_id: str,
) -> str:
    encoded = _canonical_json(
        {
            "event_kind": event_kind,
            "event_version": event_version,
            "observation_sha256": observation_sha256,
            "trade_id": trade_id,
        }
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _result_sha256(outbox_id: str, consumer_name: str) -> str:
    return hashlib.sha256(f"{outbox_id}:{consumer_name}".encode()).hexdigest()


def _require_string(payload: dict[str, Any], field: str) -> str:
    value = payload[field]
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _require_number(payload: dict[str, Any], field: str) -> float:
    value = payload[field]
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{field} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field} must be finite")
    return result


def _require_timestamp(payload: dict[str, Any], field: str) -> str:
    value = _require_string(payload, field)
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value


def _validate_payload(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    missing = _REQUIRED_FIELDS - payload.keys()
    if missing:
        raise ValueError(f"payload missing fields: {sorted(missing)}")
    if type(payload["event_version"]) is not int or payload["event_version"] != 1:
        raise ValueError("unsupported event version")
    if payload["event_kind"] != _EVENT_KIND:
        raise ValueError("unsupported event kind")
    for field in (
        "outbox_id",
        "observation_sha256",
        "trade_id",
        "ticker",
        "venue",
        "venue_market_id",
        "alias",
        "signal_source",
        "series_ticker",
    ):
        _require_string(payload, field)
    for field in ("outbox_id", "observation_sha256"):
        value = payload[field]
        if len(value) != _SHA256_LENGTH or any(ch not in "0123456789abcdef" for ch in value):
            raise ValueError(f"{field} must be lowercase SHA-256")
    if payload["venue"] not in {"kalshi", "polymarket_us"}:
        raise ValueError("invalid venue")
    if payload["outcome"] not in {"yes", "no", "void"}:
        raise ValueError("invalid outcome")
    if payload["side"] not in {"yes", "no"}:
        raise ValueError("invalid side")
    if payload["terminal_state"] not in {"won", "lost", "void"}:
        raise ValueError("invalid terminal state")
    _require_timestamp(payload, "settled_at")
    _require_timestamp(payload, "entry_ts")
    for field in ("estimated_prob", "entry_price_cents", "cost_dollars"):
        _require_number(payload, field)
    if payload["llm_magnitude"] is not None and not isinstance(
        payload["llm_magnitude"], str
    ):
        raise ValueError("llm_magnitude must be a string or null")
    if payload["llm_confidence"] is not None:
        _require_number(payload, "llm_confidence")
    for field in ("gross_payout_cents", "gross_pnl_cents"):
        value = _require_string(payload, field)
        try:
            if not Decimal(value).is_finite():
                raise ValueError(f"{field} must be finite")
        except InvalidOperation as exc:
            raise ValueError(f"{field} must be decimal text") from exc

    resolved_yes = payload["resolved_yes"]
    won = payload["won"]
    if payload["outcome"] == "void":
        if resolved_yes is not None or won is not None or payload["terminal_state"] != "void":
            raise ValueError("void outcome fields disagree")
    else:
        if type(resolved_yes) is not bool or type(won) is not bool:
            raise ValueError("directional result fields must be boolean")
        expected_resolved_yes = payload["outcome"] == "yes"
        if resolved_yes != expected_resolved_yes:
            raise ValueError("outcome and resolved_yes disagree")
        expected_won = (payload["side"] == "yes") == resolved_yes
        if won != expected_won:
            raise ValueError("chosen-side result disagrees")
        if payload["terminal_state"] != ("won" if won else "lost"):
            raise ValueError("terminal state disagrees")

    keyword_outcomes = payload["keyword_outcomes"]
    if not isinstance(keyword_outcomes, list):
        raise ValueError("keyword_outcomes must be a list")
    for item in keyword_outcomes:
        if not isinstance(item, dict) or set(item) != {"keyword", "direction", "correct"}:
            raise ValueError("invalid keyword outcome")
        if not isinstance(item["keyword"], str) or not item["keyword"]:
            raise ValueError("invalid keyword")
        if item["direction"] not in {"yes", "no"}:
            raise ValueError("invalid keyword direction")
        if payload["outcome"] == "void":
            if item["correct"] is not None:
                raise ValueError("void keyword correctness must be null")
        else:
            if type(item["correct"]) is not bool:
                raise ValueError("directional keyword correctness must be boolean")
            expected_correct = (item["direction"] == "yes") == resolved_yes
            if item["correct"] != expected_correct:
                raise ValueError("keyword correctness disagrees")

    lanes = payload["lane_estimates"]
    if not isinstance(lanes, dict) or set(lanes) != {
        "fast",
        "accumulation",
        "structural",
    }:
        raise ValueError("invalid lane estimates")
    for lane, value in lanes.items():
        if value is not None:
            _require_number({lane: value}, lane)
    return payload


class SettlementOutboxTask:
    def __init__(
        self,
        *,
        db_path: Path,
        calibration_task: object,
        trade_logger: object,
        clock: Callable[[], datetime],
        token_factory: Callable[[], str],
        lease_seconds: int,
        fault_hook: Callable[[str], None] | None = None,
    ) -> None:
        self._db_path = db_path
        self._calibration_task = calibration_task
        self._trade_logger = trade_logger
        self._clock = clock
        self._token_factory = token_factory
        self._lease_seconds = lease_seconds
        self._fault_hook = fault_hook
        self._optimistic_calibration: dict[
            str,
            tuple[str, dict[str, Any]],
        ] = {}
        self._dispatched_calibration_outbox_ids: set[str] = set()

    def _fault(self, stage: str) -> None:
        if self._fault_hook is not None:
            self._fault_hook(stage)

    @staticmethod
    def _validated_event(
        store: SettlementStore,
        requirement: PendingRequirement,
    ) -> dict[str, Any]:
        if requirement.consumer_name not in _KNOWN_CONSUMERS:
            raise ValueError("unknown settlement consumer")
        row = store.connection.execute(
            """
            SELECT outbox_id, event_version, event_kind, observation_sha256,
                   trade_id, payload_json, created_at
            FROM paper_settlement_outbox
            WHERE outbox_id=?
            """,
            (requirement.outbox_id,),
        ).fetchone()
        if row is None:
            raise ValueError("settlement outbox row is missing")
        try:
            payload = _validate_payload(json.loads(row["payload_json"]))
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError("invalid settlement payload JSON") from exc
        comparisons = {
            "outbox_id": row["outbox_id"],
            "event_version": row["event_version"],
            "event_kind": row["event_kind"],
            "observation_sha256": row["observation_sha256"],
            "trade_id": row["trade_id"],
        }
        if any(payload[field] != value for field, value in comparisons.items()):
            raise ValueError("settlement payload disagrees with outer row")
        if requirement.event_version != row["event_version"]:
            raise ValueError("requirement event version disagrees")
        if requirement.event_kind != row["event_kind"]:
            raise ValueError("requirement event kind disagrees")
        if row["created_at"] != payload["settled_at"]:
            raise ValueError("settlement timestamp disagrees")
        expected_outbox_id = _event_outbox_id(
            row["event_version"],
            row["event_kind"],
            row["observation_sha256"],
            row["trade_id"],
        )
        if row["outbox_id"] != expected_outbox_id:
            raise ValueError("settlement outbox identity is invalid")
        return payload

    def _apply(
        self,
        connection,
        requirement: PendingRequirement,
        payload: dict[str, Any],
    ) -> None:
        self._fault("before_effect")
        if requirement.consumer_name == "paper_trade_log":
            pnl_dollars = float(Decimal(payload["gross_pnl_cents"]) / 100)
            settled_at = payload["settled_at"]
            self._trade_logger.log_paper_resolution(
                trade_id=payload["trade_id"],
                ticker=payload["ticker"],
                resolved_yes=payload["resolved_yes"],
                terminal_state=payload["terminal_state"],
                pnl_dollars=pnl_dollars,
                bankroll_delta_dollars=float(
                    Decimal(payload["gross_payout_cents"]) / 100
                ),
                venue=payload["venue"],
                outbox_id=payload["outbox_id"],
                ts=settled_at,
            )
            if payload["outcome"] != "void":
                estimated_probability = float(payload["estimated_prob"])
                if payload["side"] == "no":
                    estimated_probability = 1.0 - estimated_probability
                self._trade_logger.log_calibration_observation(
                    trade_id=payload["trade_id"],
                    ticker=payload["ticker"],
                    market_prefix=payload["series_ticker"],
                    side=payload["side"],
                    estimated_probability=estimated_probability,
                    realized_outcome=int(payload["won"]),
                    entry_price_cents=float(payload["entry_price_cents"]),
                    pnl_dollars=pnl_dollars,
                    cost_dollars=float(payload["cost_dollars"]),
                    llm_magnitude=payload["llm_magnitude"],
                    llm_confidence=payload["llm_confidence"],
                    signal_source=payload["signal_source"],
                    ts_entry=payload["entry_ts"],
                    ts_resolved=settled_at,
                    outbox_id=payload["outbox_id"],
                    ts=settled_at,
                )
                final_resolution = 1.0 if payload["resolved_yes"] else 0.0
                for lane in ("fast", "accumulation", "structural"):
                    lane_estimate = payload["lane_estimates"][lane]
                    if lane_estimate is None:
                        continue
                    self._trade_logger.log_calibration_check(
                        market_ticker=payload["ticker"],
                        lane=lane,
                        lane_estimate=float(lane_estimate),
                        final_resolution=final_resolution,
                        error=abs(float(lane_estimate) - final_resolution),
                        venue=payload["venue"],
                        outbox_id=payload["outbox_id"],
                        ts=settled_at,
                    )
        elif requirement.consumer_name == "source_credibility":
            record_outcome_in_transaction(
                connection,
                source=payload["signal_source"],
                was_correct=payload["won"],
                updated_at=payload["settled_at"],
            )
        elif requirement.consumer_name == "keyword_outcomes":
            for item in payload["keyword_outcomes"]:
                connection.execute(
                    """
                    INSERT INTO keyword_outcomes (
                        trade_id, ticker, series_ticker, keyword, direction,
                        market_side, resolved_yes, correct, ts
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        payload["trade_id"],
                        payload["ticker"],
                        payload["series_ticker"],
                        item["keyword"],
                        item["direction"],
                        payload["side"],
                        int(payload["resolved_yes"]),
                        int(item["correct"]),
                        payload["settled_at"],
                    ),
                )
        elif requirement.consumer_name == _CALIBRATION_CONSUMER:
            pass
        else:
            raise ValueError("unsupported settlement consumer")
        self._fault("after_effect")

    @staticmethod
    def _completed_calibration_requirements(
        store: SettlementStore,
    ) -> tuple[PendingRequirement, ...]:
        rows = store.connection.execute(
            """
            SELECT r.outbox_id, r.consumer_name, o.event_version, o.event_kind,
                   o.payload_json, o.created_at
            FROM paper_settlement_consumer_receipts AS receipt
            JOIN paper_settlement_outbox_requirements AS r
              ON r.outbox_id = receipt.outbox_id
             AND r.consumer_name = receipt.consumer_name
            JOIN paper_settlement_outbox AS o ON o.outbox_id = r.outbox_id
            WHERE receipt.consumer_name=?
            """,
            (_CALIBRATION_CONSUMER,),
        ).fetchall()
        return tuple(PendingRequirement(*tuple(row)) for row in rows)

    @staticmethod
    def _calibration_checks(payload: dict[str, Any]) -> tuple[dict[str, object], ...]:
        if payload["outcome"] == "void":
            return ()
        final_resolution = 1.0 if payload["resolved_yes"] else 0.0
        checks: list[dict[str, object]] = []
        for lane in _CALIBRATION_LANES:
            lane_estimate = payload["lane_estimates"][lane]
            if lane_estimate is None:
                continue
            estimate = float(lane_estimate)
            checks.append(
                {
                    "market_ticker": payload["ticker"],
                    "lane": lane,
                    "lane_estimate": estimate,
                    "final_resolution": final_resolution,
                    "error": abs(estimate - final_resolution),
                    "outbox_id": payload["outbox_id"],
                }
            )
        return tuple(checks)

    async def _rebuild_calibration_state(
        self,
        store: SettlementStore,
        *,
        current: tuple[PendingRequirement, dict[str, Any]] | None = None,
        now: datetime | None = None,
    ) -> None:
        completed_payloads = {
            requirement.outbox_id: self._validated_event(store, requirement)
            for requirement in self._completed_calibration_requirements(store)
        }
        payloads = dict(completed_payloads)
        for outbox_id in completed_payloads:
            self._optimistic_calibration.pop(outbox_id, None)
        if now is not None:
            for outbox_id, (claim_token, payload) in tuple(
                self._optimistic_calibration.items()
            ):
                claim = store.connection.execute(
                    """
                    SELECT claim_token FROM paper_settlement_delivery_claims
                    WHERE consumer_name=? AND outbox_id=?
                    """,
                    (_CALIBRATION_CONSUMER, outbox_id),
                ).fetchone()
                if (
                    claim is not None
                    and claim["claim_token"] == claim_token
                    and store.claim_state(
                        _CALIBRATION_CONSUMER,
                        outbox_id,
                        now=now,
                    )
                    == "active"
                ):
                    payloads[outbox_id] = payload
                else:
                    self._optimistic_calibration.pop(outbox_id, None)
        if current is not None:
            requirement, payload = current
            payloads[requirement.outbox_id] = payload

        current_outbox_id = current[0].outbox_id if current is not None else None
        apply_current_live = (
            current_outbox_id is not None
            and current_outbox_id not in self._dispatched_calibration_outbox_ids
        )
        replay_payloads = (
            {
                outbox_id: payload
                for outbox_id, payload in payloads.items()
                if outbox_id != current_outbox_id
            }
            if apply_current_live
            else payloads
        )
        ordered_replay_payloads = sorted(
            replay_payloads.values(),
            key=lambda payload: (payload["settled_at"], payload["trade_id"]),
        )
        checks = tuple(
            check
            for payload in ordered_replay_payloads
            for check in self._calibration_checks(payload)
        )
        await self._calibration_task.replace_calibration_checks(checks)
        for payload in ordered_replay_payloads:
            outbox_id = payload["outbox_id"]
            if outbox_id in self._dispatched_calibration_outbox_ids:
                continue
            for check in self._calibration_checks(payload):
                await self._calibration_task.record_calibration_check(
                    market_ticker=str(check["market_ticker"]),
                    lane=str(check["lane"]),
                    lane_estimate=float(check["lane_estimate"]),
                    final_resolution=float(check["final_resolution"]),
                    error=float(check["error"]),
                    outbox_id=str(check["outbox_id"]),
                )
            self._dispatched_calibration_outbox_ids.add(str(outbox_id))

        if apply_current_live and current is not None:
            for check in self._calibration_checks(current[1]):
                await self._calibration_task.record_calibration_check(
                    market_ticker=str(check["market_ticker"]),
                    lane=str(check["lane"]),
                    lane_estimate=float(check["lane_estimate"]),
                    final_resolution=float(check["final_resolution"]),
                    error=float(check["error"]),
                    outbox_id=str(check["outbox_id"]),
                )
            self._dispatched_calibration_outbox_ids.add(current_outbox_id)
            ordered_payloads = sorted(
                payloads.values(),
                key=lambda payload: (payload["settled_at"], payload["trade_id"]),
            )
            await self._calibration_task.replace_calibration_checks(
                tuple(
                    check
                    for payload in ordered_payloads
                    for check in self._calibration_checks(payload)
                )
            )

        self._dispatched_calibration_outbox_ids.difference_update(
            completed_payloads
        )

    async def run_once(self, *, limit: int = 100) -> int:
        processed = 0
        with SettlementStore(self._db_path) as store:
            try:
                await self._rebuild_calibration_state(
                    store,
                    now=(self._clock() if self._optimistic_calibration else None),
                )
            except Exception:  # noqa: BLE001 - keep corrupt replay fail-closed
                log.exception("Settlement calibration state rebuild failed")
            pending = store.pending_requirements()
            supported = tuple(
                requirement
                for requirement in pending
                if requirement.consumer_name in _SUPPORTED_CONSUMERS
            ) + tuple(
                requirement
                for requirement in pending
                if requirement.consumer_name == _CALIBRATION_CONSUMER
            )
            for requirement in supported[:limit]:
                try:
                    payload = self._validated_event(store, requirement)
                    if (
                        requirement.consumer_name not in _SUPPORTED_CONSUMERS
                        and requirement.consumer_name != _CALIBRATION_CONSUMER
                    ):
                        continue
                    self._fault("before_claim")
                    claim_token = self._token_factory()
                    now = self._clock()
                    if not store.acquire_claim(
                        requirement.consumer_name,
                        requirement.outbox_id,
                        claim_token=claim_token,
                        now=now,
                        lease_seconds=self._lease_seconds,
                    ):
                        continue
                    self._fault("after_claim")
                    if requirement.consumer_name == _CALIBRATION_CONSUMER:
                        self._optimistic_calibration[requirement.outbox_id] = (
                            claim_token,
                            payload,
                        )
                        await self._rebuild_calibration_state(
                            store,
                            current=(requirement, payload),
                            now=now,
                        )
                    store.complete_claim(
                        requirement.consumer_name,
                        requirement.outbox_id,
                        claim_token=claim_token,
                        processed_at=now,
                        result_sha256=_result_sha256(
                            requirement.outbox_id,
                            requirement.consumer_name,
                        ),
                        apply=lambda connection, current: self._apply(
                            connection,
                            current,
                            payload,
                        ),
                    )
                    if requirement.consumer_name == _CALIBRATION_CONSUMER:
                        self._optimistic_calibration.pop(requirement.outbox_id, None)
                    processed += 1
                except Exception:  # noqa: BLE001 - leave requirement pending for retry
                    log.exception(
                        "Settlement outbox consumer failed: %s/%s",
                        requirement.outbox_id,
                        requirement.consumer_name,
                    )
        return processed
