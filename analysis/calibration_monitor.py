"""Pure calibration monitoring functions for cross-lane drift detection.

Consumes CALIBRATION_CHECK events and computes per-lane Brier scores and
confidence scaling factors. Scaling kicks in only when a lane's rolling mean
squared error exceeds _DRIFT_THRESHOLD × the fast-lane baseline.
"""

from __future__ import annotations

from dataclasses import dataclass, field

_DRIFT_THRESHOLD: float = 1.5
_WINDOW_SIZE: int = 50
_MIN_BASELINE_SAMPLES: int = 5
_MIN_LANE_SAMPLES: int = 5
_SCALING_FLOOR: float = 0.2


@dataclass(frozen=True)
class LaneCalibrationState:
    lane: str
    errors: tuple[float, ...]
    brier_score: float
    scaling_factor: float
    drift_detected: bool
    sample_count: int


@dataclass
class CalibrationMonitorState:
    lanes: dict[str, LaneCalibrationState] = field(default_factory=dict)
    fast_lane_brier: float | None = None
    fast_lane_sample_count: int = 0


def update_lane(
    state: CalibrationMonitorState,
    lane: str,
    error: float,
) -> CalibrationMonitorState:
    """Record a calibration error for lane. Returns new state (does not mutate input)."""
    existing = state.lanes.get(lane)
    prior_errors: tuple[float, ...] = existing.errors if existing is not None else ()
    prior_count: int = existing.sample_count if existing is not None else 0

    new_errors = (prior_errors + (error,))[-_WINDOW_SIZE:]
    brier_score = sum(e * e for e in new_errors) / len(new_errors)
    sample_count = prior_count + 1

    fast_brier = state.fast_lane_brier
    fast_samples = state.fast_lane_sample_count

    if lane == "fast":
        fast_brier = brier_score
        fast_samples += 1
        new_lane_state = LaneCalibrationState(
            lane=lane,
            errors=new_errors,
            brier_score=brier_score,
            scaling_factor=1.0,
            drift_detected=False,
            sample_count=sample_count,
        )
    else:
        drift_detected, scaling_factor = _compute_drift(
            brier_score=brier_score,
            sample_count=sample_count,
            fast_brier=fast_brier,
            fast_samples=fast_samples,
        )
        new_lane_state = LaneCalibrationState(
            lane=lane,
            errors=new_errors,
            brier_score=brier_score,
            scaling_factor=scaling_factor,
            drift_detected=drift_detected,
            sample_count=sample_count,
        )

    new_lanes = {**state.lanes, lane: new_lane_state}
    return CalibrationMonitorState(
        lanes=new_lanes,
        fast_lane_brier=fast_brier,
        fast_lane_sample_count=fast_samples,
    )


def _compute_drift(
    *,
    brier_score: float,
    sample_count: int,
    fast_brier: float | None,
    fast_samples: int,
) -> tuple[bool, float]:
    if (
        fast_brier is None
        or fast_samples < _MIN_BASELINE_SAMPLES
        or sample_count < _MIN_LANE_SAMPLES
        or fast_brier == 0.0
    ):
        return False, 1.0

    if brier_score > _DRIFT_THRESHOLD * fast_brier:
        return True, max(fast_brier / brier_score, _SCALING_FLOOR)

    return False, 1.0


def get_scaling_factor(state: CalibrationMonitorState, lane: str) -> float:
    """Return the confidence scaling factor for a lane (1.0 = no adjustment)."""
    lane_state = state.lanes.get(lane)
    return lane_state.scaling_factor if lane_state is not None else 1.0


def get_drifting_lanes(state: CalibrationMonitorState) -> list[str]:
    """Return lanes where drift has been detected."""
    return [lane for lane, ls in state.lanes.items() if ls.drift_detected]


def calibration_summary(state: CalibrationMonitorState) -> dict[str, dict]:
    """Return per-lane calibration statistics for diagnostics."""
    return {
        lane: {
            "lane": ls.lane,
            "sample_count": ls.sample_count,
            "brier_score": round(ls.brier_score, 6),
            "scaling_factor": round(ls.scaling_factor, 4),
            "drift_detected": ls.drift_detected,
        }
        for lane, ls in state.lanes.items()
    }
