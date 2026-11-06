"""Local two-axis client. Not a probability and not an order path.

The client talks only to a configured local base URL. A missing server, a
timeout, or a malformed body fails that classification. Paid hosts are
refused before any socket. xAI is never a fallback.

Sending ``response_format`` is not schema enforcement. ``schema_enforced``
is true only after a probe where the server returns the schema object for a
prompt that asked for the single word hello. A JSON-object mode can force
JSON and still is not schema enforcement. Prompt instructions are not
enforcement either.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Mapping
from urllib.parse import urlparse

from utils.local_triage import DOMAINS, SETTLEMENTS

_AMBIGUITY = ("none", "ambiguous", "insufficient_evidence")
_ALLOWED_KEYS = {"domain", "settlement", "settlement_source", "rationale", "ambiguity"}
_PROBABILITY_KEYS = {"p_raw", "p_final", "probability"}
_ORDER_KEYS = {"trade", "side", "order"}
_PAID_HOSTS = ("x.ai", "openai.com", "anthropic.com")
_PROBE_MODES = ("openai_json_schema", "openai_json_object", "ollama_native")
_ENFORCING_MODES = ("openai_json_schema", "ollama_native")
_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["domain", "settlement", "settlement_source", "rationale", "ambiguity"],
    "properties": {
        "domain": {"type": "string", "enum": list(DOMAINS)},
        "settlement": {"type": "string", "enum": list(SETTLEMENTS)},
        "settlement_source": {"type": "string"},
        "rationale": {"type": "string"},
        "ambiguity": {"type": "string", "enum": list(_AMBIGUITY)},
    },
}
_SYSTEM = (
    "Classify two axes of a Kalshi market. "
    "domain is sports, politics, macro, weather, other, or ambiguous. "
    "settlement is countable, lookup, market_price, opinion, other, or ambiguous. "
    "A sports domain can have a countable settlement. That pair is valid. "
    "Do not collapse the two axes into one label. "
    "This is not a probability and not a trade. "
    "Do not return a probability, a side, or an order. "
    "Use ambiguity none only when both axes are specific. "
    "If either axis is ambiguous because the rules are missing, use insufficient_evidence. "
    "If either axis is ambiguous because more than one value fits, use ambiguous."
)
_PROBE_SYSTEM = "Reply with only the single word hello."
_PROBE_USER = "hello"


class EndpointDown(Exception):
    """The local server could not be reached.

    ``fatal`` means later shortlist rows must not wait on the same dead socket.
    An HTTP 5xx is not fatal: the server answered.
    """

    def __init__(self, fatal: bool = True, status: int | None = None) -> None:
        super().__init__("local endpoint unavailable")
        self.fatal = fatal
        self.status = status


class MalformedBody(Exception):
    """The server answered, but the body is not the schema."""

    def __init__(self, status: int | None = None, text: str = "") -> None:
        super().__init__("malformed")
        self.status = status
        self.text = text


def is_paid_research_host(base_url: str) -> bool:
    host = (urlparse(base_url).hostname or "").lower().rstrip(".")
    if not host:
        return False
    return any(host == name or host.endswith("." + name) for name in _PAID_HOSTS)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def schema_mode_from_probe(attempts: list[Mapping[str, Any]]) -> tuple[str, bool]:
    """Pick a mode only when that mode constrained the hello probe.

    ``openai_json_object`` is never selected. JSON-only output is not the schema.
    """
    by_mode = {str(row.get("mode") or ""): row for row in attempts}
    for mode in _ENFORCING_MODES:
        if (by_mode.get(mode) or {}).get("constrained") is True:
            return mode, True
    return "openai_json_schema", False


def build_prompt(item: Mapping[str, Any]) -> str:
    sources = item.get("settlement_sources") or ()
    if isinstance(sources, str):
        source_text = sources
    else:
        source_text = ", ".join(str(label) for label in sources)
    return "\n".join(
        (
            "Classify domain and settlement. This is not a probability and not a trade.",
            f"ticker: {item.get('ticker') or ''}",
            f"series: {item.get('series_ticker') or ''}",
            f"title: {item.get('title') or ''}",
            f"rules_primary: {item.get('rules_primary') or ''}",
            f"rules_secondary: {item.get('rules_secondary') or ''}",
            f"settlement_sources: {source_text}",
            f"rule_version: {item.get('rule_version') or 'missing'}",
            "domain: sports, politics, macro, weather, other, ambiguous",
            "settlement: countable, lookup, market_price, opinion, other, ambiguous",
            "Sports and countable together are valid.",
            "ambiguity: none, ambiguous, insufficient_evidence",
            "Use ambiguity none only when both axes are specific.",
            "Do not return p_raw, p_final, probability, trade, side, or order.",
        )
    )


def _message_text(payload: Mapping[str, Any]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    message = choices[0].get("message") if isinstance(choices[0], Mapping) else None
    if not isinstance(message, Mapping):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, Mapping) and isinstance(part.get("text"), str):
                parts.append(part["text"])
        return "\n".join(parts)
    return ""


def _load_object(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.lower().startswith("json"):
            stripped = stripped[4:]
        stripped = stripped.strip()
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if isinstance(payload, dict) and "choices" in payload:
        return _load_object(_message_text(payload))
    if isinstance(payload, dict):
        return payload
    return None


def parse_classification_detail(raw: str) -> tuple[dict[str, str] | None, str]:
    """Schema object and a failure category. An empty category means valid.

    A probability, trade, side, or order field is malformed and is not returned.
    """
    payload = _load_object(raw)
    if payload is None:
        return None, "not_json"
    lowered = {str(key).lower() for key in payload}
    if lowered & _PROBABILITY_KEYS:
        return None, "probability_field"
    if lowered & _ORDER_KEYS:
        return None, "order_field"
    if set(payload) != _ALLOWED_KEYS:
        return None, "extra_or_missing_keys"
    domain = payload.get("domain")
    settlement = payload.get("settlement")
    source = payload.get("settlement_source")
    rationale = payload.get("rationale")
    ambiguity = payload.get("ambiguity")
    if not isinstance(domain, str) or domain not in DOMAINS:
        return None, "bad_domain"
    if not isinstance(settlement, str) or settlement not in SETTLEMENTS:
        return None, "bad_settlement"
    if not isinstance(source, str) or not isinstance(rationale, str):
        return None, "bad_text"
    if not isinstance(ambiguity, str) or ambiguity not in _AMBIGUITY:
        return None, "bad_ambiguity"
    axes_ambiguous = domain == "ambiguous" or settlement == "ambiguous"
    if ambiguity == "none" and axes_ambiguous:
        return None, "ambiguity_conflict"
    if ambiguity in {"ambiguous", "insufficient_evidence"} and not axes_ambiguous:
        return None, "ambiguity_conflict"
    return {
        "domain": domain,
        "settlement": settlement,
        "settlement_source": " ".join(source.split())[:300],
        "rationale": " ".join(rationale.split())[:400],
        "ambiguity": ambiguity,
    }, ""


def parse_classification(raw: str) -> dict[str, str] | None:
    parsed, _category = parse_classification_detail(raw)
    return parsed


def _timeout_seconds(env: Mapping[str, str]) -> float:
    raw = env.get("LOCAL_TRIAGE_TIMEOUT_SECONDS")
    if raw is None or not str(raw).strip():
        return 20.0
    try:
        value = Decimal(str(raw).strip())
    except (InvalidOperation, ValueError):
        raise ValueError("LOCAL_TRIAGE_TIMEOUT_SECONDS is not a number") from None
    if not value.is_finite() or value <= 0:
        raise ValueError("LOCAL_TRIAGE_TIMEOUT_SECONDS must be positive")
    return float(value)


class LocalClassifier:
    """One shortlist item per call. The first dead endpoint skips the rest.

    The retry repeats the selected mode. It does not drop to json_object.
    A timeout is not retried. ``schema_enforced`` stays false until a probe
    shows a mode constrained the hello prompt.
    """

    def __init__(
        self,
        *,
        base_url: str,
        model_id: str,
        runtime: str,
        timeout_s: float,
        opener: Callable[..., Any] | None = None,
        clock: Callable[[], datetime] | None = None,
        schema_mode: str = "openai_json_schema",
    ) -> None:
        self.base_url = base_url.strip().rstrip("/")
        self.model_id = model_id.strip()
        self.runtime = runtime.strip() or "unconfigured"
        self.timeout_s = timeout_s
        self._opener = opener or urllib.request.urlopen
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._down = False
        self.schema_mode = schema_mode
        self.schema_enforced = False
        self.refused_paid_host = bool(self.base_url) and is_paid_research_host(self.base_url)

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        opener: Callable[..., Any] | None = None,
    ) -> LocalClassifier:
        env = os.environ if environ is None else environ
        runtime = str(env.get("LOCAL_TRIAGE_RUNTIME") or "").strip()
        return cls(
            base_url=str(env.get("LOCAL_TRIAGE_BASE_URL") or ""),
            model_id=str(env.get("LOCAL_TRIAGE_MODEL") or ""),
            runtime=runtime,
            timeout_s=_timeout_seconds(env),
            opener=opener,
        )

    def _stamp(self) -> str:
        moment = self._clock()
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _request_url(self, mode: str) -> str:
        if mode == "ollama_native":
            if self.base_url.endswith("/api/chat"):
                return self.base_url
            parsed = urlparse(self.base_url)
            return f"{parsed.scheme}://{parsed.netloc}/api/chat"
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return self.base_url + "/chat/completions"

    def _body(self, mode: str, system: str, user: str) -> dict[str, Any]:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        if mode == "ollama_native":
            body: dict[str, Any] = {
                "model": self.model_id,
                "stream": False,
                "messages": messages,
                "format": _SCHEMA,
            }
            if "qwen3" in self.model_id.lower():
                body["think"] = False
            return body
        if mode == "openai_json_object":
            response_format: dict[str, Any] = {"type": "json_object"}
        else:
            response_format = {
                "type": "json_schema",
                "json_schema": {"name": "settlement_axes", "strict": True, "schema": _SCHEMA},
            }
        return {
            "model": self.model_id,
            "temperature": 0,
            "messages": messages,
            "response_format": response_format,
        }

    def _interpret(self, raw: str, mode: str) -> str:
        if mode != "ollama_native":
            return raw
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return raw
        message = payload.get("message") if isinstance(payload, dict) else None
        if isinstance(message, dict) and isinstance(message.get("content"), str):
            return message["content"]
        return raw

    def _exchange(self, mode: str, system: str, user: str) -> tuple[int | None, str]:
        url = self._request_url(mode)
        if self.refused_paid_host or is_paid_research_host(url):
            raise EndpointDown
        encoded = json.dumps(self._body(mode, system, user)).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=encoded,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self._opener(request, timeout=self.timeout_s) as response:
                raw = response.read()
                status = getattr(response, "status", None)
        except TimeoutError as exc:
            raise TimeoutError(str(exc)) from exc
        except socket.timeout as exc:
            raise TimeoutError(str(exc)) from exc
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")
            except Exception:
                detail = ""
            if exc.code in (408, 429) or exc.code >= 500:
                raise EndpointDown(fatal=False, status=exc.code) from exc
            raise MalformedBody(status=exc.code, text=detail) from exc
        except urllib.error.URLError as exc:
            raise EndpointDown(fatal=True) from exc
        except OSError as exc:
            raise EndpointDown(fatal=True) from exc
        if isinstance(raw, bytes):
            text = raw.decode("utf-8", "replace")
        else:
            text = str(raw)
        code = status if isinstance(status, int) else None
        return code, self._interpret(text, mode)

    def probe(self) -> dict[str, Any]:
        """Ask for the word hello under each request format. Record the result.

        The body of the reply is not kept. A constrained result means the
        returned text parsed as the schema anyway.
        """
        attempts: list[dict[str, Any]] = []
        if self.refused_paid_host:
            selected, enforced = "openai_json_schema", False
        elif not self.base_url or not self.model_id:
            selected, enforced = "openai_json_schema", False
        else:
            for mode in _PROBE_MODES:
                attempts.append(self._probe_one(mode))
                if attempts[-1].get("fatal"):
                    break
            selected, enforced = schema_mode_from_probe(attempts)
        self.schema_mode = selected
        self.schema_enforced = enforced
        return {
            "model_id": self.model_id,
            "runtime": self.runtime,
            "selected_mode": selected,
            "schema_enforced": enforced,
            "attempts": attempts,
        }

    def _probe_one(self, mode: str) -> dict[str, Any]:
        started = time.perf_counter()
        row: dict[str, Any] = {
            "mode": mode,
            "http_status": None,
            "constrained": False,
            "parser_failure": "",
            "error_type": "",
            "fatal": False,
            "think_false": mode == "ollama_native" and "qwen3" in self.model_id.lower(),
            "latency_ms": 0,
        }
        try:
            status, text = self._exchange(mode, _PROBE_SYSTEM, _PROBE_USER)
        except TimeoutError:
            row["parser_failure"] = "timeout"
            row["error_type"] = "TimeoutError"
        except EndpointDown as exc:
            row["http_status"] = exc.status
            row["parser_failure"] = "endpoint_unavailable"
            row["error_type"] = "EndpointDown"
            row["fatal"] = exc.fatal
            if exc.fatal:
                self._down = True
        except MalformedBody as exc:
            row["http_status"] = exc.status
            row["parser_failure"] = "http_error"
            row["error_type"] = "HTTPError"
        else:
            parsed, category = parse_classification_detail(text)
            row["http_status"] = status
            row["constrained"] = parsed is not None
            row["parser_failure"] = "" if parsed is not None else category
        row["latency_ms"] = int((time.perf_counter() - started) * 1000)
        return row

    def classify(self, item: Mapping[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        prompt = build_prompt(item)
        record: dict[str, Any] = {
            "ts": self._stamp(),
            "ticker": str(item.get("ticker") or ""),
            "model_id": self.model_id,
            "runtime": self.runtime,
            "schema_mode": self.schema_mode,
            "schema_enforced": self.schema_enforced,
            "http_status": None,
            "parser_failure": "",
            "retry_outcome": "",
            "prompt_hash": _sha256(prompt),
            "response_hash": "",
            "latency_ms": 0,
            "rule_version": str(item.get("rule_version") or "missing"),
            "domain": "",
            "settlement": "",
            "settlement_source": "",
            "ambiguity": "",
            "rationale": "",
            "outcome": "",
            "retries": 0,
        }

        def finish(outcome: str, **updates: Any) -> dict[str, Any]:
            record["outcome"] = outcome
            record["latency_ms"] = int((time.perf_counter() - started) * 1000)
            record.update(updates)
            return record

        if self.refused_paid_host:
            return finish("refused_paid_host", retry_outcome="refused_paid_host")
        if not self.base_url or not self.model_id:
            return finish("unconfigured", retry_outcome="unconfigured")
        if self._down:
            return finish("endpoint_unavailable", retry_outcome="endpoint_unavailable")
        last_hash = ""
        failure = ""
        for attempt in (0, 1):
            try:
                status, raw = self._exchange(self.schema_mode, _SYSTEM, prompt)
            except TimeoutError:
                return finish(
                    "timeout",
                    response_hash=last_hash,
                    retries=attempt,
                    retry_outcome="timeout",
                    parser_failure=failure or "timeout",
                )
            except EndpointDown as exc:
                if exc.fatal:
                    self._down = True
                return finish(
                    "endpoint_unavailable",
                    response_hash=last_hash,
                    retries=attempt,
                    retry_outcome="endpoint_unavailable",
                    http_status=exc.status,
                    parser_failure="endpoint_unavailable",
                )
            except MalformedBody as exc:
                raw = exc.text
                status = exc.status
            last_hash = _sha256(raw)
            parsed, category = parse_classification_detail(raw)
            if parsed is not None:
                retry_outcome = "ok_on_first" if attempt == 0 else "ok_on_retry"
                return finish(
                    "ok",
                    response_hash=last_hash,
                    retries=attempt,
                    retry_outcome=retry_outcome,
                    http_status=status,
                    parser_failure=failure,
                    **parsed,
                )
            failure = category or "malformed"
            record["http_status"] = status
            record["parser_failure"] = failure
        return finish(
            "malformed",
            response_hash=last_hash,
            retries=1,
            retry_outcome="malformed",
            parser_failure=failure,
        )
