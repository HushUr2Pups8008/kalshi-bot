import json
from pathlib import Path

from scripts.simulations import matcher_weight_replay


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def test_score_multiplier_uses_mean_and_treats_bad_weights_as_one() -> None:
    weights = {
        "KXTEST:deal": {"weight": 0.25},
        "KXTEST:trump": {"weight": 0.4},
        "KXTEST:bad": {"weight": "not-a-number"},
    }

    assert matcher_weight_replay.score_multiplier(["deal", "trump"], "KXTEST", weights) == 0.325
    assert matcher_weight_replay.score_multiplier(["missing", "bad"], "KXTEST", weights) == 1.0


def test_score_multiplier_keeps_defining_token_at_full_weight() -> None:
    # Replay must mirror production's defining-token guard so replayed EV does
    # not assume the loop floored a market's own ticker token out of the funnel.
    weights = {"KXVISITIRAN:iran": {"weight": 0.10}}
    assert matcher_weight_replay.score_multiplier(["iran"], "KXVISITIRAN", weights) == 1.0
    # bridge token (not in ticker) is still downweighted
    assert matcher_weight_replay.score_multiplier(["sanctions"], "KXVISITIRAN",
                                                  {"KXVISITIRAN:sanctions": {"weight": 0.20}}) == 0.20


def test_replay_classifies_head_only_over_suppression(tmp_path: Path) -> None:
    head_weights = tmp_path / "head.json"
    dirty_weights = tmp_path / "dirty.json"
    log_root = tmp_path / "logs" / "trades"
    live = log_root / "live"
    live.mkdir(parents=True)
    log_file = live / "trades.jsonl"

    # Prefix deliberately avoids containing 'deal'/'trump' so both stay genuine
    # downweightable bridge tokens. (A token that is a substring of its ticker
    # is the market's defining token and is never downweighted — see
    # is_market_defining_token / TestDefiningTokenGuard.)
    _write_json(
        head_weights,
        {
            "KXNEWPACT:deal": {"weight": 0.25},
            "KXNEWPACT:trump": {"weight": 0.4},
            "KXTEST:onlyhead": {"weight": 0.1},
        },
    )
    _write_json(
        dirty_weights,
        {
            "KXNEWPACT:deal": {"weight": 0.1},
            "KXNEWPACT:trump": {"weight": 0.1},
            "KXTEST:onlydirty": {"weight": 1.0},
        },
    )
    _write_jsonl(
        log_file,
        [
            {"type": "EARLY_FRESH_PASS", "ts": "2026-05-29T00:00:00+00:00"},
            {
                "type": "MATCH_WEIGHT_APPLIED",
                "ts": "2026-05-29T00:01:00+00:00",
                "source": "test",
                "headline": "Trump announces new deal",
                "ticker": "KXNEWPACT-JUN01",
                "market_title": "Will Trump announce a new deal?",
                "tokens": ["deal", "trump"],
                "pre_weight_score": 0.2,
            },
            {
                "type": "MATCH_WEIGHT_APPLIED",
                "ts": "2026-05-29T00:02:00+00:00",
                "source": "test",
                "headline": "Both sides clear",
                "ticker": "KXBOTH-JUN01",
                "market_title": "Both clear?",
                "tokens": ["unweighted"],
                "pre_weight_score": 0.07,
            },
            {
                "type": "MATCH_WEIGHT_APPLIED",
                "ts": "2026-05-29T00:03:00+00:00",
                "source": "test",
                "headline": "Dirty only clears",
                "ticker": "KXTEST-JUN01",
                "market_title": "Dirty only?",
                "tokens": ["onlyhead", "onlydirty"],
                "pre_weight_score": 0.1,
            },
        ],
        )

    result = matcher_weight_replay.replay(
        log_root=log_root,
        dirty_weights_path=dirty_weights,
        head_weights_path=head_weights,
        recent_files=1,
        threshold=0.06,
    )

    assert result["summary"] == {
        "threshold": 0.06,
        "log_files": 1,
        "candidate_events": 3,
        "head_clears": 2,
        "dirty_clears": 2,
        "head_only": 1,
        "dirty_only": 1,
        "both_clear": 1,
        "neither_clear": 0,
    }
    assert result["file_counts"][str(log_file)]["EARLY_FRESH_PASS"] == 1
    assert result["by_prefix"]["KXNEWPACT"]["head_only"] == 1
    assert result["by_ticker"]["KXNEWPACT-JUN01"]["head_only"] == 1
    example = result["over_suppressed_examples"][0]
    assert example["ticker"] == "KXNEWPACT-JUN01"
    assert example["head_score"] == 0.065
    assert example["dirty_score"] == 0.02
