"""Paper snapshot of IMF PortWatch daily chokepoint counts. No orders.

The official FeatureServer is revised in place. A row has no publication
time, so retrieval time is not a vintage and a holdout inside one download
is not evaluable. The baseline, when the previous aligned week is complete
in the file just retrieved, is that week's transit-call sum. Its dispersion
is the sample standard deviation of the last eight complete aligned weeks
in the same file. That is a description of the current vintage, not a
calibrated interval and not a probability.

Registry promotion does not make the source or the estimator supported.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import statistics
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from utils.series_settlement_registry import estimator_eligible

HOST = "services9.arcgis.com"
LAYER_PATH = "/weJ1QsnbMYJlCHdG/arcgis/rest/services/Daily_Chokepoints_Data/FeatureServer/0"
UNITS = "transit_call_count"
PAGE_ROWS = 2000
MAX_PAGES = 6
STALE_AFTER_DAYS = 21
BASELINE = "previous_complete_aligned_week"
UNCERTAINTY = "sample_stdev_of_last_8_aligned_weeks"
_FIELDS = (
    "date,portid,portname,n_total,n_container,n_dry_bulk,"
    "n_general_cargo,n_roro,n_tanker,n_cargo"
)
_QUERY_KEYS = frozenset(
    {
        "where",
        "outFields",
        "orderByFields",
        "resultRecordCount",
        "resultOffset",
        "maxRecordCountFactor",
        "outSR",
        "returnGeometry",
        "f",
    }
)
_PORTS = {
    "Suez Canal": "chokepoint1",
    "Bab el-Mandeb Strait": "chokepoint4",
    "Strait of Hormuz": "chokepoint6",
}
_SERIES_PLACE = {
    "KXSUEZWEEKLY": "Suez Canal",
    "KXBABELMANDEBWEEKLY": "Bab el-Mandeb Strait",
    "KXHORMUZWEEKLY": "Strait of Hormuz",
    "KXHORMUZMAX": "Strait of Hormuz",
}
_WHERE = re.compile(r"^portid='(chokepoint1|chokepoint4|chokepoint6)'$")
_WINDOW = re.compile(r"from ([A-Z][a-z]+ \d{1,2}, \d{4}) to ([A-Z][a-z]+ \d{1,2}, \d{4})")
_PLACE = re.compile(r"through the (.+?) as reported by the IMF PortWatch")
_ABOVE = re.compile(r"\babove (\d+)\b")
_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "september": 9, "oct": 10, "october": 10,
    "nov": 11, "november": 11, "dec": 12, "december": 12,
}
_SHIP = ("n_container", "n_dry_bulk", "n_general_cargo", "n_roro", "n_tanker")
# Sentences captured 2026-11-08 from open Kalshi rules. One strike per event.
# Sibling strikes are the same weekly total. Panama is not in the registry.
PAPER_CONTRACTS: tuple[dict[str, str], ...] = (
    {
        "series_ticker": "KXSUEZWEEKLY",
        "rules_primary": (
            "If the number of transit calls through the Suez Canal as reported by the IMF PortWatch "
            "is above 310 from Nov 2, 2026 to Nov 8, 2026, then the market resolves to Yes."
        ),
        "rules_secondary": (
            "The total is calculated by summing daily counts from Nov 2, 2026 to Nov 8, 2026. "
            "The market will resolve no earlier than Tuesday at 9 AM following the end of the period, "
            "once complete data is available."
        ),
    },
    {
        "series_ticker": "KXSUEZWEEKLY",
        "rules_primary": (
            "If the number of transit calls through the Suez Canal as reported by the IMF PortWatch "
            "is above 310 from Nov 9, 2026 to Nov 15, 2026, then the market resolves to Yes."
        ),
        "rules_secondary": (
            "The total is calculated by summing daily counts from Nov 9, 2026 to Nov 15, 2026. "
            "The market will resolve no earlier than Tuesday at 9 AM following the end of the period, "
            "once complete data is available."
        ),
    },
    {
        "series_ticker": "KXBABELMANDEBWEEKLY",
        "rules_primary": (
            "If the number of transit calls through the Bab el-Mandeb Strait as reported by the "
            "IMF PortWatch is above 200 from Nov 2, 2026 to Nov 8, 2026, then the market resolves to Yes."
        ),
        "rules_secondary": (
            "The total is calculated by summing daily counts from Nov 2, 2026 to Nov 8, 2026. "
            "The market will resolve no earlier than Tuesday at 9 AM following the end of the period, "
            "once complete data is available."
        ),
    },
    {
        "series_ticker": "KXBABELMANDEBWEEKLY",
        "rules_primary": (
            "If the number of transit calls through the Bab el-Mandeb Strait as reported by the "
            "IMF PortWatch is above 200 from Nov 9, 2026 to Nov 15, 2026, then the market resolves to Yes."
        ),
        "rules_secondary": (
            "The total is calculated by summing daily counts from Nov 9, 2026 to Nov 15, 2026. "
            "The market will resolve no earlier than Tuesday at 9 AM following the end of the period, "
            "once complete data is available."
        ),
    },
    {
        "series_ticker": "KXHORMUZWEEKLY",
        "rules_primary": (
            "If the number of transit calls through the Strait of Hormuz as reported by the "
            "IMF PortWatch is above 20 from Nov 2, 2026 to Nov 8, 2026, then the market resolves to Yes."
        ),
        "rules_secondary": (
            "The total is calculated by summing daily counts from Nov 2, 2026 to Nov 8, 2026. "
            "The market will resolve no earlier than Tuesday at 9 AM following the end of the period, "
            "once complete data is available."
        ),
    },
    {
        "series_ticker": "KXHORMUZWEEKLY",
        "rules_primary": (
            "If the number of transit calls through the Strait of Hormuz as reported by the "
            "IMF PortWatch is above 25 from Nov 9, 2026 to Nov 15, 2026, then the market resolves to Yes."
        ),
        "rules_secondary": (
            "The total is calculated by summing daily counts from Nov 9, 2026 to Nov 15, 2026. "
            "The market will resolve no earlier than Tuesday at 9 AM following the end of the period, "
            "once complete data is available."
        ),
    },
    {
        "series_ticker": "KXHORMUZMAX",
        "rules_primary": (
            "If Nov 4, 2026 is the date with the highest number of transit calls through the "
            "Strait of Hormuz as reported by the IMF PortWatch in the listed week, "
            "then the market resolves to Yes."
        ),
        "rules_secondary": (
            "Daily counts will be evaluated from Nov 2, 2026 to Nov 8, 2026. "
            "The market will resolve no earlier than Tuesday at 9 AM following the end of the period, "
            "once complete data is available. If multiple days tie for the highest number of transit "
            "calls, the market for each tied day will resolve to 1/N, where N is the number of tied days."
        ),
    },
)
Transport = Callable[[str], bytes]


class _ManualRedirect(urllib.request.HTTPRedirectHandler):
    """Return a redirect response to the caller. Do not follow it."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

    def http_error_301(self, req, fp, code, msg, headers):
        return fp

    http_error_302 = http_error_301
    http_error_303 = http_error_301
    http_error_307 = http_error_301
    http_error_308 = http_error_301


