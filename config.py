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

load_dotenv(Path(__file__).parent / ".env")

# ── Directories ───────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
LOGS_DIR = BASE_DIR / "logs"
DATA_DIR = BASE_DIR / "data"
LOGS_DIR.mkdir(exist_ok=True)
DATA_DIR.mkdir(exist_ok=True)

# ── Version ───────────────────────────────────────────────────────────────────
VERSION = (BASE_DIR / "VERSION").read_text(encoding="utf-8").strip()

# ── Kalshi REST base URLs ─────────────────────────────────────────────────────
# Production URL per official OpenAPI spec (api.elections.kalshi.com)
KALSHI_PROD_REST = "https://api.elections.kalshi.com/trade-api/v2"
KALSHI_DEMO_REST = "https://demo-api.kalshi.co/trade-api/v2"
KALSHI_PROD_WS   = "wss://api.elections.kalshi.com/trade-api/ws/v2"
KALSHI_DEMO_WS   = "wss://demo-api.kalshi.co/trade-api/ws/v2"

# ── RSS feeds to monitor ──────────────────────────────────────────────────────
RSS_FEEDS = [
    # Reuters
    "https://feeds.reuters.com/reuters/worldNews",
    "https://feeds.reuters.com/reuters/topNews",
    # AP News
    "https://feeds.apnews.com/rss/apf-topnews",
    "https://feeds.apnews.com/rss/apf-intlnews",
    # BBC
    "https://feeds.bbci.co.uk/news/world/rss.xml",
    "https://feeds.bbci.co.uk/news/world/middle_east/rss.xml",
    "https://feeds.bbci.co.uk/news/world/europe/rss.xml",
    "https://feeds.bbci.co.uk/news/world/asia/rss.xml",
    # Al Jazeera
    "https://www.aljazeera.com/xml/rss/all.xml",
    # The Guardian
    "https://www.theguardian.com/world/rss",
    "https://www.theguardian.com/world/ukraine/rss",
    "https://www.theguardian.com/world/middleeast/rss",
    # NPR
    "https://feeds.npr.org/1004/rss.xml",  # World
    # Foreign Policy
    "https://foreignpolicy.com/feed/",
    # Defense One
    "https://www.defenseone.com/rss/all/",
    # Politico
    "https://rss.politico.com/politics-news.xml",
    # NY Times World
    "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
    # France 24
    "https://www.france24.com/en/rss",
    # Deutsche Welle
    "https://rss.dw.com/xml/rss-en-world",
    # Radio Free Europe
    "https://www.rferl.org/api/zyqopjmxel",
]
RSS_POLL_INTERVAL_SECONDS = 60

# Maximum age of a news item when the consumer processes it. Items older than
# this were either slow through the queue or stale when they arrived — skip
# them to avoid trading on already-priced-in information.
MAX_NEWS_AGE_SECONDS: int = int(os.getenv("MAX_NEWS_AGE_SECONDS", "300"))  # 5 min

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
FADE_PRICE_LOW_THRESHOLD:  int = int(os.getenv("FADE_PRICE_LOW_THRESHOLD",  "15"))

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
REDDIT_MIN_SCORE  = 50

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
    "elections":         ["worldpolitics", "GlobalTalk", "IRstudies"],
    "trade_economic":    ["economics", "worldpolitics", "GlobalAffairs"],
    "nuclear":           ["NorthKorea", "iran", "WarCollege"],
    "sanctions":         ["sanctions", "foreignpolicy", "IRstudies"],
    "us_domestic":       ["politics", "PoliticalDiscussion"],
    "regional_europe":   ["europe", "EasternEurope", "ukraine"],
    "regional_mideast":  ["MiddleEast", "Israel", "iran", "Syria"],
    "regional_asia":     ["China", "taiwan", "NorthKorea", "southasia", "pakistan"],
    "regional_latam":    ["LatinAmerica"],
    "regional_africa":   ["Africa"],
    "regional_eurasia":  ["Eurasia", "Turkey"],
}

