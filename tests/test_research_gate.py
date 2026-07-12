from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from analysis.research_gate import (
    ResearchEvidence,
    ResearchQuery,
    ResearchStatus,
    ResearchVerdict,
    _apply_adjudication_evidence_assessments,
    _bank_of_israel_policy_search,
    _bls_cpi_search,
    _contract_fingerprint,
    _direct_source_targets,
    _decision_grade_verdict,
    _duckduckgo_lite_search,
    _economic_stat_pending_search,
    _evidence_identity,
    _event_window_pending_search,
    _federal_register_search,
    _fed_policy_search,
    _gdpnow_search,
    _govinfo_impeachment_expungement_search,
    _has_reliable_non_pending_source_path,
    _nws_daily_climate_search,
    _nws_daily_high_temp_from_text,
    _should_direct_fetch_source,
    urllib,
    _rss_search,
    _truth_social_event_search,
    _treasury_yield_search,
    build_research_queries,
    default_search_provider,
    default_ollama_adjudicator,
    decide_research_verdict,
    market_has_research_source_path,
    run_research_gate,
    _side_aware_counter_query,
)
from analysis import research_gate as research_gate_module
from kalshi.series_metadata import SettlementSource
from tasks.research_dossier import ResearchDossierStore


@pytest.fixture(autouse=True)
def _block_unmocked_research_http(monkeypatch):
    def fail_urlopen(*_args, **_kwargs):
        raise AssertionError("research-gate tests must inject search_provider; real HTTP is blocked")

    monkeypatch.setattr(urllib.request, "urlopen", fail_urlopen)


def test_crude_oil_query_pack_targets_resolution_and_contradictions():
    news = SimpleNamespace(
        headline=(
            "Iran exports 2,300% more oil in just 6 days, still has "
            "3.6 crore barrels on water"
        ),
        source="Inshorts",
        url="https://example.com/iran-oil",
    )
    market = SimpleNamespace(
        ticker="KXIRANCRUDE-26JUL13-T3.8",
        title="Will Iran's average daily crude oil production be at least 3.8M bpd?",
        rules_primary=(
            "If Iran's average daily crude oil production for June 2026 is "
            "at least 3.8M bpd, as reported in the OPEC Monthly Oil Market Report, "
            "then the market resolves to Yes."
        ),
        rules_secondary=(
            "Underlying is Iran crude oil production based on OPEC secondary "
            "sources. Later revisions will not be considered."
        ),
        settlement_sources=(),
    )

    queries = build_research_queries(news, market)
    rendered = "\n".join(query.query for query in queries)
    intents = {query.query_intent for query in queries}

    assert "site:opec.org" in rendered
    assert "Iran" in rendered
    assert "crude oil production" in rendered
    assert "exports" in rendered
    assert "inventory" in rendered or "barrels on water" in rendered
    assert {"resolution_source", "reputable_secondary", "contradiction_check"} <= intents


def test_query_pack_includes_decision_grade_intents():
    news = SimpleNamespace(
        headline="Officials say ceasefire agreement could be signed this week",
        source="Reuters",
        url="https://reuters.com/ceasefire",
    )
    market = SimpleNamespace(
        ticker="KXCEASEFIRE-26JUL01",
        title="Will a ceasefire agreement be signed by July 1?",
        rules_primary="The market resolves Yes if an agreement is signed by the deadline.",
        rules_secondary="The determination will be based on official public announcements.",
        settlement_sources=(),
        contract_terms_url="https://kalshi.com/markets/KXCEASEFIRE-26JUL01",
    )

    intents = {query.query_intent for query in build_research_queries(news, market)}

    assert {
        "supporting",
        "disconfirming",
        "base_rate",
        "official_resolution",
        "rules",
        "market_price",
        "staleness_check",
    } <= intents


def test_gdp_threshold_parser_handles_more_than_without_cpi_false_positive():
    text = "Will **real GDP** increase by more than 2.0% in Q2 2026?"

    assert research_gate_module._gdp_threshold_from_text(text) == pytest.approx(2.0)
    assert research_gate_module._cpi_threshold_from_text(text) is None


def test_query_pack_adds_official_source_hints_for_government_resolution_paths():
    cases = [
        (
            "KXEXPUNGEIMPEACH-26JUN-26AUG01",
            "Will Trump impeachments be expunged before Aug 1, 2026?",
            (
                "If legislation that purports to expunge the December 18, 2019, "
                "and January 13, 2021, impeachments of President Donald Trump "
                "passed the House before Aug 1, 2026, then the market resolves Yes."
            ),
            {"site:congress.gov"},
        ),
        (
            "KXSTOPTRADE-26AUG01",
            "Will Donald Trump issue a comprehensive trade embargo before Aug 1, 2026?",
            (
                "If Donald Trump has taken any executive action imposing a "
                "comprehensive trade embargo on a new foreign country, then the "
                "market resolves Yes."
            ),
            {"site:federalregister.gov", "site:whitehouse.gov"},
        ),
        (
            "KXPARDONSTRUMP-26JUL-1",
            "Will exactly 1 people be pardoned in July 2026?",
            (
                "If the President pardons, commutes the sentence of, or gives "
                "reprieve to exactly 1 persons in July 2026, then the market resolves Yes."
            ),
            {"site:justice.gov", "site:whitehouse.gov"},
        ),
        (
            "KXNEWDEAL-AUG01",
            "When will Trump announce a new trade deal?",
            (
                "If the President announces a new free trade agreement, trade "
                "framework, or economic agreement before Aug 1, 2026, then the "
                "market resolves Yes."
            ),
            {"site:ustr.gov", "site:whitehouse.gov"},
        ),
        (
            "KXCBDISRAEL-26JUL06-H25",
            "Will the Bank of Israel Hike 1-25bps at the Monetary Committee meeting?",
            (
                "If the Bank of Israel Monetary Committee raises its policy "
                "rate by 1-25bps at the Jul 6, 2026 meeting, this market resolves Yes."
            ),
            {"site:boi.org.il"},
        ),
        (
            "KXTRUMPPASSPORT-26-AUG01",
            (
                "Will it be reported that the United States Department of State "
                "issues passports with Donald Trump's face before Aug 1, 2026?"
            ),
            (
                "If the United States Department of State issues one or more "
                "United States passports where the passport contains an image "
                "or visual representation of Donald Trump's face, then the "
                "market resolves Yes."
            ),
            {"site:state.gov", "site:travel.state.gov"},
        ),
        (
            "KXSBUDGETRES-26JUN-26AUG01",
            "Will a budget resolution pass the Senate before Aug 1, 2026?",
            (
                "If a budget resolution has passed the Senate after Issuance "
                "and before Aug 1, 2026, then the market resolves to Yes."
            ),
            {"site:congress.gov", "site:senate.gov"},
        ),
    ]

    for ticker, title, rules_primary, expected_domains in cases:
        queries = build_research_queries(
            SimpleNamespace(headline="", source="research_prewarm", url=""),
            SimpleNamespace(
                ticker=ticker,
                title=title,
                rules_primary=rules_primary,
                rules_secondary="",
                settlement_sources=(),
            ),
        )
        official_queries = "\n".join(
            query.query
            for query in queries
            if query.query_intent == "official_resolution"
        )

        for domain in expected_domains:
            assert domain in official_queries


def test_query_pack_keeps_trump_passport_terms_in_required_intents():
    market = SimpleNamespace(
        ticker="KXTRUMPPASSPORT-26-AUG01",
        title=(
            "Will it be reported that the United States Department of State "
            "issues one or more United States passports to U.S. citizens where "
            "the passport contains an image or visual representation of Donald "
            "Trump's face before Aug 1, 2026?"
        ),
        rules_primary=(
            "If the Department of State issues passports containing an image "
            "or visual representation of Donald Trump's face, the market "
            "resolves Yes."
        ),
        rules_secondary="",
        settlement_sources=(),
    )

    queries = build_research_queries(
        SimpleNamespace(headline="", source="research_prewarm"),
        market,
    )
    by_intent = {query.query_intent: query.query for query in queries}

    for intent in ("supporting", "disconfirming", "base_rate"):
        query = by_intent[intent].lower()
        assert "trump" in query
        assert "passport" in query


def test_query_pack_targets_truth_social_for_trump_deleted_truths_market():
    market = SimpleNamespace(
        ticker="KXTRUMPDELETE-26JUL-A20",
        title="Will the number of Trump Truths deleted in Jul 2026 be at least 20?",
        rules_primary=(
            "If Donald Trump deletes at least 20 Truths during Jul 2026, "
            "this market resolves to Yes."
        ),
        rules_secondary="Only posts on Truth Social count for this market.",
        settlement_sources=(),
    )

    queries = build_research_queries(
        SimpleNamespace(headline="", source="research_prewarm"),
        market,
    )

    assert any(
        query.source_class == "official_primary"
        and "site:truthsocial.com" in query.query
        and "Trump Truths deleted" in query.query
        for query in queries
    )


def test_query_pack_keeps_ticker_in_official_source_hint_queries():
    market = SimpleNamespace(
        ticker="KXCBDISRAEL-26JUL06-H25",
        title="Will the Bank of Israel Hike 1-25bps at the Monetary Committee meeting?",
        rules_primary=(
            "If the Bank of Israel takes the action of Hike 1-25bps at the "
            "July Monetary Committee meeting, then the market resolves to Yes."
        ),
        rules_secondary="The market resolves based on the Bank of Israel decision.",
        settlement_sources=(),
    )

    queries = build_research_queries(
        SimpleNamespace(headline="", source="research_prewarm"),
        market,
    )

    assert any(
        query.query_intent == "official_resolution"
        and "site:boi.org.il" in query.query
        and "KXCBDISRAEL-26JUL06-H25" in query.query
        for query in queries
    )


def test_strict_selection_keeps_independent_official_source_hints():
    cases = [
        (
            "KXSTOPTRADE-26AUG01",
            "Will Donald Trump issue any executive action on imposing a comprehensive trade embargo before Aug 1, 2026?",
            (
                "If Donald Trump has taken any executive action imposing a "
                "comprehensive trade embargo on a new foreign country, then the "
                "market resolves Yes."
            ),
            (
                SettlementSource(
                    label="the White House",
                    url="https://www.whitehouse.gov",
                    domain="whitehouse.gov",
                ),
                SettlementSource(
                    label="Federal Register",
                    url="https://www.federalregister.gov/",
                    domain="federalregister.gov",
                ),
            ),
            "site:federalregister.gov",
        ),
        (
            "KXPARDONSTRUMP-26JUL-1",
            "Will exactly 1 people be pardoned in July 2026?",
            (
                "If the President pardons, commutes the sentence of, or gives "
                "reprieve to exactly 1 persons in July 2026, then the market resolves Yes."
            ),
            (
                SettlementSource(
                    label="White House",
                    url="https://whitehouse.gov/",
                    domain="whitehouse.gov",
                ),
                SettlementSource(
                    label="the Department of Justice",
                    url="https://www.justice.gov",
                    domain="justice.gov",
                ),
            ),
            "site:justice.gov",
        ),
        (
            "KXEXPUNGEIMPEACH-26JUN-26AUG01",
            (
                "Will legislation that expunges the December 18, 2019, and "
                "January 13, 2021, impeachments of President Donald Trump passed "
                "the House before Aug 1, 2026?"
            ),
            (
                "If legislation that purports to expunge the impeachments has "
                "passed the House, then the market resolves Yes."
            ),
            (
                SettlementSource(
                    label="White House",
                    url="https://whitehouse.gov/",
                    domain="whitehouse.gov",
                ),
                SettlementSource(
                    label="Library of Congress",
                    url="https://congress.gov",
                    domain="congress.gov",
                ),
            ),
            "site:govinfo.gov",
        ),
    ]

    for ticker, title, rules_primary, settlement_sources, expected_domain in cases:
        queries = build_research_queries(
            SimpleNamespace(headline="", source="research_prewarm", url=""),
            SimpleNamespace(
                ticker=ticker,
                title=title,
                rules_primary=rules_primary,
                rules_secondary="",
                settlement_sources=settlement_sources,
            ),
        )
        selected = research_gate_module._select_research_queries(
            queries,
            max_queries=8,
            require_decision_grade=True,
        )
        rendered = "\n".join(
            query.query
            for query in selected
            if query.query_intent == "official_resolution"
        )

        assert expected_domain in rendered


def test_strict_selection_keeps_budget_resolution_senate_source_hint():
    queries = build_research_queries(
        SimpleNamespace(headline="", source="research_prewarm", url=""),
        SimpleNamespace(
            ticker="KXSBUDGETRES-26JUN-26AUG01",
            title="Will a budget resolution passed the Senate before Aug 1, 2026?",
            rules_primary=(
                "If a budget resolution has passed the Senate after Issuance "
                "and before Aug 1, 2026, then the market resolves to Yes."
            ),
            rules_secondary="",
            settlement_sources=(
                SettlementSource(
                    label="White House",
                    url="https://whitehouse.gov/",
                    domain="whitehouse.gov",
                ),
            ),
        ),
    )

    selected = research_gate_module._select_research_queries(
        queries,
        max_queries=8,
        require_decision_grade=True,
    )
    rendered = "\n".join(query.query for query in selected)

    assert "site:senate.gov" in rendered
    assert "site:congress.gov" in rendered


def test_budget_resolution_official_queries_do_not_include_market_ticker():
    queries = build_research_queries(
        SimpleNamespace(headline="", source="research_prewarm", url=""),
        SimpleNamespace(
            ticker="KXSBUDGETRES-26JUN-26AUG01",
            title="Will a budget resolution passed the Senate before Aug 1, 2026?",
            rules_primary=(
                "If a budget resolution has passed the Senate after Issuance "
                "and before Aug 1, 2026, then the market resolves to Yes."
            ),
            rules_secondary="",
            settlement_sources=(),
        ),
    )

    official_queries = [
        query.query
        for query in queries
        if query.query_intent == "official_resolution"
        and ("site:senate.gov" in query.query or "site:congress.gov" in query.query)
    ]

    assert official_queries
    assert all("KXSBUDGETRES" not in query for query in official_queries)
    assert all("budget resolution" in query.lower() for query in official_queries)
    assert all("senate" in query.lower() for query in official_queries)


def test_known_direct_source_market_has_research_source_path_without_hydrated_rules():
    assert market_has_research_source_path(
        SimpleNamespace(
            ticker="KXHIGHNY-26JUL02-B99.5",
            title="Will the high temperature in NYC be below 99.5F on July 2?",
            settlement_sources=(),
            contract_terms_url="",
            rules_primary="",
            rules_secondary="",
        )
    )
    assert not market_has_research_source_path(
        SimpleNamespace(
            ticker="KXUNKNOWN-26JUL02",
            title="Will an unspecified event happen?",
            settlement_sources=(),
            contract_terms_url="",
            rules_primary="",
            rules_secondary="",
        )
    )


def test_expungement_legislation_queries_include_govinfo_official_source():
    queries = build_research_queries(
        SimpleNamespace(headline="", source="research_prewarm", url=""),
        SimpleNamespace(
            ticker="KXEXPUNGEIMPEACH-26JUN-26AUG01",
            title=(
                "Will legislation that expunges the December 18, 2019, and "
                "January 13, 2021, impeachments of President Donald Trump "
                "passed the House before Aug 1, 2026?"
            ),
            rules_primary=(
                "If legislation that purports to expunge the impeachments has "
                "passed the House, then the market resolves Yes."
            ),
            rules_secondary="",
            settlement_sources=(),
        ),
    )
    selected = research_gate_module._select_research_queries(
        queries,
        max_queries=8,
        require_decision_grade=True,
    )
    rendered = "\n".join(
        query.query
        for query in selected
        if query.query_intent == "official_resolution"
    )

    assert "site:govinfo.gov" in rendered


def test_govinfo_impeachment_expungement_search_reports_introduced_not_passed(
    monkeypatch,
):
    pages = {
        "BILLS-119hres24ih": """
            <html><body><pre>
            [Congressional Bills 119th Congress]
            [H. Res. 24 Introduced in House (IH)]
            Expunging the December 18, 2019, impeachment of President Donald John Trump.
            </pre></body></html>
        """,
        "BILLS-119hres25ih": """
            <html><body><pre>
            [Congressional Bills 119th Congress]
            [H. Res. 25 Introduced in House (IH)]
            Expunging the January 13, 2021, impeachment of President Donald John Trump.
            </pre></body></html>
        """,
    }

    class FakeResponse:
        def __init__(self, raw: str):
            self.raw = raw

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return self.raw.encode()

    def fake_urlopen(request, **_kwargs):
        url = str(getattr(request, "full_url", request))
        for key, page in pages.items():
            if key in url:
                return FakeResponse(page)
        raise AssertionError(f"unexpected URL {url}")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    evidence = _govinfo_impeachment_expungement_search(
        ResearchQuery(
            (
                "Will legislation that expunges the December 18, 2019, and "
                "January 13, 2021, impeachments of President Donald Trump "
                "passed the House before Aug 1, 2026?"
            ),
            "official_resolution",
            "official_primary",
        )
    )

    assert len(evidence) == 2
    assert {item.metric_name for item in evidence} == {
        "govinfo_bill_status_introduced"
    }
    assert {item.supports_direction for item in evidence} == {"no"}
    assert all("Introduced in House" in item.snippet for item in evidence)


def test_govinfo_introduced_status_stays_no_after_structured_labeling():
    market = SimpleNamespace(
        ticker="KXEXPUNGEIMPEACH-26JUN-26AUG01",
        title=(
            "Will legislation that expunges the December 18, 2019, and "
            "January 13, 2021, impeachments of President Donald Trump "
            "passed the House before Aug 1, 2026?"
        ),
        rules_primary=(
            "If legislation that purports to expunge the impeachments has "
            "passed the House, then the market resolves Yes."
        ),
        rules_secondary="",
    )
    evidence = [
        ResearchEvidence(
            source_class="official_primary",
            source_name="GovInfo Congressional Bills",
            source_url=(
                "https://www.govinfo.gov/content/pkg/"
                "BILLS-119hres24ih/html/BILLS-119hres24ih.htm"
            ),
            title="H.Res.24 Introduced in House: Trump impeachment expungement",
            snippet=(
                "GovInfo lists H.Res.24, expunging the December 18, 2019 "
                "impeachment of Donald Trump, as Introduced in House; "
                "the market requires House passage."
            ),
            claim_type="official_resolution",
            supports_direction="no",
            supports_confidence=0.9,
            metric_name="govinfo_bill_status_introduced",
            metric_unit="bill_status",
            extraction_confidence=0.9,
        )
    ]

    labeled = research_gate_module._apply_structured_indicator_evidence(evidence, market)

    assert labeled[0].supports_direction == "no"
    assert labeled[0].supports_confidence == 0.9


def test_govinfo_bill_status_resists_adjudicator_direction_override():
    market = SimpleNamespace(
        ticker="KXEXPUNGEIMPEACH-26JUN-26AUG01",
        title=(
            "Will legislation that expunges the December 18, 2019, and "
            "January 13, 2021, impeachments of President Donald Trump "
            "passed the House before Aug 1, 2026?"
        ),
        rules_primary=(
            "If legislation that purports to expunge the impeachments has "
            "passed the House, then the market resolves Yes."
        ),
        rules_secondary="",
    )
    evidence = [
        ResearchEvidence(
            source_class="official_primary",
            source_name="GovInfo Congressional Bills",
            source_url=(
                "https://www.govinfo.gov/content/pkg/"
                "BILLS-119hres24ih/html/BILLS-119hres24ih.htm"
            ),
            title="H.Res.24 Introduced in House: Trump impeachment expungement",
            snippet=(
                "GovInfo lists H.Res.24, expunging the December 18, 2019 "
                "impeachment of Donald Trump, as Introduced in House; "
                "the market requires House passage."
            ),
            claim_type="official_resolution",
            supports_direction="no",
            supports_confidence=0.9,
            metric_name="govinfo_bill_status_introduced",
            metric_unit="bill_status",
            extraction_confidence=0.9,
        )
    ]

    labeled = research_gate_module._apply_adjudication_evidence_assessments(
        evidence,
        {
            "evidence_assessments": [
                {
                    "ordinal": 0,
                    "supports_direction": "yes",
                    "supports_confidence": 0.8,
                }
            ]
        },
        market=market,
    )

    assert labeled[0].supports_direction == "no"
    assert labeled[0].supports_confidence == 0.9


def test_query_pack_adds_primary_media_and_regulator_hints():
    cases = [
        (
            "KXGOVERNORMENTION-26JUL03-OIL",
            "What will Wes Moore say during CBS Mornings?",
            (
                "If Wes Moore says Oil / Gas / Gasoline as part of CBS Mornings, "
                "then the market resolves to Yes. Video of CBS Mornings will be primary."
            ),
            {"site:cbsnews.com"},
        ),
        (
            "KXCRITICALITY-26AUG-RADIANT",
            "Which nuclear power companies will achieve criticality before Aug 2026?",
            (
                "If Radiant Industries achieves criticality before Aug 1, 2026, "
                "then the market resolves to Yes."
            ),
            {"site:nrc.gov"},
        ),
        (
            "KXLEAVEPOWELLGOV-26AUG01",
            (
                "Will Jerome Powell leave Member of the Board of Governors "
                "of the Federal Reserve System before Aug 1, 2026?"
            ),
            (
                "If Jerome Powell has officially announced their intention to "
                "leave the Federal Reserve Board of Governors before Aug 1, 2026, "
                "then the market resolves to Yes."
            ),
            {"site:federalreserve.gov"},
        ),
    ]

    for ticker, title, rules_primary, expected_domains in cases:
        queries = build_research_queries(
            SimpleNamespace(headline="", source="research_prewarm", url=""),
            SimpleNamespace(
                ticker=ticker,
                title=title,
                rules_primary=rules_primary,
                rules_secondary="",
                settlement_sources=(),
            ),
        )
        official_queries = "\n".join(
            query.query
            for query in queries
            if query.query_intent == "official_resolution"
        )

        for domain in expected_domains:
            assert domain in official_queries


@pytest.mark.parametrize(
    ("ticker", "title", "rules_primary", "domain", "compact_phrase"),
    (
        (
            "KXLEAVEPOWELLGOV-26AUG01",
            (
                "Will Jerome Powell leave Member of the Board of Governors "
                "of the Federal Reserve System before Aug 1, 2026?"
            ),
            "Jerome Powell must leave the Federal Reserve Board of Governors.",
            "federalreserve.gov",
            "Jerome Powell Board of Governors current member",
        ),
        (
            "KXTRUMPMENTIONB-26JUL09-WORL",
            "What will Donald Trump say during next White House livestream?",
            "Video of the next White House livestream is the primary source.",
            "whitehouse.gov",
            "Donald Trump livestream transcript remarks",
        ),
        (
            "KXAUGRECESSCAB-26AUG08-E2",
            (
                "Will the number of Cabinet nominees confirmed by the Senate be "
                "exactly 2 before the 2026 August recess?"
            ),
            "Senate confirmation determines the count.",
            "senate.gov",
            "Cabinet nominees confirmed Senate",
        ),
        (
            "KXTRUMPMENTION-26JUL15-IRAN",
            "What will Donald Trump say during Pennsylvania Defense and Innovation Summit?",
            "Video of the Pennsylvania Defense and Innovation Summit is primary.",
            "mccormick.senate.gov",
            "Pennsylvania Defense and Innovation Summit Donald Trump",
        ),
    ),
)
def test_query_pack_uses_compact_official_source_hints(
    ticker,
    title,
    rules_primary,
    domain,
    compact_phrase,
):
    queries = build_research_queries(
        SimpleNamespace(headline="", source="research_prewarm", url=""),
        SimpleNamespace(
            ticker=ticker,
            title=title,
            rules_primary=rules_primary,
            rules_secondary="",
            settlement_sources=(),
        ),
    )

    official_query = next(query.query for query in queries if f"site:{domain}" in query.query)

    assert compact_phrase in official_query
    assert ticker not in official_query
    assert len(official_query) <= 140


def test_powell_membership_hint_does_not_override_rate_policy_market():
    ticker = "KXFEDPOWELLRATE-27APR"
    title = "Will Jerome Powell announce a rate cut after the Federal Reserve meeting?"
    queries = build_research_queries(
        SimpleNamespace(headline="", source="research_prewarm", url=""),
        SimpleNamespace(
            ticker=ticker,
            title=title,
            rules_primary="The announced federal funds target range decides the market.",
            rules_secondary="",
            settlement_sources=(),
        ),
    )

    official_query = next(
        query.query for query in queries if "site:federalreserve.gov" in query.query
    )

    assert "Board of Governors current member" not in official_query
    assert ticker in official_query


@pytest.mark.parametrize(
    "title",
    (
        (
            "Will Jerome Powell and the Board of Governors leave interest rates "
            "unchanged after the Federal Reserve meeting?"
        ),
        (
            "Will Jerome Powell and the Board of Governors signal a departure "
            "from restrictive policy after the Federal Reserve meeting?"
        ),
        (
            "Will Jerome Powell and the Board of Governors step down the pace "
            "of quantitative tightening after the Federal Reserve meeting?"
        ),
    ),
)
def test_powell_membership_hint_does_not_match_policy_language(title):
    ticker = "KXFEDPOWELLRATE-27APR"
    queries = build_research_queries(
        SimpleNamespace(headline="", source="research_prewarm", url=""),
        SimpleNamespace(
            ticker=ticker,
            title=title,
            rules_primary="The announced federal funds target range decides the market.",
            rules_secondary="",
            settlement_sources=(),
        ),
    )

    official_query = next(
        query.query for query in queries if "site:federalreserve.gov" in query.query
    )

    assert "Board of Governors current member" not in official_query
    assert ticker in official_query


def test_cabinet_confirmation_hint_derives_contract_month_and_year():
    queries = build_research_queries(
        SimpleNamespace(headline="", source="research_prewarm", url=""),
        SimpleNamespace(
            ticker="KXAUGRECESSCAB-27SEP-E2",
            title=(
                "Will exactly two Cabinet nominees be confirmed by the Senate "
                "before the 2027 September recess?"
            ),
            rules_primary="Senate confirmation determines the count.",
            rules_secondary="",
            settlement_sources=(),
        ),
    )

    official_query = next(query.query for query in queries if "site:senate.gov" in query.query)

    assert "September 2027" in official_query
    assert "August 2026" not in official_query


def test_cabinet_confirmation_hint_preserves_exact_ticker_deadline():
    queries = build_research_queries(
        SimpleNamespace(headline="", source="research_prewarm", url=""),
        SimpleNamespace(
            ticker="KXAUGRECESSCAB-26AUG08-E2",
            title=(
                "Will exactly two Cabinet nominees be confirmed by the Senate "
                "before the 2026 August recess?"
            ),
            rules_primary="Senate confirmation determines the count.",
            rules_secondary="",
            settlement_sources=(),
        ),
    )

    official_query = next(query.query for query in queries if "site:senate.gov" in query.query)

    assert "August 8, 2026" in official_query
    pending = [
        evidence
        for query in queries
        for evidence in _event_window_pending_search(
            query,
            now=datetime(2026, 8, 9, tzinfo=timezone.utc),
        )
    ]
    assert not pending


def test_compact_official_hints_preserve_future_event_dates():
    cases = (
        (
            "KXLEAVEPOWELLGOV-26AUG01",
            (
                "Will Jerome Powell leave Member of the Board of Governors "
                "before Aug 1, 2026?"
            ),
            "Jerome Powell must leave the Board of Governors.",
            "federalreserve.gov",
            "August 1, 2026",
        ),
        (
            "KXTRUMPMENTION-26JUL15-IRAN",
            "What will Donald Trump say during Pennsylvania Defense and Innovation Summit?",
            "Video of the summit is primary.",
            "mccormick.senate.gov",
            "July 15, 2026",
        ),
    )

    for ticker, title, rules_primary, domain, expected_date in cases:
        queries = build_research_queries(
            SimpleNamespace(headline="", source="research_prewarm", url=""),
            SimpleNamespace(
                ticker=ticker,
                title=title,
                rules_primary=rules_primary,
                rules_secondary="",
                settlement_sources=(),
            ),
        )
        official_query = next(
            query.query for query in queries if f"site:{domain}" in query.query
        )

        assert expected_date in official_query
        assert ticker not in official_query
        assert len(official_query) <= 140


def test_decision_grade_query_intents_survive_long_contract_titles():
    long_title = (
        "Will any current member of the Democratic Senate caucus including "
        "independents who caucus with Democrats publicly states that Chuck "
        "Schumer should step down resign or be replaced as Senate Democratic "
        "leader before July 1?"
    )
    market = SimpleNamespace(
        ticker="KXDEMSCHUMER-27-JUL01",
        title=long_title,
        rules_primary="The market resolves Yes based on public statements.",
        rules_secondary="Resolution source is reputable reporting.",
        settlement_sources=(),
        contract_terms_url="",
    )

    queries = build_research_queries(
        SimpleNamespace(headline="", source="research_prewarm", url=""),
        market,
    )
    by_intent = {query.query_intent: query.query for query in queries}

    for intent in {
        "supporting",
        "disconfirming",
        "base_rate",
        "official_resolution",
        "rules",
        "market_price",
        "staleness_check",
    }:
        assert intent in by_intent
    assert "opponent objection" in by_intent["disconfirming"]
    assert "evidence against YES" in by_intent["disconfirming"]
    assert "evidence against NO" in by_intent["disconfirming"]
    assert "Kalshi market price" in by_intent["market_price"]


def test_gdp_query_pack_routes_base_rate_to_gdpnow():
    queries = build_research_queries(
        SimpleNamespace(headline="", source="research_prewarm"),
        SimpleNamespace(
            ticker="KXGDP-26JUL30-T3.5",
            title="Will annualized GDP growth be above 3.5% for 2026 Q2?",
            rules_primary="The BEA advance estimate of real GDP growth resolves this market.",
            rules_secondary="Annualized seasonally adjusted growth rate.",
            settlement_sources=(),
        ),
    )

    base_rate_queries = [
        query.query for query in queries if query.query_intent == "base_rate"
    ]

    assert any("GDPNow" in query for query in base_rate_queries)


def test_side_aware_counter_query_retains_side_marker_for_long_titles():
    market = SimpleNamespace(
        ticker="KXLONG-26JUL01",
        title=(
            "Will any current member of the Democratic Senate caucus including "
            "independents who caucus with Democrats publicly state that Chuck "
            "Schumer should step down resign or be replaced before July 1"
        ),
    )

    query = _side_aware_counter_query(market, "yes")

    assert "evidence against YES" in query.query


def test_schumer_counter_query_preserves_replacement_terms_for_long_title():
    market = SimpleNamespace(
        ticker="KXDEMSCHUMER-27-JUL01",
        title=(
            "Will any current member of the Democratic Senate caucus including "
            "independents who caucus with Democrats publicly states that Chuck "
            "Schumer should step down, resign, or be replaced as Senate "
            "Democratic Leader before Jul 1, 2026?"
        ),
    )

    query = _side_aware_counter_query(market, "yes")

    assert "Schumer" in query.query
    assert "step down" in query.query
    assert "replaced" in query.query
    assert "evidence against YES" in query.query

    queries = build_research_queries(
        SimpleNamespace(headline="", source="research_prewarm"),
        market,
    )
    disconfirming = [
        item.query for item in queries if item.query_intent == "disconfirming"
    ][0]
    assert "Schumer" in disconfirming
    assert "step down" in disconfirming
    assert "replaced" in disconfirming


def test_gdpnow_search_extracts_latest_threshold_direction(monkeypatch):
    csv_payload = (
        "observation_date,GDPNOW\n"
        "2026-01-01,1.2392\n"
        "2026-04-01,2.5438\n"
    ).encode()

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return csv_payload

    monkeypatch.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: FakeResponse())

    evidence = _gdpnow_search(
        ResearchQuery(
            "Will annualized GDP growth be above 3.5% for 2026 Q2? GDPNow",
            "base_rate",
            "reputable_secondary",
        )
    )

    assert len(evidence) == 1
    item = evidence[0]
    assert item.source_name == "FRED GDPNow"
    assert item.source_class == "specialized_data"
    assert item.claim_type == "base_rate"
    assert item.metric_name == "gdpnow_real_gdp_growth_saar"
    assert item.metric_value == pytest.approx(2.5438)
    assert item.supports_direction == "no"
    assert item.supports_confidence > 0.5


@pytest.mark.asyncio
async def test_default_provider_routes_gdp_base_rate_to_gdpnow_before_pending(
    monkeypatch,
):
    csv_payload = (
        "observation_date,GDPNOW\n"
        "2026-01-01,1.2392\n"
        "2026-04-01,1.1889\n"
    ).encode()

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return csv_payload

    monkeypatch.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: FakeResponse())

    evidence = await default_search_provider(
        ResearchQuery(
            "Will **real GDP** increase by more than 2.0% in Q2 2026? "
            "GDPNow nowcast real GDP growth SAAR base rate",
            "base_rate",
            "reputable_secondary",
        )
    )

    assert len(evidence) == 1
    assert evidence[0].source_name == "FRED GDPNow"
    assert evidence[0].metric_name == "gdpnow_real_gdp_growth_saar"
    assert evidence[0].supports_direction == "no"
    assert evidence[0].metric_value == pytest.approx(1.1889)


def test_bls_cpi_search_extracts_released_month_direction(monkeypatch):
    payload = {
        "status": "REQUEST_SUCCEEDED",
        "Results": {
            "series": [
                {
                    "seriesID": "CUSR0000SA0",
                    "data": [
                        {
                            "year": "2026",
                            "period": "M05",
                            "periodName": "May",
                            "value": "333.979",
                        },
                        {
                            "year": "2026",
                            "period": "M04",
                            "periodName": "April",
                            "value": "332.407",
                        },
                    ],
                }
            ]
        },
    }

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return json.dumps(payload).encode()

    monkeypatch.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: FakeResponse())

    evidence = _bls_cpi_search(
        ResearchQuery(
            "Will CPI rise more than 0.4% in May 2026?",
            "official_resolution",
            "official_primary",
        )
    )

    assert len(evidence) == 1
    item = evidence[0]
    assert item.source_name == "BLS CPI"
    assert item.source_class == "official_primary"
    assert item.metric_name == "cpi_monthly_change_single_decimal"
    assert item.metric_value == pytest.approx(0.5)
    assert item.supports_direction == "yes"
    assert item.supports_confidence >= 0.8


def test_bls_cpi_search_marks_future_month_pending_without_api_call(monkeypatch):
    def fail_urlopen(*_args, **_kwargs):
        raise AssertionError("future CPI period should not call BLS API")

    monkeypatch.setattr(urllib.request, "urlopen", fail_urlopen)

    evidence = _bls_cpi_search(
        ResearchQuery(
            "Will CPI rise more than 0.4% in September 2026?",
            "official_resolution",
            "official_primary",
        ),
        now=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )

    assert len(evidence) == 1
    item = evidence[0]
    assert item.source_name == "BLS CPI"
    assert item.metric_name == "cpi_official_data_pending"
    assert item.supports_direction == "neutral"
    assert "September 2026" in item.snippet


def test_fed_policy_search_marks_future_fomc_decision_pending():
    evidence = _fed_policy_search(
        ResearchQuery(
            (
                "Will the upper bound of the federal funds rate be above "
                "3.75% following the Fed's Apr 28, 2027 meeting?"
            ),
            "official_resolution",
            "official_primary",
        ),
        now=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )

    assert len(evidence) == 1
    item = evidence[0]
    assert item.source_name == "Federal Reserve"
    assert item.source_class == "official_primary"
    assert item.metric_name == "fed_decision_pending"
    assert item.supports_direction == "neutral"
    assert item.supports_confidence == 0.0
    assert "April 28, 2027" in item.snippet


def test_bank_of_israel_policy_search_marks_future_meeting_pending():
    evidence = _bank_of_israel_policy_search(
        ResearchQuery(
            (
                "Will the Bank of Israel Hike 1-25bps at the Monetary Committee "
                "meeting? If the Bank of Israel Monetary Committee raises its "
                "policy rate by 1-25bps at the Jul 6, 2026 meeting, this resolves Yes."
            ),
            "official_resolution",
            "official_primary",
        ),
        now=datetime(2026, 7, 2, tzinfo=timezone.utc),
    )

    assert len(evidence) == 1
    item = evidence[0]
    assert item.source_class == "official_primary"
    assert item.source_name == "Bank of Israel"
    assert item.metric_name == "bank_of_israel_decision_pending"
    assert item.published_at == "2026-07-06"
    assert "July 6, 2026" in item.snippet


@pytest.mark.asyncio
async def test_pending_bank_of_israel_rate_decision_stays_research_backlog():
    market = SimpleNamespace(
        ticker="KXCBDISRAEL-26JUL06-H25",
        title="Will the Bank of Israel Hike 1-25bps at the Monetary Committee meeting?",
        rules_primary=(
            "If the Bank of Israel takes the action of Hike 1-25bps at the "
            "July Monetary Committee meeting, then the market resolves to Yes."
        ),
        rules_secondary="The market resolves based on the Bank of Israel decision.",
        settlement_sources=(),
    )

    async def search_provider(query):
        return _bank_of_israel_policy_search(
            query,
            now=datetime(2026, 7, 2, tzinfo=timezone.utc),
        )

    async def adjudicator(**_kwargs):
        raise AssertionError("pending Bank of Israel meeting should not adjudicate")

    verdict = await run_research_gate(
        SimpleNamespace(headline="", source="research_prewarm"),
        market,
        model_direction="neutral",
        model_confidence=0.0,
        model_reason="scheduled research prewarm",
        yes_ask=0.45,
        no_ask=0.57,
        live_mode=False,
        search_provider=search_provider,
        adjudicator=adjudicator,
        max_queries=8,
        require_decision_grade=True,
    )

    assert verdict.status == ResearchStatus.NEEDS_RESEARCH
    assert verdict.skip_reason == "official_data_pending"
    assert any(
        item.metric_name == "bank_of_israel_decision_pending"
        for item in verdict.evidence
    )


@pytest.mark.asyncio
async def test_default_search_prefers_bank_of_israel_policy_over_generic_event_window(
    monkeypatch,
):
    monkeypatch.setattr(
        research_gate_module,
        "_bank_of_israel_policy_search",
        lambda query: _bank_of_israel_policy_search(
            query,
            now=datetime(2026, 7, 2, tzinfo=timezone.utc),
        ),
    )
    monkeypatch.setattr(research_gate_module, "_rss_search", lambda _query: [])
    evidence = await default_search_provider(
        ResearchQuery(
            (
                "site:boi.org.il KXCBDISRAEL-26JUL06-H25 Will the Bank of "
                "Israel Hike 1-25bps at the Monetary Committee meeting? "
                "official resolution current status"
            ),
            "official_resolution",
            "official_primary",
        )
    )

    assert len(evidence) == 1
    assert evidence[0].source_name == "Bank of Israel"
    assert evidence[0].metric_name == "bank_of_israel_decision_pending"


