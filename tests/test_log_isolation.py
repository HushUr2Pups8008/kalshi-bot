"""
Tests for test-vs-runtime log isolation (v0.29.3).

Verifies that:
  1. Log paths in the test process point to the KALSHI_LOG_ROOT temp dir,
     NOT to the runtime logs/app/bot.log path.
  2. The runtime default (no env var) resolves to BASE_DIR / "logs".

The isolation mechanism is conftest.pytest_configure(), which sets
KALSHI_LOG_ROOT before any test module is imported so module-level constants
in config.py and utils/logger.py are initialized against the temp dir.
"""

import os
import subprocess
import sys
from pathlib import Path

import config
import utils.logger as logger_module


_RUNTIME_DEFAULT_LOGS_DIR = config.BASE_DIR / "logs"


class TestLogIsolation:
    def test_logs_dir_not_runtime_default_in_test_process(self):
        """LOGS_DIR must be redirected to the temp dir during test runs."""
        assert config.LOGS_DIR != _RUNTIME_DEFAULT_LOGS_DIR, (
            "LOGS_DIR still points to the runtime logs/ directory -- "
            "conftest.pytest_configure() did not redirect it before import."
        )

    def test_logs_dir_matches_kalshi_log_root_env(self):
        """LOGS_DIR must equal the KALSHI_LOG_ROOT value set by conftest."""
        root = os.environ.get("KALSHI_LOG_ROOT", "")
        assert root, "KALSHI_LOG_ROOT not set -- conftest.pytest_configure() did not run"
        assert config.LOGS_DIR == Path(root)

    def test_app_log_file_under_temp_dir(self):
        """APP_LOG_FILE must not live under the runtime logs/ directory."""
        assert not str(logger_module.APP_LOG_FILE).startswith(
            str(_RUNTIME_DEFAULT_LOGS_DIR)
        ), (
            f"APP_LOG_FILE {logger_module.APP_LOG_FILE} is under the runtime "
            f"logs/ directory -- tests would contaminate the real bot log."
        )

    def test_runtime_default_path_unchanged(self):
        """Without KALSHI_LOG_ROOT, LOGS_DIR must default to BASE_DIR/logs."""
        env = {k: v for k, v in os.environ.items() if k != "KALSHI_LOG_ROOT"}
        result = subprocess.run(
            [
                sys.executable, "-c",
                "import config; print(config.LOGS_DIR)",
            ],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(config.BASE_DIR),
        )
        assert result.returncode == 0, (
            f"Subprocess failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        expected = str(_RUNTIME_DEFAULT_LOGS_DIR)
        actual   = result.stdout.strip()
        assert actual == expected, (
            f"Runtime default LOGS_DIR mismatch.\n"
            f"  expected: {expected}\n"
            f"  got:      {actual}"
        )
