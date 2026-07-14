import asyncio
import logging

import pytest

from analysis.calibration_monitor import _MIN_BASELINE_SAMPLES, _MIN_LANE_SAMPLES
from tasks.calibration_task import CalibrationTask


async def _feed_fast_baseline(task: CalibrationTask, error: float = 0.1, n: int = _MIN_BASELINE_SAMPLES) -> None:
    for i in range(n):
        await task.record_calibration_check(
            market_ticker=f"MKT-{i}",
            lane="fast",
            lane_estimate=0.6,
            final_resolution=0.6 + error,
            error=error,
        )


async def _feed_lane(task: CalibrationTask, lane: str, error: float, n: int = _MIN_LANE_SAMPLES) -> None:
    for i in range(n):
        await task.record_calibration_check(
            market_ticker=f"MKT-{i}",
            lane=lane,
            lane_estimate=0.5,
            final_resolution=0.5 + error,
            error=error,
        )


@pytest.mark.asyncio
async def test_initial_scaling_factor_is_one():
    task = CalibrationTask()
    assert task.get_scaling_factor("fast") == pytest.approx(1.0)
    assert task.get_scaling_factor("accumulation") == pytest.approx(1.0)
    assert task.get_scaling_factor("structural") == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_scaling_remains_one_when_no_drift():
    task = CalibrationTask()
    await _feed_fast_baseline(task, error=0.1)
    await _feed_lane(task, "accumulation", error=0.1)
    assert task.get_scaling_factor("accumulation") == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_scaling_reduced_when_drift_detected():
    task = CalibrationTask()
    await _feed_fast_baseline(task, error=0.1)  # fast brier ≈ 0.01
    await _feed_lane(task, "accumulation", error=0.2)  # accum brier ≈ 0.04 > 1.5×0.01
    scale = task.get_scaling_factor("accumulation")
    assert scale < 1.0
    assert scale == pytest.approx(0.01 / 0.04)


@pytest.mark.asyncio
async def test_drift_alert_logged_on_first_detection(caplog):
    task = CalibrationTask()
    await _feed_fast_baseline(task, error=0.1)

    with caplog.at_level(logging.WARNING, logger="calibration_task"):
        await _feed_lane(task, "accumulation", error=0.2)

    drift_records = [r for r in caplog.records if "CALIBRATION_DRIFT" in r.message]
    assert len(drift_records) == 1
    assert "accumulation" in drift_records[0].message


@pytest.mark.asyncio
async def test_drift_alert_not_repeated_after_first_detection(caplog):
    task = CalibrationTask()
    await _feed_fast_baseline(task, error=0.1)
    await _feed_lane(task, "accumulation", error=0.2)  # triggers drift

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="calibration_task"):
        # One more event — already drifting, no new alert
        await task.record_calibration_check(
            market_ticker="MKT-extra",
            lane="accumulation",
            lane_estimate=0.5,
            final_resolution=0.9,
            error=0.4,
        )

    drift_records = [r for r in caplog.records if "CALIBRATION_DRIFT" in r.message]
    assert len(drift_records) == 0


@pytest.mark.asyncio
async def test_get_calibration_summary_includes_recorded_lanes():
    task = CalibrationTask()
    await _feed_fast_baseline(task, error=0.1)
    summary = task.get_calibration_summary()
    assert "fast" in summary
    assert summary["fast"]["sample_count"] == _MIN_BASELINE_SAMPLES
    assert summary["fast"]["drift_detected"] is False


@pytest.mark.asyncio
async def test_concurrent_records_do_not_corrupt_state():
    task = CalibrationTask()
    # 20 concurrent fast-lane records
    await asyncio.gather(*[
        task.record_calibration_check(
            market_ticker=f"MKT-{i}",
            lane="fast",
            lane_estimate=0.5,
            final_resolution=0.6,
            error=0.1,
        )
        for i in range(20)
    ])
    summary = task.get_calibration_summary()
    assert summary["fast"]["sample_count"] == 20


@pytest.mark.asyncio
async def test_outbox_lineage_deduplicates_each_lane():
    task = CalibrationTask()
    common = {
        "market_ticker": "MKT-SETTLED",
        "lane_estimate": 0.7,
        "final_resolution": 1.0,
        "error": 0.3,
        "outbox_id": "a" * 64,
    }

    await task.record_calibration_check(lane="fast", **common)
    await task.record_calibration_check(lane="fast", **common)
    await task.record_calibration_check(lane="accumulation", **common)
    await task.record_calibration_check(
        lane="fast",
        **{**common, "outbox_id": "b" * 64},
    )

    summary = task.get_calibration_summary()
    assert summary["fast"]["sample_count"] == 2
    assert summary["accumulation"]["sample_count"] == 1
