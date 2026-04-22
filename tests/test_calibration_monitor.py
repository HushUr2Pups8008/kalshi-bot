import pytest

from analysis.calibration_monitor import (
    CalibrationMonitorState,
    _MIN_BASELINE_SAMPLES,
    _MIN_LANE_SAMPLES,
    _SCALING_FLOOR,
    _WINDOW_SIZE,
    calibration_summary,
    get_drifting_lanes,
    get_scaling_factor,
    update_lane,
)


def _feed(state: CalibrationMonitorState, lane: str, errors: list[float]) -> CalibrationMonitorState:
    for e in errors:
        state = update_lane(state, lane, e)
    return state


def _fast_baseline(error: float = 0.1, n: int = _MIN_BASELINE_SAMPLES) -> CalibrationMonitorState:
    return _feed(CalibrationMonitorState(), "fast", [error] * n)


# ── basic update mechanics ────────────────────────────────────────────────────

def test_initial_state_empty():
    state = CalibrationMonitorState()
    assert state.lanes == {}
    assert state.fast_lane_brier is None
    assert state.fast_lane_sample_count == 0


def test_update_creates_lane_entry():
    state = update_lane(CalibrationMonitorState(), "fast", 0.1)
    assert "fast" in state.lanes
    ls = state.lanes["fast"]
    assert ls.errors == (0.1,)
    assert ls.sample_count == 1


def test_brier_score_is_mean_squared_error():
    state = _feed(CalibrationMonitorState(), "fast", [0.2, 0.4])
    brier = state.lanes["fast"].brier_score
    assert brier == pytest.approx((0.2 ** 2 + 0.4 ** 2) / 2)


def test_window_bounded_to_window_size():
    state = _feed(CalibrationMonitorState(), "fast", [0.1] * (_WINDOW_SIZE + 5))
    assert len(state.lanes["fast"].errors) == _WINDOW_SIZE
    assert state.lanes["fast"].sample_count == _WINDOW_SIZE + 5


def test_update_does_not_mutate_input():
    original = CalibrationMonitorState()
    _ = update_lane(original, "fast", 0.1)
    assert original.lanes == {}


def test_fast_lane_brier_updated_on_fast_event():
    state = _feed(CalibrationMonitorState(), "fast", [0.3] * _MIN_BASELINE_SAMPLES)
    assert state.fast_lane_brier == pytest.approx(0.3 ** 2)
    assert state.fast_lane_sample_count == _MIN_BASELINE_SAMPLES


# ── fast-lane: always scaling_factor=1.0, no drift ───────────────────────────

def test_fast_lane_never_drifts():
    state = _feed(CalibrationMonitorState(), "fast", [0.9] * 20)
    ls = state.lanes["fast"]
    assert ls.drift_detected is False
    assert ls.scaling_factor == pytest.approx(1.0)


# ── drift suppressed before minimum samples ───────────────────────────────────

def test_no_drift_before_fast_baseline_established():
    state = _feed(CalibrationMonitorState(), "fast", [0.1] * (_MIN_BASELINE_SAMPLES - 1))
    state = _feed(state, "accumulation", [0.9] * _MIN_LANE_SAMPLES)
    assert state.lanes["accumulation"].drift_detected is False


def test_no_drift_before_lane_min_samples():
    state = _fast_baseline(0.1)
    state = _feed(state, "accumulation", [0.9] * (_MIN_LANE_SAMPLES - 1))
    assert state.lanes["accumulation"].drift_detected is False


# ── drift detection ───────────────────────────────────────────────────────────

def test_drift_detected_when_brier_exceeds_threshold():
    # fast brier ≈ 0.01 (error=0.1), accumulation brier ≈ 0.0225 (error=0.15)
    # 0.0225 > 1.5 * 0.01 → drift
    state = _fast_baseline(error=0.1)
    state = _feed(state, "accumulation", [0.15] * _MIN_LANE_SAMPLES)
    assert state.lanes["accumulation"].drift_detected is True


