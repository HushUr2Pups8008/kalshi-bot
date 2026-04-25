"""Asyncio poll task: hot-reload runtime_overrides.yaml every N seconds.

Lifecycle owned by main.py — task is started during bot startup as part
of the bot's task group and cancelled on shutdown. Failures inside the
poll loop are caught and logged; the task itself never propagates an
exception.

See docs/superpowers/specs/2026-04-24-llm-governance-agent-design.md §7.
"""

from __future__ import annotations

import asyncio

from utils.logger import get_logger
from utils.runtime_overrides import RuntimeOverridesReader

# Use the project's logger factory so messages reach bot.log + errors.log.
# Raw `logging.getLogger(...)` returns a logger with no handlers, and the
# root logger has no handlers either, so its records are silently dropped.
log = get_logger("runtime_overrides_task")


def _format_diff_for_log(diff) -> str:
    parts: list[str] = []
    if diff.sources_added:
        parts.append(f"+sources={sorted(diff.sources_added)}")
    if diff.sources_removed:
        parts.append(f"-sources={sorted(diff.sources_removed)}")
    if diff.keywords_added:
        parts.append(f"+keywords={sorted(diff.keywords_added)}")
    if diff.keywords_removed:
        parts.append(f"-keywords={sorted(diff.keywords_removed)}")
    if diff.thresholds_added:
        parts.append(f"+thresholds={sorted(diff.thresholds_added)}")
    if diff.thresholds_removed:
        parts.append(f"-thresholds={sorted(diff.thresholds_removed)}")
    return " ".join(parts) if parts else "(no changes)"


async def run_runtime_overrides_poll(
    reader: RuntimeOverridesReader,
    interval_secs: float = 600.0,
) -> None:
    """Poll the overrides file every interval_secs and reload on change.

    Runs forever until cancelled. Catches all exceptions inside the
    loop body; does NOT propagate (a malformed YAML file should not
    crash the bot's async task group).
    """
    log.info("runtime_overrides poll task started (interval=%.1fs)", interval_secs)
    while True:
        try:
            diff = reader.reload()
            if not diff.is_empty():
                log.info("runtime overrides reloaded: %s", _format_diff_for_log(diff))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("runtime_overrides reload failed; previous state preserved: %s", exc)
        try:
            await asyncio.sleep(interval_secs)
        except asyncio.CancelledError:
            raise
