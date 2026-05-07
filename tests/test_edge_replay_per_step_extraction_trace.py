from scripts.edge_replay.per_step_extraction_trace import identify_zero_collapse_step, trace_fixture


def test_trace_fixture_emits_ordered_step_records_for_directional_fixture():
    fixture = {
        "fixture_id": "F_TEST_YES",
        "market_ticker": "KXTEST-1",
        "headline": "President signs test bill into law before deadline",
        "body": "The bill was signed and enacted before the market close.",
        "source": "AP News",
        "expected_direction": "YES",
        "expected_magnitude_min": 0.05,
    }

    trace = trace_fixture(fixture, run_llm=False)

    assert trace["fixture_id"] == "F_TEST_YES"
    assert len(trace["steps"]) >= 4
    assert [step["step_name"] for step in trace["steps"]][:4] == [
        "fixture_expected_signal",
        "keyword_path",
        "llm_path",
        "final_estimate",
    ]
    for step in trace["steps"]:
        assert {"step_name", "input_signal_magnitude", "output_signal_magnitude", "intermediate_state"} <= set(step)


def test_identify_zero_collapse_step_returns_first_collapsing_step():
    trace = {
        "steps": [
            {"step_name": "a", "input_signal_magnitude": 0.10, "output_signal_magnitude": 0.08},
            {"step_name": "b", "input_signal_magnitude": 0.08, "output_signal_magnitude": 0.0},
            {"step_name": "c", "input_signal_magnitude": 0.08, "output_signal_magnitude": 0.0},
        ]
    }

    result = identify_zero_collapse_step([trace])

    assert result["zero_collapse_step"] == "b"
    assert result["finding_type"] == "single_step"
