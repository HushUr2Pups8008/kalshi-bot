"""Shadow-only fresh-pass assignment diagnostics.

This module observes matcher assignment shape. It must not affect executable
trade admission, probability, EV, readiness, or order submission.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class ShadowAssignment:
    type: str
    shadow_only: bool
    assigned: bool
    source: str
    headline: str
    top_ticker: str = ""
    top_score: float | None = None
    candidate_count: int = 0
    candidate_count_basis: str = "post_suppression_survivors"
    malformed: bool = False
    malformed_reason: str = ""
    ts: str = ""

    def to_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["ts"] = self.ts or datetime.now(timezone.utc).isoformat()
        return record


def _unpack_top_candidate(candidate: object) -> tuple[str, float]:
    try:
        market, score, _match_meta = candidate  # type: ignore[misc]
        ticker = market.ticker
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"malformed candidate tuple: {exc}") from exc
    if not ticker:
        raise ValueError("malformed candidate tuple: missing ticker")
    return str(ticker), float(score)


async def build_shadow_assignment(matcher: object, news: object) -> ShadowAssignment:
    candidates = await matcher.find_candidates(
        news,
        refresh_cache=False,
        emit_diagnostics=False,
    )
    source = str(getattr(news, "source", ""))
    headline = str(getattr(news, "headline", ""))
    if not candidates:
        return ShadowAssignment(
            type="FRESH_PASS_ASSIGNMENT_SHADOW",
            shadow_only=True,
            assigned=False,
            source=source,
            headline=headline,
            candidate_count=0,
        )
    try:
        top_ticker, top_score = _unpack_top_candidate(candidates[0])
    except ValueError as exc:
        return ShadowAssignment(
            type="FRESH_PASS_ASSIGNMENT_SHADOW",
            shadow_only=True,
            assigned=False,
            source=source,
            headline=headline,
            candidate_count=len(candidates),
            malformed=True,
            malformed_reason=str(exc),
        )
    return ShadowAssignment(
        type="FRESH_PASS_ASSIGNMENT_SHADOW",
        shadow_only=True,
        assigned=True,
        source=source,
        headline=headline,
        top_ticker=top_ticker,
        top_score=top_score,
        candidate_count=len(candidates),
    )
