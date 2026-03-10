"""
Central configuration for the Kalshi trading bot.

All tuneable parameters and environment-variable bindings live here.
Import `cfg` from this module everywhere instead of reading env vars directly.
"""

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# Load .env from the project root
load_dotenv(Path(__file__).parent / ".env")

# ── Directories ───────────────────────────────────────────────────────────────
BASE_DIR   = Path(__file__).parent
LOGS_DIR   = BASE_DIR / "logs"
DATA_DIR   = BASE_DIR / "data"
LOGS_DIR.mkdir(exist_ok=True)
DATA_DIR.mkdir(exist_ok=True)

# ── Kalshi REST base URLs ─────────────────────────────────────────────────────
KALSHI_PROD_REST = "https://trading-api.kalshi.com/trade-api/v2"
KALSHI_DEMO_REST = "https://demo-api.kalshi.co/trade-api/v2"

KALSHI_PROD_WS   = "wss://trading-api.kalshi.com/trade-api/ws/v2"
KALSHI_DEMO_WS   = "wss://demo-api.kalshi.co/trade-api/ws/v2"

# ── RSS feeds to monitor ──────────────────────────────────────────────────────
RSS_FEEDS = [
    # Reuters world news
    "https://feeds.reuters.com/reuters/worldNews",
    "https://feeds.reuters.com/Reuters/worldNews",
    # AP News top headlines
    "https://feeds.apnews.com/rss/apf-topnews",
    "https://apnews.com/apf-topnews.rss",
    # BBC World
    "https://feeds.bbci.co.uk/news/world/rss.xml",
    # Al Jazeera
    "https://www.aljazeera.com/xml/rss/all.xml",
]

RSS_POLL_INTERVAL_SECONDS = 60   # how often to re-fetch each feed

# ── Reddit subreddits to monitor ──────────────────────────────────────────────
REDDIT_SUBREDDITS = ["worldnews", "geopolitics", "news"]
REDDIT_MIN_SCORE  = 50           # only consider posts above this upvote threshold

# ── Kalshi market filters ─────────────────────────────────────────────────────
# Categories/series slugs to look for on Kalshi
KALSHI_GEOPOLITICAL_SERIES = [
    "KXPRESGELECT",   # presidential elections
    "KXINTL",         # international events
    "KXUKR",          # Ukraine conflict
    "KXMIDEAST",      # Middle East
    "KXCHINA",        # China / Taiwan
    "KXNATO",         # NATO events
    "KXRUSSIA",       # Russia events
    "KXIRAN",         # Iran
    "KXNK",           # North Korea
]

MARKET_CACHE_TTL_SECONDS = 300   # refresh market list every 5 min
MAX_MARKET_DAYS_TO_EXPIRY = 30   # ignore markets closing more than 30 days out

# ── Signal keyword categories (used by signal_analyzer.py) ───────────────────
# Each entry: { "keywords": [...], "direction": "yes"|"no", "strength": 0-1 }
# direction = which side of a binary YES/NO contract this signal pushes
GEOPOLITICAL_SIGNALS = [
    # ── Conflict escalation → bad things more likely ──
    {"keywords": ["missile strike", "bombs", "shelling", "invasion", "military attack",
                  "war declared", "troops deployed", "offensive launched"],
     "direction": "yes", "strength": 0.15},
    {"keywords": ["ceasefire", "peace deal", "withdrawal", "troops pull back",
                  "de-escalation", "peace talks success"],
     "direction": "no", "strength": 0.12},
    # ── Elections ──
    {"keywords": ["election results", "wins election", "wins presidency", "elected president",
                  "projected winner", "victory declared"],
     "direction": "yes", "strength": 0.25},
    {"keywords": ["concedes", "loses election", "runoff", "recount ordered"],
     "direction": "no", "strength": 0.15},
    # ── Sanctions / diplomacy ──
    {"keywords": ["sanctions imposed", "embargo", "expelled ambassador",
                  "diplomatic relations severed"],
     "direction": "yes", "strength": 0.08},
    {"keywords": ["sanctions lifted", "diplomatic breakthrough",
                  "summit agreement", "deal signed"],
     "direction": "no", "strength": 0.07},
    # ── Nuclear ──
    {"keywords": ["nuclear test", "nuclear weapon", "detonation", "icbm test",
                  "ballistic missile test"],
     "direction": "yes", "strength": 0.20},
    # ── Coup / regime change ──
    {"keywords": ["coup", "overthrown", "government toppled", "military takeover",
                  "martial law"],
     "direction": "yes", "strength": 0.22},
    # ── Terrorism ──
    {"keywords": ["terror attack", "bombing", "assassination", "mass shooting",
                  "hostage"],
     "direction": "yes", "strength": 0.10},
]


@dataclass
class BotConfig:
    # Kalshi credentials
    api_key_id:     str  = field(default_factory=lambda: os.getenv("KALSHI_API_KEY_ID", ""))
    api_key_secret: str  = field(default_factory=lambda: os.getenv("KALSHI_API_KEY_SECRET", ""))
    kalshi_env:     str  = field(default_factory=lambda: os.getenv("KALSHI_ENV", "demo"))

    # Reddit credentials
    reddit_client_id:     str = field(default_factory=lambda: os.getenv("REDDIT_CLIENT_ID", ""))
    reddit_client_secret: str = field(default_factory=lambda: os.getenv("REDDIT_CLIENT_SECRET", ""))
    reddit_user_agent:    str = field(default_factory=lambda: os.getenv("REDDIT_USER_AGENT", "KalshiBot/1.0"))

    # Trading params
    bankroll:           float = field(default_factory=lambda: float(os.getenv("BANKROLL", "500")))
    max_bet_dollars:    float = field(default_factory=lambda: float(os.getenv("MAX_BET_DOLLARS", "50")))
    kelly_fraction:     float = field(default_factory=lambda: float(os.getenv("KELLY_FRACTION", "0.5")))
    min_edge:           float = field(default_factory=lambda: float(os.getenv("MIN_EDGE", "0.04")))
    paper_trading_days: int   = field(default_factory=lambda: int(os.getenv("PAPER_TRADING_DAYS", "7")))

    # Optional LLM key
    anthropic_api_key: Optional[str] = field(
        default_factory=lambda: os.getenv("ANTHROPIC_API_KEY")
    )

    # Paper-trading start time — written once on first run
    paper_start_time: Optional[datetime] = None

    @property
    def rest_base_url(self) -> str:
        return KALSHI_DEMO_REST if self.kalshi_env == "demo" else KALSHI_PROD_REST

    @property
    def ws_url(self) -> str:
        return KALSHI_DEMO_WS if self.kalshi_env == "demo" else KALSHI_PROD_WS

    @property
    def is_paper_trading(self) -> bool:
        """Returns True during the mandatory paper-trading warm-up period."""
        if self.paper_start_time is None:
            return True
        elapsed = (datetime.now(timezone.utc) - self.paper_start_time).total_seconds()
        return elapsed < self.paper_trading_days * 86_400

    @property
    def days_remaining_paper(self) -> float:
        if self.paper_start_time is None:
            return float(self.paper_trading_days)
        elapsed = (datetime.now(timezone.utc) - self.paper_start_time).total_seconds()
        remaining = self.paper_trading_days * 86_400 - elapsed
        return max(0.0, remaining / 86_400)


# Singleton instance used throughout the project
cfg = BotConfig()
