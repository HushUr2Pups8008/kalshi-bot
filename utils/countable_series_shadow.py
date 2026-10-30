"""Paper-shadow probability for two countable series. No Grok and no orders.

Daily counts are overdispersed, so remaining volume is a negative binomial
fit by method of moments on days before the window. Poisson is recorded
only when that sample is not overdispersed. The public feed matches
week-level settlement. It is not the official counter at each minute, and
nothing here enters the queue.
"""

from __future__ import annotations

import json
import math
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
COUNTABLE = frozenset({"KXTRUTHSOCIAL", "KXTRUMPACT", "KXEOWEEK"})
# These series are fetched for the free count shadow. They are not Grok inputs.
PAPER_COUNT_ONLY = frozenset({"KXEOWEEK"})
TRUTH_URL = (
    "https://rollcall.com/wp-json/factbase/v1/twitter"
    "?platform=truth%20social&sort=date&sort_order=desc&format=json&page={page}"
)
ACTION_URL = "https://www.whitehouse.gov/presidential-actions/feed/"
EO_URL = (
    "https://www.federalregister.gov/api/v1/documents.json"
    "?per_page=1000&order=oldest"
    "&conditions[type][]=PRESDOCU"
    "&conditions[presidential_document_type][]=executive_order"
    "&conditions[signing_date][gte]=2025-01-01"
    "&fields[]=executive_order_number&fields[]=signing_date"
)
SHADOW_PATH = Path("logs/state/countable_shadow.jsonl")
MIN_DAYS = 5
_HOSTS = frozenset({"rollcall.com", "www.whitehouse.gov", "www.federalregister.gov"})
_MONTHS = {name: index for index, name in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1
)}
Fetch = Callable[[str], str]


def _get(market: object, name: str) -> object:
    return market.get(name) if isinstance(market, Mapping) else getattr(market, name, None)


def _series(market: object) -> str:
    text = str(_get(market, "series_ticker") or _get(market, "series") or "").strip().upper()
    return text or str(_get(market, "ticker") or "").split("-", 1)[0].upper()


def _rules(market: object) -> str:
    return str(_get(market, "rules_primary") or _get(market, "rules") or "").strip()


def _ask(market: object) -> int | None:
    raw = _get(market, "yes_ask_cents")
    if not isinstance(raw, int) or isinstance(raw, bool):
        raw = _get(market, "yes_ask")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    if isinstance(raw, float) and not raw.is_integer():
        return None
    cents = int(raw)
    return cents if 0 <= cents <= 100 else None


def _day(text: str) -> date | None:
    parts = text.replace(",", "").split()
    month = _MONTHS.get(parts[0][:3].lower()) if len(parts) == 3 else None
    if month is None or not parts[1].isdigit() or not parts[2].isdigit():
        return None
    try:
        return date(int(parts[2]), month, int(parts[1]))
    except ValueError:
        return None


def parse_contract(rules: str, strike_type: str = "") -> dict[str, object] | None:
    """Window and strike from the market rules only."""
    found = re.search(
        r"(?:from|during) ([A-Z][a-z]+ \d{1,2}, \d{4}) (?:to|through) ([A-Z][a-z]+ \d{1,2}, \d{4})",
        rules,
    )
    if found is None:
        return None
    start, end = _day(found.group(1)), _day(found.group(2))
    if start is None or end is None or end < start:
        return None
    between = re.search(r"\bbetween (\d+) and (\d+)\b", rules)
    if between is not None and int(between.group(2)) >= int(between.group(1)):
        return {"start": start, "end": end, "how": "between", "low": int(between.group(1)), "high": int(between.group(2))}
    above, least = re.search(r"\babove (\d+)\b", rules), re.search(r"\bat least (\d+)\b", rules)
    if above is not None and strike_type.strip().lower() != "greater_or_equal":
        return {"start": start, "end": end, "how": "gt", "strike": int(above.group(1))}
    if least is not None:
        return {"start": start, "end": end, "how": "ge", "strike": int(least.group(1))}
    return None


def _law(mean: float, var: float) -> tuple[str, float, float]:
    if mean <= 0 or var <= mean:
        return "poisson", mean, 1.0
    return "nb", (mean * mean) / (var - mean), mean / var


def _pmf(k: int, model: str, r: float, success: float, lam: float) -> float:
    if k < 0:
        return float("-inf")
    if model == "poisson":
        if lam == 0:
            return 0.0 if k == 0 else float("-inf")
        return -lam + k * math.log(lam) - math.lgamma(k + 1)
    if r <= 0 or not 0 < success < 1:
        return float("-inf")
    return math.lgamma(k + r) - math.lgamma(r) - math.lgamma(k + 1) + r * math.log(success) + k * math.log(1 - success)


