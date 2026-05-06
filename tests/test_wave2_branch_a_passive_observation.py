from __future__ import annotations

import pytest


_BRANCH_A_XFAIL_REASON = (
    "Wave-2 Branch A passive-observation config not deployed. Branch A begins "
    "after Wave-1 close and observes 14d with no new feed onboarding and a "
    "0-trade intervention invariant."
)


@pytest.mark.xfail(reason=_BRANCH_A_XFAIL_REASON, strict=True)
def test_branch_a_passive_observation_config_exists():
    import config

    assert getattr(config, "EDGE004_BRANCH_A_PASSIVE_OBSERVE_DAYS") == 14
    assert getattr(config, "EDGE004_BRANCH_A_ALLOW_FEED_ONBOARDING") is False
    assert getattr(config, "EDGE004_BRANCH_A_MAX_OPERATOR_TRADES") == 0