@pytest.mark.asyncio
async def test_default_search_prefers_fed_policy_and_retains_pending_with_rss(
    monkeypatch,
):
    calls: list[str] = []
    pending = ResearchEvidence(
        source_class="official_primary",
        source_name="Federal Reserve",
        source_url="https://federalreserve.gov/monetarypolicy/fomccalendars.htm",
        title="FOMC decision pending",
        snippet="The April 2027 FOMC decision has not occurred.",
        claim_type="official_resolution",
        supports_direction="neutral",
        supports_confidence=0.0,
        metric_name="fed_decision_pending",
    )
    predictive = ResearchEvidence(
        source_class="reputable_secondary",
        source_name="Reuters",
        source_url="https://reuters.com/markets/rates-outlook",
        title="Rates outlook",
        snippet="Current forecasts discuss the likely 2027 policy path.",
        claim_type="supporting",
        supports_direction="neutral",
        supports_confidence=0.0,
    )

    def fed_search(_query):
        calls.append("fed")
        return [pending]

    def generic_search(_query):
        calls.append("generic")
        return [replace(pending, metric_name="event_window_pending")]

    def rss_search(_query):
        calls.append("rss")
        return [predictive]

    monkeypatch.setattr(research_gate_module, "_fed_policy_search", fed_search)
    monkeypatch.setattr(research_gate_module, "_event_window_pending_search", generic_search)
    monkeypatch.setattr(research_gate_module, "_rss_search", rss_search)

    evidence = await default_search_provider(
        ResearchQuery(
            (
                "Will the upper bound of the federal funds rate be above 1.75% "
                "following the Fed's Apr 28, 2027 meeting?"
            ),
            "supporting",
            "reputable_secondary",
        )
    )

    assert calls == ["fed", "rss"]
    assert evidence == [pending, predictive]


@pytest.mark.asyncio
async def test_default_search_returns_nonpending_fed_evidence_without_rss(
    monkeypatch,
):
    calls: list[str] = []
    structured = ResearchEvidence(
        source_class="official_primary",
        source_name="Federal Reserve",
        source_url="https://federalreserve.gov/releases/h15/",
        title="Federal funds rate",
        snippet="The current upper bound is 4.25 percent.",
        claim_type="official_resolution",
        supports_direction="yes",
        supports_confidence=1.0,
        metric_name="fed_funds_upper_bound",
        metric_value=4.25,
    )

    def fed_search(_query):
        calls.append("fed")
        return [structured]

    def generic_search(_query):
        calls.append("generic")
        raise AssertionError("generic pending detection must follow Fed policy detection")

    def rss_search(_query):
        calls.append("rss")
        raise AssertionError("non-pending structured evidence must retain early return")

    monkeypatch.setattr(research_gate_module, "_fed_policy_search", fed_search)
    monkeypatch.setattr(research_gate_module, "_event_window_pending_search", generic_search)
    monkeypatch.setattr(research_gate_module, "_rss_search", rss_search)

    evidence = await default_search_provider(
        ResearchQuery(
            (
                "Will the upper bound of the federal funds rate be above 1.75% "
                "following the Fed's Apr 28, 2027 meeting?"
            ),
            "official_resolution",
            "official_primary",
        )
    )

    assert calls == ["fed"]
    assert evidence == [structured]


def test_treasury_yield_search_marks_same_day_data_pending():
    evidence = _treasury_yield_search(
        ResearchQuery(
            (
                "Will the yield curve par rate for 10-year U.S. treasury note "
                "be between 4.41-4.43 % on Jul 1, 2026?"
            ),
            "official_resolution",
            "official_primary",
        ),
        now=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )

    assert len(evidence) == 1
    item = evidence[0]
    assert item.metric_name == "treasury_yield_data_pending"
    assert item.source_class == "official_primary"
    assert item.supports_direction == "neutral"
    assert "July 1, 2026" in item.snippet


def test_economic_stat_pending_search_marks_future_trading_economics_release():
    evidence = _economic_stat_pending_search(
        ResearchQuery(
            (
                "Will Germany GDP growth rate YoY flash for Q2 2026 be above "
                "0.0%? KXDEGDPYOYF-26JUL30-T0.0 Trading Economics"
            ),
            "official_resolution",
            "official_primary",
        ),
        now=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )

    assert len(evidence) == 1
    item = evidence[0]
    assert item.source_name == "Economic calendar"
    assert item.source_class == "official_primary"
    assert item.metric_name == "economic_stat_data_pending"
    assert item.supports_direction == "neutral"
    assert item.supports_confidence == 0.0
    assert "July 30, 2026" in item.snippet
    assert "Germany GDP growth rate YoY flash" in item.snippet


def test_economic_stat_pending_search_marks_recent_quarter_pending():
    evidence = _economic_stat_pending_search(
        ResearchQuery(
            "Will Germany GDP growth rate QoQ for Q2 2026 be above 1.0%?",
            "official_resolution",
            "official_primary",
        ),
        now=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )

    assert len(evidence) == 1
    item = evidence[0]
    assert item.metric_name == "economic_stat_data_pending"
    assert item.published_at == "2026-Q2"
    assert "Q2 2026" in item.snippet


def test_economic_stat_pending_search_marks_bea_real_gdp_query_pending():
    evidence = _economic_stat_pending_search(
        ResearchQuery(
            "site:bea.gov Will **real GDP** increase by more than 0.5% in Q2 2026?",
            "resolution_source",
            "resolution_source",
        ),
        now=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )

    assert len(evidence) == 1
    item = evidence[0]
    assert item.source_name == "BEA GDP data"
    assert item.source_url.startswith("https://www.bea.gov/")
    assert item.metric_name == "economic_stat_data_pending"
    assert item.published_at == "2026-Q2"
    assert "real GDP" in item.snippet


def test_economic_stat_pending_search_marks_bea_rules_real_gdp_query_pending():
    evidence = _economic_stat_pending_search(
        ResearchQuery(
            (
                "Will **real GDP** increase by more than 0.5% in Q2 2026? "
                "If real GDP as measured by the BEA Advance Estimate increases "
                "by more than 0.5, the market resolves Yes."
            ),
            "official_resolution",
            "official_primary",
        ),
        now=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )

    assert len(evidence) == 1
    item = evidence[0]
    assert item.source_name == "BEA GDP data"
    assert item.metric_name == "economic_stat_data_pending"
    assert item.published_at == "2026-Q2"


def test_economic_stat_pending_search_marks_fred_nominal_gdp_pending():
    evidence = _economic_stat_pending_search(
        ResearchQuery(
            "site:fred.stlouisfed.org Will nominal U.S. GDP growth be above 1.0% in Q2 2026?",
            "resolution_source",
            "resolution_source",
        ),
        now=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )

    assert len(evidence) == 1
    item = evidence[0]
    assert item.source_name == "FRED economic data"
    assert item.source_url.startswith("https://fred.stlouisfed.org/")
    assert item.metric_name == "economic_stat_data_pending"
    assert item.published_at == "2026-Q2"
    assert "nominal U.S. GDP growth" in item.snippet


def test_economic_stat_pending_search_marks_core_pce_month_pending():
    evidence = _economic_stat_pending_search(
        ResearchQuery(
            "site:bea.gov Will the rate of core PCE inflation be above 0.3% in June 2026?",
            "resolution_source",
            "resolution_source",
        ),
        now=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )

    assert len(evidence) == 1
    item = evidence[0]
    assert item.source_name == "BEA PCE data"
    assert item.source_url.startswith("https://www.bea.gov/")
    assert item.metric_name == "economic_stat_data_pending"
    assert item.published_at == "2026-06"
    assert "core PCE inflation" in item.snippet
    assert "June 2026" in item.snippet


def test_economic_stat_pending_search_marks_effective_tariff_rate_pending():
    evidence = _economic_stat_pending_search(
        ResearchQuery(
            (
                "Will the US effective tariff rate (customs duties collected "
                "divided by nominal imports of goods, represented by "
                "B235RC1Q027SBEA divided by A255RC1Q027SBEA) for Q2 2026 "
                "be above 10%?"
            ),
            "official_resolution",
            "official_primary",
        ),
        now=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )

    assert len(evidence) == 1
    item = evidence[0]
    assert item.source_name == "FRED/BEA economic data"
    assert item.metric_name == "economic_stat_data_pending"
    assert item.published_at == "2026-Q2"
    assert "US effective tariff rate" in item.snippet


def test_nws_daily_climate_search_extracts_high_temp_direction(monkeypatch):
    raw = """
    <html><body><pre>
    CLIMATE REPORT NATIONAL WEATHER SERVICE NEW YORK, NY
    ...THE CENTRAL PARK NY CLIMATE SUMMARY FOR JUNE 30 2026...
    TEMPERATURE (F)
    TODAY
    MAXIMUM         87   1226 PM     99    1964     84      3     90
    MINIMUM         73    522 AM
    </pre></body></html>
    """

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return raw.encode()

    monkeypatch.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: FakeResponse())

    evidence = _nws_daily_climate_search(
        ResearchQuery(
            "Will the high temp in NYC be 87-88° on Jun 30, 2026?",
            "official_resolution",
            "official_primary",
        )
    )

    assert len(evidence) == 1
    item = evidence[0]
    assert item.source_name == "NWS Climatological Report"
    assert item.source_class == "official_primary"
    assert item.metric_name == "nws_daily_high_temp_f"
    assert item.metric_value == pytest.approx(87.0)
    assert item.supports_direction == "yes"
    assert item.supports_confidence >= 0.9


def test_nws_daily_climate_search_extracts_threshold_high_temp_direction(monkeypatch):
    raw = """
    <html><body><pre>
    CLIMATE REPORT NATIONAL WEATHER SERVICE NEW YORK, NY
    ...THE CENTRAL PARK NY CLIMATE SUMMARY FOR JUNE 30 2026...
    TEMPERATURE (F)
    TODAY
    MAXIMUM         87   1226 PM     99    1964     84      3     90
    MINIMUM         73    522 AM
    </pre></body></html>
    """

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return raw.encode()

    monkeypatch.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: FakeResponse())

    evidence = _nws_daily_climate_search(
        ResearchQuery(
            "Will the **high temp in NYC** be >90° on Jun 30, 2026?",
            "official_resolution",
            "official_primary",
        )
    )

    assert len(evidence) == 1
    item = evidence[0]
    assert item.metric_value == pytest.approx(87.0)
    assert item.supports_direction == "no"
    assert "at least 90F" in item.snippet


def test_nws_daily_climate_search_extracts_below_high_temp_direction(monkeypatch):
    raw = """
    <html><body><pre>
    CLIMATE REPORT NATIONAL WEATHER SERVICE NEW YORK, NY
    ...THE CENTRAL PARK NY CLIMATE SUMMARY FOR JULY 01 2026...
    TEMPERATURE (F)
    TODAY
    MAXIMUM         87   1226 PM     99    1964     84      3     90
    MINIMUM         73    522 AM
    </pre></body></html>
    """

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return raw.encode()

    monkeypatch.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: FakeResponse())

    evidence = _nws_daily_climate_search(
        ResearchQuery(
            "Will the **high temp in NYC** be <92° on Jul 1, 2026?",
            "official_resolution",
            "official_primary",
        )
    )

    assert len(evidence) == 1
    item = evidence[0]
    assert item.metric_value == pytest.approx(87.0)
    assert item.supports_direction == "yes"
    assert "below 92F" in item.snippet


def test_nws_daily_climate_search_marks_mismatched_report_date_pending(monkeypatch):
    raw = """
    <html><body><pre>
    CLIMATE REPORT NATIONAL WEATHER SERVICE NEW YORK, NY
    ...THE CENTRAL PARK NY CLIMATE SUMMARY FOR JULY 01 2026...
    TEMPERATURE (F)
    TODAY
    MAXIMUM         87   1226 PM     99    1964     84      3     90
    MINIMUM         73    522 AM
    </pre></body></html>
    """

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return raw.encode()

    monkeypatch.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: FakeResponse())

    evidence = _nws_daily_climate_search(
        ResearchQuery(
            "Will the **high temp in NYC** be <99° on Jul 2, 2026?",
            "official_resolution",
            "official_primary",
        )
    )

    assert len(evidence) == 1
    item = evidence[0]
    assert item.metric_name == "nws_daily_high_temp_pending"
    assert item.supports_direction == "neutral"
    assert item.supports_confidence == 0.0
    assert "target July 2, 2026" in item.snippet
    assert "latest official report is July 1, 2026" in item.snippet


def test_structured_weather_prefers_explicit_below_title_over_ticker_threshold():
    market = SimpleNamespace(
        ticker="KXHIGHNY-26JUL01-T92",
        title="Will the high temp in NYC be <92° on Jul 1, 2026?",
        rules_primary=(
            "If the highest temperature recorded in Central Park, New York "
            "for July 01, 2026 is below 92 degrees, this market resolves Yes."
        ),
        rules_secondary="",
    )
    evidence = [
        ResearchEvidence(
            source_class="official_primary",
            source_name="NWS Climatological Report",
            source_url="https://forecast.weather.gov/product.php?site=OKX&product=CLI&issuedby=NYC",
            title="NWS Central Park daily maximum for July 1, 2026: 87F",
            snippet=(
                "NWS Central Park climate report lists TODAY MAXIMUM 87F "
                "for July 1, 2026, versus the below 92F market range."
            ),
            claim_type="supporting",
            supports_direction="yes",
            supports_confidence=0.95,
            metric_name="nws_daily_high_temp_f",
            metric_value=87.0,
            metric_unit="fahrenheit",
            extraction_confidence=0.95,
        )
    ]

    labeled = research_gate_module._apply_structured_indicator_evidence(evidence, market)

    assert labeled[0].supports_direction == "yes"
    assert labeled[0].supports_confidence >= 0.9


def test_nws_daily_high_temp_parser_accepts_record_suffix():
    raw = """
    TEMPERATURE (F)
    TODAY
    MAXIMUM        100R   247 PM 100    1901  84     16       84
    MINIMUM         82    530 AM
    """

    assert _nws_daily_high_temp_from_text(raw) == 100.0


def test_rules_only_weather_market_adds_nws_direct_source_target():
    market = SimpleNamespace(
        ticker="KXHIGHNY-26JUL02-B99.5",
        title="Will the high temp in NYC be 99-100° on Jul 2, 2026?",
        rules_primary=(
            "If the highest temperature recorded in Central Park, New York "
            "for July 02, 2026 as reported by the National Weather Service's "
            "Climatological Report (Daily), is between 99-100°, then the "
            "market resolves to Yes."
        ),
        rules_secondary="",
        contract_terms_url="",
        settlement_sources=(),
    )

    targets = _direct_source_targets(market)

    assert (
        "https://forecast.weather.gov/product.php?site=OKX&product=CLI&issuedby=NYC",
        "resolution_source",
        "settlement_source",
    ) in targets


@pytest.mark.asyncio
async def test_structured_weather_counter_query_reuses_same_official_metric_as_countercheck():
    fresh = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    market = SimpleNamespace(
        ticker="KXHIGHNY-26JUL01-B94.5",
        title="Will the high temp in NYC be 94-95° on Jul 1, 2026?",
        rules_primary=(
            "If the highest temperature recorded in Central Park, New York "
            "for July 01, 2026 is 94-95 degrees, this market resolves Yes."
        ),
        rules_secondary="",
        settlement_sources=(
            SettlementSource(
                label="National Weather Service",
                url="https://forecast.weather.gov/product.php?site=OKX&product=CLI&issuedby=NYC",
                domain="forecast.weather.gov",
            ),
        ),
    )

    async def search_provider(query):
        if query.query_intent not in {"supporting", "disconfirming"}:
            return []
        return [
            ResearchEvidence(
                source_class="official_primary",
                source_name="NWS Climatological Report",
                source_url=(
                    "https://forecast.weather.gov/product.php"
                    "?site=OKX&product=CLI&issuedby=NYC#high-2026-07-01"
                ),
                title="NWS Central Park daily maximum for July 1, 2026: 87F",
                snippet=(
                    "NWS Central Park climate report lists TODAY MAXIMUM 87F "
                    "for July 1, 2026, versus the 94-95F market range."
                ),
                claim_type=query.query_intent,
                supports_direction="no",
                supports_confidence=0.95,
                metric_name="nws_daily_high_temp_f",
                metric_value=87.0,
                metric_unit="fahrenheit",
                extraction_confidence=0.95,
                retrieved_at=fresh,
            )
        ]

    async def adjudicator(**_kwargs):
        return {
            "direction": "no",
            "estimated_probability_yes": 0.05,
            "confidence": 0.95,
            "reason": (
                "Trade NO because official NWS data is outside the market "
                "range. Market NO price leaves positive edge after costs. "
                "Counter search reused the same official report and found no "
                "opposite settlement fact."
            ),
        }

    async def direct_fetcher(*_args):
        return ResearchEvidence(
            source_class="resolution_source",
            source_name="National Weather Service",
            source_url="https://forecast.weather.gov/product.php?site=OKX&product=CLI&issuedby=NYC",
            title="National Weather Service",
            snippet="NWS publishes the Central Park daily climate report.",
            claim_type="settlement_source",
            supports_direction="no",
            supports_confidence=0.8,
            retrieved_at=fresh,
        )

    verdict = await run_research_gate(
        SimpleNamespace(headline="", source="research_prewarm"),
        market,
        model_direction="neutral",
        model_confidence=0.0,
        model_reason="scheduled research prewarm",
        yes_ask=0.55,
        no_ask=0.50,
        live_mode=False,
        search_provider=search_provider,
        direct_fetcher=direct_fetcher,
        adjudicator=adjudicator,
        max_queries=8,
        require_decision_grade=True,
    )

    assert verdict.status == ResearchStatus.DECISION_GRADE_CANDIDATE
    assert verdict.skip_reason is None
    assert verdict.force_side == "no"
    assert any(item.claim_type == "disconfirming" for item in verdict.evidence)


@pytest.mark.asyncio
async def test_structured_weather_disconfirming_same_official_result_counts_as_neutral_countercheck(
    monkeypatch,
):
    fresh = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    raw = """
    <html><body><pre>
    CLIMATE REPORT NATIONAL WEATHER SERVICE NEW YORK, NY
    ...THE CENTRAL PARK NY CLIMATE SUMMARY FOR JULY 2 2026...
    TEMPERATURE (F)
    TODAY
    MAXIMUM         93   1226 PM     99    1964     84      9     90
    MINIMUM         73    522 AM
    </pre></body></html>
    """

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return raw.encode()

    monkeypatch.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: FakeResponse())

    market = SimpleNamespace(
        ticker="KXHIGHNY-26JUL02-T99",
        title="Will the high temp in NYC be <99° on Jul 2, 2026?",
        rules_primary=(
            "If the highest temperature recorded in Central Park, New York "
            "for July 02, 2026 is <99 degrees, this market resolves Yes."
        ),
        rules_secondary="",
        settlement_sources=(
            SettlementSource(
                label="National Weather Service",
                url="https://forecast.weather.gov/product.php?site=OKX&product=CLI&issuedby=NYC",
                domain="forecast.weather.gov",
            ),
        ),
    )

    async def adjudicator(**_kwargs):
        return {
            "direction": "yes",
            "estimated_probability_yes": 0.94,
            "confidence": 0.92,
            "reason": (
                "Trade YES because official NWS data is below the market "
                "threshold. Market YES price leaves positive edge after costs. "
                "Counter search found no contrary official high-temperature fact."
            ),
        }

    async def direct_fetcher(*_args):
        return ResearchEvidence(
            source_class="resolution_source",
            source_name="National Weather Service",
            source_url="https://forecast.weather.gov/product.php?site=OKX&product=CLI&issuedby=NYC",
            title="National Weather Service",
            snippet="NWS publishes the Central Park daily climate report.",
            claim_type="settlement_source",
            supports_direction="neutral",
            supports_confidence=0.0,
            retrieved_at=fresh,
        )

    verdict = await run_research_gate(
        SimpleNamespace(headline="", source="research_prewarm"),
        market,
        model_direction="neutral",
        model_confidence=0.0,
        model_reason="scheduled research prewarm",
        yes_ask=0.06,
        no_ask=0.95,
        live_mode=False,
        search_provider=default_search_provider,
        direct_fetcher=direct_fetcher,
        adjudicator=adjudicator,
        max_queries=8,
        require_decision_grade=True,
    )

    assert verdict.status == ResearchStatus.DECISION_GRADE_CANDIDATE
    assert verdict.skip_reason is None
    assert any(
        item.claim_type == "disconfirming" and item.supports_direction == "neutral"
        for item in verdict.evidence
    )


@pytest.mark.asyncio
async def test_structured_weather_direct_source_adds_metric_when_provider_is_empty(
    monkeypatch,
):
    raw = """
    <html><body><pre>
    CLIMATE REPORT NATIONAL WEATHER SERVICE NEW YORK, NY
    ...THE CENTRAL PARK NY CLIMATE SUMMARY FOR JUNE 30 2026...
    TEMPERATURE (F)
    TODAY
    MAXIMUM         87   1226 PM     99    1964     84      3     90
    MINIMUM         73    522 AM
    </pre></body></html>
    """

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return raw.encode()

    monkeypatch.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: FakeResponse())

    market = SimpleNamespace(
        ticker="KXHIGHNY-26JUN30-B88",
        title="Will the high temp in NYC be 87-88° on Jun 30, 2026?",
        rules_primary=(
            "If the highest temperature recorded in Central Park, New York "
            "for June 30, 2026 is 87-88 degrees, this market resolves Yes."
        ),
        rules_secondary="",
        settlement_sources=(
            SettlementSource(
                label="National Weather Service",
                url="https://forecast.weather.gov/product.php?site=OKX&product=CLI&issuedby=NYC",
                domain="forecast.weather.gov",
            ),
        ),
    )

    async def search_provider(_query):
        return []

    async def direct_fetcher(_url, _source_class, _claim_type):
        return ResearchEvidence(
            source_class="resolution_source",
            source_name="forecast.weather.gov",
            source_url="https://forecast.weather.gov/product.php?site=OKX&product=CLI&issuedby=NYC",
            title="National Weather Service",
            snippet="NWS publishes the Central Park daily climate report.",
            claim_type="settlement_source",
            supports_direction="neutral",
            supports_confidence=0.5,
        )

    verdict = await run_research_gate(
        SimpleNamespace(headline="", source="research_prewarm"),
        market,
        model_direction="neutral",
        model_confidence=0.0,
        model_reason="scheduled research prewarm",
        yes_ask=0.20,
        no_ask=0.85,
        live_mode=False,
        search_provider=search_provider,
        direct_fetcher=direct_fetcher,
        max_queries=8,
        require_decision_grade=True,
    )

    structured = [
        item for item in verdict.evidence if item.metric_name == "nws_daily_high_temp_f"
    ]
    assert structured
    assert structured[0].metric_value == pytest.approx(87.0)
    assert structured[0].supports_direction == "yes"


@pytest.mark.asyncio
async def test_structured_weather_direct_source_preserves_pending_official_data(
    monkeypatch,
):
    raw = """
    <html><body><pre>
    CLIMATE REPORT NATIONAL WEATHER SERVICE NEW YORK, NY
    ...THE CENTRAL PARK NY CLIMATE SUMMARY FOR JULY 01 2026...
    TEMPERATURE (F)
    TODAY
    MAXIMUM         87   1226 PM     99    1964     84      3     90
    MINIMUM         73    522 AM
    </pre></body></html>
    """

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return raw.encode()

    monkeypatch.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: FakeResponse())

    market = SimpleNamespace(
        ticker="KXHIGHNY-26JUL02-B99.5",
        title="Will the high temp in NYC be 99-100° on Jul 2, 2026?",
        rules_primary=(
            "If the highest temperature recorded in Central Park, New York "
            "for July 02, 2026 is 99-100 degrees, this market resolves Yes."
        ),
        rules_secondary="",
        settlement_sources=(
            SettlementSource(
                label="National Weather Service",
                url="https://forecast.weather.gov/product.php?site=OKX&product=CLI&issuedby=NYC",
                domain="forecast.weather.gov",
            ),
        ),
    )

    async def search_provider(_query):
        return []

    async def direct_fetcher(_url, _source_class, _claim_type):
        return ResearchEvidence(
            source_class="resolution_source",
            source_name="forecast.weather.gov",
            source_url="https://forecast.weather.gov/product.php?site=OKX&product=CLI&issuedby=NYC",
            title="National Weather Service",
            snippet="NWS publishes the Central Park daily climate report.",
            claim_type="settlement_source",
            supports_direction="neutral",
            supports_confidence=0.5,
        )

    verdict = await run_research_gate(
        SimpleNamespace(headline="", source="research_prewarm"),
        market,
        model_direction="neutral",
        model_confidence=0.0,
        model_reason="scheduled research prewarm",
        yes_ask=0.45,
        no_ask=0.60,
        live_mode=False,
        search_provider=search_provider,
        direct_fetcher=direct_fetcher,
        max_queries=8,
        require_decision_grade=True,
    )

    pending = [
        item
        for item in verdict.evidence
        if item.metric_name == "nws_daily_high_temp_pending"
    ]
    assert pending
    assert verdict.status == ResearchStatus.NEEDS_RESEARCH
    assert verdict.skip_reason == "official_data_pending"


def test_decision_grade_rejects_stale_retrieved_evidence_even_when_reinserted():
    fresh = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    stale = "2026-01-01T00:00:00Z"
    queries = [
        ResearchQuery("official weather report", "official_resolution", "official_primary"),
        ResearchQuery("contrary weather report", "disconfirming", "official_primary"),
    ]
    evidence = [
        ResearchEvidence(
            source_class="official_primary",
            source_name="NWS Climatological Report",
            source_url="https://forecast.weather.gov/product.php?site=OKX&product=CLI&issuedby=NYC#support",
            title="NWS Central Park daily maximum: 87F",
            snippet="Official NWS climate report lists the Central Park maximum at 87F.",
            claim_type="supporting",
            supports_direction="yes",
            supports_confidence=0.95,
            metric_name="nws_daily_high_temp_f",
            metric_value=87.0,
            metric_unit="fahrenheit",
            retrieved_at=stale,
            inserted_at=fresh,
        ),
        ResearchEvidence(
            source_class="official_primary",
            source_name="NWS Climatological Report",
            source_url="https://forecast.weather.gov/product.php?site=OKX&product=CLI&issuedby=NYC#counter",
            title="NWS Central Park preliminary correction",
            snippet=(
                "Counter search found a preliminary correction claim that could "
                "push the settlement temperature above the threshold."
            ),
            claim_type="disconfirming",
            supports_direction="no",
            supports_confidence=0.35,
            metric_name="nws_daily_high_temp_f",
            metric_value=99.0,
            metric_unit="fahrenheit",
            retrieved_at=stale,
            inserted_at=fresh,
        ),
        ResearchEvidence(
            source_class="resolution_source",
            source_name="National Weather Service",
            source_url="https://forecast.weather.gov/product.php?site=OKX&product=CLI&issuedby=NYC",
            title="National Weather Service",
            snippet="NWS publishes the Central Park daily climate report used for settlement.",
            claim_type="settlement_source",
            supports_direction="yes",
            supports_confidence=0.8,
            retrieved_at=fresh,
        ),
    ]

    verdict = decide_research_verdict(
        evidence=evidence,
        model_direction="yes",
        model_confidence=0.95,
        model_reason=(
            "Trade YES because the official NWS report lists 87F and the "
            "market ask is below estimated probability. Counter search checked "
            "the settlement source and found no contrary current fact."
        ),
        estimated_probability_yes=0.95,
        yes_ask=0.62,
        no_ask=0.39,
        live_mode=False,
        queries=queries,
        require_decision_grade=True,
    )

    assert verdict.status == ResearchStatus.NEEDS_COUNTER_EVIDENCE
    assert verdict.skip_reason == "source_freshness_ttl_exceeded"


def test_cached_evidence_retrieval_timestamp_controls_freshness():
    fresh = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    stale = "2026-01-01T00:00:00Z"
    fingerprint = "contract-fingerprint"
    evidence = [
        ResearchEvidence(
            source_class="official_primary",
            source_name="Official source",
            source_url="https://agency.gov/result",
            title="Official result",
            snippet="Official result was retrieved too long ago.",
            claim_type="supporting",
            supports_direction="yes",
            supports_confidence=0.9,
            contract_fingerprint=fingerprint,
            retrieved_at=stale,
            published_at=fresh,
            inserted_at=fresh,
        )
    ]

    usable = research_gate_module._usable_cached_evidence(evidence, fingerprint)

    assert usable == []


def test_truth_social_event_search_marks_open_endorsement_window_pending():
    evidence = _truth_social_event_search(
        ResearchQuery(
            "Will Trump endorse at least 3 people on Truth Social this week? KXTRUMPENDORSEMENTS-26JUL04-A3",
            "official_resolution",
            "official_primary",
        ),
        now=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )

    assert len(evidence) == 1
    item = evidence[0]
    assert item.source_name == "Truth Social"
    assert item.source_class == "official_primary"
    assert item.metric_name == "truth_social_window_pending"
    assert item.supports_direction == "neutral"
    assert "July 4, 2026" in item.snippet


def test_truth_social_event_search_marks_open_deleted_truths_month_pending():
    evidence = _truth_social_event_search(
        ResearchQuery(
            "Will the number of Trump Truths deleted in Jul 2026 be at least 20? "
            "Only posts on Truth Social count.",
            "official_resolution",
            "official_primary",
        ),
        now=datetime(2026, 7, 2, tzinfo=timezone.utc),
    )

    assert len(evidence) == 1
    item = evidence[0]
    assert item.source_name == "Truth Social"
    assert item.source_class == "official_primary"
    assert item.metric_name == "truth_social_window_pending"
    assert item.published_at == "2026-07-31"
    assert "July 31, 2026" in item.snippet
    assert "endorsement-count" not in item.snippet
    assert "Truth Social event window" in item.snippet


def test_event_window_pending_search_marks_visit_deadline_pending():
    evidence = _event_window_pending_search(
        ResearchQuery(
            "Will Benjamin Netanyahu visit Iran before Jul 1, 2026?",
            "official_resolution",
            "official_primary",
        ),
        now=datetime(2026, 6, 30, tzinfo=timezone.utc),
    )

    assert len(evidence) == 1
    item = evidence[0]
    assert item.metric_name == "event_window_pending"
    assert item.source_class == "official_primary"
    assert item.supports_direction == "neutral"
    assert "July 1, 2026" in item.snippet


def test_event_window_pending_search_marks_monthly_visit_count_pending():
    evidence = _event_window_pending_search(
        ResearchQuery(
            (
                "Will the number of distinct US states Donald Trump has visited "
                "be exactly 5 in Jul 2026?"
            ),
            "official_resolution",
            "official_primary",
        ),
        now=datetime(2026, 7, 3, tzinfo=timezone.utc),
    )

    assert len(evidence) == 1
    assert evidence[0].metric_name == "event_window_pending"
    assert "July 31, 2026" in evidence[0].snippet


def test_event_window_pending_search_marks_monthly_pardon_count_pending():
    evidence = _event_window_pending_search(
        ResearchQuery(
            "Will exactly 1 people be pardoned in July 2026?",
            "official_resolution",
            "official_primary",
        ),
        now=datetime(2026, 7, 3, tzinfo=timezone.utc),
    )

    assert len(evidence) == 1
    assert evidence[0].metric_name == "event_window_pending"
    assert "July 31, 2026" in evidence[0].snippet


def test_event_window_pending_search_marks_office_departure_deadline_pending():
    evidence = _event_window_pending_search(
        ResearchQuery(
            "Will Sulyok be out as President of Hungary before Aug 1, 2026?",
            "official_resolution",
            "official_primary",
        ),
        now=datetime(2026, 7, 3, tzinfo=timezone.utc),
    )

    assert len(evidence) == 1
    assert evidence[0].metric_name == "event_window_pending"
    assert "August 1, 2026" in evidence[0].snippet


def test_event_window_pending_search_marks_budget_resolution_deadline_pending():
    evidence = _event_window_pending_search(
        ResearchQuery(
            (
                "Will a budget resolution passed the Senate before Aug 1, 2026? "
                "official resolution latest"
            ),
            "official_resolution",
            "official_primary",
        ),
        now=datetime(2026, 7, 3, tzinfo=timezone.utc),
    )

    assert len(evidence) == 1
    assert evidence[0].metric_name == "event_window_pending"


def test_event_window_pending_search_marks_confirmation_deadline_pending():
    evidence = _event_window_pending_search(
        ResearchQuery(
            (
                "Will Lance Schroyer be confirmed as Director of U.S. Immigration "
                "and Customs Enforcement before Aug 1, 2026? official resolution latest"
            ),
            "official_resolution",
            "official_primary",
        ),
        now=datetime(2026, 7, 3, tzinfo=timezone.utc),
    )

    assert len(evidence) == 1
    assert evidence[0].metric_name == "event_window_pending"
    assert evidence[0].source_class == "official_primary"
    assert "August 1, 2026" in evidence[0].snippet


def test_event_window_pending_search_marks_year_first_confirmation_month_pending():
    evidence = _event_window_pending_search(
        ResearchQuery(
            (
                "Will exactly two Cabinet nominees be confirmed by the Senate "
                "before the 2026 August recess? official resolution latest"
            ),
            "official_resolution",
            "official_primary",
        ),
        now=datetime(2026, 7, 11, tzinfo=timezone.utc),
    )

    assert len(evidence) == 1
    assert evidence[0].metric_name == "event_window_pending"
    assert "August 31, 2026" in evidence[0].snippet


def test_event_window_pending_search_marks_future_gubernatorial_count_pending():
    evidence = _event_window_pending_search(
        ResearchQuery(
            (
                "Will the number of Republican governors be at least 24 once all "
                "gubernatorial elections scheduled for Nov 3, 2026 are conclusively "
                "called or certified? official resolution latest"
            ),
            "official_resolution",
            "official_primary",
        ),
        now=datetime(2026, 7, 11, tzinfo=timezone.utc),
    )

    assert len(evidence) == 1
    assert evidence[0].metric_name == "event_window_pending"
    assert "November 3, 2026" in evidence[0].snippet


@pytest.mark.parametrize(
    "query_text",
    (
        (
            "site:federalreserve.gov Jerome Powell Board of Governors current member "
            "before August 1, 2026 official resolution current status"
        ),
        (
            "site:mccormick.senate.gov Pennsylvania Defense and Innovation Summit "
            "Donald Trump July 15, 2026 official resolution current status"
        ),
    ),
)
def test_event_window_pending_search_marks_compact_future_event_queries(query_text):
    evidence = _event_window_pending_search(
        ResearchQuery(query_text, "official_resolution", "official_primary"),
        now=datetime(2026, 7, 11, tzinfo=timezone.utc),
    )

    assert len(evidence) == 1
    assert evidence[0].metric_name == "event_window_pending"


def test_nws_daily_climate_search_marks_future_high_temp_pending(monkeypatch):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return b"CLIMATE REPORT NATIONAL WEATHER SERVICE NEW YORK NY JUL 03 2026 MAXIMUM 91"

    monkeypatch.setattr(
        "analysis.research_gate.urllib.request.urlopen",
        lambda *_args, **_kwargs: FakeResponse(),
    )

    evidence = _nws_daily_climate_search(
        ResearchQuery(
            (
                "Will the high temp in NYC be 104-105F on Jul 4, 2026? "
                "official resolution latest"
            ),
            "official_resolution",
            "official_primary",
        )
    )

    assert len(evidence) == 1
    assert evidence[0].metric_name == "nws_daily_high_temp_pending"
    assert evidence[0].supports_direction == "neutral"
    assert "July 4, 2026" in evidence[0].snippet


def test_event_window_pending_search_does_not_extend_before_deadline_into_deadline_day():
    evidence = _event_window_pending_search(
        ResearchQuery(
            "Will Marco Rubio visit Iran before Jul 1, 2026?",
            "official_resolution",
            "official_primary",
        ),
        now=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )

    assert evidence == []


def test_event_window_pending_search_marks_world_cup_advance_pending():
    evidence = _event_window_pending_search(
        ResearchQuery(
            "KXWCADVANCE-26JUL02PORCRO-CRO Portugal vs Croatia World Cup To Advance",
            "official_resolution",
            "official_primary",
        ),
        now=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )

    assert len(evidence) == 1
    item = evidence[0]
    assert item.metric_name == "event_window_pending"
    assert item.source_class == "official_primary"
    assert item.supports_direction == "neutral"
    assert "July 2, 2026" in item.snippet


def test_world_cup_query_pack_includes_ticker_bearing_pending_query():
    queries = build_research_queries(
        SimpleNamespace(headline="", source="research_prewarm"),
        SimpleNamespace(
            ticker="KXWCADVANCE-26JUL02PORCRO-CRO",
            title="Portugal vs Croatia: To Advance",
            rules_primary=(
                "If Croatia advance past Portugal in the Portugal vs Croatia "
                "soccer tie in the Round Of 32 of the FIFA World Cup, then "
                "the market resolves to Yes."
            ),
            rules_secondary="",
            settlement_sources=(SettlementSource(label="ESPN", domain="espn.com"),),
        ),
    )

    assert any(
        query.query_intent == "official_resolution"
        and "KXWCADVANCE-26JUL02PORCRO-CRO" in query.query
        and "World Cup" in query.query
        for query in queries
    )


def test_event_window_pending_search_marks_future_npb_game_pending():
    evidence = _event_window_pending_search(
        ResearchQuery(
            (
                "KXNPBGAME-26JUL020500SAIFUK-SAI Saitama Seibu Lions vs "
                "Fukuoka Hawks winner?"
            ),
            "official_resolution",
            "official_primary",
        ),
        now=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )

    assert len(evidence) == 1
    item = evidence[0]
    assert item.metric_name == "event_window_pending"
    assert item.supports_direction == "neutral"
    assert "July 2, 2026" in item.snippet


