"""Calibration task: stateful consumer for CALIBRATION_CHECK events.

Records per-lane prediction errors, detects drift relative to the fast-lane
baseline, and provides confidence scaling factors consumed by blend_task.
Drift is defined as per-lane Brier score exceeding 1.5× the fast-lane baseline
(contract Section 13 item 6).
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping

from analysis.calibration_monitor import (
    CalibrationMonitorState,
    calibration_summary,
    get_drifting_lanes,
    get_scaling_factor,
    update_lane,
)
from utils.logger import get_logger

log = get_logger("calibration_task")


class CalibrationTask:
    """Async-safe consumer for CALIBRATION_CHECK events."""

    def __init__(self) -> None:
        self._state = CalibrationMonitorState()
        self._seen_outbox_lanes: set[tuple[str, str]] = set()
        self._lock = asyncio.Lock()

    async def record_calibration_check(
        self,
        *,
        market_ticker: str,
        lane: str,
        lane_estimate: float,
        final_resolution: float,
        error: float,
        outbox_id: str | None = None,
    ) -> None:
        """Record a calibration event and emit a drift alert if newly detected."""
        async with self._lock:
            lineage = (outbox_id, lane) if outbox_id is not None else None
            if lineage is not None and lineage in self._seen_outbox_lanes:
                return
            previously_drifting = set(get_drifting_lanes(self._state))
            self._state = update_lane(self._state, lane, error)
            if lineage is not None:
                self._seen_outbox_lanes.add(lineage)
            now_drifting = set(get_drifting_lanes(self._state))

        for newly_drifting_lane in sorted(now_drifting - previously_drifting):
            lane_state = self._state.lanes[newly_drifting_lane]
            log.warning(
                "[CALIBRATION_DRIFT] lane=%s brier_score=%.4f "
                "scaling_factor=%.4f sample_count=%d market_ticker=%s",
                newly_drifting_lane,
                lane_state.brier_score,
                lane_state.scaling_factor,
                lane_state.sample_count,
                market_ticker,
            )

    async def replace_calibration_checks(
        self,
        checks: Iterable[Mapping[str, object]],
    ) -> None:
        """Replace in-memory state with a deterministic replay of checks."""
        async with self._lock:
            self._state = CalibrationMonitorState()
            self._seen_outbox_lanes.clear()

        for check in checks:
            raw_outbox_id = check.get("outbox_id")
            await self.record_calibration_check(
                market_ticker=str(check["market_ticker"]),
                lane=str(check["lane"]),
                lane_estimate=float(check["lane_estimate"]),
                final_resolution=float(check["final_resolution"]),
                error=float(check["error"]),
                outbox_id=(
                    str(raw_outbox_id) if raw_outbox_id is not None else None
                ),
            )

    def get_scaling_factor(self, lane: str) -> float:
        """Return current confidence scaling factor for a lane (1.0 = no change)."""
        return get_scaling_factor(self._state, lane)

    def get_calibration_summary(self) -> dict[str, dict]:
        """Return per-lane calibration statistics for diagnostics."""
        return calibration_summary(self._state)
