"""
Kalshi Geopolitical Trading Bot — entry point.

Concurrent tasks:
  1. RSS monitor        -- polls Reuters/AP/BBC/Al Jazeera every 60s
  2. Reddit monitor     -- polls r/worldnews, r/geopolitics, r/news (public JSON)
  3. WebSocket client   -- real-time Kalshi price feed
  4. Market cache refresh
  5. Daily reporter     -- logs summary every 24h, writes report file
  6. Auto-resolver      -- polls Kalshi every 30 min for settled markets

Paper trading runs INDEFINITELY until you explicitly confirm go-live.
No automatic switchover.

Commands:
    python main.py                          # run (paper mode until --go-live used)
    python main.py --report                 # print full paper trading report and exit
    python main.py --resolve TICKER YES|NO  # manually resolve a paper trade
    python main.py --go-live                # review report, then confirm live trading
    python main.py --credibility            # print source credibility table and exit
"""

import sys

_MINIMUM_PYTHON = (3, 11)


def _ensure_supported_python(version_info=None, stderr=None) -> None:
    if version_info is None:
        version_info = sys.version_info
    if stderr is None:
        stderr = sys.stderr
    if tuple(version_info[:2]) >= _MINIMUM_PYTHON:
        return

    detected = ".".join(str(part) for part in version_info[:3])
    print(
        f"Python 3.11+ is required. Detected: {detected}",
        file=stderr,
    )
    print(
        "Recreate your virtual environment using: python3.11 -m venv .venv",
        file=stderr,
    )
    raise SystemExit(1)


_ensure_supported_python()

import argparse
import asyncio
import hashlib
import itertools
import json
import logging
import math
import os
import signal
import sqlite3
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from types import ModuleType, SimpleNamespace
from typing import Any, Awaitable, Callable, Iterable  # noqa: F401 — referenced in string annotations
from urllib.parse import urlparse

from analysis import SignalAnalysis
from analysis.evidence_scorer import source_quality
from analysis.kelly import kelly_bet
from analysis.research_gate import (
    ResearchStatus,
    market_has_research_source_path,
    run_research_gate,
)
from tasks.stats.keyword_stats import KeywordStats
from tasks.research_dossier import default_store as default_research_dossier_store
from tasks.research_paper_admission import (
    ResearchBackedBlendStore,
    ResearchPaperAdmissionBridge,
)
from tasks.research_prewarm_task import ResearchPrewarmTask, market_has_actionable_price
from analysis.market_matcher import MarketMatcher, _compute_pre_llm_match_meta
from analysis.signal_analyzer import estimate_probability
from polymarket.settlement_reconciler import (
    PolymarketPublicSettlementSource,
    SettlementReconciler,
)
from tasks.stats.source_stats import SourceStats
from config import (cfg, DATA_DIR, PAPER_MIN_EDGE, PAPER_FLAT_CONTRACTS, VERSION,
                    FADE_TWEET_FEED_URLS,
                    MARKET_SERIES_BLOCKLIST_PREFIXES,
                    EARLY_MAX_NEWS_AGE_SECONDS, EARLY_MAX_NEWS_AGE_BY_SOURCE,
                    EARLY_DROP_IF_NO_TIMESTAMP, SOURCE_PRIORITY_TIERS,
                    FADE_PRICE_HIGH_THRESHOLD, FADE_PRICE_LOW_THRESHOLD,
                    DRIFT_LOG_COOLDOWN_SECS,
                    PRICE_MOVE_THRESHOLD_CENTS, PRICE_SEARCH_COOLDOWN_SECS,
                    PRICE_VELOCITY_WINDOW_SECS)
from feeds import NewsItem
from feeds.dedup import HeadlineDedup
from feeds.gdelt_monitor import run_gdelt_monitor
from feeds.search_news_monitor import run_search_news_monitor
from feeds.reddit_monitor import run_reddit_monitor
from feeds.rss_monitor import run_rss_monitor
from kalshi.rest_client import KalshiRestClient
from kalshi.websocket_client import KalshiWebSocketClient
from kalshi.source_hints import build_market_source_hint_diagnostics
from analysis.evidence_types import Evidence
from tasks.accumulation_task import AccumulationTask
from tasks.blend_task import BlendTask, TradeCandidate
from tasks.calibration_task import CalibrationTask
from tasks.structural_task import StructuralTask
from trading.executor import TradeExecutor
from trading.paper_trader import PaperTrader
from utils.logger import (
    TRADE_LOG_FILE,
    get_logger,
    emit_startup_banner,
    rotate_logs,
    trade_log,
    write_trade_log_async,
)
from utils.lifecycle import (
    build_lifecycle_id,
    settlement_source_match as evaluate_settlement_source_match,
    strict_optional_bool,
)
from utils.runtime_overrides import (
    RuntimeOverridesReader,
    get_threshold_override,
    is_source_disabled,
    set_global_reader,
)
from utils.research_prewarm_targets import (
    RESEARCH_PREWARM_EVENT_TYPES,
    record_targets_kalshi_research_prewarm,
)
from utils.research_market_eligibility import research_market_eligibility
from utils.trade_log_reader import iter_trade_records
from tasks.runtime_overrides_task import run_runtime_overrides_poll

log = get_logger("main")

_BOT_RUNTIME_LOCK = DATA_DIR / "bot_runtime.lock"

# Monotonic counter used as PriorityQueue tiebreaker so NewsItem objects
# are never compared against each other (dataclasses without __lt__ would raise).
_news_counter = itertools.count()

NEWS_CANDIDATE_DISCOVERY_TIMEOUT_SECONDS = max(
    1.0,
    float(os.getenv("NEWS_CANDIDATE_DISCOVERY_TIMEOUT_SECONDS", "20")),
)

_RESEARCH_PREWARM_BLOCKED_SERIES_PREFIXES = tuple(MARKET_SERIES_BLOCKLIST_PREFIXES) + (
    "KXMVE",
)


def _research_prewarm_ticker_blocked(ticker: str) -> bool:
    series_ticker = str(ticker or "").strip().split("-", 1)[0]
    if not series_ticker:
        return False
    return any(
        series_ticker.startswith(prefix)
        for prefix in _RESEARCH_PREWARM_BLOCKED_SERIES_PREFIXES
    )


def _recent_runtime_research_prewarm_tickers(*, now: datetime | None = None) -> list[str]:
    now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    since = now_utc - timedelta(hours=24)
    trade_log_root = TRADE_LOG_FILE.parent.parent
    last_index_by_ticker: dict[str, int] = {}
    try:
        records = iter_trade_records(
            trade_log_root,
            since=since,
            event_types=RESEARCH_PREWARM_EVENT_TYPES,
        )
        for index, record in enumerate(records):
            if not record_targets_kalshi_research_prewarm(record):
                continue
            ticker = str(record.get("ticker") or record.get("market_ticker") or "").strip()
            if ticker and not _research_prewarm_ticker_blocked(ticker):
                last_index_by_ticker[ticker] = index
    except Exception as exc:
        log.debug("[RESEARCH_PREWARM] trade-log target scan failed: %s", exc)
        return []
    return [
        ticker
        for ticker, _index in sorted(
            last_index_by_ticker.items(),
            key=lambda item: item[1],
            reverse=True,
        )
    ]


def _validate_startup_observability_probe_record(record: dict, required_fields: tuple[str, ...]) -> list[str]:
    missing = [field for field in required_fields if field not in record]
    if record.get("type") != "SIGNAL_ANALYSIS_DETAIL":
        missing.append("type=SIGNAL_ANALYSIS_DETAIL")
    if record.get("is_synthetic_probe") is not True:
        missing.append("is_synthetic_probe")
    if record.get("is_startup_probe") is not True:
        missing.append("is_startup_probe")
    return missing


def _startup_probe_matched_tokens() -> list[str]:
    return [" Iran ", "Says"]


def _source_priority(source: str) -> int:
    """Lower number = higher priority in the processing queue.
    Tier 1 = wire services (Reuters, BBC), Tier 2 = RSS default, Tier 3 = Reddit."""
    if source.startswith("r/"):
        return 3
    if source in SOURCE_PRIORITY_TIERS:
        return SOURCE_PRIORITY_TIERS[source]
    source_lower = source.strip().lower()
    for key, value in SOURCE_PRIORITY_TIERS.items():
        if key.strip().lower() == source_lower:
            return value
    return 2


def _is_floor_clamp_suspected(
    llm_direction: str | None,
    llm_magnitude: str | None,
    estimated_probability: float,
    market_probability: float,
    llm_confidence: float | None,
) -> bool:
    """PROFIT-ALIGN-003 (2026-05-25): detector for actual floor-clamps.

    `analysis/signal_analyzer._parse_llm_response` clamps the estimated
    probability at 0.05 (NO-side) / 0.95 (YES-side). When that clamp fires,
    the LLM wanted to push further but the floor caught it — meaning our
    true certainty about the exact prob is fuzzy. The 2026-05-25 trade audit
    (KXUSAIRANAGREEMENT-27-26JUN) found bot estimated prob=0.05 against
    market 0.08 (3pp edge) but the underlying LLM shift was 6.8pp,
    clamped to 3pp. The bot's edge math then anchors against the floor,
    manufacturing edge that isn't robustly supported.

    Returns True when the floor was hit:
      - LLM emitted a directional read (direction in {yes, no})
      - LLM magnitude was non-trivial (not None / "none")
      - final estimated_probability is exactly 0.05 or 0.95 (within tolerance)
      - reconstructed raw probability crossed beyond the clamp boundary

    Exact-boundary arithmetic is not enough. Example: market 0.13 with a
    small×1.0 NO shift lands at 0.05 without being caught by the clamp.
    Caller (main.py SignalAnalysis builder) multiplies kelly/capped dollars
    by cfg.floor_clamp_kelly_multiplier (default 0.5) when this is True.
    """
    EPS = 1e-6
    if llm_direction not in ("yes", "no"):
        return False
    if llm_magnitude not in ("small", "moderate", "large"):
        return False
    if llm_confidence is None:
        return False
    try:
        confidence = float(llm_confidence)
        market_prob = float(market_probability)
    except (TypeError, ValueError):
        return False
    shift_table = {
        "small": float(getattr(cfg, "magnitude_shift_small", 0.08)),
        "moderate": float(getattr(cfg, "magnitude_shift_moderate", 0.15)),
        "large": float(getattr(cfg, "magnitude_shift_large", 0.25)),
    }
    raw_probability = (
        market_prob + shift_table[llm_magnitude] * confidence
        if llm_direction == "yes"
        else market_prob - shift_table[llm_magnitude] * confidence
    )
    if llm_direction == "no":
        return (
            abs(estimated_probability - 0.05) < EPS
            and raw_probability < (0.05 - EPS)
        )
    return (
        abs(estimated_probability - 0.95) < EPS
        and raw_probability > (0.95 + EPS)
    )


def _early_max_news_age_seconds_for_source(source: str) -> int:
    """Return the freshness threshold (seconds) for a source.

    Runtime threshold overrides (via get_threshold_override) take precedence
    over the static EARLY_MAX_NEWS_AGE_BY_SOURCE map. Without a runtime
    reader registered, behavior is identical to pre-Phase-1 (static-only
    lookup).
    """
    runtime = get_threshold_override(f"EARLY_MAX_NEWS_AGE_BY_SOURCE.{source}")
    if runtime is not None:
        return int(runtime)
    if source in EARLY_MAX_NEWS_AGE_BY_SOURCE:
        return EARLY_MAX_NEWS_AGE_BY_SOURCE[source]
    source_lower = source.strip().lower()
    for key, value in EARLY_MAX_NEWS_AGE_BY_SOURCE.items():
        if key.strip().lower() == source_lower:
            return value
    return EARLY_MAX_NEWS_AGE_SECONDS


def _is_disabled_news_source(source: str) -> bool:
    """Defer to the runtime-overrides module-level helper, which combines
    static DISABLED_NEWS_SOURCES with any runtime-applied disabled set.
    Behavior with no runtime reader registered: identical to pre-Phase-1.
    """
    return is_source_disabled(source)


def _account_from_rsshub_url(url: str) -> str:
    """Extract account name from RSSHub URL. e.g. .../user/Kalshi -> 'Kalshi'"""
    if not url:
        return "unknown"
    parts = url.rstrip("/").split("/")
    if len(parts) >= 2:
        return parts[-1]
    return parts[0]  # fallback: domain or bare token


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Kalshi Geopolitical Trading Bot")
    p.add_argument("--report",      action="store_true",
                   help="Print paper trading report and exit")
    p.add_argument("--resolve",     nargs=2, metavar=("TICKER", "RESULT"),
                   help="Resolve a paper trade: --resolve TICKER YES|NO")
    p.add_argument("--go-live",     action="store_true",
                   help="Review report then confirm switching to live trading")
    p.add_argument("--credibility", action="store_true",
                   help="Print source credibility table and exit")
    p.add_argument("--rotate-logs", action="store_true",
                   help="Force a safe rollover of logs/app/bot.log and logs/app/errors.log, then exit")
    return p.parse_args()


def _is_cli_only_command(args: argparse.Namespace) -> bool:
    return bool(args.report or args.credibility or args.resolve or args.go_live or args.rotate_logs)


def _log_boot_summary(startup_context: str) -> None:
    level = logging.INFO if startup_context == "runtime" else logging.DEBUG
    log.log(
        level,
        "[BOOT] version=%s pid=%s ctx=%s cwd=%s",
        VERSION,
        os.getpid(),
        startup_context,
        os.getcwd(),
    )


def _log_bankroll_summary(notional_bankroll: float) -> None:
    log.info("Notional bankroll: $%.2f", notional_bankroll)
    if cfg.is_paper_trading:
        log.info("Configured starting bankroll (.env BANKROLL): $%.2f", cfg.bankroll)
        if abs(notional_bankroll - cfg.bankroll) > 1e-9:
            log.warning(
                "Persisted paper bankroll ($%.2f) differs from .env BANKROLL ($%.2f); "
                "paper mode resumes the saved SQLite state until you reset data/paper_trades.db.",
                notional_bankroll,
                cfg.bankroll,
            )


def _log_polymarket_account_summary(*, client_factory=None):
    from polymarket.account_client import PolymarketAccountClient
    from polymarket.status import PolymarketAccountStatus, count_positions

    if client_factory is None:
        client_factory = PolymarketAccountClient

    status = PolymarketAccountStatus(
        enabled=bool(cfg.polymarket_us_enabled),
        live_trading=bool(cfg.polymarket_us_live_trading_enabled),
        paper_execution="blend",
    )
    if not cfg.polymarket_us_enabled:
        log.info(status.log_line())
        return status

    try:
        client = client_factory()
    except Exception as exc:
        log.warning("Could not fetch Polymarket account summary: %s", exc)
        status = PolymarketAccountStatus(
            enabled=True,
            live_trading=bool(cfg.polymarket_us_live_trading_enabled),
            paper_execution="blend",
            account_error=str(exc),
        )
        log.info(status.log_line())
        return status

    account_error = None
    balance = None
    positions_count = None
    try:
        balance = client.get_balance()
        log.info("Polymarket account balance: $%.2f", balance)
    except Exception as exc:
        account_error = str(exc)
        log.warning("Could not fetch Polymarket account balance: %s", exc)
    try:
        positions_count = count_positions(client.get_positions(limit=100))
    except Exception as exc:
        account_error = account_error or str(exc)
        log.warning("Could not fetch Polymarket positions: %s", exc)
    status = PolymarketAccountStatus(
        enabled=True,
        live_trading=bool(cfg.polymarket_us_live_trading_enabled),
        paper_execution="blend",
        account_balance=balance,
        positions_count=positions_count,
        account_error=account_error,
    )
    log.info(status.log_line())
    return status