def test_npb_query_pack_includes_ticker_bearing_pending_query():
    queries = build_research_queries(
        SimpleNamespace(headline="", source="research_prewarm"),
        SimpleNamespace(
            ticker="KXNPBGAME-26JUL020500SAIFUK-SAI",
            title="Saitama Seibu Lions vs Fukuoka Hawks winner?",
            rules_primary=(
                "If Saitama Seibu Lions wins the Saitama Seibu Lions vs "
                "Fukuoka Hawks Japan NPB game originally scheduled for Jul 2, "
                "2026 at 5:00 AM EDT, then the market resolves to Yes."
            ),
            rules_secondary="",
            settlement_sources=(SettlementSource(label="Flashscore", domain="flashscore.com"),),
        ),
    )

    assert any(
        query.query_intent == "official_resolution"
        and "KXNPBGAME-26JUL020500SAIFUK-SAI" in query.query
        and "NPB game" in query.query
        for query in queries
    )


def test_event_window_pending_search_marks_nasdaq_index_close_pending():
    evidence = _event_window_pending_search(
        ResearchQuery(
            (
                "KXNASDAQ100-26JUL01H1600-B28850 Will the Nasdaq-100 be "
                "between 28800 and 28899.9900 at the end of Jul 1, 2026 at 4pm EDT?"
            ),
            "official_resolution",
            "official_primary",
        ),
        now=datetime(2026, 7, 1, 3, tzinfo=timezone.utc),
    )

    assert len(evidence) == 1
    item = evidence[0]
    assert item.metric_name == "event_window_pending"
    assert item.supports_direction == "neutral"
    assert "July 1, 2026" in item.snippet


def test_nasdaq_query_pack_includes_ticker_bearing_pending_query():
    queries = build_research_queries(
        SimpleNamespace(headline="", source="research_prewarm"),
        SimpleNamespace(
            ticker="KXNASDAQ100-26JUL01H1600-B28850",
            title=(
                "Will the Nasdaq-100 be between 28800 and 28899.9900 "
                "at the end of Jul 1, 2026 at 4pm EDT?"
            ),
            rules_primary=(
                "If the end-of-day Nasdaq 100 index value on July 01, 2026 "
                "is between 28800 and 28899.9900, then the market resolves Yes."
            ),
            rules_secondary="",
            settlement_sources=(SettlementSource(label="Google Finance", domain="google.com"),),
        ),
    )

    assert any(
        query.query_intent == "official_resolution"
        and "KXNASDAQ100-26JUL01H1600-B28850" in query.query
        and "Nasdaq" in query.query
        for query in queries
    )


@pytest.mark.asyncio
async def test_pending_visit_event_stays_research_backlog_even_without_price():
    market = SimpleNamespace(
        ticker="KXVISITIRAN-26JUL01-BNET",
        title="Will Benjamin Netanyahu visit Iran before Jul 1, 2026?",
        rules_primary=(
            "If Benjamin Netanyahu has physically visited Iran before Jul 1, 2026, "
            "this market resolves to Yes."
        ),
        rules_secondary="Major reputable reporting decides this market.",
        settlement_sources=(
            SettlementSource(
                label="Associated Press",
                url="https://apnews.com/",
                domain="apnews.com",
            ),
        ),
    )

    async def search_provider(query):
        return _event_window_pending_search(
            query,
            now=datetime(2026, 6, 30, tzinfo=timezone.utc),
        )

    async def direct_fetcher(url, source_class, claim_type):
        return ResearchEvidence(
            source_class=source_class,
            source_name="AP",
            source_url=url,
            title="Associated Press News",
            snippet="AP publishes major international travel reporting.",
            claim_type=claim_type,
            retrieved_at="2026-07-01T00:00:00Z",
        )

    async def adjudicator(**_kwargs):
        raise AssertionError("pending visit event should not call adjudicator")

    verdict = await run_research_gate(
        SimpleNamespace(headline="", source="research_prewarm"),
        market,
        model_direction="neutral",
        model_confidence=0.0,
        model_reason="scheduled research prewarm",
        yes_ask=None,
        no_ask=None,
        live_mode=False,
        search_provider=search_provider,
        direct_fetcher=direct_fetcher,
        adjudicator=adjudicator,
        max_queries=8,
        require_decision_grade=True,
    )

    assert verdict.status == ResearchStatus.NEEDS_RESEARCH
    assert verdict.skip_reason == "official_data_pending"
    assert verdict.market_price is None
    assert any(item.metric_name == "event_window_pending" for item in verdict.evidence)


@pytest.mark.asyncio
async def test_pending_event_with_provider_predictive_evidence_reaches_adjudicator():
    market = SimpleNamespace(
        ticker="KXVISITIRAN-26JUL01-BNET",
        title="Will Benjamin Netanyahu visit Iran before Jul 1, 2026?",
        rules_primary=(
            "If Benjamin Netanyahu has physically visited Iran before Jul 1, 2026, "
            "this market resolves to Yes."
        ),
        rules_secondary="Major reputable reporting decides this market.",
        settlement_sources=(
            SettlementSource(
                label="Associated Press",
                url="https://apnews.com/",
                domain="apnews.com",
            ),
        ),
    )
    adjudication_evidence: list[list[ResearchEvidence]] = []

    async def search_provider(query):
        pending = _event_window_pending_search(
            query,
            now=datetime(2026, 6, 30, tzinfo=timezone.utc),
        )
        predictive = ResearchEvidence(
            source_class="reputable_secondary",
            source_name="Reuters",
            source_url=f"https://reuters.com/world/visit-{query.query_intent}",
            title="Current diplomatic reporting",
            snippet="Current reporting discusses whether the visit is likely before the deadline.",
            claim_type=query.query_intent,
            supports_direction="neutral",
            supports_confidence=0.0,
            retrieved_at="2026-06-30T12:00:00Z",
        )
        official_predictive = ResearchEvidence(
            source_class="official_primary",
            source_name="Associated Press",
            source_url=f"https://apnews.com/visit-{query.query_intent}",
            title="Current official-source reporting",
            snippet="Current reporting tracks whether the visit will occur.",
            claim_type=query.query_intent,
            supports_direction="neutral",
            supports_confidence=0.0,
            retrieved_at="2026-06-30T12:00:00Z",
        )
        return [*pending, predictive, official_predictive]

    async def direct_fetcher(url, source_class, claim_type):
        return ResearchEvidence(
            source_class=source_class,
            source_name="AP",
            source_url=url,
            title="Associated Press News",
            snippet="AP publishes major international travel reporting.",
            claim_type=claim_type,
            retrieved_at="2026-06-30T12:00:00Z",
        )

    async def adjudicator(**kwargs):
        adjudication_evidence.append(list(kwargs["evidence"]))
        return {
            "direction": "neutral",
            "confidence": 0.0,
            "reason": "Current predictive reporting remains ambiguous.",
        }

    verdict = await run_research_gate(
        SimpleNamespace(headline="", source="research_prewarm"),
        market,
        model_direction="neutral",
        model_confidence=0.0,
        model_reason="scheduled research prewarm",
        yes_ask=0.51,
        no_ask=0.50,
        live_mode=False,
        search_provider=search_provider,
        direct_fetcher=direct_fetcher,
        adjudicator=adjudicator,
        max_queries=8,
        require_decision_grade=True,
    )

    assert adjudication_evidence
    assert any(
        item.metric_name == "event_window_pending"
        for item in adjudication_evidence[0]
    )
    assert any(
        item.source_name == "Reuters"
        for item in adjudication_evidence[0]
    )
    assert verdict.status == ResearchStatus.NEEDS_COUNTER_EVIDENCE
    assert verdict.skip_reason != "official_data_pending"


def test_pending_event_no_edge_verdict_stays_researchable():
    pending = ResearchEvidence(
        source_class="official_primary",
        source_name="Event window",
        source_url="https://kalshi.com/#pending-2026-08-31",
        title="Event window pending",
        snippet="Final settlement reporting is not complete yet.",
        claim_type="official_resolution",
        metric_name="event_window_pending",
    )
    verdict = ResearchVerdict(
        status=ResearchStatus.UNTRADEABLE,
        attempted=True,
        evidence=[pending],
        summary="Neither side clears executable edge at the current price.",
        skip_reason="no_edge",
        force_side="yes",
        estimated_probability=0.52,
        market_price=0.51,
        estimated_edge=0.0,
    )

    guarded = research_gate_module._keep_pending_no_edge_researchable(verdict)

    assert guarded.status == ResearchStatus.NEEDS_RESEARCH
    assert guarded.skip_reason == "official_data_pending"
    assert guarded.force_side is None
    assert guarded.market_price == pytest.approx(0.51)


def test_pending_context_cannot_make_untrusted_evidence_decision_grade():
    now = datetime.now(timezone.utc).isoformat()
    candidate = research_gate_module.ResearchVerdict(
        status=ResearchStatus.TRADE_CANDIDATE,
        attempted=True,
        queries=[ResearchQuery("find countercase", "disconfirming", "other")],
        evidence=[
            ResearchEvidence(
                source_class="official_primary",
                source_name="Event window",
                source_url="https://kalshi.com/#pending-2026-08-01",
                title="Event window pending",
                snippet="The final settlement event has not occurred.",
                claim_type="official_resolution",
                metric_name="event_window_pending",
                retrieved_at=now,
            ),
            ResearchEvidence(
                source_class="other",
                source_name="Unknown Support",
                source_url="https://support.invalid/prediction",
                title="Untrusted support",
                snippet="An untrusted source predicts YES.",
                claim_type="supporting",
                supports_direction="yes",
                supports_confidence=0.9,
                metric_name="cpi_monthly_change_single_decimal",
                retrieved_at=now,
            ),
            ResearchEvidence(
                source_class="other",
                source_name="Unknown Counter",
                source_url="https://counter.invalid/prediction",
                title="Untrusted counter",
                snippet="An untrusted source presents a weak NO countercase.",
                claim_type="disconfirming",
                supports_direction="no",
                supports_confidence=0.2,
                retrieved_at=now,
            ),
        ],
        force_side="yes",
        estimated_probability=0.8,
        confidence=0.8,
        market_price=0.5,
        estimated_edge=0.29,
    )

    verdict = research_gate_module._decision_grade_verdict(
        candidate,
        model_reason=(
            "Trade YES because current evidence supports the outcome while the "
            "explicit countercase is weak."
        ),
    )

    assert verdict.status == ResearchStatus.NEEDS_RESEARCH
    assert verdict.skip_reason == "no_reliable_source_path"


@pytest.mark.parametrize(
    ("source_url", "metric_value"),
    [
        ("https://evilstlouisfed.org/series/GDPNOW", 2.5),
        ("https://fred.stlouisfed.org/series/GDPNOW", "not-a-number"),
    ],
)
def test_pending_context_rejects_spoofed_gdpnow_signal(
    source_url,
    metric_value,
):
    now = datetime.now(timezone.utc).isoformat()
    candidate = research_gate_module.ResearchVerdict(
        status=ResearchStatus.TRADE_CANDIDATE,
        attempted=True,
        queries=[ResearchQuery("find countercase", "disconfirming", "other")],
        evidence=[
            ResearchEvidence(
                source_class="official_primary",
                source_name="Event window",
                source_url="https://kalshi.com/#pending-2026-08-01",
                title="Event window pending",
                snippet="The final settlement event has not occurred.",
                claim_type="official_resolution",
                metric_name="event_window_pending",
                retrieved_at=now,
            ),
            ResearchEvidence(
                source_class="specialized_data",
                source_name="Spoofed GDPNow",
                source_url=source_url,
                title="GDPNow estimate",
                snippet="A claimed GDPNow estimate supports YES.",
                claim_type="base_rate",
                supports_direction="yes",
                supports_confidence=0.9,
                metric_name="gdpnow_real_gdp_growth_saar",
                metric_value=metric_value,
                extraction_confidence=0.95,
                retrieved_at=now,
            ),
            ResearchEvidence(
                source_class="other",
                source_name="Unknown Counter",
                source_url="https://counter.invalid/prediction",
                title="Untrusted counter",
                snippet="An untrusted source presents a weak NO countercase.",
                claim_type="disconfirming",
                supports_direction="no",
                supports_confidence=0.2,
                retrieved_at=now,
            ),
        ],
        force_side="yes",
        estimated_probability=0.8,
        confidence=0.8,
        market_price=0.5,
        estimated_edge=0.29,
    )

    verdict = research_gate_module._decision_grade_verdict(
        candidate,
        model_reason=(
            "Trade YES because current evidence supports the outcome while the "
            "explicit countercase is weak."
        ),
    )

    assert verdict.status == ResearchStatus.NEEDS_RESEARCH
    assert verdict.skip_reason == "no_reliable_source_path"


@pytest.mark.asyncio
async def test_pending_treasury_yield_survives_direct_source_timeout():
    market = SimpleNamespace(
        ticker="KXTNOTED-26JUL01-B4.42",
        title=(
            "Will the yield of 10-year U.S. treasury notes be between "
            "4.41 and 4.43 on Jul 1, 2026?"
        ),
        rules_primary=(
            "If the yield curve par rate for 10-year U.S. treasury note is "
            "between 4.41-4.43 % on Jul 1, 2026, then the market resolves to Yes."
        ),
        rules_secondary="The market expires after the data release for Jul 1, 2026.",
        contract_terms_url="https://kalshi-public-docs.s3.amazonaws.com/contract_terms/TNOTE.pdf",
        settlement_sources=(
            SettlementSource(
                label="U.S. Department of the Treasury",
                url=(
                    "https://home.treasury.gov/resource-center/data-chart-center/"
                    "interest-rates/TextView?type=daily_treasury_yield_curve"
                ),
                domain="home.treasury.gov",
            ),
        ),
    )

    async def direct_fetcher(*_args):
        raise TimeoutError("treasury source slow")

    async def search_provider(query):
        return _treasury_yield_search(
            query,
            now=datetime(2026, 7, 1, tzinfo=timezone.utc),
        )

    async def adjudicator(**_kwargs):
        raise AssertionError("pending Treasury data should not call adjudicator")

    verdict = await run_research_gate(
        SimpleNamespace(headline="", source="research_prewarm"),
        market,
        model_direction="neutral",
        model_confidence=0.0,
        model_reason="scheduled research prewarm",
        yes_ask=0.57,
        no_ask=0.88,
        live_mode=False,
        search_provider=search_provider,
        direct_fetcher=direct_fetcher,
        adjudicator=adjudicator,
        max_queries=8,
        research_timeout_seconds=5.0,
        require_decision_grade=True,
    )

    assert verdict.status == ResearchStatus.NEEDS_RESEARCH
    assert verdict.skip_reason == "official_data_pending"
    assert verdict.market_price == 0.57
    assert len(verdict.research_direct_fetch_failures) == 1
    assert any(
        item.metric_name == "treasury_yield_data_pending"
        for item in verdict.evidence
    )


@pytest.mark.asyncio
async def test_pending_foreign_gdp_target_survives_direct_source_timeout():
    market = SimpleNamespace(
        ticker="KXDEGDPYOYF-26JUL30-T0.0",
        title="Will Germany GDP growth rate YoY flash for Q2 2026 be above 0.0%?",
        rules_primary=(
            "If the Germany GDP growth rate YoY flash for Q2 2026 is above "
            "0.0% as reported by Trading Economics, then this market resolves Yes."
        ),
        rules_secondary="Trading Economics economic calendar data resolves this market.",
        settlement_sources=(
            SettlementSource(
                label="Trading Economics",
                url="https://tradingeconomics.com/germany/gdp-growth-annual",
                domain="tradingeconomics.com",
            ),
        ),
    )

    async def direct_fetcher(*_args):
        raise TimeoutError("trading economics source slow")

    async def search_provider(query):
        return _economic_stat_pending_search(
            query,
            now=datetime(2026, 7, 1, tzinfo=timezone.utc),
        )

    async def adjudicator(**_kwargs):
        raise AssertionError("pending economic-stat data should not call adjudicator")

    verdict = await run_research_gate(
        SimpleNamespace(headline="", source="research_prewarm"),
        market,
        model_direction="neutral",
        model_confidence=0.0,
        model_reason="scheduled research prewarm",
        yes_ask=0.98,
        no_ask=0.12,
        live_mode=False,
        search_provider=search_provider,
        direct_fetcher=direct_fetcher,
        adjudicator=adjudicator,
        max_queries=8,
        research_timeout_seconds=5.0,
        require_decision_grade=True,
    )

    assert verdict.status == ResearchStatus.NEEDS_RESEARCH
    assert verdict.skip_reason == "official_data_pending"
    assert verdict.market_price == pytest.approx(0.98)
    assert len(verdict.research_direct_fetch_failures) == 1
    assert any(
        item.metric_name == "economic_stat_data_pending"
        for item in verdict.evidence
    )


def test_truth_social_event_search_uses_window_end_date():
    evidence = _truth_social_event_search(
        ResearchQuery(
            (
                "If Donald Trump endorses at least 3 people on Truth Social "
                "from Jun 28, 2026 through Jul 4, 2026, then the market resolves Yes."
            ),
            "official_resolution",
            "official_primary",
        ),
        now=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )

    assert len(evidence) == 1
    assert "July 4, 2026" in evidence[0].snippet
    assert "June 28, 2026" not in evidence[0].snippet


@pytest.mark.asyncio
async def test_pending_truth_social_endorsement_stays_research_backlog_without_adjudication():
    market = SimpleNamespace(
        ticker="KXTRUMPENDORSEMENTS-26JUL04-A3",
        title="Will Trump endorse at least 3 people on Truth Social this week?",
        rules_primary=(
            "If Donald Trump endorses at least 3 people on Truth Social by "
            "the end of Jul 4, 2026, this market resolves to Yes."
        ),
        rules_secondary="Only posts on Truth Social count for this market.",
        settlement_sources=(
            SettlementSource(
                label="Truth Social",
                url="https://truthsocial.com/@realDonaldTrump",
                domain="truthsocial.com",
            ),
        ),
    )

    async def direct_fetcher(url, source_class, claim_type):
        return ResearchEvidence(
            source_class=source_class,
            source_name="Truth Social",
            source_url=url,
            title="Donald J. Trump (@realDonaldTrump) - Truth Social",
            snippet="Truth Social profile page.",
            claim_type=claim_type,
            retrieved_at="2026-07-01T00:00:00Z",
        )

    async def search_provider(query):
        return _truth_social_event_search(
            query,
            now=datetime(2026, 7, 1, tzinfo=timezone.utc),
        )

    async def adjudicator(**_kwargs):
        raise AssertionError("pending Truth Social event should not call adjudicator")

    verdict = await run_research_gate(
        SimpleNamespace(headline="", source="research_prewarm"),
        market,
        model_direction="neutral",
        model_confidence=0.0,
        model_reason="scheduled research prewarm",
        yes_ask=0.01,
        no_ask=0.99,
        live_mode=False,
        search_provider=search_provider,
        direct_fetcher=direct_fetcher,
        adjudicator=adjudicator,
        max_queries=8,
        require_decision_grade=True,
    )

    assert verdict.status == ResearchStatus.NEEDS_RESEARCH
    assert verdict.skip_reason == "official_data_pending"
    assert verdict.market_price == pytest.approx(0.01)
    assert any(item.metric_name == "truth_social_window_pending" for item in verdict.evidence)


@pytest.mark.asyncio
async def test_pending_fed_target_stays_research_backlog_without_adjudication():
    market = SimpleNamespace(
        ticker="KXFED-27APR-T3.75",
        title=(
            "Will the upper bound of the federal funds rate be above 3.75% "
            "following the Fed's Apr 28, 2027 meeting?"
        ),
        rules_primary=(
            "This market resolves based on the target range announced by "
            "the Federal Open Market Committee following the Apr 28, 2027 meeting."
        ),
        rules_secondary="Federal Reserve FOMC materials decide this market.",
        settlement_sources=(
            SettlementSource(
                label="Federal Reserve",
                url="https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
                domain="federalreserve.gov",
            ),
        ),
    )

    async def direct_fetcher(url, source_class, claim_type):
        return ResearchEvidence(
            source_class=source_class,
            source_name="Federal Reserve",
            source_url=url,
            title="Meeting calendars and information",
            snippet="Federal Reserve publishes FOMC calendar and post-meeting decisions.",
            claim_type=claim_type,
            retrieved_at="2026-07-01T00:00:00Z",
        )

    async def search_provider(query):
        if "federal funds rate" not in query.query.lower():
            return []
        return _fed_policy_search(
            query,
            now=datetime(2026, 7, 1, tzinfo=timezone.utc),
        )

    async def adjudicator(**_kwargs):
        raise AssertionError("pending FOMC decision should not call adjudicator")

    verdict = await run_research_gate(
        SimpleNamespace(headline="", source="research_prewarm"),
        market,
        model_direction="neutral",
        model_confidence=0.0,
        model_reason="scheduled research prewarm",
        yes_ask=0.85,
        no_ask=0.16,
        live_mode=False,
        search_provider=search_provider,
        direct_fetcher=direct_fetcher,
        adjudicator=adjudicator,
        max_queries=8,
        require_decision_grade=True,
    )

    assert verdict.status == ResearchStatus.NEEDS_RESEARCH
    assert verdict.skip_reason == "official_data_pending"
    assert verdict.market_price == pytest.approx(0.85)
    assert any(item.metric_name == "fed_decision_pending" for item in verdict.evidence)


@pytest.mark.asyncio
async def test_pending_bls_cpi_target_stays_research_backlog_without_adjudication():
    market = SimpleNamespace(
        ticker="KXCPI-26JUN-T-0.3",
        title="Will CPI rise more than -0.3% in June 2026?",
        rules_primary=(
            "If the Consumer Price Index (CPI) increases by more than -0.3% "
            "(single-decimal) in June 2026, then the market resolves to Yes."
        ),
        rules_secondary="BLS CPI-U all urban consumers decides this market.",
        settlement_sources=(
            SettlementSource(
                label="BLS CPI",
                url="https://www.bls.gov/cpi/",
                domain="bls.gov",
            ),
        ),
    )

    async def direct_fetcher(url, source_class, claim_type):
        return ResearchEvidence(
            source_class=source_class,
            source_name="BLS",
            source_url=url,
            title="CPI Home",
            snippet="BLS publishes CPI releases.",
            claim_type=claim_type,
            retrieved_at="2026-07-01T00:00:00Z",
        )

    async def search_provider(query):
        if "CPI" not in query.query:
            return []
        return [
            ResearchEvidence(
                source_class="official_primary",
                source_name="BLS CPI",
                source_url="https://api.bls.gov/publicAPI/v2/timeseries/data/CUSR0000SA0",
                title="BLS CPI latest available month: May 2026",
                snippet="Target June 2026 CPI is not released; latest official CPI month is May 2026.",
                claim_type="official_resolution",
                supports_direction="neutral",
                supports_confidence=0.0,
                metric_name="cpi_official_data_pending",
                metric_value=None,
                retrieved_at="2026-07-01T00:00:00Z",
            )
        ]

    async def adjudicator(**_kwargs):
        raise AssertionError("pending official CPI data should not call adjudicator")

    verdict = await run_research_gate(
        SimpleNamespace(headline="", source="research_prewarm"),
        market,
        model_direction="neutral",
        model_confidence=0.0,
        model_reason="scheduled research prewarm",
        yes_ask=0.92,
        no_ask=0.09,
        live_mode=False,
        search_provider=search_provider,
        direct_fetcher=direct_fetcher,
        adjudicator=adjudicator,
        max_queries=8,
        require_decision_grade=True,
    )

    assert verdict.status == ResearchStatus.NEEDS_RESEARCH
    assert verdict.skip_reason == "official_data_pending"
    assert verdict.market_price == pytest.approx(0.92)
    assert any(item.metric_name == "cpi_official_data_pending" for item in verdict.evidence)


@pytest.mark.asyncio
async def test_pending_official_data_does_not_block_structured_forecast_decision():
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    market = SimpleNamespace(
        ticker="KXGDP-26JUL30-T3.5",
        title="Will annualized GDP growth be above 3.5% for 2026 Q2?",
        rules_primary="The market resolves based on the BEA advance estimate of real GDP growth.",
        rules_secondary="Annualized seasonally adjusted real GDP growth determines resolution.",
        contract_terms_url="https://kalshi-public-docs.s3.amazonaws.com/contract_terms/GDP.pdf",
        settlement_sources=(
            SettlementSource(
                label="BEA GDP",
                url="https://www.bea.gov/data/gdp/gross-domestic-product",
                domain="bea.gov",
            ),
        ),
    )

    async def direct_fetcher(url, source_class, claim_type):
        return ResearchEvidence(
            source_class=source_class,
            source_name="BEA",
            source_url=url,
            title="Gross Domestic Product",
            snippet="BEA publishes the advance estimate of real GDP growth.",
            claim_type=claim_type,
            supports_direction="neutral",
            supports_confidence=0.2,
            retrieved_at=now_iso,
        )

    async def search_provider(query):
        if query.query_intent == "official_resolution":
            return [
                ResearchEvidence(
                    source_class="official_primary",
                    source_name="BEA GDP",
                    source_url="https://www.bea.gov/data/gdp/gross-domestic-product#pending-2026-q2",
                    title="BEA Q2 advance estimate pending",
                    snippet=(
                        "The BEA advance estimate for 2026 Q2 GDP is not released yet; "
                        "final settlement data remains pending."
                    ),
                    claim_type=query.query_intent,
                    supports_direction="neutral",
                    supports_confidence=0.0,
                    metric_name="economic_stat_data_pending",
                    published_at="2026-Q2",
                    retrieved_at=now_iso,
                )
            ]
        if query.query_intent == "base_rate":
            return [
                ResearchEvidence(
                    source_class="specialized_data",
                    source_name="FRED GDPNow",
                    source_url="https://fred.stlouisfed.org/series/GDPNOW",
                    title="GDPNow latest observation",
                    snippet=(
                        "GDPNow latest estimate for 2026 Q2 is 2.54%, below "
                        "the 3.50% market threshold."
                    ),
                    claim_type="base_rate",
                    supports_direction="no",
                    supports_confidence=0.62,
                    metric_name="gdpnow_real_gdp_growth_saar",
                    metric_value=2.5438,
                    metric_unit="percent_saar",
                    retrieved_at=now_iso,
                )
            ]
        return []

    verdict = await run_research_gate(
        SimpleNamespace(headline="", source="research_prewarm"),
        market,
        model_direction="neutral",
        model_confidence=0.0,
        model_reason="scheduled research prewarm",
        yes_ask=0.92,
        no_ask=0.80,
        live_mode=False,
        search_provider=search_provider,
        direct_fetcher=direct_fetcher,
        adjudicator=None,
        max_queries=8,
        require_decision_grade=True,
    )

    assert verdict.status == ResearchStatus.UNTRADEABLE
    assert verdict.skip_reason == "no_edge"
    assert verdict.force_side == "no"
    assert any(
        item.metric_name == "economic_stat_data_pending" for item in verdict.evidence
    )
    assert any(
        item.metric_name == "gdpnow_real_gdp_growth_saar"
        and item.supports_direction == "no"
        for item in verdict.evidence
    )


@pytest.mark.asyncio
async def test_corroborated_secondary_yes_evidence_can_resolve_to_no_edge():
    market = SimpleNamespace(
        ticker="KXTRUMPPASSPORT-26-AUG01",
        title=(
            "Will it be reported that the United States Department of State issues "
            "one or more United States passports where the passport contains an "
            "image or visual representation of Donald Trump's face before Aug 1, 2026?"
        ),
        rules_primary=(
            "If the Department of State issues passports containing an image "
            "of Donald Trump, the market resolves Yes."
        ),
        rules_secondary="",
    )

    async def search_provider(query: ResearchQuery) -> list[ResearchEvidence]:
        if query.query_intent == "supporting":
            return [
                ResearchEvidence(
                    source_class="reputable_secondary",
                    source_name="CNN",
                    source_url="https://cnn.com/passport-trump-picture",
                    title=(
                        "US to issue passports featuring Trump's picture to "
                        "commemorate America's 250th anniversary - CNN"
                    ),
                    snippet=(
                        "US to issue passports featuring Trump's picture to "
                        "commemorate America's 250th anniversary."
                    ),
                    claim_type="supporting",
                    supports_direction="yes",
                    supports_confidence=0.75,
                ),
                ResearchEvidence(
                    source_class="reputable_secondary",
                    source_name="KGOU",
                    source_url="https://kgou.org/passport-trump-picture",
                    title=(
                        "U.S. to issue commemorative passports with Trump's "
                        "picture for America's 250th birthday - KGOU"
                    ),
                    snippet=(
                        "U.S. to issue commemorative passports with Trump's "
                        "picture for America's 250th birthday."
                    ),
                    claim_type="supporting",
                    supports_direction="yes",
                    supports_confidence=0.75,
                ),
            ]
        if query.query_intent == "disconfirming":
            return [
                ResearchEvidence(
                    source_class="reputable_secondary",
                    source_name="AP",
                    source_url="https://apnews.com/passport-countercheck",
                    title="No contrary passport cancellation report found - AP",
                    snippet=(
                        "No report says the State Department canceled the "
                        "commemorative Trump passport plan."
                    ),
                    claim_type="disconfirming",
                    supports_direction="neutral",
                    supports_confidence=0.0,
                )
            ]
        return []

    async def adjudicator(**_kwargs):
        return {
            "direction": "neutral",
            "confidence": 0.0,
            "reason": "adjudicator did not infer a directional probability",
        }

    verdict = await run_research_gate(
        SimpleNamespace(headline="", source="research_prewarm"),
        market,
        model_direction="neutral",
        model_confidence=0.0,
        model_reason="scheduled research prewarm",
        yes_ask=0.86,
        no_ask=0.20,
        live_mode=False,
        search_provider=search_provider,
        adjudicator=adjudicator,
        max_queries=8,
        require_decision_grade=True,
    )

    assert verdict.status == ResearchStatus.UNTRADEABLE
    assert verdict.skip_reason == "no_edge"
    assert verdict.force_side == "yes"
    assert verdict.estimated_probability is not None
    assert verdict.estimated_edge is not None
    assert verdict.estimated_edge < 0.0


@pytest.mark.asyncio
async def test_pending_official_data_can_become_decision_grade_with_forecast_edge():
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    market = SimpleNamespace(
        ticker="KXGDP-26JUL30-T3.5",
        title="Will annualized GDP growth be above 3.5% for 2026 Q2?",
        rules_primary="The market resolves based on the BEA advance estimate of real GDP growth.",
        rules_secondary="Annualized seasonally adjusted real GDP growth determines resolution.",
        contract_terms_url="https://kalshi-public-docs.s3.amazonaws.com/contract_terms/GDP.pdf",
        settlement_sources=(
            SettlementSource(
                label="BEA GDP",
                url="https://www.bea.gov/data/gdp/gross-domestic-product",
                domain="bea.gov",
            ),
        ),
    )

    async def direct_fetcher(url, source_class, claim_type):
        return ResearchEvidence(
            source_class=source_class,
            source_name="BEA",
            source_url=url,
            title="Gross Domestic Product",
            snippet="BEA publishes the advance estimate of real GDP growth.",
            claim_type=claim_type,
            supports_direction="neutral",
            supports_confidence=0.2,
            retrieved_at=now_iso,
        )

    async def search_provider(query):
        if query.query_intent == "official_resolution":
            return [
                ResearchEvidence(
                    source_class="official_primary",
                    source_name="BEA GDP",
                    source_url="https://www.bea.gov/data/gdp/gross-domestic-product#pending-2026-q2",
                    title="BEA Q2 advance estimate pending",
                    snippet=(
                        "The BEA advance estimate for 2026 Q2 GDP is not released yet; "
                        "final settlement data remains pending."
                    ),
                    claim_type=query.query_intent,
                    supports_direction="neutral",
                    supports_confidence=0.0,
                    metric_name="economic_stat_data_pending",
                    published_at="2026-Q2",
                    retrieved_at=now_iso,
                )
            ]
        if query.query_intent == "base_rate":
            return [
                ResearchEvidence(
                    source_class="specialized_data",
                    source_name="FRED GDPNow",
                    source_url="https://fred.stlouisfed.org/series/GDPNOW",
                    title="GDPNow latest observation",
                    snippet=(
                        "GDPNow latest estimate for 2026 Q2 is 2.54%, below "
                        "the 3.50% market threshold."
                    ),
                    claim_type="base_rate",
                    supports_direction="no",
                    supports_confidence=0.62,
                    metric_name="gdpnow_real_gdp_growth_saar",
                    metric_value=2.5438,
                    metric_unit="percent_saar",
                    retrieved_at=now_iso,
                )
            ]
        if query.query_intent in {"disconfirming", "contradiction_check"}:
            return [
                ResearchEvidence(
                    source_class="reputable_secondary",
                    source_name="Consensus GDP survey",
                    source_url="https://example.com/gdp-consensus",
                    title="Some forecasters see above-threshold Q2 GDP",
                    snippet=(
                        "A minority economist survey scenario sees 2026 Q2 GDP "
                        "above 3.5%, which is the key countercase to the GDPNow nowcast."
                    ),
                    claim_type="disconfirming",
                    supports_direction="yes",
                    supports_confidence=0.35,
                    retrieved_at=now_iso,
                )
            ]
        return []

    verdict = await run_research_gate(
        SimpleNamespace(headline="", source="research_prewarm"),
        market,
        model_direction="neutral",
        model_confidence=0.0,
        model_reason="scheduled research prewarm",
        yes_ask=0.92,
        no_ask=0.20,
        live_mode=False,
        search_provider=search_provider,
        direct_fetcher=direct_fetcher,
        adjudicator=None,
        max_queries=8,
        require_decision_grade=True,
    )

    assert verdict.status == ResearchStatus.DECISION_GRADE_CANDIDATE
    assert verdict.skip_reason is None
    assert verdict.force_side == "no"
    assert verdict.market_price == pytest.approx(0.20)
    assert verdict.estimated_edge is not None
    assert verdict.estimated_edge > 0
    assert any(
        item.metric_name == "economic_stat_data_pending" for item in verdict.evidence
    )


@pytest.mark.asyncio
async def test_structured_gdpnow_metric_prevents_ambiguous_threshold_dossier():
    news = SimpleNamespace(
        headline="scheduled research prewarm",
        source="research_prewarm",
        url="https://example.com/prewarm",
    )
    market = SimpleNamespace(
        ticker="KXGDP-26JUL30-T3.5",
        title="Will annualized GDP growth be above 3.5% for 2026 Q2?",
        rules_primary="The market resolves based on the BEA advance estimate of real GDP growth.",
        rules_secondary="Annualized seasonally adjusted real GDP growth determines resolution.",
        contract_terms_url="https://kalshi-public-docs.s3.amazonaws.com/contract_terms/GDP.pdf",
        settlement_sources=(
            SettlementSource(
                label="BEA GDP",
                url="https://www.bea.gov/data/gdp/gross-domestic-product",
                domain="bea.gov",
            ),
        ),
    )

    async def direct_fetcher(url, source_class, claim_type):
        return ResearchEvidence(
            source_class=source_class,
            source_name="BEA",
            source_url=url,
            title="Gross Domestic Product",
            snippet="BEA publishes the advance estimate of real GDP growth.",
            claim_type=claim_type,
            supports_direction="neutral",
            supports_confidence=0.2,
            retrieved_at="2026-07-01T00:00:00Z",
        )

    async def search_provider(query):
        if query.query_intent == "base_rate":
            return [
                ResearchEvidence(
                    source_class="specialized_data",
                    source_name="FRED GDPNow",
                    source_url="https://fred.stlouisfed.org/series/GDPNOW",
                    title="GDPNow latest observation",
                    snippet=(
                        "GDPNow latest estimate for 2026 Q2 is 2.54%, below "
                        "the 3.50% market threshold."
                    ),
                    claim_type="base_rate",
                    supports_direction="no",
                    supports_confidence=0.62,
                    metric_name="gdpnow_real_gdp_growth_saar",
                    metric_value=2.5438,
                    metric_unit="percent_saar",
                    retrieved_at="2026-07-01T00:00:00Z",
                )
            ]
        return []

    async def neutral_adjudicator(**_kwargs):
        return {
            "direction": "neutral",
            "estimated_probability_yes": 0.5,
            "confidence": 0.7,
            "reason": "No directional conclusion.",
            "evidence_assessments": {
                "https://fred.stlouisfed.org/series/GDPNOW": {
                    "supports_direction": "neutral",
                    "supports_confidence": 0.0,
                }
            },
        }

    verdict = await run_research_gate(
        news,
        market,
        model_direction="neutral",
        model_confidence=0.0,
        model_reason="scheduled research prewarm",
        yes_ask=0.21,
        no_ask=0.80,
        live_mode=False,
        search_provider=search_provider,
        direct_fetcher=direct_fetcher,
        adjudicator=neutral_adjudicator,
        max_queries=8,
        require_decision_grade=True,
    )

    assert verdict.status == ResearchStatus.UNTRADEABLE
    assert verdict.skip_reason == "no_edge"
    assert verdict.force_side == "no"
    assert verdict.estimated_probability is not None
    assert verdict.estimated_probability == pytest.approx(0.30876)
    assert verdict.market_price == pytest.approx(0.80)
    gdpnow = [
        item
        for item in verdict.evidence
        if item.metric_name == "gdpnow_real_gdp_growth_saar"
    ][0]
    assert gdpnow.supports_direction == "no"
    assert gdpnow.supports_confidence > 0.5