def _stamp(moment: datetime) -> str:
    text = moment.astimezone(timezone.utc).isoformat(timespec="milliseconds")
    return text.replace("+00:00", "Z")


def _parse_stamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def _one_int(values: object, low: int, high: int) -> bool:
    if not isinstance(values, list) or len(values) != 1 or not str(values[0]).isdigit():
        return False
    number = int(values[0])
    return low <= number <= high


def require_allowlisted(url: str) -> str:
    """Accept only the official chokepoint layer and its three weekly ports.

    A model-proposed URL is not executable. Private, local, and off-host
    targets fail before a socket is opened.
    """
    if not isinstance(url, str) or not url or any(char in url for char in "\r\n\x00"):
        raise ValueError("url")
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise ValueError("url") from exc
    host = (parts.hostname or "").lower().rstrip(".")
    if (
        parts.scheme != "https"
        or parts.username is not None
        or parts.password is not None
        or parts.fragment
        or host != HOST
        or _ip_literal(host)
        or port not in (None, 443)
        or ".." in parts.path
        or parts.path not in {LAYER_PATH, LAYER_PATH + "/query"}
    ):
        raise ValueError("url")
    query = urllib.parse.parse_qs(parts.query, keep_blank_values=True)
    if parts.path == LAYER_PATH:
        if set(query) != {"f"} or query.get("f") != ["json"]:
            raise ValueError("url")
        return url
    if set(query) != _QUERY_KEYS or query.get("f") != ["json"]:
        raise ValueError("url")
    if query.get("outFields") != [_FIELDS] or query.get("orderByFields") != ["date"]:
        raise ValueError("url")
    if query.get("maxRecordCountFactor") != ["5"] or query.get("outSR") != ["4326"]:
        raise ValueError("url")
    if query.get("returnGeometry") != ["false"]:
        raise ValueError("url")
    where = query.get("where") or []
    if len(where) != 1 or _WHERE.fullmatch(where[0]) is None:
        raise ValueError("url")
    if not _one_int(query.get("resultOffset"), 0, 10000):
        raise ValueError("url")
    if not _one_int(query.get("resultRecordCount"), 1, PAGE_ROWS):
        raise ValueError("url")
    return url