class _RuntimeInstanceGuard:
    """Best-effort singleton guard for the long-running bot runtime.

    A file lock is used instead of command-line substring matching so we can
    block duplicate launches without depending on psutil or fragile process-name
    heuristics. The lock is held only for the normal long-running bot path; CLI
    commands remain unaffected.
    """

    def __init__(self, lock_path):
        self._lock_path = lock_path
        self._handle = None

    def acquire(self) -> bool:
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = self._lock_path.open("a+", encoding="utf-8")
        try:
            self._lock_handle(handle)
        except OSError:
            handle.close()
            return False

        metadata = {
            "pid": os.getpid(),
            "cwd": os.getcwd(),
            "started_utc": datetime.now(timezone.utc).isoformat(),
            "argv": sys.argv,
        }
        handle.seek(0)
        handle.truncate()
        handle.write(json.dumps(metadata, separators=(",", ":")) + "\n")
        handle.flush()
        try:
            os.fsync(handle.fileno())
        except OSError:
            pass
        self._handle = handle
        return True

    def describe_owner(self) -> str:
        try:
            text = self._lock_path.read_text(encoding="utf-8").strip()
        except OSError:
            return "unknown"
        if not text:
            return "unknown"
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return text
        pid = payload.get("pid", "?")
        cwd = payload.get("cwd", "?")
        started = payload.get("started_utc", "?")
        return f"pid={pid} cwd={cwd} started_utc={started}"

    def release(self) -> None:
        handle = self._handle
        if handle is None:
            return
        try:
            self._unlock_handle(handle)
        finally:
            handle.close()
            self._handle = None

    @staticmethod
    def _lock_handle(handle) -> None:
        if sys.platform == "win32":
            _RuntimeInstanceGuard._lock_handle_windows(handle)
            return

        _RuntimeInstanceGuard._lock_handle_posix(handle)

    @staticmethod
    def _unlock_handle(handle) -> None:
        if sys.platform == "win32":
            _RuntimeInstanceGuard._unlock_handle_windows(handle)
            return

        _RuntimeInstanceGuard._unlock_handle_posix(handle)

    @staticmethod
    def _lock_handle_windows(handle, msvcrt_module: ModuleType | None = None) -> None:
        msvcrt = msvcrt_module
        if msvcrt is None:
            import msvcrt as msvcrt_import

            msvcrt = msvcrt_import

        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write("0")
            handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

    @staticmethod
    def _unlock_handle_windows(handle, msvcrt_module: ModuleType | None = None) -> None:
        msvcrt = msvcrt_module
        if msvcrt is None:
            import msvcrt as msvcrt_import

            msvcrt = msvcrt_import

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

    @staticmethod
    def _lock_handle_posix(handle, fcntl_module: ModuleType | None = None) -> None:
        fcntl = fcntl_module
        if fcntl is None:
            import fcntl as fcntl_import

            fcntl = fcntl_import

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    @staticmethod
    def _unlock_handle_posix(handle, fcntl_module: ModuleType | None = None) -> None:
        fcntl = fcntl_module
        if fcntl is None:
            import fcntl as fcntl_import

            fcntl = fcntl_import

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _normalized_evidence_identity(value: str | None) -> str:
    return " ".join((value or "").strip().lower().split())


def _evidence_id_for_signal(analysis: SignalAnalysis) -> str:
    """Build a deterministic event ID for idempotent evidence ingestion."""
    published = getattr(analysis.news_item, "published", None)
    published_ts = published.isoformat() if published is not None else ""
    parts = [
        analysis.market.ticker,
        _normalized_evidence_identity(analysis.news_item.source),
        _normalized_evidence_identity(getattr(analysis.news_item, "url", None)),
        _normalized_evidence_identity(analysis.news_item.headline),
        published_ts,
    ]
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return f"ev-{digest[:32]}"


def _source_class_for_evidence(source: str) -> str:
    """Map runtime source labels into the evidence source-class contract.

    Classes used by G2 (evidence source-class diversity gate):
      social    — Reddit, social-feed sources
      market    — Kalshi-internal / price-fade signals
      official  — government, intergovernmental, or institutional press
      news      — major news publications (US + UK domestic + international wires)
      regional  — foreign regional news bureaus (Israeli, Ukrainian, Iranian,
                  Turkish, etc.). Distinct from `news` so a dossier mixing
                  e.g. NYT + Times of Israel surfaces 2 classes for G2,
                  reflecting genuine independence-of-coverage.
      other     — unclassified (default-fallback)

    PROFIT-EDGE-006 (2026-05-24): expanded from PROFIT-EDGE-004 Lever A.1.
    The pre-fix classifier bucketed 29% of live news evidence (2,843 events
    from 39 distinct sources including The Washington Post, The Times of
    Israel, Kyiv Post, Iran International, AOL.com, etc.) into `other`,
    silently failing G2 even for dossiers with genuinely diverse coverage.
    """
    source_text = (source or "").strip()
    lower = source_text.lower()
    if source_text.startswith("r/"):
        return "social"
    if lower == "price_fade" or lower.startswith("kalshi://"):
        return "market"
    # PROFIT-EDGE-004 Lever A.1 — official sources
    if any(token in lower for token in (
        ".gov",
        "white house",
        "state department",
        "defense department",
        "department of war",
        "department of defense",
        "federal reserve",
        "supreme court",
        "congress",
        "parliament",
        "ministry",
        "official",
        "un news",
        "united nations",
        "european commission",
        "press releases",
        "international atomic energy agency",
        "iaea",
    )):
        return "official"
    # PROFIT-EDGE-006 — regional news bureaus. Distinct from `news` so a
    # dossier mixing e.g. NYT + Times of Israel registers 2 classes for G2.
    # Regional bureaus are matched by publication name token; the geo
    # token (`israel`, `iran`, etc.) alone is too broad — a US news outlet
    # writing about Israel doesn't become an Israeli source.
    if any(token in lower for token in (
        "times of israel",
        "kyiv post",
        "kyiv independent",
        "iran international",
        "anadolu ajansı",
        "anadolu ajansi",
        "anadolu agency",
        "shafaq news",
        "newagebd",
        "global banking",
        "the daily nk",
        "asia times",
        "asahi shimbun",
        "yomiuri shimbun",
        "japan times",
        "south china morning post",
        "haaretz",
        "jerusalem post",
        "tehran times",
    )):
        return "regional"
    if source_text.endswith(" - Google News") or source_text.endswith(" - BingNews"):
        return "news"
    if any(token in lower for token in (
        # PROFIT-EDGE-004 Lever A.1 baseline
        "reuters",
        "associated press",
        "ap news",
        "bbc",
        "nyt",
        "guardian",
        "al jazeera",
        "france 24",
        "deutsche welle",
        "defense one",
        "foreign policy",
        "politico",
        "politics",
        "just in news",
        "defense news",
        "breaking defense",
        # PROFIT-EDGE-006 — additional major US/UK news publications that
        # were silently bucketing as `other`. Verified frequencies in
        # 24-day live log: WaPo=107, AOL=126, MSN=47, USA Today=21,
        # Newsweek=18, The Independent=17, WRAL=15, NYT (long form)=25.
        "the new york times",
        "the washington post",
        "washingtonpost",
        "wapo",
        "aol.com",
        "msn",
        "usa today",
        "newsweek",
        "the independent",
        "wral",
        "bloomberg",
        "axios",
        "the hill",
        "cnn",
        "abc news",
        "nbc news",
        "cbs news",
        "fox news",
        "huffpost",
        "huffington post",
        "the atlantic",
        "wired",
        "vox",
        "vice news",
        "buzzfeed",
        "salon.com",
        "slate",
    )):
        return "news"
    return "other"


def _news_retrieval_meta(news: NewsItem) -> dict[str, str]:
    meta: dict[str, str] = {}
    for key in ("retrieval_mode", "source_hint_query", "source_hint_domain"):
        value = getattr(news, key, None)
        if isinstance(value, str) and value.strip():
            meta[key] = value.strip()
    return meta