@pytest.mark.asyncio
async def test_gdpnow_more_than_threshold_moves_past_official_pending_to_countercase():
    market = SimpleNamespace(
        ticker="KXGDP-26JUL30-T2.0",
        title="Will **real GDP** increase by more than 2.0% in Q2 2026?",
        rules_primary=(
            "If real GDP (as measured by the BEA seasonally adjusted and "
            "annualized Advance Estimate) increases by more than 2.0, then "
            "the market resolves to Yes."
        ),
        rules_secondary="The BEA advance estimate of real GDP growth resolves this market.",
        contract_terms_url="",
        settlement_sources=(),
    )

    async def search_provider(query):
        if query.query_intent == "base_rate":
            return [
                ResearchEvidence(
                    source_class="specialized_data",
                    source_name="FRED GDPNow",
                    source_url="https://fred.stlouisfed.org/series/GDPNOW",
                    title="GDPNow latest observation",
                    snippet="GDPNow latest estimate is 1.1889% versus the 2.0% threshold.",
                    claim_type="base_rate",
                    supports_direction="neutral",
                    supports_confidence=0.3,
                    metric_name="gdpnow_real_gdp_growth_saar",
                    metric_value=1.1889,
                    metric_unit="percent_saar",
                    extraction_confidence=0.95,
                    retrieved_at="2026-07-03T18:30:00Z",
                )
            ]
        if query.query_intent == "official_resolution":
            return [
                ResearchEvidence(
                    source_class="official_primary",
                    source_name="BEA GDP data",
                    source_url="https://www.bea.gov/data/gdp/gross-domestic-product#pending-2026-q2",
                    title="Economic-stat release pending for real GDP (Q2 2026)",
                    snippet="The BEA advance estimate for Q2 2026 is pending.",
                    claim_type="official_resolution",
                    supports_direction="neutral",
                    supports_confidence=0.0,
                    metric_name="economic_stat_data_pending",
                    metric_unit="period_status",
                    extraction_confidence=0.9,
                    retrieved_at="2026-07-03T18:30:00Z",
                )
            ]
        return []

    verdict = await run_research_gate(
        SimpleNamespace(headline="", source="research_prewarm"),
        market,
        model_direction="neutral",
        model_confidence=0.0,
        model_reason="scheduled research prewarm",
        yes_ask=0.47,
        no_ask=0.54,
        live_mode=False,
        search_provider=search_provider,
        direct_fetcher=lambda *_args: None,
        max_queries=7,
        require_decision_grade=True,
    )

    assert verdict.status == ResearchStatus.NEEDS_COUNTER_EVIDENCE
    assert verdict.skip_reason == "missing_counter_evidence"
    assert verdict.force_side is None
    assert verdict.estimated_probability == pytest.approx(0.33778)
    assert verdict.market_price == pytest.approx(0.54)
    assert verdict.estimated_edge == pytest.approx(0.11222)
    gdpnow = [
        item
        for item in verdict.evidence
        if item.metric_name == "gdpnow_real_gdp_growth_saar"
    ][0]
    assert gdpnow.supports_direction == "no"
    assert gdpnow.supports_confidence > 0.5


@pytest.mark.asyncio
async def test_structured_official_cpi_metric_prevents_ambiguous_threshold_dossier():
    market = SimpleNamespace(
        ticker="KXCPI-26MAY-T0.4",
        title="Will CPI rise more than 0.4% in May 2026?",
        rules_primary=(
            "If the CPI-U monthly change for May 2026 is more than 0.4%, "
            "this market resolves to Yes."
        ),
        rules_secondary="BLS CPI-U series CUSR0000SA0 determines resolution.",
        contract_terms_url="https://kalshi.com/markets/KXCPI-26MAY-T0.4",
        settlement_sources=(),
    )

    async def direct_fetcher(url, source_class, claim_type):
        return ResearchEvidence(
            source_class=source_class,
            source_name="Kalshi rules",
            source_url=url,
            title="CPI contract rules",
            snippet="BLS CPI-U series CUSR0000SA0 determines the May 2026 CPI market.",
            claim_type=claim_type,
            supports_direction="neutral",
            supports_confidence=0.0,
            retrieved_at="2026-07-01T00:00:00Z",
        )

    async def search_provider(query):
        if query.query_intent != "official_resolution":
            return []
        return [
            ResearchEvidence(
                source_class="official_primary",
                source_name="BLS CPI",
                source_url="https://api.bls.gov/publicAPI/v2/timeseries/data/CUSR0000SA0",
                title="BLS CPI monthly change for May 2026: 0.5%",
                snippet=(
                    "BLS CPI-U CUSR0000SA0 rose 0.5% in May 2026 on a "
                    "single-decimal month-over-month basis."
                ),
                claim_type=query.query_intent,
                supports_direction="neutral",
                supports_confidence=0.0,
                metric_name="cpi_monthly_change_single_decimal",
                metric_value=0.5,
                metric_unit="percent_mom_single_decimal",
                extraction_confidence=0.95,
                retrieved_at="2026-07-01T00:00:00Z",
            )
        ]

    async def neutral_adjudicator(**_kwargs):
        return {
            "direction": "neutral",
            "estimated_probability_yes": 0.5,
            "confidence": 0.7,
            "reason": "No directional conclusion.",
        }

    verdict = await run_research_gate(
        SimpleNamespace(headline="", source="research_prewarm"),
        market,
        model_direction="neutral",
        model_confidence=0.0,
        model_reason="scheduled research prewarm",
        yes_ask=0.90,
        no_ask=0.11,
        live_mode=False,
        search_provider=search_provider,
        direct_fetcher=direct_fetcher,
        adjudicator=neutral_adjudicator,
        max_queries=8,
        require_decision_grade=True,
    )

    assert verdict.status == ResearchStatus.UNTRADEABLE
    assert verdict.skip_reason == "no_edge"
    assert verdict.force_side == "yes"
    assert verdict.estimated_probability == pytest.approx(0.5666666667)
    cpi = [
        item
        for item in verdict.evidence
        if item.metric_name == "cpi_monthly_change_single_decimal"
    ][0]
    assert cpi.supports_direction == "yes"
    assert cpi.supports_confidence >= 0.8


@pytest.mark.asyncio
async def test_structured_official_cpi_metric_survives_adjudicator_timeout():
    market = SimpleNamespace(
        ticker="KXCPI-26MAY-T0.4",
        title="Will CPI rise more than 0.4% in May 2026?",
        rules_primary=(
            "If the CPI-U monthly change for May 2026 is more than 0.4%, "
            "this market resolves to Yes."
        ),
        rules_secondary="BLS CPI-U series CUSR0000SA0 determines resolution.",
        contract_terms_url="https://kalshi.com/markets/KXCPI-26MAY-T0.4",
        settlement_sources=(),
    )

    async def direct_fetcher(url, source_class, claim_type):
        return ResearchEvidence(
            source_class=source_class,
            source_name="Kalshi rules",
            source_url=url,
            title="CPI contract rules",
            snippet="BLS CPI-U series CUSR0000SA0 determines the May 2026 CPI market.",
            claim_type=claim_type,
            supports_direction="neutral",
            supports_confidence=0.0,
            retrieved_at="2026-07-01T00:00:00Z",
        )

    async def search_provider(query):
        if query.query_intent != "official_resolution":
            return []
        return [
            ResearchEvidence(
                source_class="official_primary",
                source_name="BLS CPI",
                source_url="https://api.bls.gov/publicAPI/v2/timeseries/data/CUSR0000SA0",
                title="BLS CPI monthly change for May 2026: 0.5%",
                snippet=(
                    "BLS CPI-U CUSR0000SA0 rose 0.5% in May 2026 on a "
                    "single-decimal month-over-month basis."
                ),
                claim_type=query.query_intent,
                supports_direction="neutral",
                supports_confidence=0.0,
                metric_name="cpi_monthly_change_single_decimal",
                metric_value=0.5,
                metric_unit="percent_mom_single_decimal",
                extraction_confidence=0.95,
                retrieved_at="2026-07-01T00:00:00Z",
            )
        ]

    async def slow_adjudicator(**_kwargs):
        await asyncio.sleep(0.2)
        return {
            "direction": "neutral",
            "estimated_probability_yes": 0.5,
            "confidence": 0.7,
            "reason": "No directional conclusion.",
        }

    verdict = await run_research_gate(
        SimpleNamespace(headline="", source="research_prewarm"),
        market,
        model_direction="neutral",
        model_confidence=0.0,
        model_reason="scheduled research prewarm",
        yes_ask=0.90,
        no_ask=0.11,
        live_mode=False,
        search_provider=search_provider,
        direct_fetcher=direct_fetcher,
        adjudicator=slow_adjudicator,
        max_queries=8,
        research_timeout_seconds=0.05,
        require_decision_grade=True,
    )

    assert verdict.status == ResearchStatus.UNTRADEABLE
    assert verdict.skip_reason == "no_edge"
    assert verdict.force_side == "yes"
    assert verdict.market_price == pytest.approx(0.90)
    assert verdict.estimated_edge == pytest.approx(-0.3433333333)


@pytest.mark.asyncio
async def test_structured_nws_high_temp_metric_prevents_insufficient_corrob_dossier():
    market = SimpleNamespace(
        ticker="KXHIGHNY-26JUN30-B87.5",
        title="Will the high temp in NYC be 87-88° on Jun 30, 2026?",
        rules_primary=(
            "If the highest temperature recorded in Central Park, New York "
            "for June 30, 2026 as reported by the National Weather Service's "
            "Climatological Report (Daily), is between 87-88°, then the market "
            "resolves to Yes."
        ),
        rules_secondary="The official NWS Climatological Report determines resolution.",
        settlement_sources=(
            SettlementSource(
                label="NWS Climatological Report",
                url="https://forecast.weather.gov/product.php?site=OKX&product=CLI&issuedby=NYC",
                domain="forecast.weather.gov",
            ),
        ),
    )

    async def direct_fetcher(url, source_class, claim_type):
        return ResearchEvidence(
            source_class=source_class,
            source_name="forecast.weather.gov",
            source_url=url,
            title="National Weather Service",
            snippet="NWS publishes the Central Park daily climatological report.",
            claim_type=claim_type,
            supports_direction="yes",
            supports_confidence=0.9,
            retrieved_at="2026-07-01T00:00:00Z",
        )

    async def search_provider(query):
        if query.query_intent != "official_resolution":
            return []
        return [
            ResearchEvidence(
                source_class="official_primary",
                source_name="NWS Climatological Report",
                source_url=(
                    "https://forecast.weather.gov/product.php?site=OKX&product=CLI&issuedby=NYC"
                    "#high-2026-06-30"
                ),
                title="Central Park NY climate summary for June 30 2026",
                snippet="NWS Central Park climate report lists TODAY MAXIMUM 87.",
                claim_type=query.query_intent,
                supports_direction="neutral",
                supports_confidence=0.0,
                metric_name="nws_daily_high_temp_f",
                metric_value=87.0,
                metric_unit="fahrenheit",
                extraction_confidence=0.95,
                retrieved_at="2026-07-01T00:00:00Z",
            )
        ]

    async def same_direction_adjudicator(**_kwargs):
        return {
            "direction": "yes",
            "estimated_probability_yes": 0.8,
            "confidence": 0.9,
            "reason": "Official source supports YES.",
        }

    verdict = await run_research_gate(
        SimpleNamespace(headline="", source="research_prewarm"),
        market,
        model_direction="neutral",
        model_confidence=0.0,
        model_reason="scheduled research prewarm",
        yes_ask=1.0,
        no_ask=0.01,
        live_mode=False,
        search_provider=search_provider,
        direct_fetcher=direct_fetcher,
        adjudicator=same_direction_adjudicator,
        max_queries=8,
        require_decision_grade=True,
    )

    assert verdict.status == ResearchStatus.UNTRADEABLE
    assert verdict.skip_reason == "no_edge"
    assert verdict.force_side == "yes"
    assert verdict.estimated_probability == pytest.approx(0.95)
    high_temp = [
        item
        for item in verdict.evidence
        if item.metric_name == "nws_daily_high_temp_f"
    ][0]
    assert high_temp.supports_direction == "yes"
    assert high_temp.supports_confidence >= 0.9


@pytest.mark.asyncio
async def test_strict_research_gate_includes_required_decision_grade_query_intents():
    seen_intents: list[str] = []
    fresh = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    async def search_provider(query):
        seen_intents.append(query.query_intent)
        return [
            ResearchEvidence(
                source_class="official_primary",
                source_name="Official source",
                source_url=f"https://agency.gov/{query.query_intent}",
                title="Official result",
                snippet="Official source says the event resolved YES.",
                claim_type=query.query_intent,
                supports_direction="yes",
                supports_confidence=0.8,
                retrieved_at=fresh,
            )
        ]

    async def adjudicator(**_kwargs):
        return {
            "direction": "yes",
            "estimated_probability_yes": 0.68,
            "confidence": 0.82,
            "reason": (
                "Trade YES because official and supporting evidence differ from "
                "market price. Counter evidence is weak, leaving positive edge."
            ),
        }

    await run_research_gate(
        SimpleNamespace(headline="Iran crude output rises sharply", source="Reuters"),
        SimpleNamespace(
            ticker="KXIRANCRUDE-26JUL13-T3.8",
            title="Will Iran crude oil production be at least 3.8M bpd?",
            rules_primary="OPEC Monthly Oil Market Report resolves this market.",
            rules_secondary="Later revisions excluded.",
            settlement_sources=(),
        ),
        model_direction="neutral",
        model_confidence=0.5,
        model_reason="No keyword hit.",
        yes_ask=0.55,
        no_ask=0.47,
        live_mode=False,
        search_provider=search_provider,
        adjudicator=adjudicator,
        max_queries=6,
        require_decision_grade=True,
    )

    assert {
        "supporting",
        "disconfirming",
        "base_rate",
        "official_resolution",
        "rules",
        "market_price",
        "staleness_check",
    } <= set(seen_intents)


@pytest.mark.asyncio
async def test_strict_research_gate_uses_adjudicator_evidence_labels_for_decision_grade():
    fresh = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    async def search_provider(query):
        if query.query_intent == "official_resolution":
            return [
                ResearchEvidence(
                    source_class="official_primary",
                    source_name="Official source",
                    source_url="https://agency.gov/signed",
                    title="Agreement notice",
                    snippet="Official notice says the agreement was signed before the deadline.",
                    claim_type=query.query_intent,
                    retrieved_at=fresh,
                )
            ]
        if query.query_intent == "disconfirming":
            return [
                ResearchEvidence(
                    source_class="reputable_secondary",
                    source_name="AP",
                    source_url="https://apnews.com/ratification",
                    title="Ratification objection",
                    snippet="AP notes ratification of the ceasefire agreement could still fail.",
                    claim_type=query.query_intent,
                    retrieved_at=fresh,
                )
            ]
        if query.query_intent == "rules":
            return [
                ResearchEvidence(
                    source_class="rules_source",
                    source_name="Kalshi",
                    source_url="https://kalshi.com/markets/KXCEASEFIRE-26JUL01",
                    title="Contract rules",
                    snippet="Rules require a signed agreement, not ratification.",
                    claim_type=query.query_intent,
                    retrieved_at=fresh,
                )
            ]
        if query.query_intent == "supporting":
            return [
                ResearchEvidence(
                    source_class="reputable_secondary",
                    source_name="Reuters",
                    source_url="https://reuters.com/signed",
                    title="Agreement signed",
                    snippet="Reuters independently reports the agreement was signed.",
                    claim_type=query.query_intent,
                    retrieved_at=fresh,
                )
            ]
        return []

    async def adjudicator(**_kwargs):
        return {
            "direction": "yes",
            "estimated_probability_yes": 0.68,
            "confidence": 0.82,
            "reason": (
                "Trade YES because the official notice says the agreement was signed "
                "before July 1 and Reuters independently confirms signing. Market YES "
                "ask is 55c versus 68% probability, leaving positive edge after costs. "
                "Counter evidence is AP ratification risk, but the rules require signing. "
                "This would prove wrong if the rules require ratification."
            ),
            "evidence_assessments": [
                {
                    "source_url": "https://agency.gov/signed",
                    "supports_direction": "yes",
                    "supports_confidence": 0.9,
                },
                {
                    "source_url": "https://reuters.com/signed",
                    "supports_direction": "yes",
                    "supports_confidence": 0.8,
                },
                {
                    "source_url": "https://apnews.com/ratification",
                    "supports_direction": "no",
                    "supports_confidence": 0.35,
                },
            ],
            "counterclaims": ["AP says ratification could still fail."],
            "open_questions": ["Would ratification be required instead of signing?"],
        }

    verdict = await run_research_gate(
        SimpleNamespace(headline="Agreement signed before deadline", source="Reuters"),
        SimpleNamespace(
            ticker="KXCEASEFIRE-26JUL01",
            title="Will a ceasefire agreement be signed by July 1?",
            rules_primary="The market resolves Yes if an agreement is signed by the deadline.",
            rules_secondary="Official public announcements decide the market.",
            settlement_sources=(),
            contract_terms_url="https://kalshi.com/markets/KXCEASEFIRE-26JUL01",
        ),
        model_direction="neutral",
        model_confidence=0.5,
        model_reason="No keyword hit.",
        yes_ask=0.55,
        no_ask=0.47,
        live_mode=False,
        search_provider=search_provider,
        adjudicator=adjudicator,
        require_decision_grade=True,
    )

    assert verdict.status == ResearchStatus.DECISION_GRADE_CANDIDATE
    assert verdict.force_side == "yes"
    assert verdict.counterclaims == ("AP says ratification could still fail.",)
    assert verdict.open_questions == ("Would ratification be required instead of signing?",)
    labeled = {item.source_url: item.supports_direction for item in verdict.evidence}
    assert labeled["https://agency.gov/signed"] == "yes"
    assert labeled["https://apnews.com/ratification"] == "no"


@pytest.mark.asyncio
async def test_strict_research_gate_runs_side_aware_counter_search_before_candidate():
    fresh = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    seen_queries: list[ResearchQuery] = []

    async def search_provider(query):
        seen_queries.append(query)
        if query.query_intent == "official_resolution":
            return [
                ResearchEvidence(
                    source_class="official_primary",
                    source_name="Official source",
                    source_url="https://agency.gov/signed",
                    title="Agreement notice",
                    snippet="Official notice says the agreement was signed before the deadline.",
                    claim_type=query.query_intent,
                    retrieved_at=fresh,
                )
            ]
        if query.query_intent == "supporting":
            return [
                ResearchEvidence(
                    source_class="reputable_secondary",
                    source_name="Reuters",
                    source_url="https://reuters.com/signed",
                    title="Agreement signed",
                    snippet="Reuters independently reports the agreement was signed.",
                    claim_type=query.query_intent,
                    retrieved_at=fresh,
                )
            ]
        if query.query_intent == "rules":
            return [
                ResearchEvidence(
                    source_class="rules_source",
                    source_name="Kalshi",
                    source_url="https://kalshi.com/markets/KXCEASEFIRE-26JUL01",
                    title="Contract rules",
                    snippet="Rules require a signed agreement, not ratification.",
                    claim_type=query.query_intent,
                    retrieved_at=fresh,
                )
            ]
        if (
            query.query_intent == "disconfirming"
            and "evidence against YES" in query.query
            and "NO case" in query.query
        ):
            return [
                ResearchEvidence(
                    source_class="reputable_secondary",
                    source_name="AP",
                    source_url="https://apnews.com/not-final",
                    title="Not final",
                    snippet="AP reports opponents say the agreement is not final unless ratified.",
                    claim_type=query.query_intent,
                    retrieved_at=fresh,
                )
            ]
        return []

    adjudicator_calls = 0

    async def adjudicator(*, evidence, **_kwargs):
        nonlocal adjudicator_calls
        adjudicator_calls += 1
        assessments = [
            {
                "source_url": "https://agency.gov/signed",
                "supports_direction": "yes",
                "supports_confidence": 0.9,
            },
            {
                "source_url": "https://reuters.com/signed",
                "supports_direction": "yes",
                "supports_confidence": 0.8,
            },
        ]
        if any(item.source_url == "https://apnews.com/not-final" for item in evidence):
            assessments.append(
                {
                    "source_url": "https://apnews.com/not-final",
                    "supports_direction": "no",
                    "supports_confidence": 0.35,
                }
            )
        return {
            "direction": "yes",
            "estimated_probability_yes": 0.68,
            "confidence": 0.82,
            "reason": (
                "Trade YES because the official notice says the agreement was signed "
                "before July 1 and Reuters independently confirms signing. Market YES "
                "ask is 55c versus 68% probability, leaving positive edge after costs. "
                "Counter evidence is AP ratification risk. This would prove wrong if "
                "ratification is required."
            ),
            "evidence_assessments": assessments,
            "counterclaims": ["AP says ratification could still be required."],
            "open_questions": ["Would ratification be required instead of signing?"],
        }

    verdict = await run_research_gate(
        SimpleNamespace(headline="Agreement signed before deadline", source="Reuters"),
        SimpleNamespace(
            ticker="KXCEASEFIRE-26JUL01",
            title="Will a ceasefire agreement be signed by July 1?",
            rules_primary="The market resolves Yes if an agreement is signed by the deadline.",
            rules_secondary="Official public announcements decide the market.",
            settlement_sources=(),
            contract_terms_url="https://kalshi.com/markets/KXCEASEFIRE-26JUL01",
        ),
        model_direction="neutral",
        model_confidence=0.5,
        model_reason="No keyword hit.",
        yes_ask=0.55,
        no_ask=0.47,
        live_mode=False,
        search_provider=search_provider,
        adjudicator=adjudicator,
        require_decision_grade=True,
    )

    assert adjudicator_calls == 2
    assert any(
        query.query_intent == "disconfirming"
        and "evidence against YES" in query.query
        for query in seen_queries
    )
    assert verdict.status == ResearchStatus.DECISION_GRADE_CANDIDATE
    assert {
        item.source_url: item.supports_direction
        for item in verdict.evidence
    }["https://apnews.com/not-final"] == "no"


def test_crude_oil_query_pack_uses_contract_reporting_window():
    news = SimpleNamespace(
        headline="Iran output update",
        source="Example",
        url="https://example.com/iran-oil",
    )
    market = SimpleNamespace(
        ticker="KXIRANCRUDE-26AUG13-T3.8",
        title="Will Iran's average daily crude oil production be at least 3.8M bpd?",
        rules_primary=(
            "If Iran's average daily crude oil production for July 2026 is "
            "at least 3.8M bpd, as reported in the OPEC Monthly Oil Market Report, "
            "then the market resolves to Yes."
        ),
        rules_secondary="Underlying is Iran crude oil production based on OPEC secondary sources.",
        settlement_sources=(),
    )

    queries = build_research_queries(news, market)
    rendered = "\n".join(query.query for query in queries)

    assert "July 2026" in rendered
    assert "June 2026" not in rendered


def test_generic_market_query_pack_adds_contract_terms_context_fallback():
    news = SimpleNamespace(
        headline="Officials say ceasefire agreement could be signed this week",
        source="Reuters",
        url="https://reuters.com/ceasefire",
    )
    market = SimpleNamespace(
        ticker="KXCEASEFIRE-26JUL01",
        title="Will a ceasefire agreement be signed by July 1?",
        rules_primary="The market resolves Yes if an agreement is signed by the deadline.",
        rules_secondary="The determination will be based on official public announcements.",
        settlement_sources=(),
        contract_terms_url="https://kalshi.com/markets/KXCEASEFIRE-26JUL01",
    )

    queries = build_research_queries(news, market)

    assert any(
        query.source_class == "rules_source"
        and query.query_intent == "contract_terms"
        and "site:kalshi.com" in query.query
        for query in queries
    )
    assert any(
        query.source_class == "official_primary"
        and query.query_intent == "official_resolution_context"
        and "agreement is signed" in query.query
        for query in queries
    )


def test_query_pack_does_not_fabricate_official_context_without_source_path():
    news = SimpleNamespace(headline="", source="research_prewarm", url="")
    market = SimpleNamespace(
        ticker="KXMVESPORTSMULTIGAMEEXTENDED-S2026",
        title="yes Pittsburgh,yes Texas,yes Tampa Bay,yes Detroit",
        rules_primary="",
        rules_secondary="",
        settlement_sources=(),
        contract_terms_url="",
    )

    queries = build_research_queries(news, market)

    assert not any(
        query.source_class == "official_primary"
        and query.query_intent == "official_resolution_context"
        for query in queries
    )


def test_query_pack_uses_contract_terms_only_as_source_path_for_context():
    news = SimpleNamespace(headline="", source="research_prewarm", url="")
    market = SimpleNamespace(
        ticker="KXCEASEFIRE-26JUL01",
        title="Will a ceasefire agreement be signed by July 1?",
        rules_primary="",
        rules_secondary="",
        settlement_sources=(),
        contract_terms_url="https://kalshi.com/markets/KXCEASEFIRE-26JUL01",
    )

    queries = build_research_queries(news, market)

    assert any(
        query.source_class == "rules_source"
        and query.query_intent == "contract_terms"
        and "site:kalshi.com" in query.query
        for query in queries
    )
    assert any(
        query.source_class == "official_primary"
        and query.query_intent == "official_resolution_context"
        for query in queries
    )


def test_query_pack_adds_official_domain_hint_for_named_jne_market():
    news = SimpleNamespace(headline="", source="research_prewarm", url="")
    market = SimpleNamespace(
        ticker="KXPERUINVALIDATE-26JUN-26JUL28",
        title=(
            "Will Peru's National Jury of Elections (Jurado Nacional de "
            "Elecciones, JNE) announce the annulment of the 2026 Peruvian "
            "presidential election before Jul 28, 2026?"
        ),
        rules_primary="Resolution depends on a JNE announcement.",
        rules_secondary="",
        settlement_sources=(),
        contract_terms_url="https://kalshi.com/markets/KXPERUINVALIDATE",
    )

    queries = build_research_queries(news, market)

    assert any(
        query.source_class == "official_primary"
        and query.query_intent == "official_resolution"
        and query.query.startswith("site:jne.gob.pe")
        and "nulidad" in query.query
        and "elecciones presidenciales 2026" in query.query
        for query in queries
    )


def test_query_pack_targets_sars_for_south_africa_trade_balance():
    news = SimpleNamespace(headline="", source="research_prewarm", url="")
    market = SimpleNamespace(
        ticker="KXSATRADEBAL-26JUL31-T4.00",
        title="Will South Africa balance of trade for June 2026 be above ZAR4.00B?",
        rules_primary=(
            "If the South Africa balance of trade for June 2026 is above ZAR4.00B, "
            "then the market resolves to Yes."
        ),
        rules_secondary="",
        settlement_sources=(SettlementSource(label="BLS CPI", domain="bls.gov"),),
        contract_terms_url="https://kalshi.com/markets/KXSATRADEBAL-26JUL31-T4.00",
    )

    queries = build_research_queries(news, market)
    rendered = [
        query.query
        for query in queries
        if query.query_intent in {"resolution_source", "official_resolution"}
    ]

    assert not any("site:bls.gov" in query for query in rendered)
    assert any("site:sars.gov.za" in query for query in rendered)
    assert any("trade statistics" in query.lower() for query in rendered)


def test_direct_source_targets_use_domain_only_settlement_sources():
    market = SimpleNamespace(
        contract_terms_url="",
        settlement_sources=(SettlementSource(label="AP", domain="apnews.com"),),
    )

    assert _direct_source_targets(market) == [
        ("https://apnews.com", "resolution_source", "settlement_source")
    ]


def test_direct_source_targets_skip_incompatible_south_africa_trade_balance_source():
    market = SimpleNamespace(
        ticker="KXSATRADEBAL-26JUL31-T4.00",
        title="",
        rules_primary="",
        rules_secondary="",
        contract_terms_url="",
        settlement_sources=(SettlementSource(label="BLS CPI", domain="bls.gov"),),
    )

    assert _direct_source_targets(market) == []


def test_cached_evidence_skips_incompatible_south_africa_trade_balance_source():
    fresh = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    evidence = [
        ResearchEvidence(
            source_class="resolution_source",
            source_name="BLS",
            source_url="https://www.bls.gov/news.release/cpi.nr0.htm",
            title="BLS CPI",
            snippet="Wrong country and metric.",
            claim_type="settlement_source",
            supports_direction="neutral",
            supports_confidence=0.0,
            retrieved_at=fresh,
            contract_fingerprint="fp",
        ),
        ResearchEvidence(
            source_class="official_primary",
            source_name="SARS trade statistics",
            source_url="https://www.sars.gov.za/customs-and-excise/trade-statistics/",
            title="SARS trade statistics",
            snippet="Correct official source path.",
            claim_type="official_resolution",
            supports_direction="neutral",
            supports_confidence=0.0,
            retrieved_at=fresh,
            contract_fingerprint="fp",
        ),
    ]

    usable = research_gate_module._usable_cached_evidence(
        evidence,
        "fp",
        "KXSATRADEBAL-26JUL31-T4.00",
    )

    assert [item.source_name for item in usable] == ["SARS trade statistics"]


def test_query_pack_skips_kalshi_placeholder_settlement_sources():
    market = SimpleNamespace(
        ticker="KXLEAVECONGRESS-26JUN",
        title="Will any member of Congress leave office in June 2026?",
        rules_primary="Resolves using official records and reputable reporting.",
        rules_secondary="",
        contract_terms_url="https://kalshi-public-docs.s3.amazonaws.com/contract_terms/EFFECTIVELEAVE.pdf",
        settlement_sources=(
            SettlementSource(label="person", domain="kalshi.com", url="https://kalshi.com/"),
            SettlementSource(
                label="official government records and registries",
                domain="kalshi.com",
                url="https://kalshi.com",
            ),
            SettlementSource(label="Associated Press", domain="apnews.com", url="https://apnews.com"),
            SettlementSource(label="Reuters", domain="reuters.com", url="https://www.reuters.com/"),
        ),
    )

    queries = build_research_queries(
        SimpleNamespace(headline="", source="research_prewarm", url=""),
        market,
    )
    rendered = [query.query for query in queries if query.query_intent == "resolution_source"]

    assert not any("site:kalshi.com" in query for query in rendered)
    assert any("site:apnews.com" in query for query in rendered)
    assert any("site:reuters.com" in query for query in rendered)


def test_direct_source_targets_skip_kalshi_placeholder_homepages():
    market = SimpleNamespace(
        contract_terms_url="",
        settlement_sources=(
            SettlementSource(label="person", domain="kalshi.com", url="https://kalshi.com/"),
            SettlementSource(label="AP", domain="apnews.com", url="https://apnews.com"),
        ),
    )

    assert _direct_source_targets(market) == [
        ("https://apnews.com", "resolution_source", "settlement_source")
    ]


def test_decision_grade_direct_fetch_skips_raw_pdfs_and_homepages():
    assert not _should_direct_fetch_source(
        "https://kalshi-public-docs.s3.amazonaws.com/contract_terms/VISITAREA.pdf",
        "contract_terms",
    )
    assert not _should_direct_fetch_source(
        "https://apnews.com/",
        "settlement_source",
    )
    assert _should_direct_fetch_source(
        "https://www.bls.gov/news.release/cpi.htm",
        "settlement_source",
    )


@pytest.mark.asyncio
async def test_strict_research_gate_does_not_spend_budget_on_raw_pdf_or_homepage_direct_fetch():
    fetched_urls: list[str] = []
    fresh = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    async def direct_fetcher(url, source_class, claim_type):
        fetched_urls.append(url)
        return ResearchEvidence(
            source_class=source_class,
            source_name=url,
            source_url=url,
            title="Fetched direct source",
            snippet="Fetched direct source text.",
            claim_type=claim_type,
            retrieved_at=fresh,
        )

    async def search_provider(query):
        if query.query_intent == "official_resolution":
            return [
                ResearchEvidence(
                    source_class="official_primary",
                    source_name="AP",
                    source_url="https://apnews.com/article/specific",
                    title="Specific AP article",
                    snippet="AP reports the event has not happened.",
                    claim_type=query.query_intent,
                    retrieved_at=fresh,
                )
            ]
        return []

    async def adjudicator(**_kwargs):
        return {
            "direction": "neutral",
            "estimated_probability_yes": None,
            "confidence": 0.4,
            "reason": "Evidence is not directional enough.",
        }

    verdict = await run_research_gate(
        SimpleNamespace(headline="", source="research_prewarm", url=""),
        SimpleNamespace(
            ticker="KXBACKLOG-26JUL01",
            title="Will the event happen by July 1?",
            rules_primary="The market resolves based on AP reporting.",
            rules_secondary="",
            settlement_sources=(SettlementSource(label="AP", url="https://apnews.com/"),),
            contract_terms_url=(
                "https://kalshi-public-docs.s3.amazonaws.com/contract_terms/VISITAREA.pdf"
            ),
        ),
        model_direction="neutral",
        model_confidence=0.5,
        model_reason="No signal.",
        yes_ask=0.45,
        no_ask=0.56,
        live_mode=False,
        search_provider=search_provider,
        direct_fetcher=direct_fetcher,
        adjudicator=adjudicator,
        require_decision_grade=True,
    )

    assert fetched_urls == []
    assert verdict.skip_reason == "insufficient_corroboration"


def test_rss_search_does_not_treat_wrong_domain_as_resolution_source(monkeypatch):
    rss = b"""<?xml version="1.0" encoding="UTF-8"?>
    <rss><channel><item>
      <title>Reuters report on OPEC data</title>
      <link>https://reuters.com/world/oil-output</link>
      <source url="https://reuters.com">Reuters</source>
      <description>Secondary reporting mentions OPEC production.</description>
      <pubDate>Sat, 27 Jun 2026 12:00:00 GMT</pubDate>
    </item></channel></rss>
    """

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _limit):
            return rss

    monkeypatch.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: _Response())

    evidence = _rss_search(
        ResearchQuery(
            query="site:opec.org Monthly Oil Market Report Iran",
            query_intent="resolution_source",
            source_class="resolution_source",
        )
    )

    assert len(evidence) == 1
    assert evidence[0].source_url == "https://reuters.com/world/oil-output"
    assert evidence[0].source_class == "reputable_secondary"


def test_rss_search_uses_source_label_to_match_site_scoped_resolution(monkeypatch):
    rss = b"""<?xml version="1.0" encoding="UTF-8"?>
    <rss><channel><item>
      <title>Latest From The Post - The Washington Post</title>
      <link>https://news.google.com/rss/articles/example</link>
      <source url="https://washingtonpost.com">The Washington Post</source>
      <description>Washington Post coverage of Trump and Iran.</description>
      <pubDate>Sat, 27 Jun 2026 12:00:00 GMT</pubDate>
    </item></channel></rss>
    """

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _limit):
            return rss

    monkeypatch.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: _Response())

    evidence = _rss_search(
        ResearchQuery(
            query="site:washingtonpost.com Trump Supreme Leader Iran meeting",
            query_intent="resolution_source",
            source_class="resolution_source",
        )
    )

    assert evidence[0].source_class == "resolution_source"


def test_rss_search_uses_publisher_url_to_match_site_scoped_official_source(
    monkeypatch,
):
    rss = b"""<?xml version="1.0" encoding="UTF-8"?>
    <rss><channel><item>
      <title>Oklo Aurora Powerhouse licensing update</title>
      <link>https://news.google.com/rss/articles/nrc-example</link>
      <source url="https://www.nrc.gov">Nuclear Regulatory Commission (NRC) (.gov)</source>
      <description>NRC licensing material for the Aurora Powerhouse.</description>
      <pubDate>Sat, 11 Jul 2026 12:00:00 GMT</pubDate>
    </item></channel></rss>
    """

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _limit):
            return rss

    monkeypatch.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: _Response())

    evidence = _rss_search(
        ResearchQuery(
            query="site:nrc.gov reactor criticality Oklo",
            query_intent="official_resolution",
            source_class="official_primary",
        )
    )

    assert evidence[0].source_url == "https://www.nrc.gov"
    assert evidence[0].aggregator_url == (
        "https://news.google.com/rss/articles/nrc-example"
    )
    assert evidence[0].source_class == "official_primary"


@pytest.mark.parametrize(
    "source_element",
    (
        "<source>NRC.gov</source>",
        '<source url="https://nrc.gov.attacker.example">nrc.gov</source>',
    ),
)
def test_rss_search_rejects_unverified_official_publisher_identity(
    monkeypatch,
    source_element,
):
    rss = f"""<?xml version="1.0" encoding="UTF-8"?>
    <rss><channel><item>
      <title>Claimed NRC update</title>
      <link>https://news.google.com/rss/articles/unverified-nrc</link>
      {source_element}
      <description>Unverified publisher metadata.</description>
      <pubDate>Sat, 11 Jul 2026 12:00:00 GMT</pubDate>
    </item></channel></rss>
    """.encode()

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _limit):
            return rss

    monkeypatch.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: _Response())

    evidence = _rss_search(
        ResearchQuery(
            query="site:nrc.gov reactor criticality Oklo",
            query_intent="official_resolution",
            source_class="official_primary",
        )
    )

    assert evidence[0].source_class == "other"


def test_rss_search_does_not_override_direct_link_with_publisher_metadata(monkeypatch):
    rss = b"""<?xml version="1.0" encoding="UTF-8"?>
    <rss><channel><item>
      <title>Claimed NRC update</title>
      <link>https://attacker.example/claimed-nrc-update</link>
      <source url="https://www.nrc.gov">Nuclear Regulatory Commission</source>
      <description>Unverified publisher metadata on a direct link.</description>
      <pubDate>Sat, 11 Jul 2026 12:00:00 GMT</pubDate>
    </item></channel></rss>
    """

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _limit):
            return rss

    monkeypatch.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: _Response())

    evidence = _rss_search(
        ResearchQuery(
            query="site:nrc.gov reactor criticality Oklo",
            query_intent="official_resolution",
            source_class="official_primary",
        )
    )

    assert evidence[0].source_url == "https://attacker.example/claimed-nrc-update"
    assert evidence[0].aggregator_url is None
    assert evidence[0].source_class == "other"


def test_google_wrapper_does_not_create_independent_source_path():
    evidence = [
        ResearchEvidence(
            source_class="official_primary",
            source_name="NRC",
            source_url="https://www.nrc.gov",
            title="NRC update",
            snippet="Official update.",
            claim_type="official_resolution",
        ),
        ResearchEvidence(
            source_class="other",
            source_name="Unknown publisher",
            source_url="https://news.google.com/rss/articles/unknown",
            title="Aggregated update",
            snippet="Unverified update.",
            claim_type="supporting",
        ),
    ]

    assert _has_reliable_non_pending_source_path(evidence) is False


def test_distinct_official_publishers_create_independent_source_path():
    evidence = [
        ResearchEvidence(
            source_class="official_primary",
            source_name="NRC",
            source_url="https://www.nrc.gov",
            title="NRC update",
            snippet="Official update.",
            claim_type="official_resolution",
        ),
        ResearchEvidence(
            source_class="official_primary",
            source_name="USTR",
            source_url="https://ustr.gov",
            title="USTR update",
            snippet="Separate official update.",
            claim_type="supporting",
        ),
    ]

    assert _has_reliable_non_pending_source_path(evidence) is True


