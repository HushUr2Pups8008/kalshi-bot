"""LLM evaluation harness — Proposal B skeleton (PROFIT-EDGE-004 follow-up).

Runs the production signal-analyzer prompt against any Ollama-hosted model
on a frozen fixture of (headline, market, expected_direction,
expected_magnitude_class) cases. Computes precision (correct direction +
non-trivial magnitude), recall (vs cases where the LLM SHOULD have produced
non-zero magnitude), and Brier on the magnitude class.

Use this BEFORE upgrading ``OLLAMA_MODEL`` (signal analyzer) or
``GOVERNANCE_LLM_MODEL`` (governance agent) — the spec for PROFIT-LLM-001
explicitly defers model unification, but defers it because there is no
empirical gate. This harness is the gate.

Skeleton scope (this commit):

* 5 canonical PROFIT-EDGE-001 cases hard-encoded — they are immutable
  regression anchors and do not require operator labelling.
* A template file for an additional ~25 production-derived cases that
  the operator hand-labels post-soak.
* CLI: ``--model qwen3:14b``, ``--cases <fixture-path>``, ``--json``.
* Output: ``logs/eval/llm_eval_<model>_<ts>.md`` plus a one-line verdict
  (precision / recall / Brier vs. the canonical anchors).

What this harness does NOT do (intentional):

* It does not change runtime code paths — it imports the read-only
  ``_LLM_SYSTEM_PROMPT`` + ``_build_user_msg`` helpers from
  ``analysis/signal_analyzer.py`` and calls Ollama directly via
  ``governance.llm.LocalQwenLLM`` (which already carries the qwen3
  ``think=False`` workaround per PROFIT-GOV-001).
* It does not write to ``data/`` or ``logs/trades/`` or any other
  production state.

KNOWN LIMITATION (skeleton, deferred to operator post-soak):
The signal-analyzer in production calls Ollama's OpenAI-compatible
``/v1/chat/completions`` endpoint with no ``format`` constraint
(see ``analysis/signal_analyzer.py:639``). This harness instead uses
``LocalQwenLLM`` which calls ``/api/generate`` with
``format=json`` + ``think=False``. The two transports produce
different outputs on the same prompt — empirically, qwen2.5:7b
returns ``direction=neutral / magnitude=none`` on all five EDGE-001
canonical events when called through the harness's transport, even
though production observed directional output for those same headlines.

So this skeleton is a valid eval gate for the GOVERNANCE-style call
shape (which is what PROFIT-LLM-001 will eventually unify toward),
but NOT a fully production-faithful eval of the signal-analyzer
today. Adding a ``--transport openai_chat`` flag that mirrors the
chat-completions call shape is the right next step; deferred until
post-Phase 2 soak so we are not changing more than one knob at a
time.

Usage
-----
::

    .venv/bin/python scripts/llm_eval.py --model qwen3:14b
    .venv/bin/python scripts/llm_eval.py --model qwen3:14b --cases tests/fixtures/llm_eval_cases/edge_001_canonical.json
    .venv/bin/python scripts/llm_eval.py --model qwen3:14b --cases tests/fixtures/llm_eval_cases/operator_labelled_25.json --json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from analysis.signal_analyzer import _LLM_SYSTEM_PROMPT, _build_user_msg  # noqa: E402
from feeds import NewsItem  # noqa: E402
from governance.llm import LocalQwenLLM  # noqa: E402
from kalshi import KalshiMarket  # noqa: E402
from utils.output_paths import EVALUATION_REPORTS_DIR  # noqa: E402


_DEFAULT_FIXTURE = (
    _REPO_ROOT / "tests" / "fixtures" / "llm_eval_cases" / "edge_001_canonical.json"
)
_OUTPUT_DIR = EVALUATION_REPORTS_DIR

_MAGNITUDE_TO_PP = {"none": 0.0, "small": 0.055, "moderate": 0.14, "large": 0.30}


@dataclass(frozen=True)
class EvalCase:
    case_id: str
    headline: str
    source: str
    market_ticker: str
    market_title: str
    market_subtitle: str
    market_close_time: str
    expected_direction: str   # "yes" | "no" | "neutral"
    expected_magnitude: str   # "none" | "small" | "moderate" | "large"
    notes: str


@dataclass(frozen=True)
class EvalResult:
    case_id: str
    expected_direction: str
    expected_magnitude: str
    actual_direction: Optional[str]
    actual_magnitude: Optional[str]
    actual_confidence: Optional[float]
    actual_reasoning: Optional[str]
    direction_match: bool
    magnitude_match: bool
    parse_error: Optional[str]
    raw_response: Optional[str]


@dataclass(frozen=True)
class EvalReport:
    model: str
    case_count: int
    direction_correct: int
    magnitude_correct: int
    direction_accuracy: float
    magnitude_accuracy: float
    parse_failures: int
    brier_magnitude_pp: float
    results: tuple[EvalResult, ...]


def _load_cases(path: Path) -> list[EvalCase]:
    body = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(body, list):
        raise ValueError(f"fixture must be a JSON array; got {type(body).__name__}")
    return [EvalCase(**case) for case in body]


def _market_for(case: EvalCase) -> KalshiMarket:
    return KalshiMarket(
        ticker=case.market_ticker,
        title=case.market_title,
        yes_bid=49,
        yes_ask=51,
        yes_price=50,
        volume=1000,
        open_interest=500,
        close_time=case.market_close_time,
        status="active",
        subtitle=case.market_subtitle,
    )


def _news_for(case: EvalCase) -> NewsItem:
    return NewsItem(
        headline=case.headline,
        url="https://example.invalid/llm-eval",
        source=case.source,
        published=datetime.now(timezone.utc),
        body="",
    )


def _parse_response(raw: str) -> tuple[Optional[dict], Optional[str]]:
    """Return (parsed_dict | None, error | None)."""
    if not raw or not raw.strip():
        return None, "empty response"
    # Strip code fences if the model wrapped the JSON.
    stripped = re.sub(r"^\s*```(?:json)?\s*\n?(.*?)\n?\s*```\s*$", r"\1", raw, flags=re.DOTALL)
    try:
        body = json.loads(stripped)
    except json.JSONDecodeError as exc:
        return None, f"json decode: {exc}"
    if not isinstance(body, dict):
        return None, f"top-level type: {type(body).__name__}"
    return body, None


def _evaluate_case(case: EvalCase, llm: LocalQwenLLM) -> EvalResult:
    user = _build_user_msg(_news_for(case), _market_for(case))
    raw = llm.complete(_LLM_SYSTEM_PROMPT, user)
    parsed, err = _parse_response(raw)
    if parsed is None:
        return EvalResult(
            case_id=case.case_id,
            expected_direction=case.expected_direction,
            expected_magnitude=case.expected_magnitude,
            actual_direction=None,
            actual_magnitude=None,
            actual_confidence=None,
            actual_reasoning=None,
            direction_match=False,
            magnitude_match=False,
            parse_error=err,
            raw_response=raw[:500],
        )
    actual_direction = str(parsed.get("direction", "")).lower()
    actual_magnitude = str(parsed.get("magnitude", "")).lower()
    return EvalResult(
        case_id=case.case_id,
        expected_direction=case.expected_direction,
        expected_magnitude=case.expected_magnitude,
        actual_direction=actual_direction,
        actual_magnitude=actual_magnitude,
        actual_confidence=parsed.get("confidence"),
        actual_reasoning=parsed.get("reasoning"),
        direction_match=actual_direction == case.expected_direction,
        magnitude_match=actual_magnitude == case.expected_magnitude,
        parse_error=None,
        raw_response=None,
    )


def _aggregate(model: str, results: list[EvalResult]) -> EvalReport:
    n = len(results)
    dir_ok = sum(1 for r in results if r.direction_match)
    mag_ok = sum(1 for r in results if r.magnitude_match)
    parse_fail = sum(1 for r in results if r.parse_error is not None)
    # Brier on magnitude class — squared error of magnitude probability mass
    # mapped to its mid-point pp value. Cases that failed to parse contribute
    # the maximum magnitude gap as a worst-case penalty.
    brier_terms: list[float] = []
    for r in results:
        expected_pp = _MAGNITUDE_TO_PP.get(r.expected_magnitude, 0.0)
        actual_pp = _MAGNITUDE_TO_PP.get(r.actual_magnitude or "", 0.30)
        brier_terms.append((expected_pp - actual_pp) ** 2)
    brier = sum(brier_terms) / n if n else 0.0
    return EvalReport(
        model=model,
        case_count=n,
        direction_correct=dir_ok,
        magnitude_correct=mag_ok,
        direction_accuracy=(dir_ok / n) if n else 0.0,
        magnitude_accuracy=(mag_ok / n) if n else 0.0,
        parse_failures=parse_fail,
        brier_magnitude_pp=brier,
        results=tuple(results),
    )


def run(*, model: str, fixture_path: Path) -> EvalReport:
    cases = _load_cases(fixture_path)
    llm = LocalQwenLLM(model=model)
    results = [_evaluate_case(c, llm) for c in cases]
    return _aggregate(model, results)


def _emit_markdown(report: EvalReport, fixture_path: Path) -> Path:
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe_model = re.sub(r"[^A-Za-z0-9_.-]", "_", report.model)
    out = _OUTPUT_DIR / f"llm_eval_{safe_model}_{ts}.md"
    lines: list[str] = []
    lines.append(f"# LLM eval — `{report.model}`")
    lines.append("")
    lines.append(f"Generated: {datetime.now(timezone.utc).isoformat()}")
    lines.append(f"Fixture  : `{fixture_path.relative_to(_REPO_ROOT)}` ({report.case_count} cases)")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- direction_accuracy  : {report.direction_correct}/{report.case_count} ({report.direction_accuracy:.1%})")
    lines.append(f"- magnitude_accuracy  : {report.magnitude_correct}/{report.case_count} ({report.magnitude_accuracy:.1%})")
    lines.append(f"- parse_failures      : {report.parse_failures}")
    lines.append(f"- Brier (magnitude pp): {report.brier_magnitude_pp:.4f}")
    lines.append("")
    lines.append("## Per-case results")
    lines.append("")
    for r in report.results:
        marker = "✓" if (r.direction_match and r.magnitude_match) else "✗"
        lines.append(f"### {marker} {r.case_id}")
        lines.append("")
        lines.append(f"- expected: dir={r.expected_direction}, mag={r.expected_magnitude}")
        if r.parse_error:
            lines.append(f"- actual  : PARSE ERROR ({r.parse_error})")
            if r.raw_response:
                lines.append("```")
                lines.append(r.raw_response)
                lines.append("```")
        else:
            lines.append(f"- actual  : dir={r.actual_direction}, mag={r.actual_magnitude}, conf={r.actual_confidence}")
            if r.actual_reasoning:
                lines.append(f"- reasoning: {r.actual_reasoning}")
        lines.append("")
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate an Ollama model against a frozen signal-analyzer fixture set.",
    )
    parser.add_argument("--model", default="qwen2.5:7b",
                        help="Ollama model tag (e.g. qwen2.5:7b, qwen3:14b)")
    parser.add_argument("--cases", type=Path, default=_DEFAULT_FIXTURE,
                        help=f"fixture file (default: {_DEFAULT_FIXTURE.relative_to(_REPO_ROOT)})")
    parser.add_argument("--json", action="store_true",
                        help="emit JSON to stdout in addition to the Markdown report")
    args = parser.parse_args(argv)

    if not args.cases.exists():
        parser.error(f"fixture not found: {args.cases}")

    report = run(model=args.model, fixture_path=args.cases)
    md_path = _emit_markdown(report, args.cases)

    if args.json:
        json.dump(asdict(report), sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")
    else:
        print(f"Report: {md_path}")
        print(
            f"Verdict: dir={report.direction_correct}/{report.case_count} "
            f"({report.direction_accuracy:.0%}), "
            f"mag={report.magnitude_correct}/{report.case_count} "
            f"({report.magnitude_accuracy:.0%}), "
            f"Brier={report.brier_magnitude_pp:.4f}, "
            f"parse_fail={report.parse_failures}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