def layer_url() -> str:
    return f"https://{HOST}{LAYER_PATH}?f=json"


def query_url(portid: str, offset: int = 0) -> str:
    if portid not in _PORTS.values():
        raise ValueError("port")
    if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= 10000:
        raise ValueError("offset")
    query = urllib.parse.urlencode(
        {
            "where": f"portid='{portid}'",
            "outFields": _FIELDS,
            "orderByFields": "date",
            "resultOffset": str(offset),
            "resultRecordCount": str(PAGE_ROWS),
            "maxRecordCountFactor": "5",
            "outSR": "4326",
            "returnGeometry": "false",
            "f": "json",
        }
    )
    return require_allowlisted(f"https://{HOST}{LAYER_PATH}/query?{query}")


def _urlopen(request: urllib.request.Request, timeout: float = 60.0):
    opener = urllib.request.build_opener(_ManualRedirect)
    return opener.open(request, timeout=timeout)


def fetch_allowlisted(url: str, *, urlopen: Callable | None = None, timeout: float = 60.0) -> bytes:
    """GET one allowlisted URL. At most one redirect, and only to the allowlist."""
    current = require_allowlisted(url)
    open_url = urlopen or _urlopen
    for hop in range(2):
        request = urllib.request.Request(current, headers={"User-Agent": "kalshi-bot-paper-shadow"})
        status_error = False
        try:
            with open_url(request, timeout=timeout) as response:
                status = getattr(response, "status", None)
                headers = getattr(response, "headers", {})
                if status in {301, 302, 303, 307, 308}:
                    if hop == 1:
                        raise ValueError("redirect")
                    location = headers.get("Location") if hasattr(headers, "get") else None
                    if not isinstance(location, str):
                        raise ValueError("redirect")
                    current = require_allowlisted(urllib.parse.urljoin(current, location))
                    continue
                if status != 200:
                    status_error = True
                else:
                    body = response.read()
        except ValueError:
            raise
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise OSError(type(exc).__name__) from None
        if status_error:
            raise OSError("status")
        if not isinstance(body, bytes):
            raise OSError("body")
        return body
    raise ValueError("redirect")


def _count(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        number = value
    elif isinstance(value, float) and value.is_integer():
        number = int(value)
    else:
        return None
    if number < 0:
        return None
    return number


def _day_text(value: object) -> date | None:
    if not isinstance(value, str) or len(value) != 10:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _named_day(text: str) -> date | None:
    parts = text.replace(",", "").split()
    month = _MONTHS.get(parts[0].lower()) if len(parts) == 3 else None
    if month is None or not parts[1].isdigit() or not parts[2].isdigit():
        return None
    try:
        return date(int(parts[2]), month, int(parts[1]))
    except ValueError:
        return None


def _epoch(value: object) -> datetime | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str) and value.isdigit():
        number = float(value)
    else:
        return None
    if number > 10_000_000_000:
        number /= 1000.0
    if number < 0:
        return None
    return datetime.fromtimestamp(number, timezone.utc)