def test_google_wrapper_cannot_promote_decision_grade_candidate():
    retrieved_at = datetime.now(timezone.utc).isoformat()
    candidate = ResearchVerdict(
        status=ResearchStatus.TRADE_CANDIDATE,
        attempted=True,
        queries=[
            ResearchQuery(
                query="evidence against reactor criticality",
                query_intent="disconfirming",
                source_class="reputable_secondary",
            )
        ],
        evidence=[
            ResearchEvidence(
                source_class="official_primary",
                source_name="NRC",
                source_url="https://www.nrc.gov",
                title="NRC criticality update",
                snippet="The official update supports the threshold.",
                claim_type="official_resolution",
                supports_direction="yes",
                supports_confidence=0.9,
                retrieved_at=retrieved_at,
            ),
            ResearchEvidence(
                source_class="reputable_secondary",
                source_name="Unknown publisher",
                source_url="https://news.google.com/rss/articles/counter",
                title="Counterclaim",
                snippet="The counterclaim disputes the threshold.",
                claim_type="disconfirming",
                supports_direction="no",
                supports_confidence=0.8,
                retrieved_at=retrieved_at,
            ),
        ],
        summary="NRC evidence supports YES while the only counterclaim is unverified.",
        force_side="yes",
        estimated_probability=0.7,
        market_price=0.5,
        estimated_edge=0.2,
    )

    verdict = _decision_grade_verdict(candidate, model_reason=candidate.summary)

    assert verdict.status == ResearchStatus.NEEDS_RESEARCH
    assert verdict.skip_reason == "no_reliable_source_path"


def test_rss_articles_from_same_publisher_keep_distinct_evidence_identities():
    first = ResearchEvidence(
        source_class="reputable_secondary",
        source_name="Reuters",
        source_url="https://reuters.com",
        aggregator_url="https://news.google.com/rss/articles/reuters-support",
        title="Supporting report",
        snippet="Support.",
        claim_type="supporting",
    )
    second = replace(
        first,
        aggregator_url="https://news.google.com/rss/articles/reuters-counter",
        title="Counter report",
        snippet="Counter.",
        claim_type="disconfirming",
    )

    assert _evidence_identity(first) != _evidence_identity(second)


def test_rss_search_labels_trump_passport_reporting_as_supporting(monkeypatch):
    rss = b"""<?xml version="1.0" encoding="UTF-8"?>
    <rss><channel><item>
      <title>U.S. to issue commemorative passports with Trump picture - NPR</title>
      <link>https://news.google.com/rss/articles/passport</link>
      <source url="https://npr.org">NPR</source>
      <description>U.S. to issue commemorative passports with Trump's picture for America's 250th birthday.</description>
      <pubDate>Sat, 27 Jun 2026 12:00:00 GMT</pubDate>
    </item></channel></rss>
    """

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _limit):
            return rss

    monkeypatch.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: _Response())

    evidence = _rss_search(
        ResearchQuery(
            query=(
                "Will it be reported that the United States Department of State "
                "issues one or more United States passports where the passport "
                "contains an image or visual representation of Donald Trump's face"
            ),
            query_intent="supporting",
            source_class="reputable_secondary",
        )
    )

    assert len(evidence) == 1
    assert evidence[0].source_class == "reputable_secondary"
    assert evidence[0].supports_direction == "yes"
    assert evidence[0].supports_confidence >= 0.7


def test_rss_search_labels_passport_result_when_query_is_truncated(monkeypatch):
    rss = b"""<?xml version="1.0" encoding="UTF-8"?>
    <rss><channel><item>
      <title>U.S. to issue commemorative passports with Trump\xe2\x80\x99s picture for America's 250th birthday - NPR</title>
      <link>https://news.google.com/rss/articles/passport-base-rate</link>
      <source url="https://npr.org">NPR</source>
      <description>U.S. to issue commemorative passports with Trump\xe2\x80\x99s picture for America's 250th birthday.</description>
      <pubDate>Sat, 27 Jun 2026 12:00:00 GMT</pubDate>
    </item></channel></rss>
    """

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _limit):
            return rss

    monkeypatch.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: _Response())

    evidence = _rss_search(
        ResearchQuery(
            query=(
                "Will it be reported that the United States Department of State issues "
                "one or more United States passports where the passport contains an "
                "image or visual representation of D base rate historical frequency"
            ),
            query_intent="base_rate",
            source_class="reputable_secondary",
        )
    )

    assert len(evidence) == 1
    assert evidence[0].supports_direction == "yes"
    assert evidence[0].supports_confidence >= 0.7


def test_rss_search_does_not_label_passport_query_echo_as_supporting(monkeypatch):
    rss = b"""<?xml version="1.0" encoding="UTF-8"?>
    <rss><channel><item>
      <title>Escalating violence reported at Delaney Hall ICE facility - WHYY</title>
      <link>https://news.google.com/rss/articles/unrelated</link>
      <source url="https://whyy.org">WHYY</source>
      <description>Search result matched Trump picture commemorative passport State Department issued denied false not confirmed.</description>
      <pubDate>Sat, 27 Jun 2026 12:00:00 GMT</pubDate>
    </item></channel></rss>
    """

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _limit):
            return rss

    monkeypatch.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: _Response())

    evidence = _rss_search(
        ResearchQuery(
            query="Trump commemorative passport State Department denied false not confirmed",
            query_intent="disconfirming",
            source_class="reputable_secondary",
        )
    )

    assert len(evidence) == 1
    assert evidence[0].supports_direction == "neutral"
    assert evidence[0].supports_confidence == 0.0


def test_adjudication_rejects_unrelated_passport_query_echo_title():
    market = SimpleNamespace(
        ticker="KXTRUMPPASSPORT-26-AUG01",
        title=(
            "Will it be reported that the United States Department of State "
            "issues passports with Donald Trump's face before Aug 1, 2026?"
        ),
        rules_primary=(
            "If a passport contains an image or visual representation of "
            "Donald Trump's face, the market resolves Yes."
        ),
        rules_secondary="",
    )
    evidence = [
        ResearchEvidence(
            source_class="reputable_secondary",
            source_name="WHYY",
            source_url="https://whyy.org/unrelated",
            title="Escalating violence reported at Delaney Hall ICE facility - WHYY",
            snippet=(
                "Search result matched Trump picture commemorative passport "
                "State Department issued denied false not confirmed."
            ),
            claim_type="disconfirming",
        )
    ]

    labeled = _apply_adjudication_evidence_assessments(
        evidence,
        {
            "evidence_assessments": [
                {
                    "ordinal": 0,
                    "supports_direction": "yes",
                    "supports_confidence": 0.8,
                }
            ]
        },
        market=market,
    )

    assert labeled[0].supports_direction == "neutral"
    assert labeled[0].supports_confidence == 0.0


def test_rss_search_normalizes_verbose_publisher_labels(monkeypatch):
    rss = b"""<?xml version="1.0" encoding="UTF-8"?>
    <rss><channel><item>
      <title>Peru election update - ABC News</title>
      <link>https://news.google.com/rss/articles/example</link>
      <source url="https://abcnews.go.com">ABC News - Breaking News, Latest News and Videos</source>
      <description>ABC coverage of Peru election officials.</description>
      <pubDate>Sat, 27 Jun 2026 12:00:00 GMT</pubDate>
    </item></channel></rss>
    """

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _limit):
            return rss

    monkeypatch.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: _Response())

    evidence = _rss_search(
        ResearchQuery(
            query="site:abcnews.go.com Peru JNE election invalidation",
            query_intent="resolution_source",
            source_class="resolution_source",
        )
    )

    assert evidence[0].source_class == "resolution_source"


def test_rss_search_normalizes_common_google_news_publisher_labels(monkeypatch):
    rss = b"""<?xml version="1.0" encoding="UTF-8"?>
    <rss><channel>
    <item>
      <title>Mexico oil exports update - AP News</title>
      <link>https://news.google.com/rss/articles/ap-example</link>
      <source>AP News</source>
      <description>AP coverage of Mexico oil exports to Cuba.</description>
      <pubDate>Sat, 27 Jun 2026 12:00:00 GMT</pubDate>
    </item>
    <item>
      <title>Senate leadership update - The New York Times</title>
      <link>https://news.google.com/rss/articles/nyt-example</link>
      <source>The New York Times</source>
      <description>NYT coverage of Senate Democratic leadership.</description>
      <pubDate>Sat, 27 Jun 2026 12:00:00 GMT</pubDate>
    </item>
    </channel></rss>
    """

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _limit):
            return rss

    monkeypatch.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: _Response())

    evidence = _rss_search(
        ResearchQuery(
            query="Mexico oil exports Cuba current status",
            query_intent="official_resolution",
            source_class="official_primary",
        ),
        limit=5,
    )

    assert [item.source_name for item in evidence] == ["AP News", "The New York Times"]
    assert {item.source_class for item in evidence} == {"reputable_secondary"}


def test_duckduckgo_lite_search_parses_result_links_and_snippets(monkeypatch):
    html = b"""
    <html><body>
      <a rel="nofollow" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.whitehouse.gov%2Fbriefing%2Fuap&amp;rut=x">
        White House releases UAP records
      </a>
      <td class='result-snippet'>
        President Trump issued a directive related to UAP transparency.
      </td>
    </body></html>
    """

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _limit):
            return html

    monkeypatch.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: _Response())

    evidence = _duckduckgo_lite_search(
        ResearchQuery(
            query="site:whitehouse.gov Trump UAP directive",
            query_intent="official_resolution",
            source_class="official_primary",
        ),
        limit=1,
    )

    assert len(evidence) == 1
    assert evidence[0].source_url == "https://www.whitehouse.gov/briefing/uap"
    assert evidence[0].source_class == "official_primary"
    assert evidence[0].title == "White House releases UAP records"
    assert "UAP transparency" in evidence[0].snippet


def test_white_house_dot_gov_source_name_classifies_as_official_primary():
    source_class = research_gate_module._classify_evidence_source(
        ResearchQuery(
            query="White House announcement Trump administration",
            query_intent="official_resolution",
            source_class="official_primary",
        ),
        "The White House (.gov)",
        "",
    )

    assert source_class == "official_primary"


@pytest.mark.parametrize(
    ("query_source_class", "source_name", "source_url", "expected"),
    (
        (
            "rules_source",
            "Indy100",
            "https://www.indy100.com/politics/trump-quotes",
            "other",
        ),
        (
            "market_price",
            "Federal News Network",
            "https://federalnewsnetwork.com/odds",
            "other",
        ),
        (
            "rules_source",
            "Kalshi",
            "https://kalshi.com/markets/KXTRUMPMENTION",
            "rules_source",
        ),
    ),
)
def test_unscoped_structural_query_class_requires_matching_publisher(
    query_source_class,
    source_name,
    source_url,
    expected,
):
    source_class = research_gate_module._classify_evidence_source(
        ResearchQuery(
            query="contract context latest",
            query_intent="rules",
            source_class=query_source_class,
        ),
        source_name,
        source_url,
    )

    assert source_class == expected


def test_duckduckgo_lite_search_ignores_internal_navigation_links(monkeypatch):
    html = b"""
    <html><body>
      <a href="https://duckduckgo.com/">here</a>
      <a rel="nofollow" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fapnews.com%2Fuap&amp;rut=x">
        AP reports on UAP records
      </a>
      <td class='result-snippet'>AP reports on UAP records.</td>
    </body></html>
    """

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _limit):
            return html

    monkeypatch.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: _Response())

    evidence = _duckduckgo_lite_search(
        ResearchQuery(
            query="Trump UAP records AP",
            query_intent="supporting",
            source_class="reputable_secondary",
        ),
        limit=2,
    )

    assert [item.source_url for item in evidence] == ["https://apnews.com/uap"]


@pytest.mark.asyncio
async def test_default_search_provider_falls_back_to_duckduckgo_lite(monkeypatch):
    calls: list[str] = []

    def rss_unavailable(_query):
        calls.append("rss")
        raise TimeoutError("rss unavailable")

    def fallback_search(query, **_kwargs):
        calls.append("fallback")
        return [
            ResearchEvidence(
                source_class=query.source_class,
                source_name="White House",
                source_url="https://www.whitehouse.gov/briefing/uap",
                title="White House UAP directive",
                snippet="Official UAP directive.",
                claim_type=query.query_intent,
            )
        ]

    monkeypatch.setattr(research_gate_module, "_rss_search", rss_unavailable)
    monkeypatch.setattr(research_gate_module, "_duckduckgo_lite_search", fallback_search)

    evidence = await default_search_provider(
        ResearchQuery(
            query="Trump UAP directive",
            query_intent="official_resolution",
            source_class="official_primary",
        )
    )

    assert calls == ["rss", "fallback"]
    assert evidence[0].source_url == "https://www.whitehouse.gov/briefing/uap"


def test_federal_register_search_parses_structured_api_results(monkeypatch):
    payload = json.dumps(
        {
            "results": [
                {
                    "title": "Executive Order on UAP Records",
                    "html_url": "https://www.federalregister.gov/documents/2026/06/30/uap-records",
                    "abstract": "Executive order releasing UAP records.",
                    "publication_date": "2026-06-30",
                    "type": "Presidential Document",
                }
            ]
        }
    ).encode()

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _limit):
            return payload

    monkeypatch.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: _Response())

    evidence = _federal_register_search(
        ResearchQuery(
            query="site:federalregister.gov Trump UAP records executive order",
            query_intent="official_resolution",
            source_class="official_primary",
        )
    )

    assert len(evidence) == 1
    assert evidence[0].source_name == "Federal Register"
    assert evidence[0].source_url.endswith("/uap-records")
    assert evidence[0].source_class == "official_primary"
    assert evidence[0].claim_type == "official_resolution"
    assert "Executive order releasing" in evidence[0].snippet


@pytest.mark.asyncio
async def test_default_search_provider_uses_federal_register_api_first(monkeypatch):
    calls: list[str] = []

    def federal_register_search(query, **_kwargs):
        calls.append("federal_register")
        return [
            ResearchEvidence(
                source_class=query.source_class,
                source_name="Federal Register",
                source_url="https://www.federalregister.gov/documents/example",
                title="Executive Order",
                snippet="Official order.",
                claim_type=query.query_intent,
            )
        ]

    def rss_search(_query):
        calls.append("rss")
        raise AssertionError("rss should not be called when official API returns evidence")

    monkeypatch.setattr(
        research_gate_module,
        "_federal_register_search",
        federal_register_search,
    )
    monkeypatch.setattr(research_gate_module, "_rss_search", rss_search)

    evidence = await default_search_provider(
        ResearchQuery(
            query="site:federalregister.gov Trump UAP records",
            query_intent="official_resolution",
            source_class="official_primary",
        )
    )

    assert calls == ["federal_register"]
    assert evidence[0].source_name == "Federal Register"


def test_inconsistent_research_reason_continues_researching():
    evidence = [
        ResearchEvidence(
            source_class="other",
            source_name="Inshorts",
            source_url="https://example.com/iran-oil",
            title="Iran exports surge",
            snippet="Exports rose sharply, but this may be barrels on water.",
            claim_type="leading_indicator",
            supports_direction="neutral",
            supports_confidence=0.4,
        )
    ]

    verdict = decide_research_verdict(
        evidence=evidence,
        model_direction="no",
        model_confidence=0.75,
        model_reason=(
            "The headline indicates a significant increase in Iran's oil exports, "
            "which could potentially lead to higher average daily crude oil production."
        ),
        yes_ask=0.02,
        no_ask=0.98,
        live_mode=True,
    )

    assert verdict.status == ResearchStatus.CONTINUE_RESEARCHING
    assert verdict.skip_reason == "direction_reason_conflict"
    assert verdict.force_side is None


def test_single_resolution_source_requires_independent_corroboration():
    verdict = decide_research_verdict(
        evidence=[
            ResearchEvidence(
                source_class="resolution_source",
                source_name="Official source",
                source_url="https://agency.gov/result",
                title="Official result",
                snippet="Official data supports YES.",
                claim_type="resolution",
                supports_direction="yes",
                supports_confidence=0.95,
            )
        ],
        queries=[],
        model_direction="yes",
        model_confidence=0.9,
        model_reason="Official source supports the YES side.",
        estimated_probability_yes=0.72,
        yes_ask=0.5,
        no_ask=0.5,
        live_mode=False,
    )

    assert verdict.status == ResearchStatus.CONTINUE_RESEARCHING
    assert verdict.skip_reason == "insufficient_corroboration"
    assert verdict.force_side is None


def test_reputable_secondary_resolution_evidence_can_satisfy_public_statement_market():
    fresh = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    verdict = decide_research_verdict(
        evidence=[
            ResearchEvidence(
                source_class="reputable_secondary",
                source_name="Wall Street Journal",
                source_url="https://wsj.com/schumer",
                title="Senator says Schumer should be replaced",
                snippet=(
                    "A current Democratic senator publicly said Chuck Schumer "
                    "should be replaced as Senate Democratic Leader."
                ),
                claim_type="official_resolution",
                supports_direction="yes",
                supports_confidence=0.85,
                retrieved_at=fresh,
            ),
            ResearchEvidence(
                source_class="reputable_secondary",
                source_name="Time",
                source_url="https://time.com/schumer",
                title="Backlash grows",
                snippet="Time independently reports the public statement and its context.",
                claim_type="supporting",
                supports_direction="yes",
                supports_confidence=0.75,
                retrieved_at=fresh,
            ),
            ResearchEvidence(
                source_class="reputable_secondary",
                source_name="New York Times",
                source_url="https://nytimes.com/schumer-counter",
                title="Democrats stop short of calling for replacement",
                snippet="Other Democratic senators criticized Schumer but did not ask him to step down.",
                claim_type="disconfirming",
                supports_direction="no",
                supports_confidence=0.35,
                retrieved_at=fresh,
            ),
        ],
        queries=[
            ResearchQuery(
                query="Schumer should step down counter evidence",
                query_intent="disconfirming",
                source_class="reputable_secondary",
            )
        ],
        model_direction="yes",
        model_confidence=0.76,
        model_reason=(
            "Trade YES because reputable reporting quotes a current Democratic "
            "senator publicly calling for Schumer to be replaced, which directly "
            "matches the market rules. Market YES ask is 2c versus 8% probability, "
            "leaving positive edge after costs. Counter evidence is that many "
            "critics stopped short of asking him to step down."
        ),
        estimated_probability_yes=0.08,
        yes_ask=0.02,
        no_ask=1.0,
        live_mode=False,
        require_decision_grade=True,
    )

    assert verdict.status == ResearchStatus.DECISION_GRADE_CANDIDATE
    assert verdict.force_side == "yes"
    assert verdict.estimated_edge is not None and verdict.estimated_edge > 0


def test_decision_grade_requires_directional_counter_evidence():
    fresh = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    verdict = decide_research_verdict(
        evidence=[
            ResearchEvidence(
                source_class="reputable_secondary",
                source_name="Wall Street Journal",
                source_url="https://wsj.com/schumer",
                title="Senator says Schumer should be replaced",
                snippet=(
                    "A current Democratic senator publicly said Chuck Schumer "
                    "should be replaced as Senate Democratic Leader."
                ),
                claim_type="official_resolution",
                supports_direction="yes",
                supports_confidence=0.85,
                retrieved_at=fresh,
            ),
            ResearchEvidence(
                source_class="reputable_secondary",
                source_name="Time",
                source_url="https://time.com/schumer",
                title="Backlash grows",
                snippet="Time independently reports the public statement and its context.",
                claim_type="supporting",
                supports_direction="yes",
                supports_confidence=0.75,
                retrieved_at=fresh,
            ),
            ResearchEvidence(
                source_class="reputable_secondary",
                source_name="New York Times",
                source_url="https://nytimes.com/schumer-counter",
                title="Senate votes on spending bill",
                snippet="The Senate focused on unrelated spending legislation.",
                claim_type="disconfirming",
                supports_direction="neutral",
                supports_confidence=0.7,
                retrieved_at=fresh,
            ),
        ],
        queries=[
            ResearchQuery(
                query="Schumer should step down counter evidence",
                query_intent="disconfirming",
                source_class="reputable_secondary",
            )
        ],
        model_direction="yes",
        model_confidence=0.76,
        model_reason=(
            "Trade YES because reputable reporting quotes a current Democratic "
            "senator publicly calling for Schumer to be replaced. Market YES "
            "ask is 2c versus 8% probability, leaving positive edge after costs. "
            "No disconfirming source shows the opposite."
        ),
        estimated_probability_yes=0.08,
        yes_ask=0.02,
        no_ask=1.0,
        live_mode=False,
        require_decision_grade=True,
    )

    assert verdict.status == ResearchStatus.NEEDS_COUNTER_EVIDENCE
    assert verdict.skip_reason == "missing_counter_evidence"


def test_decision_grade_requires_directional_support_for_selected_side():
    fresh = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    candidate = ResearchVerdict(
        status=ResearchStatus.TRADE_CANDIDATE,
        attempted=True,
        queries=[
            ResearchQuery(
                "MAGA mention evidence against YES",
                "disconfirming",
                "reputable_secondary",
            )
        ],
        evidence=[
            ResearchEvidence(
                source_class="rules_source",
                source_name="Kalshi",
                source_url="https://kalshi.com/markets/KXTRUMPMENTION",
                title="Contract terms",
                snippet="The market resolves Yes if Trump says MAGA.",
                claim_type="rules",
                supports_direction="neutral",
                retrieved_at=fresh,
            ),
            ResearchEvidence(
                source_class="reputable_secondary",
                source_name="New York Times",
                source_url="https://nytimes.com/no-maga-confirmation",
                title="No MAGA wording confirmed",
                snippet="No direct evidence confirms Trump will say MAGA.",
                claim_type="disconfirming",
                supports_direction="no",
                supports_confidence=0.2,
                retrieved_at=fresh,
            ),
        ],
        summary=(
            "Trade YES at 58c with 81% estimated probability despite no source "
            "directly supporting the selected side."
        ),
        force_side="yes",
        estimated_probability=0.81,
        market_price=0.58,
        estimated_edge=0.22,
    )

    verdict = _decision_grade_verdict(candidate, model_reason=candidate.summary)

    assert verdict.status == ResearchStatus.NEEDS_COUNTER_EVIDENCE
    assert verdict.skip_reason == "missing_directional_support"
    assert verdict.force_side is None


def test_decision_grade_rejects_low_confidence_selected_side_support():
    fresh = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    candidate = ResearchVerdict(
        status=ResearchStatus.TRADE_CANDIDATE,
        attempted=True,
        queries=[
            ResearchQuery(
                "official Iran crude production threshold counter evidence",
                "disconfirming",
                "reputable_secondary",
            )
        ],
        evidence=[
            ResearchEvidence(
                source_class="resolution_source",
                source_name="OPEC",
                source_url="https://opec.org/momr",
                title="Iran crude production threshold",
                snippet="The official table lists Iran crude production.",
                claim_type="resolution",
                supports_direction="yes",
                supports_confidence=0.01,
                retrieved_at=fresh,
            ),
            ResearchEvidence(
                source_class="reputable_secondary",
                source_name="New York Times",
                source_url="https://nytimes.com/greenland",
                title="Greenland policy debate",
                snippet="The article discusses an unrelated diplomatic dispute.",
                claim_type="disconfirming",
                supports_direction="no",
                supports_confidence=0.9,
                retrieved_at=fresh,
            ),
        ],
        summary="Specific edge reasoning based on a weak directional label.",
        force_side="yes",
        estimated_probability=0.8,
        market_price=0.5,
        estimated_edge=0.29,
    )

    verdict = _decision_grade_verdict(candidate, model_reason=candidate.summary)

    assert verdict.status == ResearchStatus.NEEDS_COUNTER_EVIDENCE
    assert verdict.skip_reason == "missing_directional_support"


def test_decision_grade_rejects_unrelated_opposite_counter_evidence():
    fresh = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    candidate = ResearchVerdict(
        status=ResearchStatus.TRADE_CANDIDATE,
        attempted=True,
        queries=[
            ResearchQuery(
                "official Iran crude production threshold counter evidence",
                "disconfirming",
                "reputable_secondary",
            )
        ],
        evidence=[
            ResearchEvidence(
                source_class="resolution_source",
                source_name="OPEC",
                source_url="https://opec.org/momr",
                title="Iran crude production threshold",
                snippet="The official table lists Iran crude production above the threshold.",
                claim_type="resolution",
                supports_direction="yes",
                supports_confidence=0.9,
                retrieved_at=fresh,
            ),
            ResearchEvidence(
                source_class="reputable_secondary",
                source_name="Reuters",
                source_url="https://reuters.com/iran-production",
                title="Iran crude production rises",
                snippet="Iran crude production may exceed the market threshold.",
                claim_type="corroboration",
                supports_direction="yes",
                supports_confidence=0.8,
                retrieved_at=fresh,
            ),
            ResearchEvidence(
                source_class="reputable_secondary",
                source_name="New York Times",
                source_url="https://nytimes.com/greenland",
                title="Greenland policy debate",
                snippet="The article discusses an unrelated diplomatic dispute.",
                claim_type="disconfirming",
                supports_direction="no",
                supports_confidence=0.9,
                retrieved_at=fresh,
            ),
        ],
        summary=(
            "Trade YES because the OPEC table and Reuters report show production "
            "above the threshold; the Greenland article is unrelated."
        ),
        force_side="yes",
        estimated_probability=0.8,
        market_price=0.5,
        estimated_edge=0.29,
    )

    verdict = _decision_grade_verdict(candidate, model_reason=candidate.summary)

    assert verdict.status == ResearchStatus.NEEDS_COUNTER_EVIDENCE
    assert verdict.skip_reason == "missing_counter_evidence"


def test_reputable_secondary_supporting_evidence_alone_is_not_settlement_evidence():
    fresh = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    verdict = decide_research_verdict(
        evidence=[
            ResearchEvidence(
                source_class="reputable_secondary",
                source_name="Time",
                source_url="https://time.com/schumer",
                title="Backlash grows",
                snippet="Time reports broad backlash but no direct qualifying public statement.",
                claim_type="supporting",
                supports_direction="yes",
                supports_confidence=0.75,
                retrieved_at=fresh,
            ),
            ResearchEvidence(
                source_class="reputable_secondary",
                source_name="New York Times",
                source_url="https://nytimes.com/schumer-counter",
                title="Democrats stop short of calling for replacement",
                snippet="Other Democratic senators criticized Schumer but did not ask him to step down.",
                claim_type="disconfirming",
                supports_direction="no",
                supports_confidence=0.35,
                retrieved_at=fresh,
            ),
        ],
        queries=[
            ResearchQuery(
                query="Schumer should step down counter evidence",
                query_intent="disconfirming",
                source_class="reputable_secondary",
            )
        ],
        model_direction="yes",
        model_confidence=0.76,
        model_reason=(
            "Trade YES because broad criticism differs from market price. Counter "
            "evidence says critics stopped short of asking him to step down."
        ),
        estimated_probability_yes=0.08,
        yes_ask=0.02,
        no_ask=1.0,
        live_mode=False,
        require_decision_grade=True,
    )

    assert verdict.status == ResearchStatus.NEEDS_RESEARCH
    assert verdict.skip_reason == "missing_resolution_source"


def test_decision_grade_ignores_stale_background_when_critical_evidence_retrieved_now():
    fresh = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    verdict = decide_research_verdict(
        evidence=[
            ResearchEvidence(
                source_class="reputable_secondary",
                source_name="Wall Street Journal",
                source_url="https://wsj.com/schumer",
                title="Senator says Schumer should be replaced",
                snippet="A current Democratic senator publicly said Schumer should be replaced.",
                claim_type="official_resolution",
                supports_direction="yes",
                supports_confidence=0.85,
                published_at="2026-03-20T12:00:00Z",
                retrieved_at=fresh,
            ),
            ResearchEvidence(
                source_class="reputable_secondary",
                source_name="New York Times",
                source_url="https://nytimes.com/schumer-counter",
                title="Democrats stop short",
                snippet="Other Democrats criticized Schumer but did not ask him to step down.",
                claim_type="disconfirming",
                supports_direction="no",
                supports_confidence=0.35,
                published_at="2025-10-14T12:00:00Z",
                retrieved_at=fresh,
            ),
            ResearchEvidence(
                source_class="reputable_secondary",
                source_name="PBS",
                source_url="https://pbs.org/baserate",
                title="Old party unity context",
                snippet="Old party unity context.",
                claim_type="base_rate",
                supports_direction="neutral",
                supports_confidence=0.0,
                published_at="2024-07-19T12:00:00Z",
                retrieved_at=fresh,
            ),
        ],
        queries=[
            ResearchQuery(
                query="Schumer should step down counter evidence",
                query_intent="disconfirming",
                source_class="reputable_secondary",
            )
        ],
        model_direction="yes",
        model_confidence=0.76,
        model_reason=(
            "Trade YES because reputable reporting quotes a current Democratic "
            "senator publicly calling for Schumer to be replaced, which directly "
            "matches the market rules. Market YES ask is 2c versus 8% probability, "
            "leaving positive edge after costs. Counter evidence is that many "
            "critics stopped short of asking him to step down."
        ),
        estimated_probability_yes=0.08,
        yes_ask=0.02,
        no_ask=1.0,
        live_mode=False,
        require_decision_grade=True,
    )

    assert verdict.status == ResearchStatus.DECISION_GRADE_CANDIDATE


def test_decision_grade_blocks_when_critical_evidence_not_freshly_retrieved():
    stale = "2026-03-20T12:00:00Z"

    verdict = decide_research_verdict(
        evidence=[
            ResearchEvidence(
                source_class="reputable_secondary",
                source_name="Wall Street Journal",
                source_url="https://wsj.com/schumer",
                title="Senator says Schumer should be replaced",
                snippet="A current Democratic senator publicly said Schumer should be replaced.",
                claim_type="official_resolution",
                supports_direction="yes",
                supports_confidence=0.85,
                published_at=stale,
                retrieved_at=stale,
            ),
            ResearchEvidence(
                source_class="reputable_secondary",
                source_name="New York Times",
                source_url="https://nytimes.com/schumer-counter",
                title="Democrats stop short",
                snippet="Other Democrats criticized Schumer but did not ask him to step down.",
                claim_type="disconfirming",
                supports_direction="no",
                supports_confidence=0.35,
                published_at=stale,
                retrieved_at=stale,
            ),
        ],
        queries=[
            ResearchQuery(
                query="Schumer should step down counter evidence",
                query_intent="disconfirming",
                source_class="reputable_secondary",
            )
        ],
        model_direction="yes",
        model_confidence=0.76,
        model_reason=(
            "Trade YES because reputable reporting quotes a current Democratic "
            "senator publicly calling for Schumer to be replaced, which directly "
            "matches the market rules. Market YES ask is 2c versus 8% probability, "
            "leaving positive edge after costs. Counter evidence is that many "
            "critics stopped short of asking him to step down."
        ),
        estimated_probability_yes=0.08,
        yes_ask=0.02,
        no_ask=1.0,
        live_mode=False,
        require_decision_grade=True,
    )

    assert verdict.status == ResearchStatus.NEEDS_COUNTER_EVIDENCE
    assert verdict.skip_reason == "source_freshness_ttl_exceeded"


def test_decision_grade_synthesizes_specific_reason_from_structured_evidence():
    fresh = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    verdict = decide_research_verdict(
        evidence=[
            ResearchEvidence(
                source_class="reputable_secondary",
                source_name="Wall Street Journal",
                source_url="https://wsj.com/schumer",
                title="Senator says Schumer should be replaced",
                snippet="A current Democratic senator publicly said Schumer should be replaced.",
                claim_type="official_resolution",
                supports_direction="yes",
                supports_confidence=0.85,
                retrieved_at=fresh,
            ),
            ResearchEvidence(
                source_class="reputable_secondary",
                source_name="New York Times",
                source_url="https://nytimes.com/schumer-counter",
                title="Democrats stop short",
                snippet="Other Democrats criticized Schumer but did not ask him to step down.",
                claim_type="disconfirming",
                supports_direction="no",
                supports_confidence=0.35,
                retrieved_at=fresh,
            ),
        ],
        queries=[
            ResearchQuery(
                query="Schumer should step down counter evidence",
                query_intent="disconfirming",
                source_class="reputable_secondary",
            )
        ],
        model_direction="yes",
        model_confidence=0.76,
        model_reason="Growing dissatisfaction suggests a chance of replacement.",
        estimated_probability_yes=0.08,
        yes_ask=0.02,
        no_ask=1.0,
        live_mode=False,
        require_decision_grade=True,
    )

    assert verdict.status == ResearchStatus.DECISION_GRADE_CANDIDATE
    assert verdict.summary != "Growing dissatisfaction suggests a chance of replacement."
    assert "Wall Street Journal" in verdict.summary
    assert "Market YES ask" in verdict.summary
    assert "edge" in verdict.summary
    assert "Counter" in verdict.summary


def test_decision_grade_synthesized_reason_uses_no_probability_for_no_side():
    fresh = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    verdict = decide_research_verdict(
        evidence=[
            ResearchEvidence(
                source_class="resolution_source",
                source_name="Official Source",
                source_url="https://agency.gov/result",
                title="Official result below threshold",
                snippet="Official result is below the YES threshold.",
                claim_type="official_resolution",
                supports_direction="no",
                supports_confidence=0.85,
                retrieved_at=fresh,
            ),
            ResearchEvidence(
                source_class="reputable_secondary",
                source_name="Reuters",
                source_url="https://reuters.com/counter",
                title="Countercase",
                snippet="Reuters says late revisions could still move the result above threshold.",
                claim_type="disconfirming",
                supports_direction="yes",
                supports_confidence=0.35,
                retrieved_at=fresh,
            ),
        ],
        queries=[
            ResearchQuery(
                query="threshold revision counter evidence",
                query_intent="disconfirming",
                source_class="reputable_secondary",
            )
        ],
        model_direction="no",
        model_confidence=0.76,
        model_reason="Official data looks below threshold.",
        estimated_probability_yes=0.12,
        yes_ask=0.9,
        no_ask=0.05,
        live_mode=False,
        require_decision_grade=True,
    )

    assert verdict.status == ResearchStatus.DECISION_GRADE_CANDIDATE
    assert "estimated NO probability 0.88" in verdict.summary
    assert "estimated YES probability" not in verdict.summary


def test_live_tail_risk_blocks_chosen_no_side():
    verdict = decide_research_verdict(
        evidence=[
            ResearchEvidence(
                source_class="resolution_source",
                source_name="Resolution Source",
                source_url="https://agency.gov/resolution",
                title="Resolution source table",
                snippet="Official data supports NO.",
                claim_type="resolution",
                supports_direction="no",
                supports_confidence=0.95,
            ),
            ResearchEvidence(
                source_class="reputable_secondary",
                source_name="Reuters",
                source_url="https://reuters.com/no",
                title="Wire confirmation",
                snippet="Wire report confirms the official data is below the threshold.",
                claim_type="corroboration",
                supports_direction="no",
                supports_confidence=0.8,
            ),
        ],
        queries=[],
        model_direction="no",
        model_confidence=0.95,
        model_reason="Official source is below the threshold.",
        estimated_probability_yes=0.05,
        yes_ask=0.98,
        no_ask=0.02,
        live_mode=True,
    )

    assert verdict.status == ResearchStatus.HARD_CAPITAL_BLOCK
    assert verdict.skip_reason == "no_trade_capital_protection"


def test_direction_confidence_without_probability_continues_researching():
    evidence = [
        ResearchEvidence(
            source_class="resolution_source",
            source_name="Resolution Source",
            source_url="https://agency.gov/resolution",
            title="Resolution source table",
            snippet="Official data supports YES.",
            claim_type="resolution",
            supports_direction="yes",
            supports_confidence=0.9,
        ),
        ResearchEvidence(
            source_class="reputable_secondary",
            source_name="Reuters",
            source_url="https://reuters.com/yes",
            title="Wire confirmation",
            snippet="Wire report supports YES.",
            claim_type="corroboration",
            supports_direction="yes",
            supports_confidence=0.8,
        ),
    ]

    verdict = decide_research_verdict(
        evidence=evidence,
        queries=[],
        model_direction="yes",
        model_confidence=0.8,
        model_reason="Evidence points yes with high confidence.",
        yes_ask=0.6,
        no_ask=0.4,
        live_mode=False,
    )

    assert verdict.status == ResearchStatus.CONTINUE_RESEARCHING
    assert verdict.skip_reason == "missing_estimated_probability"


def _decision_grade_evidence() -> list[ResearchEvidence]:
    fresh = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return [
        ResearchEvidence(
            source_class="official_primary",
            source_name="Official ministry",
            source_url="https://agency.gov/result",
            title="Signed agreement notice",
            snippet="Official notice says the agreement was signed before the deadline.",
            claim_type="official_resolution",
            supports_direction="yes",
            supports_confidence=0.9,
            retrieved_at=fresh,
        ),
        ResearchEvidence(
            source_class="rules_source",
            source_name="Kalshi",
            source_url="https://kalshi.com/markets/KXCEASEFIRE-26JUL01",
            title="Contract rules",
            snippet="Rules require a signed agreement by July 1 for YES resolution.",
            claim_type="rules",
            supports_direction="neutral",
            supports_confidence=0.0,
            retrieved_at=fresh,
        ),
        ResearchEvidence(
            source_class="reputable_secondary",
            source_name="Reuters",
            source_url="https://reuters.com/signed",
            title="Agreement signed",
            snippet="Reuters independently reports the agreement was signed.",
            claim_type="supporting",
            supports_direction="yes",
            supports_confidence=0.8,
            retrieved_at=fresh,
        ),
        ResearchEvidence(
            source_class="reputable_secondary",
            source_name="AP",
            source_url="https://apnews.com/not-yet-filed",
            title="Ratification objection",
            snippet="AP notes opponents argue ratification of the agreement could still fail.",
            claim_type="disconfirming",
            supports_direction="no",
            supports_confidence=0.35,
            retrieved_at=fresh,
        ),
    ]


def _decision_grade_queries() -> list[ResearchQuery]:
    return [
        ResearchQuery("official signed agreement July 1", "official_resolution", "official_primary"),
        ResearchQuery("Kalshi agreement signed rules July 1", "rules", "rules_source"),
        ResearchQuery("agreement signed July 1", "supporting", "reputable_secondary"),
        ResearchQuery("agreement not signed July 1 objection", "disconfirming", "reputable_secondary"),
        ResearchQuery("agreement base rate ratification", "base_rate", "reputable_secondary"),
        ResearchQuery("agreement market price Kalshi", "market_price", "market_price"),
        ResearchQuery("agreement latest stale check", "staleness_check", "reputable_secondary"),
    ]


