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
TRUTH_SOCIAL_PHRASE_PROBABILITY_METRIC = "truth_social_phrase_probability"
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


def remaining_threshold_probability(
    *,
    count: int,
    threshold: int,
    elapsed_days: float,
    remaining_days: float,
) -> tuple[float, str, float] | None:
    """P(final count >= threshold) from a known floor and remaining time."""
    if count >= threshold:
        return _LOCKED_YES, "already_above_threshold", 0.95
    if remaining_days <= 0:
        return _LOCKED_NO, "closed", 0.95
    if elapsed_days < _MIN_ELAPSED_DAYS:
        return None
    rate = float(count) / elapsed_days if elapsed_days > 0 else 0.0
    expected_remaining = rate * remaining_days * _REMAINING_RATE_HAIRCUT
    need = max(0, int(threshold) - int(count))
    probability = 1.0 - _poisson_cdf(need - 1, expected_remaining)
    confidence = 0.85 if remaining_days < 2.0 else 0.82
    return _clip_probability(probability), "open_run_rate_above", confidence


@dataclass(frozen=True)
class TruthSocialPhraseContract:
    phrases: tuple[str, ...]
    window_start: date
    window_end: date
    ticker: str


@dataclass(frozen=True)
class TruthSocialPhraseObservation:
    hit: bool
    matched_phrase: str | None
    match_date: date | None
    match_url: str | None
    posts_scanned: int
    window_start: date
    window_end: date
    complete: bool
    pages: int
    source_url: str
    observed_at: str


_MONTH_NAME_PATTERN = (
    r"January|February|March|April|May|June|July|August|"
    r"September|October|November|December|Jan|Feb|Mar|Apr|"
    r"Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec"
)
_PHRASE_SAY_RE = re.compile(
    r"\bwill\s+(?:donald\s+)?trump\s+say\s+"
    r"[\"“]?(?P<phrases>.+?)[\"”]?\s+"
    r"(?:before|by|by\s+the\s+end\s+of)\s+"
    rf"(?P<month>{_MONTH_NAME_PATTERN})\.?\s+"
    r"(?P<day>\d{1,2}),?\s+(?P<year>20\d{2})\b",
    flags=re.IGNORECASE | re.DOTALL,
)
_PHRASE_AFTER_RE = re.compile(
    rf"\bafter\s+(?P<month>{_MONTH_NAME_PATTERN})\.?\s+"
    r"(?P<day>\d{1,2}),?\s+(?P<year>20\d{2})"
    r"(?:\s+at\s+\d{1,2}:\d{2}\s*(?:a\.?m\.?|p\.?m\.?)\s*ET)?",
    flags=re.IGNORECASE,
)
_PHRASE_BEFORE_MIDNIGHT_RE = re.compile(
    rf"\bbefore\s+(?P<month>{_MONTH_NAME_PATTERN})\.?\s+"
    r"(?P<day>\d{1,2}),?\s+(?P<year>20\d{2})"
    r"\s+at\s+12:00\s*a\.?m\.?\s*ET",
    flags=re.IGNORECASE,
)
_POST_WINDOW_CACHE: dict[
    tuple[date, date, int], tuple[float, tuple[dict[str, Any], ...] | None]
] = {}
_POST_WINDOW_CACHE_LOCK = threading.Lock()
_PHRASE_MAX_PAGES = 20
_PHRASE_OBS_CACHE: dict[
    tuple[str, date, date, tuple[str, ...], int],
    tuple[float, TruthSocialPhraseObservation | None],
] = {}
_PHRASE_OBS_CACHE_LOCK = threading.Lock()


def _named_date_from_match(match: re.Match[str]) -> date | None:
    month = _MONTHS.get(match.group("month").lower())
    if month is None:
        month = _MONTHS.get(
            next(
                (
                    name
                    for name in _MONTHS
                    if name.startswith(match.group("month").lower())
                ),
                "",
            )
        )
    if month is None:
        return None
    try:
        return date(int(match.group("year")), month, int(match.group("day")))
    except ValueError:
        return None


def parse_truth_social_phrase_contract(query: str) -> TruthSocialPhraseContract | None:
    """Parse KXTRUMPSAY phrase contracts.

    Title text is 'before DATE' and would start the window on the 1st.
    Settlement rules are 'after DATE at 8:00am ET and before DATE at
    12:00am ET'. Prefer the rules window so a July/early-August post is
    not a locked YES.
    """
    match = _PHRASE_SAY_RE.search(query)
    if match is None:
        return None
    raw_phrases = match.group("phrases")
    phrases = tuple(
        part.strip(" \t\"“”'")
        for part in re.split(r"\s*/\s*", raw_phrases)
        if part.strip(" \t\"“”'")
    )
    if not phrases:
        return None
    window_end = _named_date_from_match(match)
    if window_end is None:
        return None
    window_start = date(window_end.year, window_end.month, 1)
    after_match = _PHRASE_AFTER_RE.search(query)
    if after_match is not None:
        after_date = _named_date_from_match(after_match)
        if after_date is None:
            return None
        window_start = after_date
    midnight_match = _PHRASE_BEFORE_MIDNIGHT_RE.search(query)
    if midnight_match is not None:
        exclusive_end = _named_date_from_match(midnight_match)
        if exclusive_end is None:
            return None
        window_end = exclusive_end - timedelta(days=1)
    if window_start > window_end:
        return None
    ticker_match = re.search(r"\bKXTRUMPSAY[A-Z]*-[A-Z0-9-]+\b", query, re.IGNORECASE)
    return TruthSocialPhraseContract(
        phrases=phrases,
        window_start=window_start,
        window_end=window_end,
        ticker=ticker_match.group(0).upper() if ticker_match else "KXTRUMPSAY",
    )


