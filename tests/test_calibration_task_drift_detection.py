from __future__ import annotations

import pytest

from analysis.calibration_monitor import (
    CalibrationMonitorState,
    _MIN_BASELINE_SAMPLES,
    _MIN_LANE_SAMPLES,
    _SCALING_FLOOR,
    get_drifting_lanes,
    get_scaling_factor,
    update_lane,
)


def _feed(state: CalibrationMonitorState, lane: str, errors: list[float]) -> CalibrationMonitorState:
    for error in errors:
        state = update_lane(state, lane, error)
    return state


def _baseline(error: float = 0.1) -> CalibrationMonitorState:
    return _feed(CalibrationMonitorState(), "fast", [error] * _MIN_BASELINE_SAMPLES)


def test_calibration_drift_requires_fast_baseline_before_lane_is_scaled():
    state = _feed(CalibrationMonitorState(), "fast", [0.1] * (_MIN_BASELINE_SAMPLES - 1))
    state = _feed(state, "branch_c", [1.0] * _MIN_LANE_SAMPLES)

    assert state.lanes["branch_c"].drift_detected is False
    assert get_scaling_factor(state, "branch_c") == pytest.approx(1.0)


def test_calibration_drift_requires_lane_sample_floor_before_lane_is_scaled():
    state = _baseline(0.1)
    state = _feed(state, "branch_c", [1.0] * (_MIN_LANE_SAMPLES - 1))

    assert state.lanes["branch_c"].drift_detected is False
    assert get_scaling_factor(state, "branch_c") == pytest.approx(1.0)


def test_calibration_drift_scaling_is_lane_local_and_floor_clamped():
    state = _baseline(0.1)
    state = _feed(state, "branch_c", [1.0] * _MIN_LANE_SAMPLES)
    state = _feed(state, "wave1", [0.1] * _MIN_LANE_SAMPLES)

    assert get_drifting_lanes(state) == ["branch_c"]
    assert get_scaling_factor(state, "branch_c") == pytest.approx(_SCALING_FLOOR)
    assert get_scaling_factor(state, "wave1") == pytest.approx(1.0)