# Per-topic keyword sets used to match active Kalshi market titles
REDDIT_TOPIC_KEYWORDS: dict[str, frozenset] = {
    "military_conflict": frozenset({
        "war", "invasion", "military", "ceasefire", "troops", "attack",
        "strike", "offensive", "conflict", "soldiers", "weapons",
    }),
    "elections": frozenset({
        "election", "vote", "president", "prime", "minister", "senator",
        "governor", "ballot", "polling", "runoff", "candidate",
    }),
    "trade_economic": frozenset({
        "tariff", "tariffs", "trade", "import", "export", "embargo",
        "customs", "commerce", "duty", "duties", "reciprocal", "liberation",
    }),
    "nuclear": frozenset({
        "nuclear", "icbm", "ballistic", "missile", "warhead",
        "enrichment", "uranium", "plutonium",
    }),
    "sanctions": frozenset({
        "sanctions", "sanction", "embargo", "expelled", "diplomatic",
        "ambassador", "alliance", "bilateral",
    }),
    "us_domestic": frozenset({
        "executive", "shutdown", "impeach", "congress", "senate", "cabinet",
        "supreme", "legislation", "regulation", "pardon", "indictment",
        "confirmation", "doge", "budget", "debt",
    }),
    "regional_europe": frozenset({
        "europe", "european", "ukraine", "russia", "nato", "germany",
        "france", "britain", "poland", "czech", "hungary", "moldova",
    }),
    "regional_mideast": frozenset({
        "israel", "gaza", "iran", "lebanon", "syria", "hamas", "hezbollah",
        "saudi", "iraq", "yemen", "qatar", "jordan",
    }),
    "regional_asia": frozenset({
        "china", "taiwan", "korea", "japan", "pakistan", "india", "vietnam",
        "philippines", "myanmar", "bangladesh", "tibet",
    }),
    "regional_latam": frozenset({
        "venezuela", "cuba", "mexico", "colombia", "brazil", "argentina",
        "nicaragua", "ecuador", "peru", "chile",
    }),
    "regional_africa": frozenset({
        "africa", "ethiopia", "sudan", "somalia", "nigeria", "kenya",
        "congo", "libya", "egypt", "sahel", "mali", "niger",
    }),
    "regional_eurasia": frozenset({
        "turkey", "georgia", "armenia", "azerbaijan", "kazakhstan",
        "belarus", "erdogan", "caucasus", "eurasia",
    }),
}

# Max subreddits per poll cycle (core always included; topic subs fill remaining slots)
REDDIT_MAX_SUBREDDITS: int = 20

# ── Kalshi market filters ─────────────────────────────────────────────────────
# NOTE: KALSHI_GEOPOLITICAL_SERIES is no longer used by the market cache.
# Kalshi retired these organised geo series (KXUKR, KXINTL, etc.) — there are
# no open markets under them. The market cache now uses series-title keyword
# discovery via _GEO_SERIES_KEYWORDS in analysis/market_matcher.py.
KALSHI_GEOPOLITICAL_SERIES = [
    "KXPRESGELECT", "KXINTL", "KXUKR", "KXMIDEAST",
    "KXCHINA", "KXNATO", "KXRUSSIA", "KXIRAN", "KXNK",
]

# Series ticker PREFIXES to reject — used as a second filter after keyword
# discovery to drop sports leagues from geo-relevant countries (e.g. Saudi Pro
# League matches "saudi", J-League matches "japan").
MARKET_SERIES_BLOCKLIST_PREFIXES = [
    # US major sports
    "KXNCAA",      # NCAA sports
    "KXNFL",       # NFL football
    "KXNBA",       # NBA basketball
    "KXMLB",       # MLB baseball
    "KXNHL",       # NHL hockey
    "KXMLS",       # MLS soccer
    # International soccer leagues / cups
    "KXSOCCER",    # Generic international soccer
    "KXUCL",       # UEFA Champions League
    "KXUEL",       # UEFA Europa League
    "KXSAUDIPL",   # Saudi Pro League
    "KXJLEAGUE",   # Japan J-League
    "KXJBLEAGUE",  # Japan B-League (basketball)
    "KXKLEAGUE",   # Korea K-League
    "KXLIGUE1",    # French Ligue 1
    "KXBUNDESLIGA",# German Bundesliga
    "KXVENFUTVE",  # Venezuela soccer
    # Other international sports
    "KXTENNIS",    # Tennis
    "KXGOLF",      # Golf
    "KXBOXING",    # Boxing
    "KXMMA",       # MMA / UFC
    "KXNASCAR",    # NASCAR
    "KXFORMULA",   # Formula 1
    "KXOLYMPIC",   # Olympics
    "KXBSL",       # Basketball Super Lig (Turkey)
    "KXFIBAECUP",  # FIBA Europe Cup
    # Polling / approval rating markets (not geopolitical events)
    "KXAPRPOTUS",  # Presidential approval rating polls
    "KXPOLLPOTUS", # Presidential polls
    # Central bank decision markets (monetary policy, not geopolitical events)
    "KXCBDECISION", # Central bank rate decisions (Russia, China, etc.)
    # Trump quote-prediction markets (not event markets; match any Trump headline)
    "KXTRUMPSAY",  # Will Trump say [word] this week?
    "KXTRUMPSAYMONTH",  # Will Trump say [word] this month?
    # Entertainment / crypto / weather
    "KXENTERTAIN", # Entertainment / pop culture
    "KXCRYPTO",    # Crypto price markets
    "KXWEATHER",   # Weather markets
    # Multi-event parlay packages
    "KXMVESPORT",  # Multi-event sports
    "KXMVECROSS",  # Cross-category multi-event
]