def test_no_drift_when_brier_at_threshold():
    # fast brier = 0.01, accumulation brier = exactly 1.5 * 0.01 = 0.015
    # strictly greater than threshold required
    import math
    fast_error = 0.1  # brier = 0.01
    # need accumulation brier = 0.015 exactly → error = sqrt(0.015)
    accum_error = math.sqrt(0.015)
    state = _fast_baseline(error=fast_error)
    state = _feed(state, "accumulation", [accum_error] * _MIN_LANE_SAMPLES)
    # 0.015 is NOT > 1.5 * 0.01 (it equals it), so no drift
    assert state.lanes["accumulation"].drift_detected is False


def test_no_drift_when_brier_below_threshold():
    # fast brier = 0.01, accumulation brier = 0.012 < 1.5 * 0.01
    import math
    state = _fast_baseline(error=0.1)
    state = _feed(state, "accumulation", [math.sqrt(0.012)] * _MIN_LANE_SAMPLES)
    assert state.lanes["accumulation"].drift_detected is False


# ── scaling factor ────────────────────────────────────────────────────────────

def test_scaling_factor_is_ratio_when_drifting():
    fast_error = 0.1  # fast brier = 0.01
    accum_error = 0.2  # accum brier = 0.04 > 1.5 * 0.01
    state = _fast_baseline(error=fast_error)
    state = _feed(state, "accumulation", [accum_error] * _MIN_LANE_SAMPLES)
    ls = state.lanes["accumulation"]
    assert ls.drift_detected is True
    # scaling_factor = fast_brier / lane_brier = 0.01 / 0.04 = 0.25
    assert ls.scaling_factor == pytest.approx(0.01 / 0.04)


def test_scaling_factor_floor_applied():
    # Very high error → scaling_factor floor of _SCALING_FLOOR
    fast_error = 0.1  # brier = 0.01
    accum_error = 1.0  # brier = 1.0 → ratio = 0.01, below floor
    state = _fast_baseline(error=fast_error)
    state = _feed(state, "accumulation", [accum_error] * _MIN_LANE_SAMPLES)
    ls = state.lanes["accumulation"]
    assert ls.drift_detected is True
    assert ls.scaling_factor == pytest.approx(_SCALING_FLOOR)


def test_scaling_factor_one_when_no_drift():
    state = _fast_baseline(error=0.1)
    state = _feed(state, "accumulation", [0.1] * _MIN_LANE_SAMPLES)
    assert state.lanes["accumulation"].scaling_factor == pytest.approx(1.0)


def test_get_scaling_factor_returns_one_for_unknown_lane():
    state = CalibrationMonitorState()
    assert get_scaling_factor(state, "accumulation") == pytest.approx(1.0)


def test_get_scaling_factor_returns_lane_value():
    state = _fast_baseline(error=0.1)
    state = _feed(state, "accumulation", [0.2] * _MIN_LANE_SAMPLES)
    expected = state.lanes["accumulation"].scaling_factor
    assert get_scaling_factor(state, "accumulation") == pytest.approx(expected)


# ── get_drifting_lanes ────────────────────────────────────────────────────────

def test_get_drifting_lanes_empty_initially():
    assert get_drifting_lanes(CalibrationMonitorState()) == []


def test_get_drifting_lanes_returns_drifting():
    state = _fast_baseline(error=0.1)
    state = _feed(state, "accumulation", [0.5] * _MIN_LANE_SAMPLES)
    state = _feed(state, "structural", [0.1] * _MIN_LANE_SAMPLES)  # not drifting
    assert get_drifting_lanes(state) == ["accumulation"]


# ── calibration_summary ───────────────────────────────────────────────────────

def test_calibration_summary_structure():
    state = _fast_baseline(error=0.1)
    summary = calibration_summary(state)
    assert "fast" in summary
    fast_entry = summary["fast"]
    assert fast_entry["lane"] == "fast"
    assert fast_entry["sample_count"] == _MIN_BASELINE_SAMPLES
    assert "brier_score" in fast_entry
    assert "scaling_factor" in fast_entry
    assert "drift_detected" in fast_entry


def test_calibration_summary_empty_state():
    assert calibration_summary(CalibrationMonitorState()) == {}
