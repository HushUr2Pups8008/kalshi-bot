"""Read-only validation of one persisted research timeout diagnostic."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path

from analysis.research_timeout_replay import (
    ResearchTimeoutReplayResult,
    replay_persisted_timeout,
)
from tasks.research_dossier import DEFAULT_RESEARCH_DOSSIER_DB_PATH


def render_text(result: ResearchTimeoutReplayResult) -> str:
    return "\n".join(
        (
            "RESEARCH TIMEOUT REPLAY",
            f"  Run:                 {result.research_run_id or 'n/a'}",
            f"  Replayable:          {result.replayable}",
            f"  Reason:              {result.reason or 'n/a'}",
            f"  Timeout stage:       {result.timeout_stage or 'n/a'}",
            f"  Expected status:     {result.expected_status or 'n/a'}",
            f"  Skip reason:         {result.skip_reason or 'n/a'}",
            f"  Candidate eligible:  {result.candidate_eligible}",
            f"  Cache eligible:      {result.cache_eligible}",
            f"  Admission eligible:  {result.admission_eligible}",
            f"  Captured queries:    {result.query_count}",
            f"  Captured evidence:   {result.evidence_count}",
            f"  Input SHA-256:       {result.input_sha256 or 'n/a'}",
        )
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True, help="Persisted research run identifier")
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_RESEARCH_DOSSIER_DB_PATH,
        help=f"Research dossier DB path (default: {DEFAULT_RESEARCH_DOSSIER_DB_PATH})",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text")
    args = parser.parse_args(argv)

    result = replay_persisted_timeout(args.db, args.run_id)
    if args.json:
        print(json.dumps(asdict(result), indent=2, sort_keys=True))
    else:
        print(render_text(result))
    return 0 if result.replayable else 1


if __name__ == "__main__":
    raise SystemExit(main())
