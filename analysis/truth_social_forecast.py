"""Bounded Truth Social post-count observations for research-gate evidence.

This module is deliberately an observation provider, not a trading strategy.
It only returns a count when the public Factbase response is structurally
valid and the requested week can be covered by bounded pagination.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import json
import math
import re
import threading
import time
from typing import Any
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo


_ENDPOINT = "https://rollcall.com/wp-json/factbase/v1/twitter"
_PAGE_SIZE = 50
_MAX_RESPONSE_BYTES = 512_000
_DEFAULT_TIMEOUT_SECONDS = 8.0
_DEFAULT_MAX_PAGES = 8
_ET = ZoneInfo("America/New_York")
TRUTH_SOCIAL_RANGE_PROBABILITY_METRIC = "truth_social_range_probability"
TRUTH_SOCIAL_COUNT_METRIC = "truth_social_weekly_post_count"
_LOCKED_YES = 0.98
_LOCKED_NO = 0.02
_MIN_ELAPSED_DAYS = 0.5
_NORMAL_LAMBDA = 50.0
# Remaining posts are not a weekday clone. Haircut the observed daily
# rate so a Friday-Saturday slowdown cannot mint an 80c YES from a 38c
# ask. Locked outcomes (window closed / already outside) are unhaircut.
_REMAINING_RATE_HAIRCUT = 0.80
_OBSERVATION_CACHE_TTL_SECONDS = 45.0
_OBSERVATION_CACHE: dict[
    tuple[date, int], tuple[float, TruthSocialCountObservation | None]
] = {}
_OBSERVATION_CACHE_LOCK = threading.Lock()
_MONTHS = {
    name.lower(): number
    for number, name in enumerate(
        (
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ),
        1,
    )
}


@dataclass(frozen=True)
class TruthSocialCountContract:
    lower: int
    upper: int
    week_start: date
    ticker: str


@dataclass(frozen=True)
class TruthSocialCountObservation:
    count: int
    deleted_count: int
    window_start: date
    window_end: date
    lower: int
    upper: int
    complete: bool
    pages: int
    source_url: str
    observed_at: str

def parse_truth_social_count_contract(query: str) -> TruthSocialCountContract | None:
    """Parse the numeric weekly range and week start from a market query."""
    range_match = re.search(
        r"\bbetween\s+(\d+)\s+and\s+(\d+)\b.*?\btruth\s+social\s+posts?\b",
        query,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if range_match is not None:
        lower, upper = (int(value) for value in range_match.groups())
        if lower > upper:
            return None
    else:
        threshold_match = re.search(
            r"\b(above|at\s+least|more\s+than|greater\s+than|below|at\s+most|"
            r"less\s+than)\s+(\d+)\b.*?\btruth\s+social\s+posts?\b",
            query,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if threshold_match is None:
            return None
        operator, threshold_text = threshold_match.groups()
        threshold = int(threshold_text)
        if operator.lower() in {"below", "at most", "less than"}:
            lower, upper = 0, threshold
        elif operator.lower() == "at least":
            lower, upper = threshold, 10**9
        else:
            lower, upper = threshold + 1, 10**9
    week_match = re.search(
        r"\bweek\s+of\s+"
        r"(January|February|March|April|May|June|July|August|September|October|"
        r"November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)"
        r"\.?\s+(\d{1,2}),?\s+(20\d{2})\b",
        query,
        flags=re.IGNORECASE,
    )
    if week_match is None:
        return None
    month_text, day_text, year_text = week_match.groups()
    month = _MONTHS.get(month_text.lower())
    if month is None:
        month = _MONTHS.get(
            next(
                (name for name in _MONTHS if name.startswith(month_text.lower())),
                "",
            )
        )
    if month is None:
        return None
    try:
        week_start = date(int(year_text), month, int(day_text))
    except ValueError:
        return None
    ticker_match = re.search(r"\bKXTRUTHSOCIAL-[A-Z0-9-]+\b", query, re.IGNORECASE)
    return TruthSocialCountContract(
        lower=lower,
        upper=upper,
        week_start=week_start,
        ticker=ticker_match.group(0).upper() if ticker_match else "KXTRUTHSOCIAL",
    )


def _fetch_json(url: str, *, timeout: float) -> object:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "kalshi-bot-research/1.0",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = response.read(_MAX_RESPONSE_BYTES + 1)
    if len(payload) > _MAX_RESPONSE_BYTES:
        raise ValueError("Factbase response exceeded bounded size")
    return json.loads(payload.decode("utf-8"))


def _record_date(record: dict[str, Any]) -> date | None:
    value = record.get("date")
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def fetch_truth_social_count(
    query: str,
    *,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    max_pages: int = _DEFAULT_MAX_PAGES,
) -> TruthSocialCountObservation | None:
    """Fetch and count Factbase records for the query's seven-day window."""
    contract = parse_truth_social_count_contract(query)
    if contract is None or max_pages < 1:
        return None
    cache_key = (contract.week_start, max_pages)
    now = time.monotonic()
    with _OBSERVATION_CACHE_LOCK:
        cached = _OBSERVATION_CACHE.get(cache_key)
        if cached is not None and now - cached[0] < _OBSERVATION_CACHE_TTL_SECONDS:
            return cached[1]

        observation = _fetch_truth_social_count_uncached(
            contract,
            timeout=timeout,
            max_pages=max_pages,
        )
        if observation is not None:
            _OBSERVATION_CACHE[cache_key] = (time.monotonic(), observation)
        return observation


