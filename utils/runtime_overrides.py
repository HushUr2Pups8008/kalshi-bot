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

import dataclasses as _dataclasses
import os as _os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone as _timezone
from pathlib import Path
from typing import Any, Literal

import yaml as _yaml

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


def _default_empty_state() -> OverridesState:
    """Return a baseline OverridesState used when no overrides file exists.

    Mode defaults to 'shadow' -- the safest default. Even if the agent
    later writes to this state, shadow mode means the bot ignores `applied`
    until a human flips mode by hand.
    """
    return OverridesState(
        version=1,
        updated_at=datetime.now(_timezone.utc),
        updated_by="default-empty",
        mode="shadow",
    )


def load_from_disk(path: Path) -> OverridesState:
    """Read a YAML overrides file and return a parsed OverridesState.

    Behavior contract:
      - Missing file: return _default_empty_state(). NOT an error.
      - Empty file: raise ValueError. The agent should never write empty.
      - Malformed YAML: raise ValueError wrapping the underlying YAMLError.
      - Schema violation: raise ValueError with the field path that failed.
    """
    if not path.exists():
        return _default_empty_state()

    text = path.read_text(encoding="utf-8")
    if not text.strip():
        raise ValueError(f"overrides file at {path} is empty")

    try:
        data = _yaml.safe_load(text)
    except _yaml.YAMLError as exc:
        raise ValueError(f"malformed YAML in {path}: {exc}") from exc

    if data is None:
        raise ValueError(f"overrides file at {path} parsed to None (empty document)")

    return parse_yaml_to_state(data)


def _state_to_yaml_dict(state: OverridesState) -> dict:
    """Serialize an OverridesState to a dict suitable for yaml.safe_dump."""

    def _override_to_dict(o: _OverrideBase) -> dict:
        d = _dataclasses.asdict(o)
        # asdict converts nested dataclasses too -- predicted_effect becomes
        # a dict already. Re-format datetimes to ISO 8601 with explicit UTC.
        d["decided_at"] = o.decided_at.isoformat()
        d["expires_at"] = (
            o.expires_at.isoformat() if o.expires_at is not None else None
        )
        d["predicted_effect"]["evaluate_at"] = o.predicted_effect.evaluate_at.isoformat()
        return d

    out: dict = {
        "version": state.version,
        "updated_at": state.updated_at.isoformat(),
        "updated_by": state.updated_by,
        "mode": state.mode,
        "applied": {
            "disabled_sources": [_override_to_dict(o) for o in state.applied_disabled_sources],
            "disabled_keywords": [_override_to_dict(o) for o in state.applied_disabled_keywords],
            "threshold_overrides": [_override_to_dict(o) for o in state.applied_threshold_overrides],
        },
        "proposed": {
            "disabled_sources": [_override_to_dict(o) for o in state.proposed_disabled_sources],
            "disabled_keywords": [_override_to_dict(o) for o in state.proposed_disabled_keywords],
            "threshold_overrides": [_override_to_dict(o) for o in state.proposed_threshold_overrides],
        },
    }
    if state.last_applied_batch is not None:
        out["last_applied_batch"] = state.last_applied_batch
    return out


