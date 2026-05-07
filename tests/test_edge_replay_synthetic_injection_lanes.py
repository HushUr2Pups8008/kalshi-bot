from scripts.edge_replay.synthetic_injection_lanes import run_synthetic_lanes


def test_synthetic_lanes_pass_for_clear_yes_and_no_fixture():
    result = run_synthetic_lanes(extractor=lambda fixture: fixture.implied_probability)

    assert result["lane_a"]["pass"] is True
    assert result["lane_b"]["pass"] is True
    assert result["verdict"] == "synthetic_lanes_pass"
