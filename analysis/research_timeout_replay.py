"""Immutable, read-only replay records for research timeout exits.

The replay boundary validates the captured timeout input only. It never invokes
providers, adjudicators, or the research gate, and it cannot produce an
admission-eligible result.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence
from urllib.parse import quote


_SCHEMA_VERSION = 1
_DIAGNOSTIC_TABLE = "research_timeout_diagnostics"
_EVIDENCE_FIELD_COUNT = 18


@dataclass(frozen=True)
class ResearchTimeoutReplaySnapshot:
    """Canonical, immutable inputs captured at a timeout decision boundary."""

    schema_version: int
    research_run_id: str
    market_ticker: str
    contract_fingerprint: str
    timeout_stage: str
    configured_timeout_seconds: float
    remaining_budget_seconds: float
    observed_market_price: float | None
    yes_ask: float | None
    no_ask: float | None
    require_decision_grade: bool
    live_mode: bool
    counter_evidence_added: bool
    model_direction: str | None
    model_confidence: float | None
    estimated_probability_yes: float | None
    model_reason: str | None
    counterclaims: tuple[str, ...]
    open_questions: tuple[str, ...]
    queries: tuple[tuple[str, str, str], ...]
    evidence: tuple[tuple[object, ...], ...]

    def canonical_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "research_run_id": self.research_run_id,
            "market_ticker": self.market_ticker,
            "contract_fingerprint": self.contract_fingerprint,
            "timeout_stage": self.timeout_stage,
            "configured_timeout_seconds": self.configured_timeout_seconds,
            "remaining_budget_seconds": self.remaining_budget_seconds,
            "observed_market_price": self.observed_market_price,
            "yes_ask": self.yes_ask,
            "no_ask": self.no_ask,
            "require_decision_grade": self.require_decision_grade,
            "live_mode": self.live_mode,
            "counter_evidence_added": self.counter_evidence_added,
            "model_direction": self.model_direction,
            "model_confidence": self.model_confidence,
            "estimated_probability_yes": self.estimated_probability_yes,
            "model_reason": self.model_reason,
            "counterclaims": list(self.counterclaims),
            "open_questions": list(self.open_questions),
            "queries": [list(query) for query in self.queries],
            "evidence": [list(item) for item in self.evidence],
        }

    def canonical_json(self) -> str:
        return json.dumps(
            self.canonical_payload(),
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )

    def input_sha256(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    @classmethod
    def from_payload(cls, payload: object) -> "ResearchTimeoutReplaySnapshot":
        if not isinstance(payload, dict):
            raise ValueError("timeout diagnostic payload must be an object")
        return cls(
            schema_version=_required_int(payload, "schema_version"),
            research_run_id=_required_text(payload, "research_run_id"),
            market_ticker=_required_text(payload, "market_ticker"),
            contract_fingerprint=_required_text(payload, "contract_fingerprint"),
            timeout_stage=_required_text(payload, "timeout_stage"),
            configured_timeout_seconds=_required_float(
                payload,
                "configured_timeout_seconds",
                nonnegative=True,
            ),
            remaining_budget_seconds=_required_float(
                payload,
                "remaining_budget_seconds",
                nonnegative=True,
            ),
            observed_market_price=_optional_float(payload.get("observed_market_price")),
            yes_ask=_optional_float(payload.get("yes_ask")),
            no_ask=_optional_float(payload.get("no_ask")),
            require_decision_grade=_required_bool(payload, "require_decision_grade"),
            live_mode=_required_bool(payload, "live_mode"),
            counter_evidence_added=_required_bool(payload, "counter_evidence_added"),
            model_direction=_optional_text(payload.get("model_direction")),
            model_confidence=_optional_float(payload.get("model_confidence")),
            estimated_probability_yes=_optional_float(payload.get("estimated_probability_yes")),
            model_reason=_optional_text(payload.get("model_reason")),
            counterclaims=_text_tuple(payload.get("counterclaims"), "counterclaims"),
            open_questions=_text_tuple(payload.get("open_questions"), "open_questions"),
            queries=_query_tuple(payload.get("queries")),
            evidence=_evidence_tuple(payload.get("evidence")),
        )


@dataclass(frozen=True)
class ResearchTimeoutReplayResult:
    """Diagnostic-only replay outcome; it is intentionally not a gate verdict."""

    research_run_id: str | None
    replayable: bool
    reason: str | None
    timeout_stage: str | None
    expected_status: str | None
    skip_reason: str | None
    candidate_eligible: bool
    cache_eligible: bool
    admission_eligible: bool
    query_count: int
    evidence_count: int
    input_sha256: str | None


def capture_timeout_replay_snapshot(
    *,
    research_run_id: str,
    market_ticker: str,
    contract_fingerprint: str,
    timeout_stage: str,
    configured_timeout_seconds: float,
    remaining_budget_seconds: float,
    observed_market_price: float | None,
    yes_ask: float | None,
    no_ask: float | None,
    require_decision_grade: bool,
    live_mode: bool,
    counter_evidence_added: bool,
    model_direction: str | None,
    model_confidence: float | None,
    estimated_probability_yes: float | None,
    model_reason: str | None,
    counterclaims: Sequence[str],
    open_questions: Sequence[str],
    queries: Sequence[object],
    evidence: Sequence[object],
) -> ResearchTimeoutReplaySnapshot:
    """Freeze timeout inputs before final persistence can mutate surrounding state."""

    return ResearchTimeoutReplaySnapshot(
        schema_version=_SCHEMA_VERSION,
        research_run_id=_nonempty_text(research_run_id, "research_run_id"),
        market_ticker=_nonempty_text(market_ticker, "market_ticker"),
        contract_fingerprint=_nonempty_text(contract_fingerprint, "contract_fingerprint"),
        timeout_stage=_nonempty_text(timeout_stage, "timeout_stage"),
        configured_timeout_seconds=_finite_float(configured_timeout_seconds, nonnegative=True),
        remaining_budget_seconds=_finite_float(remaining_budget_seconds, nonnegative=True),
        observed_market_price=_optional_float(observed_market_price),
        yes_ask=_optional_float(yes_ask),
        no_ask=_optional_float(no_ask),
        require_decision_grade=bool(require_decision_grade),
        live_mode=bool(live_mode),
        counter_evidence_added=bool(counter_evidence_added),
        model_direction=_optional_text(model_direction),
        model_confidence=_optional_float(model_confidence),
        estimated_probability_yes=_optional_float(estimated_probability_yes),
        model_reason=_optional_text(model_reason),
        counterclaims=tuple(_nonempty_text(item, "counterclaim") for item in counterclaims),
        open_questions=tuple(_nonempty_text(item, "open_question") for item in open_questions),
        queries=tuple(
            (
                _text_attr(query, "query"),
                _text_attr(query, "query_intent"),
                _text_attr(query, "source_class"),
            )
            for query in queries
        ),
        evidence=tuple(_evidence_values(item) for item in evidence),
    )


def load_timeout_replay_snapshot(
    db_path: Path,
    research_run_id: str,
) -> ResearchTimeoutReplaySnapshot | None:
    """Load one timeout snapshot using a SQLite read-only connection."""

    row = _load_timeout_diagnostic_row(db_path, research_run_id)
    if row is None:
        return None
    snapshot = _snapshot_from_row(row)
    return snapshot


def replay_persisted_timeout(
    db_path: Path,
    research_run_id: str,
) -> ResearchTimeoutReplayResult:
    """Validate one captured timeout without invoking runtime research machinery."""

    try:
        row = _load_timeout_diagnostic_row(db_path, research_run_id)
    except OSError:
        row = None
    if row is None:
        return _not_replayable(research_run_id, "timeout_diagnostic_unavailable")
    try:
        snapshot = _snapshot_from_row(row)
    except ValueError as exc:
        return _not_replayable(research_run_id, str(exc))
    if str(row["skip_reason"] or "") != "research_timeout":
        return _not_replayable(research_run_id, "timeout_diagnostic_run_mismatch")
    if snapshot.research_run_id != research_run_id:
        return _not_replayable(research_run_id, "timeout_diagnostic_run_mismatch")
    if snapshot.market_ticker != str(row["market_ticker"] or ""):
        return _not_replayable(research_run_id, "timeout_diagnostic_ticker_mismatch")
    if snapshot.timeout_stage != str(row["timeout_stage"] or ""):
        return _not_replayable(research_run_id, "timeout_diagnostic_stage_mismatch")
    if snapshot.schema_version != _SCHEMA_VERSION:
        return _not_replayable(research_run_id, "timeout_diagnostic_schema_unsupported")
    return replay_timeout_snapshot(snapshot)


def replay_timeout_snapshot(
    snapshot: ResearchTimeoutReplaySnapshot,
) -> ResearchTimeoutReplayResult:
    """Return the invariant non-promoting disposition for a valid timeout input."""

    if snapshot.schema_version != _SCHEMA_VERSION:
        return _not_replayable(
            snapshot.research_run_id,
            "timeout_diagnostic_schema_unsupported",
        )
    return ResearchTimeoutReplayResult(
        research_run_id=snapshot.research_run_id,
        replayable=True,
        reason=None,
        timeout_stage=snapshot.timeout_stage,
        expected_status="continue_researching",
        skip_reason="research_timeout",
        candidate_eligible=False,
        cache_eligible=False,
        admission_eligible=False,
        query_count=len(snapshot.queries),
        evidence_count=len(snapshot.evidence),
        input_sha256=snapshot.input_sha256(),
    )


def _load_timeout_diagnostic_row(
    db_path: Path,
    research_run_id: str,
) -> sqlite3.Row | None:
    if not db_path.is_file():
        return None
    uri = f"file:{quote(str(db_path.resolve()), safe='/')}?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only = ON")
            return conn.execute(
                f"""
                SELECT
                    diagnostic.research_run_id,
                    diagnostic.market_ticker,
                    diagnostic.timeout_stage,
                    diagnostic.input_sha256,
                    diagnostic.snapshot_json,
                    run.skip_reason
                FROM {_DIAGNOSTIC_TABLE} AS diagnostic
                JOIN research_runs AS run
                  ON run.research_run_id = diagnostic.research_run_id
                WHERE diagnostic.research_run_id = ?
                """,
                (research_run_id,),
            ).fetchone()
    except sqlite3.OperationalError:
        return None


def _snapshot_from_row(row: sqlite3.Row) -> ResearchTimeoutReplaySnapshot:
    snapshot_json = str(row["snapshot_json"] or "")
    try:
        payload = json.loads(snapshot_json)
    except json.JSONDecodeError as exc:
        raise ValueError("timeout_diagnostic_invalid_snapshot") from exc
    try:
        snapshot = ResearchTimeoutReplaySnapshot.from_payload(payload)
    except (TypeError, ValueError):
        raise ValueError("timeout_diagnostic_invalid_snapshot") from None
    if snapshot.canonical_json() != snapshot_json:
        raise ValueError("timeout_diagnostic_noncanonical_payload")
    digest = str(row["input_sha256"] or "")
    if digest != snapshot.input_sha256():
        raise ValueError("timeout_diagnostic_digest_mismatch")
    return snapshot


def _not_replayable(
    research_run_id: str | None,
    reason: str,
) -> ResearchTimeoutReplayResult:
    return ResearchTimeoutReplayResult(
        research_run_id=research_run_id,
        replayable=False,
        reason=reason,
        timeout_stage=None,
        expected_status=None,
        skip_reason=None,
        candidate_eligible=False,
        cache_eligible=False,
        admission_eligible=False,
        query_count=0,
        evidence_count=0,
        input_sha256=None,
    )


def _evidence_values(item: object) -> tuple[object, ...]:
    return (
        _text_attr(item, "source_class"),
        _text_attr(item, "source_name"),
        _text_attr(item, "source_url"),
        _text_attr(item, "title"),
        _text_attr(item, "snippet"),
        _text_attr(item, "claim_type"),
        _text_attr(item, "supports_direction"),
        _finite_float(getattr(item, "supports_confidence", 0.0)),
        _optional_text(getattr(item, "published_at", None)),
        _optional_text(getattr(item, "retrieved_at", None)),
        _optional_text(getattr(item, "metric_name", None)),
        _optional_float(getattr(item, "metric_value", None)),
        _optional_text(getattr(item, "metric_unit", None)),
        _optional_float(getattr(item, "extraction_confidence", None)),
        _optional_text(getattr(item, "inserted_at", None)),
        _optional_text(getattr(item, "contract_fingerprint", None)),
        _optional_text(getattr(item, "aggregator_url", None)),
        _optional_text(getattr(item, "available_at", None)),
    )


def _text_attr(item: object, name: str) -> str:
    return _nonempty_text(getattr(item, name, None), name)


def _required_text(payload: dict[str, object], name: str) -> str:
    return _nonempty_text(payload.get(name), name)


def _nonempty_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty text")
    return value.strip()


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("optional text field must be text or null")
    return value


def _required_bool(payload: dict[str, object], name: str) -> bool:
    value = payload.get(name)
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be boolean")
    return value


def _required_int(payload: dict[str, object], name: str) -> int:
    value = payload.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be integer")
    return value


def _required_float(
    payload: dict[str, object],
    name: str,
    *,
    nonnegative: bool = False,
) -> float:
    if name not in payload:
        raise ValueError(f"{name} is required")
    return _finite_float(payload[name], nonnegative=nonnegative)


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    return _finite_float(value)


def _finite_float(value: object, *, nonnegative: bool = False) -> float:
    if isinstance(value, bool):
        raise ValueError("numeric field cannot be boolean")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("numeric field must be finite") from exc
    if not math.isfinite(number) or (nonnegative and number < 0):
        raise ValueError("numeric field must be finite and non-negative")
    return number


def _text_tuple(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be a list")
    return tuple(_nonempty_text(item, name) for item in value)


def _query_tuple(value: object) -> tuple[tuple[str, str, str], ...]:
    if not isinstance(value, list):
        raise ValueError("queries must be a list")
    result: list[tuple[str, str, str]] = []
    for item in value:
        if not isinstance(item, list) or len(item) != 3:
            raise ValueError("query entry is invalid")
        result.append(
            (
                _nonempty_text(item[0], "query"),
                _nonempty_text(item[1], "query_intent"),
                _nonempty_text(item[2], "source_class"),
            )
        )
    return tuple(result)


def _evidence_tuple(value: object) -> tuple[tuple[object, ...], ...]:
    if not isinstance(value, list):
        raise ValueError("evidence must be a list")
    result: list[tuple[object, ...]] = []
    for item in value:
        if not isinstance(item, list) or len(item) != _EVIDENCE_FIELD_COUNT:
            raise ValueError("evidence entry is invalid")
        result.append(
            (
                _nonempty_text(item[0], "source_class"),
                _nonempty_text(item[1], "source_name"),
                _nonempty_text(item[2], "source_url"),
                _nonempty_text(item[3], "title"),
                _nonempty_text(item[4], "snippet"),
                _nonempty_text(item[5], "claim_type"),
                _nonempty_text(item[6], "supports_direction"),
                _finite_float(item[7]),
                _optional_text(item[8]),
                _optional_text(item[9]),
                _optional_text(item[10]),
                _optional_float(item[11]),
                _optional_text(item[12]),
                _optional_float(item[13]),
                _optional_text(item[14]),
                _optional_text(item[15]),
                _optional_text(item[16]),
                _optional_text(item[17]),
            )
        )
    return tuple(result)
