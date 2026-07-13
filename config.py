"""
Central configuration for the Kalshi trading bot.

All tuneable parameters and environment-variable bindings live here.
Import `cfg` from this module everywhere instead of reading env vars directly.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from utils.output_paths import DB_STATE_DIR, OUTPUT_ROOT

load_dotenv(Path(__file__).parent / ".env")

# ── Directories ───────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
# KALSHI_OUTPUT_ROOT is the canonical output root. KALSHI_LOG_ROOT remains a
# compatibility alias handled in utils.output_paths.
LOGS_DIR = OUTPUT_ROOT
DATA_DIR = DB_STATE_DIR
LOGS_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(exist_ok=True)

# ── Version ───────────────────────────────────────────────────────────────────
VERSION = (BASE_DIR / "VERSION").read_text(encoding="utf-8").strip()


def _parse_float_band_pairs(value: str, *, default: list[tuple[float, float]]) -> list[tuple[float, float]]:
    text = str(value or "").strip()
    if not text:
        return list(default)
    bands: list[tuple[float, float]] = []
    for raw_part in text.split(","):
        part = raw_part.strip()
        if not part:
            continue
        if "-" not in part:
            raise ValueError(f"invalid band '{part}' (expected low-high)")
        low_text, high_text = part.split("-", 1)
        low = float(low_text.strip())
        high = float(high_text.strip())
        bands.append((low, high))
    return bands


def _resolve_keyword_override_mode() -> str:
    """Resolve the pre-LLM keyword override mode from env, defaulting to `all_required`.

    Precedence:
    1. `PRE_LLM_MATCH_GATE_KEYWORD_OVERRIDE_MODE` if set (primary env var).
    2. Legacy `PRE_LLM_MATCH_GATE_KEYWORD_OVERRIDE_ANY_HIT` — explicit "false/0/off"
       resolves to `disabled`; explicit "true/1/on" resolves to `any_hit`
       (preserves prior behavior for operators who set that flag explicitly).
    3. Default: `all_required` (ROADMAP P2.1, 2026-04-22).
    """
    mode = os.getenv("PRE_LLM_MATCH_GATE_KEYWORD_OVERRIDE_MODE", "").strip().lower()
    if mode:
        return mode
    legacy = os.getenv("PRE_LLM_MATCH_GATE_KEYWORD_OVERRIDE_ANY_HIT")
    if legacy is not None:
        legacy_norm = legacy.strip().lower()
        if legacy_norm in {"0", "false", "no", "off"}:
            return "disabled"
        if legacy_norm in {"1", "true", "yes", "on"}:
            return "any_hit"
    return "all_required"


def _resolve_market_source_hints_mode() -> str:
    """Resolve default-off MarketSourceHints diagnostics mode from env."""
    return os.getenv("MARKET_SOURCE_HINTS_MODE", "off").strip().lower() or "off"


def _parse_bool_env(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _parse_string_tuple(value: str | None, *, default: tuple[str, ...]) -> tuple[str, ...]:
    text = str(value or "").strip()
    if not text:
        return tuple(default)
    return tuple(part.strip() for part in text.split(",") if part.strip())

# ── Kalshi REST base URLs ─────────────────────────────────────────────────────
# Production URL per official OpenAPI spec (api.elections.kalshi.com)
KALSHI_PROD_REST = "https://api.elections.kalshi.com/trade-api/v2"
KALSHI_DEMO_REST = "https://demo-api.kalshi.co/trade-api/v2"
KALSHI_PROD_WS = "wss://api.elections.kalshi.com/trade-api/ws/v2"
KALSHI_DEMO_WS = "wss://demo-api.kalshi.co/trade-api/ws/v2"

# ── RSS feeds to monitor ──────────────────────────────────────────────────────
RSS_FEEDS = [
    # Al Jazeera
    "https://www.aljazeera.com/xml/rss/all.xml",
    # The Guardian
    "https://www.theguardian.com/world/rss",
    "https://www.theguardian.com/world/ukraine/rss",
    "https://www.theguardian.com/world/middleeast/rss",
    # Politico (source string = "Politics"; per-source 1800s override below)
    "https://rss.politico.com/politics-news.xml",
    # NY Times World
    "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
    #
    # ─── B1 direct-to-market-set additions (2026-04-23, URLs live-probed) ───
    # Ukraine coverage (KXUKR, KXRUSSIA):
    "https://kyivindependent.com/news-archive/rss/",      # feed.title = "The Kyiv Independent"
    "https://www.kyivpost.com/feed",                      # feed.title = "Kyiv Post"
    # Middle East / Iran coverage (KXTRUMPIRAN, KXMIDEAST, KXIRAN):
    "https://www.iranintl.com/en/feed",                   # feed.title = "Iran International"
    "https://www.timesofisrael.com/feed/",                # feed.title = "The Times of Israel"
    # US Government direct (Trump markets, executive / military actions):
    "https://www.defense.gov/DesktopModules/ArticleCS/RSS.ashx?max=10&ContentType=1&Site=945",  # "Department of War News Feed"
    "https://www.whitehouse.gov/news/feed",               # "News – The White House" (note en-dash)
    #
    # ─── B2 institutional + industry additions (2026-04-23, URLs live-probed) ───
    # Intergovernmental / institutional:
    "https://news.un.org/feed/subscribe/en/news/all/rss.xml",                            # "UN News - Global perspective Human stories"
    "https://ec.europa.eu/commission/presscorner/api/rss?language=en",                   # "Press releases - RSS"  (European Commission)
    "https://www.iaea.org/feeds/news",                                                   # "Top Stories From the International Atomic Energy Agency"
    # Foreign MFA (slow-cadence but authoritative):
    #   https://mid.ru/en/rss/  -- removed 2026-04-24: F5/Shape TSPD bot-protection
    #       shield. Default curl/feedparser/python-urllib UAs get TCP reset at the
    #       proxy; browser-typed UAs get a JS-challenge HTML page (bobcmn/TSPD_101_DID)
    #       instead of RSS content. Unreachable without executing the challenge in a
    #       real browser engine — out of scope for an RSS client.
    # Defense industry press (break stories ahead of mainstream wires):
    "https://www.defensenews.com/arc/outboundfeeds/rss/?outputType=xml",                 # "Defense News"
    "https://breakingdefense.com/feed/",                                                 # "Breaking Defense"
    #
    # ─── B3 Tier 2 analytical + re-enables (2026-04-23, URLs live-probed) ───
    # Tier 2: OSINT investigative (slow cadence, high quality):
    "https://www.bellingcat.com/feed/",                                                  # "bellingcat" (lowercase in feed.title)
    # Re-enables from DISABLED_NEWS_SOURCES (previously paused; feed URLs confirmed live):
    "https://feeds.bbci.co.uk/news/world/rss.xml",                                       # "BBC News"
    "https://www.france24.com/en/rss",                                                   # "France 24 - International breaking news, top stories and headlines"
    "https://rss.dw.com/rdf/rss-en-world",                                               # "World | Deutsche Welle"
    #
    # Removed 2026-04-23 as pipeline-hygiene cleanup (direct HTTP probes confirmed dead):
    #   Reuters (feeds.reuters.com/reuters/{worldNews,topNews}) -- connection fails;
    #       Reuters discontinued public RSS in June 2020
    #   AP News (feeds.apnews.com/rss/apf-{topnews,intlnews})  -- connection fails;
    #       AP has no official public RSS endpoint
    #   Radio Free Europe (www.rferl.org/api/zyqopjmxel)       -- HTTP 404
    # For Reuters/AP coverage, re-evaluate re-enabling feeds/search_news_monitor.py
    # (google_news_query family, currently in DISABLED_SOURCE_FAMILIES) or routing
    # via RSSHub / RSS.app. Do not resurrect the dead direct URLs.
    #
    # Still disabled via DISABLED_NEWS_SOURCES (NPR, Foreign Policy, Defense One).
    # BBC / France 24 / Deutsche Welle were re-enabled in B3 (2026-04-23) after
    # URL probe confirmed fresh content.
    #
    # ─── B3.1 high-yield publisher-desk additions (2026-05-30, URLs live-probed) ───
    # Track B / PROFIT-EDGE-004: all 13 OPPORTUNITY-generating series are US-political/
    # geopolitical, but NYT and the Guardian were configured World-desk-only and The
    # Hill (the canonical Congress / endorsement / cabinet beat) was absent. These add
    # the missing US-politics desks of the top opportunity publishers (NYT is the #1
    # OPPORTUNITY source; Guardian desks are the largest single source). Each also gets
    # a 1800s EARLY_MAX_NEWS_AGE_BY_SOURCE override below (publisher RSS lags past the
    # 300s default). WaPo deferred: its generic "Politics"/"National" feed titles would
    # collide with the Politico source label -- needs a per-feed source-name override.
    "https://rss.nytimes.com/services/xml/rss/nyt/Politics.xml",  # feed.title = "NYT > U.S. > Politics"
    "https://rss.nytimes.com/services/xml/rss/nyt/US.xml",        # feed.title = "NYT > U.S. News"
    "https://thehill.com/news/feed/",                             # feed.title = "Just In News" (The Hill)
    "https://thehill.com/homenews/senate/feed/",                  # feed.title = "Senate News" (The Hill)
    "https://www.theguardian.com/us-news/rss",                    # feed.title = "US news | The Guardian"
]
RSS_POLL_INTERVAL_SECONDS = 60

# Maximum age of a news item when the consumer processes it. Items older than
# this were either slow through the queue or stale when they arrived — skip
# them to avoid trading on already-priced-in information.
MAX_NEWS_AGE_SECONDS: int = int(os.getenv("MAX_NEWS_AGE_SECONDS", "300"))  # 5 min

# Early age pre-filter applied before news enters the deeper queue/matching
# path. This is an optimization only; _process_candidate() still enforces the
# authoritative stale-news guard with MAX_NEWS_AGE_SECONDS.
EARLY_MAX_NEWS_AGE_SECONDS: int = int(
    os.getenv("EARLY_MAX_NEWS_AGE_SECONDS", "1800")
)  # 30 min default — PROFIT-STALE-002 (2026-05-24): raised from 300s to
   # match the per-source-override value (1800s) for all curated publishers.
   # The 7-day funnel showed 681 items at 5-15m age killed because their
   # source string had no explicit override and fell to the old 300s default.
   # The existing per-source entries below remain as documentation of intent
   # but no longer need to be exhaustive — new publishers (Washington Post,
   # Bloomberg, The Hill, South China Morning Post, NYT direct via Google
   # News, BBC short form, France 24 short form, CNBC, Times of India)
   # benefit automatically. Lower-trust aggregator sources (MSN, Forbes,
   # Reddit) also receive the longer window; LLM-side filtering and the
   # downstream source_class / source_multiplier guards continue to suppress
   # spurious signals. Cost analysis: LLM is local (Ollama qwen3), so the
   # extra throughput is wallclock, not $$.

# Curated per-source overrides for the early freshness pre-filter.
# Exact source strings are preferred for predictability; main.py also does a
# simple case-insensitive fallback match for readability.
EARLY_MAX_NEWS_AGE_BY_SOURCE: dict[str, int] = {
    "NYT > World News": 1800,
    "World news | The Guardian": 1800,
    "Al Jazeera \u2013 Breaking News, World News and Video from Al Jazeera": 1800,
    "Middle East and north Africa | The Guardian": 1800,
    "Ukraine | The Guardian": 1800,
    # Politico RSS; feed.title = "Politics". Re-enabled 2026-04-23 from
    # DISABLED_NEWS_SOURCES. Live probe showed newest items 3.5h old -- at the
    # default 300s threshold virtually nothing would pass. Using 1800s for
    # parity with other publisher-RSS overrides; measure-and-adjust post-soak.
    "Politics": 1800,
    # ─── B1 direct-to-market-set (2026-04-23) ───
    "The Kyiv Independent": 1800,
    "Kyiv Post": 1800,
    "Iran International": 1800,
    "The Times of Israel": 1800,
    "Department of War News Feed": 1800,
    "News – The White House": 1800,  # en-dash (U+2013) in feed.title
    # ─── B2 institutional + industry (2026-04-23) ───
    "UN News - Global perspective Human stories": 1800,
    "Press releases - RSS": 1800,  # generic title; from European Commission press corner
    "Top Stories From the International Atomic Energy Agency": 1800,
    "Defense News": 1800,
    "Breaking Defense": 1800,
    # ─── B3 Tier 2 + re-enables (2026-04-23) ───
    "bellingcat": 1800,  # lowercase in feed.title
    "BBC News": 1800,
    "France 24 - International breaking news, top stories and headlines": 1800,
    "World | Deutsche Welle": 1800,
    # ─── B3.1 high-yield publisher desks (2026-05-30; titles live-probed) ───
    "NYT > U.S. > Politics": 1800,
    "NYT > U.S. News": 1800,
    "Just In News": 1800,        # The Hill -- "Just In" news desk
    "Senate News": 1800,         # The Hill -- Senate desk
    "US news | The Guardian": 1800,
}

# Per-source queue priority overrides. Lower number = processed first.
# Tier 1: wire services -- fastest, break news earliest, highest signal density.
# Tier 2: quality editorial / regional RSS (default for any unlisted RSS source).
# Tier 3: Reddit -- community discussion, slowest to arrive, lowest signal density.
#
# Keys must match the exact source string on NewsItem (comes from RSS channel title).
# Add new tier-1 sources here as their exact strings are confirmed from logs.
# NOTE: AP feeds are configured in RSS_FEEDS but their source string is unconfirmed
# (never observed in trades.jsonl). Add when string is confirmed from live logs.
SOURCE_PRIORITY_TIERS: dict[str, int] = {
    "Reuters": 1,  # confirmed source string
    "BBC News": 1,  # confirmed source string; re-enabled 2026-04-23 in B3
}

# When enabled, candidates that are almost certainly spurious matches are silently
# dropped before reaching LLM analysis. Off by default -- enable after reviewing
# MATCH_SUPPRESSED events in trades.jsonl to confirm suppression is accurate.
# Suppression criteria (all three must hold):
#   - low_match_quality flag is set (at least one heuristic flag present)
#   - heuristic_flags includes minimal_overlap OR single_named_entity_only
#   - heuristic_flags includes near_threshold_score (score within 0.02 of min_score)
ENABLE_LOW_QUALITY_MATCH_SUPPRESSION: bool = os.getenv(
    "ENABLE_LOW_QUALITY_MATCH_SUPPRESSION", "true"
).strip().lower() in {"1", "true", "yes", "on"}

# When enabled, logs a MATCH_SUPPRESSION_CANDIDATE event for every match that meets
# suppression criteria, regardless of whether suppression itself is on. Use this to
# inspect would-be suppressions before enabling ENABLE_LOW_QUALITY_MATCH_SUPPRESSION.
# Safe to leave on during validation -- fires only at DEBUG level into trades.jsonl.
ENABLE_MATCH_SUPPRESSION_DEBUG: bool = os.getenv(
    "ENABLE_MATCH_SUPPRESSION_DEBUG", "false"
).strip().lower() in {"1", "true", "yes", "on"}

# If a feed item arrives without a valid timestamp, drop it before enqueueing
# rather than letting it into the deeper analysis path.
EARLY_DROP_IF_NO_TIMESTAMP: bool = os.getenv(
    "EARLY_DROP_IF_NO_TIMESTAMP", "true"
).strip().lower() in {"1", "true", "yes", "on"}

# Sources disabled at intake based on operator review of stale-heavy / low-value
# behavior. Exact source strings are preferred; main.py also applies a simple
# case-insensitive exact-match fallback.
DISABLED_NEWS_SOURCES: set[str] = {
    # Re-enabled 2026-04-23 in B3 source-integration batch (URLs probed live,
    # added to RSS_FEEDS with 1800s EARLY_MAX_NEWS_AGE_BY_SOURCE overrides):
    #   "BBC News"
    #   "France 24 - International breaking news, top stories and headlines"
    #   "World | Deutsche Welle"
    # Politico ("Politics") re-enabled 2026-04-23 in the pipeline-hygiene fix.
    # Remaining disabled:
    "Foreign Policy",
    "Defense One - All Content",
    "GDELT",
    "r/worldnews",
    "r/ukraine",
    "r/europe",
    "r/politics",
    "r/InternationalNews",
    "r/economics",
    "r/iran",
    "r/worldpolitics",
    "r/IRstudies",
    "r/PoliticalDiscussion",
    "r/WarCollege",
    "r/geopolitics",
    "r/CredibleDefense",
    # Added 2026-04-24 (P1.5.3) after P1.5.2 audit showed these 10 subs
    # have produced zero analysis rows across the full trade-log archive
    # (commit 7117620, scripts/reddit_source_audit.py). Ingestion volume
    # is entirely in the `all_stale` or `no_matches` bands -- either the
    # 5-min EARLY_MAX_NEWS_AGE gate kills every post, or the handful of
    # fresh posts don't overlap any active market. Disable to reclaim
    # network and Reddit rate-limit budget for the remaining subs until
    # a separate fix (threshold retune / keyword coverage) justifies
    # re-enabling.
    "r/Africa",
    "r/China",
    "r/EasternEurope",
    "r/GlobalTalk",
    "r/Israel",
    "r/NorthKorea",
    "r/Syria",
    "r/Turkey",
    "r/pakistan",
    "r/taiwan",
    "NPR Topics: World",
}

DISABLED_SOURCE_FAMILIES: set[str] = {
    "bing_news_query",
    # "google_news_query" re-enabled 2026-04-23 after:
    #   (a) live-probe confirmed Google News RSS returns 100 items/query with
    #       fresh content (0.19h newest on "ukraine russia ceasefire"; sources
    #       include Reuters, AP News, BBC, NYT, Kyiv Independent, Al Jazeera);
    #   (b) v0.29.43 added per-entry publisher attribution so items are
    #       attributed to their real publisher rather than the query string;
    #   (c) queue-overflow protections from v0.12.0 (25 queries/cycle cap,
    #       per-query article cap, AIMD on queue depth) remain in place.
    # Bing News stays disabled: probe showed 9-12 items/query, no per-entry
    # source attribution, 279h-stale newest item on a hot topic. Not worth
    # the polling cost. Re-evaluate if Bing News RSS improves.
}

# Optional RSSHub feed for @Kalshi tweets — enables "fade the Kalshi tweet" signal.
# Leave empty (default) to disable. Set to https://rsshub.app/twitter/user/Kalshi
# or a self-hosted RSSHub instance URL before going live with this signal.
# Comma-separated RSSHub feed URLs for the fade signal.
# Backward-compatible: old KALSHI_TWEET_FEED_URL env var is still read as fallback.
_fade_raw = os.getenv("FADE_TWEET_FEED_URLS", os.getenv("KALSHI_TWEET_FEED_URL", ""))
FADE_TWEET_FEED_URLS: list[str] = [u.strip() for u in _fade_raw.split(",") if u.strip()]

# Price-based fade thresholds (cents, 0-100).
# A market crossing above HIGH → buy NO (fade the spike).
# A market crossing below LOW  → buy YES (fade the collapse).
FADE_PRICE_HIGH_THRESHOLD: int = int(os.getenv("FADE_PRICE_HIGH_THRESHOLD", "85"))
FADE_PRICE_LOW_THRESHOLD: int = int(os.getenv("FADE_PRICE_LOW_THRESHOLD", "15"))

# ── Reddit subreddits to monitor ──────────────────────────────────────────────
REDDIT_SUBREDDITS = [
    # Core geopolitical / world news
    "worldnews",
    "geopolitics",
    "InternationalNews",
    "GlobalTalk",
    "worldpolitics",
    # Regional / conflict-specific
    "ukraine",
    "MiddleEast",
    "China",
    "iran",
    "europe",
    "NATO",
    "NorthKorea",
    "Israel",
    "pakistan",
    "southasia",
    "EasternEurope",
    "taiwan",
    "Turkey",
    "Syria",
    "Africa",
    "LatinAmerica",
    "Eurasia",
    # Analysis & defense
    "CredibleDefense",
    "ArmedConflicts",
    "WarCollege",
    "IRstudies",
    "GlobalAffairs",
    "sanctions",
    "foreignpolicy",
    "military",
]
REDDIT_MIN_SCORE = 50

# ── Adaptive subreddit selection ──────────────────────────────────────────────
# Tier-1: always polled every cycle regardless of active markets
REDDIT_CORE_SUBREDDITS: list[str] = [
    "worldnews",
    "geopolitics",
    "InternationalNews",
    "CredibleDefense",
    "ArmedConflicts",
]

# Tier-2: topic -> subreddits (ordered by signal quality within each topic)
REDDIT_SUBREDDIT_TOPIC_MAP: dict[str, list[str]] = {
    "military_conflict": ["WarCollege", "GlobalAffairs", "ukraine", "NATO"],
    "elections": ["worldpolitics", "GlobalTalk", "IRstudies"],
    "trade_economic": ["economics", "worldpolitics", "GlobalAffairs"],
    "nuclear": ["NorthKorea", "iran", "WarCollege"],
    "sanctions": ["sanctions", "foreignpolicy", "IRstudies"],
    "us_domestic": ["politics", "PoliticalDiscussion"],
    "regional_europe": ["europe", "EasternEurope", "ukraine"],
    "regional_mideast": ["MiddleEast", "Israel", "iran", "Syria"],
    "regional_asia": ["China", "taiwan", "NorthKorea", "southasia", "pakistan"],
    "regional_latam": ["LatinAmerica"],
    "regional_africa": ["Africa"],
    "regional_eurasia": ["Eurasia", "Turkey"],
}

# Per-topic keyword sets used to match active Kalshi market titles
REDDIT_TOPIC_KEYWORDS: dict[str, frozenset] = {
    "military_conflict": frozenset(
        {
            "war",
            "invasion",
            "military",
            "ceasefire",
            "troops",
            "attack",
            "strike",
            "offensive",
            "conflict",
            "soldiers",
            "weapons",
        }
    ),
    "elections": frozenset(
        {
            "election",
            "vote",
            "president",
            "prime",
            "minister",
            "senator",
            "governor",
            "ballot",
            "polling",
            "runoff",
            "candidate",
        }
    ),
    "trade_economic": frozenset(
        {
            "tariff",
            "tariffs",
            "trade",
            "import",
            "export",
            "embargo",
            "customs",
            "commerce",
            "duty",
            "duties",
            "reciprocal",
            "liberation",
        }
    ),
    "nuclear": frozenset(
        {
            "nuclear",
            "icbm",
            "ballistic",
            "missile",
            "warhead",
            "enrichment",
            "uranium",
            "plutonium",
        }
    ),
    "sanctions": frozenset(
        {
            "sanctions",
            "sanction",
            "embargo",
            "expelled",
            "diplomatic",
            "ambassador",
            "alliance",
            "bilateral",
        }
    ),
    "us_domestic": frozenset(
        {
            "executive",
            "shutdown",
            "impeach",
            "congress",
            "senate",
            "cabinet",
            "supreme",
            "legislation",
            "regulation",
            "pardon",
            "indictment",
            "confirmation",
            "doge",
            "budget",
            "debt",
        }
    ),
    "regional_europe": frozenset(
        {
            "europe",
            "european",
            "ukraine",
            "russia",
            "nato",
            "germany",
            "france",
            "britain",
            "poland",
            "czech",
            "hungary",
            "moldova",
        }
    ),
    "regional_mideast": frozenset(
        {
            "israel",
            "gaza",
            "iran",
            "lebanon",
            "syria",
            "hamas",
            "hezbollah",
            "saudi",
            "iraq",
            "yemen",
            "qatar",
            "jordan",
        }
    ),
    "regional_asia": frozenset(
        {
            "china",
            "taiwan",
            "korea",
            "japan",
            "pakistan",
            "india",
            "vietnam",
            "philippines",
            "myanmar",
            "bangladesh",
            "tibet",
        }
    ),
    "regional_latam": frozenset(
        {
            "venezuela",
            "cuba",
            "mexico",
            "colombia",
            "brazil",
            "argentina",
            "nicaragua",
            "ecuador",
            "peru",
            "chile",
        }
    ),
    "regional_africa": frozenset(
        {
            "africa",
            "ethiopia",
            "sudan",
            "somalia",
            "nigeria",
            "kenya",
            "congo",
            "libya",
            "egypt",
            "sahel",
            "mali",
            "niger",
        }
    ),
    "regional_eurasia": frozenset(
        {
            "turkey",
            "georgia",
            "armenia",
            "azerbaijan",
            "kazakhstan",
            "belarus",
            "erdogan",
            "caucasus",
            "eurasia",
        }
    ),
}

# Max subreddits per poll cycle (core always included; topic subs fill remaining slots)
REDDIT_MAX_SUBREDDITS: int = 20

# ── News-edge series (option A, 2026-05-30) ──────────────────────────────────
# Series with demonstrated news-edge: all 158 of the bot's lifetime trading
# opportunities clustered on these event-driven political/geopolitical series
# (and every one already carries a categorical prior in
# analysis/regime_classifier._SERIES_PRIORS). feeds/search_news_monitor
# prioritizes active markets on these series for targeted news retrieval so they
# are never crowded out of the SEARCH_MAX_QUERIES budget by high-open-interest
# macro / "mention" markets the bot has no news-edge on (CPI/GDP/yield threshold
# markets resolve on a number, not news). Membership is by series-ticker prefix.
#
# ⚠️ COLD-START SEED ONLY — this list rots (it is tied to the current political
# moment: KXTRUMP*, KXMOCTRUMP25, etc.). It is NOT the source of truth. The
# source of truth is the self-maintaining set in tasks/stats/edge_series.py
# (option A-2): `active_edge_series()` derives the live edge set from a rolling
# 45-day window of OPPORTUNITY history (`data/news_edge_series.json`, refreshed
# by scripts/match_feedback_aggregator.py), auto-promoting producing series and
# auto-aging-out dead ones — mirroring feeds/subreddit_discovery's
# candidate + ZERO_SIGNAL_POSTS suppression. This static set is used ONLY when
# no artifact exists yet (a fresh DB); once the aggregator has run, dead series
# here are never resurrected. So when the administration changes or Kalshi
# retires a series, the live set self-corrects — unlike the obsolete
# KALSHI_GEOPOLITICAL_SERIES allowlist (see CLAUDE.md) which had no such loop.
# Blast radius is low: this only affects retrieval *priority*, not the trade
# gate, so even a wrong entry degrades throughput silently rather than causing
# bad trades. Operator-`pinned` entries in the artifact override aging.
NEWS_EDGE_SERIES: frozenset = frozenset({
    "KXTRUMPIRAN",          # Iran diplomacy / kinetic events (34 opps)
    "KXTRUMPCHINA",         # China-related Trump decisions (31 opps; 5 resolved)
    "KXTXRUNOFFENDORSE",    # Texas Senate runoff endorsements (28 opps)
    "KXTRUMPMENTION",       # Trump mention markets (17 opps)
    "KXMOCTRUMP25",         # Trump month-of-action calendar (11 opps)
    "KXUSAIRANAGREEMENT",   # US-Iran nuclear deal (9 opps; 1 resolved)
    "KXCHINAANNOUNCE",      # China-side announcements (8 opps)
    "KXNEWDEAL",            # Trump trade-deal announcement (7 opps)
    "KXTRUMPMENTIONB",      # Trump mention markets, B variant (7 opps)
    "KXTRUMPENDORSE",       # Trump endorsement events (3 opps)
    "KXFISAEXTEND",         # FISA extension legislation (3 resolved trades)
    "KXLUTNICKOUT",         # Lutnick LNG / policy (2 opps; 1 resolved)
    "KXNEWTARIFFS",         # New-tariffs-this-month (1 opp)
})

# Gate for the news-edge retrieval prioritization (option A). DEFAULT OFF.
# When False, feeds/search_news_monitor ranks markets purely by
# open_interest×uncertainty (the prior production behavior). When True, markets
# on the active news-edge set (tasks/stats/edge_series) are ranked first within
# the unchanged SEARCH_MAX_QUERIES budget. This is a DECISION-AFFECTING change
# (it alters which markets get targeted news → which paper trades arise), so per
# IC §16 it ships INERT and may only be turned on with replayed-EV evidence (or
# an explicit operator REPLAY_GATE_OVERRIDE memo). Flip via .env + restart.
ENABLE_NEWS_EDGE_PRIORITIZATION: bool = (
    os.getenv("ENABLE_NEWS_EDGE_PRIORITIZATION", "false").strip().lower()
    in {"1", "true", "yes", "on"}
)

# Shadow-only market-first retrieval diagnostics. DEFAULT OFF.
ENABLE_MARKET_FIRST_QUERY_SHADOW: bool = _parse_bool_env(
    "ENABLE_MARKET_FIRST_QUERY_SHADOW",
    default="false",
)
_MARKET_SOURCE_HINTS_QUERY_MODE_ENV = os.getenv("MARKET_SOURCE_HINTS_QUERY_MODE")
MARKET_SOURCE_HINTS_QUERY_MODE: str = (
    _MARKET_SOURCE_HINTS_QUERY_MODE_ENV.strip().lower()
    if _MARKET_SOURCE_HINTS_QUERY_MODE_ENV is not None
    else (
        "production"
        if _resolve_market_source_hints_mode() == "production"
        else ("shadow" if ENABLE_MARKET_FIRST_QUERY_SHADOW else "off")
    )
)
MARKET_SOURCE_HINTS_QUERY_CAP: int = int(os.getenv("MARKET_SOURCE_HINTS_QUERY_CAP", "2"))

# Real web-research gate before terminal neutral/no-keyword skips.
# DEFAULT OFF: production-effect network research must be enabled explicitly.
REAL_WEB_RESEARCH_MODE: str = os.getenv("REAL_WEB_RESEARCH_MODE", "off").strip().lower()
REAL_WEB_RESEARCH_MAX_QUERIES: int = int(os.getenv("REAL_WEB_RESEARCH_MAX_QUERIES", "6"))
REAL_WEB_RESEARCH_TIMEOUT_SECONDS: float = float(
    os.getenv("REAL_WEB_RESEARCH_TIMEOUT_SECONDS", "12.0")
)
ENABLE_RESEARCH_PREWARM_TASK: bool = _parse_bool_env(
    "ENABLE_RESEARCH_PREWARM_TASK",
    default="false",
)
RESEARCH_PREWARM_INTERVAL_SECONDS: float = float(
    os.getenv("RESEARCH_PREWARM_INTERVAL_SECONDS", "900")
)
RESEARCH_PREWARM_MAX_MARKETS: int = int(os.getenv("RESEARCH_PREWARM_MAX_MARKETS", "25"))
RESEARCH_PREWARM_MAX_PAGES: int = int(os.getenv("RESEARCH_PREWARM_MAX_PAGES", "5"))
RESEARCH_PREWARM_CONCURRENCY: int = int(os.getenv("RESEARCH_PREWARM_CONCURRENCY", "1"))
RESEARCH_PREWARM_SOURCEABLE_SERIES_FALLBACK: tuple[str, ...] = _parse_string_tuple(
    os.getenv("RESEARCH_PREWARM_SOURCEABLE_SERIES_FALLBACK"),
    default=(
        "KXGDP",
        "KXCPI",
        "KXFED",
        "KXNASDAQ100",
        "KXBTC",
        "KXETH",
        "KXHIGHNY",
        "KXMLB",
        "KXNBA",
    ),
)
RESEARCH_PREWARM_TARGET_COOLDOWN_SECONDS: float = float(
    os.getenv("RESEARCH_PREWARM_TARGET_COOLDOWN_SECONDS", "1800")
)

# Shadow-only per-fresh-item assignment diagnostics. DEFAULT OFF.
ENABLE_FRESH_PASS_ASSIGNMENT_SHADOW: bool = _parse_bool_env(
    "ENABLE_FRESH_PASS_ASSIGNMENT_SHADOW",
    default="false",
)

# ── Kalshi market filters ─────────────────────────────────────────────────────
# Series ticker PREFIXES to reject — used as a second filter after keyword
# discovery to drop sports leagues from geo-relevant countries (e.g. Saudi Pro
# League matches "saudi", J-League matches "japan").
MARKET_SERIES_BLOCKLIST_PREFIXES = [
    # US major sports
    "KXNCAA",  # NCAA sports
    "KXNFL",  # NFL football
    "KXNBA",  # NBA basketball
    "KXMLB",  # MLB baseball
    "KXNHL",  # NHL hockey
    "KXMLS",  # MLS soccer
    # International soccer leagues / cups
    "KXSOCCER",  # Generic international soccer
    "KXUCL",  # UEFA Champions League
    "KXUEL",  # UEFA Europa League
    "KXSAUDIPL",  # Saudi Pro League
    "KXJLEAGUE",  # Japan J-League
    "KXJBLEAGUE",  # Japan B-League (basketball)
    "KXKLEAGUE",  # Korea K-League
    "KXLIGUE1",  # French Ligue 1
    "KXBUNDESLIGA",  # German Bundesliga
    "KXVENFUTVE",  # Venezuela soccer
    # International cricket
    "KXIPL",  # Indian Premier League cricket
    "KXPSL",  # Pakistan Super League cricket
    "KXWPL",  # Women's Indian Premier League cricket
    "KXCRICKET",  # General cricket markets (ODI / Test / T20I)
    "KXT20",  # T20 cricket
    # Other international sports
    "KXTENNIS",  # Tennis (generic)
    "KXATP",  # ATP tennis (men's)
    "KXWTA",  # WTA tennis (women's)
    "KXGOLF",  # Golf (generic)
    "KXPGA",  # PGA golf
    "KXBOXING",  # Boxing (generic)
    "KXWBC",  # World Boxing Council title fights / World Baseball Classic (both sports)
    "KXMMA",  # MMA / generic UFC
    "KXUFC",  # UFC-prefixed series (titles, fights, weight classes)
    "KXNASCAR",  # NASCAR
    "KXFORMULA",  # Formula 1 (KXFORMULA prefix)
    "KXF1",  # Formula 1 (KXF1 prefix variants)
    "KXOLYMPIC",  # Summer / generic Olympics
    "KXWO",  # Winter Olympics (KXWO* prefix is distinct from KXOLYMPIC*)
    "KXEWC",  # Esports World Cup
    "KXBSL",  # Basketball Super Lig (Turkey)
    "KXFIBAECUP",  # FIBA Europe Cup
    # Additional basketball leagues
    "KXWNBA",  # Women's NBA
    "KXCBA",  # Chinese Basketball Association
    "KXNBL",  # Australian National Basketball League
    "KXEUROCUP",  # European basketball EuroCup
    # Additional soccer leagues
    "KXEPL",  # English Premier League
    "KXEGYPL",  # Egyptian Premier League
    "KXISL",  # Israel Super League
    "KXCHNSL",  # Chinese Super League
    "KXCANPL",  # Canadian Premier League
    "KXAFCC",  # AFC Champions League (sibling of KXUCL/KXUEL)
    "KXUEFA",  # UEFA Champions League / UEFA-prefixed (covers KXUEFAGAME etc.)
    # College sports
    "KXCFP",  # College Football Playoff
    "KXCFB",  # College Football
    "KXMARMAD",  # March Madness (college basketball)
    # Sports leader / draft / coaching markets — uniformly sport-driven
    "KXLEADER",  # Per-sport leader markets (KXLEADERMLB, KXLEADERNBA, KXLEADERNFL)
    "KXNEXTTEAM",  # Player next-team
    "KXNEXTCOACH",  # Next coach
    "KXCOACHOUT",  # Coach out / fired
    "KXTEAMSIN",  # Teams in [final / playoff round]
    "KXTRADEOFF",  # Trade offseason
    "KXRANKLIST",  # Fantasy football rank lists
    # Polling / approval rating markets (not geopolitical events)
    "KXAPRPOTUS",  # Presidential approval rating polls
    "KXPOLLPOTUS",  # Presidential polls
    # Central bank decision markets (monetary policy, not geopolitical events)
    "KXCBDECISION",  # Central bank rate decisions (Russia, China, etc.)
    # Trump quote-prediction markets (not event markets; match any Trump headline)
    "KXTRUMPSAY",  # Will Trump say [word] this week?
    "KXTRUMPSAYMONTH",  # Will Trump say [word] this month?
    # Entertainment / crypto / weather
    "KXENTERTAIN",  # Entertainment / pop culture
    "KXCRYPTO",  # Crypto price markets (generic)
    # Individual crypto-coin series (Kalshi uses per-coin prefixes outside KXCRYPTO)
    "KXDOGE",  # Dogecoin markets
    "KXBTC",  # Bitcoin markets
    "KXETH",  # Ethereum markets
    "KXSOL",  # Solana markets
    "KXXRP",  # XRP markets
    "KXWEATHER",  # Weather markets
    # Multi-event parlay packages
    "KXMVESPORT",  # Multi-event sports
    "KXMVECROSS",  # Cross-category multi-event
]

MARKET_CACHE_TTL_SECONDS = (
    1800  # 30 min — geo market refresh takes ~3 min; no need to refresh every 5
)
MAX_MARKET_DAYS_TO_EXPIRY = 30

# ── Market feedback loop thresholds ──────────────────────────────────────────
# Loop C: open position drift alert
# Log a POSITION_DRIFT event to trades.jsonl when an open position's market
# price moves this many cents from the entry price. Rate-limited per ticker.
DRIFT_ALERT_CENTS: int = int(os.getenv("DRIFT_ALERT_CENTS", "15"))
DRIFT_LOG_COOLDOWN_SECS: int = int(os.getenv("DRIFT_LOG_COOLDOWN_SECS", "3600"))

# Loop A: price-velocity-driven targeted news search
# Trigger a targeted Google News + GDELT search for a geo market when its price
# moves this many cents within the last 5 minutes (PRICE_VELOCITY_WINDOW_SECS).
PRICE_MOVE_THRESHOLD_CENTS: int = int(os.getenv("PRICE_MOVE_THRESHOLD_CENTS", "10"))
PRICE_SEARCH_COOLDOWN_SECS: int = int(os.getenv("PRICE_SEARCH_COOLDOWN_SECS", "1800"))
PRICE_VELOCITY_WINDOW_SECS: int = int(os.getenv("PRICE_VELOCITY_WINDOW_SECS", "300"))

# ── Paper trading thresholds (cast a wide net for data collection) ────────────
# These are more permissive than live thresholds to maximise resolved trades
# and build source credibility data during the paper phase.
PAPER_MIN_EDGE = 0.02  # vs live 0.04
PAPER_MIN_MATCH_SCORE = (
    0.06  # raised from 0.03 -- scores <0.06 are almost always wrong-market
)
# noise (e.g. "Trump fires Bondi" -> China visit at 0.033).
# Real relevance starts at ~0.06 based on match score distribution.
PAPER_MAX_CANDIDATES = 3  # raised from 1 -- evaluate top 3 market matches per article.
# With max_candidates=1 the LLM was forced to evaluate wrong
# markets (top Jaccard match != best conceptual match) and
# correctly returned neutral, suppressing real signals.
# CPU CONSTRAINT: at 20-40s/call this means up to ~120s inference
# per article. On Mac Studio M4 Max (<5s/call) raise to 8-10.
PAPER_FLAT_CONTRACTS = (
    5  # flat contract count during paper training (no bankroll gating)
)
PAPER_BLOCK_SAME_SIDE_DUPLICATE = (
    True  # block any same-ticker same-side position during paper phase
)

# ── Source credibility settings ───────────────────────────────────────────────
CREDIBILITY_MIN_SAMPLE = 10  # trades needed before multiplier takes effect
CREDIBILITY_MIN_MULT = 0.5  # floor multiplier (unreliable source)
CREDIBILITY_MAX_MULT = 1.5  # ceiling multiplier (very reliable source)
# Half-life (days) for exponential time decay on credibility outcomes.
# A win/loss CREDIBILITY_HALF_LIFE_DAYS days ago counts 50% as much as today's.
CREDIBILITY_HALF_LIFE_DAYS: float = 30.0

# ── Source stats / quality gate settings ─────────────────────────────────────
# Signal rate (signals / posts_seen) is available in 24-48h vs win_rate which
# needs 10+ resolved trades per source (weeks to months).  Quality gates fire
# only after MIN_POSTS posts so brand-new sources get a fair trial first.
SOURCE_STATS_MIN_POSTS: int = int(os.getenv("SOURCE_STATS_MIN_POSTS", "100"))
SOURCE_STATS_LOW_SIGNAL_RATE: float = float(
    os.getenv("SOURCE_STATS_LOW_SIGNAL_RATE", "0.005")
)
SOURCE_STATS_ZERO_SIGNAL_POSTS: int = int(
    os.getenv("SOURCE_STATS_ZERO_SIGNAL_POSTS", "200")
)

# Subreddit discovery (v0.21.0) -- Reddit post search finds candidate subreddits
# organically active on our market topics; source_stats then evaluates quality.
SUBREDDIT_DISCOVERY_INTERVAL_SECS: int = int(
    os.getenv("SUBREDDIT_DISCOVERY_INTERVAL_SECS", "21600")
)  # 6h between passes
SUBREDDIT_PROBE_COOLDOWN_SECS: int = int(
    os.getenv("SUBREDDIT_PROBE_COOLDOWN_SECS", "10800")
)  # 3h cooldown per candidate
SUBREDDIT_PROBE_SLOTS: int = int(
    os.getenv("SUBREDDIT_PROBE_SLOTS", "3")
)  # candidates per poll cycle
SUBREDDIT_DISCOVERY_MAX_QUERIES: int = int(
    os.getenv("SUBREDDIT_DISCOVERY_MAX_QUERIES", "10")
)  # queries per pass

# ── Signal keyword categories ─────────────────────────────────────────────────
GEOPOLITICAL_SIGNALS = [
    {
        "keywords": [
            "missile strike",
            "bombs",
            "shelling",
            "invasion",
            "military attack",
            "war declared",
            "troops deployed",
            "offensive launched",
            # Natural-language variants observed missing from keyword gate (2026-04-12)
            "war with",  # "War With Iran is Not Yet Over" -- was missed
            "at war",  # "at war with"
            "airstrikes",  # standard wire-service military phrasing
            # Phase 7.1 supervised promotion (shadow-validated, 2026-04-13)
            "strait hormuz",
            "hormuz blockade",
        ],
        "direction": "yes",
        "strength": 0.15,
    },
    {
        "keywords": [
            "ceasefire",
            "peace deal",
            "withdrawal",
            "troops pull back",
            "de-escalation",
            "peace talks success",
            # Natural-language variants for resuming / successful negotiations.
            # NOTE: "peace talks" intentionally omitted -- too ambiguous for a directional
            # group. "peace talks fail" would get NO direction in keyword-only fallback,
            # which is wrong. Failure phrasing is covered by the negotiations-failure group
            # (YES). Resumption is covered by "talks resume" below.
            "truce",  # informal ceasefire, common in wire copy
            "talks resume",  # resumption of dialogue after breakdown
        ],
        "direction": "no",
        "strength": 0.12,
    },
    # Negotiations / talks failure -- opposite direction from ceasefire group.
    # Failed talks signal continued conflict; must be a separate YES group so the
    # keyword-only fallback direction is correct.
    # All phrases observed as keyword_gate misses in the 2026-04-12 signal audit.
    {
        "keywords": [
            "talks fail",
            "talks collapse",
            "talks break down",
            "without a deal",
            "no deal reached",
            "failed to agree",
            "negotiations collapse",
            "negotiations fail",
            "deal rejected",
            "ceasefire collapses",
        ],
        "direction": "yes",
        "strength": 0.10,
    },
    {
        "keywords": [
            "election results",
            "wins election",
            "wins presidency",
            "elected president",
            "projected winner",
            "victory declared",
        ],
        "direction": "yes",
        "strength": 0.25,
    },
    {
        "keywords": ["concedes", "loses election", "runoff", "recount ordered"],
        "direction": "no",
        "strength": 0.15,
    },
    {
        "keywords": [
            "sanctions imposed",
            "embargo",
            "expelled ambassador",
            "diplomatic relations severed",
        ],
        "direction": "yes",
        "strength": 0.08,
    },
    {
        "keywords": [
            "sanctions lifted",
            "diplomatic breakthrough",
            "summit agreement",
            "deal signed",
            "agreement reached",  # "agreement reached after talks"
            "deal reached",  # "deal reached between"
        ],
        "direction": "no",
        "strength": 0.07,
    },
    {
        "keywords": [
            "nuclear test",
            "nuclear weapon",
            "detonation",
            "icbm test",
            "ballistic missile test",
        ],
        "direction": "yes",
        "strength": 0.20,
    },
    {
        "keywords": [
            "coup",
            "overthrown",
            "government toppled",
            "military takeover",
            "martial law",
        ],
        "direction": "yes",
        "strength": 0.22,
    },
    {
        "keywords": [
            "terror attack",
            "bombing",
            "assassination",
            "mass shooting",
            "hostage",
        ],
        "direction": "yes",
        "strength": 0.10,
    },
    # Trade & economic policy
    {
        "keywords": [
            "tariff imposed",
            "new tariff",
            "tariff hike",
            "trade war",
            "import ban",
            "export ban",
            "trade embargo",
            "reciprocal tariff",
        ],
        "direction": "yes",
        "strength": 0.10,
    },
    {
        "keywords": [
            "tariff removed",
            "tariff reduced",
            "trade deal signed",
            "trade agreement",
            "free trade",
            "tariff exemption",
        ],
        "direction": "no",
        "strength": 0.08,
    },
    # Foreign policy
    {
        "keywords": [
            "diplomatic expulsion",
            "ambassador recalled",
            "embassy closed",
            "foreign aid suspended",
            "alliance withdrawal",
            "nato invoked",
            "security pact",
            "defense agreement",
        ],
        "direction": "yes",
        "strength": 0.10,
    },
    {
        "keywords": [
            "diplomatic talks",
            "foreign aid restored",
            "embassy reopened",
            "alliance renewed",
            "security guarantee",
        ],
        "direction": "no",
        "strength": 0.07,
    },
    # Domestic policy (US-focused -- Kalshi heavy on US markets)
    {
        "keywords": [
            "executive order signed",
            "government shutdown",
            "debt ceiling",
            "impeachment vote",
            "cabinet fired",
            "attorney general fired",
            "special counsel appointed",
            "national emergency declared",
        ],
        "direction": "yes",
        "strength": 0.12,
    },
    {
        "keywords": [
            "government reopened",
            "debt ceiling raised",
            "budget passed",
            "bipartisan deal",
            "confirmation vote passed",
        ],
        "direction": "no",
        "strength": 0.08,
    },
    # Senate confirmations / testimony (KXSENATECONFIRM, KXBONDITESTIFY, etc.)
    # These markets are active on Kalshi but the keyword gate was previously silent
    # on confirmation-specific language.
    {
        "keywords": [
            "confirmation hearing",
            "senate confirms",
            "senate vote",
            "confirmation vote",
            "cabinet confirmed",
            "filibuster",
            "cloture vote",
            "nominee confirmed",
            "nominee rejected",
            "testimony begins",
            "testifies before",
            "senate armed services",
            "advise and consent",
        ],
        "direction": "yes",
        "strength": 0.12,
    },
    # Low-strength standalone committee mention. Procedural "Senate Judiciary"
    # headlines alone are too weak to move Lane B neutral fixture F8 above 0.02.
    {
        "keywords": ["senate judiciary"],
        "direction": "yes",
        "strength": 0.019,
    },
    # Cycle-15B extraction repair: resolution-event phrases for synthetic Lane B
    # fixtures. Phrases stay event-specific so neutral procedural mentions remain
    # governed by the low-strength standalone committee group above.
    {
        "keywords": [
            "fisa section 702 reauthorization signed into law",
            "fisa section 702 reauthorization legislation",
            "trump issues pardons",
            "signed pardons",
            "sign nuclear deal",
            "comprehensive nuclear agreement",
            "arrives in islamabad",
            "official pakistan visit",
        ],
        "direction": "yes",
        "strength": 0.12,
    },
    {
        "keywords": [
            "fisa section 702 expires",
            "senate fails to act",
            "will not become law",
            "no january 6 pardons",
            "no pardons for january 6 defendants",
            "cancels pakistan trip",
            "canceled his planned pakistan trip",
        ],
        "direction": "no",
        "strength": 0.12,
    },
    # Trump executive actions -- high-volume Kalshi market category covering
    # firings, pardons, and executive orders where the headline IS the market event.
    {
        "keywords": [
            "trump fires",
            "trump pardons",
            "executive order signed",
            "fired by trump",
            "dismissed by trump",
            "removed from office",
            "trump dismisses",
            "white house fires",
            "trump terminates",
        ],
        "direction": "yes",
        "strength": 0.10,
    },
]


@dataclass
class BotConfig:
    # Kalshi credentials
    api_key_id: str = field(default_factory=lambda: os.getenv("KALSHI_API_KEY_ID", ""))
    api_key_secret: str = field(
        default_factory=lambda: os.getenv("KALSHI_API_KEY_SECRET", "")
    )
    kalshi_env: str = field(default_factory=lambda: os.getenv("KALSHI_ENV", "demo"))

    # Polymarket US credentials and endpoints. Disabled until explicitly enabled.
    polymarket_us_enabled: bool = field(
        default_factory=lambda: _parse_bool_env("POLYMARKET_US_ENABLED", "false")
    )
    polymarket_us_key_id: str = field(
        default_factory=lambda: os.getenv("POLYMARKET_US_KEY_ID", "").strip()
    )
    polymarket_us_secret: str = field(
        default_factory=lambda: os.getenv("POLYMARKET_US_SECRET", "").strip()
    )
    polymarket_us_public_base_url: str = field(
        default_factory=lambda: os.getenv(
            "POLYMARKET_US_PUBLIC_BASE_URL", "https://gateway.polymarket.us"
        ).rstrip("/")
    )
    polymarket_us_api_base_url: str = field(
        default_factory=lambda: os.getenv(
            "POLYMARKET_US_API_BASE_URL", "https://api.polymarket.us"
        ).rstrip("/")
    )
    polymarket_us_live_trading_enabled: bool = field(
        default_factory=lambda: _parse_bool_env(
            "POLYMARKET_US_LIVE_TRADING_ENABLED", "false"
        )
    )
    polymarket_us_public_requests_per_second: int = field(
        default_factory=lambda: int(
            os.getenv("POLYMARKET_US_PUBLIC_REQUESTS_PER_SECOND", "20")
        )
    )
    polymarket_us_order_requests_per_second: int = field(
        default_factory=lambda: int(
            os.getenv("POLYMARKET_US_ORDER_REQUESTS_PER_SECOND", "100")
        )
    )
    polymarket_us_bbo_requests_per_minute: int = field(
        default_factory=lambda: int(
            os.getenv("POLYMARKET_US_BBO_REQUESTS_PER_MINUTE", "12")
        )
    )
    polymarket_us_position_valuation_requests_per_minute: float = field(
        default_factory=lambda: float(
            os.getenv("POLYMARKET_US_POSITION_VALUATION_REQUESTS_PER_MINUTE", "0.5")
        )
    )

    # Live trading params
    bankroll: float = field(default_factory=lambda: float(os.getenv("BANKROLL", "500")))
    kelly_fraction: float = field(
        default_factory=lambda: float(os.getenv("KELLY_FRACTION", "0.5"))
    )
    min_edge: float = field(
        default_factory=lambda: float(os.getenv("MIN_EDGE", "0.04"))
    )

    # Dynamic bet sizing: max_bet = min(hard_cap, max_bet_pct_bankroll * notional_bankroll)
    # max_bet_pct_bankroll is the operating ceiling (scales with bankroll).
    # max_bet_hard_cap is a safety backstop (rarely the binding constraint).
    max_bet_pct_bankroll: float = field(
        default_factory=lambda: float(os.getenv("MAX_BET_PCT_BANKROLL", "0.15"))
    )
    max_bet_hard_cap: float = field(
        default_factory=lambda: float(os.getenv("MAX_BET_HARD_CAP", "200.0"))
    )
    # PROFIT-SIZING-001 (2026-06-11): min-bet floor removed (default 0.0) per
    # operator decision — the bot trades freely on Kelly down to the natural
    # 1-contract floor (contracts_from_dollars). This removes the rejection in
    # kelly_bet (kelly_dollars < min_bet -> 0) and the floor on dynamic_max_bet's
    # cap. CAVEAT (load-bearing): fees are NOT modelled anywhere in the EV/Kelly
    # path, so min_bet_dollars was the implicit fee-floor. With it at 0, a tiny
    # positive-edge LIVE trade can be net-negative after Kalshi fees — positive-
    # EV gating still holds at the edge level (min_edge), but NOT net-of-fees.
    # Follow-up: add a fee-aware EV gate before relying on live profitability.
    min_bet_dollars: float = field(
        default_factory=lambda: float(os.getenv("MIN_BET_DOLLARS", "0.0"))
    )

    # PROFIT-ALIGN-001/003 (2026-05-25): trade-audit driven safeguards.
    # See docs/profit_path_debt_log.md PROFIT-ALIGN-001 for the full design.
    #
    # Floor-clamp Kelly multiplier: when _parse_llm_response clamps the
    # estimated probability at 0.05 / 0.95 (i.e. LLM wanted more extreme
    # but the function floor caught it), the bot's true certainty about the
    # exact prob is fuzzy. Multiply the Kelly stake by this factor to
    # reduce exposure on clamped trades. 0.5 = halve. Set to 1.0 to disable.
    floor_clamp_kelly_multiplier: float = field(
        default_factory=lambda: float(os.getenv("FLOOR_CLAMP_KELLY_MULTIPLIER", "0.5"))
    )
    # Per-market-prefix open-position cap (PROFIT-ALIGN-004). The matcher
    # can correctly bridge multiple outcome contracts in the same series
    # (e.g. KXTXRUNOFFENDORSE-DJT-BOTH / -KPAX / -JCOR). Without a cap,
    # one piece of news can cause N concurrent bets against the same
    # underlying topic — losses compound if the bot's read is wrong.
    # Default 2 simultaneous open positions per prefix.
    max_open_positions_per_prefix: int = field(
        default_factory=lambda: int(os.getenv("MAX_OPEN_POSITIONS_PER_PREFIX", "2"))
    )

    # PROFIT-ALIGN-011 (2026-05-25): magnitude→shift table moved from
    # hardcoded constants in analysis/signal_analyzer.py to cfg. Defaults
    # preserve historical behavior. Operator can recalibrate once
    # PROFIT-ALIGN-002 CALIBRATION_OBSERVATION evidence accumulates (≥30
    # resolved trades, then bucket by magnitude and measure realized vs
    # predicted shift). Env overrides: MAGNITUDE_SHIFT_{SMALL,MODERATE,LARGE}.
    magnitude_shift_small: float = field(
        default_factory=lambda: float(os.getenv("MAGNITUDE_SHIFT_SMALL", "0.08"))
    )
    magnitude_shift_moderate: float = field(
        default_factory=lambda: float(os.getenv("MAGNITUDE_SHIFT_MODERATE", "0.15"))
    )
    magnitude_shift_large: float = field(
        default_factory=lambda: float(os.getenv("MAGNITUDE_SHIFT_LARGE", "0.25"))
    )

    # PROFIT-ALIGN-007 (2026-05-25): position-drift observability threshold.
    # When a held position's current_price drifts by more than this fraction
    # from entry_price, emit a POSITION_DRIFT event (already-wired logger
    # method `log_position_drift`). Pure observability — does NOT auto-exit.
    # Real exit logic is deferred until calibration evidence informs the
    # drift-vs-resolution trade-off. Set to 1.0 to disable emission.
    position_drift_alert_threshold: float = field(
        default_factory=lambda: float(os.getenv("POSITION_DRIFT_ALERT_THRESHOLD", "0.15"))
    )

    # PROFIT-ALIGN-010 (2026-05-25): LLM content-hash dedup cache for the
    # runtime signal-analyzer. Distinct from the replay-CI llm_cache.sqlite
    # (deterministic-replay only). When the same (headline_text, market_title,
    # market_yes_price_bucket) combo is analyzed within this TTL, return the
    # cached result instead of issuing a new LLM call. Wallclock saving;
    # local Ollama wallclock is the only cost since model is on-host. Set to
    # 0 to disable (forces per-call LLM evaluation).
    llm_dedup_cache_ttl_seconds: int = field(
        default_factory=lambda: int(os.getenv("LLM_DEDUP_CACHE_TTL_SECONDS", "900"))
    )

    # PROFIT-ALIGN-007 (3-lane simplification): when True, lanes with no
    # contributing evidence (e.g. accumulation lane with zero dossier
    # updates, structural lane with no series_prior data) emit a
    # LANE_SKIPPED event instead of computing equal-weight defaults.
    # Default False — opt-in until operator validates no behavior change
    # on real BDs.
    enable_lane_skip_when_no_data: bool = field(
        default_factory=lambda: _parse_bool_env("ENABLE_LANE_SKIP_WHEN_NO_DATA", "false")
    )

    # PROFIT-ALIGN-009 (derived series priors): opt-in flag for
    # auto-deriving a regime weight prior from market metadata (close_time
    # window + ticker-prefix heuristics) when no explicit _SERIES_PRIORS
    # entry exists. Default False — falls back to _time_prior as today.
    enable_derived_series_priors: bool = field(
        default_factory=lambda: _parse_bool_env("ENABLE_DERIVED_SERIES_PRIORS", "false")
    )

    # Time discount parameters -- passed through to kelly_bet() to penalise bets
    # that lock up capital for months. Exponential decay with a configurable floor.
    time_discount_half_life: float = field(
        default_factory=lambda: float(os.getenv("TIME_DISCOUNT_HALF_LIFE", "14.0"))
    )
    time_discount_floor: float = field(
        default_factory=lambda: float(os.getenv("TIME_DISCOUNT_FLOOR", "0.20"))
    )

    # Ticker cooldowns (configurable to tune via .env without code changes)
    live_ticker_cooldown: int = field(
        default_factory=lambda: int(os.getenv("LIVE_TICKER_COOLDOWN", "600"))
    )  # 10 min
    paper_ticker_cooldown: int = field(
        default_factory=lambda: int(os.getenv("PAPER_TICKER_COOLDOWN", "14400"))
    )  # 4h

    # PROFIT-EXEC-002 series-correlation guard window. When BlendTask is
    # about to enqueue a candidate, it checks whether another candidate
    # from the same Kalshi series prefix enqueued within this window;
    # if so, the new candidate is suppressed with reason
    # `series_correlation_in_window`. Default 1h; any value `<= 0`
    # disables the guard entirely (operator escape hatch). Negative
    # env values are clamped to 0 so `-300` cannot silently bypass the
    # guard via the `window > 0` short-circuit at tasks/blend_task.py.
    series_correlation_window_seconds: int = field(
        default_factory=lambda: max(0, int(os.getenv("SERIES_CORRELATION_WINDOW_SECONDS", "3600")))
    )

    # Go-live gate thresholds -- evaluated by --go-live before allowing confirmation.
    # Set these to 0/1.0 to disable any individual gate.
    go_live_min_resolved: int = field(
        default_factory=lambda: int(os.getenv("GO_LIVE_MIN_RESOLVED", "20"))
    )
    go_live_min_win_rate: float = field(
        default_factory=lambda: float(os.getenv("GO_LIVE_MIN_WIN_RATE", "0.52"))
    )
    go_live_max_drawdown_pct: float = field(
        default_factory=lambda: float(os.getenv("GO_LIVE_MAX_DRAWDOWN_PCT", "0.20"))
    )

    # Hard kill-switch for live trading.  Must be explicitly set to "true" in .env.
    # Without this flag: --go-live is refused, and go_live_confirmed in the DB is
    # ignored at startup -- the bot stays in paper mode regardless.
    # Policy: do NOT enable until Mac Studio hardware is in place.
    live_trading_enabled: bool = field(
        default_factory=lambda: (
            os.getenv("LIVE_TRADING_ENABLED", "false").lower() == "true"
        )
    )

    # Live session loss limit: halt all trading if live Kalshi balance drops more than
    # this fraction of BANKROLL below the session-start balance. Default 10% ($50 on $500).
    # Set to 1.0 to disable. Checked on every live trade attempt via get_balance().
    live_loss_limit_pct: float = field(
        default_factory=lambda: float(os.getenv("LIVE_LOSS_LIMIT_PERCENT", "0.10"))
    )

    # Portfolio risk: max fraction of notional bankroll deployed in a single ticker.
    # Prevents runaway concentration on one market (e.g. Iran war dominates the book).
    # Default 25% — raise in .env if needed. Set to 1.0 to disable.
    max_ticker_exposure_pct: float = field(
        default_factory=lambda: float(os.getenv("MAX_TICKER_EXPOSURE_PCT", "0.25"))
    )

    # Optional Anthropic API key (fallback LLM if Ollama unavailable)
    anthropic_api_key: Optional[str] = field(
        default_factory=lambda: os.getenv("ANTHROPIC_API_KEY")
    )

    # Reddit OAuth2 (optional — public JSON fallback if missing)
    reddit_client_id: Optional[str] = field(
        default_factory=lambda: os.getenv("REDDIT_CLIENT_ID")
    )
    reddit_client_secret: Optional[str] = field(
        default_factory=lambda: os.getenv("REDDIT_CLIENT_SECRET")
    )
    reddit_user_agent: Optional[str] = field(
        default_factory=lambda: os.getenv("REDDIT_USER_AGENT")
    )

    # Reddit ingestion master switch. Default OFF: Reddit denied the bot's OAuth
    # app (2026-05-29, "no community benefit"), so anonymous .json polling is
    # IP-blocked (~2,600 HTTP-403/wk; circuit permanently open) and Reddit yields
    # 0 signals / 0 opportunities / 0 trades lifetime (Track B B0 evidence,
    # PROFIT-EDGE-004). Leaving it scheduled just burns poll cycles and floods the
    # log with 403 warnings. Set REDDIT_ENABLED=true to re-enable cleanly if an
    # approved app ever exists.
    reddit_enabled: bool = field(
        default_factory=lambda: _parse_bool_env("REDDIT_ENABLED", "false")
    )

    @property
    def reddit_oauth_available(self) -> bool:
        return bool(self.reddit_client_id and self.reddit_client_secret)

    # Local Ollama settings (primary LLM backend)
    ollama_base_url: str = field(
        default_factory=lambda: os.getenv(
            "OLLAMA_BASE_URL", "http://localhost:11434/v1"
        )
    )
    ollama_model: str = field(
        default_factory=lambda: os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
    )
    ollama_request_timeout_seconds: int = field(
        default_factory=lambda: int(os.getenv("OLLAMA_REQUEST_TIMEOUT_SECONDS", "60"))
    )
    ollama_stage_budget_seconds: int = field(
        default_factory=lambda: int(os.getenv("OLLAMA_STAGE_BUDGET_SECONDS", "45"))
    )
    ollama_probe_timeout_seconds: int = field(
        default_factory=lambda: int(os.getenv("OLLAMA_PROBE_TIMEOUT_SECONDS", "5"))
    )
    enable_llm_routing_filter: bool = field(
        default_factory=lambda: os.getenv("ENABLE_LLM_ROUTING_FILTER", "false").strip().lower() in {"1", "true", "yes", "on"}
    )
    llm_allowed_price_bands: list[tuple[float, float]] = field(
        default_factory=lambda: _parse_float_band_pairs(
            os.getenv("LLM_ALLOWED_PRICE_BANDS", "0.00-0.35,0.65-1.00"),
            default=[(0.00, 0.35), (0.65, 1.00)],
        )
    )
    llm_excluded_price_bands: list[tuple[float, float]] = field(
        default_factory=lambda: _parse_float_band_pairs(
            os.getenv("LLM_EXCLUDED_PRICE_BANDS", ""),
            default=[],
        )
    )
    llm_min_keyword_signal: float = field(
        default_factory=lambda: float(os.getenv("LLM_MIN_KEYWORD_SIGNAL", "0.0"))
    )
    enable_pre_llm_match_gate: bool = field(
        default_factory=lambda: os.getenv("ENABLE_PRE_LLM_MATCH_GATE", "false").strip().lower() in {"1", "true", "yes", "on"}
    )
    pre_llm_match_gate_diagnostics_only: bool = field(
        default_factory=lambda: os.getenv("PRE_LLM_MATCH_GATE_DIAGNOSTICS_ONLY", "true").strip().lower() in {"1", "true", "yes", "on"}
    )
    pre_llm_match_gate_keyword_override_any_hit: bool = field(
        default_factory=lambda: os.getenv("PRE_LLM_MATCH_GATE_KEYWORD_OVERRIDE_ANY_HIT", "true").strip().lower() in {"1", "true", "yes", "on"}
    )
    pre_llm_match_gate_keyword_override_mode: str = field(
        default_factory=lambda: _resolve_keyword_override_mode()
    )
    pre_llm_match_gate_keyword_override_min_signal: float = field(
        default_factory=lambda: float(
            os.getenv(
                "PRE_LLM_MATCH_GATE_KEYWORD_OVERRIDE_MIN_SIGNAL",
                os.getenv("LLM_MIN_KEYWORD_SIGNAL", "0.0"),
            )
        )
    )
    market_source_hints_mode: str = field(
        default_factory=lambda: _resolve_market_source_hints_mode()
    )
    market_source_hints_emit_records: bool = field(
        default_factory=lambda: _parse_bool_env("MARKET_SOURCE_HINTS_EMIT_RECORDS", "false")
    )
    generic_search_circuit_mode: str = field(
        default_factory=lambda: os.getenv(
            "GENERIC_SEARCH_CIRCUIT_MODE", "shadow"
        ).strip()
    )
    real_web_research_mode: str = field(default_factory=lambda: REAL_WEB_RESEARCH_MODE)
    real_web_research_max_queries: int = field(
        default_factory=lambda: REAL_WEB_RESEARCH_MAX_QUERIES
    )
    real_web_research_timeout_seconds: float = field(
        default_factory=lambda: REAL_WEB_RESEARCH_TIMEOUT_SECONDS
    )
    enable_research_prewarm_task: bool = field(
        default_factory=lambda: ENABLE_RESEARCH_PREWARM_TASK
    )
    research_prewarm_interval_seconds: float = field(
        default_factory=lambda: RESEARCH_PREWARM_INTERVAL_SECONDS
    )
    research_prewarm_max_markets: int = field(
        default_factory=lambda: RESEARCH_PREWARM_MAX_MARKETS
    )
    research_prewarm_max_pages: int = field(
        default_factory=lambda: RESEARCH_PREWARM_MAX_PAGES
    )
    research_prewarm_concurrency: int = field(
        default_factory=lambda: RESEARCH_PREWARM_CONCURRENCY
    )
    research_prewarm_sourceable_series_fallback: tuple[str, ...] = field(
        default_factory=lambda: RESEARCH_PREWARM_SOURCEABLE_SERIES_FALLBACK
    )
    research_prewarm_target_cooldown_seconds: float = field(
        default_factory=lambda: RESEARCH_PREWARM_TARGET_COOLDOWN_SECONDS
    )
    enable_startup_observability_probe: bool = field(
        default_factory=lambda: os.getenv("ENABLE_STARTUP_OBSERVABILITY_PROBE", "true").strip().lower() in {"1", "true", "yes", "on"}
    )
    startup_observability_probe_strict: bool = field(
        default_factory=lambda: os.getenv("STARTUP_OBSERVABILITY_PROBE_STRICT", "false").strip().lower() in {"1", "true", "yes", "on"}
    )
    startup_observability_probe_required_fields: tuple[str, ...] = field(
        default_factory=lambda: _parse_string_tuple(
            os.getenv("STARTUP_OBSERVABILITY_PROBE_REQUIRED_FIELDS"),
            default=(
                "pre_llm_quality_pass",
                "pre_llm_semantic_overlap_count",
                "pre_llm_semantic_overlap_ratio",
                "pre_llm_headline_token_count",
                "pre_llm_market_token_count",
                "pre_llm_would_block",
                "pre_llm_keyword_override",
                "pre_llm_gate_reason",
            ),
        )
    )

    # Paper trading mode — True until explicitly confirmed via --go-live
    # Set at runtime via set_paper_mode() — do not mutate directly.
    is_paper_trading: bool = True

    def polymarket_us_startup_status(self) -> str:
        """Operator-facing Polymarket status without credential material."""
        enabled = str(self.polymarket_us_enabled).lower()
        live_trading = str(self.polymarket_us_live_trading_enabled).lower()
        if not self.polymarket_us_enabled:
            return "Polymarket US: enabled=false"
        return (
            "Polymarket US: "
            f"enabled={enabled} "
            "paper_execution=blend "
            f"live_trading={live_trading}"
        )

    def __post_init__(self) -> None:
        """Validate critical config at startup -- fail fast, not hours later."""
        errors: list[str] = []
        self.ollama_base_url = self.ollama_base_url.strip()
        self.ollama_model = self.ollama_model.strip()
        if not self.api_key_id:
            errors.append("KALSHI_API_KEY_ID is missing or empty")
        if not self.api_key_secret:
            errors.append("KALSHI_API_KEY_SECRET is missing or empty")
        if self.pre_llm_match_gate_keyword_override_mode not in {"any_hit", "all_required", "min_signal", "disabled"}:
            errors.append(
                "PRE_LLM_MATCH_GATE_KEYWORD_OVERRIDE_MODE must be one of "
                "any_hit|all_required|min_signal|disabled, "
                f"got '{self.pre_llm_match_gate_keyword_override_mode}'"
            )
        if self.pre_llm_match_gate_keyword_override_min_signal < 0:
            errors.append(
                "PRE_LLM_MATCH_GATE_KEYWORD_OVERRIDE_MIN_SIGNAL must be non-negative, "
                f"got {self.pre_llm_match_gate_keyword_override_min_signal}"
            )
        if self.market_source_hints_mode not in {"off", "shadow", "advisory", "production"}:
            errors.append(
                "MARKET_SOURCE_HINTS_MODE must be one of off|shadow|advisory|production, "
                f"got '{self.market_source_hints_mode}'"
            )
        if self.generic_search_circuit_mode not in {"off", "shadow", "enforce"}:
            errors.append(
                "GENERIC_SEARCH_CIRCUIT_MODE must be one of off|shadow|enforce, "
                f"got '{self.generic_search_circuit_mode}'"
            )
        if self.real_web_research_mode not in {"off", "shadow", "production"}:
            errors.append(
                "REAL_WEB_RESEARCH_MODE must be one of off|shadow|production, "
                f"got '{self.real_web_research_mode}'"
            )
        if self.real_web_research_max_queries <= 0:
            errors.append("REAL_WEB_RESEARCH_MAX_QUERIES must be positive")
        if self.real_web_research_timeout_seconds <= 0:
            errors.append("REAL_WEB_RESEARCH_TIMEOUT_SECONDS must be positive")
        if self.research_prewarm_interval_seconds <= 0:
            errors.append("RESEARCH_PREWARM_INTERVAL_SECONDS must be positive")
        if self.research_prewarm_max_markets <= 0:
            errors.append("RESEARCH_PREWARM_MAX_MARKETS must be positive")
        if self.research_prewarm_max_pages <= 0:
            errors.append("RESEARCH_PREWARM_MAX_PAGES must be positive")
        if self.research_prewarm_concurrency <= 0:
            errors.append("RESEARCH_PREWARM_CONCURRENCY must be positive")
        if self.research_prewarm_target_cooldown_seconds <= 0:
            errors.append("RESEARCH_PREWARM_TARGET_COOLDOWN_SECONDS must be positive")
        if self.kalshi_env not in ("demo", "prod"):
            errors.append(
                "KALSHI_ENV must be 'demo' or 'prod', got '%s'" % self.kalshi_env
            )
        if self.polymarket_us_public_requests_per_second <= 0:
            errors.append("POLYMARKET_US_PUBLIC_REQUESTS_PER_SECOND must be positive")
        if self.polymarket_us_order_requests_per_second <= 0:
            errors.append("POLYMARKET_US_ORDER_REQUESTS_PER_SECOND must be positive")
        if self.polymarket_us_bbo_requests_per_minute <= 0:
            errors.append("POLYMARKET_US_BBO_REQUESTS_PER_MINUTE must be positive")
        if self.polymarket_us_position_valuation_requests_per_minute <= 0:
            errors.append(
                "POLYMARKET_US_POSITION_VALUATION_REQUESTS_PER_MINUTE must be positive"
            )
        if self.polymarket_us_enabled:
            if not self.polymarket_us_key_id:
                errors.append(
                    "POLYMARKET_US_KEY_ID is required when POLYMARKET_US_ENABLED=true"
                )
            if not self.polymarket_us_secret:
                errors.append(
                    "POLYMARKET_US_SECRET is required when POLYMARKET_US_ENABLED=true"
                )
        if self.bankroll <= 0:
            errors.append("BANKROLL must be positive, got %.2f" % self.bankroll)
        if not (0 < self.kelly_fraction <= 1.0):
            errors.append(
                "KELLY_FRACTION must be in (0, 1], got %.2f" % self.kelly_fraction
            )
        if not self.ollama_model:
            errors.append("OLLAMA_MODEL is missing or empty")
        if self.ollama_request_timeout_seconds <= 0:
            errors.append(
                "OLLAMA_REQUEST_TIMEOUT_SECONDS must be positive, got %d"
                % self.ollama_request_timeout_seconds
            )
        if self.ollama_stage_budget_seconds <= 0:
            errors.append(
                "OLLAMA_STAGE_BUDGET_SECONDS must be positive, got %d"
                % self.ollama_stage_budget_seconds
            )
        if self.ollama_stage_budget_seconds > self.ollama_request_timeout_seconds:
            errors.append(
                "OLLAMA_STAGE_BUDGET_SECONDS must be <= OLLAMA_REQUEST_TIMEOUT_SECONDS"
            )
        if self.ollama_probe_timeout_seconds <= 0:
            errors.append(
                "OLLAMA_PROBE_TIMEOUT_SECONDS must be positive, got %d"
                % self.ollama_probe_timeout_seconds
            )
        if self.llm_min_keyword_signal < 0:
            errors.append("LLM_MIN_KEYWORD_SIGNAL must be non-negative")
        for label, bands in (
            ("LLM_ALLOWED_PRICE_BANDS", self.llm_allowed_price_bands),
            ("LLM_EXCLUDED_PRICE_BANDS", self.llm_excluded_price_bands),
        ):
            for low, high in bands:
                if not (0.0 <= low <= 1.0 and 0.0 <= high <= 1.0 and low < high):
                    errors.append(
                        f"{label} entries must satisfy 0.0 <= low < high <= 1.0; got ({low}, {high})"
                    )
        # Validate RSA key can be loaded (catches bad PEM before first trade)
        if self.api_key_secret:
            try:
                from cryptography.hazmat.primitives import serialization

                pem = self.api_key_secret
                if isinstance(pem, str):
                    pem = pem.replace("\\n", "\n").strip()
                    if "BEGIN" not in pem:
                        pem = (
                            "-----BEGIN RSA PRIVATE KEY-----\n"
                            + pem
                            + "\n-----END RSA PRIVATE KEY-----"
                        )
                    pem = pem.encode()
                serialization.load_pem_private_key(pem, password=None)
            except Exception as exc:
                errors.append(
                    "KALSHI_API_KEY_SECRET is not a valid RSA PEM key: %s" % exc
                )
        if errors:
            import sys

            for e in errors:
                print("[CONFIG ERROR] %s" % e, file=sys.stderr)
            print(
                "\nFix the above in your .env file and restart.",
                file=sys.stderr,
            )
            sys.exit(1)

    def set_paper_mode(self, paper: bool) -> None:
        """Single controlled mutation point for the paper/live flag."""
        self.is_paper_trading = paper

    @property
    def rest_base_url(self) -> str:
        return KALSHI_DEMO_REST if self.kalshi_env == "demo" else KALSHI_PROD_REST

    @property
    def ws_url(self) -> str:
        return KALSHI_DEMO_WS if self.kalshi_env == "demo" else KALSHI_PROD_WS

    @property
    def ollama_tags_url(self) -> str:
        base = self.ollama_base_url.rstrip("/")
        if base.endswith("/v1"):
            base = base[:-3]
        return f"{base}/api/tags"

    def dynamic_max_bet(self, notional_bankroll: float) -> float:
        """
        Calculate the current max bet allowed.

        Primary ceiling: max_bet_pct_bankroll * notional_bankroll (scales with bankroll).
        Safety backstop: max_bet_hard_cap (rarely binding at default $200).
        Floor: min_bet_dollars.
        """
        pct_based = self.max_bet_pct_bankroll * notional_bankroll
        return max(self.min_bet_dollars, min(self.max_bet_hard_cap, pct_based))

    # Convenience alias used in existing call sites
    @property
    def max_bet_dollars(self) -> float:
        """Static alias -- use dynamic_max_bet() for live sizing."""
        return self.max_bet_hard_cap


# Singleton instance used throughout the project
cfg = BotConfig()