def _source_domain(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    text = value.strip()
    parsed = urlparse(text if "://" in text else f"https://{text}")
    domain = (parsed.netloc or parsed.path.split("/", 1)[0]).lower()
    return domain[4:] if domain.startswith("www.") else domain


def _settlement_source_match(news: NewsItem, market: object) -> bool | None:
    return evaluate_settlement_source_match(
        source=getattr(news, "source", None),
        url=getattr(news, "url", None),
        source_hint_domain=getattr(news, "source_hint_domain", None),
        settlement_sources=getattr(market, "settlement_sources", ()) or (),
    )


def _counterfactual_llm_eval_context(news: NewsItem, market: object) -> dict[str, object]:
    context: dict[str, object] = {
        **_news_retrieval_meta(news),
        "source_class": _source_class_for_evidence(news.source),
    }
    for key in ("rules_primary", "rules_secondary", "contract_terms_url"):
        value = getattr(market, key, None)
        if isinstance(value, str) and value.strip():
            context[key] = value.strip()
    source_names: list[str] = []
    source_urls: list[str] = []
    for source in getattr(market, "settlement_sources", ()) or ():
        label = getattr(source, "label", None)
        url = getattr(source, "url", None)
        if isinstance(label, str) and label.strip():
            source_names.append(label.strip())
        if isinstance(url, str) and url.strip():
            source_urls.append(url.strip())
    if source_names:
        context["settlement_source_names"] = source_names
    if source_urls:
        context["settlement_source_urls"] = source_urls
    return context


def _signal_to_evidence(analysis: SignalAnalysis) -> Evidence:
    """Convert a fast-lane SignalAnalysis into an Evidence record for accumulation."""
    content = f"{analysis.news_item.headline}|{analysis.market.ticker}"
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
    published = getattr(analysis.news_item, "published", None)
    return Evidence(
        evidence_id=_evidence_id_for_signal(analysis),
        market_ticker=analysis.market.ticker,
        source=analysis.news_item.source,
        source_class=_source_class_for_evidence(analysis.news_item.source),
        headline=analysis.news_item.headline,
        ingested_ts=datetime.now(timezone.utc).isoformat(),
        implied_probability=analysis.estimated_probability,
        content_hash=content_hash,
        url=getattr(analysis.news_item, "url", None),
        published_ts=published.isoformat() if published is not None else None,
    )


class TradingBot:
    def __init__(self):
        self.rest          = KalshiRestClient()
        self.ws            = KalshiWebSocketClient()
        self.matcher       = MarketMatcher(self.rest)
        # PROFIT-CAL-001: CalibrationTask is constructed before PaperTrader so the
        # same instance can be injected into PaperTrader (for resolve-time
        # CALIBRATION_CHECK emission) and BlendTask (for get_scaling_factor).
        self._calibration_task = CalibrationTask()
        self.paper         = PaperTrader(
            startup_context="runtime",
            calibration_task=self._calibration_task,
        )
        self.executor      = TradeExecutor(self.rest, self.paper)
        # Wire live loss limit shutdown: executor calls this when the session loss
        # threshold is breached. Uses asyncio.create_task so it's safe from any context.
        self.executor.set_shutdown_callback(
            lambda: asyncio.create_task(_shutdown(self))
        )
        # Phase 1: Runtime overrides reader. Loads `data/runtime_overrides.yaml`
        # if present; falls back to a default empty state if absent. Registered
        # as the module-level singleton so utils.runtime_overrides.is_source_disabled
        # / is_keyword_disabled / get_threshold_override consult it.
        from pathlib import Path as _Path  # local alias — Path not imported at module level
        self._runtime_overrides_reader = RuntimeOverridesReader(
            path=_Path("data/runtime_overrides.yaml"),
        )
        self._runtime_overrides_reader.reload()  # synchronous initial load
        set_global_reader(self._runtime_overrides_reader)
        log.info(
            "runtime_overrides reader initialized: applied=%d sources / %d keywords / %d thresholds (mode=%s)",
            len(self._runtime_overrides_reader.snapshot().applied_disabled_sources),
            len(self._runtime_overrides_reader.snapshot().applied_disabled_keywords),
            len(self._runtime_overrides_reader.snapshot().applied_threshold_overrides),
            self._runtime_overrides_reader.snapshot().mode,
        )
        self.source_stats  = SourceStats(db_path=DATA_DIR / "paper_trades.db")
        self.keyword_stats = KeywordStats(DATA_DIR / "paper_trades.db")
        self.polymarket_paper_runtime = None
        try:
            from polymarket.paper_runtime import (
                PolymarketPaperRuntime,
                polymarket_paper_runtime_disabled_reason,
            )

            disabled_reason = polymarket_paper_runtime_disabled_reason(cfg)
            if disabled_reason is None:
                self.polymarket_paper_runtime = PolymarketPaperRuntime(
                    route_analysis=self._route_analysis_through_blend,
                    keyword_stats=self.keyword_stats,
                    source_stats=self.source_stats,
                )
                log.info("[POLYMARKET_PAPER] active paper_execution=blend")
            else:
                log.info(
                    "[POLYMARKET_PAPER] inactive reason=%s",
                    disabled_reason,
                )
        except Exception as exc:
            log.warning("[POLYMARKET_PAPER] initialization_failed error=%s", exc)
        self.ws.on_price_update(self._on_price_update)
        # Multi-lane queues and tasks.  BlendTask owns the trading queue; the
        # evidence queue feeds AccumulationTask independently of the fast lane.
        self._trading_queue: asyncio.Queue[TradeCandidate] = asyncio.Queue(maxsize=500)
        self._evidence_queue: asyncio.Queue[Evidence | None] = asyncio.Queue(maxsize=2000)
        self._blend_task = BlendTask(
            trading_queue=self._trading_queue,
            calibration=self._calibration_task,
            open_exposure_drawdown_provider=lambda: _paper_open_exposure_drawdown_pct(
                self.paper
            ),
        )
        self._research_paper_admission_bridge = ResearchPaperAdmissionBridge(
            research_store=default_research_dossier_store(),
            trading_queue=self._trading_queue,
            logger=trade_log,
            now=lambda: datetime.now(timezone.utc),
            route_analysis=self._route_research_analysis_through_blend,
        )
        self._accumulation_task = AccumulationTask()
        self._structural_task = StructuralTask()
        # Priority queue decouples feed pollers from slow LLM inference.
        # RSS/wire services (priority 1) are processed before Reddit (priority 2).
        # Tuple layout: (priority, seq, news) — seq prevents NewsItem comparison.
        self._news_queue: asyncio.PriorityQueue[tuple[int, int, NewsItem]] = asyncio.PriorityQueue(maxsize=2000)
        # Cross-source dedup: Reuters/AP/BBC often publish the same story within
        # minutes. Skip near-identical headlines seen in the last 15 minutes.
        self._dedup = HeadlineDedup()
        self._shadow_assignment_tasks: set[asyncio.Task[None]] = set()
        self._shadow_assignment_semaphore = asyncio.Semaphore(2)
        # Previous WS mid-price per ticker -- used to detect threshold crossings
        # for the price-based fade signal.
        self._ws_prev_prices: dict[str, float] = {}
        # Loop A: price velocity tracker (ticker -> deque of (monotonic_ts, mid_price))
        # Used to detect >10c moves in the last 5 minutes and trigger targeted search.
        self._ws_velocity: dict[str, deque] = defaultdict(lambda: deque(maxlen=60))
        self._last_search_triggered: dict[str, float] = {}  # ticker -> monotonic
        # Loop C: drift log cooldown (ticker -> last log monotonic time)
        self._last_drift_logged: dict[str, float] = {}
        # Loop D: previous market ticker set for new-market detection
        self._known_market_tickers: set[str] = set()
        self._known_polymarket_market_tickers: set[str] = set()
        self._last_polymarket_yes_ask_cents: dict[str, int] = {}
        self._market_refresh_lock = asyncio.Lock()
        self._startup_started_monotonic = time.monotonic()
        self._startup_started_at = datetime.now(timezone.utc)
        self._market_cache_ready_at: datetime | None = None
        self._market_cache_ready_after_secs: float | None = None
        self._market_cache_empty_discovery_passes = 0
        self._targeted_research_prewarm_tasks: set[asyncio.Task] = set()
        self._last_research_prewarm_by_ticker: dict[str, float] = {}
        self._last_targeted_research_prewarm = self._last_research_prewarm_by_ticker
        self._last_periodic_research_prewarm = self._last_research_prewarm_by_ticker

    _RETRYABLE_RESEARCH_PREWARM_REASONS = {
        "ambiguous_direction",
        "cached_dossier_insufficient",
        "cached_dossier_unvetted",
        "direction_reason_conflict",
        "no_research_hits",
        "missing_resolution_source",
        "official_data_pending",
        "insufficient_corroboration",
        "missing_estimated_probability",
        "probability_direction_conflict",
        "research_timeout",
        "research_provider_error",
        "research_adjudicator_error",
        "new_market",
    }

    def _research_prewarm_cooldown_book(self) -> dict[str, float]:
        book = getattr(self, "_last_research_prewarm_by_ticker", None)
        if book is None:
            book = {}
            self._last_research_prewarm_by_ticker = book
        for attr in (
            "_last_targeted_research_prewarm",
            "_last_periodic_research_prewarm",
        ):
            legacy = getattr(self, attr, None)
            if legacy is not None and legacy is not book:
                for ticker, started_at in legacy.items():
                    previous = book.get(ticker)
                    if previous is None or started_at > previous:
                        book[ticker] = started_at
            setattr(self, attr, book)
        return book

    def _research_prewarm_target_cooldown_seconds(self) -> float:
        return float(getattr(cfg, "research_prewarm_target_cooldown_seconds", 1800.0))

    def _research_prewarm_ticker_available(
        self,
        ticker: str,
        *,
        now_monotonic: float,
        cooldown_seconds: float | None = None,
    ) -> bool:
        if not ticker:
            return False
        cooldown = (
            self._research_prewarm_target_cooldown_seconds()
            if cooldown_seconds is None
            else cooldown_seconds
        )
        if cooldown <= 0:
            return True
        last_started = self._research_prewarm_cooldown_book().get(ticker)
        return last_started is None or now_monotonic - last_started >= cooldown

    def _mark_research_prewarm_started(
        self,
        ticker: str,
        *,
        now_monotonic: float,
    ) -> None:
        if ticker:
            self._research_prewarm_cooldown_book()[ticker] = now_monotonic

    def _clear_research_prewarm_started(self, ticker: str) -> None:
        if ticker:
            self._research_prewarm_cooldown_book().pop(ticker, None)

    def _research_prewarm_due_task_tickers(
        self,
        *,
        limit: int,
        cooldown_seconds: float,
    ) -> list[str]:
        try:
            return default_research_dossier_store().get_due_research_task_tickers(
                limit=limit,
                target_cooldown_seconds=cooldown_seconds,
            )
        except Exception as exc:
            log.warning("[RESEARCH_PREWARM] due task lookup failed: %s", exc)
            return []

    def _enrich_research_prewarm_market_source_path(self, market: object) -> object:
        if tuple(getattr(market, "settlement_sources", ()) or ()):
            return market
        series_ticker = str(getattr(market, "series_ticker", "") or "").strip()
        if not series_ticker:
            ticker = str(getattr(market, "ticker", "") or "").strip()
            series_ticker = ticker.split("-", 1)[0] if ticker else ""
        if not series_ticker or not hasattr(self.rest, "get_series"):
            return market
        try:
            metadata = self.rest.get_series(series_ticker)
        except Exception as exc:
            log.warning(
                "[RESEARCH_PREWARM] series metadata fetch failed series=%s: %s",
                series_ticker,
                exc,
            )
            return market
        if metadata is None:
            return market
        for attr in (
            "rules_primary",
            "rules_secondary",
            "contract_terms_url",
            "settlement_sources",
        ):
            current = getattr(market, attr, None)
            if current:
                continue
            value = getattr(metadata, attr, None)
            if value:
                setattr(market, attr, value)
        return market

    def _research_prewarm_market_provider(self) -> list[object]:
        try:
            markets = self.rest.get_all_open_markets(
                max_pages=int(getattr(cfg, "research_prewarm_max_pages", 5))
            )
        except Exception as exc:
            log.warning("[RESEARCH_PREWARM] open-market scan failed: %s", exc)
            return []
        max_markets = int(getattr(cfg, "research_prewarm_max_markets", 25))
        market_list = list(markets or [])
        now_monotonic = time.monotonic()
        cooldown = self._research_prewarm_target_cooldown_seconds()
        target_sequence: list[str] = []
        seen_targets: set[str] = set()
        due_task_tickers = self._research_prewarm_due_task_tickers(
            limit=max_markets,
            cooldown_seconds=cooldown,
        )
        due_task_ticker_set = set(due_task_tickers)
        for ticker in due_task_tickers + _recent_runtime_research_prewarm_tickers():
            if ticker in seen_targets:
                continue
            seen_targets.add(ticker)
            target_sequence.append(ticker)
        target_order = {
            ticker: index
            for index, ticker in enumerate(target_sequence)
        }

        def market_ticker(market: object) -> str:
            return str(getattr(market, "ticker", "") or "").strip()

        def ticker_available(ticker: str) -> bool:
            if _research_prewarm_ticker_blocked(ticker):
                return False
            return self._research_prewarm_ticker_available(
                ticker,
                now_monotonic=now_monotonic,
                cooldown_seconds=cooldown,
            )

        def mark_selected(selected: list[object]) -> list[object]:
            for market in selected:
                if not market_has_research_source_path(market):
                    continue
                ticker = market_ticker(market)
                if ticker:
                    self._mark_research_prewarm_started(
                        ticker,
                        now_monotonic=now_monotonic,
                    )
            return selected

        def is_open_market(market: object) -> bool:
            return research_market_eligibility(market).eligible

        def is_terminal_market(market: object) -> bool:
            status = str(getattr(market, "status", "") or "").lower()
            return status in {"closed", "finalized", "settled", "resolved"}

        def is_sourceable_open_market(market: object) -> bool:
            if not is_open_market(market):
                return False
            self._enrich_research_prewarm_market_source_path(market)
            return market_has_research_source_path(market)

        def open_markets_by_price(markets: Iterable[object]) -> list[object]:
            return sorted(
                [market for market in markets if is_open_market(market)],
                key=lambda market: 0 if market_has_actionable_price(market) else 1,
            )

        def sourceable_series_fallback(selected_tickers: set[str] | None = None) -> list[object]:
            if not hasattr(self.rest, "get_markets"):
                return []
            selected_tickers = selected_tickers or set()
            selected: list[object] = []
            for raw_series in getattr(
                cfg, "research_prewarm_sourceable_series_fallback", ()
            ) or ():
                if len(selected) >= max_markets:
                    break
                series_ticker = str(raw_series or "").strip()
                if not series_ticker:
                    continue
                if _research_prewarm_ticker_blocked(series_ticker):
                    continue
                try:
                    page, _cursor = self.rest.get_markets(
                        series_ticker=series_ticker,
                        limit=max_markets,
                    )
                except Exception as exc:
                    log.warning(
                        "[RESEARCH_PREWARM] sourceable fallback fetch failed "
                        "series=%s: %s",
                        series_ticker,
                        exc,
                    )
                    continue
                for market in page or ():
                    if len(selected) >= max_markets:
                        break
                    ticker = market_ticker(market)
                    if (
                        not ticker
                        or ticker in selected_tickers
                        or not ticker_available(ticker)
                    ):
                        continue
                    if not is_sourceable_open_market(market):
                        continue
                    selected.append(market)
                    selected_tickers.add(ticker)
            return selected

        if target_order:
            selected_by_ticker = {
                market_ticker(market): market
                for market in market_list
                if market_ticker(market) in target_order
            }
            selected_tickers: set[str] = set()
            selected: list[object] = []
            unsourceable_targets: list[object] = []
            direct_fetch_attempts = 0

            for ticker in target_order:
                if not ticker_available(ticker):
                    continue
                market = selected_by_ticker.get(ticker)
                if (
                    market is None
                    and hasattr(self.rest, "get_market")
                    and direct_fetch_attempts < max_markets
                ):
                    direct_fetch_attempts += 1
                    try:
                        market = self.rest.get_market(ticker)
                    except Exception as exc:
                        log.warning(
                            "[RESEARCH_PREWARM] targeted market fetch failed ticker=%s: %s",
                            ticker,
                            exc,
                        )
                        market = None
                if market is not None:
                    if is_sourceable_open_market(market):
                        selected.append(market)
                        selected_tickers.add(market_ticker(market))
                    elif is_open_market(market):
                        unsourceable_targets.append(market)
                        selected_tickers.add(market_ticker(market))
                    elif ticker in due_task_ticker_set and is_terminal_market(market):
                        # Closed due tasks must reach process_market so the
                        # research task can terminalize instead of consuming
                        # direct-fetch budget on every prewarm cycle.
                        selected.append(market)
                        selected_tickers.add(market_ticker(market))
                if len(selected) >= max_markets:
                    break
            for market in open_markets_by_price(market_list):
                if len(selected) >= max_markets:
                    break
                ticker = market_ticker(market)
                if (
                    ticker in selected_tickers
                    or not ticker_available(ticker)
                    or not is_sourceable_open_market(market)
                ):
                    continue
                selected.append(market)
            if selected:
                if len(selected) < max_markets:
                    selected.extend(
                        sourceable_series_fallback(selected_tickers)[
                            : max_markets - len(selected)
                        ]
                    )
                if len(selected) < max_markets:
                    selected.extend(
                        unsourceable_targets[
                            : max_markets - len(selected)
                        ]
                    )
                return mark_selected(selected[:max_markets])
            fallback = sourceable_series_fallback(selected_tickers)
            if fallback:
                if len(fallback) < max_markets:
                    fallback.extend(
                        unsourceable_targets[: max_markets - len(fallback)]
                    )
                return mark_selected(fallback)
            if unsourceable_targets:
                return unsourceable_targets[:max_markets]
            return open_markets_by_price(market_list)[:max_markets]
        sourceable = sorted(
            [
                market
                for market in market_list
                if is_open_market(market)
                and ticker_available(market_ticker(market))
                and market_has_research_source_path(
                    self._enrich_research_prewarm_market_source_path(market)
                )
            ],
            key=lambda market: 0 if market_has_actionable_price(market) else 1,
        )[:max_markets]
        if sourceable:
            return mark_selected(sourceable)
        fallback = sourceable_series_fallback()
        if fallback:
            return mark_selected(fallback)
        return open_markets_by_price(market_list)[:max_markets]

    def _create_research_prewarm_runtime_task(self) -> asyncio.Task | None:
        if not bool(getattr(cfg, "enable_research_prewarm_task", False)):
            return None
        prewarm = ResearchPrewarmTask(
            max_queries=int(getattr(cfg, "real_web_research_max_queries", 6)),
            research_timeout_seconds=float(
                getattr(cfg, "real_web_research_timeout_seconds", 12.0)
            ),
            max_concurrency=int(getattr(cfg, "research_prewarm_concurrency", 3)),
            target_cooldown_seconds=float(
                getattr(cfg, "research_prewarm_target_cooldown_seconds", 1800.0)
            ),
            market_result_sink=self._admit_research_prewarm_paper_review,
        )
        return asyncio.create_task(
            prewarm.run_periodic(
                self._research_prewarm_market_provider,
                interval_seconds=float(
                    getattr(cfg, "research_prewarm_interval_seconds", 900.0)
                ),
            ),
            name="research_prewarm",
        )

    async def _run_targeted_research_prewarm(self, market, skip_reason: str) -> None:
        prewarm = ResearchPrewarmTask(
            max_queries=int(getattr(cfg, "real_web_research_max_queries", 6)),
            research_timeout_seconds=float(
                getattr(cfg, "real_web_research_timeout_seconds", 12.0)
            ),
            max_concurrency=int(getattr(cfg, "research_prewarm_concurrency", 3)),
            target_cooldown_seconds=float(
                getattr(cfg, "research_prewarm_target_cooldown_seconds", 1800.0)
            ),
        )
        result = await prewarm.process_market(market)
        await prewarm.emit_result(result)
        await self._admit_research_prewarm_paper_review(result, market)
        log.info(
            "[RESEARCH_PREWARM] targeted ticker=%s source_skip_reason=%s "
            "status=%s attempted=%s evidence=%d queries=%d",
            result.market_ticker,
            skip_reason,
            result.status,
            result.attempted,
            result.evidence_count,
            result.query_count,
        )

    async def _admit_research_prewarm_paper_review(self, result, market) -> None:
        try:
            admission = await self._research_paper_admission_bridge.admit_prewarm_result(
                result,
                market,
            )
        except Exception as exc:
            log.warning(
                "[RESEARCH_PAPER_ADMISSION] failed ticker=%s status=%s error=%s",
                getattr(result, "market_ticker", None),
                getattr(result, "status", None),
                exc,
                exc_info=exc,
            )
            return
        if admission.admitted:
            log.info(
                "[RESEARCH_PAPER_ADMISSION] admitted ticker=%s enqueued=%s",
                admission.market_ticker,
                admission.enqueued,
            )
        elif getattr(result, "status", None) == "decision_grade_candidate":
            log.info(
                "[RESEARCH_PAPER_ADMISSION] blocked ticker=%s reason=%s",
                admission.market_ticker,
                admission.reason,
            )

    def _schedule_targeted_research_prewarm(self, market, skip_reason: str | None) -> bool:
        reason = str(skip_reason or "").strip()
        if reason not in self._RETRYABLE_RESEARCH_PREWARM_REASONS:
            return False
        research_mode = str(getattr(cfg, "real_web_research_mode", "off") or "off").lower()
        if research_mode not in {"production", "shadow"}:
            return False
        ticker = str(getattr(market, "ticker", "") or "").strip()
        if not ticker:
            return False
        if not hasattr(self, "_targeted_research_prewarm_tasks"):
            self._targeted_research_prewarm_tasks = set()
        now_monotonic = time.monotonic()
        cooldown = self._research_prewarm_target_cooldown_seconds()
        if not self._research_prewarm_ticker_available(
            ticker,
            now_monotonic=now_monotonic,
            cooldown_seconds=cooldown,
        ):
            return False
        self._mark_research_prewarm_started(ticker, now_monotonic=now_monotonic)
        task = asyncio.create_task(
            self._run_targeted_research_prewarm(market, reason),
            name=f"research_prewarm_target:{ticker}",
        )
        self._targeted_research_prewarm_tasks.add(task)

        def _discard(done: asyncio.Task) -> None:
            self._targeted_research_prewarm_tasks.discard(done)
            try:
                done.result()
            except asyncio.CancelledError:
                return
            except Exception as exc:
                self._clear_research_prewarm_started(ticker)
                log.warning(
                    "[RESEARCH_PREWARM] targeted ticker=%s failed: %s",
                    ticker,
                    exc,
                )

        task.add_done_callback(_discard)
        return True

    async def cancel_targeted_research_prewarm_tasks(self) -> None:
        tasks = [
            task
            for task in getattr(self, "_targeted_research_prewarm_tasks", set())
            if not task.done()
        ]
        if not tasks:
            return
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._targeted_research_prewarm_tasks.difference_update(tasks)

    # ── News pipeline ─────────────────────────────────────────────────────────

    def _source_family(self, source: str) -> str:
        if not source:
            return "other"

        s = source.strip()

        if s.endswith(" - BingNews"):
            return "bing_news_query"
        if s.endswith(" - Google News"):
            return "google_news_query"
        if s.startswith("r/"):
            return "reddit"

        lower = s.lower()
        if any(k in lower for k in [
            "reuters", "bbc", "nyt", "guardian",
            "al jazeera", "france 24", "deutsche welle",
            "defense one", "foreign policy", "politics"
        ]):
            return "publisher_rss"

        if s == "Just In News":
            return "direct_feed"

        return "other"

    def _is_disabled_source_family(self, source: str) -> bool:
        from config import DISABLED_SOURCE_FAMILIES
        family = self._source_family(source)
        return family in DISABLED_SOURCE_FAMILIES

    def _exchange_open_or_skip(self, cycle_label: str) -> bool:
        """P-7: one-fetch-per-cycle ExchangeState gate. Fetches
        /exchange/status synchronously through the REST client and applies
        fail-closed logic. Returns True iff the exchange is open and trading
        is active; logs the skip reason otherwise.

        Skip reasons emitted:
          - exchange_status_fetch_failed (None returned by REST — fail-closed)
          - exchange_paused             (exchange_active is False)
          - trading_inactive            (trading_active is False)
        """
        state = self.rest.get_exchange_status()
        if state is None:
            log.warning(
                "[P-7] Skipping %s cycle: exchange_status_fetch_failed (fail-closed)",
                cycle_label,
            )
            return False
        if not state.exchange_active:
            log.info(
                "[P-7] Skipping %s cycle: exchange_paused (exchange_active=False)",
                cycle_label,
            )
            return False
        if not state.trading_active:
            log.info(
                "[P-7] Skipping %s cycle: trading_inactive (trading_active=False)",
                cycle_label,
            )
            return False
        return True

    async def _enqueue_news(self, news: NewsItem) -> None:
        """Non-blocking enqueue from feed pollers. Drops items if queue is full."""
        from utils.logger import trade_log
        source = news.source
        headline = news.headline
        if self._is_disabled_source_family(source):
            await write_trade_log_async(
                trade_log.log_early_stale_drop,
                reason="disabled_source_family",
                source=source,
                headline=headline,
            )
            return
        if _is_disabled_news_source(source):
            await write_trade_log_async(
                trade_log.log_early_stale_drop,
                reason="disabled_source",
                source=source,
                headline=headline,
            )
            log.debug("News dropped for disabled source [%s]: %s", source, headline[:60])
            return
        published = getattr(news, "published", None)
        if not isinstance(published, datetime) or published.tzinfo is None:
            if EARLY_DROP_IF_NO_TIMESTAMP:
                await write_trade_log_async(
                    trade_log.log_early_stale_drop,
                    reason="missing_timestamp",
                    source=source,
                    headline=headline,
                )
                log.debug("News dropped for missing/invalid timestamp: %s", headline[:60])
                return
        else:
            age_secs = (datetime.now(timezone.utc) - published).total_seconds()
            threshold_secs = _early_max_news_age_seconds_for_source(source)
            if age_secs > threshold_secs:
                await write_trade_log_async(
                    trade_log.log_early_stale_drop,
                    reason="stale_by_source_policy",
                    source=source,
                    headline=headline,
                    age_seconds=age_secs,
                    threshold_seconds=threshold_secs,
                )
                log.debug(
                    "Early stale news dropped (%.0fs > %ds for %s): %s",
                    age_secs, threshold_secs, source, headline[:60],
                )
                return
        if self._dedup.is_duplicate(headline, source=source):
            return
        if isinstance(published, datetime) and published.tzinfo is not None:
            await write_trade_log_async(
                trade_log.log_early_fresh_pass,
                source=source,
                headline=headline,
                age_seconds=age_secs,
            )
            from config import ENABLE_FRESH_PASS_ASSIGNMENT_SHADOW
            if ENABLE_FRESH_PASS_ASSIGNMENT_SHADOW:
                self._schedule_fresh_pass_assignment_shadow(news)
        priority = _source_priority(source)
        if priority == 1:
            log.debug("[FAST_LANE] tier-1 source priority=%d source=[%s]: %s",
                      priority, source, headline[:60])
        try:
            self._news_queue.put_nowait((priority, next(_news_counter), news))
        except asyncio.QueueFull:
            log.warning("News queue full (%d items) -- dropping: %s",
                        self._news_queue.maxsize, headline[:60])

    def _schedule_fresh_pass_assignment_shadow(self, news: NewsItem) -> None:
        if not hasattr(self, "_shadow_assignment_tasks"):
            self._shadow_assignment_tasks = set()
        if not hasattr(self, "_shadow_assignment_semaphore"):
            self._shadow_assignment_semaphore = asyncio.Semaphore(2)

        if len(self._shadow_assignment_tasks) >= 25:
            log.debug("[SHADOW_ASSIGNMENT] backlog full; dropping diagnostic row")
            return
        task = asyncio.create_task(
            self._emit_fresh_pass_assignment_shadow(news),
            name="fresh-pass-assignment-shadow",
        )
        self._shadow_assignment_tasks.add(task)
        task.add_done_callback(self._shadow_assignment_tasks.discard)

    async def _emit_fresh_pass_assignment_shadow(self, news: NewsItem) -> None:
        try:
            from analysis.candidate_assignment_shadow import build_shadow_assignment
            from utils.logger import shadow_trade_log

            if not self.matcher._cache.has_market_snapshot():
                log.debug("[SHADOW_ASSIGNMENT] market cache cold; skipping diagnostic row")
                return
            async with self._shadow_assignment_semaphore:
                shadow_row = await asyncio.wait_for(
                    build_shadow_assignment(self.matcher, news),
                    timeout=30.0,
                )
            await write_trade_log_async(
                shadow_trade_log.log_fresh_pass_assignment_shadow,
                shadow_row.to_record(),
            )
        except asyncio.TimeoutError:
            log.warning("[SHADOW_ASSIGNMENT] timed out")
        except Exception as exc:
            log.warning("[SHADOW_ASSIGNMENT] failed: %s", exc)

    async def _news_consumer_task(self) -> None:
        """Drain the priority news queue, processing one item at a time."""
        processed = 0
        while True:
            _priority, _seq, news = await self._news_queue.get()
            try:
                await self.on_news_item(news)
            except Exception as exc:
                log.error("Unhandled error processing news item: %s", exc)
            finally:
                self._news_queue.task_done()
                processed += 1
                if processed % 10 == 0:
                    log.debug(
                        "News consumer: %d processed, queue depth=%d",
                        processed, self._news_queue.qsize(),
                    )

    async def on_news_item(self, news: NewsItem) -> None:
        log.info("[NEWS] [%s] %s", news.source, news.headline[:100])
        self.source_stats.increment_posts(news.source)

        if self.polymarket_paper_runtime is not None:
            try:
                await self.polymarket_paper_runtime.process_news(news)
            except Exception:
                log.exception("[POLYMARKET_PAPER] consumer_error")

        # P-7: one-fetch-per-cycle exchange-status fail-closed gate.
        if not self._exchange_open_or_skip("news"):
            return

        try:
            candidates = await asyncio.wait_for(
                self.matcher.find_candidates(news, refresh_cache=False),
                timeout=NEWS_CANDIDATE_DISCOVERY_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            log.warning(
                "News candidate discovery timed out after %.1fs; skipping Kalshi "
                "candidate path for source=%s headline=%s",
                NEWS_CANDIDATE_DISCOVERY_TIMEOUT_SECONDS,
                news.source,
                news.headline[:80],
            )
            return
        if not candidates:
            log.debug("No matching markets for: %s", news.headline[:60])
            return

        for market, match_score, match_meta in candidates:
            await self._process_candidate(news, market, match_score, match_meta)

    async def _emit_market_source_hint_diagnostics(self, market) -> None:
        """Emit default-off, shadow-only MarketSourceHints diagnostics.

        This path is intentionally non-behavioral: it never changes readiness,
        admission, scoring, routing, fetch cadence, websocket watches, order
        flow, DB state, or paper/live trading decisions. Diagnostic failures are
        logged and ignored so the candidate pipeline continues unchanged.
        """
        mode = getattr(cfg, "market_source_hints_mode", "off")
        emit_records = bool(getattr(cfg, "market_source_hints_emit_records", False))
        if mode == "off":
            return
        try:
            diagnostic = build_market_source_hint_diagnostics(
                market,
                mode=mode,
                emit_records=emit_records,
            )
        except Exception as exc:  # pragma: no cover - tested via behavior, not log text
            log.warning(
                "[MARKET_SOURCE_HINTS] diagnostic build failed ticker=%s mode=%s: %s",
                getattr(market, "ticker", "unknown"),
                mode,
                exc,
            )
            return

        targets = [
            {
                "source": target.source.canonical_name,
                "domain": target.source.domain,
                "source_class": target.source.source_class.value,
                "query_count": len(target.search_queries),
                "feed_url_count": len(target.feed_urls),
            }
            for target in diagnostic.plan.targets
        ]
        log_fn = log.info if diagnostic.mode == "advisory" else log.debug
        log_fn(
            "[MARKET_SOURCE_HINTS] ticker=%s mode=%s shadow_only=%s targets=%d "
            "rejected_labels=%d emit_records=%s",
            diagnostic.ticker,
            diagnostic.mode,
            diagnostic.shadow_only,
            len(targets),
            len(diagnostic.plan.rejected_labels),
            emit_records,
        )
        if emit_records:
            from utils.logger import trade_log

            await write_trade_log_async(
                trade_log.log_market_source_hint_diagnostic,
                ticker=diagnostic.ticker,
                mode=diagnostic.mode,
                shadow_only=diagnostic.shadow_only,
                targets=targets,
                counters=diagnostic.counters,
                rejected_labels=diagnostic.plan.rejected_labels,
                log_records=diagnostic.log_records,
            )

    async def _process_candidate(self, news: NewsItem, market, match_score: float, match_meta: dict | None = None) -> None:
        from utils.logger import trade_log
        # P-7: per-market status guard. Upstream of price-availability and
        # tradeable checks — the market must be in Kalshi's "active" state.
        if getattr(market, "status", None) != "active":
            # F-15: emit at INFO so this skip event surfaces in the default
            # production log level. Pre-fix this was DEBUG, which combined
            # with the silent attrition fixed in F-08 made the candidate
            # funnel hard to diagnose from log tails alone.
            log.info(
                "[P-7] Skipping market %s: status_not_active (status=%s)",
                market.ticker, getattr(market, "status", None),
            )
            return
        # P-5 CR-C / LD-2: short-circuit when the market has no executable
        # price. Reading `market.yes_price` is now guarded; honor it here
        # before any downstream consumer would crash.
        if not getattr(market, "price_available", True):
            # F-15: emit at INFO so this skip event surfaces in the default
            # production log level (see F-15 rationale on the sibling branch
            # above). The market.price_available=False condition is the
            # producer-side surface area F-06 watches; making it visible
            # at INFO completes the diagnostic loop.
            log.info(
                "[ANALYSIS] price_unavailable ticker=%s source=%s -- skipping candidate",
                market.ticker, news.source,
            )
            return
        # Staleness check: skip if the article is too old when we process it.
        # With a queue, items can sit for several minutes; old news is already
        # priced in.
        #
        # PARITY INVARIANT (PROFIT-STALE-001, fix/stale-news-source-aware-analyzer):
        # the analyzer-stage threshold MUST equal the intake-stage threshold
        # for the same source. The pre-fix code used a flat
        # MAX_NEWS_AGE_SECONDS=300, while intake admits items under the
        # per-source EARLY_MAX_NEWS_AGE_BY_SOURCE override (1800s for ~25
        # geopolitical sources). The result was a 19% analyzer-stage stale-
        # loss in the 2026-05-24 audit — items that intake legitimately
        # admitted then failed analyzer's stricter check with no recourse.
        # See docs/profit_path_debt_log.md (PROFIT-STALE-001) for the audit
        # data. Sharing the helper enforces the invariant in code.
        threshold_secs = _early_max_news_age_seconds_for_source(news.source)
        age_secs = (datetime.now(timezone.utc) - news.published).total_seconds()
        market_yes_price = market.yes_price
        if age_secs > threshold_secs:
            await write_trade_log_async(
                trade_log.log_analysis_rejected,
                reason="stale_news",
                ticker=market.ticker,
                source=news.source,
                headline=news.headline,
                match_score=match_score,
                age_seconds=age_secs,
                threshold_seconds=threshold_secs,
            )
            log.debug(
                "Stale news skipped (%.0fs > %ds for %s): %s",
                age_secs, threshold_secs, news.source, news.headline[:60],
            )
            return

        # WS midpoint mutation removed in P-4 (LD-11): WS is reference /
        # staleness signal only; REST normalized prices via the P-2 normalizer
        # remain the canonical executable source.
        price_source = "rest_cache"

        log.debug(
            "[ANALYSIS] candidate ticker=%s source=%s match_score=%.3f age=%.0fs "
            "price_source=%s yes_price=%.1fc cache_yes_price=%.1fc",
            market.ticker,
            news.source,
            match_score,
            age_secs,
            price_source,
            market.yes_price,
            market_yes_price,
        )
        await self._emit_market_source_hint_diagnostics(market)

        # PROFIT-MATCH-003 (L2-a): thread the matcher score onto match_meta so
        # the downstream MATCH_LLM_REVIEW emission carries it. The feedback
        # loop's score-gate (analysis/match_feedback.py) only counts a
        # false_positive_neutral verdict toward downweighting when the match was
        # MARGINAL (low score) -- a neutral on a clearly-correct (high-score)
        # match is "right market, no edge from this headline", not a wrong
        # match. Without this the score-gate sees no score and stays a no-op.
        news_meta = _news_retrieval_meta(news)
        raw_lifecycle_id = (
            match_meta.get("lifecycle_id") if isinstance(match_meta, dict) else None
        )
        lifecycle_id = (
            raw_lifecycle_id.strip()
            if isinstance(raw_lifecycle_id, str) and raw_lifecycle_id.strip()
            else build_lifecycle_id(
                venue=str(getattr(market, "venue", None) or "kalshi"),
                ticker=market.ticker,
                source=news.source,
                url=getattr(news, "url", None),
                headline=news.headline,
                published=getattr(news, "published", None),
            )
        )
        settlement_source_match = (
            strict_optional_bool(match_meta.get("settlement_source_match"))
            if isinstance(match_meta, dict) and "settlement_source_match" in match_meta
            else _settlement_source_match(news, market)
        )
        if isinstance(match_meta, dict):
            match_meta.setdefault("match_score", round(float(match_score), 4))
            match_meta.setdefault("source_class", _source_class_for_evidence(news.source))
            for key, value in news_meta.items():
                match_meta.setdefault(key, value)
            match_meta["lifecycle_id"] = lifecycle_id
            match_meta["settlement_source_match"] = settlement_source_match

        estimated_prob, confidence, keywords, reasoning, llm_dir, llm_mag, llm_conf = \
            await estimate_probability(news, market, keyword_stats=self.keyword_stats, match_meta=match_meta)
        # PROFIT-EDGE-001: reject only when neither signal source produced
        # anything. Empty `keywords` with a usable LLM signal (mag != none) is
        # a legitimate LLM-only path — the LLM identifies semantic relevance
        # the keyword glossary misses, and the governance agent (v0.29.55+)
        # learns keyword-config gaps from these events. Pre-fix, all 5 LLM-
        # validated real-market events in a 9-day window were killed here.
        llm_emitted_signal = llm_mag is not None and llm_mag != "none"
        saw_llm_result = llm_dir is not None or llm_mag is not None or llm_conf is not None
        sparse_neutral_keywords = (
            bool(keywords)
            and len(keywords) <= 1
            and not llm_emitted_signal
            and saw_llm_result
        )
        if (not keywords and not llm_emitted_signal) or sparse_neutral_keywords:
            eval_context = _counterfactual_llm_eval_context(news, market) if saw_llm_result else {}
            research_mode = str(getattr(cfg, "real_web_research_mode", "off") or "off").lower()
            should_research = research_mode in {"production", "shadow"}
            if should_research:
                def _ask_prob(*names: str) -> float | None:
                    for name in names:
                        raw = getattr(market, name, None)
                        if raw is None:
                            continue
                        value = float(raw)
                        return value / 100.0 if value > 1.0 else value
                    return None

                dossier_store = getattr(self, "_research_dossier_store", None)
                if dossier_store is None:
                    dossier_store = default_research_dossier_store()

                research_started = datetime.now(timezone.utc)
                research_started_monotonic = time.monotonic()
                research_verdict = await run_research_gate(
                    news,
                    market,
                    model_direction=llm_dir,
                    model_confidence=llm_conf,
                    model_reason=reasoning,
                    yes_ask=_ask_prob("yes_ask_cents", "yes_ask", "yes_price"),
                    no_ask=_ask_prob("no_ask_cents", "no_ask", "no_price"),
                    live_mode=not cfg.is_paper_trading,
                    max_queries=int(getattr(cfg, "real_web_research_max_queries", 6)),
                    research_timeout_seconds=float(
                        getattr(cfg, "real_web_research_timeout_seconds", 12.0)
                    ),
                    dossier_store=dossier_store,
                    cache_only=research_mode == "production",
                    require_decision_grade=True,
                )
                research_completed = datetime.now(timezone.utc)
                eval_context.update(research_verdict.log_fields())
                eval_context.update(
                    {
                        "research_started_ts": research_started.isoformat(),
                        "research_completed_ts": research_completed.isoformat(),
                        "research_duration_ms": round(
                            (time.monotonic() - research_started_monotonic) * 1000.0,
                            2,
                        ),
                    }
                )
                if (
                    research_mode == "production"
                    and research_verdict.status
                    in {
                        ResearchStatus.TRADE_CANDIDATE,
                    }
                    and research_verdict.force_side in {"yes", "no"}
                    and research_verdict.estimated_probability is not None
                ):
                    age_after_research = (
                        datetime.now(timezone.utc) - news.published
                    ).total_seconds()
                    if age_after_research > threshold_secs:
                        await write_trade_log_async(
                            trade_log.log_analysis_rejected,
                            reason="stale_news_after_research",
                            ticker=market.ticker,
                            source=news.source,
                            headline=news.headline,
                            match_score=match_score,
                            age_seconds=age_after_research,
                            threshold_seconds=threshold_secs,
                            **eval_context,
                        )
                        return
                    estimated_prob = research_verdict.estimated_probability
                    confidence = research_verdict.confidence or llm_conf or confidence
                    keywords = ["research_evidence"]
                    reasoning = research_verdict.summary
                    llm_dir = research_verdict.force_side
                    llm_mag = "moderate"
                    llm_conf = research_verdict.confidence or llm_conf
                elif research_mode == "production":
                    status = research_verdict.status
                    if status == ResearchStatus.CONTINUE_RESEARCHING:
                        reason = "research_incomplete"
                        category = "research_continue"
                        signal_branch = (
                            "sparse_keywords_research_continue"
                            if sparse_neutral_keywords
                            else "empty_keywords_research_continue"
                        )
                        self._schedule_targeted_research_prewarm(
                            market,
                            research_verdict.skip_reason,
                        )
                    elif status in {
                        ResearchStatus.RESEARCH_PROVIDER_ERROR,
                        ResearchStatus.RESEARCH_ADJUDICATOR_ERROR,
                    }:
                        reason = "research_operational_error"
                        category = status.value
                        signal_branch = (
                            "sparse_keywords_research_error"
                            if sparse_neutral_keywords
                            else "empty_keywords_research_error"
                        )
                        self._schedule_targeted_research_prewarm(
                            market,
                            research_verdict.skip_reason,
                        )
                    else:
                        reason = "researched_no_edge"
                        category = status.value
                        signal_branch = (
                            "sparse_keywords_researched_terminal"
                            if sparse_neutral_keywords
                            else "empty_keywords_researched_terminal"
                        )
                        if (
                            research_verdict.skip_reason
                            in self._RETRYABLE_RESEARCH_PREWARM_REASONS
                        ):
                            self._schedule_targeted_research_prewarm(
                                market,
                                research_verdict.skip_reason,
                            )
                    await write_trade_log_async(
                        trade_log.log_analysis_rejected,
                        reason=reason,
                        rejection_category=category,
                        signal_branch=signal_branch,
                        method="llm" if saw_llm_result else None,
                        llm_direction=llm_dir,
                        llm_magnitude=llm_mag,
                        llm_confidence=llm_conf,
                        keywords=keywords,
                        ticker=market.ticker,
                        source=news.source,
                        headline=news.headline,
                        match_score=match_score,
                        **eval_context,
                    )
                    return
                elif research_verdict.status == ResearchStatus.CONTINUE_RESEARCHING:
                    self._schedule_targeted_research_prewarm(
                        market,
                        research_verdict.skip_reason,
                    )
                elif (
                    research_verdict.skip_reason
                    in self._RETRYABLE_RESEARCH_PREWARM_REASONS
                ):
                    self._schedule_targeted_research_prewarm(
                        market,
                        research_verdict.skip_reason,
                    )

        if not keywords and not llm_emitted_signal:
            saw_llm_result = llm_dir is not None or llm_mag is not None or llm_conf is not None
            if not eval_context:
                eval_context = _counterfactual_llm_eval_context(news, market) if saw_llm_result else {}
            await write_trade_log_async(
                trade_log.log_analysis_rejected,
                reason="no_keywords",
                rejection_category=(
                    "post_llm_neutral_empty_keywords"
                    if saw_llm_result
                    else "no_signal_empty_keywords"
                ),
                signal_branch=(
                    "empty_keywords_neutral_llm"
                    if saw_llm_result
                    else "empty_keywords_no_llm_signal"
                ),
                method="llm" if saw_llm_result else None,
                llm_direction=llm_dir,
                llm_magnitude=llm_mag,
                llm_confidence=llm_conf,
                keywords=keywords,
                ticker=market.ticker,
                source=news.source,
                headline=news.headline,
                match_score=match_score,
                **eval_context,
            )
            log.debug(
                "[ANALYSIS] no_signal ticker=%s source=%s match_score=%.3f",
                market.ticker,
                news.source,
                match_score,
            )
            return

        self.source_stats.increment_signals(news.source)
        # P-5 LD-10 / CR-A: two-sided executable EV side selection.
        # Replaces legacy YES-midpoint sign heuristic; each side scored
        # against its own executable ask. Fail-closed when the market is
        # not tradeable or neither side has positive edge.
        from analysis.side_selection import select_side, compute_edge
        if not market.is_tradeable():
            log.debug(
                "[ANALYSIS] not_tradeable ticker=%s -- skipping",
                market.ticker,
            )
            return
        try:
            side, executed_price_cents = select_side(market, estimated_prob)
        except ValueError as exc:
            log.debug(
                "[ANALYSIS] no_positive_edge ticker=%s reason=%s",
                market.ticker, exc,
            )
            return
        try:
            edges = compute_edge(market, estimated_prob)
        except ValueError as exc:
            log.debug(
                "[ANALYSIS] edge_unavailable ticker=%s reason=%s",
                market.ticker, exc,
            )
            return
        edge = edges.yes_edge if side == "yes" else edges.no_edge
        method = "llm" if any(value is not None for value in (llm_dir, llm_mag, llm_conf)) else "keyword"

        # Get source credibility multiplier
        source_mult = self.paper.credibility.get_multiplier(news.source)

        # Dynamic max bet based on current notional bankroll
        notional   = self.paper.get_notional_bankroll()
        max_bet    = cfg.dynamic_max_bet(notional)
        min_edge   = PAPER_MIN_EDGE if cfg.is_paper_trading else cfg.min_edge

        # Days to close -- used by kelly_bet() for time discount
        from analysis.market_matcher import _days_to_close
        days_to_close = _days_to_close(market.close_time) or 14.0

        # P-5 LD-10 / CR-A: Kelly consumes the executed-side ask in cents,
        # not the YES midpoint. For NO trades this is no_ask_cents; for
        # YES trades this is yes_ask_cents. Replaces market.yes_price
        # (midpoint), which silently broke Kelly sizing on NO-side trades.
        kelly_frac, kelly_dollars, capped_dollars = kelly_bet(
            estimated_probability=estimated_prob if side == "yes" else (1.0 - estimated_prob),
            market_price_cents=executed_price_cents,
            bankroll=notional,
            kelly_fraction=cfg.kelly_fraction,
            max_bet_dollars=max_bet,
            min_bet_dollars=cfg.min_bet_dollars,
            min_edge=min_edge,
            source_multiplier=source_mult,
            confidence=confidence,
            days_to_close=days_to_close,
            time_discount_half_life=cfg.time_discount_half_life,
            time_discount_floor=cfg.time_discount_floor,
        )

        # PROFIT-ALIGN-003 (2026-05-25): floor-clamp Kelly halving.
        # See _is_floor_clamp_suspected() for the full rationale + audit
        # context. Mitigation: when clamp is suspected, multiply the Kelly
        # stake by cfg.floor_clamp_kelly_multiplier (default 0.5).
        # Conservative; affects only clamped trades.
        if (
            _is_floor_clamp_suspected(
                llm_dir,
                llm_mag,
                estimated_prob,
                market.yes_prob,
                llm_conf,
            )
            and cfg.floor_clamp_kelly_multiplier < 1.0
        ):
            _pre = capped_dollars
            kelly_dollars *= cfg.floor_clamp_kelly_multiplier
            capped_dollars *= cfg.floor_clamp_kelly_multiplier
            log.debug(
                "[KELLY] floor_clamp_halved ticker=%s side=%s "
                "est_prob=%.4f mult=%.2f capped=$%.2f→$%.2f",
                market.ticker, side, estimated_prob,
                cfg.floor_clamp_kelly_multiplier, _pre, capped_dollars,
            )

        log.debug(
            "[ANALYSIS] decision_input ticker=%s source=%s side=%s edge=%+.4f "
            "est_prob=%.3f market_prob=%.3f confidence=%.2f keywords=%d "
            "src_mult=%.2f kelly=$%.2f capped=$%.2f llm_dir=%s llm_mag=%s",
            market.ticker,
            news.source,
            side.upper(),
            edge,
            estimated_prob,
            market.yes_prob,
            confidence,
            len(keywords),
            source_mult,
            kelly_dollars,
            capped_dollars,
            llm_dir or "-",
            llm_mag or "-",
        )

        await write_trade_log_async(
            trade_log.log_signal,
            source=news.source,
            headline=news.headline,
            url=news.url,
            signal_strength=abs(edge),
            keywords_matched=keywords,
        )
        raw_venue = match_meta.get("venue") if isinstance(match_meta, dict) else None
        if raw_venue is None:
            raw_venue = getattr(market, "venue", None)
        if hasattr(raw_venue, "value"):
            raw_venue = raw_venue.value
        opportunity_venue = str(raw_venue).strip().lower() if raw_venue is not None else "kalshi"
        await write_trade_log_async(
            trade_log.log_opportunity,
            ticker=market.ticker,
            market_title=market.title,
            entry_price_cents=float(executed_price_cents),
            estimated_probability=estimated_prob,
            edge=edge,
            kelly_fraction=kelly_frac,
            kelly_dollars=kelly_dollars,
            capped_dollars=capped_dollars,
            side=side,
            reasoning=reasoning,
            source=news.source,
            headline=news.headline,
            method=method,
            llm_direction=llm_dir,
            llm_magnitude=llm_mag,
            venue=opportunity_venue or "kalshi",
            keywords=keywords,
            source_class=_source_class_for_evidence(news.source),
            retrieval_mode=news_meta.get("retrieval_mode"),
            source_hint_domain=news_meta.get("source_hint_domain"),
            source_hint_query=news_meta.get("source_hint_query"),
            settlement_source_match=settlement_source_match,
            lifecycle_id=lifecycle_id,
        )
        self.source_stats.increment_opportunities(news.source)

        if capped_dollars <= 0:
            if not cfg.is_paper_trading:
                log.debug("No bet for %s: edge=%+.4f below threshold", market.ticker, edge)
                return
            # Paper mode uses flat contracts regardless of Kelly sizing.
            # Set a placeholder so the executor runs its own edge/position checks
            # and logs a proper SKIPPED event if needed. Use the executed-side
            # ask cents instead of the YES midpoint to avoid NO-side mispricing.
            capped_dollars = PAPER_FLAT_CONTRACTS * max(1, min(99, int(executed_price_cents))) / 100.0
            log.debug(
                "[ANALYSIS] paper_placeholder ticker=%s placeholder=$%.2f",
                market.ticker,
                capped_dollars,
            )

        signal_meta = dict(news_meta)
        signal_meta["lifecycle_id"] = lifecycle_id
        signal_meta["settlement_source_match"] = settlement_source_match

        analysis = SignalAnalysis(
            news_item=news,
            market=market,
            estimated_probability=estimated_prob,
            # Post-P0 analysis carries the executed-side ask for the chosen side.
            executed_price_cents=executed_price_cents,
            edge=edge,
            side=side,
            kelly_fraction=kelly_frac,
            kelly_dollars=kelly_dollars,
            capped_dollars=capped_dollars,
            keywords_matched=keywords,
            reasoning=reasoning,
            confidence=confidence,
            match_score=match_score,
            signal_type="news",
            llm_direction=llm_dir,
            llm_magnitude=llm_mag,
            llm_confidence=llm_conf,
            signal_meta=signal_meta or None,
        )

        log.debug(
            "[ANALYSIS] handoff ticker=%s side=%s signal_type=%s "
            "capped=$%.2f match_score=%.3f",
            analysis.market.ticker,
            analysis.side.upper(),
            analysis.signal_type,
            analysis.capped_dollars,
            analysis.match_score,
        )

        # Route through blend task instead of directly to executor.
        # BlendTask reads the dossier + structural prior, applies regime weights,
        # runs the readiness gate, and enqueues approved TradeCandidate objects.
        # The _trading_queue_consumer_task drains the queue and calls executor.execute().
        blend_result = await self._route_analysis_through_blend(analysis)
        if not blend_result.enqueued:
            log.debug(
                "[ANALYSIS] no_execution ticker=%s source=%s blocked_reason=%s",
                market.ticker,
                news.source,
                blend_result.trade_blocked_reason or "none",
            )

    async def _route_analysis_through_blend(
        self,
        analysis: SignalAnalysis,
        *,
        accumulate: bool = True,
        watch: bool = True,
        blend_task: BlendTask | None = None,
    ):
        """Submit a prepared signal through the canonical blend/readiness path."""
        if accumulate:
            evidence = _signal_to_evidence(analysis)
            analysis.signal_meta = {
                **(analysis.signal_meta or {}),
                "trigger_evidence_id": evidence.evidence_id,
                "trigger_evidence_source": evidence.source,
                "trigger_evidence_source_class": evidence.source_class,
                "trigger_evidence_headline": evidence.headline,
                "trigger_evidence_ingested_ts": evidence.ingested_ts,
                "trigger_evidence_content_hash": evidence.content_hash,
                "trigger_evidence_original_weight": source_quality(evidence.source_class),
            }
            try:
                self._evidence_queue.put_nowait(evidence)
            except asyncio.QueueFull:
                log.warning(
                    "[ACCUMULATION] evidence_queue_full ticker=%s — evidence dropped",
                    analysis.market.ticker,
                )
        task = blend_task or self._blend_task
        blend_result = await task.process_fast_lane_result(analysis)
        if watch:
            self.ws.watch([analysis.market.ticker])
        return blend_result

    async def _route_research_analysis_through_blend(
        self,
        analysis: SignalAnalysis,
        store: ResearchBackedBlendStore,
    ):
        """Route research proof through blend without feed-only side effects."""
        task = BlendTask(
            trading_queue=self._trading_queue,
            store=store,
            calibration=self._calibration_task,
            logger=trade_log,
            is_paper_mode=True,
            open_exposure_drawdown_provider=lambda: _paper_open_exposure_drawdown_pct(
                self.paper
            ),
        )
        return await self._route_analysis_through_blend(
            analysis,
            accumulate=False,
            watch=False,
            blend_task=task,
        )

    # ── Fade tweet pipeline ────────────────────────────────────────────────────

    async def _on_fade_tweet(self, tweet: NewsItem) -> None:
        """Callback for the fade-tweet RSS monitor. Extracts source account and routes."""
        account = _account_from_rsshub_url(tweet.url or "")
        log.info("[FADE] [%s] %s", account, tweet.headline[:100])
        # P-7: one-fetch-per-cycle exchange-status fail-closed gate.
        if not self._exchange_open_or_skip("fade_tweet"):
            return
        await self._process_fade_tweet(tweet, account)

    async def _process_fade_tweet(self, tweet: NewsItem, account: str) -> None:
        """
        Separate code path for prediction-market hype tweets.

        Detects hype/ATH patterns and fades them: bullish tweet → buy NO.
        Searches ALL active markets (geo + sports) so we can compare efficacy
        across both categories during the paper trading validation phase.
        All trades are tagged [FADE/GEO/@account] or [FADE/SPORTS/@account] for DB analysis.
        """
        from analysis.fade_signal import detect_fade_pattern

        pattern = detect_fade_pattern(tweet)
        if not pattern:
            log.debug("[FADE] [%s] No fade pattern: %s", account, tweet.headline[:80])
            return

        candidates = await self.matcher.find_all_candidates(tweet)
        if not candidates:
            log.debug("[FADE] [%s] No matching market: %s", account, tweet.headline[:80])
            return

        market, score = candidates[0]

        # P-7: per-market status guard. Upstream of tradeable check.
        if getattr(market, "status", None) != "active":
            log.debug(
                "[P-7] Skipping market %s: status_not_active (status=%s)",
                market.ticker, getattr(market, "status", None),
            )
            return

        # P-5 CR-C: short-circuit when the matched market has no executable price.
        if not market.is_tradeable():
            log.debug(
                "[FADE] not_tradeable ticker=%s -- skipping",
                market.ticker,
            )
            return

        # WS midpoint mutation removed in P-4 (LD-11): see fade flow above.

        # Fade: bullish tweet → buy NO (short the hype)
        fade_side = "no" if pattern == "bullish" else "yes"
        edge_sign = -1.0 if fade_side == "no" else 1.0
        fake_edge = edge_sign * (PAPER_MIN_EDGE + 0.01)   # just above threshold

        # Tag by market category for later sports-vs-geo win-rate comparison
        _ticker = (market.series_ticker or market.ticker).upper()
        is_sports = any(_ticker.startswith(p) for p in MARKET_SERIES_BLOCKLIST_PREFIXES)
        category  = "SPORTS" if is_sports else "GEO"

        # P-5 LD-10: executed-side ask in cents for the chosen fade side.
        executed_price_cents_fade = (
            market.yes_ask_cents if fade_side == "yes" else market.no_ask_cents
        )

        analysis = SignalAnalysis(
            news_item=tweet,
            market=market,
            estimated_probability=market.yes_prob + fake_edge,
            executed_price_cents=executed_price_cents_fade,
            edge=fake_edge,
            side=fade_side,
            kelly_fraction=cfg.kelly_fraction,
            kelly_dollars=0.0,
            capped_dollars=0.0,
            keywords_matched=[],
            reasoning=(
                f"[FADE/{category}/@{account}] {pattern}: "
                f"{tweet.headline[:80]}"
            ),
            confidence=0.3,
            match_score=score,
            signal_type="fade_tweet",
        )
        log.info("[FADE/%s/@%s] %s | %s | pattern=%s | match_score=%.3f",
                 category, account, market.ticker, fade_side.upper(), pattern, score)
        await self._route_analysis_through_blend(analysis)

    async def _on_price_update(self, ticker: str, yes_bid: float, yes_ask: float) -> None:
        now_mid  = (yes_bid + yes_ask) / 2.0
        now_mono = time.monotonic()
        log.debug("[WS] %s bid=%.1fc ask=%.1fc mid=%.1fc", ticker, yes_bid, yes_ask, now_mid)

        prev_mid = self._ws_prev_prices.get(ticker)
        self._ws_prev_prices[ticker] = now_mid

        if prev_mid is None:
            # First tick: seed the velocity deque and return (no crossing yet)
            self._ws_velocity[ticker].append((now_mono, now_mid))
            return

        # ── Loop A: price velocity -> targeted search ─────────────────────────
        # Track rolling price history; trigger a targeted news search if price
        # has moved >= PRICE_MOVE_THRESHOLD_CENTS within the velocity window.
        vq = self._ws_velocity[ticker]
        vq.append((now_mono, now_mid))
        # Purge ticks older than PRICE_VELOCITY_WINDOW_SECS
        cutoff = now_mono - PRICE_VELOCITY_WINDOW_SECS
        while vq and vq[0][0] < cutoff:
            vq.popleft()
        if vq:
            oldest_price = vq[0][1]
            price_move   = abs(now_mid - oldest_price)
            last_search  = self._last_search_triggered.get(ticker, 0.0)
            if (price_move >= PRICE_MOVE_THRESHOLD_CENTS
                    and now_mono - last_search >= PRICE_SEARCH_COOLDOWN_SECS):
                self._last_search_triggered[ticker] = now_mono
                log.info(
                    "[PRICE_MOVE] %s moved %.1fc in %.0fs -- triggering targeted search",
                    ticker, price_move, PRICE_VELOCITY_WINDOW_SECS,
                )
                asyncio.create_task(self._trigger_targeted_search(ticker))

        # ── Loop C: open position drift logging ───────────────────────────────
        # If we have an open paper position on this ticker and the price has
        # drifted >= cfg.position_drift_alert_threshold from entry, log a
        # POSITION_DRIFT event. A threshold >= 1.0 disables emission.
        open_positions = self.paper.portfolio.open_positions(ticker)
        if open_positions:
            last_drift = self._last_drift_logged.get(ticker, 0.0)
            if now_mono - last_drift >= DRIFT_LOG_COOLDOWN_SECS:
                for pos in open_positions:
                    drift = now_mid - pos.entry_price_cents
                    threshold_fraction = float(cfg.position_drift_alert_threshold)
                    if threshold_fraction >= 1.0:
                        continue
                    threshold_cents = pos.entry_price_cents * threshold_fraction
                    if abs(drift) >= threshold_cents:
                        self._last_drift_logged[ticker] = now_mono
                        from utils.logger import trade_log as _tl
                        _tl.log_position_drift(
                            ticker=ticker,
                            entry_price=pos.entry_price_cents,
                            current_price=now_mid,
                            drift_cents=drift,
                            side=pos.side,
                            open_since=pos.ts,
                        )
                        log.info(
                            "[DRIFT] %s: entry=%.1fc now=%.1fc drift=%+.1fc (%s)",
                            ticker, pos.entry_price_cents, now_mid, drift, pos.side.upper(),
                        )
                        break  # one log event per ticker per cooldown period

        # ── Fade signal (existing) ────────────────────────────────────────────
        from analysis.fade_signal import detect_price_fade
        crossing = detect_price_fade(
            ticker, prev_mid, now_mid,
            FADE_PRICE_HIGH_THRESHOLD, FADE_PRICE_LOW_THRESHOLD,
        )
        if crossing is not None:
            await self._process_price_fade(ticker, crossing, now_mid, yes_bid, yes_ask)

    async def _trigger_targeted_search(self, ticker: str) -> None:
        """
        Loop A: fetch targeted Google News + GDELT results for a single market
        that just had a significant price move. Emits articles through the normal
        news queue so they go through the full signal pipeline.

        Rate-limiting is handled by the caller (_on_price_update checks
        _last_search_triggered before calling this).
        """
        try:
            markets = await self.matcher._cache.get_markets()
            market  = next((m for m in markets if m.ticker == ticker), None)
            if market is None:
                polymarket_runtime = getattr(self, "polymarket_paper_runtime", None)
                polymarket_markets = (
                    polymarket_runtime.cached_candidate_markets()
                    if polymarket_runtime is not None
                    else []
                )
                market = next(
                    (m for m in polymarket_markets if m.ticker == ticker),
                    None,
                )
                if market is None:
                    log.debug(
                        "[PRICE_MOVE] %s not in market caches, skipping targeted search",
                        ticker,
                    )
                    return

            from feeds.search_news_monitor import _markets_to_queries, _gnews_url, _bing_url
            from feeds.rss_monitor import poll_feed
            from collections import OrderedDict
            from config import DISABLED_SOURCE_FAMILIES

            queries = _markets_to_queries([market])
            if not queries:
                log.debug("[PRICE_MOVE] %s: no query tokens, skipping", ticker)
                return

            engine_urls = []
            if "google_news_query" not in DISABLED_SOURCE_FAMILIES:
                engine_urls.append(_gnews_url)
            if "bing_news_query" not in DISABLED_SOURCE_FAMILIES:
                engine_urls.append(_bing_url)
            if not engine_urls:
                log.debug("[PRICE_MOVE] %s: search families disabled, skipping", ticker)
                return

            seen: OrderedDict = OrderedDict()
            log.debug("[PRICE_MOVE] %s: targeted search query='%s'", ticker, queries[0])
            for q in queries[:1]:  # single query per triggered search
                await asyncio.gather(
                    *[
                        poll_feed(url_builder(q), self._enqueue_news, seen)
                        for url_builder in engine_urls
                    ],
                    return_exceptions=True,
                )
        except Exception as exc:
            log.warning("[PRICE_MOVE] Targeted search failed for %s: %s", ticker, exc)

    async def _process_price_fade(
        self,
        ticker: str,
        crossing: str,
        now_mid: float,
        yes_bid: float,
        yes_ask: float,
    ) -> None:
        """
        Execute a price-based fade when a geo market crosses the high/low threshold.

        high_cross (>=85c) -> buy NO  (fade the spike)
        low_cross  (<=15c) -> buy YES (fade the collapse)
        """
        # Only fade geo/political markets
        _t = ticker.upper()
        if any(_t.startswith(p) for p in MARKET_SERIES_BLOCKLIST_PREFIXES):
            log.debug("[PRICE_FADE] Skipping sports ticker %s", ticker)
            return

        # P-7: one-fetch-per-cycle exchange-status fail-closed gate.
        if not self._exchange_open_or_skip("price_fade"):
            return

        markets = await self.matcher._cache.get_markets()
        market = next((m for m in markets if m.ticker == ticker), None)
        if market is None:
            log.debug("[PRICE_FADE] %s not in geo cache, skipping", ticker)
            return

        # P-7: per-market status guard. Upstream of tradeable check.
        if getattr(market, "status", None) != "active":
            log.debug(
                "[P-7] Skipping market %s: status_not_active (status=%s)",
                market.ticker, getattr(market, "status", None),
            )
            return

        # WS midpoint mutation removed in P-4 advisory fold-in (LD-11):
        # WS is reference/staleness signal only; never overwrites REST
        # executable bid/ask. `now_mid`/`yes_bid`/`yes_ask` parameters
        # remain in scope as the *trigger* values (used for diagnostic
        # log + synthetic news headline); the trade decision below
        # consumes REST-normalized prices from `market.yes_price` /
        # `market.yes_prob` populated by the P-2 normalizer.

        # P-5 CR-C: short-circuit when the price-fade market lacks executable price.
        if not market.is_tradeable():
            log.debug(
                "[PRICE_FADE] not_tradeable ticker=%s -- skipping",
                market.ticker,
            )
            return

        fade_side = "no" if crossing == "high_cross" else "yes"
        edge_sign = -1.0 if fade_side == "no" else 1.0
        fake_edge = edge_sign * (PAPER_MIN_EDGE + 0.01)

        threshold_val = (FADE_PRICE_HIGH_THRESHOLD if crossing == "high_cross"
                         else FADE_PRICE_LOW_THRESHOLD)
        direction = "above" if crossing == "high_cross" else "below"

        synthetic_news = NewsItem(
            headline=(f"[PRICE_FADE] {ticker} crossed {direction} {threshold_val}c"
                      f" (mid={now_mid:.1f}c)"),
            url=f"kalshi://price_fade/{ticker}",
            source="price_fade",
        )

        executed_price_cents_fade = (
            market.yes_ask_cents if fade_side == "yes" else market.no_ask_cents
        )

        analysis = SignalAnalysis(
            news_item=synthetic_news,
            market=market,
            estimated_probability=market.yes_prob + fake_edge,
            executed_price_cents=executed_price_cents_fade,
            edge=fake_edge,
            side=fade_side,
            kelly_fraction=cfg.kelly_fraction,
            kelly_dollars=0.0,
            capped_dollars=0.0,
            keywords_matched=[],
            reasoning=(
                f"[PRICE_FADE] {ticker} {crossing}: mid={now_mid:.1f}c "
                f"threshold={threshold_val}c -> {fade_side.upper()}"
            ),
            confidence=0.4,
            signal_type="price_fade",
        )

        log.info("[PRICE_FADE] %s | %s | mid=%.1fc | threshold=%dc | side=%s",
                 ticker, crossing, now_mid, threshold_val, fade_side.upper())
        await self._route_analysis_through_blend(analysis, watch=False)

    # ── Scheduled tasks ───────────────────────────────────────────────────────

    async def _warm_ws_subscriptions(self) -> None:
        """
        Background task: wait for the initial market cache warmup to finish,
        then subscribe the WS to every geo market ticker so the price-fade
        detector has live prices from the start.

        This must not trigger its own cache refresh. Startup already has a
        dedicated cache warmup path via _market_refresh_task(); calling
        get_markets() here would race that path and duplicate the expensive
        /series discovery scan.
        """
        try:
            while True:
                markets = list(self.matcher._cache._markets)
                if markets:
                    tickers = [m.ticker for m in markets]
                    self.ws.watch(tickers)
                    log.info(
                        "WS: subscribed to %d geo market tickers for price fade",
                        len(tickers),
                    )
                    return
                await asyncio.sleep(0.25)
        except Exception as exc:
            log.warning("WS price-fade warm-up failed: %s", exc)

    async def _auto_resolve_task(self) -> None:
        """Poll Kalshi API for settled markets and auto-resolve open paper trades.

        Runs every 30 minutes. For each unique ticker with open paper trades,
        fetches the market from Kalshi. If status is 'finalized' or 'settled'
        and a result is present, resolves all open trades for that ticker.
        """
        _RESOLVE_INTERVAL = 1800  # 30 minutes
        while True:
            await asyncio.sleep(_RESOLVE_INTERVAL)
            try:
                await self._check_and_resolve()
            except Exception as exc:
                log.error("Auto-resolve cycle failed: %s", exc)

    async def _check_and_resolve(self) -> None:
        """Single pass: check all open paper trade tickers for settlement."""
        self.source_stats.flush()
        open_trades = await asyncio.to_thread(
            lambda: self.paper._conn.execute(
                "SELECT DISTINCT ticker, COALESCE(venue, 'kalshi') AS venue "
                "FROM paper_trades WHERE resolved = 0"
            ).fetchall()
        )
        if not open_trades:
            return

        rows = []
        for row in open_trades:
            if hasattr(row, "keys"):
                ticker = row["ticker"]
                venue = row["venue"] if "venue" in row.keys() else "kalshi"
            else:
                ticker = row[0]
                venue = row[1] if len(row) > 1 else "kalshi"
            rows.append((str(ticker), str(venue or "kalshi")))
        kalshi_tickers = [
            ticker for ticker, venue in rows
            if venue in ("", "kalshi")
        ]
        has_polymarket = any(venue == "polymarket_us" for _ticker, venue in rows)
        log.debug("Auto-resolve: checking %d open tickers", len(rows))

        loop = asyncio.get_running_loop()
        resolved_count = 0
        for ticker in kalshi_tickers:
            try:
                market = await loop.run_in_executor(
                    None, self.rest.get_market, ticker
                )
                if market is None:
                    continue
                if market.status not in ("finalized", "settled"):
                    continue
                result_str = (market.result or "").lower().strip()
                if result_str not in ("yes", "no"):
                    log.warning(
                        "Auto-resolve: %s is %s but result=%r -- skipping",
                        ticker, market.status, market.result,
                    )
                    continue
                resolved_yes = result_str == "yes"
                await self.paper.resolve_market(ticker, resolved_yes)
                resolved_count += 1
                log.info(
                    "Auto-resolve: %s -> %s",
                    ticker, "YES" if resolved_yes else "NO",
                )
            except Exception as exc:
                log.warning("Auto-resolve: failed to check %s: %s", ticker, exc)

        if has_polymarket and cfg.polymarket_us_enabled:
            try:
                result = await asyncio.to_thread(
                    lambda: SettlementReconciler(
                        source=PolymarketPublicSettlementSource(),
                        resolver=self.paper,
                    ).reconcile()
                )
                resolved_count += result.resolved
                if result.checked:
                    log.info(
                        "Auto-resolve: Polymarket checked=%d resolved=%d "
                        "not_found=%d errors=%d",
                        result.checked,
                        result.resolved,
                        result.not_found,
                        result.errors,
                    )
                if result.errors:
                    # Loud, dedicated signal: per-ticker failures are isolated by
                    # SettlementReconciler.reconcile()'s broad except and only land
                    # in the file log. Without this an all-errors outage (e.g.
                    # public API down) is indistinguishable from a quiet
                    # "nothing settled yet" cycle in the operator summary.
                    if result.errors >= result.checked:
                        log.warning(
                            "Auto-resolve: Polymarket settlement ALL %d checked "
                            "tickers errored (resolved=0) -- public API outage "
                            "likely; no settlements processed this cycle",
                            result.checked,
                        )
                    else:
                        log.warning(
                            "Auto-resolve: Polymarket settlement had %d errored "
                            "ticker(s) out of %d checked",
                            result.errors,
                            result.checked,
                        )
                if result.lane_events:
                    await self.paper.record_resolution_calibration_events(
                        result.lane_events
                    )
            except Exception as exc:
                log.warning("Auto-resolve: Polymarket settlement failed: %s", exc)

        if resolved_count:
            bankroll = await asyncio.to_thread(self.paper.get_notional_bankroll)
            log.info(
                "Auto-resolve: resolved %d/%d tickers | bankroll=$%.2f",
                resolved_count, len(rows),
                bankroll,
            )

    async def _refresh_market_cache_once(self, *, initial: bool = False) -> None:
        async with self._market_refresh_lock:
            if initial:
                log.info("[STARTUP] Market cache warmup started")
                warmup_started_at = time.monotonic()
            else:
                warmup_started_at = None

            await self.matcher.refresh_cache()

            markets = await self.matcher._cache.get_markets()
            new_tickers = {m.ticker for m in markets}
            self.ws.watch(list(new_tickers))
            first_non_empty = self._record_market_cache_ready(len(markets))

            if initial:
                warmup_elapsed = time.monotonic() - warmup_started_at
                log.info(
                    "[STARTUP] Market cache ready: %d markets (%.1fs)",
                    len(markets),
                    warmup_elapsed,
                )
            if first_non_empty is not None:
                ready_at, elapsed = first_non_empty
                log.info(
                    "[STARTUP] Market cache first_non_empty_ts=%s markets=%d "
                    "wall_clock_since_boot=%.1fs effective_multi_lane_runtime_start=true",
                    ready_at.isoformat(),
                    len(markets),
                    elapsed,
                )

            # Loop D: detect newly listed markets and proactively search for news
            if self._known_market_tickers:
                added = new_tickers - self._known_market_tickers
                for m in markets:
                    if m.ticker not in added:
                        continue
                    from utils.logger import trade_log as _tl
                    await write_trade_log_async(
                        _tl.log_new_market,
                        ticker=m.ticker,
                        title=m.title,
                        series_ticker=m.series_ticker,
                    )
                    log.info(
                        "[NEW_MARKET] %s listed: '%s' -- triggering targeted search",
                        m.ticker, m.title[:60],
                    )
                    asyncio.create_task(self._trigger_targeted_search(m.ticker))
                    if bool(getattr(cfg, "enable_research_prewarm_task", False)):
                        self._schedule_targeted_research_prewarm(m, "new_market")
            self._known_market_tickers = new_tickers

    async def _market_refresh_task(self) -> None:
        from config import MARKET_CACHE_TTL_SECONDS
        try:
            await self._refresh_market_cache_once(initial=True)
        except Exception as exc:
            log.error("Initial market cache population failed: %s", exc)
        while True:
            await asyncio.sleep(MARKET_CACHE_TTL_SECONDS)
            try:
                await self._refresh_market_cache_once()
            except Exception as exc:
                log.warning("Market refresh task error: %s", exc)

    async def _log_rotation_task(self) -> None:
        """Guarantee midnight UTC log rotation independent of emit timing.

        TimedRotatingFileHandler only rotates inside emit().  If emit-triggered
        rotation silently exits early (Python 3.14: os.path.exists(dfn) guard),
        this task fires 5 s after rolloverAt and forces rotation via
        rotate_logs() if shouldRollover() still returns True.
        """
        from utils.logger import _ensure_file_handlers, rotate_logs

        while True:
            fh, _ = _ensure_file_handlers()
            sleep_s = max(5.0, fh.rolloverAt - time.time() + 5)
            await asyncio.sleep(sleep_s)
            fh, _ = _ensure_file_handlers()
            if fh.shouldRollover(None):
                log.info("[LOG_ROT] Midnight rotation guard firing — emit rotation missed")
                try:
                    rotate_logs()
                except Exception as exc:
                    log.warning("[LOG_ROT] Forced rotation failed: %s", exc)
            else:
                log.debug("[LOG_ROT] Midnight rotation guard: rotation already complete")

    async def _log_maintenance_task(self) -> None:
        """
        Daily housekeeping for the logs/ directory.

        Retention rules:
          logs/reports/report_*.txt       -- 90 days
          logs/reports/analysis_*.txt     -- 30 days
          logs/service/service_*.log      -- active NSSM service logs (Windows only; never touched)
          logs/service/*-*.log            -- 30 days (rotated NSSM archives; Windows only)
          logs/service_stderr-*.log       -- 30 days (legacy NSSM archives in root; Windows only)
          logs/trades/archive/*.jsonl.gz  -- 12 months

        Monthly rotation:
          When the month rolls over, gzip-compresses trades.jsonl to
          logs/trades/archive/trades-YYYYMM.jsonl.gz and truncates it in-place.
          The active file is never deleted -- only truncated.

        Migration sweep (one-time, idempotent):
          Moves any bot.log.YYYY-MM-DD / errors.log.YYYY-MM-DD files still
          in logs/ root to logs/app/ so the TimedRotatingFileHandler's
          backupCount=90 enforcement covers them.
        """
        import gzip
        import shutil as _shutil
        from config import LOGS_DIR
        from utils.logger import _LOG_APP_DIR, _LOG_TRADES_DIR, _LOG_REPORTS_DIR, _LOG_ARCHIVE_DIR

        _MAINT_INTERVAL = 86_400  # 24 hours
        _INITIAL_DELAY  = 60      # 1 minute -- let the bot fully start first

        await asyncio.sleep(_INITIAL_DELAY)

        while True:
            now = datetime.now(timezone.utc)
            deleted_count = 0
            moved_count   = 0

            try:
                # ── Retention: reports/ ───────────────────────────────────────
                for pattern, max_days in (("report_*.txt", 90), ("analysis_*.txt", 30)):
                    for p in _LOG_REPORTS_DIR.glob(pattern):
                        age_days = (now.timestamp() - p.stat().st_mtime) / 86_400
                        if age_days > max_days:
                            p.unlink()
                            deleted_count += 1
                            log.info("[LOG_MAINT] Deleted old report: %s (%.0f days)", p.name, age_days)

                # ── Retention: NSSM service log archives (Windows only) ──────────
                # NSSM (Non-Sucking Service Manager) is Windows-only. These globs
                # are always empty on macOS; the block is kept for Windows deployments.
                if sys.platform == "win32":
                    service_log_dir = LOGS_DIR / "service"
                    service_archive_patterns = (
                        (service_log_dir, "service_stderr-*.log"),
                        (service_log_dir, "service_stdout-*.log"),
                        (service_log_dir, "ollama_stderr-*.log"),
                        (service_log_dir, "ollama_stdout-*.log"),
                        (LOGS_DIR, "service_stderr-*.log"),
                        (LOGS_DIR, "service_stdout-*.log"),
                        (LOGS_DIR, "ollama_stderr-*.log"),
                        (LOGS_DIR, "ollama_stdout-*.log"),
                    )
                    for base_dir, pattern in service_archive_patterns:
                        for p in base_dir.glob(pattern):
                            age_days = (now.timestamp() - p.stat().st_mtime) / 86_400
                            if age_days > 30:
                                p.unlink()
                                deleted_count += 1
                                log.info("[LOG_MAINT] Deleted old service archive: %s (%.0f days)", p, age_days)

                # ── Retention: trade archives older than 12 months ────────────
                for p in _LOG_ARCHIVE_DIR.glob("*.jsonl.gz"):
                    age_days = (now.timestamp() - p.stat().st_mtime) / 86_400
                    if age_days > 366:
                        p.unlink()
                        deleted_count += 1
                        log.info("[LOG_MAINT] Deleted old trade archive: %s (%.0f days)", p.name, age_days)

                # ── Monthly trades.jsonl rotation ─────────────────────────────
                # Rotates on the 1st of each month: archives last month's records
                # to trades/archive/trades-YYYYMM.jsonl.gz and truncates in-place.
                # Only fires on the 1st so mid-month restarts never prematurely
                # archive the current month's active data.
                trades_path = _LOG_TRADES_DIR / "trades.jsonl"
                if now.day == 1 and trades_path.exists() and trades_path.stat().st_size > 0:
                    prev_month   = (now.replace(day=1) - __import__("datetime").timedelta(days=1)).strftime("%Y%m")
                    archive_name = f"trades-{prev_month}.jsonl.gz"
                    archive_path = _LOG_ARCHIVE_DIR / archive_name
                    if not archive_path.exists():
                        with open(trades_path, "rb") as f_in:
                            with gzip.open(archive_path, "wb") as f_out:
                                _shutil.copyfileobj(f_in, f_out)
                        with open(trades_path, "w", encoding="utf-8"):
                            pass  # truncate in-place -- never delete the active file
                        log.info(
                            "[LOG_MAINT] Rotated trades.jsonl -> %s (%.1f KB compressed)",
                            archive_name,
                            archive_path.stat().st_size / 1024,
                        )

                # ── Migration sweep ───────────────────────────────────────────
                # Move rotated app logs from root to logs/app/
                for prefix in ("bot.log.", "errors.log."):
                    for p in LOGS_DIR.glob(f"{prefix}????-??-??"):
                        dest = _LOG_APP_DIR / p.name
                        if not dest.exists():
                            p.rename(dest)
                            moved_count += 1
                            log.debug("[LOG_MAINT] Migrated %s -> app/", p.name)

                # Move orphaned active logs (bot.log / errors.log in root).
                # After a restart from <v0.25.0 these are no longer written --
                # the new service opens logs/app/bot.log instead. Safe to move.
                for name in ("bot.log", "errors.log"):
                    p = LOGS_DIR / name
                    dest = _LOG_APP_DIR / name
                    if p.exists() and p.stat().st_size > 0:
                        if not dest.exists() or dest.stat().st_size == 0:
                            p.rename(dest)
                            moved_count += 1
                            log.info("[LOG_MAINT] Migrated orphaned %s -> app/", name)
                        elif (now.timestamp() - p.stat().st_mtime) > 3600:
                            # dest has data AND root copy hasn't been written to in
                            # over an hour -- it's a true orphan, safe to delete
                            p.unlink()
                            deleted_count += 1
                            log.info("[LOG_MAINT] Removed stale orphan %s from root", name)

                # Move old report_*.txt and analysis_*.txt from root to logs/reports/
                for pattern in ("report_*.txt", "analysis_*.txt"):
                    for p in LOGS_DIR.glob(pattern):
                        dest = _LOG_REPORTS_DIR / p.name
                        if not dest.exists():
                            p.rename(dest)
                            moved_count += 1
                            log.debug("[LOG_MAINT] Migrated %s -> reports/", p.name)

                # Move trades.jsonl from root to logs/trades/ (one-time)
                old_trades = LOGS_DIR / "trades.jsonl"
                new_trades = _LOG_TRADES_DIR / "trades.jsonl"
                if old_trades.exists() and old_trades.stat().st_size > 0:
                    if not new_trades.exists() or new_trades.stat().st_size == 0:
                        old_trades.rename(new_trades)
                        moved_count += 1
                        log.info("[LOG_MAINT] Migrated trades.jsonl -> trades/")
                    else:
                        # Both exist with data -- append old root content to new, then remove
                        with open(old_trades, "rb") as src, open(new_trades, "ab") as dst:
                            _shutil.copyfileobj(src, dst)
                        old_trades.unlink()
                        moved_count += 1
                        log.info("[LOG_MAINT] Merged root trades.jsonl -> trades/ and removed original")

                # ── WAL checkpoint (MAC-DB-005) ───────────────────────────────
                # Prevents unbounded WAL file growth on long-running macOS sessions.
                # Windows NSSM would restart daily; macOS dev machines may run for weeks.
                try:
                    db_conn = sqlite3.connect(
                        str(DATA_DIR / "paper_trades.db"), check_same_thread=False
                    )
                    try:
                        db_conn.execute("PRAGMA wal_checkpoint(RESTART)")
                        db_conn.close()
                        log.debug("[LOG_MAINT] WAL checkpoint complete")
                    except Exception:
                        db_conn.close()
                        raise
                except Exception as wal_exc:
                    log.warning("[LOG_MAINT] WAL checkpoint failed: %s", wal_exc)

                if deleted_count or moved_count:
                    log.info(
                        "[LOG_MAINT] Maintenance complete: %d deleted, %d migrated",
                        deleted_count, moved_count,
                    )
                else:
                    log.debug("[LOG_MAINT] Maintenance pass complete -- nothing to clean")

            except Exception as exc:
                log.warning("[LOG_MAINT] Maintenance pass failed: %s", exc)

            await asyncio.sleep(_MAINT_INTERVAL)

    async def _subreddit_discovery_task(self) -> None:
        """
        Periodically discover new candidate subreddits via Reddit post search.
        Waits 5 minutes on startup so the market cache is warm before the first pass.
        """
        from feeds.subreddit_discovery import run_discovery_pass
        if not cfg.reddit_enabled:
            log.info(
                "[DISCOVERY] Reddit disabled (REDDIT_ENABLED not set) -- subreddit "
                "discovery task exiting cleanly; no Reddit search to perform."
            )
            return
        await asyncio.sleep(300)  # Let market cache warm up
        while True:
            try:
                markets = self.matcher._cache._markets
                if markets:
                    count = await run_discovery_pass(markets, DATA_DIR / "paper_trades.db")
                    if count:
                        log.info("[DISCOVERY] Found %d new candidate subreddits", count)
                else:
                    self._market_cache_empty_discovery_passes += 1
                    elapsed = time.monotonic() - self._startup_started_monotonic
                    log.debug(
                        "[DISCOVERY] Market cache empty, skipping discovery pass "
                        "(count=%d since_startup=%.1fs effective_multi_lane_runtime_started=%s)",
                        self._market_cache_empty_discovery_passes,
                        elapsed,
                        self._market_cache_ready_at is not None,
                    )
            except Exception as exc:
                log.warning("[DISCOVERY] Discovery pass failed: %s", exc)
            from config import SUBREDDIT_DISCOVERY_INTERVAL_SECS
            await asyncio.sleep(SUBREDDIT_DISCOVERY_INTERVAL_SECS)

    def _record_market_cache_ready(self, market_count: int) -> tuple[datetime, float] | None:
        """Record the first non-empty market cache for startup observability."""
        if market_count <= 0 or self._market_cache_ready_at is not None:
            return None
        ready_at = datetime.now(timezone.utc)
        elapsed = time.monotonic() - self._startup_started_monotonic
        self._market_cache_ready_at = ready_at
        self._market_cache_ready_after_secs = elapsed
        return ready_at, elapsed

    # ── Run ───────────────────────────────────────────────────────────────────

    def _make_subreddit_getter(self) -> "Callable[[], Awaitable[list[str]]]":
        """
        Return an async callable that computes the subreddit list from the live
        market cache. Called once per Reddit poll cycle (~300s) to enable
        adaptive selection: topic subreddits are added only when matching markets
        are open. Falls back to REDDIT_CORE_SUBREDDITS if the cache is cold or
        the selector raises.
        """
        from feeds.subreddit_selector import select_subreddits

        async def _get() -> list[str]:
            try:
                markets = self.matcher._cache._markets
                result = select_subreddits(
                    markets,
                    source_stats=self.source_stats,
                    db_path=DATA_DIR / "paper_trades.db",
                )
                log.debug(
                    "Subreddit selector: %d subs for this cycle (%d active markets)",
                    len(result), len(markets),
                )
                return result
            except Exception as exc:
                log.warning("Subreddit selector error, using core list: %s", exc)
                from config import REDDIT_CORE_SUBREDDITS
                return list(REDDIT_CORE_SUBREDDITS)

        return _get

    def _make_market_getter(self) -> "Callable[[], list]":
        """Sync callable returning the live market cache for search/GDELT query generation."""
        def _get() -> list:
            markets = list(self.matcher._cache._markets)
            polymarket_runtime = getattr(self, "polymarket_paper_runtime", None)
            if polymarket_runtime is not None:
                markets.extend(polymarket_runtime.cached_candidate_markets())
            return markets
        return _get

    async def _warm_polymarket_paper_runtime_cache(self) -> int:
        warmed = await TradingBot._refresh_polymarket_paper_runtime_cache(
            self,
            initial=True,
        )
        log.info("[POLYMARKET_PAPER] warm_cache_ready markets=%d", warmed)
        return warmed

    async def _refresh_polymarket_paper_runtime_cache(
        self,
        *,
        initial: bool = False,
    ) -> int:
        polymarket_runtime = getattr(self, "polymarket_paper_runtime", None)
        if polymarket_runtime is None:
            return 0
        warmed = await polymarket_runtime.warm_cache()
        candidate_markets = list(polymarket_runtime.cached_candidate_markets())
        current_tickers = {market.ticker for market in candidate_markets}
        known_tickers = getattr(self, "_known_polymarket_market_tickers", set())
        added: set[str] = set()

        if known_tickers and not initial:
            added = current_tickers - known_tickers
            for market in candidate_markets:
                if market.ticker not in added:
                    continue
                from utils.logger import trade_log as _tl

                await write_trade_log_async(
                    _tl.log_new_market,
                    ticker=market.ticker,
                    title=market.title,
                    series_ticker=market.series_ticker,
                )
                log.info(
                    "[POLYMARKET_NEW_MARKET] %s listed: '%s' -- triggering targeted search",
                    market.ticker,
                    market.title[:60],
                )
                asyncio.create_task(self._trigger_targeted_search(market.ticker))

        last_prices = getattr(self, "_last_polymarket_yes_ask_cents", {})
        if not initial and last_prices:
            now = time.monotonic()
            last_search = getattr(self, "_last_search_triggered", {})
            for market in candidate_markets:
                if market.ticker in added or market.yes_ask_cents is None:
                    continue
                previous_price = last_prices.get(market.ticker)
                if previous_price is None:
                    continue
                move = abs(market.yes_ask_cents - previous_price)
                if move < PRICE_MOVE_THRESHOLD_CENTS:
                    continue
                last_trigger = last_search.get(market.ticker, 0.0)
                if now - last_trigger < PRICE_SEARCH_COOLDOWN_SECS:
                    continue
                last_search[market.ticker] = now
                log.info(
                    "[POLYMARKET_PRICE_MOVE] %s yes_ask %dc -> %dc "
                    "(move=%dc) -- triggering targeted search",
                    market.ticker,
                    previous_price,
                    market.yes_ask_cents,
                    move,
                )
                asyncio.create_task(self._trigger_targeted_search(market.ticker))

        self._last_polymarket_yes_ask_cents = {
            market.ticker: market.yes_ask_cents
            for market in candidate_markets
            if market.yes_ask_cents is not None
        }
        self._known_polymarket_market_tickers = current_tickers
        stats = polymarket_runtime.stats()
        log.info(
            "[POLYMARKET_PAPER] candidate_cache_ready markets=%d "
            "candidate_markets=%d initial=%s",
            stats.market_count,
            len(candidate_markets),
            initial,
        )
        return warmed

    async def _polymarket_market_refresh_task(self) -> None:
        from polymarket.paper_runtime import _DEFAULT_MARKET_CACHE_TTL_SECONDS

        if getattr(self, "polymarket_paper_runtime", None) is None:
            return
        while True:
            await asyncio.sleep(_DEFAULT_MARKET_CACHE_TTL_SECONDS)
            try:
                await self._refresh_polymarket_paper_runtime_cache()
            except Exception as exc:
                log.warning("[POLYMARKET_PAPER] market_refresh_task_error error=%s", exc)

    async def _check_llm_health(self) -> None:
        """Log LLM availability at startup so the operator knows what's active."""
        import aiohttp
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(
                    cfg.ollama_tags_url,
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as r:
                    if r.status == 200:
                        data   = await r.json()
                        models = [m["name"] for m in data.get("models", [])]
                        log.info("LLM: Ollama running | model=%s | available=%s",
                                 cfg.ollama_model, models)
                        if models and cfg.ollama_model not in models:
                            log.warning(
                                "LLM: configured model '%s' not in Ollama available list %s"
                                " -- every inference call will return 404;"
                                " set OLLAMA_MODEL env var or run: ollama pull %s",
                                cfg.ollama_model, models, cfg.ollama_model,
                            )
                        return
        except Exception:
            pass
        if cfg.anthropic_api_key:
            log.info("LLM: Ollama not reachable -- Anthropic fallback active")
        else:
            log.warning("LLM: Ollama not reachable and no ANTHROPIC_API_KEY "
                        "-- keyword scoring only (reduced signal accuracy)")

    async def _run_startup_observability_probe(self) -> None:
        if not cfg.enable_startup_observability_probe:
            return

        from analysis import signal_analyzer as signal_analyzer_module
        from utils.logger import trade_log

        news = NewsItem(
            headline="U.S. and Iran fail to agree on peace deal after marathon talks",
            url="startup-probe://signal-analysis-detail",
            source="startup_probe",
            published=datetime.now(timezone.utc),
            body="Synthetic startup observability probe. Do not trade.",
            item_id="startup-observability-probe",
        )
        market = SimpleNamespace(
            ticker="KXSTARTUP-PROBE",
            title="Will the U.S. and Iran agree to a peace deal this month?",
            subtitle="Synthetic startup observability probe market",
            yes_price=50.0,
            yes_prob=0.50,
            series_ticker="KXSTARTUP",
            close_time="2026-12-31T23:59:59Z",
        )
        match_meta = _compute_pre_llm_match_meta(
            news.headline,
            market.title,
            _startup_probe_matched_tokens(),
        )

        emitted_records: list[dict] = []
        validation_missing: list[str] = []
        original_write = trade_log._write
        original_llm_estimate = signal_analyzer_module.llm_estimate_detailed

        async def _probe_llm(*_args, **_kwargs):
            return (
                (0.38, 0.82, "Startup probe synthetic LLM analysis", "no", "moderate"),
                {
                    "attempted": True,
                    "status": "startup_probe_success",
                    "provider": "startup_probe",
                    "result_used": True,
                },
            )

        def _capture_probe_write(record: dict) -> None:
            if record.get("type") == "SIGNAL_ANALYSIS_DETAIL" and record.get("is_synthetic_probe") is True:
                captured = dict(record)
                emitted_records.append(captured)
                missing = _validate_startup_observability_probe_record(
                    captured,
                    cfg.startup_observability_probe_required_fields,
                )
                if missing:
                    validation_missing[:] = missing
                    log.error(
                        "[STARTUP_PROBE][FAIL] missing SIGNAL_ANALYSIS_DETAIL fields=%s strict=%s ticker=%s method=%s",
                        ",".join(missing),
                        cfg.startup_observability_probe_strict,
                        captured.get("ticker"),
                        captured.get("method"),
                    )
                    if cfg.startup_observability_probe_strict:
                        raise RuntimeError(
                            "Startup observability probe missing required fields: "
                            + ", ".join(missing)
                        )
            original_write(record)

        signal_analyzer_module.llm_estimate_detailed = _probe_llm
        trade_log._write = _capture_probe_write
        try:
            await estimate_probability(
                news,
                market,
                keyword_stats=self.keyword_stats,
                match_meta=match_meta,
                is_startup_probe=True,
            )
        finally:
            trade_log._write = original_write
            signal_analyzer_module.llm_estimate_detailed = original_llm_estimate

        if len(emitted_records) != 1:
            message = (
                "Startup observability probe expected exactly 1 SIGNAL_ANALYSIS_DETAIL "
                f"record, got {len(emitted_records)}"
            )
            log.error(
                "[STARTUP_PROBE][FAIL] %s strict=%s",
                message,
                cfg.startup_observability_probe_strict,
            )
            if cfg.startup_observability_probe_strict:
                raise RuntimeError(message)
            return

        record = emitted_records[0]
        if validation_missing:
            return

        log.info(
            "[STARTUP_PROBE][PASS] SIGNAL_ANALYSIS_DETAIL verified fields=%s method=%s ticker=%s source=%s",
            ",".join(cfg.startup_observability_probe_required_fields),
            record.get("method"),
            record.get("ticker"),
            record.get("source"),
        )

    async def _trading_queue_consumer_task(self) -> None:
        """Drain approved TradeCandidate objects and forward them to the executor."""
        while True:
            candidate = await self._trading_queue.get()
            try:
                trade_id = await self.executor.execute(candidate)
                if trade_id:
                    self.source_stats.increment_trades(
                        candidate.fast_lane_analysis.news_item.source
                    )
                if self.executor._venue_value(candidate.market) == "kalshi":
                    self.ws.watch([candidate.market.ticker])
            except Exception:
                log.exception(
                    "[BLEND] consumer_error ticker=%s", candidate.market.ticker
                )
            finally:
                self._trading_queue.task_done()

    async def _structural_recompute_task(self) -> None:
        """Periodically recompute structural priors for all active markets."""
        while not self.matcher._cache._markets:
            await asyncio.sleep(0.25)
        log.info(
            "[STRUCTURAL] Market cache ready: starting recompute loop with %d markets",
            len(self.matcher._cache._markets),
        )
        while True:
            # Guaranteed yield point: prevents a hot spin if run_periodic ever
            # returns synchronously (e.g. under a mock). Zero-delay in production.
            await asyncio.sleep(0)
            try:
                await self._structural_task.run_periodic(
                    market_provider=lambda: list(self.matcher._cache._markets),
                    interval_seconds=3600,
                )
            except Exception as exc:
                log.warning(
                    "[STRUCTURAL] run_periodic raised unexpectedly; retrying in 60s: %s", exc
                )
                await asyncio.sleep(60)

    async def run(self) -> None:
        notional = self.paper.get_notional_bankroll()
        max_bet  = cfg.dynamic_max_bet(notional)

        emit_startup_banner(VERSION, cfg.ollama_model, cfg.kalshi_env)
        log.info("=" * 60)
        log.info("Kalshi Trading Bot v%s starting", VERSION)
        log.info("Mode:             %s", "PAPER TRADING" if cfg.is_paper_trading else "LIVE TRADING")
        log.info(cfg.polymarket_us_startup_status())
        if cfg.polymarket_us_enabled:
            from polymarket.startup_probe import log_polymarket_startup_probe

            await asyncio.to_thread(log_polymarket_startup_probe)
        _log_bankroll_summary(notional)
        log.info("Max bet (dynamic): $%.2f  (%.0f%% of bankroll, hard cap $%.2f)",
                 max_bet, cfg.max_bet_pct_bankroll * 100, cfg.max_bet_hard_cap)
        log.info("Kelly fraction:   %.0f%%", cfg.kelly_fraction * 100)
        if cfg.is_paper_trading:
            log.info("Paper trading -- run `python main.py --go-live` when ready to switch.")
            log.info(
                "Go-live gates: min_resolved=%d, min_win_rate=%.0f%%, max_drawdown=%.0f%%",
                cfg.go_live_min_resolved,
                cfg.go_live_min_win_rate * 100,
                cfg.go_live_max_drawdown_pct * 100,
            )
        log.info("=" * 60)

        try:
            balance = self.rest.get_balance()
            log.info("Kalshi account balance: $%.2f", balance)
        except Exception as exc:
            log.warning("Could not fetch Kalshi balance: %s", exc)
        if cfg.polymarket_us_enabled:
            await asyncio.to_thread(_log_polymarket_account_summary)

        await self._check_llm_health()
        await self._run_startup_observability_probe()
        await self._warm_polymarket_paper_runtime_cache()

        tasks = [
            asyncio.create_task(run_rss_monitor(self._enqueue_news),    name="rss"),
            asyncio.create_task(
                run_reddit_monitor(self._enqueue_news, subreddits=self._make_subreddit_getter()),
                name="reddit",
            ),
            asyncio.create_task(
                run_search_news_monitor(
                    self._enqueue_news,
                    self._make_market_getter(),
                    queue_depth_fn=lambda: (
                        self._news_queue.qsize() / self._news_queue.maxsize
                    ),
                    get_series_metadata=self.matcher._cache.get_series_metadata_snapshot,
                ),
                name="search",
            ),
            asyncio.create_task(
                run_gdelt_monitor(self._enqueue_news, self._make_market_getter()),
                name="gdelt",
            ),
            asyncio.create_task(self._news_consumer_task(),             name="news_consumer"),
            asyncio.create_task(self.ws.run(),                          name="websocket"),
            asyncio.create_task(self._warm_ws_subscriptions(),          name="ws_warm"),
            asyncio.create_task(self._market_refresh_task(),            name="market_refresh"),
            asyncio.create_task(
                self._polymarket_market_refresh_task(),
                name="polymarket_market_refresh",
            ),
            asyncio.create_task(self._auto_resolve_task(),              name="auto_resolve"),
            asyncio.create_task(self._subreddit_discovery_task(),       name="sub_discovery"),
            asyncio.create_task(self._log_rotation_task(),              name="log_rotation"),
            asyncio.create_task(self._log_maintenance_task(),           name="log_maint"),
            asyncio.create_task(
                run_runtime_overrides_poll(
                    self._runtime_overrides_reader,
                    interval_secs=600.0,  # 10 minutes
                ),
                name="runtime_overrides_poll",
            ),
            asyncio.create_task(
                self._accumulation_task.run(self._evidence_queue),      name="accumulation",
            ),
            asyncio.create_task(self._trading_queue_consumer_task(),    name="blend_consumer"),
            asyncio.create_task(self._structural_recompute_task(),      name="structural"),
            *([asyncio.create_task(
                run_rss_monitor(self._on_fade_tweet, feeds=FADE_TWEET_FEED_URLS),
                name="fade_tweets",
            )] if FADE_TWEET_FEED_URLS else []),
        ]
        research_prewarm_task = self._create_research_prewarm_runtime_task()
        if research_prewarm_task is not None:
            tasks.append(research_prewarm_task)

        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            log.info("Bot shutting down...")
        finally:
            for task in tasks:
                task.cancel()
            await self.cancel_targeted_research_prewarm_tasks()
            self.ws.stop()

    def stop(self) -> None:
        self.ws.stop()


# ── CLI handlers ──────────────────────────────────────────────────────────────

async def async_main() -> None:
    args = parse_args()
    startup_context = "cli" if _is_cli_only_command(args) else "runtime"
    _log_boot_summary(startup_context)

    if startup_context == "cli":
        if args.rotate_logs:
            rotated = rotate_logs()
            log.info("[BOOT] manual_log_rotation_complete=true files=%s", ", ".join(str(path) for path in rotated))
            return
        paper = PaperTrader(startup_context="cli")
        if args.report:
            print(paper.generate_report())
            return
        if args.credibility:
            print("\nSOURCE CREDIBILITY TABLE")
            print(paper.credibility.format_table())
            return
        if args.resolve:
            ticker, result_str = args.resolve
            resolved_yes = result_str.upper() == "YES"
            # resolve_market is async as of v0.29.47 (PROFIT-CAL-001).
            asyncio.run(paper.resolve_market(ticker, resolved_yes))
            log.info("Resolved %s as %s", ticker, "YES" if resolved_yes else "NO")
            return
        if args.go_live:
            _handle_go_live(paper)
            return

    runtime_guard = _RuntimeInstanceGuard(_BOT_RUNTIME_LOCK)
    if not runtime_guard.acquire():
        log.warning(
            "[BOOT] duplicate_runtime_blocked=true lock=%s owner=%s",
            _BOT_RUNTIME_LOCK,
            runtime_guard.describe_owner(),
        )
        raise SystemExit(1)

    try:
        bot = TradingBot()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, lambda: asyncio.create_task(_shutdown(bot)))
            except (NotImplementedError, RuntimeError):
                pass

        await bot.run()
    finally:
        runtime_guard.release()


def _paper_open_exposure_drawdown_pct(paper: PaperTrader) -> float:
    """Return conservative MTM drawdown for pre-entry readiness gating."""
    if cfg.bankroll <= 0:
        return 1.0

    from scripts.mark_open_positions import compute_open_position_marks

    try:
        marks = compute_open_position_marks(paper.db_path)
        notional = paper.get_notional_bankroll()
        marked_value = None if marks is None else marks.get("marked_value", 0.0)
        marked_value = float(marked_value)
    except Exception as exc:
        log.warning(
            "Open exposure drawdown unavailable; readiness will fail closed: %s",
            exc,
        )
        return 1.0

    if not math.isfinite(notional) or not math.isfinite(marked_value):
        return 1.0

    mtm_equity = notional + marked_value
    return max(0.0, (cfg.bankroll - mtm_equity) / cfg.bankroll)


def _check_go_live_gates(paper: PaperTrader) -> list[str]:
    """
    Evaluate config-driven go-live readiness gates.

    Returns a list of failure reasons (empty = all gates passed).
    Gates are set in .env and logged at startup:
      GO_LIVE_MIN_RESOLVED    -- minimum resolved trades (default 20)
      GO_LIVE_MIN_WIN_RATE    -- minimum win rate (default 0.52)
      GO_LIVE_MAX_DRAWDOWN_PCT -- max drawdown as fraction of starting bankroll (default 0.20)
    """
    trades   = paper.get_all_trades()
    resolved = [t for t in trades if t["resolved"]]
    notional = paper.get_notional_bankroll()
    failures = []

    n_resolved = len(resolved)
    if n_resolved < cfg.go_live_min_resolved:
        failures.append(
            f"Resolved trades: {n_resolved} < minimum {cfg.go_live_min_resolved}"
        )

    if resolved:
        wins     = sum(1 for t in resolved if (t["pnl_dollars"] or 0) > 0)
        win_rate = wins / n_resolved
        if win_rate < cfg.go_live_min_win_rate:
            failures.append(
                f"Win rate: {win_rate:.1%} < minimum {cfg.go_live_min_win_rate:.1%}"
            )

    # P4 (PROFIT-DRAWDOWN-001c reconcile): gate drawdown on MARK-TO-MARKET
    # equity, the SAME basis the authoritative section-8 daily report uses, not
    # raw notional free-cash. The paper bankroll model deducts each open trade's
    # FULL entry cost at placement, so notional understates true equity whenever
    # open positions retain value and inflates the drawdown number (~61.6% raw
    # vs ~25.9% MTM at the 2026-06 peak-to-trough). Two drawdown numbers for one
    # live-cutover decision is a latent hazard at the 20% boundary; this pins the
    # gate onto compute_open_position_marks (delegate, never re-derive).
    #
    # MTM equity = notional + marked_value of open positions. Unpriced positions
    # already count as $0 inside marked_value (compute_open_position_marks
    # excludes them), so lower equity -> higher drawdown -> more likely to FAIL
    # -- the safe direction for go-live, exactly as the report does.
    #
    # Fail-closed on error: if marking raises, returns None (DB missing), or
    # yields a non-finite marked_value, the gate FAILS. The report falls back to
    # notional offline (conservative for a *report*), but the live-cutover GATE
    # must never PASS when it cannot establish MTM equity.
    from scripts.mark_open_positions import compute_open_position_marks

    try:
        marks = compute_open_position_marks()
    except Exception as exc:  # live REST/Polymarket surface
        marks = None
        mark_error = str(exc)[:80]
    else:
        mark_error = None

    if marks is None:
        failures.append(
            "Drawdown: mark-to-market equity unavailable "
            f"({mark_error or 'no position DB'}) -- gate fails closed"
        )
    else:
        marked_value = marks.get("marked_value", 0.0)
        try:
            marked_value = float(marked_value)
        except (TypeError, ValueError):
            marked_value = None
        if marked_value is None or not math.isfinite(marked_value):
            failures.append(
                "Drawdown: mark-to-market value is non-finite -- gate fails closed"
            )
        else:
            mtm_equity = notional + marked_value
            drawdown_pct = (
                (cfg.bankroll - mtm_equity) / cfg.bankroll if cfg.bankroll > 0 else 0.0
            )
            if drawdown_pct > cfg.go_live_max_drawdown_pct:
                failures.append(
                    f"Drawdown: {drawdown_pct:.1%} > maximum {cfg.go_live_max_drawdown_pct:.1%}"
                )

    return failures


def _handle_go_live(paper: PaperTrader) -> None:
    """
    Interactive go-live confirmation gate.

    Checks config-driven readiness gates first, then prints the full report
    and requires the user to type CONFIRM to proceed.
    This is the ONLY way to switch the bot to live trading.

    Requires LIVE_TRADING_ENABLED=true in .env -- a hard kill-switch that prevents
    accidental live activation. Policy: do not enable until Mac Studio is in place.
    """
    if not cfg.live_trading_enabled:
        print("=" * 60)
        print("  LIVE TRADING BLOCKED")
        print("=" * 60)
        print()
        print("  LIVE_TRADING_ENABLED is not set to 'true' in .env.")
        print()
        print("  Policy: no live trading until Mac Studio hardware is in place,")
        print("  or until an explicit request is made with stated goals and costs.")
        print()
        print("  To unlock: set LIVE_TRADING_ENABLED=true in .env, then re-run")
        print("  python main.py --go-live")
        print()
        print("=" * 60)
        return
    print(paper.generate_report())
    print()
    print("=" * 60)
    print("  GO-LIVE READINESS GATES")
    print("=" * 60)
    gate_failures = _check_go_live_gates(paper)
    if gate_failures:
        print("  GATES FAILED -- resolve these before going live:")
        for f in gate_failures:
            print(f"    [FAIL] {f}")
        print()
        print("  Override gates with GO_LIVE_MIN_RESOLVED=0 etc. in .env if intentional.")
        print("=" * 60)
    else:
        print("  All readiness gates passed.")
        print("=" * 60)
    print()
    print("=" * 60)
    print("  WARNING: You are about to switch to LIVE TRADING.")
    print("  Real money will be at risk. Review the report above.")
    print("=" * 60)
    notional = paper.get_notional_bankroll()
    print(f"  Notional bankroll at switch: ${notional:.2f}")
    print(f"  Live max bet: ${cfg.dynamic_max_bet(notional):.2f}")
    if gate_failures:
        print()
        print("  NOTE: Readiness gates have FAILED. Proceeding anyway is your choice.")
    print()
    answer = input("  Type CONFIRM to go live, or anything else to cancel: ").strip()
    if answer == "CONFIRM":
        paper.confirm_go_live()
        print("Live trading confirmed. Restart the bot with `python main.py` to begin.")
    else:
        print("Cancelled -- still in paper trading mode.")


async def _shutdown(bot: TradingBot) -> None:
    log.info("Shutdown signal received")
    bot.stop()
    await bot.cancel_targeted_research_prewarm_tasks()
    try:
        report = bot.paper.generate_report()
        log.info("\n%s", report)
    except Exception as exc:
        log.warning("Report generation failed: %s", exc)
    for task in asyncio.all_tasks():
        if task is not asyncio.current_task():
            task.cancel()


if __name__ == "__main__":
    try:
        asyncio.run(async_main())
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
