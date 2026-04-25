"""Runtime overrides reader for kalshi-bot.

Reads data/runtime_overrides.yaml at startup and on hot-reload (via
tasks/runtime_overrides_task.py). Exposes typed query methods consumed
by analysis/ and feeds/ modules in place of static config-set lookups.

See docs/superpowers/specs/2026-04-24-llm-governance-agent-design.md
sections 6 (data contracts) and 7 (Phase 1 design).

Phase 1 boundaries:
  - This module only READS the YAML file. The agent (Phase 2+) writes it.
  - The atomic-write helper here is provided so tests + future agent
    can produce valid files; nothing in the bot writes during Phase 1.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

# Schema versions this module knows how to read. Forward-incompatible
# bumps (rare) require a code update before reading the new file.
SUPPORTED_SCHEMA_VERSIONS: frozenset[int] = frozenset({1})

# The agent writes decision IDs in this exact format. Anything else
# is a corruption signal -- reject on read.
_DECISION_ID_RE = re.compile(r"^gd_\d{4}-\d{2}-\d{2}_\d{4}$")


@dataclass(frozen=True)
class PredictedEffect:
    """Mandatory prediction attached to every decision (per spec §6.1)."""

    metric: str
    baseline: float
    predicted_post_change: float
    evaluate_at: datetime

    def __post_init__(self) -> None:
        if not self.metric:
            raise ValueError("metric is required and must be non-empty")


@dataclass(frozen=True)
class _OverrideBase:
    """Common fields for all override types. Validated in __post_init__."""

    reason: str
    confidence: float
    decided_at: datetime
    decided_by: str
    decision_id: str
    expires_at: datetime | None
    predicted_effect: PredictedEffect

    def _validate_common(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(
                f"confidence must be in [0, 1], got {self.confidence}"
            )
        if not _DECISION_ID_RE.match(self.decision_id):
            raise ValueError(
                f"decision_id must match {_DECISION_ID_RE.pattern}, got {self.decision_id!r}"
            )


@dataclass(frozen=True)
class DisabledSource(_OverrideBase):
    source: str = ""

    def __post_init__(self) -> None:
        if not self.source:
            raise ValueError("source is required and must be non-empty")
        self._validate_common()


@dataclass(frozen=True)
class DisabledKeyword(_OverrideBase):
    keyword: str = ""

    def __post_init__(self) -> None:
        if not self.keyword:
            raise ValueError("keyword is required and must be non-empty")
        self._validate_common()


@dataclass(frozen=True)
class ThresholdOverride(_OverrideBase):
    path: str = ""
    value: Any = None

    def __post_init__(self) -> None:
        if not self.path:
            raise ValueError("path is required and must be non-empty")
        if self.value is None:
            raise ValueError(
                "value is required and must not be None; the = None default "
                "exists only to satisfy dataclass field-ordering and is not a "
                "valid runtime value"
            )
        self._validate_common()


Mode = Literal["shadow", "real"]


@dataclass
class OverridesState:
    """Full in-memory representation of the YAML overrides file.

    Phase 1 reads this; Phase 2+ writes it. The bot consults
    `applied_*` fields only; `proposed_*` are human/agent review queue
    that the bot ignores.
    """

    version: int
    updated_at: datetime
    updated_by: str
    mode: Mode
    applied_disabled_sources: list[DisabledSource] = field(default_factory=list)
    applied_disabled_keywords: list[DisabledKeyword] = field(default_factory=list)
    applied_threshold_overrides: list[ThresholdOverride] = field(default_factory=list)
    proposed_disabled_sources: list[DisabledSource] = field(default_factory=list)
    proposed_disabled_keywords: list[DisabledKeyword] = field(default_factory=list)
    proposed_threshold_overrides: list[ThresholdOverride] = field(default_factory=list)
    last_applied_batch: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.version not in SUPPORTED_SCHEMA_VERSIONS:
            raise ValueError(
                f"version must be one of {sorted(SUPPORTED_SCHEMA_VERSIONS)}, "
                f"got {self.version}"
            )
        if self.mode not in ("shadow", "real"):
            raise ValueError(f"mode must be 'shadow' or 'real', got {self.mode!r}")


def _parse_iso(value: Any, field_name: str) -> datetime:
    """Parse an ISO 8601 timestamp string. Accepts both '+00:00' and 'Z' UTC suffixes."""
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be an ISO 8601 string, got {type(value).__name__}")
    s = value.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except ValueError as exc:
        raise ValueError(f"{field_name}: invalid ISO 8601 timestamp {value!r}") from exc


def _parse_optional_iso(value: Any, field_name: str) -> datetime | None:
    if value is None:
        return None
    return _parse_iso(value, field_name)


def _parse_predicted_effect(data: dict, ctx: str) -> PredictedEffect:
    if not isinstance(data, dict):
        raise ValueError(f"{ctx}.predicted_effect must be a mapping")
    try:
        return PredictedEffect(
            metric=str(data["metric"]),
            baseline=float(data["baseline"]),
            predicted_post_change=float(data["predicted_post_change"]),
            evaluate_at=_parse_iso(data["evaluate_at"], f"{ctx}.predicted_effect.evaluate_at"),
        )
    except KeyError as exc:
        raise ValueError(f"{ctx}.predicted_effect: missing required field {exc.args[0]!r}") from exc


def _parse_disabled_source(data: dict, idx: int, section: str) -> DisabledSource:
    ctx = f"{section}.disabled_sources[{idx}]"
    if not isinstance(data, dict):
        raise ValueError(f"{ctx} must be a mapping")
    try:
        return DisabledSource(
            source=str(data["source"]),
            reason=str(data.get("reason", "")),
            confidence=float(data["confidence"]),
            decided_at=_parse_iso(data["decided_at"], f"{ctx}.decided_at"),
            decided_by=str(data["decided_by"]),
            decision_id=str(data["decision_id"]),
            expires_at=_parse_optional_iso(data.get("expires_at"), f"{ctx}.expires_at"),
            predicted_effect=_parse_predicted_effect(data["predicted_effect"], ctx),
        )
    except KeyError as exc:
        raise ValueError(f"{ctx}: missing required field {exc.args[0]!r}") from exc


def _parse_disabled_keyword(data: dict, idx: int, section: str) -> DisabledKeyword:
    ctx = f"{section}.disabled_keywords[{idx}]"
    if not isinstance(data, dict):
        raise ValueError(f"{ctx} must be a mapping")
    try:
        return DisabledKeyword(
            keyword=str(data["keyword"]),
            reason=str(data.get("reason", "")),
            confidence=float(data["confidence"]),
            decided_at=_parse_iso(data["decided_at"], f"{ctx}.decided_at"),
            decided_by=str(data["decided_by"]),
            decision_id=str(data["decision_id"]),
            expires_at=_parse_optional_iso(data.get("expires_at"), f"{ctx}.expires_at"),
            predicted_effect=_parse_predicted_effect(data["predicted_effect"], ctx),
        )
    except KeyError as exc:
        raise ValueError(f"{ctx}: missing required field {exc.args[0]!r}") from exc


def _parse_threshold_override(data: dict, idx: int, section: str) -> ThresholdOverride:
    ctx = f"{section}.threshold_overrides[{idx}]"
    if not isinstance(data, dict):
        raise ValueError(f"{ctx} must be a mapping")
    try:
        return ThresholdOverride(
            path=str(data["path"]),
            value=data["value"],
            reason=str(data.get("reason", "")),
            confidence=float(data["confidence"]),
            decided_at=_parse_iso(data["decided_at"], f"{ctx}.decided_at"),
            decided_by=str(data["decided_by"]),
            decision_id=str(data["decision_id"]),
            expires_at=_parse_optional_iso(data.get("expires_at"), f"{ctx}.expires_at"),
            predicted_effect=_parse_predicted_effect(data["predicted_effect"], ctx),
        )
    except KeyError as exc:
        raise ValueError(f"{ctx}: missing required field {exc.args[0]!r}") from exc


def parse_yaml_to_state(data: dict) -> OverridesState:
    """Parse a YAML-loaded dict into a typed OverridesState.

    Raises ValueError on schema violations with a path indicating where
    the failure occurred (e.g., "applied.disabled_sources[0].confidence").
    Unknown top-level sections are ignored for forward-compat.
    """
    if not isinstance(data, dict):
        raise ValueError(f"top-level YAML must be a mapping, got {type(data).__name__}")

    try:
        version = int(data["version"])
        updated_at = _parse_iso(data["updated_at"], "updated_at")
        updated_by = str(data["updated_by"])
        mode = str(data["mode"])
    except KeyError as exc:
        raise ValueError(f"missing required top-level field {exc.args[0]!r}") from exc

    applied = data.get("applied") or {}
    proposed = data.get("proposed") or {}

    if not isinstance(applied, dict):
        raise ValueError("applied must be a mapping")
    if not isinstance(proposed, dict):
        raise ValueError("proposed must be a mapping")

    return OverridesState(
        version=version,
        updated_at=updated_at,
        updated_by=updated_by,
        mode=mode,  # type: ignore[arg-type]  # validated in OverridesState.__post_init__
        applied_disabled_sources=[
            _parse_disabled_source(d, i, "applied")
            for i, d in enumerate(applied.get("disabled_sources") or [])
        ],
        applied_disabled_keywords=[
            _parse_disabled_keyword(d, i, "applied")
            for i, d in enumerate(applied.get("disabled_keywords") or [])
        ],
        applied_threshold_overrides=[
            _parse_threshold_override(d, i, "applied")
            for i, d in enumerate(applied.get("threshold_overrides") or [])
        ],
        proposed_disabled_sources=[
            _parse_disabled_source(d, i, "proposed")
            for i, d in enumerate(proposed.get("disabled_sources") or [])
        ],
        proposed_disabled_keywords=[
            _parse_disabled_keyword(d, i, "proposed")
            for i, d in enumerate(proposed.get("disabled_keywords") or [])
        ],
        proposed_threshold_overrides=[
            _parse_threshold_override(d, i, "proposed")
            for i, d in enumerate(proposed.get("threshold_overrides") or [])
        ],
        last_applied_batch=data.get("last_applied_batch"),
    )