def _fetch_truth_social_count_uncached(
    contract: TruthSocialCountContract,
    *,
    timeout: float,
    max_pages: int,
) -> TruthSocialCountObservation | None:
    window_end = contract.week_start + timedelta(days=6)
    count = 0
    deleted_count = 0
    pages = 0
    oldest_seen: date | None = None
    for page in range(1, max_pages + 1):
        params = urllib.parse.urlencode(
            {
                "platform": "truth social",
                "sort": "date",
                "sort_order": "desc",
                "page": page,
                "format": "json",
            }
        )
        payload = _fetch_json(f"{_ENDPOINT}?{params}", timeout=timeout)
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise ValueError("Factbase response missing data list")
        rows = payload["data"]
        if not rows:
            break
        pages = page
        for record in rows:
            if not isinstance(record, dict):
                raise ValueError("Factbase data row was not an object")
            observed_date = _record_date(record)
            if observed_date is None:
                raise ValueError("Factbase data row missing valid date")
            oldest_seen = observed_date if oldest_seen is None else min(oldest_seen, observed_date)
            if contract.week_start <= observed_date <= window_end:
                count += 1
                deleted_count += int(bool(record.get("deleted_flag")))
        if oldest_seen < contract.week_start:
            break
    complete = oldest_seen is not None and oldest_seen < contract.week_start
    if not complete:
        return None
    return TruthSocialCountObservation(
        count=count,
        deleted_count=deleted_count,
        window_start=contract.week_start,
        window_end=window_end,
        lower=contract.lower,
        upper=contract.upper,
        complete=True,
        pages=pages,
        source_url=f"{_ENDPOINT}?platform=truth%20social&sort=date&sort_order=desc&format=json",
        observed_at=datetime.now(timezone.utc).isoformat(),
    )


def _clip_probability(value: float) -> float:
    return min(_LOCKED_YES, max(_LOCKED_NO, float(value)))


def _poisson_cdf(k: int, lam: float) -> float:
    if k < 0:
        return 0.0
    if lam <= 0.0:
        return 1.0
    if lam >= _NORMAL_LAMBDA:
        # Continuity-corrected normal tail; Poisson is too heavy for recursion.
        return 0.5 * (1.0 + math.erf((k + 0.5 - lam) / math.sqrt(2.0 * lam)))
    term = math.exp(-lam)
    total = term
    for index in range(1, k + 1):
        term *= lam / index
        total += term
        if total >= 1.0:
            return 1.0
    return min(total, 1.0)


def _poisson_interval_probability(low: int, high: int, lam: float) -> float:
    if high < low:
        return 0.0
    return max(0.0, _poisson_cdf(high, lam) - _poisson_cdf(low - 1, lam))


def truth_social_range_probability(
    observation: TruthSocialCountObservation,
    *,
    now: datetime | None = None,
) -> tuple[float, str, float] | None:
    """Return (p_yes, state, observation_confidence) from a Factbase floor.

    Mid-week counts are a floor on the final week total. A remaining-time
    Poisson using the observed daily rate is the tradeable probability.
    Fail closed on a sub-half-day sample so a thin morning print cannot
    mint a 98c lock.
    """
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current_et = current.astimezone(_ET)
    window_start = datetime.combine(
        observation.window_start, datetime.min.time(), tzinfo=_ET
    )
    window_end = datetime.combine(
        observation.window_end, datetime.max.time().replace(microsecond=0),
        tzinfo=_ET,
    )
    if current_et >= window_end:
        in_range = observation.lower <= observation.count <= observation.upper
        return (
            _LOCKED_YES if in_range else _LOCKED_NO,
            "closed",
            0.95,
        )
    if observation.count > observation.upper:
        return _LOCKED_NO, "already_outside_range", 0.95
    if observation.count >= observation.lower and observation.upper >= 10**8:
        return _LOCKED_YES, "already_above_threshold", 0.95
    elapsed_days = (current_et - window_start).total_seconds() / 86400.0
    remaining_days = (window_end - current_et).total_seconds() / 86400.0
    if elapsed_days < _MIN_ELAPSED_DAYS:
        return None
    if remaining_days <= 0:
        in_range = observation.lower <= observation.count <= observation.upper
        return (
            _LOCKED_YES if in_range else _LOCKED_NO,
            "closed",
            0.95,
        )
    rate = observation.count / elapsed_days
    expected_remaining = rate * remaining_days * _REMAINING_RATE_HAIRCUT
    need_low = max(0, observation.lower - observation.count)
    if observation.upper >= 10**8:
        probability = 1.0 - _poisson_cdf(need_low - 1, expected_remaining)
        state = "open_run_rate_above"
    else:
        need_high = max(0, observation.upper - observation.count)
        probability = _poisson_interval_probability(
            need_low, need_high, expected_remaining
        )
        state = "open_run_rate"
    confidence = 0.85 if remaining_days < 2.0 else 0.82
    return _clip_probability(probability), state, confidence
