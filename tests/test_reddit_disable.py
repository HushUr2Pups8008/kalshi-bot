"""Reddit ingestion disable gate (Track B / B3 — Reddit permanently unavailable 2026-05-29).

Reddit denied the bot's OAuth app ("no community benefit"), so anonymous `.json`
polling is IP-blocked (~2,600 HTTP-403/wk; the global circuit opened 2026-05-13 and
never recovered) and Reddit contributes 0 genuine opportunities. Rather than keep
spending poll cycles + flooding the log with 403 warnings, Reddit ingestion is gated
off by default.

These tests pin the *clean, reversible* disable:
  - `reddit_enabled` defaults False  -> no wasted poll cycles / 403 log-spam.
  - The two Reddit ingestion entry points (`run_reddit_monitor`,
    `run_discovery_pass`) no-op *before* any network work when disabled.
  - `REDDIT_ENABLED=true` re-enables cleanly (the escape hatch if Reddit ever
    approves an app) — the gate must not over-block.

WHY these assertions matter: a gate that silently still polled (or that blocked even
when re-enabled) would either re-open the 403 storm or strand the recovery path. The
spies assert the gate short-circuits ahead of the real work, not merely that a count
happens to be 0 for some other reason (empty-markets short-circuit, etc.).
"""
from __future__ import annotations

import pytest


def test_reddit_enabled_defaults_false(monkeypatch):
    monkeypatch.delenv("REDDIT_ENABLED", raising=False)
    from config import BotConfig

    assert BotConfig().reddit_enabled is False


def test_reddit_enabled_true_when_env_set(monkeypatch):
    monkeypatch.setenv("REDDIT_ENABLED", "true")
    from config import BotConfig

    assert BotConfig().reddit_enabled is True


async def test_run_reddit_monitor_noops_when_disabled(monkeypatch):
    from config import cfg
    import feeds.reddit_monitor as rm

    monkeypatch.setattr(cfg, "reddit_enabled", False, raising=False)

    def _boom(*args, **kwargs):
        raise AssertionError("aiohttp ClientSession created despite reddit disabled")

    monkeypatch.setattr(rm.aiohttp, "ClientSession", _boom)

    called = False

    async def cb(item):
        nonlocal called
        called = True

    # Gated: returns immediately (the real monitor is an infinite `while True` loop,
    # so a clean return is itself proof of the early-exit). Ungated: hits the spied
    # ClientSession and raises -> the test fails for the right reason.
    result = await rm.run_reddit_monitor(cb)

    assert result is None
    assert called is False


async def test_run_reddit_monitor_proceeds_when_enabled(monkeypatch):
    from config import cfg
    import feeds.reddit_monitor as rm

    monkeypatch.setattr(cfg, "reddit_enabled", True, raising=False)

    reached_session = False

    def _stop(*args, **kwargs):
        nonlocal reached_session
        reached_session = True
        raise RuntimeError("stop after passing the gate")

    monkeypatch.setattr(rm.aiohttp, "ClientSession", _stop)

    async def cb(item):
        return None

    # Re-enable must NOT over-block: the monitor proceeds past the gate to build its
    # session. We stop it there to avoid the infinite poll loop.
    with pytest.raises(RuntimeError):
        await rm.run_reddit_monitor(cb)

    assert reached_session is True


async def test_run_discovery_pass_noops_when_disabled(monkeypatch):
    from config import cfg
    import feeds.subreddit_discovery as sd

    monkeypatch.setattr(cfg, "reddit_enabled", False, raising=False)

    queried = False

    def _spy(markets):
        nonlocal queried
        queried = True
        return []

    monkeypatch.setattr(sd, "_markets_to_queries", _spy)

    # Non-empty markets so the empty-markets short-circuit can't mask the gate;
    # the gate must return ahead of any query construction. db_path=None is safe
    # precisely because the gate returns before any sqlite use.
    count = await sd.run_discovery_pass(["market-sentinel"], None)

    assert count == 0
    assert queried is False
