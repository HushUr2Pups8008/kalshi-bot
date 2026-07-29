"""Run an operator-invoked, bounded Brave Search shadow probe."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


if __package__ in {None, ""}:
    REPO_ROOT = Path(__file__).resolve().parent.parent
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

from utils.bounded_https import fetch_bounded_https_ipv4


BRAVE_HOST = "api.search.brave.com"
BRAVE_ENDPOINT = f"https://{BRAVE_HOST}/res/v1/web/search"
MAX_QUERIES = 30
TIMEOUT_SECONDS = 2.0
MAX_BYTES = 256_000

_INPUT_FIELDS = ("probe_window_id", "ticker", "research_run_id", "query")
_IDENTIFIER_FIELDS = ("probe_window_id", "ticker", "research_run_id")
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class ProbeInputError(ValueError):
    """Raised when the operator-provided input cannot be safely processed."""


@dataclass(frozen=True)
class ProbeInput:
    probe_window_id: str
    ticker: str
    research_run_id: str
    query: str


@dataclass(frozen=True)
class ProbeRecord:
    input_index: int
    probe_window_id: str
    ticker: str
    research_run_id: str
    provider: str
    outcome: str
    duration_ms: int
    http_status: int | None
    body_bytes: int
    schema_valid: bool
    result_count: int
    error_class: str | None


@dataclass(frozen=True)
class ProbeRunResult:
    exit_code: int
    attempts: int
    successes: int


Fetcher = Callable[..., Awaitable[bytes]]


def _object_with_unique_fields(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    record: dict[str, Any] = {}
    for field, value in pairs:
        if field in record:
            raise ProbeInputError("input contains a duplicate field")
        record[field] = value
    return record


def _validate_identifier(value: str, line_number: int) -> None:
    if not _IDENTIFIER_PATTERN.fullmatch(value):
        raise ProbeInputError(f"input line {line_number} has an invalid identifier")


def _validate_query(value: str, line_number: int) -> None:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ProbeInputError(f"input line {line_number} has an invalid query") from exc
    if any(ord(character) < 32 or 127 <= ord(character) <= 159 for character in value):
        raise ProbeInputError(f"input line {line_number} has an invalid query")


def _load_inputs(input_path: Path) -> list[ProbeInput]:
    try:
        lines = input_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ProbeInputError("input file cannot be read") from exc

    if len(lines) > MAX_QUERIES:
        raise ProbeInputError(f"input contains more than {MAX_QUERIES} rows")

    inputs: list[ProbeInput] = []
    research_run_ids: set[str] = set()
    expected_fields = set(_INPUT_FIELDS)
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            raise ProbeInputError(f"input line {line_number} is blank")
        try:
            parsed = json.loads(line, object_pairs_hook=_object_with_unique_fields)
        except ProbeInputError:
            raise
        except json.JSONDecodeError as exc:
            raise ProbeInputError(f"input line {line_number} is malformed") from exc

        if not isinstance(parsed, dict) or set(parsed) != expected_fields:
            raise ProbeInputError(f"input line {line_number} has invalid fields")

        values: dict[str, str] = {}
        for field in _INPUT_FIELDS:
            value = parsed[field]
            if not isinstance(value, str) or not value.strip():
                raise ProbeInputError(f"input line {line_number} has a blank required field")
            if field in _IDENTIFIER_FIELDS:
                _validate_identifier(value, line_number)
            elif field == "query":
                _validate_query(value, line_number)
            values[field] = value

        research_run_id = values["research_run_id"]
        if research_run_id in research_run_ids:
            raise ProbeInputError("input contains a duplicate research run identifier")
        research_run_ids.add(research_run_id)
        inputs.append(ProbeInput(**values))
    return inputs


def _duration_ms(started_at: float) -> int:
    return max(0, round((time.monotonic() - started_at) * 1000))


def _record_payload(record: ProbeRecord) -> dict[str, object]:
    return {
        "type": "BRAVE_SEARCH_SHADOW_ATTEMPT",
        "shadow_only": True,
        "admission_path": "none",
        "evidence_persisted": False,
        "paper_review_enqueued": False,
        "input_index": record.input_index,
        "probe_window_id": record.probe_window_id,
        "ticker": record.ticker,
        "research_run_id": record.research_run_id,
        "provider": record.provider,
        "outcome": record.outcome,
        "duration_ms": record.duration_ms,
        "http_status": record.http_status,
        "body_bytes": record.body_bytes,
        "schema_valid": record.schema_valid,
        "result_count": record.result_count,
        "error_class": record.error_class,
    }


def _p95(values: Sequence[int]) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[math.ceil(0.95 * len(ordered)) - 1]


def _summary_payload(records: Sequence[ProbeRecord]) -> dict[str, object]:
    durations = [record.duration_ms for record in records]
    successful_durations = [record.duration_ms for record in records if record.outcome == "success"]
    return {
        "type": "BRAVE_SEARCH_SHADOW_SUMMARY",
        "shadow_only": True,
        "admission_path": "none",
        "evidence_persisted": False,
        "paper_review_enqueued": False,
        "attempts": len(records),
        "successes": len(successful_durations),
        "probe_window_ids": sorted({record.probe_window_id for record in records}),
        "p95_duration_ms": _p95(durations),
        "p95_success_duration_ms": _p95(successful_durations),
    }


def _write_output(output_path: Path, records: Sequence[ProbeRecord]) -> None:
    payloads = [_record_payload(record) for record in records]
    payloads.append(_summary_payload(records))
    rendered = "".join(json.dumps(payload, separators=(",", ":")) + "\n" for payload in payloads)
    output_path.write_text(rendered, encoding="utf-8")


def _reserve_output(output_path: Path) -> Path:
    if output_path.is_dir() or not output_path.parent.is_dir():
        raise ProbeInputError("output destination cannot be reserved")
    try:
        descriptor, staged_path = tempfile.mkstemp(
            dir=output_path.parent,
            prefix=f".{output_path.name}.",
            suffix=".tmp",
        )
    except OSError as exc:
        raise ProbeInputError("output destination cannot be reserved") from exc
    os.close(descriptor)
    return Path(staged_path)


async def run_probe(
    input_path: Path,
    output_path: Path,
    *,
    enabled: bool,
    api_key: str,
    fetcher: Fetcher = fetch_bounded_https_ipv4,
) -> ProbeRunResult:
    """Run serial, bounded provider calls after a complete input preflight."""
    if not enabled or not api_key.strip():
        return ProbeRunResult(exit_code=2, attempts=0, successes=0)

    inputs = _load_inputs(input_path)
    staged_output_path = _reserve_output(output_path)
    try:
        records: list[ProbeRecord] = []
        for input_index, probe_input in enumerate(inputs):
            query_string = urllib.parse.urlencode(
                {
                    "q": probe_input.query,
                    "count": 3,
                    "country": "US",
                    "search_lang": "en",
                }
            )
            url = f"{BRAVE_ENDPOINT}?{query_string}"
            started_at = time.monotonic()
            try:
                response = await fetcher(
                    url,
                    canonical_host=BRAVE_HOST,
                    provider_name="Brave Search API Shadow",
                    user_agent="kalshi-bot-brave-shadow/1.0",
                    timeout=TIMEOUT_SECONDS,
                    max_bytes=MAX_BYTES,
                    request_headers={
                        "Accept": "application/json",
                        "X-Subscription-Token": api_key,
                    },
                )
            except TimeoutError:
                records.append(
                    ProbeRecord(
                        input_index=input_index,
                        probe_window_id=probe_input.probe_window_id,
                        ticker=probe_input.ticker,
                        research_run_id=probe_input.research_run_id,
                        provider="brave_search",
                        outcome="timeout",
                        duration_ms=_duration_ms(started_at),
                        http_status=None,
                        body_bytes=0,
                        schema_valid=False,
                        result_count=0,
                        error_class="TimeoutError",
                    )
                )
                continue
            except urllib.error.HTTPError as exc:
                http_status = exc.code if isinstance(exc.code, int) else None
                records.append(
                    ProbeRecord(
                        input_index=input_index,
                        probe_window_id=probe_input.probe_window_id,
                        ticker=probe_input.ticker,
                        research_run_id=probe_input.research_run_id,
                        provider="brave_search",
                        outcome="http_error",
                        duration_ms=_duration_ms(started_at),
                        http_status=http_status,
                        body_bytes=0,
                        schema_valid=False,
                        result_count=0,
                        error_class="HTTPError",
                    )
                )
                continue
            except Exception:
                records.append(
                    ProbeRecord(
                        input_index=input_index,
                        probe_window_id=probe_input.probe_window_id,
                        ticker=probe_input.ticker,
                        research_run_id=probe_input.research_run_id,
                        provider="brave_search",
                        outcome="provider_exception",
                        duration_ms=_duration_ms(started_at),
                        http_status=None,
                        body_bytes=0,
                        schema_valid=False,
                        result_count=0,
                        error_class="ProviderError",
                    )
                )
                continue

            duration_ms = _duration_ms(started_at)
            if not isinstance(response, bytes):
                records.append(
                    ProbeRecord(
                        input_index=input_index,
                        probe_window_id=probe_input.probe_window_id,
                        ticker=probe_input.ticker,
                        research_run_id=probe_input.research_run_id,
                        provider="brave_search",
                        outcome="malformed_response",
                        duration_ms=duration_ms,
                        http_status=None,
                        body_bytes=0,
                        schema_valid=False,
                        result_count=0,
                        error_class="ProviderError",
                    )
                )
                continue

            try:
                parsed_response = json.loads(response)
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                records.append(
                    ProbeRecord(
                        input_index=input_index,
                        probe_window_id=probe_input.probe_window_id,
                        ticker=probe_input.ticker,
                        research_run_id=probe_input.research_run_id,
                        provider="brave_search",
                        outcome="malformed_response",
                        duration_ms=duration_ms,
                        http_status=None,
                        body_bytes=len(response),
                        schema_valid=False,
                        result_count=0,
                        error_class=type(exc).__name__,
                    )
                )
                continue

            web = parsed_response.get("web") if isinstance(parsed_response, dict) else None
            results = web.get("results") if isinstance(web, dict) else None
            if not isinstance(results, list):
                records.append(
                    ProbeRecord(
                        input_index=input_index,
                        probe_window_id=probe_input.probe_window_id,
                        ticker=probe_input.ticker,
                        research_run_id=probe_input.research_run_id,
                        provider="brave_search",
                        outcome="malformed_response",
                        duration_ms=duration_ms,
                        http_status=None,
                        body_bytes=len(response),
                        schema_valid=False,
                        result_count=0,
                        error_class="ProviderError",
                    )
                )
                continue

            records.append(
                ProbeRecord(
                    input_index=input_index,
                    probe_window_id=probe_input.probe_window_id,
                    ticker=probe_input.ticker,
                    research_run_id=probe_input.research_run_id,
                    provider="brave_search",
                    outcome="success",
                    duration_ms=duration_ms,
                    http_status=None,
                    body_bytes=len(response),
                    schema_valid=True,
                    result_count=len(results),
                    error_class=None,
                )
            )

        _write_output(staged_output_path, records)
        os.replace(staged_output_path, output_path)
        successes = sum(record.outcome == "success" for record in records)
        return ProbeRunResult(exit_code=0, attempts=len(records), successes=successes)
    finally:
        staged_output_path.unlink(missing_ok=True)


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    return parser.parse_args(argv)


def _print_summary(result: ProbeRunResult, output_path: Path | None = None) -> None:
    summary = (
        "BRAVE_SEARCH_SHADOW_RUN "
        f"exit_code={result.exit_code} attempts={result.attempts} successes={result.successes}"
    )
    if result.exit_code == 0 and output_path is not None:
        summary = f"{summary} output_path={output_path}"
    print(summary)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    input_path = Path(args.input)
    output_path = Path(args.output)
    enabled = os.environ.get("ENABLE_BRAVE_SEARCH_SHADOW") == "true"
    api_key = os.environ.get("BRAVE_SEARCH_API_KEY", "")

    if not args.execute or not input_path.is_absolute() or not output_path.is_absolute():
        result = ProbeRunResult(exit_code=2, attempts=0, successes=0)
        _print_summary(result)
        return result.exit_code

    if not enabled or not api_key.strip():
        result = ProbeRunResult(exit_code=2, attempts=0, successes=0)
        _print_summary(result)
        return result.exit_code

    try:
        result = asyncio.run(
            run_probe(
                input_path,
                output_path,
                enabled=enabled,
                api_key=api_key,
            )
        )
    except (OSError, ProbeInputError):
        result = ProbeRunResult(exit_code=2, attempts=0, successes=0)
        _print_summary(result)
        return result.exit_code

    _print_summary(result, output_path)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
