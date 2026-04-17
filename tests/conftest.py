"""
Session-scoped pytest configuration for the kalshi-bot test suite.

Isolation guarantee: sets KALSHI_LOG_ROOT to a temp directory before any test
module is imported, so every logger and path constant in utils/logger.py and
config.py resolves to the temp dir instead of logs/app/bot.log.

This runs via pytest_configure(), which fires before collection (i.e. before
any test file is imported), so module-level singletons in utils/logger.py are
always initialized against the temp path, never the runtime bot.log path.
"""

import os
import shutil
import tempfile

_KALSHI_TEST_LOG_DIR: str = ""


def pytest_configure(config: object) -> None:
    global _KALSHI_TEST_LOG_DIR
    _KALSHI_TEST_LOG_DIR = tempfile.mkdtemp(prefix="kalshi_test_logs_")
    os.environ["KALSHI_LOG_ROOT"] = _KALSHI_TEST_LOG_DIR


def pytest_unconfigure(config: object) -> None:
    os.environ.pop("KALSHI_LOG_ROOT", None)
    if _KALSHI_TEST_LOG_DIR and os.path.isdir(_KALSHI_TEST_LOG_DIR):
        shutil.rmtree(_KALSHI_TEST_LOG_DIR, ignore_errors=True)