def atomic_write_state(state: OverridesState, target: Path) -> None:
    """Write state to target via temp-file-and-rename.

    The bot reader doing a concurrent read at any point during this
    function will always see either the previous valid file or the new
    valid file -- never a half-written file. Achieved via os.replace
    (cross-platform atomic on Windows too).

    The temp file is created in the same directory as `target` so the
    rename is on the same filesystem (rename across filesystems is NOT
    atomic on POSIX).

    On rename failure the temp file is removed so it doesn't accumulate
    or confuse the next writer. The original target (if any) is left
    untouched; previous-content guarantee holds.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    payload = _state_to_yaml_dict(state)
    text = _yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
    tmp.write_text(text, encoding="utf-8")
    try:
        _os.replace(tmp, target)
    except OSError:
        # Best-effort cleanup; never mask the original error.
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def _not_expired(override: _OverrideBase, now: datetime) -> bool:
    return override.expires_at is None or override.expires_at > now


def filter_expired(state: OverridesState, now: datetime) -> OverridesState:
    """Return a new OverridesState with all expired entries removed.

    Does NOT mutate the input. An entry is expired iff its expires_at
    is non-None and <= now. Both `applied` and `proposed` sections are
    filtered (the agent should not see its own expired proposals as
    in-force when deciding the next batch).
    """
    return OverridesState(
        version=state.version,
        updated_at=state.updated_at,
        updated_by=state.updated_by,
        mode=state.mode,
        applied_disabled_sources=[
            o for o in state.applied_disabled_sources if _not_expired(o, now)
        ],
        applied_disabled_keywords=[
            o for o in state.applied_disabled_keywords if _not_expired(o, now)
        ],
        applied_threshold_overrides=[
            o for o in state.applied_threshold_overrides if _not_expired(o, now)
        ],
        proposed_disabled_sources=[
            o for o in state.proposed_disabled_sources if _not_expired(o, now)
        ],
        proposed_disabled_keywords=[
            o for o in state.proposed_disabled_keywords if _not_expired(o, now)
        ],
        proposed_threshold_overrides=[
            o for o in state.proposed_threshold_overrides if _not_expired(o, now)
        ],
        last_applied_batch=state.last_applied_batch,
    )


@dataclass
class StateDiff:
    """Difference between two OverridesStates -- used for change logging.

    `sources_added` / `sources_removed` are the source-name string sets.
    Same convention for keywords. Threshold overrides reported as
    (path, value) tuples since the value is the meaningful change.
    """

    sources_added: set[str] = field(default_factory=set)
    sources_removed: set[str] = field(default_factory=set)
    keywords_added: set[str] = field(default_factory=set)
    keywords_removed: set[str] = field(default_factory=set)
    thresholds_added: set[tuple[str, Any]] = field(default_factory=set)
    thresholds_removed: set[tuple[str, Any]] = field(default_factory=set)

    def is_empty(self) -> bool:
        return not (
            self.sources_added or self.sources_removed
            or self.keywords_added or self.keywords_removed
            or self.thresholds_added or self.thresholds_removed
        )


def _hashable(value: Any) -> Any:
    """Best-effort hashable form of an arbitrary YAML value."""
    if isinstance(value, (list, tuple)):
        return tuple(_hashable(v) for v in value)
    if isinstance(value, dict):
        return tuple(sorted((k, _hashable(v)) for k, v in value.items()))
    try:
        hash(value)
        return value
    except TypeError:
        return str(value)


def _diff_states(prev: OverridesState, new: OverridesState) -> StateDiff:
    prev_sources = {o.source for o in prev.applied_disabled_sources}
    new_sources = {o.source for o in new.applied_disabled_sources}
    prev_keywords = {o.keyword for o in prev.applied_disabled_keywords}
    new_keywords = {o.keyword for o in new.applied_disabled_keywords}
    # Thresholds aren't hashable as full dataclasses, so use (path, value).
    # value may be an unhashable type in principle; convert to str defensively.
    prev_thresholds = {(o.path, _hashable(o.value)) for o in prev.applied_threshold_overrides}
    new_thresholds = {(o.path, _hashable(o.value)) for o in new.applied_threshold_overrides}

    return StateDiff(
        sources_added=new_sources - prev_sources,
        sources_removed=prev_sources - new_sources,
        keywords_added=new_keywords - prev_keywords,
        keywords_removed=prev_keywords - new_keywords,
        thresholds_added=new_thresholds - prev_thresholds,
        thresholds_removed=prev_thresholds - new_thresholds,
    )


class RuntimeOverridesReader:
    """In-process singleton holding the current effective overrides state.

    Phase 1: bot creates one of these at startup. The asyncio poll task
    calls reload() every N minutes. Existing pipeline modules query
    is_source_disabled / is_keyword_disabled / get_threshold_override
    instead of indexing into static config sets directly.

    Reload contract: returns a StateDiff describing what changed.
    Reload failure (malformed YAML, schema violation) raises and leaves
    the previous valid state in place -- the bot never operates on a
    half-loaded state.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._state: OverridesState = _default_empty_state()

    def snapshot(self) -> OverridesState:
        """Return the current loaded state. Useful for diagnostics."""
        return self._state

    def reload(self) -> StateDiff:
        """Read the file fresh, compute the diff vs current state, swap.

        On failure: previous state preserved; exception raised. Caller
        is responsible for catching and logging (see tasks/runtime_overrides_task.py).
        """
        new_state_raw = load_from_disk(self._path)
        new_state = filter_expired(new_state_raw, now=datetime.now(_timezone.utc))
        diff = _diff_states(self._state, new_state)
        self._state = new_state
        return diff
