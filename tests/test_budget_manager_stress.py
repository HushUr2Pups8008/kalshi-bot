from scripts.budget_manager_stress import run_stress
from tasks.budget_manager import GLOBAL_HOURLY_LIMIT


def test_synthetic_stress_fires_breaker_at_three_times_queue_depth():
    result = run_stress(global_limit=4, per_market_limit=2, overflow_requests=3)

    assert result.circuit_breaker_threshold == 12
    assert result.breaker_first_observed_at_request == 16
    assert result.final_queue_depth == 12
    assert result.circuit_breaker_open is True


def test_synthetic_stress_emits_budget_pressure_once_with_expected_payload():
    result = run_stress(global_limit=3, per_market_limit=2, overflow_requests=5)

    assert result.budget_pressure_event_count == 1
    event = result.budget_pressure_events[0]
    assert event["reason"] == "queue_depth_reached"
    assert event["queue_depth"] == result.circuit_breaker_threshold
    assert event["circuit_breaker_threshold"] == result.circuit_breaker_threshold
    assert event["global_calls_last_hour"] == 3


def test_synthetic_stress_prevents_runaway_admission_and_preserves_limits():
    result = run_stress(global_limit=5, per_market_limit=2, overflow_requests=8)

    assert result.admitted_count == 5
    assert result.no_runaway_admission is True
    assert result.global_limit_holds is True
    assert result.per_market_limit_holds is True
    assert result.per_market_admitted_in_probe == 2


def test_synthetic_stress_is_deterministic_for_default_profile():
    first = run_stress()
    second = run_stress()

    assert first == second
    assert first.admitted_count == GLOBAL_HOURLY_LIMIT
    assert first.deterministic_pass is True
