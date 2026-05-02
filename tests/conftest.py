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

import pytest

_KALSHI_TEST_LOG_DIR: str = ""


def _ci_stub_env() -> None:
    """Populate Kalshi/Ollama env vars when running in CI without a `.env`.

    Local runs already load real values from `.env` via `dotenv` inside
    `config.py`. CI has no `.env`, so `BotConfig.__post_init__` calls
    `sys.exit(1)`. Only stubs when `CI` is set; uses `setdefault` so any
    real value in the runner environment still wins.
    """
    if not os.environ.get("CI"):
        return

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    pem = rsa.generate_private_key(
        public_exponent=65537, key_size=2048
    ).private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode().replace("\n", "\\n")

    os.environ.setdefault("KALSHI_API_KEY_ID", "ci-stub-key-id")
    os.environ.setdefault("KALSHI_API_KEY_SECRET", pem)
    os.environ.setdefault("KALSHI_ENV", "demo")
    os.environ.setdefault("BANKROLL", "1000")
    os.environ.setdefault("OLLAMA_MODEL", "ci-stub-model")
    # Disable cooldown gates in CI: `time.monotonic()` on a freshly-booted
    # container is tiny (seconds since container start), so the executor's
    # `_last_traded.get(ticker, 0.0)` fallback makes never-traded tickers
    # look just-traded. Tests that bypass the `_make_executor` fixture
    # (which monkeypatches these) trip the cooldown spuriously. Tracked as
    # PROFIT-OBS-005 — a latent prod bug for never-traded tickers in the
    # first 4h after bot restart. Stubbing here keeps CI signal honest
    # without touching production behaviour mid-soak.
    os.environ.setdefault("PAPER_TICKER_COOLDOWN", "0")
    os.environ.setdefault("LIVE_TICKER_COOLDOWN", "0")


def pytest_configure(config: object) -> None:
    global _KALSHI_TEST_LOG_DIR
    _KALSHI_TEST_LOG_DIR = tempfile.mkdtemp(prefix="kalshi_test_logs_")
    os.environ["KALSHI_LOG_ROOT"] = _KALSHI_TEST_LOG_DIR
    _ci_stub_env()


def pytest_unconfigure(config: object) -> None:
    os.environ.pop("KALSHI_LOG_ROOT", None)
    if _KALSHI_TEST_LOG_DIR and os.path.isdir(_KALSHI_TEST_LOG_DIR):
        shutil.rmtree(_KALSHI_TEST_LOG_DIR, ignore_errors=True)


@pytest.fixture(autouse=True)
def _reset_governance_global_reader():
    """Ensure tests cannot leak runtime-overrides global-reader state.

    Saves and restores utils.runtime_overrides._global_reader around every test.
    Most tests do not touch it and pay zero cost; the few that do (e.g.,
    tests/test_runtime_overrides_module_helpers.py) get clean isolation.
    """
    from utils import runtime_overrides as ro
    original = ro._global_reader
    yield
    ro._global_reader = original
