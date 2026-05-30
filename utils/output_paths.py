"""Central output-path contract for runtime telemetry, reports, and artifacts."""

from __future__ import annotations

import os
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


def _env_path(name: str) -> Path | None:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return None
    return Path(raw).expanduser()


# KALSHI_LOG_ROOT is retained as the compatibility alias for existing tests and
# ad-hoc scripts. New callers should use KALSHI_OUTPUT_ROOT.
OUTPUT_ROOT = (
    _env_path("KALSHI_OUTPUT_ROOT")
    or _env_path("KALSHI_LOG_ROOT")
    or REPO_ROOT / "logs"
)

RAW_APP_DIR = OUTPUT_ROOT / "app"
RAW_TRADES_DIR = OUTPUT_ROOT / "trades"
RAW_TRADES_LIVE_DIR = RAW_TRADES_DIR / "live"
RAW_TRADES_ARCHIVE_DIR = RAW_TRADES_DIR / "archive"
RAW_GOVERNANCE_DIR = OUTPUT_ROOT / "governance"
RAW_EDGE_REPLAY_DIR = OUTPUT_ROOT / "edge_replay"

REPORTS_ROOT = OUTPUT_ROOT / "reports"
DAILY_REPORTS_DIR = REPORTS_ROOT / "daily"
HEALTH_REPORTS_DIR = REPORTS_ROOT / "health"
PERFORMANCE_REPORTS_DIR = REPORTS_ROOT / "performance"
EVALUATION_REPORTS_DIR = REPORTS_ROOT / "evaluations"

STATE_ROOT = OUTPUT_ROOT / "state"
DERIVED_STATE_DIR = STATE_ROOT / "derived"

BACKUPS_ROOT = OUTPUT_ROOT / "backups"
DB_SNAPSHOTS_DIR = BACKUPS_ROOT / "db_snapshots"

# Canonical DB files remain in repo data/ until an explicit DB cutover is
# separately approved and run. Derived outputs should use DERIVED_STATE_DIR.
DB_STATE_DIR = REPO_ROOT / "data"
PAPER_TRADES_DB = DB_STATE_DIR / "paper_trades.db"
EVIDENCE_STORE_DB = DB_STATE_DIR / "evidence_store.db"
RUNTIME_STATE_DIR = DB_STATE_DIR / "runtime"

RAW_PATHS = {
    "app": RAW_APP_DIR,
    "trades": RAW_TRADES_DIR,
    "governance": RAW_GOVERNANCE_DIR,
    "edge_replay": RAW_EDGE_REPLAY_DIR,
}

REPORT_PATHS = {
    "daily": DAILY_REPORTS_DIR,
    "health": HEALTH_REPORTS_DIR,
    "performance": PERFORMANCE_REPORTS_DIR,
    "evaluations": EVALUATION_REPORTS_DIR,
}

OUTPUT_CONTRACT_PATHS = {
    **RAW_PATHS,
    **REPORT_PATHS,
    "state_derived": DERIVED_STATE_DIR,
    "db_snapshots": DB_SNAPSHOTS_DIR,
}