def _ge(needed: int, model: str, r: float, success: float, lam: float) -> float:
    """P(count >= needed). The left sum is used when the strike is near the mean."""
    if needed <= 0:
        return 1.0
    mean = lam if model == "poisson" else r * (1 - success) / success
    if needed <= max(mean, 1):
        logs = [_pmf(k, model, r, success, lam) for k in range(needed)]
        peak = max(logs)
        cdf = 0.0 if peak == float("-inf") else math.exp(peak) * sum(math.exp(item - peak) for item in logs)
        return min(1.0, max(0.0, 1.0 - cdf))
    logs = []
    last = _pmf(needed, model, r, success, lam)
    k = needed
    while k < needed + 200000 and last > float("-inf"):
        logs.append(last)
        nxt = _pmf(k + 1, model, r, success, lam)
        if nxt < last - 40 and k > needed + 4:
            break
        k += 1
        last = nxt
    if not logs or max(logs) == float("-inf"):
        return 0.0
    peak = max(logs)
    return min(1.0, math.exp(peak) * sum(math.exp(item - peak) for item in logs))


def yes_probability(so_far: int, days: float, mean: float, var: float, contract: Mapping[str, object]) -> tuple[str, float] | None:
    """YES probability from the count so far, time left, and the prior daily rate."""
    model, r, success = _law(mean, var)
    scale = 0.0 if days < 1e-6 else days
    lam, r_tot = mean * scale, r * scale
    how = contract.get("how")
    if how == "gt":
        value = _ge(int(contract["strike"]) - so_far + 1, model, r_tot, success, lam)
    elif how == "ge":
        value = _ge(int(contract["strike"]) - so_far, model, r_tot, success, lam)
    elif how == "between":
        value = _ge(int(contract["low"]) - so_far, model, r_tot, success, lam) - _ge(int(contract["high"]) - so_far + 1, model, r_tot, success, lam)
    else:
        return None
    if not math.isfinite(value):
        return None
    return model, min(1.0, max(0.0, value))