MARKET_CACHE_TTL_SECONDS  = 1800  # 30 min — geo market refresh takes ~3 min; no need to refresh every 5
MAX_MARKET_DAYS_TO_EXPIRY = 30

# ── Paper trading thresholds (cast a wide net for data collection) ────────────
# These are more permissive than live thresholds to maximise resolved trades
# and build source credibility data during the paper phase.
PAPER_MIN_EDGE                  = 0.02   # vs live 0.04
PAPER_MIN_MATCH_SCORE           = 0.03   # vs live 0.06
PAPER_MAX_CANDIDATES            = 1      # top match only -- one trade per article, clean signal
PAPER_FLAT_CONTRACTS            = 5      # flat contract count during paper training (no bankroll gating)
PAPER_BLOCK_SAME_SIDE_DUPLICATE = True   # block any same-ticker same-side position during paper phase

# ── Source credibility settings ───────────────────────────────────────────────
CREDIBILITY_MIN_SAMPLE = 10     # trades needed before multiplier takes effect
CREDIBILITY_MIN_MULT   = 0.5    # floor multiplier (unreliable source)
CREDIBILITY_MAX_MULT   = 1.5    # ceiling multiplier (very reliable source)
# Half-life (days) for exponential time decay on credibility outcomes.
# A win/loss CREDIBILITY_HALF_LIFE_DAYS days ago counts 50% as much as today's.
CREDIBILITY_HALF_LIFE_DAYS: float = 30.0

# ── Signal keyword categories ─────────────────────────────────────────────────
GEOPOLITICAL_SIGNALS = [
    {"keywords": ["missile strike", "bombs", "shelling", "invasion", "military attack",
                  "war declared", "troops deployed", "offensive launched"],
     "direction": "yes", "strength": 0.15},
    {"keywords": ["ceasefire", "peace deal", "withdrawal", "troops pull back",
                  "de-escalation", "peace talks success"],
     "direction": "no", "strength": 0.12},
    {"keywords": ["election results", "wins election", "wins presidency", "elected president",
                  "projected winner", "victory declared"],
     "direction": "yes", "strength": 0.25},
    {"keywords": ["concedes", "loses election", "runoff", "recount ordered"],
     "direction": "no", "strength": 0.15},
    {"keywords": ["sanctions imposed", "embargo", "expelled ambassador",
                  "diplomatic relations severed"],
     "direction": "yes", "strength": 0.08},
    {"keywords": ["sanctions lifted", "diplomatic breakthrough",
                  "summit agreement", "deal signed"],
     "direction": "no", "strength": 0.07},
    {"keywords": ["nuclear test", "nuclear weapon", "detonation", "icbm test",
                  "ballistic missile test"],
     "direction": "yes", "strength": 0.20},
    {"keywords": ["coup", "overthrown", "government toppled", "military takeover",
                  "martial law"],
     "direction": "yes", "strength": 0.22},
    {"keywords": ["terror attack", "bombing", "assassination", "mass shooting",
                  "hostage"],
     "direction": "yes", "strength": 0.10},
    # Trade & economic policy
    {"keywords": ["tariff imposed", "new tariff", "tariff hike", "trade war",
                  "import ban", "export ban", "trade embargo", "reciprocal tariff"],
     "direction": "yes", "strength": 0.10},
    {"keywords": ["tariff removed", "tariff reduced", "trade deal signed",
                  "trade agreement", "free trade", "tariff exemption"],
     "direction": "no", "strength": 0.08},
    # Foreign policy
    {"keywords": ["diplomatic expulsion", "ambassador recalled", "embassy closed",
                  "foreign aid suspended", "alliance withdrawal", "nato invoked",
                  "security pact", "defense agreement"],
     "direction": "yes", "strength": 0.10},
    {"keywords": ["diplomatic talks", "foreign aid restored", "embassy reopened",
                  "alliance renewed", "security guarantee"],
     "direction": "no", "strength": 0.07},
    # Domestic policy (US-focused -- Kalshi heavy on US markets)
    {"keywords": ["executive order signed", "government shutdown", "debt ceiling",
                  "impeachment vote", "cabinet fired", "attorney general fired",
                  "special counsel appointed", "national emergency declared"],
     "direction": "yes", "strength": 0.12},
    {"keywords": ["government reopened", "debt ceiling raised", "budget passed",
                  "bipartisan deal", "confirmation vote passed"],
     "direction": "no", "strength": 0.08},
]


