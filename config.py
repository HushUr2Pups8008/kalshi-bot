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

# ── Kalshi REST base URLs ─────────────────────────────────────────────────────
KALSHI_PROD_REST = "https://api.elections.kalshi.com/trade-api/v2"
KALSHI_DEMO_REST = "https://demo-api.kalshi.co/trade-api/v2"
KALSHI_PROD_WS   = "wss://api.elections.kalshi.com/trade-api/ws/v2"
KALSHI_DEMO_WS   = "wss://demo-api.kalshi.co/trade-api/ws/v2"

# ── RSS feeds to monitor ──────────────────────────────────────────────────────
RSS_FEEDS = [
    "https://feeds.reuters.com/reuters/worldNews",
    "https://feeds.reuters.com/Reuters/worldNews",
    "https://feeds.apnews.com/rss/apf-topnews",
    "https://apnews.com/apf-topnews.rss",
    "https://feeds.bbci.co.uk/news/world/rss.xml",
    "https://www.aljazeera.com/xml/rss/all.xml",
]
RSS_POLL_INTERVAL_SECONDS = 60

# ── Reddit subreddits to monitor ──────────────────────────────────────────────
REDDIT_SUBREDDITS = ["worldnews", "geopolitics", "news"]
REDDIT_MIN_SCORE  = 50

# ── Kalshi market filters ─────────────────────────────────────────────────────
KALSHI_GEOPOLITICAL_SERIES = [
    "KXPRESGELECT", "KXINTL", "KXUKR", "KXMIDEAST",
    "KXCHINA", "KXNATO", "KXRUSSIA", "KXIRAN", "KXNK",
]
MARKET_CACHE_TTL_SECONDS  = 300
MAX_MARKET_DAYS_TO_EXPIRY = 30

# ── Paper trading thresholds (cast a wide net for data collection) ────────────
# These are more permissive than live thresholds to maximise resolved trades
# and build source credibility data during the paper phase.
PAPER_MIN_EDGE         = 0.02   # vs live 0.04
PAPER_MIN_MATCH_SCORE  = 0.03   # vs live 0.06
PAPER_MAX_CANDIDATES   = 10     # vs live 5

# ── Source credibility settings ───────────────────────────────────────────────
CREDIBILITY_MIN_SAMPLE = 10     # trades needed before multiplier takes effect
CREDIBILITY_MIN_MULT   = 0.5    # floor multiplier (unreliable source)
CREDIBILITY_MAX_MULT   = 1.5    # ceiling multiplier (very reliable source)

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

    # Dynamic bet sizing: max_bet = min(max_bet_hard_cap, bet_pct_bankroll * notional_bankroll)
    bet_pct_bankroll:  float = field(default_factory=lambda: float(os.getenv("BET_PCT_BANKROLL", "0.05")))
    max_bet_hard_cap:  float = field(default_factory=lambda: float(os.getenv("MAX_BET_HARD_CAP", "25.0")))
    min_bet_dollars:   float = field(default_factory=lambda: float(os.getenv("MIN_BET_DOLLARS", "2.0")))

    # Optional LLM key
    anthropic_api_key: Optional[str] = field(
        default_factory=lambda: os.getenv("ANTHROPIC_API_KEY")
    )

    # Paper trading mode — True until explicitly confirmed via --go-live
    # Set at runtime by PaperTrader reading the DB flag.
    is_paper_trading: bool = True

    @property
    def rest_base_url(self) -> str:
        return KALSHI_DEMO_REST if self.kalshi_env == "demo" else KALSHI_PROD_REST

    @property
    def ws_url(self) -> str:
        return KALSHI_DEMO_WS if self.kalshi_env == "demo" else KALSHI_PROD_WS

    def dynamic_max_bet(self, notional_bankroll: float) -> float:
        """
        Calculate the current max bet allowed.

        = min(max_bet_hard_cap, bet_pct_bankroll * notional_bankroll)
        Always at least min_bet_dollars.
        """
        pct_based = self.bet_pct_bankroll * notional_bankroll
        return max(self.min_bet_dollars, min(self.max_bet_hard_cap, pct_based))

    # Convenience aliases used in existing call sites
    @property
    def max_bet_dollars(self) -> float:
        """Static alias — use dynamic_max_bet() for live sizing."""
        return self.max_bet_hard_cap


# Singleton instance used throughout the project
cfg = BotConfig()