def parse_features(features: object, portid: str) -> tuple[dict[date, int], str]:
    """Parse one port. A missing day is not zero. A conflict fails the port."""
    if not isinstance(features, list):
        return {}, "source_shape"
    expected = next((name for name, coded in _PORTS.items() if coded == portid), "")
    if not expected:
        return {}, "port_mismatch"
    rows: dict[date, int] = {}
    for feature in features:
        attrs = feature.get("attributes") if isinstance(feature, Mapping) else None
        if not isinstance(attrs, Mapping):
            return {}, "source_shape"
        if str(attrs.get("portid") or "") != portid or str(attrs.get("portname") or "") != expected:
            return {}, "port_mismatch"
        day = _day_text(attrs.get("date"))
        total = _count(attrs.get("n_total"))
        if day is None:
            return {}, "incompatible_date"
        if total is None:
            return {}, "missing_n_total"
        if day in rows:
            return {}, "duplicate_dates"
        parts = [_count(attrs.get(name)) for name in _SHIP]
        if all(part is not None for part in parts) and sum(part or 0 for part in parts) != total:
            return {}, "conflicting_counts"
        cargo, tanker = _count(attrs.get("n_cargo")), _count(attrs.get("n_tanker"))
        if cargo is not None and tanker is not None and cargo + tanker != total:
            return {}, "conflicting_counts"
        rows[day] = total
    if not rows:
        return {}, "missing_observations"
    return rows, ""


def _usable(day: date, decision: datetime) -> bool:
    """The UTC date is usable only after that date has ended."""
    period_end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=timezone.utc)
    return period_end < decision


def _week_sum(rows: Mapping[date, int], start: date, decision: datetime) -> int | None:
    total = 0
    for offset in range(7):
        day = start + timedelta(days=offset)
        if day not in rows or not _usable(day, decision):
            return None
        total += rows[day]
    return total


def _window_complete(rows: Mapping[date, int], start: date, end: date, decision: datetime) -> bool:
    day = start
    while day <= end:
        if day not in rows or not _usable(day, decision):
            return False
        day += timedelta(days=1)
    return True


def parse_contract(series: str, primary: str, secondary: str = "") -> dict[str, object] | None:
    """Read the weekly window from the rule text. The source date stays UTC."""
    text = f"{primary} {secondary}"
    if "imf portwatch" not in text.lower():
        return None
    found = _WINDOW.search(text)
    place = _PLACE.search(primary)
    if found is None or place is None:
        return None
    start, end = _named_day(found.group(1)), _named_day(found.group(2))
    if start is None or end is None or end < start:
        return None
    highest = "highest number of transit calls" in primary
    summing = "summing daily counts" in text
    if highest and summing:
        how = "conflicting"
    elif highest:
        how = "max_day"
    elif summing:
        how = "weekly_sum"
    else:
        return None
    above = _ABOVE.search(primary)
    strike = int(above.group(1)) if above is not None else None
    if how == "weekly_sum" and strike is None:
        return None
    return {
        "series": series,
        "start": start,
        "end": end,
        "place": place.group(1).strip(),
        "how": how,
        "strike": strike,
        "market_sources": ["IMF PortWatch"],
    }


def registry_qualified(series: str, market_sources: object) -> bool:
    """Promoted registry row plus an overlapping market label. Nothing more."""
    if series not in _SERIES_PLACE:
        return False
    return estimator_eligible(series, market_sources=market_sources)


def _blank_forecast(series: str, reason: str, *, qualified: bool, source: bool) -> dict[str, object]:
    return {
        "series_ticker": series,
        "event_key": "",
        "strike_count": 0,
        "strikes": [],
        "portid": "",
        "place": "",
        "start": "",
        "end": "",
        "aggregation": "",
        "registry_qualified": qualified,
        "source_supported": source,
        "estimator_supported": False,
        "point_forecast": None,
        "uncertainty": None,
        "uncertainty_label": None,
        "uncertainty_weeks": 0,
        "uncertainty_reason": reason,
        "baseline": BASELINE,
        "input_basis": "current_vintage_rows_before_decision",
        "not_an_asof_vintage": True,
        "skip_reason": reason,
        "publication_time": None,
        "market_comparison": "not_estimable",
        "fee_net": "not_estimable",
    }


