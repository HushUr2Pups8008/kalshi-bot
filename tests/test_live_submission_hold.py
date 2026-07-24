"""Tests for durable unknown-live-submission ticker holds."""

from __future__ import annotations

import threading

import trading.live_submission_hold as hold_module
from trading.live_submission_hold import LiveSubmissionHoldStore


def test_hold_persists_across_fresh_store_without_auto_clear(tmp_path):
    path = tmp_path / "unknown_submission_holds.json"
    ticker = "KXTEST-25DEC31"

    store = LiveSubmissionHoldStore(path)
    assert store.can_submit(ticker)
    assert store.hold(ticker)

    fresh_store = LiveSubmissionHoldStore(path)
    assert not fresh_store.can_submit(ticker)
    assert fresh_store.can_submit("KXOTHER-25DEC31")


def test_reserve_is_exclusive_across_concurrent_stores(tmp_path):
    path = tmp_path / "unknown_submission_holds.json"
    ticker = "KXTEST-25DEC31"
    first = LiveSubmissionHoldStore(path)
    second = LiveSubmissionHoldStore(path)
    barrier = threading.Barrier(2)
    results: list[bool] = []

    def reserve(store):
        barrier.wait()
        results.append(store.reserve(ticker))

    first_thread = threading.Thread(target=reserve, args=(first,))
    second_thread = threading.Thread(target=reserve, args=(second,))
    first_thread.start()
    second_thread.start()
    first_thread.join(timeout=2)
    second_thread.join(timeout=2)

    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert sorted(results) == [False, True]
    assert not LiveSubmissionHoldStore(path).can_submit(ticker)


def test_release_write_failure_keeps_reservation_fail_closed(tmp_path, monkeypatch):
    path = tmp_path / "unknown_submission_holds.json"
    ticker = "KXTEST-25DEC31"
    store = LiveSubmissionHoldStore(path)
    assert store.reserve(ticker)

    def fail_replace(*_args):
        raise OSError("release replace unavailable")

    monkeypatch.setattr(hold_module.os, "replace", fail_replace)

    assert not store.release(ticker)
    assert not store.can_submit(ticker)
    assert not LiveSubmissionHoldStore(path).can_submit(ticker)


def test_release_missing_reservation_persists_fail_closed_ticker(tmp_path):
    path = tmp_path / "unknown_submission_holds.json"
    ticker = "KXTEST-25DEC31"
    store = LiveSubmissionHoldStore(path)

    assert not store.release(ticker)
    assert not LiveSubmissionHoldStore(path).can_submit(ticker)


def test_corrupt_hold_state_fails_closed(tmp_path):
    path = tmp_path / "unknown_submission_holds.json"
    path.write_text("{not-json", encoding="utf-8")

    store = LiveSubmissionHoldStore(path)

    assert not store.can_submit("KXTEST-25DEC31")


def test_hold_write_failure_fails_closed(tmp_path):
    blocked_parent = tmp_path / "not-a-directory"
    blocked_parent.write_text("block child state path", encoding="utf-8")
    store = LiveSubmissionHoldStore(blocked_parent / "unknown_submission_holds.json")

    assert not store.hold("KXTEST-25DEC31")
    assert not store.can_submit("KXTEST-25DEC31")
    assert not store.can_submit("KXOTHER-25DEC31")


def test_replace_failure_blocks_fresh_store_with_temp_marker(tmp_path, monkeypatch):
    path = tmp_path / "unknown_submission_holds.json"
    store = LiveSubmissionHoldStore(path)

    def fail_replace(*_args):
        raise OSError("replace unavailable")

    monkeypatch.setattr(hold_module.os, "replace", fail_replace)

    assert not store.hold("KXTEST-25DEC31")
    assert path.with_suffix(".json.tmp").exists()

    fresh_store = LiveSubmissionHoldStore(path)
    assert not fresh_store.can_submit("KXTEST-25DEC31")