def test_high_confidence_reputable_supporting_evidence_is_settlement_aligned():
    fresh = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    evidence = [
        ResearchEvidence(
            source_class="reputable_secondary",
            source_name="The Hill",
            source_url="https://thehill.com/policy/example",
            title="Current senators call for leader to be replaced",
            snippet="Current caucus members publicly called for the leader to be replaced.",
            claim_type="supporting",
            supports_direction="yes",
            supports_confidence=0.9,
            retrieved_at=fresh,
        ),
        ResearchEvidence(
            source_class="reputable_secondary",
            source_name="Time Magazine",
            source_url="https://time.com/example",
            title="Leadership backlash grows",
            snippet="Several current members publicly said new leadership is needed.",
            claim_type="supporting",
            supports_direction="yes",
            supports_confidence=0.8,
            retrieved_at=fresh,
        ),
    ]

    verdict = decide_research_verdict(
        evidence=evidence,
        queries=_decision_grade_queries(),
        model_direction="neutral",
        model_confidence=0.7,
        model_reason="Evidence has support but direction remains unconfirmed.",
        estimated_probability_yes=0.5,
        yes_ask=0.02,
        no_ask=0.99,
        live_mode=False,
        require_decision_grade=True,
    )

    assert verdict.status == ResearchStatus.NEEDS_COUNTER_EVIDENCE
    assert verdict.skip_reason == "ambiguous_direction"


@pytest.mark.asyncio
async def test_strict_gate_uses_strong_settlement_aligned_evidence_when_adjudicator_stays_neutral():
    fresh = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    market = SimpleNamespace(
        ticker="KXDEMSCHUMER-27-JUL01",
        title=(
            "Will any current member of the Democratic Senate caucus publicly "
            "state that Chuck Schumer should step down, resign, or be replaced "
            "as Senate Democratic Leader before Jul 1, 2026?"
        ),
        rules_primary=(
            "This market resolves Yes if a current member of the Democratic "
            "Senate caucus publicly states that Chuck Schumer should step "
            "down, resign, or be replaced as Senate Democratic Leader before "
            "Jul 1, 2026."
        ),
        rules_secondary="Reputable reporting may be used for resolution.",
        settlement_sources=(
            SettlementSource(label="The Wall Street Journal", domain="wsj.com"),
        ),
    )

    supporting = [
        ResearchEvidence(
            source_class="reputable_secondary",
            source_name="The Hill",
            source_url="https://thehill.com",
            aggregator_url="https://news.google.com/rss/articles/the-hill-schumer",
            title="Here are the Democrats calling for Schumer to be replaced as leader",
            snippet=(
                "Current Democratic senators publicly called for Chuck Schumer "
                "to be replaced as leader before the deadline."
            ),
            claim_type="supporting",
            supports_direction="neutral",
            supports_confidence=0.0,
            retrieved_at=fresh,
        ),
        ResearchEvidence(
            source_class="reputable_secondary",
            source_name="Time Magazine",
            source_url="https://time.com",
            aggregator_url="https://news.google.com/rss/articles/time-schumer",
            title="'We Need New Leadership': Chuck Schumer Faces Broad Backlash",
            snippet=(
                "A current Democratic caucus member said new leadership is "
                "needed, directly supporting the Schumer replacement condition."
            ),
            claim_type="supporting",
            supports_direction="neutral",
            supports_confidence=0.0,
            retrieved_at=fresh,
        ),
    ]
    counter = ResearchEvidence(
        source_class="reputable_secondary",
        source_name="The New York Times",
        source_url="https://nytimes.com/shutdown-context",
        title="The Senate votes to avert a shutdown after Schumer relents",
        snippet="Background coverage does not report a replacement call.",
        claim_type="disconfirming",
        supports_direction="no",
        supports_confidence=0.35,
        retrieved_at=fresh,
    )

    async def search_provider(query):
        if query.query_intent == "disconfirming":
            return [counter]
        if query.query_intent == "supporting":
            return supporting
        return []

    async def adjudicator(**_kwargs):
        return {
            "direction": "neutral",
            "confidence": 0.4,
            "estimated_probability_yes": 0.5,
            "reason": "Adjudicator remained neutral despite directional evidence.",
            "counterclaims": ["Background coverage does not report a replacement call."],
            "open_questions": [],
        }

    verdict = await run_research_gate(
        SimpleNamespace(headline="", source="research_prewarm"),
        market,
        model_direction="neutral",
        model_confidence=0.0,
        model_reason="scheduled research prewarm",
        yes_ask=0.02,
        no_ask=0.99,
        live_mode=False,
        search_provider=search_provider,
        direct_fetcher=lambda *_args: None,
        adjudicator=adjudicator,
        max_queries=8,
        require_decision_grade=True,
    )

    assert verdict.status == ResearchStatus.DECISION_GRADE_CANDIDATE
    assert verdict.force_side == "yes"
    assert verdict.market_price == pytest.approx(0.02)
    assert verdict.estimated_edge is not None
    assert verdict.estimated_edge > 0
    assert "The Hill" in verdict.summary


def test_low_confidence_reputable_supporting_evidence_is_not_settlement_aligned():
    fresh = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    evidence = [
        ResearchEvidence(
            source_class="reputable_secondary",
            source_name="BorderReport",
            source_url="https://borderreport.example.com/oil",
            title="Oil exports continue",
            snippet="Oil exports may continue despite opposition.",
            claim_type="supporting",
            supports_direction="yes",
            supports_confidence=0.4,
            retrieved_at=fresh,
        )
    ]

    verdict = decide_research_verdict(
        evidence=evidence,
        queries=_decision_grade_queries(),
        model_direction="neutral",
        model_confidence=0.7,
        model_reason="Weak evidence only.",
        estimated_probability_yes=0.5,
        yes_ask=0.04,
        no_ask=0.99,
        live_mode=False,
        require_decision_grade=True,
    )

    assert verdict.status == ResearchStatus.NEEDS_RESEARCH
    assert verdict.skip_reason == "missing_resolution_source"


def test_decision_grade_fails_without_counter_evidence_search():
    queries = [
        query for query in _decision_grade_queries() if query.query_intent != "disconfirming"
    ]
    evidence = [
        item for item in _decision_grade_evidence() if item.claim_type != "disconfirming"
    ]

    verdict = decide_research_verdict(
        evidence=evidence,
        queries=queries,
        model_direction="yes",
        model_confidence=0.82,
        model_reason=(
            "Official notice and Reuters support YES now; downside is no "
            "separate disconfirming search has been recorded."
        ),
        estimated_probability_yes=0.68,
        yes_ask=0.55,
        no_ask=0.47,
        live_mode=False,
        require_decision_grade=True,
    )

    assert verdict.status == ResearchStatus.NEEDS_COUNTER_EVIDENCE
    assert verdict.skip_reason == "missing_counter_evidence"


def test_decision_grade_rejects_same_side_disconfirming_result():
    fresh = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    evidence = [
        item for item in _decision_grade_evidence() if item.claim_type != "disconfirming"
    ] + [
        ResearchEvidence(
            source_class="official_primary",
            source_name="NWS",
            source_url="https://weather.example.com/climate",
            title="Daily high below contract threshold",
            snippet=(
                "NWS daily climate report lists the observed high as 87F, "
                "below the 99F threshold and supporting YES."
            ),
            claim_type="disconfirming",
            supports_direction="yes",
            supports_confidence=0.95,
            retrieved_at=fresh,
            metric_name="nws_daily_high_temp_f",
            metric_value=87.0,
            metric_unit="F",
        )
    ]

    verdict = decide_research_verdict(
        evidence=evidence,
        queries=_decision_grade_queries(),
        model_direction="yes",
        model_confidence=0.82,
        model_reason=(
            "Trade YES because official evidence supports YES now. Market YES "
            "ask is 55c versus 68% probability, leaving edge. Counter evidence "
            "is the NWS result also supporting YES."
        ),
        estimated_probability_yes=0.68,
        yes_ask=0.55,
        no_ask=0.47,
        live_mode=False,
        require_decision_grade=True,
    )

    assert verdict.status == ResearchStatus.NEEDS_COUNTER_EVIDENCE
    assert verdict.skip_reason == "missing_counter_evidence"


def test_decision_grade_rejects_stale_time_sensitive_weather_evidence():
    stale = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat().replace(
        "+00:00",
        "Z",
    )
    evidence = [
        item
        for item in _decision_grade_evidence()
        if item.claim_type != "official_resolution"
    ] + [
        ResearchEvidence(
            source_class="official_primary",
            source_name="NWS",
            source_url="https://weather.example.com/climate",
            title="Daily high over contract threshold",
            snippet="NWS daily climate report lists the observed high as 101F.",
            claim_type="official_resolution",
            supports_direction="yes",
            supports_confidence=0.95,
            retrieved_at=stale,
            metric_name="nws_daily_high_temp_f",
            metric_value=101.0,
            metric_unit="F",
        )
    ]

    verdict = decide_research_verdict(
        evidence=evidence,
        queries=_decision_grade_queries(),
        model_direction="yes",
        model_confidence=0.82,
        model_reason=(
            "NWS observed high supports YES, AP supplies the countercase, "
            "and YES price remains below estimated probability."
        ),
        estimated_probability_yes=0.7,
        yes_ask=0.55,
        no_ask=0.47,
        live_mode=False,
        require_decision_grade=True,
    )

    assert verdict.status != ResearchStatus.DECISION_GRADE_CANDIDATE
    assert verdict.skip_reason == "source_freshness_ttl_exceeded"


def test_decision_grade_accepts_neutral_disconfirming_search_result():
    fresh = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    evidence = [
        item for item in _decision_grade_evidence() if item.claim_type != "disconfirming"
    ] + [
        ResearchEvidence(
            source_class="reputable_secondary",
            source_name="AP",
            source_url="https://apnews.com/counter-search",
            title="Counter search found no direct contradiction",
            snippet="AP background coverage does not dispute the signed agreement.",
            claim_type="disconfirming",
            supports_direction="neutral",
            supports_confidence=0.0,
            retrieved_at=fresh,
        )
    ]

    verdict = decide_research_verdict(
        evidence=evidence,
        queries=_decision_grade_queries(),
        model_direction="yes",
        model_confidence=0.82,
        model_reason=(
            "Official notice and Reuters support YES now; the disconfirming "
            "search found no direct contradiction."
        ),
        estimated_probability_yes=0.68,
        yes_ask=0.55,
        no_ask=0.47,
        live_mode=False,
        require_decision_grade=True,
    )

    assert verdict.status == ResearchStatus.DECISION_GRADE_CANDIDATE


def test_decision_grade_rejects_unrelated_neutral_disconfirming_result_with_two_strong_sources():
    fresh = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    evidence = [
        ResearchEvidence(
            source_class="reputable_secondary",
            source_name="The Hill",
            source_url="https://thehill.com/schumer-replaced",
            title="Here are the Democrats calling for Schumer to be replaced as leader",
            snippet="Current Democratic caucus members called for Schumer to be replaced as leader.",
            claim_type="supporting",
            supports_direction="yes",
            supports_confidence=0.9,
            retrieved_at=fresh,
        ),
        ResearchEvidence(
            source_class="reputable_secondary",
            source_name="Time",
            source_url="https://time.com/schumer-new-leadership",
            title="We need new leadership",
            snippet="Time independently reports backlash and calls for new Democratic leadership.",
            claim_type="supporting",
            supports_direction="yes",
            supports_confidence=0.82,
            retrieved_at=fresh,
        ),
        ResearchEvidence(
            source_class="reputable_secondary",
            source_name="The Hill",
            source_url="https://thehill.com/ice-tactics",
            title="Republicans divided on ICE tactics as shutdown looms",
            snippet="Republicans debated ICE tactics and a shutdown deadline.",
            claim_type="disconfirming",
            supports_direction="neutral",
            supports_confidence=0.5,
            retrieved_at=fresh,
        ),
    ]

    verdict = decide_research_verdict(
        evidence=evidence,
        queries=[
            ResearchQuery("Schumer replacement reporting", "supporting", "reputable_secondary"),
            ResearchQuery("Schumer replacement counter evidence", "disconfirming", "reputable_secondary"),
            ResearchQuery("Schumer replacement market price", "market_price", "market_price"),
        ],
        model_direction="yes",
        model_confidence=0.9,
        model_reason=(
            "Trade YES because The Hill and Time report current Democrats "
            "calling for Schumer replacement. Market YES ask is 2c versus 80% "
            "probability, leaving edge. Counter evidence search returned no "
            "related objection."
        ),
        estimated_probability_yes=0.8,
        yes_ask=0.02,
        no_ask=0.99,
        live_mode=False,
        require_decision_grade=True,
    )

    assert verdict.status == ResearchStatus.NEEDS_COUNTER_EVIDENCE
    assert verdict.skip_reason == "missing_counter_evidence"


def test_decision_grade_fails_neutral_only_evidence():
    fresh = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    evidence = [
        ResearchEvidence(
            source_class="official_primary",
            source_name="Official source",
            source_url="https://agency.gov/current",
            title="Current page",
            snippet="Current page lists background terms only.",
            claim_type="official_resolution",
            supports_direction="neutral",
            supports_confidence=0.0,
            retrieved_at=fresh,
        ),
        ResearchEvidence(
            source_class="reputable_secondary",
            source_name="Reuters",
            source_url="https://reuters.com/current",
            title="Current coverage",
            snippet="Coverage gives background but no resolution fact.",
            claim_type="disconfirming",
            supports_direction="neutral",
            supports_confidence=0.0,
            retrieved_at=fresh,
        ),
    ]

    verdict = decide_research_verdict(
        evidence=evidence,
        queries=_decision_grade_queries(),
        model_direction="yes",
        model_confidence=0.8,
        model_reason="Background only.",
        estimated_probability_yes=0.68,
        yes_ask=0.55,
        no_ask=0.47,
        live_mode=False,
        require_decision_grade=True,
    )

    assert verdict.status == ResearchStatus.NEEDS_COUNTER_EVIDENCE
    assert verdict.skip_reason == "neutral_only_evidence"


def test_decision_grade_neutral_adjudicator_preserves_neutral_only_reason():
    fresh = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    evidence = [
        ResearchEvidence(
            source_class="official_primary",
            source_name="Official source",
            source_url="https://agency.gov/current",
            title="Current page",
            snippet="Current page lists background terms only.",
            claim_type="official_resolution",
            supports_direction="neutral",
            supports_confidence=0.0,
            retrieved_at=fresh,
        ),
        ResearchEvidence(
            source_class="reputable_secondary",
            source_name="Reuters",
            source_url="https://reuters.com/current",
            title="Current coverage",
            snippet="Coverage gives background but no resolution fact.",
            claim_type="disconfirming",
            supports_direction="neutral",
            supports_confidence=0.0,
            retrieved_at=fresh,
        ),
    ]

    verdict = decide_research_verdict(
        evidence=evidence,
        queries=_decision_grade_queries(),
        model_direction="neutral",
        model_confidence=0.0,
        model_reason="Background only.",
        estimated_probability_yes=0.5,
        yes_ask=0.55,
        no_ask=0.47,
        live_mode=False,
        require_decision_grade=True,
    )

    assert verdict.status == ResearchStatus.NEEDS_COUNTER_EVIDENCE
    assert verdict.skip_reason == "neutral_only_evidence"


def test_strict_ambiguous_direction_uses_research_task_state():
    verdict = decide_research_verdict(
        evidence=_decision_grade_evidence(),
        queries=_decision_grade_queries(),
        model_direction="neutral",
        model_confidence=0.8,
        model_reason="Evidence remains mixed and does not choose a side.",
        estimated_probability_yes=0.5,
        yes_ask=0.55,
        no_ask=0.47,
        live_mode=False,
        require_decision_grade=True,
        counterclaims=("Reuters and Yahoo imply opposite CPI direction.",),
        open_questions=("Need current BLS release date and specific threshold context.",),
    )

    assert verdict.status == ResearchStatus.NEEDS_COUNTER_EVIDENCE
    assert verdict.skip_reason == "ambiguous_direction"
    assert verdict.counterclaims == ("Reuters and Yahoo imply opposite CPI direction.",)
    assert verdict.open_questions == (
        "Need current BLS release date and specific threshold context.",
    )
    assert verdict.market_price == pytest.approx(0.55)


def test_strict_missing_probability_preserves_executable_price():
    verdict = decide_research_verdict(
        evidence=_decision_grade_evidence(),
        queries=_decision_grade_queries(),
        model_direction="no",
        model_confidence=0.7,
        model_reason="Official evidence is current, but the probability was not estimated.",
        estimated_probability_yes=None,
        yes_ask=0.55,
        no_ask=0.47,
        live_mode=False,
        require_decision_grade=True,
    )

    assert verdict.status == ResearchStatus.NEEDS_PRICE_EDGE
    assert verdict.skip_reason == "missing_estimated_probability"
    assert verdict.market_price == pytest.approx(0.47)


def test_strict_research_requires_actionable_non_tail_market_price():
    verdict = decide_research_verdict(
        evidence=_decision_grade_evidence(),
        queries=_decision_grade_queries(),
        model_direction="neutral",
        model_confidence=0.8,
        model_reason="Evidence remains mixed and does not choose a side.",
        estimated_probability_yes=0.5,
        yes_ask=0.0,
        no_ask=1.0,
        live_mode=False,
        require_decision_grade=True,
    )

    assert verdict.status == ResearchStatus.UNTRADEABLE
    assert verdict.skip_reason == "non_actionable_market_price"
    assert verdict.market_price is None


def test_decision_grade_fails_without_market_price():
    verdict = decide_research_verdict(
        evidence=_decision_grade_evidence(),
        queries=_decision_grade_queries(),
        model_direction="yes",
        model_confidence=0.82,
        model_reason=(
            "Official notice and Reuters support YES now; AP countercase is "
            "ratification delay, but signed-agreement rules make that objection bounded."
        ),
        estimated_probability_yes=0.68,
        yes_ask=None,
        no_ask=0.47,
        live_mode=False,
        require_decision_grade=True,
    )

    assert verdict.status == ResearchStatus.NEEDS_PRICE_EDGE
    assert verdict.skip_reason == "missing_market_price"


def test_decision_grade_repairs_generic_summary_from_structured_evidence():
    verdict = decide_research_verdict(
        evidence=_decision_grade_evidence(),
        queries=_decision_grade_queries(),
        model_direction="yes",
        model_confidence=0.82,
        model_reason="Research supports YES and executable net edge clears threshold.",
        estimated_probability_yes=0.68,
        yes_ask=0.55,
        no_ask=0.47,
        live_mode=False,
        require_decision_grade=True,
    )

    assert verdict.status == ResearchStatus.DECISION_GRADE_CANDIDATE
    assert verdict.skip_reason is None
    assert verdict.summary != "Research supports YES and executable net edge clears threshold."
    assert "Official ministry" in verdict.summary
    assert "Market YES ask" in verdict.summary
    assert "Counter" in verdict.summary


def test_decision_grade_passes_with_price_countercase_and_specific_reasoning():
    verdict = decide_research_verdict(
        evidence=_decision_grade_evidence(),
        queries=_decision_grade_queries(),
        model_direction="yes",
        model_confidence=0.82,
        model_reason=(
            "Trade YES because the official notice says the agreement was signed "
            "before July 1 and Reuters independently confirms signing. Market YES "
            "ask is 55c versus 68% probability, leaving 12c edge after costs. "
            "AP countercase is ratification delay; this would prove wrong if the "
            "resolution rules required ratification rather than signing."
        ),
        estimated_probability_yes=0.68,
        yes_ask=0.55,
        no_ask=0.47,
        live_mode=False,
        require_decision_grade=True,
    )

    assert verdict.status == ResearchStatus.DECISION_GRADE_CANDIDATE
    assert verdict.force_side == "yes"
    assert verdict.estimated_edge == pytest.approx(0.12)
    assert verdict.market_price == pytest.approx(0.55)


def test_adjudicator_url_keyed_evidence_assessments_label_evidence():
    evidence = [
        ResearchEvidence(
            source_class="official_primary",
            source_name="Official source",
            source_url="https://agency.gov/signed",
            title="Agreement notice",
            snippet="Official notice says the agreement was signed.",
            claim_type="official_resolution",
        )
    ]

    from analysis.research_gate import _apply_adjudication_evidence_assessments

    labeled = _apply_adjudication_evidence_assessments(
        evidence,
        {
            "evidence_assessments": {
                "https://agency.gov/signed": {
                    "supports_direction": "yes",
                    "supports_confidence": 0.9,
                }
            }
        },
    )

    assert labeled[0].supports_direction == "yes"
    assert labeled[0].supports_confidence == pytest.approx(0.9)


def test_adjudicator_evidence_assessments_reject_irrelevant_directional_labels():
    evidence = [
        ResearchEvidence(
            source_class="reputable_secondary",
            source_name="The Hill",
            source_url="https://thehill.com/ice",
            title="Republicans divided on ICE tactics as shutdown looms",
            snippet="The article discusses immigration enforcement and shutdown politics.",
            claim_type="disconfirming",
        ),
        ResearchEvidence(
            source_class="reputable_secondary",
            source_name="WSJ",
            source_url="https://wsj.com/schumer",
            title="Growing frustration with Chuck Schumer spurs talk of replacing him",
            snippet="Democrats are discussing whether Schumer should be replaced.",
            claim_type="official_resolution",
        ),
    ]
    market = SimpleNamespace(
        ticker="KXDEMSCHUMER-27-JUL01",
        title=(
            "Will any current member of the Democratic Senate caucus publicly "
            "state that Chuck Schumer should step down resign or be replaced?"
        ),
        rules_primary="Resolution depends on public statements about Chuck Schumer.",
        rules_secondary="",
    )

    from analysis.research_gate import _apply_adjudication_evidence_assessments

    labeled = _apply_adjudication_evidence_assessments(
        evidence,
        {
            "evidence_assessments": [
                {
                    "source_url": "https://thehill.com/ice",
                    "supports_direction": "no",
                    "supports_confidence": 0.7,
                },
                {
                    "source_url": "https://wsj.com/schumer",
                    "supports_direction": "yes",
                    "supports_confidence": 0.9,
                },
            ]
        },
        market=market,
    )

    assert labeled[0].supports_direction == "neutral"
    assert labeled[0].supports_confidence == 0.0
    assert labeled[1].supports_direction == "yes"
    assert labeled[1].supports_confidence == pytest.approx(0.9)


def test_adjudicator_assessments_require_speech_contract_resolution_phrase():
    evidence = [
        ResearchEvidence(
            source_class="reputable_secondary",
            source_name="Britannica",
            source_url="https://britannica.com/topic/MAGA-movement",
            title="MAGA movement meaning and history under Donald Trump",
            snippet="The article explains the movement's political history.",
            claim_type="supporting",
        ),
        ResearchEvidence(
            source_class="reputable_secondary",
            source_name="BBC",
            source_url="https://bbc.com/white-house-event",
            title="Donald Trump to attend White House dinner",
            snippet="The article notes MAGA movement history and event attendance.",
            claim_type="supporting",
        ),
        ResearchEvidence(
            source_class="reputable_secondary",
            source_name="Reuters",
            source_url="https://reuters.com/trump-maga",
            title="Donald Trump previews MAGA message for rescheduled dinner",
            snippet="Donald Trump said his remarks will repeat Make America Great Again.",
            claim_type="supporting",
        ),
        ResearchEvidence(
            source_class="reputable_secondary",
            source_name="New York Times",
            source_url="https://nytimes.com/no-maga-confirmation",
            title="No MAGA wording confirmed for Donald Trump dinner",
            snippet="No direct evidence confirms Donald Trump will say MAGA at the event.",
            claim_type="disconfirming",
        ),
        ResearchEvidence(
            source_class="reputable_secondary",
            source_name="Britannica",
            source_url="https://britannica.com/topic/MAGA-movement",
            title="MAGA movement meaning and history",
            snippet="The MAGA movement became closely associated with Donald Trump.",
            claim_type="supporting",
        ),
    ]
    market = SimpleNamespace(
        ticker="KXTRUMPMENTION-26JUL24-MAGA",
        title=(
            "What will Donald Trump say during White House Correspondents' Dinner "
            "originally scheduled for July 24th, 2026?"
        ),
        rules_primary=(
            "If Donald Trump says MAGA / Make America Great Again as part of the "
            "White House Correspondents' Dinner, then the market resolves Yes."
        ),
        rules_secondary="Video of the event is the primary resolution source.",
    )

    from analysis.research_gate import _apply_adjudication_evidence_assessments

    labeled = _apply_adjudication_evidence_assessments(
        evidence,
        {
            "evidence_assessments": [
                {
                    "source_url": item.source_url,
                    "supports_direction": "no" if index == 3 else "yes",
                    "supports_confidence": 0.9,
                }
                for index, item in enumerate(evidence)
            ]
        },
        market=market,
    )

    assert [item.supports_direction for item in labeled] == [
        "neutral",
        "neutral",
        "yes",
        "no",
        "neutral",
    ]
    assert labeled[0].supports_confidence == 0.0
    assert labeled[1].supports_confidence == 0.0
    assert labeled[4].supports_confidence == 0.0


def test_leadership_replacement_structured_pass_neutralizes_irrelevant_no_label():
    market = SimpleNamespace(
        ticker="KXDEMSCHUMER-27-JUL01",
        title=(
            "Will any current member of the Democratic Senate caucus publicly "
            "state that Chuck Schumer should step down, resign, or be replaced "
            "as Senate Democratic Leader before Jul 1, 2026?"
        ),
        rules_primary="Resolution depends on replacement calls about Chuck Schumer.",
        rules_secondary="",
    )
    evidence = [
        ResearchEvidence(
            source_class="reputable_secondary",
            source_name="The New York Times",
            source_url="https://nytimes.com/shutdown",
            title="The Senate votes to avert a shutdown after Schumer relents",
            snippet="The article covers government funding and shutdown politics.",
            claim_type="disconfirming",
            supports_direction="no",
            supports_confidence=0.7,
        ),
        ResearchEvidence(
            source_class="reputable_secondary",
            source_name="The Hill",
            source_url="https://thehill.com/criticism",
            title="Democrats criticized Schumer but did not ask him to step down",
            snippet="Other Democrats criticized Schumer but did not ask him to step down.",
            claim_type="disconfirming",
            supports_direction="no",
            supports_confidence=0.7,
        ),
    ]

    labeled = research_gate_module._apply_structured_indicator_evidence(evidence, market)

    assert labeled[0].supports_direction == "neutral"
    assert labeled[0].supports_confidence == 0.0
    assert labeled[1].supports_direction == "no"
    assert labeled[1].supports_confidence == pytest.approx(0.7)


def test_office_departure_structured_pass_relabels_refusal_and_proposals():
    market = SimpleNamespace(
        ticker="KXSULYOKOUT-26MAY-JUL01",
        title="Will Tamas Sulyok be out as President of Hungary before Jul 1, 2026?",
        rules_primary=(
            "If Tamas Sulyok leaves as President of Hungary before Jul 1, "
            "2026, then the market resolves to Yes."
        ),
        rules_secondary="",
    )
    evidence = [
        ResearchEvidence(
            source_class="reputable_secondary",
            source_name="Daily News Hungary",
            source_url="https://dailynewshungary.example.com/refuses",
            title="President Sulyok refuses to resign after PM demand",
            snippet="President Tamas Sulyok refused to resign and remains in office.",
            claim_type="official_resolution",
            supports_direction="yes",
            supports_confidence=0.8,
        ),
        ResearchEvidence(
            source_class="reputable_secondary",
            source_name="Al Jazeera",
            source_url="https://aljazeera.example.com/remove-plan",
            title="Hungary's Magyar to amend the constitution to remove President Tamas Sulyok",
            snippet="The proposal would ease removal after a future parliamentary process.",
            claim_type="staleness_check",
            supports_direction="yes",
            supports_confidence=0.8,
        ),
        ResearchEvidence(
            source_class="reputable_secondary",
            source_name="Reuters",
            source_url="https://reuters.com/resigns",
            title="Tamas Sulyok resigns as President of Hungary",
            snippet="Sulyok resigned as president before the market deadline.",
            claim_type="official_resolution",
            supports_direction="neutral",
            supports_confidence=0.0,
        ),
    ]

    labeled = research_gate_module._apply_structured_indicator_evidence(evidence, market)

    assert labeled[0].supports_direction == "no"
    assert labeled[0].supports_confidence >= 0.8
    assert labeled[1].supports_direction == "neutral"
    assert labeled[1].supports_confidence == 0.0
    assert labeled[2].supports_direction == "yes"
    assert labeled[2].supports_confidence >= 0.8


def test_adjudicator_evidence_assessments_reject_office_departure_proposal_label():
    market = SimpleNamespace(
        ticker="KXSULYOKOUT-26MAY-JUL01",
        title="Will Tamas Sulyok be out as President of Hungary before Jul 1, 2026?",
        rules_primary="This resolves Yes only if Sulyok leaves as president.",
        rules_secondary="",
    )
    evidence = [
        ResearchEvidence(
            source_class="reputable_secondary",
            source_name="Al Jazeera",
            source_url="https://aljazeera.example.com/remove-plan",
            title="Hungary's Magyar to amend the constitution to remove President Tamas Sulyok",
            snippet="The proposal would ease removal after a future parliamentary process.",
            claim_type="staleness_check",
        ),
        ResearchEvidence(
            source_class="reputable_secondary",
            source_name="Daily News Hungary",
            source_url="https://dailynewshungary.example.com/refuses",
            title="President Sulyok refuses to resign after PM demand",
            snippet="President Tamas Sulyok refused to resign and remains in office.",
            claim_type="official_resolution",
        ),
    ]

    from analysis.research_gate import _apply_adjudication_evidence_assessments

    labeled = _apply_adjudication_evidence_assessments(
        evidence,
        {
            "evidence_assessments": [
                {
                    "source_url": "https://aljazeera.example.com/remove-plan",
                    "supports_direction": "yes",
                    "supports_confidence": 0.8,
                },
                {
                    "source_url": "https://dailynewshungary.example.com/refuses",
                    "supports_direction": "no",
                    "supports_confidence": 0.8,
                },
            ]
        },
        market=market,
    )

    assert labeled[0].supports_direction == "neutral"
    assert labeled[0].supports_confidence == 0.0
    assert labeled[1].supports_direction == "no"
    assert labeled[1].supports_confidence == pytest.approx(0.8)


def test_research_prompt_uses_compact_ordinals_not_long_urls():
    from analysis.research_gate import _research_prompt

    long_url = "https://news.google.com/rss/articles/" + ("A" * 600)
    evidence = [
        ResearchEvidence(
            source_class="reputable_secondary",
            source_name="Wire",
            source_url=long_url,
            title=f"Evidence {index}",
            snippet="Snippet text",
            claim_type="supporting",
        )
        for index in range(12)
    ]

    prompt = _research_prompt(
        news=SimpleNamespace(headline="", source="research_prewarm"),
        market=SimpleNamespace(
            ticker="KXLONG",
            title="Long prompt market",
            rules_primary="",
            rules_secondary="",
        ),
        queries=[],
        evidence=evidence,
        yes_ask=0.55,
        no_ask=0.47,
    )

    assert "ordinal=0" in prompt
    assert "source_domain=news.google.com" in prompt
    assert "CURRENT YES ASK: 0.55" in prompt
    assert "CURRENT NO ASK: 0.47" in prompt
    assert "A" * 200 not in prompt
    assert prompt.count("ordinal=") <= 8
    assert len(prompt) < 3500


def test_decision_grade_contradiction_forces_more_counter_evidence():
    evidence = _decision_grade_evidence() + [
        ResearchEvidence(
            source_class="reputable_secondary",
            source_name="Bloomberg",
            source_url="https://bloomberg.example.com/not-signed",
            title="Agreement not signed",
            snippet="Bloomberg reports negotiators have not signed a final agreement.",
            claim_type="disconfirming",
            supports_direction="no",
            supports_confidence=0.8,
            retrieved_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        )
    ]

    verdict = decide_research_verdict(
        evidence=evidence,
        queries=_decision_grade_queries(),
        model_direction="yes",
        model_confidence=0.82,
        model_reason=(
            "Official notice and Reuters support YES now, but Bloomberg reports "
            "negotiators have not signed a final agreement."
        ),
        estimated_probability_yes=0.68,
        yes_ask=0.55,
        no_ask=0.47,
        live_mode=False,
        require_decision_grade=True,
    )

    assert verdict.status == ResearchStatus.NEEDS_COUNTER_EVIDENCE
    assert verdict.skip_reason == "unresolved_contradiction"


def test_research_verdict_log_fields_include_probability_and_freshness_span():
    verdict = decide_research_verdict(
        evidence=[
            ResearchEvidence(
                source_class="resolution_source",
                source_name="OPEC",
                source_url="https://opec.example.com/current",
                title="Current report",
                snippet="Official data supports YES.",
                claim_type="resolution",
                supports_direction="yes",
                supports_confidence=0.9,
                published_at="2026-06-27T10:00:00Z",
                retrieved_at="2026-06-27T10:05:00Z",
            ),
            ResearchEvidence(
                source_class="reputable_secondary",
                source_name="Reuters",
                source_url="https://reuters.com/current",
                title="Wire report",
                snippet="Wire report supports YES.",
                claim_type="corroboration",
                supports_direction="yes",
                supports_confidence=0.8,
                published_at="2026-06-27T11:00:00Z",
                retrieved_at="2026-06-27T11:03:00Z",
            ),
        ],
        queries=[],
        model_direction="yes",
        model_confidence=0.8,
        model_reason="Fresh settlement-backed evidence supports yes.",
        estimated_probability_yes=0.72,
        yes_ask=0.6,
        no_ask=0.4,
        live_mode=False,
    )

    fields = verdict.log_fields()

    assert fields["research_model_probability_yes"] == 0.72
    assert fields["research_min_published_at"] == "2026-06-27T10:00:00+00:00"
    assert fields["research_max_published_at"] == "2026-06-27T11:00:00+00:00"
    assert fields["research_min_retrieved_at"] == "2026-06-27T10:05:00+00:00"
    assert fields["research_max_retrieved_at"] == "2026-06-27T11:03:00+00:00"


async def _fake_search(query):
    if query.query_intent == "resolution_source":
        return [
            ResearchEvidence(
                source_class="resolution_source",
                source_name="OPEC",
                source_url="https://opec.org/momr",
                title="MOMR table",
                snippet="Iran crude production secondary sources table.",
                claim_type="resolution",
                supports_direction="yes",
                supports_confidence=0.9,
            )
        ]
    return [
        ResearchEvidence(
            source_class="reputable_secondary",
            source_name="Reuters",
            source_url="https://reuters.com/iran-production",
            title="Iran production rises",
            snippet="Analysts expect Iran crude production to exceed the threshold.",
            claim_type="baseline",
            supports_direction="yes",
            supports_confidence=0.8,
        )
    ]


async def _fake_adjudicator(*, evidence, queries, news, market):
    return {
        "direction": "yes",
        "confidence": 0.8,
        "estimated_probability_yes": 0.8,
        "reason": "Resolution-source and wire evidence support higher production.",
    }


@pytest.mark.asyncio
async def test_cache_only_research_gate_promotes_from_vetted_cached_dossier(tmp_path):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")
    await store.initialize()
    market = SimpleNamespace(
        ticker="KXIRANCRUDE-26JUL13-T3.8",
        title="Will Iran crude oil production be at least 3.8M bpd?",
        rules_primary="OPEC MOMR secondary sources decide the market.",
        rules_secondary="Later revisions ignored.",
        settlement_sources=(),
    )
    retrieved_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    evidence = [
        ResearchEvidence(
            source_class="resolution_source",
            source_name="OPEC",
            source_url="https://opec.org/momr",
            title="MOMR table",
            snippet="Iran crude production secondary sources table.",
            claim_type="resolution",
            supports_direction="yes",
            supports_confidence=0.9,
            retrieved_at=retrieved_at,
            contract_fingerprint=_contract_fingerprint(market),
        ),
        ResearchEvidence(
            source_class="reputable_secondary",
            source_name="Reuters",
            source_url="https://reuters.com/iran-production",
            title="Iran production rises",
            snippet="Analysts expect Iran crude production to exceed the threshold.",
            claim_type="corroboration",
            supports_direction="yes",
            supports_confidence=0.8,
            retrieved_at=retrieved_at,
            contract_fingerprint=_contract_fingerprint(market),
        ),
    ]
    await store.record_research_run(
        market.ticker,
        "rr-vetted-cache",
        trigger_headline="scheduled prewarm",
        trigger_source="research_prewarm",
        attempted=True,
        summary="Cached vetted research supports yes.",
        verdict_status=ResearchStatus.TRADE_CANDIDATE.value,
        force_side="yes",
        estimated_probability=0.8,
        confidence=0.8,
        evidence=evidence,
    )

    async def fail_search(_query):
        raise AssertionError("cache-only production path must not search")

    async def fail_direct_fetcher(*_args):
        raise AssertionError("cache-only production path must not fetch")

    async def fail_adjudicator(**_kwargs):
        raise AssertionError("cache-only production path must not call LLM")

    verdict = await run_research_gate(
        SimpleNamespace(headline="Iran crude output rises sharply", source="Reuters"),
        market,
        model_direction="neutral",
        model_confidence=0.5,
        model_reason="No keywords.",
        yes_ask=0.51,
        no_ask=0.51,
        live_mode=True,
        search_provider=fail_search,
        direct_fetcher=fail_direct_fetcher,
        adjudicator=fail_adjudicator,
        dossier_store=store,
        cache_only=True,
    )

    assert verdict.status == ResearchStatus.TRADE_CANDIDATE
    assert verdict.force_side == "yes"
    assert verdict.estimated_probability == pytest.approx(0.8)
    assert len(verdict.evidence) == 2
    snapshot = await store.get_dossier_snapshot(market.ticker)
    assert snapshot is not None
    assert snapshot.last_research_run_id == "rr-vetted-cache"
    assert verdict.research_run_id is None


