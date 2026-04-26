"""Sports prefix blocklist regression tests (PROFIT-EDGE-002, v0.29.57).

The MARKET_SERIES_BLOCKLIST_PREFIXES list controls which series families are
filtered out before they reach the trading pipeline. These tests pin the
prefixes that have been verified to leak sport markets into geopolitical
matching (e.g. KXPSL = Pakistan Super League cricket, surfaced 2026-04-26
via /tmp diagnosis on the no-edge investigation).
"""

import pytest

from config import MARKET_SERIES_BLOCKLIST_PREFIXES


def _is_blocked(series_ticker: str) -> bool:
    upper = series_ticker.upper()
    return any(upper.startswith(p) for p in MARKET_SERIES_BLOCKLIST_PREFIXES)


@pytest.mark.parametrize(
    "ticker, why",
    [
        # International cricket — KXPSL was the confirmed leak that prompted
        # this fix: Pakistan Super League cricket markets were reaching the
        # signal pipeline because no prefix matched.
        ("KXPSL", "Pakistan Super League cricket (confirmed leak 2026-04-26)"),
        ("KXPSL-26-PZA", "PSL cricket — full ticker form"),
        ("KXIPL", "Indian Premier League cricket"),
        ("KXIPLGAME", "IPL per-game markets"),
        ("KXWPL", "Women's Indian Premier League cricket"),
        ("KXCRICKETT20IMATCH", "Cricket T20I match"),
        # Tennis — KXATP / KXWTA distinct from KXTENNIS prefix.
        ("KXATPMATCH", "ATP men's tennis match"),
        ("KXWTAMATCH", "WTA women's tennis match"),
        # UFC distinct from KXMMA.
        ("KXUFCTITLE", "UFC weight-class title"),
        ("KXUFCFIGHT", "UFC bout"),
        # Boxing weight classes (KXWBC*) and World Baseball Classic (also KXWBC*)
        # — both sports, single prefix covers both.
        ("KXWBCLIGHTWEIGHTTITLE", "Boxing lightweight title"),
        ("KXWBCGAME", "World Baseball Classic game"),
        # Women's basketball.
        ("KXWNBA", "WNBA championship"),
        ("KXWNBAGAME", "WNBA per-game"),
        # Soccer leagues distinct from KXSOCCER / KXUCL / KXUEL.
        ("KXEPL", "English Premier League winner"),
        ("KXEPLGAME", "EPL per-game"),
        ("KXEGYPL", "Egyptian Premier League"),
        ("KXISLGAME", "Israel Super League game"),
        ("KXCHNSL", "Chinese Super League"),
        ("KXCANPLGAME", "Canadian Premier League"),
        ("KXAFCCLGAME", "AFC Champions League game"),
        ("KXUEFAGAME", "UEFA Champions League games"),
        # F1 distinct prefix from KXFORMULA (Kalshi uses both).
        ("KXF1", "Formula 1 winner"),
        ("KXF1QUALIFY", "F1 qualifying"),
        # PGA distinct from KXGOLF.
        ("KXPGAROUNDBIRDIES", "PGA round birdies"),
        # College sports.
        ("KXCFP", "College Football Playoff"),
        ("KXCFBCOTY", "College Football Coach of Year"),
        ("KXMARMAD", "March Madness"),
        # Winter Olympics distinct from KXOLYMPIC.
        ("KXWO", "Winter Olympics"),
        ("KXWOMHOCKEY", "Winter Olympics men's hockey"),
        # Esports.
        ("KXEWCLEAGUEOFLEGENDS", "Esports World Cup — League of Legends"),
        # International basketball.
        ("KXCBA", "Chinese Basketball Association"),
        ("KXNBLGAME", "Australian NBL game"),
        ("KXEUROCUPGAME", "European basketball EuroCup"),
        # Sports leader / draft / coaching markets — uniformly sports.
        ("KXLEADERMLBHITS", "MLB hits leader"),
        ("KXLEADERNFLPYDS", "NFL passing yards leader"),
        ("KXLEADERNBAREB", "NBA rebounds leader"),
        ("KXNEXTTEAMNHL", "NHL next team"),
        ("KXCOACHOUTNFL", "NFL coach out"),
        ("KXTEAMSINNBAF", "Teams in NBA finals"),
        ("KXTRADEOFFNBA", "NBA trade offseason"),
        ("KXRANKLISTFFRB", "Fantasy football RB rank"),
    ],
)
def test_sport_prefix_is_blocked(ticker: str, why: str) -> None:
    assert _is_blocked(ticker), f"expected blocked, but slipped through: {ticker} ({why})"


@pytest.mark.parametrize(
    "ticker",
    [
        # Confirm we did NOT accidentally block geo / policy markets.
        "KXSBUDGETRES-26APR-APR28",  # Budget / continuing resolution / ICE funding
        "KXTRUMPIRAN-26MAY01",       # Iran diplomacy / kinetic events
        "KXVANCEPAKISTAN-26",         # Vance Pakistan diplomacy
        "KXARMOMINF-26MAY14-T2.4",   # Armed conflict
        "KXFISAEXTEND-26APR-APR29",  # FISA extension legislation
        "KXMOCTRUMP25-26",            # Trump month-of-action
        "KXTRUMPACT-26APR19-T1",     # Executive actions
        "KXVOTESAVEAMERICA-26",      # Vote events
        "KXEFFTARIFF-26APR30-T7.5",  # Tariff schedule
        "KXCPIYOY-26APR-T4.5",       # CPI YoY release
        "KXVISITVENEZUELA-26MAY01-DJTN",  # Venezuela visit
    ],
)
def test_geo_policy_market_is_not_blocked(ticker: str) -> None:
    assert not _is_blocked(ticker), f"geo/policy market wrongly blocked: {ticker}"