def _forecast_event(
    parsed: Mapping[str, object],
    strikes: list[int],
    port: Mapping[str, object],
    *,
    decision: datetime,
    retrieval: datetime,
    vintage: datetime | None,
) -> dict[str, object]:
    series = str(parsed["series"])
    qualified = registry_qualified(series, parsed.get("market_sources"))
    place = str(parsed["place"])
    start, end = parsed["start"], parsed["end"]
    assert isinstance(start, date) and isinstance(end, date)
    source = bool(port.get("source_supported"))
    row = _blank_forecast(series, "", qualified=qualified, source=source)
    row.update(
        {
            "event_key": f"{series}:{start.isoformat()}:{end.isoformat()}",
            "strike_count": len(strikes),
            "strikes": strikes,
            "portid": str(port.get("portid") or ""),
            "place": place,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "aggregation": str(parsed["how"]),
        }
    )
    if not qualified:
        row["skip_reason"] = "not_registry_qualified"
        row["source_supported"] = False
        return row
    if place != _SERIES_PLACE.get(series) or place not in _PORTS:
        row["skip_reason"] = "port_mismatch"
        row["source_supported"] = False
        return row
    if parsed["how"] != "weekly_sum":
        row["skip_reason"] = "aggregation_not_weekly_sum"
        return row
    if (end - start).days != 6:
        row["skip_reason"] = "window_not_seven_days"
        return row
    if not source:
        row["skip_reason"] = str(port.get("skip_reason") or "source_unavailable")
        return row
    if decision != retrieval:
        row["skip_reason"] = "decision_not_retrieval"
        return row
    if vintage is None:
        row["skip_reason"] = "vintage_unknown"
        return row
    if vintage > retrieval:
        row["skip_reason"] = "vintage_after_retrieval"
        return row
    observations = port.get("rows")
    if not isinstance(observations, Mapping):
        row["skip_reason"] = "missing_observations"
        row["source_supported"] = False
        return row
    usable = [day for day in observations if isinstance(day, date) and _usable(day, decision)]
    if not usable:
        row["skip_reason"] = "no_usable_observations"
        return row
    if (decision.astimezone(timezone.utc).date() - max(usable)).days > STALE_AFTER_DAYS:
        row["skip_reason"] = "stale_observations"
        return row
    if _window_complete(observations, start, end, decision):
        row["skip_reason"] = "window_already_published"
        return row
    sums: list[int] = []
    for index in range(8):
        week = start - timedelta(days=7 * (index + 1))
        total = _week_sum(observations, week, decision)
        if total is None:
            break
        sums.append(total)
    row["uncertainty_weeks"] = len(sums)
    if not sums:
        row["skip_reason"] = "prior_week_incomplete"
        row["uncertainty_reason"] = "prior_week_incomplete"
        return row
    row["point_forecast"] = sums[0]
    if len(sums) < 8:
        row["uncertainty_reason"] = "fewer_than_8_complete_weeks"
        return row
    row["uncertainty"] = statistics.stdev(sums)
    row["uncertainty_label"] = "current_vintage_dispersion"
    row["uncertainty_reason"] = ""
    return row


def _store(out_dir: Path, url: str, body: bytes) -> dict[str, object]:
    digest = hashlib.sha256(body).hexdigest()
    relative = f"raw/{digest}.json"
    path = out_dir / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(body)
    return {"url": url, "sha256": digest, "bytes": len(body), "raw_path": relative}


def _read_json(body: bytes) -> tuple[Mapping[str, object] | None, str]:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return None, "source_not_json"
    if not isinstance(payload, dict):
        return None, "source_shape"
    if payload.get("error"):
        return None, "source_error"
    return payload, ""


def _read_port(transport: Transport, portid: str) -> tuple[dict[str, object], list[tuple[str, bytes]]]:
    features: list[object] = []
    bodies: list[tuple[str, bytes]] = []
    offset = 0
    place = next(name for name, coded in _PORTS.items() if coded == portid)
    meta: dict[str, object] = {
        "portid": portid,
        "portname": place,
        "source_supported": False,
        "skip_reason": "source_unavailable",
        "row_count": 0,
        "observation_start": None,
        "observation_end": None,
        "units": UNITS,
        "rows": {},
        "responses": [],
    }
    for _page in range(MAX_PAGES):
        url = query_url(portid, offset)
        try:
            body = transport(url)
        except (OSError, ValueError):
            meta["skip_reason"] = "source_unavailable"
            return meta, bodies
        if not isinstance(body, bytes):
            meta["skip_reason"] = "source_unavailable"
            return meta, bodies
        bodies.append((url, body))
        payload, reason = _read_json(body)
        if payload is None:
            meta["skip_reason"] = reason
            return meta, bodies
        page = payload.get("features")
        if not isinstance(page, list):
            meta["skip_reason"] = "source_shape"
            return meta, bodies
        features.extend(page)
        exceeded = payload.get("exceededTransferLimit") is True
        if not exceeded and not (payload.get("exceededTransferLimit") is None and len(page) >= PAGE_ROWS):
            break
        if not page:
            meta["skip_reason"] = "truncated"
            return meta, bodies
        offset += len(page)
        if offset > 10000:
            meta["skip_reason"] = "truncated"
            return meta, bodies
    else:
        meta["skip_reason"] = "truncated"
        return meta, bodies
    rows, reason = parse_features(features, portid)
    meta["rows"] = rows
    meta["row_count"] = len(rows)
    if reason:
        meta["skip_reason"] = reason
        return meta, bodies
    days = sorted(rows)
    meta["observation_start"] = days[0].isoformat()
    meta["observation_end"] = days[-1].isoformat()
    meta["source_supported"] = True
    meta["skip_reason"] = ""
    return meta, bodies


