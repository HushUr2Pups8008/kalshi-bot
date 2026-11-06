"""Local settlement-class client. Not a probability and not an order path.

The client talks only to a configured OpenAI-compatible base URL. A missing
server, a timeout, or a malformed body fails that classification. Paid hosts
are refused before any socket. xAI is never a fallback.
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

from utils.local_triage import CLASSES

_AMBIGUITY = ("none", "ambiguous", "insufficient_evidence")
_ALLOWED_KEYS = {"class", "settlement_source", "rationale", "ambiguity"}
_PROBABILITY_KEYS = {"p_raw", "p_final", "probability"}
_PAID_HOSTS = ("x.ai", "openai.com", "anthropic.com")
_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["class", "settlement_source", "rationale", "ambiguity"],
    "properties": {
        "class": {"type": "string", "enum": list(CLASSES)},
        "settlement_source": {"type": "string"},
        "rationale": {"type": "string"},
        "ambiguity": {"type": "string", "enum": list(_AMBIGUITY)},
    },
}
_SYSTEM = (
    "Classify Kalshi settlement rules and the settlement source. "
    "This is not a probability and not a trading signal. "
    "Do not return a probability. "
    "If the rules are missing or more than one class fits, use class ambiguous. "
    "Use ambiguity insufficient_evidence when the rules do not support a class. "
    "Use ambiguity ambiguous when more than one class fits. "
    "Use ambiguity none only when one class fits."
)


class EndpointDown(Exception):
    """The local server could not be reached.

    ``fatal`` means later shortlist rows must not wait on the same dead socket.
    An HTTP 5xx is not fatal: the server answered.
    """

    def __init__(self, fatal: bool = True) -> None:
        super().__init__("local endpoint unavailable")
        self.fatal = fatal


class MalformedBody(Exception):
    """The server answered, but the body is not the schema."""


def is_paid_research_host(base_url: str) -> bool:
    host = (urlparse(base_url).hostname or "").lower().rstrip(".")
    if not host:
        return False
    return any(host == name or host.endswith("." + name) for name in _PAID_HOSTS)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_prompt(item: Mapping[str, Any]) -> str:
    sources = item.get("settlement_sources") or ()
    if isinstance(sources, str):
        source_text = sources
    else:
        source_text = ", ".join(str(label) for label in sources)
    return "\n".join(
        (
            "Classify the settlement rules. This is not a probability and not a trade.",
            f"ticker: {item.get('ticker') or ''}",
            f"series: {item.get('series_ticker') or ''}",
            f"title: {item.get('title') or ''}",
            f"rules_primary: {item.get('rules_primary') or ''}",
            f"rules_secondary: {item.get('rules_secondary') or ''}",
            f"settlement_sources: {source_text}",
            f"rule_version: {item.get('rule_version') or 'missing'}",
            "classes: countable, lookup, market_price, opinion, sports, other, ambiguous",
            "ambiguity: none, ambiguous, insufficient_evidence",
            "If the evidence is thin, class is ambiguous and ambiguity is insufficient_evidence.",
            "Do not return p_raw, p_final, or probability.",
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


def parse_classification(raw: str) -> dict[str, str] | None:
    """Schema object, or None when the body is malformed.

    A probability field is malformed and is not returned.
    """
    payload = _load_object(raw)
    if payload is None:
        return None
    lowered = {str(key).lower() for key in payload}
    if lowered & _PROBABILITY_KEYS:
        return None
    if set(payload) != _ALLOWED_KEYS:
        return None
    label = payload.get("class")
    source = payload.get("settlement_source")
    rationale = payload.get("rationale")
    ambiguity = payload.get("ambiguity")
    if not isinstance(label, str) or label not in CLASSES:
        return None
    if not isinstance(source, str) or not isinstance(rationale, str):
        return None
    if not isinstance(ambiguity, str) or ambiguity not in _AMBIGUITY:
        return None
    if label == "ambiguous" and ambiguity == "none":
        return None
    if ambiguity == "insufficient_evidence" and label != "ambiguous":
        return None
    if ambiguity == "ambiguous" and label != "ambiguous":
        return None
    return {
        "class": label,
        "settlement_source": " ".join(source.split())[:300],
        "rationale": " ".join(rationale.split())[:400],
        "ambiguity": ambiguity,
    }


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
    """One shortlist item per call. The first dead endpoint skips the rest."""

    def __init__(
        self,
        *,
        base_url: str,
        model_id: str,
        runtime: str,
        timeout_s: float,
        opener: Callable[..., Any] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.base_url = base_url.strip().rstrip("/")
        self.model_id = model_id.strip()
        self.runtime = runtime.strip() or "unconfigured"
        self.timeout_s = timeout_s
        self._opener = opener or urllib.request.urlopen
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._down = False
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

    def _url(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return self.base_url + "/chat/completions"

    def _post(self, prompt: str, *, schema: bool) -> str:
        if self.refused_paid_host or is_paid_research_host(self._url()):
            raise EndpointDown
        response_format: dict[str, Any]
        if schema:
            response_format = {
                "type": "json_schema",
                "json_schema": {"name": "settlement_class", "strict": True, "schema": _SCHEMA},
            }
        else:
            response_format = {"type": "json_object"}
        body = {
            "model": self.model_id,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": prompt},
            ],
            "response_format": response_format,
        }
        encoded = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            self._url(),
            data=encoded,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self._opener(request, timeout=self.timeout_s) as response:
                raw = response.read()
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
                raise EndpointDown(fatal=False) from exc
            raise MalformedBody(detail) from exc
        except urllib.error.URLError as exc:
            raise EndpointDown(fatal=True) from exc
        except OSError as exc:
            raise EndpointDown(fatal=True) from exc
        if isinstance(raw, bytes):
            return raw.decode("utf-8", "replace")
        return str(raw)

    def classify(self, item: Mapping[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        prompt = build_prompt(item)
        record: dict[str, Any] = {
            "ts": self._stamp(),
            "ticker": str(item.get("ticker") or ""),
            "model_id": self.model_id,
            "runtime": self.runtime,
            "prompt_hash": _sha256(prompt),
            "response_hash": "",
            "latency_ms": 0,
            "rule_version": str(item.get("rule_version") or "missing"),
            "class": "",
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
            return finish("refused_paid_host")
        if not self.base_url or not self.model_id:
            return finish("unconfigured")
        if self._down:
            return finish("endpoint_unavailable")
        last_hash = ""
        for attempt in (0, 1):
            try:
                raw = self._post(prompt, schema=attempt == 0)
            except TimeoutError:
                return finish("timeout", response_hash=last_hash, retries=attempt)
            except EndpointDown as exc:
                if exc.fatal:
                    self._down = True
                return finish("endpoint_unavailable", response_hash=last_hash, retries=attempt)
            except MalformedBody as exc:
                raw = str(exc)
            last_hash = _sha256(raw)
            parsed = parse_classification(raw)
            if parsed is not None:
                return finish("ok", response_hash=last_hash, retries=attempt, **parsed)
        return finish("malformed", response_hash=last_hash, retries=1)
