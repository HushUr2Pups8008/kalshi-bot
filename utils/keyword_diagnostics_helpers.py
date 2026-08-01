from __future__ import annotations

import hashlib
import json
import os
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping

from utils.trade_log_reader import TradeLogReadStats, iter_trade_records

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOG_PATH = REPO_ROOT / "logs" / "trades"

TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9']+")
STOPWORDS = {
    "a", "after", "again", "all", "amid", "an", "and", "are", "as", "at",
    "be", "before", "but", "by", "for", "from", "has", "have", "here", "his",
    "in", "into", "is", "it", "its", "latest", "live", "more", "new", "not",
    "of", "on", "or", "over", "said", "says", "that", "the", "their", "this",
    "to", "updates", "was", "what", "when", "where", "who", "why", "will",
    "with",
}

DEFAULT_SHADOW_PHRASES: list[str] = [
    "strait hormuz",
    "hormuz blockade",
    "blockade iran",
]

CONCENTRATION_FLAG_THRESHOLD = 0.50
PROMOTE_MIN_HITS = 5
PROMOTE_MIN_SOURCES = 2
PROMOTE_MIN_SCORE = 20

BUCKET_PROMOTE = "promote candidate"
BUCKET_SHADOW = "continue shadowing"
BUCKET_REJECT = "reject for now"
BUCKET_COUNTER_EVIDENCE_REVIEW = "requires counter-evidence review"

KEYWORD_SHADOW_REPLAY_SNAPSHOT_SCHEMA_VERSION = 1
_MODERN_TARGET_SELECTOR = {
    "type": "ANALYSIS_REJECTED",
    "reason": "no_keywords",
    "rejection_category": "post_llm_neutral_empty_keywords",
    "method": "llm",
    "signal_branch": "empty_keywords_neutral_llm",
}
_PRECISION_RISK_SELECTOR = {
    "type": "MATCH_LLM_REVIEW",
    "verdict": "false_positive_neutral",
    "keyword_count": 0,
}
_SNAPSHOT_DECISION_TIME_FIELDS = (
    "type",
    "ts",
    "ticker",
    "source",
    "headline",
    "reason",
    "rejection_category",
    "method",
    "signal_branch",
    "llm_direction",
    "llm_magnitude",
    "llm_confidence",
    "keywords",
    "keyword_count",
    "verdict",
    "market_title",
    "market_subtitle",
    "market_prefix",
    "venue",
    "source_class",
    "rules_primary",
    "rules_secondary",
    "contract_terms_url",
    "settlement_sources",
    "settlement_source_names",
    "settlement_source_urls",
    "source_hint_domain",
    "source_hint_query",
    "runtime_paper_cohort_id",
    "runtime_paper_cohort_kind",
    "runtime_paper_cohort_identity",
    "runtime_paper_cohort_manifest_sha256",
)
_SNAPSHOT_REQUIRED_FIELDS = ("type", "ts", "ticker", "source", "headline")


def _default_is_test_record(record: dict[str, Any]) -> bool:
    source = str(record.get("source") or "").lower()
    ticker = str(record.get("ticker") or "").upper()
    return "r/test" in source or "KXTEST" in ticker


def tokenize_keyword_text(text: str) -> list[str]:
    tokens = []
    for token in TOKEN_RE.findall((text or "").lower()):
        if len(token) >= 3 and token not in STOPWORDS:
            tokens.append(token)
    return tokens


def phrase_matches(phrase: str, headline: str) -> bool:
    phrase_tokens = tokenize_keyword_text(phrase)
    if not phrase_tokens:
        return False
    headline_tokens = tokenize_keyword_text(headline)
    n = len(phrase_tokens)
    for i in range(len(headline_tokens) - n + 1):
        if headline_tokens[i : i + n] == phrase_tokens:
            return True
    return False


def load_no_keyword_miss_corpus(
    path: Path,
    since: datetime | None,
    until: datetime | None,
    exclude_test: bool,
    *,
    is_test_record: Callable[[dict[str, Any]], bool] = _default_is_test_record,
) -> tuple[list[dict[str, Any]], int, int]:
    records: list[dict[str, Any]] = []
    read_stats = TradeLogReadStats()
    for record in iter_trade_records(path, since=since, until=until, stats=read_stats):
        if exclude_test and is_test_record(record):
            continue
        event_type = str(record.get("type") or record.get("event") or "").strip()
        if event_type == "ANALYSIS_REJECTED" and record.get("reason") == "no_keywords":
            records.append(record)
    return records, read_stats.lines_total, read_stats.lines_malformed


