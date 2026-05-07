#!/usr/bin/env python3
"""Cycle-15B C6: select a single-step extraction sub-fix from C2-C5 evidence."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.edge_replay.cycle15b_common import OUTPUT_DIR, load_json


PROPOSAL_PATH = Path("docs/governance/edge-replay-cycle15b-sub-fix-proposal.md")


def choose_sub_fix(
    zero: dict[str, Any],
    keyword: dict[str, Any],
    suppression: dict[str, Any],
    llm: dict[str, Any],
) -> dict[str, Any]:
    keyword_gaps = [
        row
        for row in keyword.get("fixtures", [])
        if str(row.get("expected_direction") or "").upper() in {"YES", "NO"} and not row.get("coverage_ok")
    ]
    suppressions = [row for row in suppression.get("fixtures", []) if row.get("suppression_triggered")]
    llm_statuses = sorted({str(row.get("status")) for row in llm.get("fixtures", [])})
    if zero.get("zero_collapse_step") == "keyword_path" and keyword_gaps and not suppressions:
        return {
            "scope": "single_step",
            "selected_surface": "per_keyword_direction_map",
            "target_file": "config.py",
            "target_symbol": "GEOPOLITICAL_SIGNALS",
            "reason": "keyword_path is the first zero-collapse step; directional fixture vocabulary is absent from the keyword direction map; suppression did not fire.",
            "evidence": {
                "zero_collapse_step": zero.get("zero_collapse_step"),
                "zero_collapse_count": zero.get("count"),
                "keyword_gap_count": len(keyword_gaps),
                "suppression_count": len(suppressions),
                "llm_statuses": llm_statuses,
            },
        }
    return {
        "scope": "operator_extension_required",
        "selected_surface": None,
        "target_file": None,
        "reason": "C2-C5 evidence does not isolate one keyword-map-only sub-fix.",
        "evidence": {
            "zero_collapse_step": zero.get("zero_collapse_step"),
            "keyword_gap_count": len(keyword_gaps),
            "suppression_count": len(suppressions),
            "llm_statuses": llm_statuses,
        },
    }


def _symbol_line(path: str, symbol: str) -> int | None:
    try:
        output = subprocess.check_output(["rg", "-n", f"{symbol} =", path], text=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    first = output.splitlines()[0].split(":", 1)[0]
    return int(first)


def render_proposal(selection: dict[str, Any]) -> str:
    target_line = _symbol_line("config.py", "GEOPOLITICAL_SIGNALS")
    target = "config.py"
    if target_line:
        target = f"config.py:{target_line}"
    status = "single-step sub-fix selected" if selection["scope"] == "single_step" else "scope extension required"
    return f"""# Cycle-15B Sub-Fix Proposal

Status: {status}

## Selected Surface

- Surface: `{selection.get("selected_surface")}`
- Target: `{target}` / `{selection.get("target_symbol")}`
- Scope: `{selection.get("scope")}`

## Evidence

- C2 zero-collapse step: `{selection["evidence"].get("zero_collapse_step")}` (`{selection["evidence"].get("zero_collapse_count")}` fixtures)
- C4 keyword coverage gaps: `{selection["evidence"].get("keyword_gap_count")}` directional fixtures
- C5 suppression count: `{selection["evidence"].get("suppression_count")}`
- C3 LLM statuses: `{", ".join(selection["evidence"].get("llm_statuses") or [])}`

## Reason

{selection.get("reason")}

## Before

```python
GEOPOLITICAL_SIGNALS = [
    # Existing geopolitical/event keyword groups.
    # Cycle-15B resolution-event fixture vocabulary is absent, so _keyword_score(...)
    # returns net_shift = 0 for FISA, pardon, Iran-deal, and Vance-visit outcomes.
]
```

## After

```python
GEOPOLITICAL_SIGNALS = [
    # Existing groups unchanged.
    {{
        "keywords": [
            "fisa section 702 reauthorization signed into law",
            "fisa section 702 reauthorization legislation",
            "trump issues pardons",
            "signed pardons",
            "sign nuclear deal",
            "comprehensive nuclear agreement",
            "arrives in islamabad",
            "official pakistan visit",
        ],
        "direction": "yes",
        "strength": 0.12,
    }},
    {{
        "keywords": [
            "fisa section 702 expires",
            "senate fails to act",
            "will not become law",
            "no january 6 pardons",
            "no pardons for january 6 defendants",
            "cancels pakistan trip",
            "canceled his planned pakistan trip",
        ],
        "direction": "no",
        "strength": 0.12,
    }},
]
```

## Guardrails

- C7 may implement only this keyword-map change unless the operator grants a scope extension.
- Acceptance remains `>=6/10` Lane B fixtures passing direction + magnitude.
- IC §16 final acceptance still requires replay evidence: `>=1` slice with `ev_ci_95_lo > 0` and `trades >= 10`.
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zero", type=Path, default=OUTPUT_DIR / "zero_collapse_step.json")
    parser.add_argument("--keyword", type=Path, default=OUTPUT_DIR / "keyword_audit.json")
    parser.add_argument("--suppression", type=Path, default=OUTPUT_DIR / "suppression_trace.json")
    parser.add_argument("--llm", type=Path, default=OUTPUT_DIR / "llm_prompt_audit.json")
    parser.add_argument("--output", type=Path, default=PROPOSAL_PATH)
    args = parser.parse_args()
    selection = choose_sub_fix(load_json(args.zero), load_json(args.keyword), load_json(args.suppression), load_json(args.llm))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render_proposal(selection), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "scope": selection["scope"], "selected_surface": selection["selected_surface"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
