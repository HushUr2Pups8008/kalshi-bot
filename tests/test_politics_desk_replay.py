"""Politics desk replay reports fee-net deltas without weakening the gate."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from scripts.simulations.politics_desk_replay import (
    LIVE_SETTLED_ROWS,
    PAPER_ROWS,
    REALIZED_ROWS,
    capped_contracts,
    corpus_mismatch,
    format_politics_replay,
    replay_modes,
    yes_floor_drops,
)


def _mode(name: str) -> dict[str, str]:
    return next(row for row in replay_modes() if row["mode"] == name)


def test_baseline_is_the_sealed_paper_and_settled_live_sum() -> None:
    baseline = _mode("baseline")
    assert baseline["n"] == "9"
    assert baseline["fee_net"] == "-0.2784"
    assert baseline["delta"] == "0.0000"
    assert len(PAPER_ROWS) == 6
    assert len(LIVE_SETTLED_ROWS) == 3
    assert len(REALIZED_ROWS) == 9


def test_yes_floor_drops_only_the_36c_yes_loss() -> None:
    dropped = [row.ticker for row in REALIZED_ROWS if yes_floor_drops(row)]
    assert dropped == ["KXTRUTHSOCIAL-26OCT03-B129"]
    assert yes_floor_drops(next(row for row in PAPER_ROWS if row.ticker.endswith("T4"))) is False
    mode = _mode("yes_floor")
    assert mode["n"] == "8"
    assert mode["fee_net"] == "7.2442"
    assert mode["delta"] == "7.5226"
    assert mode["positive_fee_net_removed"] == "0.0000"


def test_risk_cap_uses_the_same_contract_formula_as_the_desk() -> None:
    by_ticker = {row.ticker: capped_contracts(row) for row in REALIZED_ROWS}
    assert by_ticker["KXTRUTHSOCIAL-26OCT03-B129"] == 8
    assert by_ticker["KXTRUMPACT-26SEP06-T4"] == 8
    assert by_ticker["KXTRUMPACT-26OCT04-T6"] == 8
    assert by_ticker["KXTRUTHSOCIAL-26AUG29-T240"] == 4
    assert by_ticker["KXTRUMPSAY-26SEP14-UKRA"] == 6
    mode = _mode("risk_cap")
    assert mode["n"] == "9"
    assert Decimal(mode["fee_net"]) != Decimal("-0.2784")
    assert "not perfectly linear" in mode["note"]


def test_family_drops_the_overlapping_paper_winner_and_leaves_live_unmeasured() -> None:
    mode = _mode("one_per_family")
    assert mode["n"] == "8"
    assert mode["delta"] == "-1.9722"
    assert mode["positive_fee_net_removed"] == "1.9722"
    assert "not_measured" in mode["note"]


def test_exposure_stays_unmeasured_and_maker_is_a_same_price_quote() -> None:
    exposure = _mode("exposure")
    assert exposure["fee_net"] == "not_measured"
    assert exposure["delta"] == "not_measured"
    assert "1872" in exposure["note"]
    assert "1160" in exposure["note"]
    maker = _mode("maker")
    assert maker["n"] == "9"
    assert Decimal(maker["delta"]) > 0
    assert "0.0175" in maker["note"]
    assert "fill probability is not modeled" in maker["note"]


def test_all_modes_apply_together_without_the_exposure_guess() -> None:
    mode = _mode("all")
    yes_floor = _mode("yes_floor")
    assert mode["n"] == "7"
    assert Decimal(mode["delta"]) == Decimal(mode["fee_net"]) - Decimal("-0.2784")
    # Family drops a winner and the risk cap shrinks the rest, so the stack
    # is below the YES-floor result. Exposure is not guessed into this number.
    assert Decimal(mode["fee_net"]) < Decimal(yes_floor["fee_net"])
    assert "exposure stays not_measured" in mode["note"]


def test_fixture_mismatch_does_not_weaken_the_gate() -> None:
    mismatch = corpus_mismatch()
    assert mismatch["fixture_inside_discovery"] is False
    assert mismatch["path_mismatch"] is True
    assert mismatch["format_mismatch"] is True
    assert mismatch["evidence_class"] == ["desk_history"]
    assert mismatch["required_evidence_class"] == "registered_oos"
    assert mismatch["failure_reason"] == "insufficient corpus"
    assert mismatch["gate_rule_change"] is False
    text = format_politics_replay()
    assert "failure_reason=insufficient corpus" in text
    assert "tests/fixtures/edge_replay/corpus_politics_desk_history.jsonl" in text
    for banned in ("client_order_id", "ghp_", "BEGIN ", "signal_headline", "api_key"):
        assert banned not in Path("scripts/simulations/politics_desk_replay.py").read_text(
            encoding="utf-8"
        )


def test_ci_entry_prints_the_table_and_still_fails(monkeypatch, capsys) -> None:
    from scripts.edge_replay.ci_entry import main
    from scripts.edge_replay.replay_gate import GateVerdict

    fake = GateVerdict(
        pass_=False,
        tier="T3",
        rule4=None,
        coverage_report_path=Path("/tmp/cov.json"),
        rule4_table_path=Path("/tmp/rule4.json"),
        scenario_report_path=Path("/tmp/scen.txt"),
        notes=["InsufficientCorpusError: 0 usable corpora"],
        failure_reason="insufficient corpus",
        output_dir=Path("/tmp"),
    )
    monkeypatch.delenv("REPLAY_GATE_OVERRIDE", raising=False)
    monkeypatch.setattr("scripts.edge_replay.ci_entry.run_replay_gate", lambda **_kw: fake)
    monkeypatch.setattr(
        "scripts.edge_replay.ci_entry._git_changed_files",
        lambda *_a, **_kw: [Path("trading/executor.py")],
    )
    assert main(["--base", "origin/main"]) == 1
    captured = capsys.readouterr()
    assert "pass:    False" in captured.out
    assert "failure_reason=insufficient corpus" in captured.out
    assert "mode n fee_net delta" in captured.out


def test_ci_entry_keeps_the_exit_code_when_the_table_raises(monkeypatch, capsys) -> None:
    from scripts.edge_replay.ci_entry import main
    from scripts.edge_replay.replay_gate import GateVerdict

    fake = GateVerdict(
        pass_=False,
        tier="T3",
        rule4=None,
        coverage_report_path=Path("/tmp/cov.json"),
        rule4_table_path=Path("/tmp/rule4.json"),
        scenario_report_path=Path("/tmp/scen.txt"),
        notes=[],
        failure_reason="insufficient corpus",
        output_dir=Path("/tmp"),
    )
    monkeypatch.delenv("REPLAY_GATE_OVERRIDE", raising=False)
    monkeypatch.setattr("scripts.edge_replay.ci_entry.run_replay_gate", lambda **_kw: fake)
    monkeypatch.setattr(
        "scripts.edge_replay.ci_entry._git_changed_files",
        lambda *_a, **_kw: [Path("trading/executor.py")],
    )

    def _boom() -> str:
        raise RuntimeError("table failed")

    monkeypatch.setattr(
        "scripts.simulations.politics_desk_replay.format_politics_replay",
        _boom,
    )
    assert main(["--base", "origin/main"]) == 1
    captured = capsys.readouterr()
    assert "politics replay table unavailable: RuntimeError" in captured.out
