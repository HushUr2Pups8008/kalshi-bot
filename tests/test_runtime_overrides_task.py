"""Tests for the asyncio poll task that hot-reloads the overrides file."""

from __future__ import annotations

import asyncio
import logging
import textwrap
from pathlib import Path

import pytest

from tasks.runtime_overrides_task import run_runtime_overrides_poll
from utils.runtime_overrides import RuntimeOverridesReader


VALID_YAML = textwrap.dedent("""
    version: 1
    updated_at: "2026-05-02T14:30:00+00:00"
    updated_by: "test"
    mode: shadow
    applied:
      disabled_sources: []
      disabled_keywords: []
      threshold_overrides: []
    proposed:
      disabled_sources: []
      disabled_keywords: []
      threshold_overrides: []
""").lstrip()


@pytest.mark.asyncio
async def test_polls_and_reloads(tmp_path: Path):
    p = tmp_path / "overrides.yaml"
    p.write_text(VALID_YAML)
    reader = RuntimeOverridesReader(path=p)

    # Run the poll task as a background task with a tiny interval; cancel
    # after a couple of cycles.
    task = asyncio.create_task(
        run_runtime_overrides_poll(reader, interval_secs=0.05)
    )
    await asyncio.sleep(0.15)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    # Final state should be loaded
    assert reader.snapshot().version == 1


@pytest.mark.asyncio
async def test_malformed_file_does_not_crash_task(tmp_path: Path, caplog):
    p = tmp_path / "overrides.yaml"
    p.write_text(VALID_YAML)
    reader = RuntimeOverridesReader(path=p)
    reader.reload()  # initial valid state

    # Corrupt the file
    p.write_text("not: valid: yaml: : :")

    with caplog.at_level(logging.WARNING):
        task = asyncio.create_task(
            run_runtime_overrides_poll(reader, interval_secs=0.05)
        )
        await asyncio.sleep(0.15)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    # Task survived; warning logged
    assert any("malformed" in r.message.lower() or "yaml" in r.message.lower() for r in caplog.records)


@pytest.mark.asyncio
async def test_diff_logged_on_change(tmp_path: Path, caplog):
    p = tmp_path / "overrides.yaml"
    p.write_text(VALID_YAML)
    reader = RuntimeOverridesReader(path=p)

    yaml_with_source = VALID_YAML.replace(
        "disabled_sources: []",
        textwrap.dedent("""
            disabled_sources:
                - source: "r/Test"
                  reason: "x"
                  confidence: 0.9
                  decided_at: "2026-05-02T14:30:00+00:00"
                  decided_by: "test"
                  decision_id: "gd_2026-05-02_0099"
                  expires_at: null
                  predicted_effect:
                    metric: "m"
                    baseline: 0
                    predicted_post_change: 0
                    evaluate_at: "2026-05-09T14:30:00+00:00"
        """).strip(),
        1,  # only the first occurrence (the `applied` one)
    )

    with caplog.at_level(logging.INFO):
        task = asyncio.create_task(
            run_runtime_overrides_poll(reader, interval_secs=0.05)
        )
        await asyncio.sleep(0.10)
        p.write_text(yaml_with_source)
        await asyncio.sleep(0.15)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert any("r/Test" in r.message for r in caplog.records)