def _phrase_in_text(phrase: str, text: str) -> bool:
    tokens = re.findall(r"[a-z0-9]+", phrase.lower())
    if not tokens:
        return False
    pattern = r"\b" + r"\s+".join(re.escape(token) for token in tokens) + r"\b"
    return re.search(pattern, text.lower()) is not None


def _fetch_truth_social_posts_in_window(
    window_start: date,
    window_end: date,
    *,
    timeout: float,
    max_pages: int,
) -> tuple[tuple[dict[str, Any], ...], int, bool] | None:
    cache_key = (window_start, window_end, max_pages)
    now = time.monotonic()
    with _POST_WINDOW_CACHE_LOCK:
        cached = _POST_WINDOW_CACHE.get(cache_key)
        if cached is not None and now - cached[0] < _OBSERVATION_CACHE_TTL_SECONDS:
            rows = cached[1]
            if rows is None:
                return None
            return rows, max_pages, True

    posts: list[dict[str, Any]] = []
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
            oldest_seen = (
                observed_date if oldest_seen is None else min(oldest_seen, observed_date)
            )
            if window_start <= observed_date <= window_end:
                posts.append(record)
        if oldest_seen is not None and oldest_seen < window_start:
            break
    complete = oldest_seen is not None and oldest_seen < window_start
    packed = tuple(posts)
    with _POST_WINDOW_CACHE_LOCK:
        _POST_WINDOW_CACHE[cache_key] = (time.monotonic(), packed if posts else None)
    if not posts and not complete:
        return None
    return packed, pages, complete


def fetch_truth_social_phrase(
    query: str,
    *,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    max_pages: int = _PHRASE_MAX_PAGES,
) -> TruthSocialPhraseObservation | None:
    """Scan Factbase post text for a KXTRUMPSAY phrase inside the contract window.

    A hit returns immediately. A miss is only trusted after pagination crosses
    the window start.
    """
    contract = parse_truth_social_phrase_contract(query)
    if contract is None or max_pages < 1:
        return None
    cache_key = (
        contract.ticker,
        contract.window_start,
        contract.window_end,
        contract.phrases,
        max_pages,
    )
    now_mono = time.monotonic()
    with _PHRASE_OBS_CACHE_LOCK:
        cached = _PHRASE_OBS_CACHE.get(cache_key)
        if cached is not None and now_mono - cached[0] < _OBSERVATION_CACHE_TTL_SECONDS:
            return cached[1]
    posts_scanned = 0
    pages = 0
    oldest_seen: date | None = None
    matched_phrase = None
    match_date = None
    match_url = None
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
            oldest_seen = (
                observed_date if oldest_seen is None else min(oldest_seen, observed_date)
            )
            if not (contract.window_start <= observed_date <= contract.window_end):
                continue
            posts_scanned += 1
            text = str(record.get("text") or "")
            for phrase in contract.phrases:
                if _phrase_in_text(phrase, text):
                    matched_phrase = phrase
                    match_date = observed_date
                    url = record.get("post_url") or record.get("account_url")
                    match_url = str(url) if url else None
                    break
            if matched_phrase is not None:
                break
        if matched_phrase is not None:
            break
        if oldest_seen is not None and oldest_seen < contract.window_start:
            break
    complete = oldest_seen is not None and oldest_seen < contract.window_start
    if matched_phrase is None and not complete:
        with _PHRASE_OBS_CACHE_LOCK:
            _PHRASE_OBS_CACHE[cache_key] = (time.monotonic(), None)
        return None
    observation = TruthSocialPhraseObservation(
        hit=matched_phrase is not None,
        matched_phrase=matched_phrase,
        match_date=match_date,
        match_url=match_url,
        posts_scanned=posts_scanned,
        window_start=contract.window_start,
        window_end=contract.window_end,
        complete=True if matched_phrase is not None else complete,
        pages=pages,
        source_url=(
            f"{_ENDPOINT}?platform=truth%20social&sort=date&sort_order=desc&format=json"
        ),
        observed_at=datetime.now(timezone.utc).isoformat(),
    )
    with _PHRASE_OBS_CACHE_LOCK:
        _PHRASE_OBS_CACHE[cache_key] = (time.monotonic(), observation)
    return observation


def truth_social_phrase_probability(
    observation: TruthSocialPhraseObservation,
    *,
    now: datetime | None = None,
) -> tuple[float, str, float]:
    """Hit → locked YES. Closed miss → locked NO. Open miss → remaining prior."""
    if observation.hit:
        return _LOCKED_YES, "phrase_hit", 0.95
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current_et = current.astimezone(_ET)
    window_end = datetime.combine(
        observation.window_end, datetime.max.time().replace(microsecond=0), tzinfo=_ET
    )
    if current_et >= window_end:
        return _LOCKED_NO, "closed_miss", 0.95
    window_start = datetime.combine(
        observation.window_start, datetime.min.time(), tzinfo=_ET
    )
    total_days = max((window_end - window_start).total_seconds() / 86400.0, 1.0)
    remaining_days = max((window_end - current_et).total_seconds() / 86400.0, 0.0)
    # No hit yet. Keep a small remaining chance; do not mint a 50c YES.
    probability = 0.20 * (remaining_days / total_days) * _REMAINING_RATE_HAIRCUT
    return _clip_probability(probability), "open_miss", 0.80