@dataclass
class BotConfig:
    # Kalshi credentials
    api_key_id:     str = field(default_factory=lambda: os.getenv("KALSHI_API_KEY_ID", ""))
    api_key_secret: str = field(default_factory=lambda: os.getenv("KALSHI_API_KEY_SECRET", ""))
    kalshi_env:     str = field(default_factory=lambda: os.getenv("KALSHI_ENV", "demo"))

    # Live trading params
    bankroll:        float = field(default_factory=lambda: float(os.getenv("BANKROLL", "500")))
    kelly_fraction:  float = field(default_factory=lambda: float(os.getenv("KELLY_FRACTION", "0.5")))
    min_edge:        float = field(default_factory=lambda: float(os.getenv("MIN_EDGE", "0.04")))

    # Dynamic bet sizing: max_bet = min(hard_cap, max_bet_pct_bankroll * notional_bankroll)
    # max_bet_pct_bankroll is the operating ceiling (scales with bankroll).
    # max_bet_hard_cap is a safety backstop (rarely the binding constraint).
    max_bet_pct_bankroll: float = field(default_factory=lambda: float(os.getenv("MAX_BET_PCT_BANKROLL", "0.15")))
    max_bet_hard_cap:     float = field(default_factory=lambda: float(os.getenv("MAX_BET_HARD_CAP", "200.0")))
    min_bet_dollars:      float = field(default_factory=lambda: float(os.getenv("MIN_BET_DOLLARS", "2.0")))

    # Time discount parameters -- passed through to kelly_bet() to penalise bets
    # that lock up capital for months. Exponential decay with a configurable floor.
    time_discount_half_life: float = field(default_factory=lambda: float(os.getenv("TIME_DISCOUNT_HALF_LIFE", "14.0")))
    time_discount_floor:     float = field(default_factory=lambda: float(os.getenv("TIME_DISCOUNT_FLOOR", "0.20")))

    # Ticker cooldowns (configurable to tune via .env without code changes)
    live_ticker_cooldown:  int = field(default_factory=lambda: int(os.getenv("LIVE_TICKER_COOLDOWN", "600")))    # 10 min
    paper_ticker_cooldown: int = field(default_factory=lambda: int(os.getenv("PAPER_TICKER_COOLDOWN", "14400")))  # 4h

    # Go-live gate thresholds -- evaluated by --go-live before allowing confirmation.
    # Set these to 0/1.0 to disable any individual gate.
    go_live_min_resolved:    int   = field(default_factory=lambda: int(os.getenv("GO_LIVE_MIN_RESOLVED", "20")))
    go_live_min_win_rate:    float = field(default_factory=lambda: float(os.getenv("GO_LIVE_MIN_WIN_RATE", "0.52")))
    go_live_max_drawdown_pct: float = field(default_factory=lambda: float(os.getenv("GO_LIVE_MAX_DRAWDOWN_PCT", "0.20")))

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
    reddit_client_id:     Optional[str] = field(default_factory=lambda: os.getenv("REDDIT_CLIENT_ID"))
    reddit_client_secret: Optional[str] = field(default_factory=lambda: os.getenv("REDDIT_CLIENT_SECRET"))
    reddit_user_agent:    Optional[str] = field(default_factory=lambda: os.getenv("REDDIT_USER_AGENT"))

    @property
    def reddit_oauth_available(self) -> bool:
        return bool(self.reddit_client_id and self.reddit_client_secret)

    # Local Ollama settings (primary LLM backend)
    ollama_base_url: str = field(default_factory=lambda: os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1"))
    ollama_model:    str = field(default_factory=lambda: os.getenv("OLLAMA_MODEL", "qwen2.5:3b"))

    # Paper trading mode — True until explicitly confirmed via --go-live
    # Set at runtime via set_paper_mode() — do not mutate directly.
    is_paper_trading: bool = True

    def __post_init__(self) -> None:
        """Validate critical config at startup -- fail fast, not hours later."""
        errors: list[str] = []
        if not self.api_key_id:
            errors.append("KALSHI_API_KEY_ID is missing or empty")
        if not self.api_key_secret:
            errors.append("KALSHI_API_KEY_SECRET is missing or empty")
        if self.kalshi_env not in ("demo", "prod"):
            errors.append(
                "KALSHI_ENV must be 'demo' or 'prod', got '%s'" % self.kalshi_env
            )
        if self.bankroll <= 0:
            errors.append("BANKROLL must be positive, got %.2f" % self.bankroll)
        if not (0 < self.kelly_fraction <= 1.0):
            errors.append(
                "KELLY_FRACTION must be in (0, 1], got %.2f" % self.kelly_fraction
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
