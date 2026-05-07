from scripts.edge_replay.keyword_direction_audit import audit_fixture_keyword_coverage
from scripts.edge_replay.llm_prompt_audit import flag_llm_output
from scripts.edge_replay.sub_fix_selection import choose_sub_fix
from scripts.edge_replay.suppression_trace import classify_suppression


def test_llm_prompt_audit_flags_neutral_directional_fixture():
    fixture = {"fixture_id": "F1", "expected_direction": "YES"}
    parsed = {"direction": "neutral", "magnitude": "none"}

    result = flag_llm_output(fixture, parsed)

    assert result["flagged"] is True
    assert "directional_fixture_collapsed" in result["reasons"]


def test_keyword_audit_reports_missing_directional_fixture_vocabulary():
    fixture = {
        "fixture_id": "F1_FISA_REAUTHORIZED_YES",
        "headline": "FISA Section 702 reauthorization signed into law on April 30, 2026",
        "body": "President signed FISA Section 702 reauthorization legislation today.",
        "expected_direction": "YES",
    }
    signal_defs = [{"keywords": ["missile strike"], "direction": "yes", "strength": 0.15}]

    result = audit_fixture_keyword_coverage(fixture, signal_defs)

    assert result["coverage_ok"] is False
    assert "reauthorization signed" in result["missing_expected_phrases"]
    assert result["matched_keywords"] == []


def test_suppression_trace_classifies_geo_mismatch_suppression():
    result = classify_suppression(
        "Geo-entity mismatch: article mentions {'iran'} but market concerns {'russia'} -- signal suppressed.",
        pre_suppression_magnitude=0.12,
        post_suppression_magnitude=0.0,
    )

    assert result["suppression_triggered"] is True
    assert result["suppression_reason"] == "geo_entity_mismatch"


def test_sub_fix_selection_picks_keyword_map_when_keyword_path_collapses_without_suppression():
    zero = {"zero_collapse_step": "keyword_path", "finding_type": "single_step", "count": 8}
    keyword = {
        "fixtures": [
            {"fixture_id": "F1", "expected_direction": "YES", "coverage_ok": False},
            {"fixture_id": "F2", "expected_direction": "NO", "coverage_ok": False},
        ]
    }
    suppression = {"fixtures": [{"fixture_id": "F1", "suppression_triggered": False}]}
    llm = {"fixtures": [{"fixture_id": "F1", "status": "ollama_unavailable"}]}

    result = choose_sub_fix(zero, keyword, suppression, llm)

    assert result["scope"] == "single_step"
    assert result["selected_surface"] == "per_keyword_direction_map"
    assert result["target_file"] == "config.py"
