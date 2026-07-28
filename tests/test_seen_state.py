"""Tests for durable, bounded ingest seen-ID checkpoints."""

import json
import logging
from collections import OrderedDict
from pathlib import Path

import pytest

from feeds.seen_state import checkpoint_seen_ids, load_seen_ids


def test_checkpoint_round_trip_retains_newest_ids_within_cap(tmp_path):
    path = tmp_path / "rss_seen_ids.json"
    seen = OrderedDict((value, None) for value in ("a" * 64, "b" * 64, "c" * 64))

    checkpoint_seen_ids(path, seen, max_seen=2)

    assert list(load_seen_ids(path, max_seen=2)) == ["b" * 64, "c" * 64]


def test_checkpoint_writes_versioned_schema_with_only_valid_ids(tmp_path):
    path = tmp_path / "rss_seen_ids.json"
    seen = OrderedDict(
        (value, None)
        for value in (
            "a" * 64,
            "A" * 64,
            "b" * 63,
            1,
            "b" * 64,
            "c" * 64,
        )
    )

    checkpoint_seen_ids(path, seen, max_seen=2)

    assert json.loads(path.read_text(encoding="utf-8")) == {
        "version": 1,
        "ids": ["b" * 64, "c" * 64],
    }


def test_missing_checkpoint_fails_open(tmp_path):
    loaded = load_seen_ids(tmp_path / "rss_seen_ids.json", max_seen=5_000)

    assert isinstance(loaded, OrderedDict)
    assert list(loaded) == []


def test_missing_checkpoint_fails_open_without_warning(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger="feeds.seen_state"):
        assert list(load_seen_ids(tmp_path / "rss_seen_ids.json", max_seen=5_000)) == []

    assert not caplog.records


def test_corrupt_checkpoint_fails_open(tmp_path):
    path = tmp_path / "rss_seen_ids.json"
    path.write_text("not-json", encoding="utf-8")

    assert list(load_seen_ids(path, max_seen=5_000)) == []


@pytest.mark.parametrize("version", [True, 1.0])
def test_non_integer_schema_versions_fail_open_even_with_valid_ids(tmp_path, version):
    path = tmp_path / "rss_seen_ids.json"
    path.write_text(
        json.dumps({"version": version, "ids": ["a" * 64]}),
        encoding="utf-8",
    )

    assert list(load_seen_ids(path, max_seen=5_000)) == []


def test_unreadable_checkpoint_fails_open(tmp_path, monkeypatch):
    path = tmp_path / "rss_seen_ids.json"
    path.write_text('{"version": 1, "ids": ["' + "a" * 64 + '"]}', encoding="utf-8")

    def raise_permission_error(*_args, **_kwargs):
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "read_text", raise_permission_error)

    assert list(load_seen_ids(path, max_seen=5_000)) == []


def test_load_discards_invalid_ids_and_keeps_newest_valid_values(tmp_path):
    path = tmp_path / "rss_seen_ids.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "ids": ["a" * 64, "A" * 64, "b" * 63, "b" * 64, "c" * 64],
            }
        ),
        encoding="utf-8",
    )

    assert list(load_seen_ids(path, max_seen=2)) == ["b" * 64, "c" * 64]


def test_load_fails_open_when_checkpoint_ids_is_an_object(tmp_path):
    path = tmp_path / "rss_seen_ids.json"
    path.write_text(
        json.dumps({"version": 1, "ids": {"a" * 64: None}}),
        encoding="utf-8",
    )

    assert list(load_seen_ids(path, max_seen=5_000)) == []


def test_load_moves_duplicate_ids_to_newest_position_before_capping(tmp_path):
    path = tmp_path / "rss_seen_ids.json"
    path.write_text(
        json.dumps({"version": 1, "ids": ["a" * 64, "b" * 64, "a" * 64, "c" * 64]}),
        encoding="utf-8",
    )

    assert list(load_seen_ids(path, max_seen=2)) == ["a" * 64, "c" * 64]


def test_malformed_checkpoint_fails_open_with_generic_warning(tmp_path, caplog):
    path = tmp_path / "rss_seen_ids.json"
    path.write_text("not-json", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="feeds.seen_state"):
        assert list(load_seen_ids(path, max_seen=5_000)) == []

    messages = [record.getMessage() for record in caplog.records]
    assert messages == ["Unable to load seen-ID checkpoint; starting empty."]


def test_checkpoint_replace_failure_cleans_temp_and_preserves_previous_file(tmp_path, monkeypatch):
    path = tmp_path / "rss_seen_ids.json"
    previous = '{"version": 1, "ids": ["' + "a" * 64 + '"]}'
    path.write_text(previous, encoding="utf-8")
    temp_path = path.with_suffix(path.suffix + ".tmp")

    def raise_replace(source, destination):
        assert source == temp_path
        assert destination == path
        assert source.parent == path.parent
        raise OSError("no")

    monkeypatch.setattr("feeds.seen_state.os.replace", raise_replace)

    with pytest.raises(OSError, match="no"):
        checkpoint_seen_ids(path, OrderedDict((("b" * 64, None),)), max_seen=5_000)

    assert path.read_text(encoding="utf-8") == previous
    assert not temp_path.exists()
