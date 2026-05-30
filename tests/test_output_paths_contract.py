from pathlib import Path

from utils import output_paths


def _under(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def test_output_contract_paths_are_under_single_output_root():
    for path in output_paths.OUTPUT_CONTRACT_PATHS.values():
        assert _under(path, output_paths.OUTPUT_ROOT), f"{path} is outside output root"


def test_report_paths_are_separate_from_raw_telemetry_paths():
    for report_path in output_paths.REPORT_PATHS.values():
        for raw_path in output_paths.RAW_PATHS.values():
            assert not _under(report_path, raw_path), (
                f"report path {report_path} must not be under raw telemetry path {raw_path}"
            )


def test_default_report_and_derived_paths_are_classified():
    assert output_paths.DAILY_REPORTS_DIR.name == "daily"
    assert output_paths.HEALTH_REPORTS_DIR.name == "health"
    assert output_paths.PERFORMANCE_REPORTS_DIR.name == "performance"
    assert output_paths.EVALUATION_REPORTS_DIR.name == "evaluations"
    assert output_paths.DERIVED_STATE_DIR == output_paths.OUTPUT_ROOT / "state" / "derived"
