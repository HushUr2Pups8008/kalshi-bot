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
import re
import shutil
import tempfile
from pathlib import Path

import pytest

_KALSHI_TEST_LOG_DIR: str = ""

# ---------------------------------------------------------------------------
# I-8 Wave-1 automated closure plugin
# ---------------------------------------------------------------------------
# Replaces manual wave-1 canary closure (operator writes PASS line + removes
# xfail decorator) with evidence-driven automated closure. The source-file
# xfail decorators stay; this plugin removes them dynamically from the
# in-memory pytest item when the evidence file shows the canary has passed.
#
# Spec: docs/superpowers/specs/2026-05-23-paper-mode-rapid-learning-framework
#       -design.md §3 I-8.
# Stand-in source: docs/_archive/governance/wave-1-post-deploy-observation-
#   plan.md. I-7 (variance gate calculator, Phase 4) will eventually replace
#   this markdown source with a structured signal.
# ---------------------------------------------------------------------------

# Relative path is INTENTIONAL — resolved against the process CWD at
# read_text() time so the pytester sandbox tests can shadow the real
# evidence file by writing to `pytester.path / docs/_archive/...`. An
# absolute __file__-anchored path would always resolve to the real
# repo's evidence file and bypass the sandbox. The bot's launchd job
# and pytest both run from the repo root, so production resolution is
# correct. Per python-reviewer nit 2: documented (not changed) because
# the __file__-anchored alternative would break test isolation.
_WAVE1_OBSERVATION_PLAN = Path(
    "docs/_archive/governance/wave-1-post-deploy-observation-plan.md"
)

# Wave-1 canary ticket prefixes. Keys are the prefix as it appears in the
# xfail-reason string; values are the prefix as it appears on a PASS line in
# the evidence file. The two happen to coincide today but the indirection
# documents the contract and makes a future rename safe.
_WAVE1_CANARY_TICKETS: dict[str, str] = {
    "OBS-005": "OBS-005",
    "MATCH-001": "MATCH-001",
    "OBS-003": "OBS-003",
    "EXEC-002": "EXEC-002",
}

# A wave-1 xfail reason looks like:
#   "OBS-005 deploy not landed; 24h cooldown validation window ..."
# We match the canary ticket prefix anchored to the literal phrase
# "deploy not landed" so that an unrelated xfail mentioning a ticket name
# in some other context is not mis-stripped.
_WAVE1_REASON_RE = re.compile(
    r"\b(OBS-005|MATCH-001|OBS-003|EXEC-002)\b.*?\bdeploy\s+not\s+landed\b",
    re.IGNORECASE | re.DOTALL,
)

# A wave-1 PASS line looks like:
#   "OBS-005 24h validation: PASS"
# We accept any duration of the form \d+[hd] (hours/days) and tolerate extra
# whitespace + arbitrary case. Format-string template is filled per ticket
# below.
_WAVE1_PASS_LINE_TEMPLATE = r"\b{ticket}\s+\d+[hd]\s+validation:\s*PASS\b"


def _wave1_evidence_text() -> str:
    """Return the observation-plan markdown text, or ``""`` if missing /
    unreadable / not valid UTF-8.

    Fail-soft by design: a missing or corrupt evidence file must not raise
    during pytest collection. The downstream effect is that no canary is
    closed, so the xfail-strict marks remain in place and the suite
    continues with the safe pre-closure behaviour.
    """
    try:
        return _WAVE1_OBSERVATION_PLAN.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _wave1_canaries_passing(evidence_text: str) -> set[str]:
    """Return the set of wave-1 canary ticket prefixes whose PASS line
    appears anywhere in ``evidence_text``.
    """
    passing: set[str] = set()
    if not evidence_text:
        return passing
    for reason_ticket, evidence_ticket in _WAVE1_CANARY_TICKETS.items():
        pattern = _WAVE1_PASS_LINE_TEMPLATE.format(ticket=re.escape(evidence_ticket))
        if re.search(pattern, evidence_text, re.IGNORECASE):
            passing.add(reason_ticket)
    return passing


def pytest_collection_modifyitems(
    config: object, items: list[pytest.Item]
) -> None:
    """I-8: strip xfail-strict marks from wave-1 canaries when the
    evidence file contains the matching ``<TICKET> Nh validation: PASS``
    line.

    Idempotent and side-effect-free except for mutating the in-memory
    ``item.own_markers`` list. The source-file ``@pytest.mark.xfail``
    decorator is not touched.
    """
    passing = _wave1_canaries_passing(_wave1_evidence_text())
    if not passing:
        return
    for item in items:
        marker = item.get_closest_marker("xfail")
        if marker is None:
            continue
        reason = marker.kwargs.get("reason")
        if not isinstance(reason, str) and marker.args:
            reason = marker.args[0] if isinstance(marker.args[0], str) else None
        if not isinstance(reason, str):
            continue
        match = _WAVE1_REASON_RE.search(reason)
        if match is None:
            continue
        ticket = match.group(1).upper()
        if ticket in passing:
            # `item.own_markers` is a documented public list in pytest >=7;
            # list-replacement is the canonical mutation pattern for
            # pytest_collection_modifyitems hooks (per python-reviewer nit 1).
            # If pytest internals shift in a future major, the alternative
            # is item.iter_markers + a fresh add_marker() round-trip.
            item.own_markers = [m for m in item.own_markers if m.name != "xfail"]


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
    # PROFIT-OBS-005: cooldown env stubs removed. The executor now defaults
    # the never-traded sentinel to `float("-inf")` at trading/executor.py:208
    # / :276, so a freshly-booted container with low `time.monotonic()` no
    # longer trips the paper / live cooldown on never-traded tickers.


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