def _read_layer(transport: Transport) -> tuple[datetime | None, list[tuple[str, bytes]], str]:
    url = layer_url()
    try:
        body = transport(url)
    except (OSError, ValueError):
        return None, [], "source_unavailable"
    if not isinstance(body, bytes):
        return None, [], "source_unavailable"
    payload, reason = _read_json(body)
    if payload is None:
        return None, [(url, body)], reason
    edit = payload.get("editingInfo")
    stamp = _epoch(edit.get("dataLastEditDate")) if isinstance(edit, Mapping) else None
    if stamp is None:
        return None, [(url, body)], "vintage_unknown"
    return stamp, [(url, body)], ""


def forecast_contracts(
    contracts: Sequence[Mapping[str, str]],
    ports: Mapping[str, Mapping[str, object]],
    *,
    decision: datetime,
    retrieval: datetime,
    vintage: datetime | None,
) -> list[dict[str, object]]:
    """One forecast per event. Strikes on the same week are not extra samples."""
    grouped: dict[tuple[str, date, date, str, str], list[int]] = {}
    order: list[tuple[str, date, date, str, str]] = []
    parsed_for: dict[tuple[str, date, date, str, str], Mapping[str, object]] = {}
    skipped: list[dict[str, object]] = []
    for raw in contracts:
        series = str(raw.get("series_ticker") or "")
        parsed = parse_contract(series, str(raw.get("rules_primary") or ""), str(raw.get("rules_secondary") or ""))
        if parsed is None:
            qualified = registry_qualified(series, [])
            skipped.append(_blank_forecast(series, "rules_unparsed", qualified=qualified, source=False))
            continue
        key = (series, parsed["start"], parsed["end"], str(parsed["how"]), str(parsed["place"]))
        assert isinstance(parsed["start"], date) and isinstance(parsed["end"], date)
        if key not in grouped:
            grouped[key] = []
            order.append(key)
            parsed_for[key] = parsed
        strike = parsed.get("strike")
        if isinstance(strike, int) and strike not in grouped[key]:
            grouped[key].append(strike)
    forecasts = list(skipped)
    for key in order:
        parsed = parsed_for[key]
        place = str(parsed["place"])
        portid = _PORTS.get(place, "")
        port = ports.get(portid, {"source_supported": False, "skip_reason": "port_mismatch", "rows": {}})
        forecasts.append(
            _forecast_event(
                parsed,
                sorted(grouped[key]),
                port,
                decision=decision,
                retrieval=retrieval,
                vintage=vintage,
            )
        )
    return forecasts


