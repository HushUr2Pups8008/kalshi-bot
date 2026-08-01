"""Configuration contracts for active and legacy-pending paper cohorts."""

from __future__ import annotations

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
import pytest

import config as config_module


def _valid_rsa_pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")


def _bot_config() -> config_module.BotConfig:
    return config_module.BotConfig(
        api_key_id="kalshi-key",
        api_key_secret=_valid_rsa_pem(),
    )


def test_paper_side_calibration_quarantine_defaults_off(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("ENABLE_PAPER_SIDE_CALIBRATION_QUARANTINE", raising=False)

    cfg = _bot_config()

    assert cfg.enable_paper_side_calibration_quarantine is False


@pytest.mark.parametrize(
    ("cohort_id", "expected_kind"),
    [
        ("legacy", "legacy"),
        ("pending-20260728", "active"),
    ],
)
def test_paper_cohort_kind_defaults_from_cohort_id(
    monkeypatch: pytest.MonkeyPatch,
    cohort_id: str,
    expected_kind: str,
):
    monkeypatch.setenv("PAPER_COHORT_ID", cohort_id)
    monkeypatch.delenv("PAPER_COHORT_KIND", raising=False)
    monkeypatch.setenv("PAPER_ACTIVE_COHORT_BANKROLL", "125")

    cfg = _bot_config()

    assert cfg.paper_cohort_kind == expected_kind


def test_legacy_pending_cohort_kind_uses_active_admission_horizon(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("PAPER_COHORT_ID", "pending-20260728")
    monkeypatch.setenv("PAPER_COHORT_KIND", "legacy_pending")
    monkeypatch.setenv("PAPER_ACTIVE_COHORT_BANKROLL", "125")

    cfg = _bot_config()

    assert cfg.paper_cohort_kind == "legacy_pending"
    assert cfg.paper_admission_max_days_to_close == pytest.approx(14.0)


def test_nonlegacy_cohort_requires_active_bankroll(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("PAPER_COHORT_ID", "pending-20260728")
    monkeypatch.setenv("PAPER_COHORT_KIND", "legacy_pending")
    monkeypatch.delenv("PAPER_ACTIVE_COHORT_BANKROLL", raising=False)

    with pytest.raises(SystemExit):
        _bot_config()


@pytest.mark.parametrize(
    ("cohort_id", "kind"),
    [
        ("legacy", "not-a-kind"),
        ("legacy", "active"),
        ("pending-20260728", "legacy"),
    ],
)
def test_paper_cohort_kind_rejects_invalid_or_incompatible_values(
    monkeypatch: pytest.MonkeyPatch,
    cohort_id: str,
    kind: str,
):
    monkeypatch.setenv("PAPER_COHORT_ID", cohort_id)
    monkeypatch.setenv("PAPER_COHORT_KIND", kind)
    monkeypatch.setenv("PAPER_ACTIVE_COHORT_BANKROLL", "125")

    with pytest.raises(SystemExit):
        _bot_config()