@pytest.mark.asyncio
async def test_cache_only_research_gate_promotes_from_decision_grade_cached_dossier(tmp_path):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")
    await store.initialize()
    market = SimpleNamespace(
        ticker="KXIRANCRUDE-26JUL13-T3.8",
        title="Will Iran crude oil production be at least 3.8M bpd?",
        rules_primary="OPEC MOMR secondary sources decide the market.",
        rules_secondary="Later revisions ignored.",
        settlement_sources=(),
    )
    retrieved_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    evidence = [
        ResearchEvidence(
            source_class="resolution_source",
            source_name="OPEC",
            source_url="https://opec.org/momr",
            title="MOMR table",
            snippet="Iran crude production secondary sources table.",
            claim_type="resolution",
            supports_direction="yes",
            supports_confidence=0.9,
            retrieved_at=retrieved_at,
            contract_fingerprint=_contract_fingerprint(market),
        ),
        ResearchEvidence(
            source_class="reputable_secondary",
            source_name="Reuters",
            source_url="https://reuters.com/iran-production",
            title="Iran production rises",
            snippet="Analysts expect Iran crude production to exceed the threshold.",
            claim_type="corroboration",
            supports_direction="yes",
            supports_confidence=0.8,
            retrieved_at=retrieved_at,
            contract_fingerprint=_contract_fingerprint(market),
        ),
        ResearchEvidence(
            source_class="reputable_secondary",
            source_name="Argus",
            source_url="https://argus.example.com/iran-production-risks",
            title="Iran production risk",
            snippet="Analysts warn outages could keep production below the threshold.",
            claim_type="contradiction",
            supports_direction="no",
            supports_confidence=0.55,
            retrieved_at=retrieved_at,
            contract_fingerprint=_contract_fingerprint(market),
        ),
    ]
    await store.record_research_run(
        market.ticker,
        "rr-decision-grade-cache",
        trigger_headline="scheduled prewarm",
        trigger_source="research_prewarm",
        attempted=True,
        summary="Decision-grade cached research supports yes.",
        verdict_status=ResearchStatus.DECISION_GRADE_CANDIDATE.value,
        force_side="yes",
        estimated_probability=0.8,
        confidence=0.8,
        contract_fingerprint=_contract_fingerprint(market),
        market_price=0.51,
        estimated_edge=0.28,
        decision_grade_status=ResearchStatus.DECISION_GRADE_CANDIDATE.value,
        evidence=evidence,
        queries=[
            ResearchQuery(
                query="official Iran crude production",
                query_intent="official_resolution",
                source_class="resolution_source",
            ),
            ResearchQuery(
                query="Iran crude production countercase",
                query_intent="disconfirming",
                source_class="reputable_secondary",
            ),
        ],
    )

    async def fail_search(_query):
        raise AssertionError("cache-only production path must not search")

    async def fail_direct_fetcher(*_args):
        raise AssertionError("cache-only production path must not fetch")

    async def fail_adjudicator(**_kwargs):
        raise AssertionError("cache-only production path must not call LLM")

    verdict = await run_research_gate(
        SimpleNamespace(headline="Iran crude output rises sharply", source="Reuters"),
        market,
        model_direction="neutral",
        model_confidence=0.5,
        model_reason="No keywords.",
        yes_ask=0.51,
        no_ask=0.51,
        live_mode=False,
        search_provider=fail_search,
        direct_fetcher=fail_direct_fetcher,
        adjudicator=fail_adjudicator,
        dossier_store=store,
        cache_only=True,
    )

    assert verdict.status == ResearchStatus.DECISION_GRADE_CANDIDATE
    assert verdict.force_side == "yes"
    assert verdict.estimated_probability == pytest.approx(0.8)
    assert verdict.estimated_edge == pytest.approx(0.28)
    assert len(verdict.evidence) == 3
    assert verdict.research_run_id is None


@pytest.mark.asyncio
async def test_cache_only_research_gate_rejects_irrelevant_speech_candidate(tmp_path):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")
    await store.initialize()
    market = SimpleNamespace(
        ticker="KXTRUMPMENTION-26JUL24-MAGA",
        title="What will Donald Trump say during the July 24 dinner?",
        rules_primary=(
            "If Donald Trump says MAGA as part of the dinner, then the market "
            "resolves Yes."
        ),
        rules_secondary="Video of the event is the primary resolution source.",
        settlement_sources=(),
    )
    fingerprint = _contract_fingerprint(market)
    retrieved_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    evidence = [
        ResearchEvidence(
            source_class="rules_source",
            source_name="Kalshi",
            source_url="https://kalshi.com/markets/KXTRUMPMENTION-26JUL24-MAGA",
            title="Contract terms",
            snippet="The market resolves Yes if Donald Trump says MAGA at the dinner.",
            claim_type="rules",
            supports_direction="neutral",
            retrieved_at=retrieved_at,
            contract_fingerprint=fingerprint,
        ),
        ResearchEvidence(
            source_class="reputable_secondary",
            source_name="Britannica",
            source_url="https://britannica.com/topic/MAGA-movement",
            title="MAGA movement meaning and history under Donald Trump",
            snippet="The article explains the movement's political history.",
            claim_type="supporting",
            supports_direction="yes",
            supports_confidence=0.9,
            retrieved_at=retrieved_at,
            contract_fingerprint=fingerprint,
        ),
        ResearchEvidence(
            source_class="reputable_secondary",
            source_name="BBC",
            source_url="https://bbc.com/white-house-event",
            title="Donald Trump to attend White House dinner",
            snippet="The article notes MAGA movement history and event attendance.",
            claim_type="supporting",
            supports_direction="yes",
            supports_confidence=0.8,
            retrieved_at=retrieved_at,
            contract_fingerprint=fingerprint,
        ),
        ResearchEvidence(
            source_class="reputable_secondary",
            source_name="New York Times",
            source_url="https://nytimes.com/greenland",
            title="Trump discusses Greenland policy",
            snippet="The article discusses an unrelated diplomatic dispute.",
            claim_type="disconfirming",
            supports_direction="no",
            supports_confidence=0.1,
            retrieved_at=retrieved_at,
            contract_fingerprint=fingerprint,
        ),
    ]
    await store.record_research_run(
        market.ticker,
        "rr-irrelevant-speech-cache",
        trigger_headline="scheduled prewarm",
        trigger_source="research_prewarm",
        attempted=True,
        summary="Cached evidence was previously labeled decision grade.",
        verdict_status=ResearchStatus.DECISION_GRADE_CANDIDATE.value,
        force_side="yes",
        estimated_probability=0.81,
        confidence=0.8,
        contract_fingerprint=fingerprint,
        market_price=0.58,
        estimated_edge=0.22,
        decision_grade_status=ResearchStatus.DECISION_GRADE_CANDIDATE.value,
        evidence=evidence,
        queries=[
            ResearchQuery(
                query=market.rules_primary,
                query_intent="official_resolution",
                source_class="rules_source",
            ),
            ResearchQuery(
                query="Donald Trump MAGA dinner counter evidence",
                query_intent="disconfirming",
                source_class="reputable_secondary",
            ),
        ],
    )
    # Simulate a decision-grade snapshot written before evidence-specific
    # persistence validation existed.
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            """
            UPDATE research_dossiers
            SET last_verdict_status = ?,
                last_skip_reason = NULL,
                last_force_side = 'yes',
                last_estimated_probability = 0.81,
                last_confidence = 0.8,
                last_market_price = 0.58,
                last_estimated_edge = 0.22,
                last_decision_grade_status = ?
            WHERE market_ticker = ?
            """,
            (
                ResearchStatus.DECISION_GRADE_CANDIDATE.value,
                ResearchStatus.DECISION_GRADE_CANDIDATE.value,
                market.ticker,
            ),
        )

    async def fail_external(*_args, **_kwargs):
        raise AssertionError("cache-only path must not call external research")

    verdict = await run_research_gate(
        SimpleNamespace(headline="Dinner preview", source="research_prewarm"),
        market,
        model_direction="neutral",
        model_confidence=0.5,
        model_reason="No keywords.",
        yes_ask=0.58,
        no_ask=0.43,
        live_mode=False,
        search_provider=fail_external,
        direct_fetcher=fail_external,
        adjudicator=fail_external,
        dossier_store=store,
        cache_only=True,
    )

    assert verdict.status == ResearchStatus.NEEDS_COUNTER_EVIDENCE
    assert verdict.skip_reason == "missing_directional_support"
    assert verdict.force_side is None


@pytest.mark.asyncio
async def test_cache_only_rejects_irrelevant_non_speech_candidate():
    market = SimpleNamespace(
        ticker="KXUSTRDAGREEMENT-26JUL01",
        title="Will the US sign a trade agreement before July 1?",
        rules_primary=(
            "The market resolves Yes if the United States signs a trade agreement "
            "before July 1."
        ),
        rules_secondary="Official government announcements control resolution.",
        settlement_sources=(),
    )
    fingerprint = _contract_fingerprint(market)
    retrieved_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    evidence = [
        ResearchEvidence(
            source_class="resolution_source",
            source_name="Sports Authority",
            source_url="https://sports.example.com/final",
            title="Championship result",
            snippet="The home team won the final before the July deadline.",
            claim_type="official_resolution",
            supports_direction="yes",
            supports_confidence=0.9,
            retrieved_at=retrieved_at,
            contract_fingerprint=fingerprint,
        ),
        ResearchEvidence(
            source_class="reputable_secondary",
            source_name="Sports Wire",
            source_url="https://sportswire.example.com/final",
            title="Team confirms championship result",
            snippet="Independent sports reporting confirms the win.",
            claim_type="supporting",
            supports_direction="yes",
            supports_confidence=0.9,
            retrieved_at=retrieved_at,
            contract_fingerprint=fingerprint,
        ),
        ResearchEvidence(
            source_class="reputable_secondary",
            source_name="Opponent Blog",
            source_url="https://sports.example.net/objection",
            title="Opponent denied objection",
            snippet="The objection concerns an unrelated sports dispute.",
            claim_type="disconfirming",
            supports_direction="no",
            supports_confidence=0.5,
            retrieved_at=retrieved_at,
            contract_fingerprint=fingerprint,
        ),
    ]

    class LegacyCacheStore:
        async def get_recent_evidence(self, _ticker):
            return evidence

        async def get_dossier_snapshot(self, _ticker):
            return SimpleNamespace(
                last_verdict_status=ResearchStatus.DECISION_GRADE_CANDIDATE.value,
                last_force_side="yes",
                last_estimated_probability=0.8,
                last_confidence=0.8,
            )

    async def fail_external(*_args, **_kwargs):
        raise AssertionError("cache-only path must not call external research")

    verdict = await run_research_gate(
        SimpleNamespace(headline="Trade agreement update", source="research_prewarm"),
        market,
        model_direction="neutral",
        model_confidence=0.5,
        model_reason="No keywords.",
        yes_ask=0.51,
        no_ask=0.50,
        live_mode=False,
        search_provider=fail_external,
        direct_fetcher=fail_external,
        adjudicator=fail_external,
        dossier_store=LegacyCacheStore(),
        cache_only=True,
    )

    assert verdict.status == ResearchStatus.NEEDS_COUNTER_EVIDENCE
    assert verdict.skip_reason == "missing_directional_support"
    assert verdict.force_side is None


@pytest.mark.asyncio
async def test_research_gate_refreshes_cached_missing_counter_evidence_dossier(tmp_path):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")
    await store.initialize()
    market = SimpleNamespace(
        ticker="KXHIGHNY-26JUL02-B99.5",
        title="Will the high temp in NYC be 99-100° on Jul 2, 2026?",
        rules_primary=(
            "If the highest temperature recorded in Central Park, New York "
            "for July 02, 2026 is 99-100 degrees, this market resolves Yes."
        ),
        rules_secondary="",
        settlement_sources=(
            SettlementSource(
                label="National Weather Service",
                url="https://forecast.weather.gov/product.php?site=OKX&product=CLI&issuedby=NYC",
                domain="forecast.weather.gov",
            ),
        ),
    )
    retrieved_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    cached_evidence = [
        ResearchEvidence(
            source_class="official_primary",
            source_name="NWS Climatological Report",
            source_url="https://forecast.weather.gov/product.php?site=OKX&product=CLI&issuedby=NYC#high-2026-07-02",
            title="NWS Central Park daily maximum for July 2, 2026: 93F",
            snippet="NWS Central Park climate report lists TODAY MAXIMUM 93F.",
            claim_type="supporting",
            supports_direction="no",
            supports_confidence=0.95,
            metric_name="nws_daily_high_temp_f",
            metric_value=93.0,
            metric_unit="fahrenheit",
            retrieved_at=retrieved_at,
            contract_fingerprint=_contract_fingerprint(market),
        ),
        ResearchEvidence(
            source_class="official_primary",
            source_name="NWS Climatological Report",
            source_url="https://forecast.weather.gov/product.php?site=OKX&product=CLI&issuedby=NYC#high-2026-07-02-old-counter",
            title="NWS Central Park daily maximum for July 2, 2026: 93F",
            snippet="Old cached disconfirming row was directional.",
            claim_type="disconfirming",
            supports_direction="no",
            supports_confidence=0.95,
            metric_name="nws_daily_high_temp_f",
            metric_value=93.0,
            metric_unit="fahrenheit",
            retrieved_at=retrieved_at,
            contract_fingerprint=_contract_fingerprint(market),
        ),
        ResearchEvidence(
            source_class="resolution_source",
            source_name="National Weather Service",
            source_url="https://forecast.weather.gov/product.php?site=OKX&product=CLI&issuedby=NYC",
            title="National Weather Service",
            snippet="NWS publishes the Central Park daily climate report.",
            claim_type="settlement_source",
            supports_direction="neutral",
            supports_confidence=0.0,
            retrieved_at=retrieved_at,
            contract_fingerprint=_contract_fingerprint(market),
        ),
    ]
    await store.record_research_run(
        market.ticker,
        "rr-cached-missing-counter",
        trigger_headline="scheduled prewarm",
        trigger_source="research_prewarm",
        attempted=True,
        summary="Cached weather research still lacks a neutral countercheck.",
        verdict_status=ResearchStatus.NEEDS_COUNTER_EVIDENCE.value,
        skip_reason="missing_counter_evidence",
        decision_grade_status=ResearchStatus.NEEDS_COUNTER_EVIDENCE.value,
        force_side="no",
        estimated_probability=0.05,
        confidence=0.95,
        contract_fingerprint=_contract_fingerprint(market),
        market_price=0.30,
        estimated_edge=0.64,
        evidence=cached_evidence,
    )
    search_calls: list[str] = []

    async def search_provider(query):
        search_calls.append(query.query_intent)
        if query.query_intent == "disconfirming":
            return [
                ResearchEvidence(
                    source_class="official_primary",
                    source_name="NWS Climatological Report",
                    source_url="https://forecast.weather.gov/product.php?site=OKX&product=CLI&issuedby=NYC#high-2026-07-02",
                    title="NWS Central Park daily maximum countercheck for July 2, 2026: 93F",
                    snippet=(
                        "Disconfirming search checked the NWS Central Park "
                        "daily maximum of 93F against the 99-100F market "
                        "range; no contrary official high-temperature fact "
                        "was found."
                    ),
                    claim_type="disconfirming",
                    supports_direction="neutral",
                    supports_confidence=0.95,
                    metric_name="nws_daily_high_temp_f",
                    metric_value=93.0,
                    metric_unit="fahrenheit",
                    retrieved_at=retrieved_at,
                )
            ]
        return []

    async def direct_fetcher(*_args):
        return ResearchEvidence(
            source_class="resolution_source",
            source_name="National Weather Service",
            source_url="https://forecast.weather.gov/product.php?site=OKX&product=CLI&issuedby=NYC",
            title="National Weather Service",
            snippet="NWS publishes the Central Park daily climate report.",
            claim_type="settlement_source",
            supports_direction="neutral",
            supports_confidence=0.0,
            retrieved_at=retrieved_at,
        )

    verdict = await run_research_gate(
        SimpleNamespace(headline="", source="research_prewarm"),
        market,
        model_direction="neutral",
        model_confidence=0.0,
        model_reason="scheduled research prewarm",
        yes_ask=0.70,
        no_ask=0.30,
        live_mode=False,
        search_provider=search_provider,
        direct_fetcher=direct_fetcher,
        adjudicator=None,
        dossier_store=store,
        max_queries=8,
        require_decision_grade=True,
    )

    assert "disconfirming" in search_calls
    assert verdict.status == ResearchStatus.DECISION_GRADE_CANDIDATE
    assert verdict.skip_reason is None
    assert verdict.force_side == "no"


@pytest.mark.asyncio
async def test_cached_rss_publisher_does_not_suppress_official_source_refresh(tmp_path):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")
    await store.initialize()
    market = SimpleNamespace(
        ticker="KXCRITICALITY-26AUG-OKLO",
        title="Which nuclear power companies will achieve criticality before Aug 2026?",
        rules_primary=(
            "If Oklo achieves criticality before Aug 1, 2026, then the market "
            "resolves to Yes."
        ),
        rules_secondary="",
        settlement_sources=(),
        contract_terms_url="",
        status="open",
        close_time="2026-08-01T23:59:59Z",
    )
    contract_fingerprint = _contract_fingerprint(market)
    retrieved_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    await store.record_research_run(
        market.ticker,
        "rr-cached-nrc-rss",
        trigger_headline="scheduled prewarm",
        trigger_source="research_prewarm",
        attempted=True,
        summary="Cached NRC result remains neutral.",
        verdict_status=ResearchStatus.NEEDS_RESEARCH.value,
        skip_reason="insufficient_corroboration",
        decision_grade_status=ResearchStatus.NEEDS_RESEARCH.value,
        contract_fingerprint=contract_fingerprint,
        evidence=[
            ResearchEvidence(
                source_class="official_primary",
                source_name="Nuclear Regulatory Commission",
                source_url="https://www.nrc.gov",
                aggregator_url="https://news.google.com/rss/articles/cached-nrc",
                title="Cached NRC licensing update",
                snippet="The cached result does not resolve the market condition.",
                claim_type="official_resolution",
                supports_direction="neutral",
                retrieved_at=retrieved_at,
                contract_fingerprint=contract_fingerprint,
            )
        ],
    )
    search_calls: list[ResearchQuery] = []

    async def search_provider(query):
        search_calls.append(query)
        return []

    async def direct_fetcher(*_args):
        return None

    async def adjudicator(**_kwargs):
        return {
            "direction": "neutral",
            "confidence": 0.4,
            "estimated_probability_yes": 0.5,
            "reason": "Fresh official evidence is still required.",
        }

    await run_research_gate(
        SimpleNamespace(headline="", source="research_prewarm", url=""),
        market,
        model_direction="neutral",
        model_confidence=0.0,
        model_reason="scheduled research prewarm",
        yes_ask=0.45,
        no_ask=0.56,
        live_mode=False,
        search_provider=search_provider,
        direct_fetcher=direct_fetcher,
        adjudicator=adjudicator,
        dossier_store=store,
        max_queries=8,
        research_timeout_seconds=5.0,
        require_decision_grade=True,
    )

    assert any(
        query.source_class in {"official_primary", "resolution_source"}
        and "site:nrc.gov" in query.query
        for query in search_calls
    )


@pytest.mark.asyncio
async def test_researched_no_edge_invalidates_stale_cached_candidate(tmp_path):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")
    await store.initialize()
    market = SimpleNamespace(
        ticker="KXIRANCRUDE-26JUL13-T3.8",
        title="Will Iran crude oil production be at least 3.8M bpd?",
        rules_primary="OPEC MOMR secondary sources decide the market.",
        rules_secondary="Later revisions ignored.",
        settlement_sources=(),
    )
    retrieved_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    evidence = [
        ResearchEvidence(
            source_class="resolution_source",
            source_name="OPEC",
            source_url="https://opec.org/momr",
            title="MOMR table",
            snippet="Iran crude production secondary sources table.",
            claim_type="resolution",
            supports_direction="yes",
            supports_confidence=0.9,
            retrieved_at=retrieved_at,
            contract_fingerprint=_contract_fingerprint(market),
        ),
        ResearchEvidence(
            source_class="reputable_secondary",
            source_name="Reuters",
            source_url="https://reuters.com/iran-production",
            title="Iran production rises",
            snippet="Analysts expect Iran crude production to exceed the threshold.",
            claim_type="corroboration",
            supports_direction="yes",
            supports_confidence=0.8,
            retrieved_at=retrieved_at,
            contract_fingerprint=_contract_fingerprint(market),
        ),
    ]
    await store.record_research_run(
        market.ticker,
        "rr-vetted-cache",
        trigger_headline="scheduled prewarm",
        trigger_source="research_prewarm",
        attempted=True,
        summary="Cached vetted research supports yes.",
        verdict_status=ResearchStatus.TRADE_CANDIDATE.value,
        force_side="yes",
        estimated_probability=0.8,
        confidence=0.8,
        evidence=evidence,
    )

    async def fail_search(_query):
        raise AssertionError("sufficient cached evidence should avoid fresh search")

    async def no_edge_adjudicator(*, evidence, queries, news, market):
        return {
            "direction": "yes",
            "confidence": 0.8,
            "estimated_probability_yes": 0.53,
            "reason": "Evidence still supports YES but edge no longer clears costs.",
        }

    no_edge = await run_research_gate(
        SimpleNamespace(headline="Iran crude output rises sharply", source="Reuters"),
        market,
        model_direction="neutral",
        model_confidence=0.5,
        model_reason="No keywords.",
        yes_ask=0.51,
        no_ask=0.51,
        live_mode=False,
        search_provider=fail_search,
        adjudicator=no_edge_adjudicator,
        dossier_store=store,
    )

    assert no_edge.status == ResearchStatus.RESEARCHED_SKIP_NO_EDGE
    snapshot = await store.get_dossier_snapshot(market.ticker)
    assert snapshot is not None
    assert snapshot.last_research_run_id == no_edge.research_run_id
    assert snapshot.last_verdict_status == ResearchStatus.RESEARCHED_SKIP_NO_EDGE.value

    cache_only = await run_research_gate(
        SimpleNamespace(headline="Iran crude output rises sharply", source="Reuters"),
        market,
        model_direction="neutral",
        model_confidence=0.5,
        model_reason="No keywords.",
        yes_ask=0.51,
        no_ask=0.51,
        live_mode=True,
        search_provider=fail_search,
        adjudicator=no_edge_adjudicator,
        dossier_store=store,
        cache_only=True,
    )

    assert cache_only.status == ResearchStatus.CONTINUE_RESEARCHING
    assert cache_only.skip_reason == "cached_dossier_unvetted"


@pytest.mark.asyncio
async def test_research_prewarm_ambiguous_retry_preserves_fresh_candidate_snapshot(
    tmp_path,
):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")
    await store.initialize()
    market = SimpleNamespace(
        ticker="KXIRANCRUDE-26JUL13-T3.8",
        title="Will Iran crude oil production be at least 3.8M bpd?",
        rules_primary="OPEC MOMR secondary sources decide the market.",
        rules_secondary="Later revisions ignored.",
        settlement_sources=(),
    )
    contract_fingerprint = _contract_fingerprint(market)
    retrieved_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    evidence = [
        ResearchEvidence(
            source_class="resolution_source",
            source_name="OPEC",
            source_url="https://opec.org/momr",
            title="MOMR table",
            snippet="Iran crude production secondary sources table.",
            claim_type="resolution",
            supports_direction="yes",
            supports_confidence=0.9,
            retrieved_at=retrieved_at,
            contract_fingerprint=contract_fingerprint,
        ),
        ResearchEvidence(
            source_class="reputable_secondary",
            source_name="Reuters",
            source_url="https://reuters.com/iran-production",
            title="Iran production rises",
            snippet="Analysts expect Iran crude production to exceed the threshold.",
            claim_type="corroboration",
            supports_direction="yes",
            supports_confidence=0.8,
            retrieved_at=retrieved_at,
            contract_fingerprint=contract_fingerprint,
        ),
        ResearchEvidence(
            source_class="reputable_secondary",
            source_name="Energy Intelligence",
            source_url="https://energyintel.com/iran-production-countercase",
            title="Iran production uncertainty",
            snippet=(
                "Independent estimates warn revisions could keep Iran crude "
                "production below the threshold."
            ),
            claim_type="contradiction",
            supports_direction="no",
            supports_confidence=0.7,
            retrieved_at=retrieved_at,
            contract_fingerprint=contract_fingerprint,
        ),
    ]
    await store.record_research_run(
        market.ticker,
        "rr-vetted-prewarm",
        trigger_headline="scheduled prewarm",
        trigger_source="research_prewarm",
        attempted=True,
        summary="Cached vetted research supports yes.",
        verdict_status=ResearchStatus.TRADE_CANDIDATE.value,
        force_side="yes",
        estimated_probability=0.8,
        confidence=0.8,
        contract_fingerprint=contract_fingerprint,
        evidence=evidence,
    )

    async def fail_search(_query):
        raise AssertionError("fresh candidate cache should avoid fresh search")

    async def ambiguous_adjudicator(*, evidence, queries, news, market):
        return {
            "direction": "neutral",
            "confidence": 0.8,
            "estimated_probability_yes": 0.50,
            "reason": "Prewarm retry found mixed/noisy context.",
        }

    verdict = await run_research_gate(
        SimpleNamespace(headline="scheduled prewarm", source="research_prewarm"),
        market,
        model_direction="neutral",
        model_confidence=0.5,
        model_reason="Scheduled prewarm.",
        yes_ask=0.51,
        no_ask=0.51,
        live_mode=False,
        search_provider=fail_search,
        adjudicator=ambiguous_adjudicator,
        dossier_store=store,
    )

    assert verdict.status == ResearchStatus.RESEARCHED_SKIP_AMBIGUOUS
    snapshot = await store.get_dossier_snapshot(market.ticker)
    assert snapshot is not None
    assert snapshot.last_research_run_id == "rr-vetted-prewarm"
    assert snapshot.last_verdict_status == ResearchStatus.TRADE_CANDIDATE.value
    assert snapshot.last_force_side == "yes"


@pytest.mark.asyncio
async def test_research_prewarm_ambiguous_retry_preserves_decision_grade_snapshot(
    tmp_path,
):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")
    await store.initialize()
    market = SimpleNamespace(
        ticker="KXIRANCRUDE-26JUL13-T3.8",
        title="Will Iran crude oil production be at least 3.8M bpd?",
        rules_primary="OPEC MOMR secondary sources decide the market.",
        rules_secondary="Later revisions ignored.",
        settlement_sources=(),
    )
    contract_fingerprint = _contract_fingerprint(market)
    retrieved_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    evidence = [
        ResearchEvidence(
            source_class="resolution_source",
            source_name="OPEC",
            source_url="https://opec.org/momr",
            title="MOMR table",
            snippet="Iran crude production secondary sources table.",
            claim_type="resolution",
            supports_direction="yes",
            supports_confidence=0.9,
            retrieved_at=retrieved_at,
            contract_fingerprint=contract_fingerprint,
        ),
        ResearchEvidence(
            source_class="reputable_secondary",
            source_name="Reuters",
            source_url="https://reuters.com/iran-production",
            title="Iran production rises",
            snippet="Analysts expect Iran crude production to exceed the threshold.",
            claim_type="corroboration",
            supports_direction="yes",
            supports_confidence=0.8,
            retrieved_at=retrieved_at,
            contract_fingerprint=contract_fingerprint,
        ),
        ResearchEvidence(
            source_class="reputable_secondary",
            source_name="Energy Intelligence",
            source_url="https://energyintel.com/iran-production-countercase",
            title="Iran production uncertainty",
            snippet=(
                "Independent estimates warn revisions could keep Iran crude "
                "production below the threshold."
            ),
            claim_type="contradiction",
            supports_direction="no",
            supports_confidence=0.7,
            retrieved_at=retrieved_at,
            contract_fingerprint=contract_fingerprint,
        ),
    ]
    await store.record_research_run(
        market.ticker,
        "rr-decision-grade-prewarm",
        trigger_headline="scheduled prewarm",
        trigger_source="research_prewarm",
        attempted=True,
        summary="Cached decision-grade research supports yes.",
        verdict_status=ResearchStatus.DECISION_GRADE_CANDIDATE.value,
        force_side="yes",
        estimated_probability=0.8,
        confidence=0.8,
        market_price=0.51,
        estimated_edge=0.28,
        contract_fingerprint=contract_fingerprint,
        decision_grade_status=ResearchStatus.DECISION_GRADE_CANDIDATE.value,
        evidence=evidence,
        queries=[
            ResearchQuery(
                query="official Iran crude production",
                query_intent="official_resolution",
                source_class="resolution_source",
            ),
            ResearchQuery(
                query="Iran crude production countercase",
                query_intent="disconfirming",
                source_class="reputable_secondary",
            ),
        ],
    )

    async def fail_search(_query):
        raise AssertionError("fresh candidate cache should avoid fresh search")

    async def ambiguous_adjudicator(*, evidence, queries, news, market):
        return {
            "direction": "neutral",
            "confidence": 0.8,
            "estimated_probability_yes": 0.50,
            "reason": "Prewarm retry found mixed/noisy context.",
        }

    verdict = await run_research_gate(
        SimpleNamespace(headline="scheduled prewarm", source="research_prewarm"),
        market,
        model_direction="neutral",
        model_confidence=0.5,
        model_reason="Scheduled prewarm.",
        yes_ask=0.51,
        no_ask=0.51,
        live_mode=False,
        search_provider=fail_search,
        adjudicator=ambiguous_adjudicator,
        dossier_store=store,
    )

    assert verdict.status == ResearchStatus.RESEARCHED_SKIP_AMBIGUOUS
    snapshot = await store.get_dossier_snapshot(market.ticker)
    assert snapshot is not None
    assert snapshot.last_research_run_id == "rr-decision-grade-prewarm"
    assert snapshot.last_verdict_status == ResearchStatus.DECISION_GRADE_CANDIDATE.value
    assert snapshot.last_force_side == "yes"


@pytest.mark.asyncio
async def test_cached_evidence_candidate_records_matching_full_proof_run(tmp_path):
    db_path = tmp_path / "research_dossier.db"
    store = ResearchDossierStore(db_path)
    await store.initialize()
    market = SimpleNamespace(
        ticker="KXIRANCRUDE-26JUL13-T3.8",
        title="Will Iran crude oil production be at least 3.8M bpd?",
        rules_primary="OPEC MOMR secondary sources decide the market.",
        rules_secondary="Later revisions ignored.",
        settlement_sources=(),
    )
    retrieved_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    evidence = [
        ResearchEvidence(
            source_class="resolution_source",
            source_name="OPEC",
            source_url="https://opec.org/momr",
            title="MOMR table",
            snippet="Iran crude production secondary sources table.",
            claim_type="resolution",
            supports_direction="yes",
            supports_confidence=0.9,
            retrieved_at=retrieved_at,
            contract_fingerprint=_contract_fingerprint(market),
        ),
        ResearchEvidence(
            source_class="reputable_secondary",
            source_name="Reuters",
            source_url="https://reuters.com/iran-production",
            title="Iran production rises",
            snippet="Analysts expect Iran crude production to exceed the threshold.",
            claim_type="corroboration",
            supports_direction="yes",
            supports_confidence=0.8,
            retrieved_at=retrieved_at,
            contract_fingerprint=_contract_fingerprint(market),
        ),
    ]
    await store.record_research_run(
        market.ticker,
        "rr-vetted-cache",
        trigger_headline="scheduled prewarm",
        trigger_source="research_prewarm",
        attempted=True,
        summary="Cached vetted research supports yes.",
        verdict_status=ResearchStatus.TRADE_CANDIDATE.value,
        force_side="yes",
        estimated_probability=0.8,
        confidence=0.8,
        evidence=evidence,
    )

    async def fail_search(_query):
        raise AssertionError("sufficient cached evidence should avoid fresh search")

    verdict = await run_research_gate(
        SimpleNamespace(headline="Iran crude output rises sharply", source="Reuters"),
        market,
        model_direction="neutral",
        model_confidence=0.5,
        model_reason="No keywords.",
        yes_ask=0.51,
        no_ask=0.51,
        live_mode=False,
        search_provider=fail_search,
        adjudicator=_fake_adjudicator,
        dossier_store=store,
    )

    assert verdict.status == ResearchStatus.TRADE_CANDIDATE
    snapshot = await store.get_dossier_snapshot(market.ticker)
    assert snapshot is not None
    assert snapshot.last_research_run_id == verdict.research_run_id
    with sqlite3.connect(db_path) as conn:
        proof_rows = conn.execute(
            """
            SELECT source_class
            FROM research_evidence
            WHERE research_run_id = ?
            ORDER BY source_class
            """,
            (snapshot.last_research_run_id,),
        ).fetchall()

    assert proof_rows == [("reputable_secondary",), ("resolution_source",)]


@pytest.mark.asyncio
async def test_mixed_cached_and_fresh_candidate_records_full_proof_run(tmp_path):
    db_path = tmp_path / "research_dossier.db"
    store = ResearchDossierStore(db_path)
    await store.initialize()
    market = SimpleNamespace(
        ticker="KXIRANCRUDE-26JUL13-T3.8",
        title="Will Iran crude oil production be at least 3.8M bpd?",
        rules_primary="OPEC MOMR secondary sources decide the market.",
        rules_secondary="Later revisions ignored.",
        settlement_sources=(),
    )
    retrieved_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    await store.record_research_run(
        market.ticker,
        "rr-resolution-only",
        trigger_headline="scheduled prewarm",
        trigger_source="research_prewarm",
        attempted=True,
        summary="Cached resolution source only.",
        verdict_status=ResearchStatus.CONTINUE_RESEARCHING.value,
        skip_reason="insufficient_corroboration",
        contract_fingerprint=_contract_fingerprint(market),
        evidence=[
            ResearchEvidence(
                source_class="resolution_source",
                source_name="OPEC",
                source_url="https://opec.org/momr",
                title="MOMR table",
                snippet="Iran crude production secondary sources table.",
                claim_type="resolution",
                supports_direction="yes",
                supports_confidence=0.9,
                retrieved_at=retrieved_at,
                contract_fingerprint=_contract_fingerprint(market),
            )
        ],
    )

    async def one_fresh_secondary(_query):
        return [
            ResearchEvidence(
                source_class="reputable_secondary",
                source_name="Reuters",
                source_url="https://reuters.com/iran-production",
                title="Iran production rises",
                snippet="Analysts expect Iran crude production to exceed the threshold.",
                claim_type="corroboration",
                supports_direction="yes",
                supports_confidence=0.8,
                retrieved_at=retrieved_at,
                contract_fingerprint=_contract_fingerprint(market),
            )
        ]

    verdict = await run_research_gate(
        SimpleNamespace(headline="Iran crude output rises sharply", source="Reuters"),
        market,
        model_direction="neutral",
        model_confidence=0.5,
        model_reason="No keywords.",
        yes_ask=0.51,
        no_ask=0.51,
        live_mode=False,
        search_provider=one_fresh_secondary,
        adjudicator=_fake_adjudicator,
        dossier_store=store,
    )

    assert verdict.status == ResearchStatus.TRADE_CANDIDATE
    snapshot = await store.get_dossier_snapshot(market.ticker)
    assert snapshot is not None
    assert snapshot.last_research_run_id == verdict.research_run_id
    with sqlite3.connect(db_path) as conn:
        proof_rows = conn.execute(
            """
            SELECT source_class
            FROM research_evidence
            WHERE research_run_id = ?
            ORDER BY source_class
            """,
            (snapshot.last_research_run_id,),
        ).fetchall()

    assert proof_rows == [("reputable_secondary",), ("resolution_source",)]


@pytest.mark.asyncio
async def test_cache_only_research_gate_fails_closed_without_sufficient_cache(tmp_path):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")
    await store.initialize()

    async def fail_search(_query):
        raise AssertionError("cache-only production path must not search")

    async def fail_direct_fetcher(*_args):
        raise AssertionError("cache-only production path must not fetch")

    async def fail_adjudicator(**_kwargs):
        raise AssertionError("cache-only production path must not call LLM")

    verdict = await run_research_gate(
        SimpleNamespace(headline="Iran crude output rises sharply", source="Reuters"),
        SimpleNamespace(
            ticker="KXIRANCRUDE-26JUL13-T3.8",
            title="Will Iran crude oil production be at least 3.8M bpd?",
            rules_primary="OPEC MOMR secondary sources decide the market.",
            rules_secondary="Later revisions ignored.",
            settlement_sources=(),
        ),
        model_direction="neutral",
        model_confidence=0.5,
        model_reason="No keywords.",
        yes_ask=0.51,
        no_ask=0.51,
        live_mode=True,
        search_provider=fail_search,
        direct_fetcher=fail_direct_fetcher,
        adjudicator=fail_adjudicator,
        dossier_store=store,
        cache_only=True,
    )

    assert verdict.status == ResearchStatus.CONTINUE_RESEARCHING
    assert verdict.attempted is False
    assert verdict.skip_reason == "cached_dossier_insufficient"
    snapshot = await store.get_dossier_snapshot("KXIRANCRUDE-26JUL13-T3.8")
    assert snapshot is None


@pytest.mark.asyncio
async def test_run_research_gate_persists_contract_fingerprint_without_evidence(tmp_path):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")
    await store.initialize()
    market = SimpleNamespace(
        ticker="KXIRANCRUDE-26JUL13-T3.8",
        title="Will Iran crude oil production be at least 3.8M bpd?",
        rules_primary="OPEC MOMR secondary sources decide the market.",
        rules_secondary="Later revisions ignored.",
        settlement_sources=(),
    )
    expected_fingerprint = _contract_fingerprint(market)

    async def no_hits(_query):
        return []

    verdict = await run_research_gate(
        SimpleNamespace(headline="Iran crude output rises sharply", source="Reuters"),
        market,
        model_direction="neutral",
        model_confidence=0.5,
        model_reason="No keywords.",
        yes_ask=0.51,
        no_ask=0.51,
        live_mode=False,
        search_provider=no_hits,
        dossier_store=store,
    )

    snapshot = await store.get_dossier_snapshot(market.ticker)
    fields = verdict.log_fields()

    assert verdict.status == ResearchStatus.CONTINUE_RESEARCHING
    assert verdict.skip_reason == "no_research_hits"
    assert snapshot is not None
    assert snapshot.last_contract_fingerprint == expected_fingerprint
    assert fields["research_contract_fingerprint"] == expected_fingerprint


@pytest.mark.asyncio
async def test_run_research_gate_can_promote_neutral_to_trade_candidate():
    news = SimpleNamespace(
        headline="Iran crude output rises sharply",
        source="Reuters",
        url="https://reuters.com/iran-production",
    )
    market = SimpleNamespace(
        ticker="KXIRANCRUDE-26JUL13-T3.8",
        title="Will Iran crude oil production be at least 3.8M bpd?",
        rules_primary="OPEC MOMR secondary sources decide the market.",
        rules_secondary="Later revisions ignored.",
        settlement_sources=(),
    )

    verdict = await run_research_gate(
        news,
        market,
        model_direction="neutral",
        model_confidence=0.85,
        model_reason="No relevant keywords found.",
        yes_ask=0.51,
        no_ask=0.51,
        live_mode=False,
        search_provider=_fake_search,
        adjudicator=_fake_adjudicator,
    )

    assert verdict.status == ResearchStatus.TRADE_CANDIDATE
    assert verdict.force_side == "yes"