def record_shadow(
    *,
    now: datetime,
    out_dir: Path,
    contracts: Sequence[Mapping[str, str]] = PAPER_CONTRACTS,
    transport: Transport | None = None,
    timeout: float = 60.0,
    decision_time: datetime | None = None,
) -> dict[str, object]:
    """Fetch the three weekly ports, store the bytes, and write one paper row."""
    retrieval = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    retrieval = retrieval.astimezone(timezone.utc)
    decision = decision_time.astimezone(timezone.utc) if decision_time is not None else retrieval
    fetch = transport or (lambda url: fetch_allowlisted(url, timeout=timeout))
    destination = Path(out_dir)
    destination.mkdir(parents=True, exist_ok=True)
    vintage, layer_bodies, layer_reason = _read_layer(fetch)
    ports: dict[str, dict[str, object]] = {}
    stored_ports: list[dict[str, object]] = []
    for portid in ("chokepoint1", "chokepoint4", "chokepoint6"):
        meta, bodies = _read_port(fetch, portid)
        meta["responses"] = [_store(destination, url, body) for url, body in bodies]
        ports[portid] = meta
        public = {key: value for key, value in meta.items() if key != "rows"}
        stored_ports.append(public)
    forecasts = forecast_contracts(contracts, ports, decision=decision, retrieval=retrieval, vintage=vintage)
    evaluation = same_vintage_holdout()
    record: dict[str, object] = {
        "schema": "portwatch_shadow_v1",
        "family": "imf_portwatch",
        "paper_only": True,
        "live_admission": False,
        "grok_calls": 0,
        "orders": 0,
        "retrieval_time": _stamp(retrieval),
        "decision_time": _stamp(decision),
        "publication_time": None,
        "table_vintage_time": None if vintage is None else _stamp(vintage),
        "table_vintage_reason": layer_reason,
        "units": UNITS,
        "baseline": BASELINE,
        "uncertainty_method": UNCERTAINTY,
        "layer": [_store(destination, url, body) for url, body in layer_bodies],
        "ports": stored_ports,
        "forecasts": forecasts,
        "evaluation": evaluation["evaluation"],
        "evaluation_reason": evaluation["reason"],
        "independent_settled_events": 0,
        "market_comparison": "not_estimable",
        "fee_net": "not_estimable",
        "estimator_supported": False,
        "registry_promotion_is_not_support": True,
    }
    with (destination / "snapshots.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")
    return record


def same_vintage_holdout(snapshot: Mapping[str, object] | None = None) -> dict[str, object]:
    """One revised file cannot score itself. Reading it is not forecast skill."""
    del snapshot
    return {
        "evaluation": "not_yet_evaluable",
        "reason": "revised_vintage_not_asof",
        "independent_settled_events": 0,
        "market_comparison": "not_estimable",
        "fee_net": "not_estimable",
        "estimator_supported": False,
        "publication_time": None,
    }


def score_forward(
    prior: Mapping[str, object],
    later: Mapping[str, object],
    *,
    prior_rows: Mapping[str, Mapping[date, int]],
    later_rows: Mapping[str, Mapping[date, int]],
) -> dict[str, object]:
    """Score a stored forecast only against a later table vintage.

    The target week must be absent from the forecast file and complete in the
    later file. The later edit time must be after the forecast retrieval.
    Fee-net stays not estimable: this function has no quote, depth, or fee.
    """
    refused = same_vintage_holdout()
    prior_retrieval = _parse_stamp(prior.get("retrieval_time"))
    later_retrieval = _parse_stamp(later.get("retrieval_time"))
    prior_vintage = _parse_stamp(prior.get("table_vintage_time"))
    later_vintage = _parse_stamp(later.get("table_vintage_time"))
    if prior_retrieval is None or later_retrieval is None or later_retrieval <= prior_retrieval:
        refused["reason"] = "same_or_earlier_retrieval"
        return refused
    if prior_vintage is None or later_vintage is None or later_vintage <= prior_retrieval or later_vintage == prior_vintage:
        return refused
    events: list[dict[str, object]] = []
    forecasts = prior.get("forecasts")
    if not isinstance(forecasts, list):
        return refused
    for forecast in forecasts:
        if not isinstance(forecast, Mapping) or forecast.get("point_forecast") is None:
            continue
        if forecast.get("source_supported") is not True:
            continue
        portid = str(forecast.get("portid") or "")
        start = _day_text(forecast.get("start"))
        end = _day_text(forecast.get("end"))
        point = forecast.get("point_forecast")
        if start is None or end is None or isinstance(point, bool) or not isinstance(point, int):
            continue
        before = prior_rows.get(portid) or {}
        after = later_rows.get(portid) or {}
        days = [start + timedelta(days=offset) for offset in range((end - start).days + 1)]
        if any(day in before for day in days):
            continue
        if any(day not in after for day in days):
            continue
        actual = sum(after[day] for day in days)
        strikes = forecast.get("strikes") if isinstance(forecast.get("strikes"), list) else []
        events.append(
            {
                "event_key": forecast.get("event_key"),
                "actual_sum": actual,
                "point_forecast": point,
                "absolute_error": abs(actual - point),
                "strike_count": len(strikes),
                "publication_time": None,
            }
        )
    if not events:
        return refused
    return {
        "evaluation": "forward_first_complete_vintage",
        "reason": "",
        "independent_settled_events": len(events),
        "events": events,
        "market_comparison": "not_estimable",
        "fee_net": "not_estimable",
        "estimator_supported": True,
        "publication_time": None,
    }