def _when(text: str) -> datetime | None:
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def fetch_text(url: str, timeout: float = 20.0) -> str:
    """GET one official JSON or RSS feed. An HTML page is refused."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in _HOSTS:
        raise ValueError("host")
    request = urllib.request.Request(url, headers={"User-Agent": "kalshi-bot-paper-shadow"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status, payload = getattr(response, "status", 200), response.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise OSError(type(exc).__name__) from None
    if status != 200:
        raise OSError("status")
    text = payload.decode("utf-8")
    head = text.lstrip()[:15].lower()
    if head.startswith("<!doctype") or head.startswith("<html"):
        raise ValueError("html")
    return text


def _truth_page(payload: object) -> tuple[list[datetime], date | None]:
    rows = payload.get("data") if isinstance(payload, Mapping) else None
    if not isinstance(rows, list):
        raise ValueError("shape")
    kept: list[datetime] = []
    oldest: date | None = None
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        moment = _when(str(row.get("date") or ""))
        if moment is None:
            continue
        day = moment.astimezone(ET).date()
        oldest = day if oldest is None else min(oldest, day)
        handle = str(row.get("handle") or "").lower()
        platform = str(row.get("platform") or "").lower()
        if handle == "realdonaldtrump" and platform == "truth social" and row.get("deleted_flag") is False and row.get("type") == "Social":
            kept.append(moment)
    return kept, oldest


def _actions(xml: str) -> list[datetime]:
    found: list[datetime] = []
    for item in ElementTree.fromstring(xml).iter("item"):
        raw = item.findtext("pubDate")
        if not raw:
            continue
        moment = parsedate_to_datetime(raw)
        found.append(moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc))
    return found


def _clock(moment: datetime) -> str:
    """Sunday open and Wednesday afternoon are the backtest clocks. Other scans still log."""
    local = moment.astimezone(ET)
    if local.weekday() == 6 and local.hour < 6:
        return "sunday_open"
    if local.weekday() == 2 and 10 <= local.hour < 16:
        return "wednesday"
    return "other"


def _feed_key(series: str) -> str:
    if series == "KXTRUTHSOCIAL":
        return "truth"
    if series == "KXEOWEEK":
        return "orders"
    return "actions"


def _orders(payload: str) -> tuple[list[datetime], bool]:
    """Numbered executive orders at noon ET on the signing date. One page, or the row says capped."""
    body = json.loads(payload)
    results = body.get("results") if isinstance(body, Mapping) else None
    if not isinstance(results, list):
        raise ValueError("shape")
    found: list[datetime] = []
    for row in results:
        if not isinstance(row, Mapping):
            continue
        raw = row.get("signing_date")
        if not row.get("executive_order_number") or not isinstance(raw, str):
            continue
        day = date.fromisoformat(raw)
        found.append(datetime(day.year, day.month, day.day, 12, tzinfo=ET))
    total = body.get("count") if isinstance(body, Mapping) else None
    capped = bool(body.get("next_page_url")) if isinstance(body, Mapping) else False
    if isinstance(total, int) and len(results) < total:
        capped = True
    return found, capped


def _posts(kind: str, need: date, fetch: Fetch, sleeper: Callable[[float], None] | None) -> tuple[list[datetime], bool]:
    if kind == "orders":
        return _orders(fetch(EO_URL))
    if kind == "actions":
        found = _actions(fetch(ACTION_URL))
        return found, len(found) >= 30
    found: list[datetime] = []
    for page in range(1, 41):
        if page > 1 and sleeper is not None:
            sleeper(0.25)
        kept, oldest = _truth_page(json.loads(fetch(TRUTH_URL.format(page=page))))
        found.extend(kept)
        if oldest is None or oldest <= need:
            return found, False
    return found, True


def _row(market: object, now: datetime, reason: str) -> dict[str, object]:
    return {
        "ts": now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ticker": str(_get(market, "ticker") or ""),
        "series": _series(market),
        "yes_ask": _ask(market),
        "probability": None,
        "model": None,
        "reason": reason,
        "minute_counter": False,
    }


def _fit(posts: Sequence[datetime], start: date, capped: bool, need: date) -> tuple[float, float] | None:
    days = [item.astimezone(ET).date() for item in posts]
    if not days:
        return None
    oldest = min(days)
    first = oldest + timedelta(days=1) if capped else (need if oldest <= need else oldest)
    if first >= start:
        return None
    counts, cursor = [], first
    daily: dict[date, int] = {}
    for day in days:
        daily[day] = daily.get(day, 0) + 1
    while cursor < start:
        counts.append(daily.get(cursor, 0))
        cursor += timedelta(days=1)
    if len(counts) < MIN_DAYS:
        return None
    mean = sum(counts) / len(counts)
    var = sum((count - mean) ** 2 for count in counts) / (len(counts) - 1)
    return mean, var


def _score(market: object, contract: Mapping[str, object], posts: Sequence[datetime], capped: bool, now: datetime) -> dict[str, object]:
    start_day, end_day = contract["start"], contract["end"]
    assert isinstance(start_day, date) and isinstance(end_day, date)
    start = datetime(start_day.year, start_day.month, start_day.day, tzinfo=ET)
    end = datetime(end_day.year, end_day.month, end_day.day, tzinfo=ET) + timedelta(days=1)
    moment = now.astimezone(ET)
    stop = min(moment, end)
    so_far = sum(1 for item in posts if start <= item.astimezone(ET) < stop)
    days_left = 0.0 if moment >= end else ((end - start) if moment <= start else (end - moment)).total_seconds() / 86400
    row = _row(market, now, "short_history")
    row.update({
        "so_far": so_far,
        "days_left": f"{days_left:.6f}",
        "how": contract["how"],
        "feed": {
            "truth": "factbase",
            "orders": "federal_register",
            "actions": "whitehouse_rss",
        }[_feed_key(_series(market))],
        "feed_capped": capped,
        "clock": _clock(now),
    })
    if "strike" in contract:
        row["strike"] = contract["strike"]
    if contract["how"] == "between":
        row["low"], row["high"] = contract["low"], contract["high"]
    fitted = _fit(posts, start_day, capped, start_day - timedelta(days=60))
    if fitted is None:
        return row
    row["prior_mean"] = f"{fitted[0]:.6f}"
    scored = yes_probability(so_far, days_left, fitted[0], fitted[1], contract)
    if scored is None:
        row["reason"] = "probability"
        return row
    row["model"], row["probability"], row["reason"] = scored[0], f"{scored[1]:.6f}", None
    return row


def record_countable_shadow(
    markets: Sequence[object],
    *,
    now: datetime,
    path: Path = SHADOW_PATH,
    fetch: Fetch | None = None,
    rules_for: Callable[[str], object] | None = None,
    sleeper: Callable[[float], None] | None = None,
) -> int:
    """Append one shadow row per countable market. A feed error does not raise."""
    moment = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    chosen = [market for market in markets if _series(market) in COUNTABLE]
    if not chosen:
        return 0
    fetch = fetch or fetch_text
    if sleeper is None and fetch is fetch_text:
        sleeper = time.sleep
    path.parent.mkdir(parents=True, exist_ok=True)
    ready: list[tuple[object, dict[str, object]]] = []
    written = 0
    for market in chosen:
        rules, kind = _rules(market), str(_get(market, "strike_type") or "")
        if not rules and rules_for is not None:
            try:
                detail = rules_for(str(_get(market, "ticker") or ""))
            except Exception:
                _write(path, _row(market, moment, "detail"))
                written += 1
                continue
            if detail is not None:
                rules = _rules(detail) or rules
                kind = kind or str(_get(detail, "strike_type") or "")
        contract = parse_contract(rules, kind)
        if contract is None:
            _write(path, _row(market, moment, "rules"))
            written += 1
        else:
            ready.append((market, contract))
    loaded: dict[str, tuple[list[datetime], bool] | None] = {}
    earliest: dict[str, date] = {}
    for _market, contract in ready:
        key = _feed_key(_series(_market))
        start = contract["start"]
        assert isinstance(start, date)
        earliest[key] = start if key not in earliest else min(start, earliest[key])
    for key, start in earliest.items():
        try:
            loaded[key] = _posts(key, start - timedelta(days=60), fetch, sleeper)
        except Exception:
            loaded[key] = None
    for market, contract in ready:
        key = _feed_key(_series(market))
        got = loaded[key]
        _write(path, _row(market, moment, "feed") if got is None else _score(market, contract, got[0], got[1], moment))
        written += 1
    return written


def _write(path: Path, row: Mapping[str, object]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=True, sort_keys=True) + "\n")
