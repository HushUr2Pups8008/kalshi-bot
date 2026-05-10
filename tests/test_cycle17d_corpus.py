from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.edge_replay.build_cycle17d_corpus import build_cycle17d_corpus, main
from tests._helpers import write_jsonl


def _row(
    ticker: str,
    decision_ts: str,
    signal_source: str,
    headline: str,
    market_yes_price: float | None,
    **extra,
) -> dict:
    row = {
        "ticker": ticker,
        "decision_ts": decision_ts,
        "signal_source": signal_source,
        "headline": headline,
        "market_yes_price": market_yes_price,
    }
    row.update(extra)
    return row


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_cycle13_sources_merge_with_live_preference_and_manifest(tmp_path):
    cycle13_live = tmp_path / "cycle13_live.jsonl"
    cycle13_local = tmp_path / "cycle13_local.jsonl"
    cycle15b = tmp_path / "cycle15b.jsonl"
    cycle16d = tmp_path / "cycle16d.jsonl"
    output = tmp_path / "replay_dataset_merged.jsonl"
    manifest_path = tmp_path / "build_manifest.json"

    duplicate_key = {
        "ticker": "KXALPHA",
        "decision_ts": "2026-05-01T00:00:00Z",
        "signal_source": "publisher_rss",
        "headline": "shared cycle13 headline",
    }
    write_jsonl(
        cycle13_live,
        [_row(**duplicate_key, market_yes_price=0.42, live_only=True)],
    )
    write_jsonl(
        cycle13_local,
        [
            _row(**duplicate_key, market_yes_price=0.41, local_only=True),
            _row("KXBETA", "2026-05-01T00:01:00Z", "google_news", "local only", None),
        ],
    )
    write_jsonl(cycle15b, [])
    write_jsonl(cycle16d, [])

    manifest = build_cycle17d_corpus(
        inputs={
            "cycle13_live": cycle13_live,
            "cycle13_local": cycle13_local,
            "cycle15b": cycle15b,
            "cycle16d": cycle16d,
        },
        output=output,
        manifest_path=manifest_path,
    )

    rows = _read_jsonl(output)
    assert len(rows) == 2
    assert rows[0]["ticker"] == "KXALPHA"
    assert rows[0]["live_only"] is True
    assert "local_only" not in rows[0]
    assert {row["cohort"] for row in rows} == {"PRE_FIX"}

    assert manifest["input_row_counts"] == {
        "cycle13_live": 1,
        "cycle13_local": 2,
        "cycle15b": 0,
        "cycle16d": 0,
    }
    assert manifest["output_row_count"] == 2
    assert manifest["dropped_duplicate_count"] == 1
    assert manifest["dropped_counts_by_source_reason"] == {
        "cycle13_local": {"cycle13_live_preferred": 1}
    }
    assert manifest["cohort_counts"] == {"PRE_FIX": 2}
    assert manifest["dedup_key_blank_headline_count_by_source"] == {}
    assert manifest["input_paths"] == {
        "cycle13_live": str(cycle13_live),
        "cycle13_local": str(cycle13_local),
        "cycle15b": str(cycle15b),
        "cycle16d": str(cycle16d),
    }
    assert manifest["output_path"] == str(output)
    assert manifest["sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    assert json.loads(manifest_path.read_text(encoding="utf-8")) == manifest


def test_cross_vintage_duplicate_prefers_priced_later_source_and_cli_json(tmp_path, capsys):
    cycle13_live = tmp_path / "cycle13_live.jsonl"
    cycle13_local = tmp_path / "cycle13_local.jsonl"
    cycle15b = tmp_path / "cycle15b.jsonl"
    cycle16d = tmp_path / "cycle16d.jsonl"
    output = tmp_path / "out.jsonl"
    manifest_path = tmp_path / "manifest.json"

    duplicate_key = {
        "ticker": "KXGAMMA",
        "decision_ts": "2026-05-02T00:00:00Z",
        "signal_source": "publisher_rss",
        "headline": "rebuilt duplicate",
    }
    write_jsonl(cycle13_live, [])
    write_jsonl(cycle13_local, [])
    write_jsonl(
        cycle15b,
        [
            _row(**duplicate_key, market_yes_price=None, selected="cycle15b"),
            _row("KXOMEGA", "2026-05-01T00:00:00Z", "google_news", "sorts first", 0.33),
        ],
    )
    write_jsonl(
        cycle16d,
        [
            _row(**duplicate_key, market_yes_price=0.55, selected="cycle16d"),
            _row(
                "KXNEW",
                "2026-05-03T00:00:00Z",
                "publisher_rss",
                "preserve post fix new",
                0.66,
                cohort="POST_FIX_NEW",
            ),
        ],
    )

    exit_code = main(
        [
            "--cycle13-live",
            str(cycle13_live),
            "--cycle13-local",
            str(cycle13_local),
            "--cycle15b",
            str(cycle15b),
            "--cycle16d",
            str(cycle16d),
            "--output",
            str(output),
            "--manifest",
            str(manifest_path),
            "--json",
        ]
    )

    assert exit_code == 0
    printed = json.loads(capsys.readouterr().out)
    rows = _read_jsonl(output)
    assert [row["ticker"] for row in rows] == ["KXOMEGA", "KXGAMMA", "KXNEW"]
    assert rows[1]["selected"] == "cycle16d"
    assert rows[0]["cohort"] == "POST_FIX_REBUILT"
    assert rows[1]["cohort"] == "POST_FIX_REBUILT"
    assert rows[2]["cohort"] == "POST_FIX_NEW"

    assert printed["output_row_count"] == 3
    assert printed["dropped_duplicate_count"] == 1
    assert printed["dropped_counts_by_source_reason"] == {
        "cycle15b": {"non_cycle13_conflict_priced_row_preferred": 1}
    }
    assert printed["cohort_counts"] == {"POST_FIX_NEW": 1, "POST_FIX_REBUILT": 2}
    assert printed["dedup_key_blank_headline_count_by_source"] == {}
    assert printed["dedup_policy"] == {
        "key": ["ticker", "decision_ts", "signal_source", "headline"],
        "cycle13_conflicts": "prefer cycle13_live over cycle13_local",
        "other_conflicts": (
            "prefer rows with non-null market_yes_price; "
            "if tied prefer source precedence cycle16d > cycle15b > cycle13_live > cycle13_local; "
            "if still tied keep the first input row"
        ),
    }
    assert printed["sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    assert json.loads(manifest_path.read_text(encoding="utf-8")) == printed


def test_source_precedence_branch_is_recorded_when_prices_tie(tmp_path):
    cycle13_live = tmp_path / "cycle13_live.jsonl"
    cycle13_local = tmp_path / "cycle13_local.jsonl"
    cycle15b = tmp_path / "cycle15b.jsonl"
    cycle16d = tmp_path / "cycle16d.jsonl"
    output = tmp_path / "out.jsonl"
    manifest_path = tmp_path / "manifest.json"
    duplicate_key = {
        "ticker": "KXDELTA",
        "decision_ts": "2026-05-02T00:00:00Z",
        "signal_source": "publisher_rss",
        "headline": "same price duplicate",
        "market_yes_price": 0.55,
    }
    write_jsonl(cycle13_live, [])
    write_jsonl(cycle13_local, [])
    write_jsonl(cycle15b, [_row(**duplicate_key, selected="cycle15b")])
    write_jsonl(cycle16d, [_row(**duplicate_key, selected="cycle16d")])

    manifest = build_cycle17d_corpus(
        inputs={
            "cycle13_live": cycle13_live,
            "cycle13_local": cycle13_local,
            "cycle15b": cycle15b,
            "cycle16d": cycle16d,
        },
        output=output,
        manifest_path=manifest_path,
    )

    rows = _read_jsonl(output)
    assert len(rows) == 1
    assert rows[0]["selected"] == "cycle16d"
    assert manifest["dropped_counts_by_source_reason"] == {
        "cycle15b": {"non_cycle13_conflict_source_precedence_preferred": 1}
    }


def test_same_source_duplicate_keeps_first_input_row(tmp_path):
    cycle13_live = tmp_path / "cycle13_live.jsonl"
    cycle13_local = tmp_path / "cycle13_local.jsonl"
    cycle15b = tmp_path / "cycle15b.jsonl"
    cycle16d = tmp_path / "cycle16d.jsonl"
    output = tmp_path / "out.jsonl"
    manifest_path = tmp_path / "manifest.json"
    duplicate_key = {
        "ticker": "KXEPSILON",
        "decision_ts": "2026-05-02T00:00:00Z",
        "signal_source": "publisher_rss",
        "headline": "same source duplicate",
        "market_yes_price": 0.55,
    }
    write_jsonl(cycle13_live, [])
    write_jsonl(cycle13_local, [])
    write_jsonl(
        cycle15b,
        [
            _row(**duplicate_key, selected="first"),
            _row(**duplicate_key, selected="second"),
        ],
    )
    write_jsonl(cycle16d, [])

    manifest = build_cycle17d_corpus(
        inputs={
            "cycle13_live": cycle13_live,
            "cycle13_local": cycle13_local,
            "cycle15b": cycle15b,
            "cycle16d": cycle16d,
        },
        output=output,
        manifest_path=manifest_path,
    )

    rows = _read_jsonl(output)
    assert len(rows) == 1
    assert rows[0]["selected"] == "first"
    assert manifest["dropped_counts_by_source_reason"] == {
        "cycle15b": {"non_cycle13_conflict_first_input_order_preferred": 1}
    }


def test_missing_required_dedup_key_field_rejected_before_merge(tmp_path):
    cycle13_live = tmp_path / "cycle13_live.jsonl"
    cycle13_local = tmp_path / "cycle13_local.jsonl"
    cycle15b = tmp_path / "cycle15b.jsonl"
    cycle16d = tmp_path / "cycle16d.jsonl"
    write_jsonl(
        cycle13_live,
        [
            {
                "ticker": "KXBROKEN",
                "headline": "missing source",
                "decision_ts": "2026-05-01T00:00:00Z",
            }
        ],
    )
    write_jsonl(cycle13_local, [])
    write_jsonl(cycle15b, [])
    write_jsonl(cycle16d, [])

    with pytest.raises(ValueError, match="missing required dedup key field"):
        build_cycle17d_corpus(
            inputs={
                "cycle13_live": cycle13_live,
                "cycle13_local": cycle13_local,
                "cycle15b": cycle15b,
                "cycle16d": cycle16d,
            },
            output=tmp_path / "out.jsonl",
            manifest_path=tmp_path / "manifest.json",
        )


def test_missing_headline_normalized_and_reported(tmp_path):
    cycle13_live = tmp_path / "cycle13_live.jsonl"
    cycle13_local = tmp_path / "cycle13_local.jsonl"
    cycle15b = tmp_path / "cycle15b.jsonl"
    cycle16d = tmp_path / "cycle16d.jsonl"
    write_jsonl(
        cycle13_live,
        [
            {
                "ticker": "KXHEADLINE",
                "decision_ts": "2026-05-01T00:00:00Z",
                "signal_source": "unknown",
                "market_yes_price": 0.42,
            }
        ],
    )
    write_jsonl(cycle13_local, [])
    write_jsonl(cycle15b, [])
    write_jsonl(cycle16d, [])

    manifest = build_cycle17d_corpus(
        inputs={
            "cycle13_live": cycle13_live,
            "cycle13_local": cycle13_local,
            "cycle15b": cycle15b,
            "cycle16d": cycle16d,
        },
        output=tmp_path / "out.jsonl",
        manifest_path=tmp_path / "manifest.json",
    )

    rows = _read_jsonl(tmp_path / "out.jsonl")
    assert rows[0]["headline"] == ""
    assert manifest["dedup_key_blank_headline_count_by_source"] == {"cycle13_live": 1}


def test_non_json_object_row_rejected(tmp_path):
    cycle13_live = tmp_path / "cycle13_live.jsonl"
    cycle13_local = tmp_path / "cycle13_local.jsonl"
    cycle15b = tmp_path / "cycle15b.jsonl"
    cycle16d = tmp_path / "cycle16d.jsonl"
    cycle13_live.write_text("[1, 2, 3]\n", encoding="utf-8")
    write_jsonl(cycle13_local, [])
    write_jsonl(cycle15b, [])
    write_jsonl(cycle16d, [])

    with pytest.raises(ValueError, match="is not a JSON object"):
        build_cycle17d_corpus(
            inputs={
                "cycle13_live": cycle13_live,
                "cycle13_local": cycle13_local,
                "cycle15b": cycle15b,
                "cycle16d": cycle16d,
            },
            output=tmp_path / "out.jsonl",
            manifest_path=tmp_path / "manifest.json",
        )
