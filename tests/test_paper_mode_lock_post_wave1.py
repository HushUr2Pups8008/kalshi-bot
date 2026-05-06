"""Paper-mode lock guardrail post-Wave-1 (Cycle-14 charter §5).

These tests assert that Wave-1 deploy commits do NOT flip live-trading flags.
Wave-1 ships OBS-005 (cooldown sentinel fix) which unblocks more never-traded
tickers from cooldown — IF the bot's belief model has a sign-error suspect,
that unblock could surreptitiously widen exposure on a broken model. The
hard paper-mode lock prevents this.

Per Cycle-14 charter:
- live_trading_enabled MUST remain False in default config + production
  default `LIVE_TRADING_ENABLED` env var.
- Wave-1 deploy commits MUST NOT mutate the lock to True without operator-
  explicit override + replayed-EV evidence per IC §16.

If Cycle-14 verdict returns "model_fine" or "calibration fixable in Cycle-15
with replay evidence," operator MAY toggle live_trading_enabled=True ONCE
the IC §16 evidence gate is cleared — but NOT before, and NOT as a
side-effect of Wave-1 hygiene shipping.

This test runs in CI for every Wave-1 deploy commit.
"""

from __future__ import annotations

import os
from unittest.mock import patch

from config import cfg


def test_live_trading_disabled_by_default():
    """Default config (no env override) must have live_trading_enabled=False.

    This catches a Wave-1 commit that accidentally flips the default in code.
    """
    # Ensure no env override leaks into the test
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("LIVE_TRADING_ENABLED", None)
        # Re-instantiate the Config dataclass with the cleared env
        from config import BotConfig

        fresh = BotConfig()
        assert fresh.live_trading_enabled is False, (
            "live_trading_enabled MUST default to False. "
            "Cycle-14 charter §5: paper-mode lock is a hard guardrail post-Wave-1. "
            "If a Wave-1 commit accidentally toggled the default to True, that's a "
            "bug; operator MUST NOT enable live trading without IC §16 replay-evidence "
            "gate clearance per Cycle-15+ scope."
        )


def test_live_trading_disabled_when_env_unset():
    """Operator's actual production .env should leave LIVE_TRADING_ENABLED unset.

    If the env var is unset, default 'false' applies. This test asserts the
    runtime config matches that default. Catches a non-disclosed override in
    .env or shell.
    """
    # The currently-imported cfg reflects the actual environment
    if cfg.live_trading_enabled is True:
        # Operator may have explicitly enabled it — that's allowed only after
        # Cycle-15 IC §16 replay-evidence gate clearance.
        # This test refuses to silently pass in that case; it FAILS LOUDLY
        # so operator must explicitly acknowledge the override.
        import pytest

        pytest.fail(
            "live_trading_enabled is True at runtime. Cycle-14 charter §5 paper-mode "
            "lock requires operator-explicit override + IC §16 replayed-EV evidence "
            "before this flag flips. If you (operator) intentionally enabled live "
            "trading post-Cycle-15 fix-shipping with replay evidence, edit this test "
            "to assert the post-Cycle-15 state explicitly + reference the replay "
            "report. Otherwise: investigate why the flag is True and revert."
        )

    assert cfg.live_trading_enabled is False, "Paper-mode lock holds at runtime"


def test_live_trading_flag_source_is_explicit_env_var_only():
    """The only legitimate path to live_trading_enabled=True is the env var.

    No code-level default, no DB-driven flip, no test-fixture path. The flag's
    field's default_factory inspects only LIVE_TRADING_ENABLED env var.

    Catches a Wave-1 (or future) commit that adds an alternate path to
    flipping the flag (e.g., DB-row-driven, kill-switch reverse).
    """
    from pathlib import Path

    config_text = Path(__file__).parent.parent.joinpath("config.py").read_text()

    # The single legitimate setter location is `default_factory=lambda: ...`
    # examining `LIVE_TRADING_ENABLED` env var. Confirm no other path sets the
    # flag to True via direct assignment in config.py.
    forbidden_patterns = [
        "live_trading_enabled = True",
        "self.live_trading_enabled = True",
        '"live_trading_enabled": True',
    ]
    for pat in forbidden_patterns:
        assert pat not in config_text, (
            f"Forbidden direct True assignment found in config.py: {pat!r}. "
            f"live_trading_enabled MUST be flipped only via the LIVE_TRADING_ENABLED "
            f"env var, never via code-level default or in-process mutation. Per "
            f"Cycle-14 charter §5 paper-mode lock."
        )


def test_paper_trading_flag_default_is_true():
    """is_paper_trading must default to True.

    Companion to the live-trading lock; the bot's per-instance paper-mode flag.
    Catches a code change that flips this default.
    """
    from config import BotConfig

    fresh = BotConfig()
    assert fresh.is_paper_trading is True, (
        "is_paper_trading MUST default to True. Per Cycle-14 charter §5 paper-mode "
        "lock, the bot ships in paper-only mode unless operator explicitly toggles "
        "via set_paper_mode(False) AND IC §16 replay-evidence gate is cleared."
    )