def load_keyword_shadow_replay_evidence(
    path: Path,
    since: datetime | None,
    until: datetime | None,
    exclude_test: bool,
    *,
    is_test_record: Callable[[dict[str, Any]], bool] = _default_is_test_record,
) -> dict[str, Any]:
    """Load the opt-in modern target and false-positive-neutral proxy corpora.

    The target corpus is deliberately narrower than the legacy no-keyword
    corpus.  The precision-risk corpus is a review proxy only; it does not
    establish precision and must not be used by phrase promotion scoring.
    """

    coverage_records: list[dict[str, Any]] = []
    precision_risk_records: list[dict[str, Any]] = []
    excluded_legacy_or_malformed_count = 0
    read_stats = TradeLogReadStats()
    for record in iter_trade_records(path, since=since, until=until, stats=read_stats):
        if exclude_test and is_test_record(record):
            continue
        event_type = _event_type(record)
        if event_type == "ANALYSIS_REJECTED" and record.get("reason") == "no_keywords":
            if _is_modern_target_coverage_record(record):
                coverage_records.append(record)
            else:
                excluded_legacy_or_malformed_count += 1
            continue
        if _is_precision_risk_record(record):
            precision_risk_records.append(record)

    return {
        "coverage_records": coverage_records,
        "precision_risk_records": precision_risk_records,
        "target_coverage_count": len(coverage_records),
        "precision_risk_count": len(precision_risk_records),
        "excluded_legacy_or_malformed_count": excluded_legacy_or_malformed_count,
        "lines_total": read_stats.lines_total,
        "lines_malformed": read_stats.lines_malformed,
        "selection": {
            "target_coverage": dict(_MODERN_TARGET_SELECTOR),
            "precision_risk_proxy": dict(_PRECISION_RISK_SELECTOR),
        },
        "filters": {
            "since": _format_filter_timestamp(since),
            "until": _format_filter_timestamp(until),
            "exclude_test": exclude_test,
        },
    }


def evaluate_keyword_shadow_evidence(
    evidence: Mapping[str, Any],
    phrases: list[str],
    max_examples: int = 3,
) -> dict[str, Any]:
    """Evaluate coverage and a separately labeled precision-risk proxy.

    ``hits`` remains the target-coverage value consumed by the existing scorer.
    The ``precision_risk_*`` fields are review-proxy annotations only.
    """

    coverage_records = _record_list(evidence, "coverage_records")
    precision_risk_records = _record_list(evidence, "precision_risk_records")
    result = evaluate_shadow_phrases(coverage_records, phrases, max_examples=max_examples)
    risk_result = evaluate_shadow_phrases(
        precision_risk_records,
        phrases,
        max_examples=max_examples,
    )
    risk_by_phrase = {row["phrase"]: row for row in risk_result["phrases"]}
    coverage_total = len(coverage_records)
    precision_risk_total = len(precision_risk_records)
    for row in result["phrases"]:
        risk_row = risk_by_phrase[row["phrase"]]
        row["coverage_hits"] = row["hits"]
        row["coverage_rate"] = _rate(row["hits"], coverage_total)
        row["coverage_unique_ticker_count"] = len(row["tickers"])
        row["coverage_unique_source_count"] = len(row["sources"])
        row["precision_risk_hits"] = risk_row["hits"]
        row["precision_risk_hit_rate"] = (
            _rate(risk_row["hits"], row["coverage_hits"]) if row["coverage_hits"] else None
        )
        row["precision_risk_corpus_rate"] = _rate(risk_row["hits"], precision_risk_total)
        row["precision_risk_unique_ticker_count"] = len(risk_row["tickers"])
        row["precision_risk_unique_source_count"] = len(risk_row["sources"])

    result.update(
        {
            "target_coverage_count": coverage_total,
            "precision_risk_count": precision_risk_total,
            "excluded_legacy_or_malformed_count": _nonnegative_int(
                evidence.get("excluded_legacy_or_malformed_count")
            ),
            "precision_risk_is_proxy": True,
            "precision_risk_label": "not_precision_truth",
        }
    )
    return result