@pytest.mark.asyncio
async def test_run_research_gate_exposes_persisted_run_id(tmp_path):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")
    await store.initialize()
    market = SimpleNamespace(
        ticker="KXIRANCRUDE-26JUL13-T3.8",
        title="Will Iran crude oil production be at least 3.8M bpd?",
        rules_primary="OPEC MOMR secondary sources decide the market.",
        rules_secondary="Later revisions ignored.",
        settlement_sources=(),
        status="active",
        close_time="2099-07-13T23:59:59Z",
    )

    verdict = await run_research_gate(
        SimpleNamespace(
            headline="Iran crude output rises sharply",
            source="Reuters",
            url="https://reuters.com/iran-production",
        ),
        market,
        model_direction="neutral",
        model_confidence=0.85,
        model_reason="No relevant keywords found.",
        yes_ask=0.51,
        no_ask=0.51,
        live_mode=False,
        search_provider=_fake_search,
        adjudicator=_fake_adjudicator,
        dossier_store=store,
    )

    assert verdict.status == ResearchStatus.TRADE_CANDIDATE
    assert verdict.research_run_id
    assert verdict.research_persisted is True
    fields = verdict.log_fields()
    assert fields["research_run_id"] == verdict.research_run_id
    assert fields["research_persisted"] is True
    assert fields["research_contract_fingerprint"] == _contract_fingerprint(market)
    snapshot = await store.get_dossier_snapshot(market.ticker)
    assert snapshot is not None
    assert snapshot.market_status == "active"
    assert snapshot.market_close_time == "2099-07-13T23:59:59Z"


@pytest.mark.asyncio
async def test_run_research_gate_uses_unique_run_id_per_attempt(tmp_path):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")
    await store.initialize()
    news = SimpleNamespace(
        headline="Iran crude output rises sharply",
        source="Reuters",
        url="https://reuters.com/iran-production",
    )
    market = SimpleNamespace(
        ticker="KXIRANCRUDE-26JUL13-T3.8",
        title="Will Iran crude oil production be at least 3.8M bpd?",
        rules_primary="OPEC MOMR secondary sources decide the market.",
        rules_secondary="Later revisions ignored.",
        settlement_sources=(),
    )

    first = await run_research_gate(
        news,
        market,
        model_direction="neutral",
        model_confidence=0.85,
        model_reason="No relevant keywords found.",
        yes_ask=0.51,
        no_ask=0.51,
        live_mode=False,
        search_provider=_fake_search,
        adjudicator=_fake_adjudicator,
        dossier_store=store,
    )
    second = await run_research_gate(
        news,
        market,
        model_direction="neutral",
        model_confidence=0.85,
        model_reason="No relevant keywords found.",
        yes_ask=0.51,
        no_ask=0.51,
        live_mode=False,
        search_provider=_fake_search,
        adjudicator=_fake_adjudicator,
        dossier_store=store,
    )

    assert first.research_run_id
    assert second.research_run_id
    assert first.research_run_id != second.research_run_id


@pytest.mark.asyncio
async def test_run_research_gate_surfaces_persistence_failure():
    class FailingStore:
        async def get_recent_evidence(self, _ticker):
            return []

        async def record_research_run(self, *_args, **_kwargs):
            raise RuntimeError("sqlite locked")

    verdict = await run_research_gate(
        SimpleNamespace(
            headline="Iran crude output rises sharply",
            source="Reuters",
            url="https://reuters.com/iran-production",
        ),
        SimpleNamespace(
            ticker="KXIRANCRUDE-26JUL13-T3.8",
            title="Will Iran crude oil production be at least 3.8M bpd?",
            rules_primary="OPEC MOMR secondary sources decide the market.",
            rules_secondary="Later revisions ignored.",
            settlement_sources=(),
        ),
        model_direction="neutral",
        model_confidence=0.85,
        model_reason="No relevant keywords found.",
        yes_ask=0.51,
        no_ask=0.51,
        live_mode=False,
        search_provider=_fake_search,
        adjudicator=_fake_adjudicator,
        dossier_store=FailingStore(),
    )

    assert verdict.status == ResearchStatus.TRADE_CANDIDATE
    assert verdict.research_run_id
    assert verdict.research_persisted is False
    assert verdict.research_persistence_error == "sqlite locked"
    assert verdict.log_fields()["research_persisted"] is False


@pytest.mark.asyncio
async def test_run_research_gate_surfaces_direct_fetch_failure():
    async def direct_fetcher(_url, _source_class, _claim_type):
        raise RuntimeError("official source unavailable")

    async def search_provider(query):
        assert query.source_class != "resolution_source"
        return [
            ResearchEvidence(
                source_class="reputable_secondary",
                source_name="Reuters",
                source_url="https://reuters.com/ceasefire",
                title="Ceasefire agreement signed",
                snippet="Officials confirm the agreement was signed before the deadline.",
                claim_type="corroboration",
            )
        ]

    verdict = await run_research_gate(
        SimpleNamespace(
            headline="Officials confirm ceasefire agreement was signed",
            source="Reuters",
            url="https://reuters.com/ceasefire",
        ),
        SimpleNamespace(
            ticker="KXCEASEFIRE-26JUL01",
            title="Will a ceasefire agreement be signed by July 1?",
            rules_primary="The market resolves Yes if an agreement is signed by the deadline.",
            rules_secondary="The determination will be based on official public announcements.",
            settlement_sources=(),
            contract_terms_url="https://kalshi.com/markets/KXCEASEFIRE-26JUL01",
        ),
        model_direction="neutral",
        model_confidence=0.5,
        model_reason="No keywords.",
        yes_ask=0.6,
        no_ask=0.4,
        live_mode=False,
        search_provider=search_provider,
        direct_fetcher=direct_fetcher,
        adjudicator=_fake_adjudicator,
    )

    assert verdict.status == ResearchStatus.CONTINUE_RESEARCHING
    assert verdict.skip_reason == "missing_resolution_source"
    assert len(verdict.research_direct_fetch_failures) == 1
    assert verdict.log_fields()["research_direct_fetch_failure_count"] == 1


@pytest.mark.asyncio
async def test_persisted_source_policy_demotion_updates_returned_verdict(tmp_path):
    retrieved_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    store = ResearchDossierStore(tmp_path / "research.db")

    async def search_provider(query):
        if query.query_intent in {"disconfirming", "contradiction_check"}:
            return [
                ResearchEvidence(
                    source_class="reputable_secondary",
                    source_name="Single Wire",
                    source_url="https://same.example/counter",
                    title="Countercase",
                    snippet="A specific but weak countercase was found.",
                    claim_type="disconfirming",
                    supports_direction="no",
                    supports_confidence=0.2,
                    retrieved_at=retrieved_at,
                )
            ]
        return [
            ResearchEvidence(
                source_class="resolution_source",
                source_name="Single Wire",
                source_url="https://same.example/support",
                title="Resolution report",
                snippet="A specific report supports YES.",
                claim_type="resolution",
                supports_direction="yes",
                supports_confidence=0.9,
                retrieved_at=retrieved_at,
            )
        ]

    async def direct_fetcher(*_args):
        return None

    async def adjudicator(**_kwargs):
        return {
            "direction": "yes",
            "estimated_probability_yes": 0.8,
            "confidence": 0.8,
            "reason": (
                "Trade YES because the specific resolution report supports the "
                "outcome and the weak countercase does not overturn the executable edge."
            ),
        }

    verdict = await run_research_gate(
        SimpleNamespace(headline="", source="research_prewarm", url=""),
        SimpleNamespace(
            ticker="KXSAME-SOURCE",
            title="Will the event happen?",
            rules_primary="The official report resolves the market.",
            rules_secondary="",
            settlement_sources=(),
            contract_terms_url="",
            status="open",
            close_time="2099-12-31T23:59:59Z",
        ),
        model_direction="neutral",
        model_confidence=0.0,
        model_reason="",
        yes_ask=0.5,
        no_ask=0.5,
        live_mode=False,
        search_provider=search_provider,
        direct_fetcher=direct_fetcher,
        adjudicator=adjudicator,
        dossier_store=store,
        max_queries=6,
        research_timeout_seconds=5.0,
        require_decision_grade=True,
    )
    snapshot = await store.get_dossier_snapshot("KXSAME-SOURCE")

    assert verdict.status == ResearchStatus.NEEDS_RESEARCH
    assert verdict.skip_reason == "no_reliable_source_path"
    assert verdict.research_persisted is True
    assert verdict.research_run_id
    assert snapshot is not None
    assert snapshot.last_research_run_id == verdict.research_run_id
    assert snapshot.last_verdict_status == ResearchStatus.NEEDS_RESEARCH.value


def _persisted_candidate_verdict() -> research_gate_module.ResearchVerdict:
    return research_gate_module.ResearchVerdict(
        status=ResearchStatus.DECISION_GRADE_CANDIDATE,
        attempted=True,
        evidence=[
            ResearchEvidence(
                source_class="official_primary",
                source_name="Official source",
                source_url="https://agency.gov/result",
                title="Official result",
                snippet="Official evidence supports YES.",
                claim_type="resolution",
                contract_fingerprint="fingerprint-new",
            )
        ],
        force_side="yes",
        decision_grade_reasons=("price_present", "counter_evidence_present"),
        research_run_id="run-new",
        research_persisted=True,
        research_persistence_error="initial persistence warning",
        research_contract_fingerprint="fingerprint-new",
    )


def _persisted_candidate_snapshot(**overrides):
    values = {
        "market_ticker": "KXPERSIST",
        "last_research_run_id": "run-new",
        "last_contract_fingerprint": "fingerprint-new",
        "last_verdict_status": ResearchStatus.DECISION_GRADE_CANDIDATE.value,
        "last_skip_reason": None,
        "last_force_side": "yes",
        "last_estimated_probability": 0.7,
        "last_confidence": 0.8,
        "last_market_price": 0.5,
        "last_estimated_edge": 0.2,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "snapshot",
    [
        None,
        _persisted_candidate_snapshot(
            last_research_run_id="run-older",
            last_contract_fingerprint="fingerprint-older",
        ),
        _persisted_candidate_snapshot(market_ticker="KXOTHER"),
        _persisted_candidate_snapshot(last_contract_fingerprint="fingerprint-other"),
    ],
    ids=["missing_snapshot", "retained_older_run", "ticker_mismatch", "fingerprint_mismatch"],
)
async def test_candidate_reconciliation_fails_closed_for_unverified_dossier_identity(snapshot):
    class Store:
        async def get_dossier_snapshot(self, _ticker):
            return snapshot

        async def get_research_run_evidence(self, _ticker, _run_id):
            return _persisted_candidate_verdict().evidence

    verdict = _persisted_candidate_verdict()
    reconciled = await research_gate_module._reconcile_persisted_verdict(
        verdict,
        dossier_store=Store(),
        ticker="KXPERSIST",
        run_id="run-new",
    )

    assert reconciled.status == ResearchStatus.NEEDS_RESEARCH
    assert reconciled.skip_reason == "persistence_status_unverified"
    assert reconciled.force_side is None
    assert reconciled.decision_grade_reasons == verdict.decision_grade_reasons
    assert reconciled.research_run_id == "run-new"
    assert reconciled.research_contract_fingerprint == "fingerprint-new"
    assert reconciled.research_persisted is True
    assert reconciled.research_persistence_error == "initial persistence warning"


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_getter", ["snapshot", "run_evidence"])
async def test_candidate_reconciliation_fails_closed_when_persistence_getter_missing(
    missing_getter,
):
    async def get_dossier_snapshot(_ticker):
        return _persisted_candidate_snapshot()

    async def get_research_run_evidence(_ticker, _run_id):
        return _persisted_candidate_verdict().evidence

    store = SimpleNamespace(
        **{
            name: getter
            for name, getter in {
                "get_dossier_snapshot": get_dossier_snapshot,
                "get_research_run_evidence": get_research_run_evidence,
            }.items()
            if missing_getter not in name
        }
    )

    reconciled = await research_gate_module._reconcile_persisted_verdict(
        _persisted_candidate_verdict(),
        dossier_store=store,
        ticker="KXPERSIST",
        run_id="run-new",
    )

    assert reconciled.status == ResearchStatus.NEEDS_RESEARCH
    assert reconciled.skip_reason == "persistence_status_unverified"


@pytest.mark.asyncio
async def test_candidate_reconciliation_requires_matching_exact_run_evidence():
    class Store:
        async def get_dossier_snapshot(self, _ticker):
            return _persisted_candidate_snapshot()

        async def get_research_run_evidence(self, _ticker, _run_id):
            return [
                ResearchEvidence(
                    source_class="official_primary",
                    source_name="Official source",
                    source_url="https://agency.gov/older",
                    title="Older result",
                    snippet="Evidence belongs to a different contract.",
                    claim_type="resolution",
                    contract_fingerprint="fingerprint-older",
                )
            ]

    reconciled = await research_gate_module._reconcile_persisted_verdict(
        _persisted_candidate_verdict(),
        dossier_store=Store(),
        ticker="KXPERSIST",
        run_id="run-new",
    )

    assert reconciled.status == ResearchStatus.NEEDS_RESEARCH
    assert reconciled.skip_reason == "persistence_status_unverified"


@pytest.mark.asyncio
async def test_candidate_reconciliation_requires_verdict_run_id_to_match_persisted_run():
    class Store:
        async def get_dossier_snapshot(self, _ticker):
            return _persisted_candidate_snapshot(last_research_run_id="run-other")

        async def get_research_run_evidence(self, _ticker, _run_id):
            return _persisted_candidate_verdict().evidence

    reconciled = await research_gate_module._reconcile_persisted_verdict(
        _persisted_candidate_verdict(),
        dossier_store=Store(),
        ticker="KXPERSIST",
        run_id="run-other",
    )

    assert reconciled.status == ResearchStatus.NEEDS_RESEARCH
    assert reconciled.skip_reason == "persistence_status_unverified"


@pytest.mark.asyncio
async def test_nondecision_reconciliation_ignores_unavailable_persistence_identity():
    verdict = research_gate_module.ResearchVerdict(
        status=ResearchStatus.NEEDS_RESEARCH,
        attempted=True,
        skip_reason="insufficient_corroboration",
        decision_grade_reasons=("audit_reason",),
    )

    reconciled = await research_gate_module._reconcile_persisted_verdict(
        verdict,
        dossier_store=SimpleNamespace(),
        ticker="KXPERSIST",
        run_id="run-new",
    )

    assert reconciled is verdict


@pytest.mark.parametrize("timestamp_field", ["retrieved_at", "published_at", "inserted_at"])
def test_freshness_rejects_timestamps_beyond_clock_skew(timestamp_field):
    now = datetime(2026, 7, 11, 18, 0, tzinfo=timezone.utc)
    evidence = ResearchEvidence(
        source_class="official_primary",
        source_name="Official source",
        source_url="https://agency.gov/result",
        title="Result",
        snippet="Official result.",
        claim_type="resolution",
        supports_direction="yes",
        supports_confidence=0.9,
        **{timestamp_field: (now + timedelta(minutes=6)).isoformat()},
    )

    assert not research_gate_module._is_fresh_decision_evidence(evidence, now=now)
    assert not research_gate_module._is_fresh_evidence(evidence, now=now)


@pytest.mark.parametrize("timestamp_field", ["retrieved_at", "published_at", "inserted_at"])
def test_freshness_allows_narrow_clock_skew(timestamp_field):
    now = datetime(2026, 7, 11, 18, 0, tzinfo=timezone.utc)
    evidence = ResearchEvidence(
        source_class="official_primary",
        source_name="Official source",
        source_url="https://agency.gov/result",
        title="Result",
        snippet="Official result.",
        claim_type="resolution",
        supports_direction="yes",
        supports_confidence=0.9,
        **{timestamp_field: (now + timedelta(minutes=1)).isoformat()},
    )

    assert research_gate_module._is_fresh_decision_evidence(evidence, now=now)
    assert research_gate_module._is_fresh_evidence(evidence, now=now)


@pytest.mark.asyncio
async def test_contract_terms_direct_fetch_alone_does_not_satisfy_resolution_evidence():
    async def search_provider(query):
        assert query.source_class != "resolution_source"
        return [
            ResearchEvidence(
                source_class="reputable_secondary",
                source_name="Reuters",
                source_url="https://reuters.com/ceasefire",
                title="Ceasefire agreement signed",
                snippet="Officials confirm the agreement was signed before the deadline.",
                claim_type="corroboration",
                supports_direction="yes",
                supports_confidence=0.8,
            )
        ]

    async def direct_fetcher(url, source_class, claim_type):
        assert url == "https://kalshi.com/markets/KXCEASEFIRE-26JUL01"
        return ResearchEvidence(
            source_class=source_class,
            source_name="Kalshi Contract",
            source_url=url,
            title="Contract terms",
            snippet="Official public announcements decide the ceasefire agreement market.",
            claim_type=claim_type,
            supports_direction="neutral",
            supports_confidence=0.0,
        )

    async def adjudicator(**_kwargs):
        return {
            "direction": "yes",
            "estimated_probability_yes": 0.78,
            "confidence": 0.8,
            "reason": "Contract terms and wire evidence support YES.",
        }

    verdict = await run_research_gate(
        SimpleNamespace(
            headline="Officials confirm ceasefire agreement was signed",
            source="Reuters",
            url="https://reuters.com/ceasefire",
        ),
        SimpleNamespace(
            ticker="KXCEASEFIRE-26JUL01",
            title="Will a ceasefire agreement be signed by July 1?",
            rules_primary="The market resolves Yes if an agreement is signed by the deadline.",
            rules_secondary="The determination will be based on official public announcements.",
            settlement_sources=(),
            contract_terms_url="https://kalshi.com/markets/KXCEASEFIRE-26JUL01",
        ),
        model_direction="neutral",
        model_confidence=0.5,
        model_reason="No keywords.",
        yes_ask=0.6,
        no_ask=0.4,
        live_mode=False,
        search_provider=search_provider,
        direct_fetcher=direct_fetcher,
        adjudicator=adjudicator,
    )

    assert verdict.status == ResearchStatus.CONTINUE_RESEARCHING
    assert verdict.skip_reason == "missing_resolution_source"
    assert "https://kalshi.com/markets/KXCEASEFIRE-26JUL01" in {
        item.source_url for item in verdict.evidence
    }


@pytest.mark.asyncio
async def test_run_research_gate_passes_current_asks_to_default_adjudicator(monkeypatch):
    import analysis.research_gate as research_gate_module

    captured: dict[str, float | None] = {}
    fresh = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    async def search_provider(query):
        return [
            ResearchEvidence(
                source_class="official_primary",
                source_name="Official source",
                source_url=f"https://agency.gov/{query.query_intent}",
                title="Official result",
                snippet="Official source says the event supports YES.",
                claim_type=query.query_intent,
                supports_direction="yes",
                supports_confidence=0.8,
                retrieved_at=fresh,
            )
        ]

    async def default_adjudicator_spy(**kwargs):
        captured["yes_ask"] = kwargs.get("yes_ask")
        captured["no_ask"] = kwargs.get("no_ask")
        return {
            "direction": "yes",
            "estimated_probability_yes": 0.78,
            "confidence": 0.8,
            "reason": (
                "Official source supports YES at 78% versus current 60c market price. "
                "Countercase is weak but tracked."
            ),
        }

    monkeypatch.setattr(
        research_gate_module,
        "default_ollama_adjudicator",
        default_adjudicator_spy,
    )

    await run_research_gate(
        SimpleNamespace(headline="Official result posted", source="Official"),
        SimpleNamespace(
            ticker="KXASKS-26JUL01",
            title="Will the event resolve yes?",
            rules_primary="Official source resolves this market.",
            rules_secondary="",
            settlement_sources=(),
        ),
        model_direction="neutral",
        model_confidence=0.5,
        model_reason="No keywords.",
        yes_ask=0.6,
        no_ask=0.42,
        live_mode=False,
        search_provider=search_provider,
        require_decision_grade=True,
    )

    assert captured == {"yes_ask": 0.6, "no_ask": 0.42}


@pytest.mark.asyncio
async def test_default_ollama_adjudicator_requests_bounded_json(monkeypatch):
    captured: dict[str, object] = {}

    class _Response:
        def __init__(self):
            self.read_count = 0

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _limit):
            self.read_count += 1
            if self.read_count > 1:
                raise AssertionError("response body should be read once")
            return (
                b'{"choices":[{"message":{"content":"{\\"direction\\":\\"neutral\\"}"}}]}'
            )

    def fake_urlopen(request, *, timeout):
        captured["timeout"] = timeout
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return _Response()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    parsed = await default_ollama_adjudicator(
        evidence=[],
        queries=[],
        news=SimpleNamespace(headline="", source=""),
        market=SimpleNamespace(ticker="KXJSON", title="Will event happen?"),
        yes_ask=0.4,
        no_ask=0.61,
    )

    assert parsed == {"direction": "neutral"}
    assert captured["body"]["temperature"] == 0.0
    assert captured["body"]["max_tokens"] == 700
    assert captured["body"]["response_format"] == {"type": "json_object"}
    prompt = captured["body"]["messages"][1]["content"]
    assert "why this side" in prompt
    assert "probability versus current market price" in prompt
    assert "strongest countercase" in prompt


@pytest.mark.asyncio
async def test_strict_research_gate_skips_adjudication_without_actionable_price():
    calls = {"adjudicator": 0}
    fresh = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    async def search_provider(query):
        return [
            ResearchEvidence(
                source_class="official_primary",
                source_name="Official source",
                source_url=f"https://agency.gov/{query.query_intent}",
                title="Official result",
                snippet="Official source says the event supports YES.",
                claim_type=query.query_intent,
                supports_direction="yes",
                supports_confidence=0.8,
                retrieved_at=fresh,
            )
        ]

    async def adjudicator(**_kwargs):
        calls["adjudicator"] += 1
        return {
            "direction": "yes",
            "estimated_probability_yes": 0.78,
            "confidence": 0.8,
            "reason": "Should not run without actionable price.",
        }

    verdict = await run_research_gate(
        SimpleNamespace(headline="Official result posted", source="Official"),
        SimpleNamespace(
            ticker="KXNOPRICE-26JUL01",
            title="Will the event resolve yes?",
            rules_primary="Official source resolves this market.",
            rules_secondary="",
            settlement_sources=(),
        ),
        model_direction="neutral",
        model_confidence=0.5,
        model_reason="No keywords.",
        yes_ask=0.0,
        no_ask=1.0,
        live_mode=False,
        search_provider=search_provider,
        adjudicator=adjudicator,
        require_decision_grade=True,
    )

    assert verdict.status == ResearchStatus.UNTRADEABLE
    assert verdict.skip_reason == "non_actionable_market_price"
    assert calls["adjudicator"] == 0


@pytest.mark.asyncio
async def test_run_research_gate_reports_provider_exception():
    async def failing_search(_query):
        raise RuntimeError("rss unavailable")

    verdict = await run_research_gate(
        SimpleNamespace(headline="Iran crude output rises sharply", source="Reuters"),
        SimpleNamespace(
            ticker="KXIRANCRUDE-26JUL13-T3.8",
            title="Will Iran crude oil production be at least 3.8M bpd?",
            rules_primary="OPEC MOMR secondary sources decide the market.",
            rules_secondary="Later revisions ignored.",
            settlement_sources=(),
        ),
        model_direction="neutral",
        model_confidence=0.5,
        model_reason="No keywords.",
        yes_ask=0.51,
        no_ask=0.51,
        live_mode=False,
        search_provider=failing_search,
        adjudicator=_fake_adjudicator,
    )

    assert verdict.status == ResearchStatus.RESEARCH_PROVIDER_ERROR
    assert verdict.skip_reason == "research_provider_error"


@pytest.mark.asyncio
async def test_provider_error_does_not_hide_direct_official_evidence_blocker():
    fresh = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    async def failing_search(_query):
        raise RuntimeError("rss unavailable")

    async def direct_fetcher(url, source_class, claim_type):
        return ResearchEvidence(
            source_class=source_class,
            source_name="BEA",
            source_url=url,
            title="Gross Domestic Product",
            snippet="BEA page describes gross domestic product release tables.",
            claim_type=claim_type,
            supports_direction="neutral",
            supports_confidence=0.2,
            retrieved_at=fresh,
        )

    async def neutral_adjudicator(**_kwargs):
        return {
            "direction": "neutral",
            "estimated_probability_yes": None,
            "confidence": 0.4,
            "reason": "Official BEA page is present but does not yet contain a directional Q2 estimate.",
        }

    verdict = await run_research_gate(
        SimpleNamespace(headline="", source="research_prewarm"),
        SimpleNamespace(
            ticker="KXGDP-26JUL30-T3.0",
            title="Will real GDP increase by more than 3.0% in Q2 2026?",
            rules_primary="BEA advance estimate resolves this market.",
            rules_secondary="",
            settlement_sources=(
                SimpleNamespace(
                    label="BEA",
                    url="https://www.bea.gov/data/gdp/gross-domestic-product",
                ),
            ),
        ),
        model_direction="neutral",
        model_confidence=0.5,
        model_reason="No keywords.",
        yes_ask=0.26,
        no_ask=0.79,
        live_mode=False,
        search_provider=failing_search,
        direct_fetcher=direct_fetcher,
        adjudicator=neutral_adjudicator,
        require_decision_grade=True,
    )

    assert verdict.status == ResearchStatus.NEEDS_RESEARCH
    assert verdict.skip_reason != "research_provider_error"
    assert {item.source_name for item in verdict.evidence} == {"BEA"}


@pytest.mark.asyncio
async def test_noncritical_provider_error_does_not_block_sufficient_evidence():
    async def search_provider(query):
        if query.query_intent == "corroboration":
            return [
                ResearchEvidence(
                    source_class="reputable_secondary",
                    source_name="Reuters",
                    source_url="https://reuters.com/opec-corroboration",
                    title="Reuters confirms OPEC table",
                    snippet="OPEC table supports the reported production figure.",
                    claim_type="corroboration",
                    supports_direction="yes",
                    supports_confidence=0.8,
                )
            ]
        raise RuntimeError("one non-critical query failed")

    async def direct_fetcher(url, source_class, claim_type):
        return ResearchEvidence(
            source_class=source_class,
            source_name="OPEC",
            source_url=url,
            title="OPEC monthly report",
            snippet="Official table reports Iran crude production above threshold.",
            claim_type=claim_type,
            supports_direction="yes",
            supports_confidence=0.9,
        )

    verdict = await run_research_gate(
        SimpleNamespace(
            headline="Reuters confirms OPEC table supports Iran crude production",
            source="Reuters",
        ),
        SimpleNamespace(
            ticker="KXIRANCRUDE-26JUL13-T3.8",
            title="Will Iran crude oil production be at least 3.8M bpd?",
            rules_primary="OPEC MOMR secondary sources decide the market.",
            rules_secondary="Later revisions ignored.",
            settlement_sources=(
                SimpleNamespace(label="OPEC", url="https://opec.org/momr"),
            ),
        ),
        model_direction="neutral",
        model_confidence=0.5,
        model_reason="No keywords.",
        yes_ask=0.51,
        no_ask=0.51,
        live_mode=False,
        search_provider=search_provider,
        direct_fetcher=direct_fetcher,
        adjudicator=_fake_adjudicator,
    )

    assert verdict.status == ResearchStatus.TRADE_CANDIDATE
    assert verdict.force_side == "yes"


@pytest.mark.asyncio
async def test_noncritical_provider_error_does_not_block_decision_grade_candidate():
    fresh = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    async def search_provider(query):
        if query.query_intent == "base_rate":
            raise RuntimeError("base-rate search unavailable")
        if query.query_intent == "official_resolution":
            return [
                ResearchEvidence(
                    source_class="official_primary",
                    source_name="Official source",
                    source_url="https://agency.gov/signed",
                    title="Agreement notice",
                    snippet="Official notice says the agreement was signed before the deadline.",
                    claim_type=query.query_intent,
                    retrieved_at=fresh,
                )
            ]
        if query.query_intent == "supporting":
            return [
                ResearchEvidence(
                    source_class="reputable_secondary",
                    source_name="Reuters",
                    source_url="https://reuters.com/signed",
                    title="Agreement signed",
                    snippet="Reuters independently reports the agreement was signed.",
                    claim_type=query.query_intent,
                    retrieved_at=fresh,
                )
            ]
        if query.query_intent == "disconfirming":
            return [
                ResearchEvidence(
                    source_class="reputable_secondary",
                    source_name="AP",
                    source_url="https://apnews.com/ratification",
                    title="Ratification objection",
                    snippet="AP notes ratification of the ceasefire agreement could still fail.",
                    claim_type=query.query_intent,
                    retrieved_at=fresh,
                )
            ]
        return []

    async def adjudicator(**_kwargs):
        return {
            "direction": "yes",
            "estimated_probability_yes": 0.68,
            "confidence": 0.82,
            "reason": (
                "Trade YES because the official notice says the agreement was signed "
                "before July 1 and Reuters independently confirms signing. Market YES "
                "ask is 55c versus 68% probability, leaving positive edge after costs. "
                "Counter evidence is AP ratification risk, but the rules require signing. "
                "This would prove wrong if the rules require ratification."
            ),
            "evidence_assessments": [
                {
                    "source_url": "https://agency.gov/signed",
                    "supports_direction": "yes",
                    "supports_confidence": 0.9,
                },
                {
                    "source_url": "https://reuters.com/signed",
                    "supports_direction": "yes",
                    "supports_confidence": 0.8,
                },
                {
                    "source_url": "https://apnews.com/ratification",
                    "supports_direction": "no",
                    "supports_confidence": 0.35,
                },
            ],
            "counterclaims": ["AP says ratification could still fail."],
        }

    verdict = await run_research_gate(
        SimpleNamespace(headline="Agreement signed before deadline", source="Reuters"),
        SimpleNamespace(
            ticker="KXCEASEFIRE-26JUL01",
            title="Will a ceasefire agreement be signed by July 1?",
            rules_primary="The market resolves Yes if an agreement is signed by the deadline.",
            rules_secondary="Official public announcements decide the market.",
            settlement_sources=(),
            contract_terms_url="https://kalshi.com/markets/KXCEASEFIRE-26JUL01",
        ),
        model_direction="neutral",
        model_confidence=0.5,
        model_reason="No keyword hit.",
        yes_ask=0.55,
        no_ask=0.47,
        live_mode=False,
        search_provider=search_provider,
        adjudicator=adjudicator,
        require_decision_grade=True,
    )

    assert verdict.status == ResearchStatus.DECISION_GRADE_CANDIDATE
    assert verdict.force_side == "yes"


@pytest.mark.asyncio
async def test_run_research_gate_reports_adjudicator_exception():
    async def failing_adjudicator(**_kwargs):
        raise RuntimeError("ollama unavailable")

    verdict = await run_research_gate(
        SimpleNamespace(headline="Iran crude output rises sharply", source="Reuters"),
        SimpleNamespace(
            ticker="KXIRANCRUDE-26JUL13-T3.8",
            title="Will Iran crude oil production be at least 3.8M bpd?",
            rules_primary="OPEC MOMR secondary sources decide the market.",
            rules_secondary="Later revisions ignored.",
            settlement_sources=(),
        ),
        model_direction="neutral",
        model_confidence=0.5,
        model_reason="No keywords.",
        yes_ask=0.51,
        no_ask=0.51,
        live_mode=False,
        search_provider=_fake_search,
        adjudicator=failing_adjudicator,
    )

    assert verdict.status == ResearchStatus.RESEARCH_ADJUDICATOR_ERROR
    assert verdict.skip_reason == "research_adjudicator_error"
    assert verdict.market_price == pytest.approx(0.51)


@pytest.mark.asyncio
async def test_run_research_gate_times_out_hung_search_provider(tmp_path):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")
    await store.initialize()
    market = SimpleNamespace(
        ticker="KXIRANCRUDE-26JUL13-T3.8",
        title="Will Iran crude oil production be at least 3.8M bpd?",
        rules_primary="OPEC MOMR secondary sources decide the market.",
        rules_secondary="Later revisions ignored.",
        settlement_sources=(),
    )

    async def hung_search(_query):
        await asyncio.sleep(60)
        return []

    verdict = await asyncio.wait_for(
        run_research_gate(
            SimpleNamespace(headline="Iran crude output rises sharply", source="Reuters"),
            market,
            model_direction="neutral",
            model_confidence=0.5,
            model_reason="No keywords.",
            yes_ask=0.51,
            no_ask=0.51,
            live_mode=False,
            search_provider=hung_search,
            adjudicator=_fake_adjudicator,
            dossier_store=store,
            research_timeout_seconds=0.05,
        ),
        timeout=1.0,
    )

    assert verdict.status == ResearchStatus.CONTINUE_RESEARCHING
    assert verdict.skip_reason == "research_timeout"
    assert verdict.research_run_id
    assert verdict.research_persisted is True
    fields = verdict.log_fields()
    assert fields["research_contract_fingerprint"] == _contract_fingerprint(market)
    snapshot = await store.get_dossier_snapshot(market.ticker)
    assert snapshot is not None
    assert snapshot.last_contract_fingerprint == _contract_fingerprint(market)


@pytest.mark.asyncio
async def test_run_research_gate_timeout_preserves_vetted_dossier_snapshot(tmp_path):
    store = ResearchDossierStore(tmp_path / "research_dossier.db")
    await store.initialize()
    market = SimpleNamespace(
        ticker="KXIRANCRUDE-26JUL13-T3.8",
        title="Will Iran crude oil production be at least 3.8M bpd?",
        rules_primary="OPEC MOMR secondary sources decide the market.",
        rules_secondary="Later revisions ignored.",
        settlement_sources=(),
    )
    await store.record_research_run(
        market.ticker,
        "rr-vetted-timeout",
        trigger_headline="scheduled prewarm",
        trigger_source="research_prewarm",
        attempted=True,
        summary="Cached vetted research supports yes.",
        verdict_status=ResearchStatus.TRADE_CANDIDATE.value,
        force_side="yes",
        estimated_probability=0.8,
        confidence=0.8,
        contract_fingerprint=_contract_fingerprint(market),
    )

    async def hung_search(_query):
        await asyncio.sleep(60)
        return []

    verdict = await asyncio.wait_for(
        run_research_gate(
            SimpleNamespace(headline="Iran crude output rises sharply", source="Reuters"),
            market,
            model_direction="neutral",
            model_confidence=0.5,
            model_reason="No keywords.",
            yes_ask=0.51,
            no_ask=0.51,
            live_mode=False,
            search_provider=hung_search,
            adjudicator=_fake_adjudicator,
            dossier_store=store,
            research_timeout_seconds=0.05,
        ),
        timeout=1.0,
    )

    assert verdict.status == ResearchStatus.CONTINUE_RESEARCHING
    assert verdict.skip_reason == "research_timeout"
    assert verdict.research_run_id
    assert verdict.research_run_id != "rr-vetted-timeout"
    snapshot = await store.get_dossier_snapshot(market.ticker)
    assert snapshot is not None
    assert snapshot.last_research_run_id == "rr-vetted-timeout"
    assert snapshot.last_verdict_status == ResearchStatus.TRADE_CANDIDATE.value
    assert snapshot.last_force_side == "yes"


@pytest.mark.asyncio
async def test_run_research_gate_enforces_end_to_end_timeout_budget():
    async def slow_direct_fetcher(url, source_class, claim_type):
        await asyncio.sleep(0.04)
        return ResearchEvidence(
            source_class=source_class,
            source_name=url,
            source_url=url,
            title="Slow source",
            snippet="Slow source",
            claim_type=claim_type,
        )

    started = asyncio.get_running_loop().time()
    verdict = await run_research_gate(
        SimpleNamespace(headline="Slow research target", source="Reuters"),
        SimpleNamespace(
            ticker="KXSLOW-26JUL13",
            title="Will slow source resolve?",
            rules_primary="Two official sources decide this market.",
            rules_secondary="Later revisions ignored.",
            settlement_sources=(
                SimpleNamespace(label="Source A", url="https://source-a.example.com/result"),
                SimpleNamespace(label="Source B", url="https://source-b.example.com/result"),
            ),
        ),
        model_direction="neutral",
        model_confidence=0.5,
        model_reason="No keywords.",
        yes_ask=0.51,
        no_ask=0.51,
        live_mode=False,
        search_provider=_fake_search,
        direct_fetcher=slow_direct_fetcher,
        adjudicator=_fake_adjudicator,
        research_timeout_seconds=0.05,
    )
    elapsed = asyncio.get_running_loop().time() - started

    assert verdict.status == ResearchStatus.CONTINUE_RESEARCHING
    assert verdict.skip_reason == "research_timeout"
    assert elapsed < 0.10


@pytest.mark.asyncio
async def test_generic_market_contract_terms_fallback_remains_non_promotable():
    async def search_provider(query):
        if query.source_class == "resolution_source":
            return [
                ResearchEvidence(
                    source_class="resolution_source",
                    source_name="Kalshi Contract",
                    source_url="https://kalshi.com/markets/KXCEASEFIRE-26JUL01",
                    title="Contract terms",
                    snippet="Official public announcements decide the ceasefire agreement market.",
                    claim_type="resolution",
                    supports_direction="yes",
                    supports_confidence=0.85,
                )
            ]
        return [
            ResearchEvidence(
                source_class="reputable_secondary",
                source_name="Reuters",
                source_url="https://reuters.com/ceasefire",
                title="Ceasefire agreement signed",
                snippet="Officials confirm the agreement was signed before the deadline.",
                claim_type="corroboration",
                supports_direction="yes",
                supports_confidence=0.8,
            )
        ]

    async def adjudicator(**_kwargs):
        return {
            "direction": "yes",
            "estimated_probability_yes": 0.78,
            "confidence": 0.8,
            "reason": "Contract terms and wire evidence support YES.",
        }

    verdict = await run_research_gate(
        SimpleNamespace(
            headline="Officials confirm ceasefire agreement was signed",
            source="Reuters",
            url="https://reuters.com/ceasefire",
        ),
        SimpleNamespace(
            ticker="KXCEASEFIRE-26JUL01",
            title="Will a ceasefire agreement be signed by July 1?",
            rules_primary="The market resolves Yes if an agreement is signed by the deadline.",
            rules_secondary="The determination will be based on official public announcements.",
            settlement_sources=(),
            contract_terms_url="https://kalshi.com/markets/KXCEASEFIRE-26JUL01",
        ),
        model_direction="neutral",
        model_confidence=0.5,
        model_reason="No keywords.",
        yes_ask=0.6,
        no_ask=0.4,
        live_mode=False,
        search_provider=search_provider,
        adjudicator=adjudicator,
    )

    assert verdict.status == ResearchStatus.CONTINUE_RESEARCHING
    assert verdict.skip_reason == "missing_resolution_source"
