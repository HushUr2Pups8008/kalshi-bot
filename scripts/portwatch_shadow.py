"""Paper IMF PortWatch snapshot. No orders and no Grok calls.

Run from the repo root. Output stays under logs/state/portwatch/.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from utils.portwatch_shadow import PAPER_CONTRACTS, record_shadow


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Paper IMF PortWatch snapshot. No orders.")
    parser.add_argument("--out-dir", default="logs/state/portwatch")
    args = parser.parse_args(argv)
    row = record_shadow(now=datetime.now(timezone.utc), out_dir=Path(args.out_dir), contracts=PAPER_CONTRACTS)
    print(f"retrieval_time={row['retrieval_time']}")
    print("publication_time=null")
    print(f"table_vintage_time={row['table_vintage_time']}")
    print(f"evaluation={row['evaluation']} reason={row['evaluation_reason']}")
    print(f"independent_settled_events={row['independent_settled_events']}")
    print(f"market_comparison={row['market_comparison']} fee_net={row['fee_net']}")
    print(f"estimator_supported={row['estimator_supported']}")
    ports = row["ports"]
    if isinstance(ports, list):
        for port in ports:
            if not isinstance(port, dict):
                continue
            print(
                f"port={port.get('portid')} source_supported={port.get('source_supported')} "
                f"rows={port.get('row_count')} through={port.get('observation_end')} "
                f"skip={port.get('skip_reason')}"
            )
    forecasts = row["forecasts"]
    if isinstance(forecasts, list):
        for forecast in forecasts:
            if not isinstance(forecast, dict):
                continue
            print(
                f"event={forecast.get('event_key')} registry={forecast.get('registry_qualified')} "
                f"source={forecast.get('source_supported')} estimator={forecast.get('estimator_supported')} "
                f"point={forecast.get('point_forecast')} uncertainty={forecast.get('uncertainty')} "
                f"strikes={forecast.get('strike_count')} skip={forecast.get('skip_reason')}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