def materialize_keyword_shadow_snapshot(
    output_path: Path,
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    """Write an explicit immutable replay snapshot with an exclusive create."""

    _validate_snapshot_destination(output_path)
    snapshot_records = _snapshot_records(evidence)
    encoded_records = sorted(_canonical_json(record) for record in snapshot_records)
    records_sha256 = hashlib.sha256("\n".join(encoded_records).encode("utf-8")).hexdigest()
    header = {
        "type": "KEYWORD_SHADOW_REPLAY_EVIDENCE_SNAPSHOT_HEADER",
        "schema_version": KEYWORD_SHADOW_REPLAY_SNAPSHOT_SCHEMA_VERSION,
        "provenance": {
            "source_contract": "decision_time_log_fields_only",
            "target_coverage_selector": dict(_MODERN_TARGET_SELECTOR),
            "precision_risk_proxy_selector": dict(_PRECISION_RISK_SELECTOR),
            "target_coverage_count": len(_record_list(evidence, "coverage_records")),
            "precision_risk_count": len(_record_list(evidence, "precision_risk_records")),
            "excluded_legacy_or_malformed_count": _nonnegative_int(
                evidence.get("excluded_legacy_or_malformed_count")
            ),
            "lines_total": _nonnegative_int(evidence.get("lines_total")),
            "lines_malformed": _nonnegative_int(evidence.get("lines_malformed")),
            "filters": _snapshot_filters(evidence),
            "records_sha256": records_sha256,
        },
    }
    payload = "\n".join([_canonical_json(header), *encoded_records]) + "\n"
    _exclusive_write(output_path, payload)
    return header


def _event_type(record: Mapping[str, Any]) -> str:
    value = record.get("type") or record.get("event") or ""
    return value.strip() if isinstance(value, str) else ""


def _is_modern_target_coverage_record(record: Mapping[str, Any]) -> bool:
    return all(record.get(key) == value for key, value in _MODERN_TARGET_SELECTOR.items())


def _is_precision_risk_record(record: Mapping[str, Any]) -> bool:
    return (
        _event_type(record) == _PRECISION_RISK_SELECTOR["type"]
        and record.get("verdict") == _PRECISION_RISK_SELECTOR["verdict"]
        and _keyword_count(record) == _PRECISION_RISK_SELECTOR["keyword_count"]
    )


def _keyword_count(record: Mapping[str, Any]) -> int | None:
    """Match the local keyword_feedback parser's count normalization."""

    raw_count = record.get("keyword_count")
    if raw_count is not None:
        try:
            return int(raw_count)
        except (TypeError, ValueError):
            return None
    keywords = record.get("keywords")
    return len(keywords) if isinstance(keywords, list) else None


def _record_list(evidence: Mapping[str, Any], key: str) -> list[dict[str, Any]]:
    raw_records = evidence.get(key)
    if not isinstance(raw_records, list) or not all(isinstance(row, dict) for row in raw_records):
        raise ValueError(f"{key} must be a list of records")
    return raw_records


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def _nonnegative_int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _snapshot_filters(evidence: Mapping[str, Any]) -> dict[str, str | bool | None]:
    raw_filters = evidence.get("filters")
    if not isinstance(raw_filters, Mapping):
        raise ValueError("malformed snapshot evidence filters")
    since = raw_filters.get("since")
    until = raw_filters.get("until")
    exclude_test = raw_filters.get("exclude_test")
    if since is not None and not isinstance(since, str):
        raise ValueError("malformed snapshot evidence since filter")
    if until is not None and not isinstance(until, str):
        raise ValueError("malformed snapshot evidence until filter")
    if not isinstance(exclude_test, bool):
        raise ValueError("malformed snapshot evidence exclude-test filter")
    return {"since": since, "until": until, "exclude_test": exclude_test}


def _format_filter_timestamp(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _snapshot_records(evidence: Mapping[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for role, key in (
        ("target_coverage", "coverage_records"),
        ("precision_risk_proxy", "precision_risk_records"),
    ):
        for record in _record_list(evidence, key):
            records.append(_snapshot_record(record, role))
    return records


def _snapshot_record(record: Mapping[str, Any], role: str) -> dict[str, Any]:
    if role not in {"target_coverage", "precision_risk_proxy"}:
        raise ValueError("malformed snapshot record role")
    for field in _SNAPSHOT_REQUIRED_FIELDS:
        value = record.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"malformed snapshot record: required {field}")
    timestamp = record["ts"]
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("malformed snapshot record: invalid ts") from exc
    if parsed.tzinfo is None:
        raise ValueError("malformed snapshot record: ts must include timezone")

    snapshot = {"evidence_role": role}
    for field in _SNAPSHOT_DECISION_TIME_FIELDS:
        if field in record:
            snapshot[field] = record[field]
    try:
        json.dumps(snapshot, ensure_ascii=True, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("malformed snapshot record: non-JSON decision field") from exc
    return snapshot


def _validate_snapshot_destination(output_path: Path) -> None:
    if output_path.suffix != ".jsonl":
        raise ValueError("snapshot destination must use a .jsonl suffix")
    parent = output_path.parent
    if not parent.exists() or not parent.is_dir() or parent.is_symlink():
        raise ValueError("unsafe snapshot destination parent")
    if output_path.exists() or output_path.is_symlink():
        raise FileExistsError(f"snapshot destination already exists: {output_path}")


def _exclusive_write(output_path: Path, payload: str) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(output_path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
    except BaseException:
        try:
            output_path.unlink(missing_ok=True)
        finally:
            raise


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)


def evaluate_shadow_phrases(
    records: list[dict[str, Any]],
    phrases: list[str],
    max_examples: int = 3,
) -> dict[str, Any]:
    phrase_data: dict[str, dict[str, Any]] = {
        phrase: {
            "phrase": phrase,
            "hits": 0,
            "sources": set(),
            "tickers": set(),
            "ticker_counts": defaultdict(int),
            "examples": [],
        }
        for phrase in phrases
    }

    event_phrase_hits: list[set[str]] = [set() for _ in records]

    for idx, record in enumerate(records):
        raw_headline = str(record.get("headline") or "").strip()
        source = str(record.get("source") or "").strip()
        ticker = str(record.get("ticker") or "").strip()

        for phrase in phrases:
            if phrase_matches(phrase, raw_headline):
                data = phrase_data[phrase]
                data["hits"] += 1
                if source:
                    data["sources"].add(source)
                if ticker:
                    data["tickers"].add(ticker)
                    data["ticker_counts"][ticker] += 1
                if raw_headline not in data["examples"] and len(data["examples"]) < max_examples:
                    data["examples"].append(raw_headline)
                event_phrase_hits[idx].add(phrase)

    phrase_overlap_hits: dict[str, int] = {phrase: 0 for phrase in phrases}
    for hits in event_phrase_hits:
        if len(hits) >= 2:
            for ph in hits:
                phrase_overlap_hits[ph] += 1

    events_with_overlap = sum(1 for hits in event_phrase_hits if len(hits) >= 2)

    pair_overlap: dict[str, int] = {}
    for hits in event_phrase_hits:
        hit_list = sorted(hits)
        for i in range(len(hit_list)):
            for j in range(i + 1, len(hit_list)):
                key = f"{hit_list[i]} + {hit_list[j]}"
                pair_overlap[key] = pair_overlap.get(key, 0) + 1

    phrase_results: list[dict[str, Any]] = []
    for phrase in phrases:
        data = phrase_data[phrase]
        hits = data["hits"]
        ticker_counts = dict(data["ticker_counts"])
        concentration: dict[str, Any] = {}
        if hits > 0 and ticker_counts:
            top_ticker = max(ticker_counts, key=lambda t: ticker_counts[t])
            top_count = ticker_counts[top_ticker]
            fraction = top_count / hits
            concentration = {
                "top_ticker": top_ticker,
                "top_ticker_hits": top_count,
                "fraction": round(fraction, 3),
                "flag": fraction >= CONCENTRATION_FLAG_THRESHOLD,
            }
        phrase_results.append(
            {
                "phrase": phrase,
                "hits": hits,
                "sources": sorted(data["sources"]),
                "tickers": sorted(data["tickers"]),
                "ticker_counts": ticker_counts,
                "examples": data["examples"],
                "concentration": concentration,
                "overlap_hits": phrase_overlap_hits[phrase],
            }
        )

    total = len(records)
    events_with_any_hit = sum(1 for hits in event_phrase_hits if hits)
    events_with_no_hit = total - events_with_any_hit

    return {
        "phrases": phrase_results,
        "total_miss_events": total,
        "events_with_any_hit": events_with_any_hit,
        "events_with_no_hit": events_with_no_hit,
        "events_with_overlap": events_with_overlap,
        "pair_overlap": pair_overlap,
    }


def score_shadow_phrases(phrase_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for pr in phrase_results:
        hits = pr["hits"]
        source_count = len(pr["sources"])
        ticker_count = len(pr["tickers"])
        conc = pr.get("concentration") or {}
        concentration_flagged = conc.get("flag", False)
        overlap_hits = pr.get("overlap_hits", 0)

        score = hits * 4 + source_count * 6 + ticker_count * 3
        if concentration_flagged:
            score -= 10
        score -= overlap_hits * 2

        if hits == 0:
            bucket = BUCKET_REJECT
            reason = "zero hits in miss corpus"
        elif (
            score >= PROMOTE_MIN_SCORE
            and hits >= PROMOTE_MIN_HITS
            and source_count >= PROMOTE_MIN_SOURCES
            and not concentration_flagged
        ):
            bucket = BUCKET_PROMOTE
            reason = (
                f"score={score}, hits={hits}, sources={source_count},"
                f" tickers={ticker_count}, no concentration flag"
            )
        else:
            bucket = BUCKET_SHADOW
            causes: list[str] = []
            if score < PROMOTE_MIN_SCORE:
                causes.append(f"score {score} < {PROMOTE_MIN_SCORE}")
            if hits < PROMOTE_MIN_HITS:
                causes.append(f"hits {hits} < {PROMOTE_MIN_HITS}")
            if source_count < PROMOTE_MIN_SOURCES:
                causes.append(f"sources {source_count} < {PROMOTE_MIN_SOURCES}")
            if concentration_flagged:
                causes.append("concentration flag set")
            reason = "; ".join(causes) if causes else "below promote thresholds"

        pr["score"] = score
        pr["bucket"] = bucket
        pr["reason"] = reason

    return phrase_results


def apply_keyword_shadow_counter_evidence(phrase_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply modern proxy-only review status after unchanged coverage scoring.

    The false-positive-neutral rows are not precision truth. They only prevent
    automatic presentation of a coverage promotion until independent/manual
    evidence is reviewed.
    """

    for phrase_result in phrase_results:
        coverage_bucket = phrase_result.get("bucket", BUCKET_SHADOW)
        coverage_reason = phrase_result.get("reason", "")
        raw_proxy_hits = phrase_result.get("precision_risk_hits")
        proxy_hits_available = (
            isinstance(raw_proxy_hits, int) and not isinstance(raw_proxy_hits, bool) and raw_proxy_hits >= 0
        )
        proxy_hits = raw_proxy_hits if proxy_hits_available else 0

        phrase_result["coverage_bucket"] = coverage_bucket
        phrase_result["coverage_reason"] = coverage_reason
        phrase_result["coverage_score"] = phrase_result.get("score")
        phrase_result["automatic_promotion"] = False

        if coverage_bucket == BUCKET_PROMOTE and (not proxy_hits_available or proxy_hits > 0):
            phrase_result["bucket"] = BUCKET_COUNTER_EVIDENCE_REVIEW
            phrase_result["counter_evidence_status"] = BUCKET_COUNTER_EVIDENCE_REVIEW
            phrase_result["promotion_eligible"] = False
            phrase_result["promotion_status"] = "blocked_pending_independent_manual_evidence"
            proxy_detail = (
                f"{proxy_hits} paired false-positive-neutral proxy hit(s)"
                if proxy_hits_available
                else "unavailable false-positive-neutral proxy hit count"
            )
            phrase_result["reason"] = (
                f"coverage scoring met promote criteria, but {proxy_detail} require counter-evidence review; "
                "the proxy is not precision truth and blocks automatic promotion pending "
                "independent/manual evidence"
            )
        elif coverage_bucket == BUCKET_PROMOTE:
            phrase_result["counter_evidence_status"] = "no paired false-positive-neutral proxy hits"
            phrase_result["promotion_eligible"] = True
            phrase_result["promotion_status"] = "manual_evidence_only_not_runtime_approval"
        else:
            phrase_result["counter_evidence_status"] = "coverage scoring not promotion eligible"
            phrase_result["promotion_eligible"] = False
            phrase_result["promotion_status"] = "not_coverage_promotion_eligible"

    return phrase_results
