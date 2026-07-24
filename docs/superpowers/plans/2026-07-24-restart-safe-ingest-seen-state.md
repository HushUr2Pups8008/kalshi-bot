# Restart-Safe Ingest Seen State Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Persist RSS and search-news deduplication IDs across process restarts so retained feed backlogs do not flood the existing stale-news gate.

**Architecture:** Add a narrow shared JSON checkpoint helper with one independent file per monitor. run_rss_monitor and run_search_news_monitor load their own bounded OrderedDict once and checkpoint it only after a full polling cycle. Separate files avoid concurrent writer races and malformed state fails open to duplicate delivery rather than false suppression.

**Tech Stack:** Python 3, collections.OrderedDict, json, pathlib.Path, os.replace, pytest.

## Global Constraints

- Persist only existing SHA-256 link-plus-title IDs; never persist URLs, titles, queries, or feed bodies.
- RSS retains at most 5,000 IDs and search retains at most 2,000 IDs.
- Use STATE_ROOT / "ingest_seen" / {rss,search}_seen_ids.json; add only that directory to .gitignore.
- Write via a same-directory temporary file plus os.replace; failed checkpoints leave the previous file intact.
- Missing, corrupt, unreadable, or schema-invalid state must return an empty cache and never raise from monitor startup.
- Do not change recency policy, _parse_date, generic-search policy, paper/live flags, sizing, order submission, or central gating.
- Use CI=1 .venv/bin/python -m pytest; create and remove the worktree .venv symlink only for test commands.

---

### Task 1: Add Bounded Seen-State Checkpoint Helper

**Files:**
- Create: feeds/seen_state.py
- Create: tests/test_seen_state.py

**Interfaces:**
- Produces: load_seen_ids(path: Path, max_seen: int) -> OrderedDict[str, None].
- Produces: checkpoint_seen_ids(path: Path, seen: OrderedDict[str, None], max_seen: int) -> None.
- Consumes: 64-character lowercase SHA-256 IDs from existing monitor _make_id functions.

- [ ] **Step 1: Write failing round-trip and cap tests**

~~~python
from collections import OrderedDict

from feeds.seen_state import checkpoint_seen_ids, load_seen_ids


def test_checkpoint_round_trip_retains_newest_ids_within_cap(tmp_path):
    path = tmp_path / "rss_seen_ids.json"
    seen = OrderedDict((value, None) for value in ("a" * 64, "b" * 64, "c" * 64))

    checkpoint_seen_ids(path, seen, max_seen=2)

    assert list(load_seen_ids(path, max_seen=2)) == ["b" * 64, "c" * 64]
~~~

- [ ] **Step 2: Run the test to verify RED**

Run: CI=1 .venv/bin/python -m pytest -q tests/test_seen_state.py::test_checkpoint_round_trip_retains_newest_ids_within_cap

Expected: FAIL because feeds.seen_state does not exist.

- [ ] **Step 3: Add corruption and atomic-failure tests**

~~~python
def test_corrupt_checkpoint_fails_open(tmp_path):
    path = tmp_path / "rss_seen_ids.json"
    path.write_text("not-json", encoding="utf-8")

    assert list(load_seen_ids(path, max_seen=5_000)) == []


def test_checkpoint_replace_failure_preserves_previous_file(tmp_path, monkeypatch):
    path = tmp_path / "rss_seen_ids.json"
    path.write_text('{"version":1,"ids":["' + "a" * 64 + '"]}', encoding="utf-8")
    monkeypatch.setattr(
        "feeds.seen_state.os.replace",
        lambda *_: (_ for _ in ()).throw(OSError("no")),
    )

    with pytest.raises(OSError):
        checkpoint_seen_ids(path, OrderedDict((("b" * 64, None),)), max_seen=5_000)

    assert "a" * 64 in path.read_text(encoding="utf-8")
~~~

- [ ] **Step 4: Implement minimal helper**

~~~python
def load_seen_ids(path: Path, max_seen: int) -> OrderedDict[str, None]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        ids = payload["ids"] if payload.get("version") == 1 else []
    except (OSError, ValueError, TypeError, AttributeError):
        return OrderedDict()
    return _bounded_valid_ids(ids, max_seen)


def checkpoint_seen_ids(path: Path, seen: OrderedDict[str, None], max_seen: int) -> None:
    bounded = _bounded_valid_ids(seen.keys(), max_seen)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps({"version": 1, "ids": list(bounded)}), encoding="utf-8")
    try:
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise
~~~

_bounded_valid_ids must retain only IDs matching re.fullmatch(r"[0-9a-f]{64}", value) and keep the newest max_seen insertion-order values.

- [ ] **Step 5: Run helper tests to verify GREEN**

Run: CI=1 .venv/bin/python -m pytest -q tests/test_seen_state.py

Expected: PASS.

- [ ] **Step 6: Commit Task 1**

~~~bash
git add feeds/seen_state.py tests/test_seen_state.py
git commit -m "feat: persist bounded ingest seen IDs"
~~~

### Task 2: Persist RSS Seen IDs After Each Completed Cycle

**Files:**
- Modify: feeds/rss_monitor.py lines 9-13 and 151-171.
- Modify: tests/test_rss_monitor.py.

**Interfaces:**
- Consumes: load_seen_ids and checkpoint_seen_ids from feeds.seen_state.
- Produces: run_rss_monitor(callback, feeds=None, poll_interval=RSS_POLL_INTERVAL_SECONDS, seen_state_path=RSS_SEEN_STATE_PATH) -> None.
- Preserves: poll_feed(url, callback, seen) and MAX_SEEN == 5_000.

- [ ] **Step 1: Write a failing two-lifetime RSS test**

~~~python
import pytest
from types import SimpleNamespace


def _parsed(*entries):
    return SimpleNamespace(feed=SimpleNamespace(title="Example"), entries=list(entries))


@pytest.mark.asyncio
async def test_rss_monitor_restart_suppresses_retained_backlog(tmp_path, monkeypatch):
    entry = SimpleNamespace(
        link="https://example.test/a",
        title="fresh",
        summary="",
        published="2026-07-24T06:00:00Z",
    )
    monkeypatch.setattr("feeds.rss_monitor.feedparser.parse", lambda _url: _parsed(entry))

    delivered = []
    await _run_one_rss_cycle(delivered, tmp_path / "rss_seen_ids.json", monkeypatch)
    await _run_one_rss_cycle(delivered, tmp_path / "rss_seen_ids.json", monkeypatch)

    assert [item.headline for item in delivered] == ["fresh"]
~~~

Use this real lifecycle helper in both tests; it must not mock poll_feed or the new checkpoint helper.

~~~python
class StopAfterOneCycle(Exception):
    pass


async def _run_one_rss_cycle(delivered, seen_state_path, monkeypatch):
    import feeds.rss_monitor as rss

    async def callback(item):
        delivered.append(item)

    async def stop_after_cycle(_seconds):
        raise StopAfterOneCycle

    monkeypatch.setattr(rss.asyncio, "sleep", stop_after_cycle)
    with pytest.raises(StopAfterOneCycle):
        await rss.run_rss_monitor(
            callback,
            feeds=["https://example.test/feed"],
            poll_interval=1,
            seen_state_path=seen_state_path,
        )
~~~

- [ ] **Step 2: Run the test to verify RED**

Run: CI=1 .venv/bin/python -m pytest -q tests/test_rss_monitor.py::test_rss_monitor_restart_suppresses_retained_backlog

Expected: FAIL because run_rss_monitor has no persistent state path.

- [ ] **Step 3: Add a distinct-ID restart test**

~~~python
@pytest.mark.asyncio
async def test_rss_monitor_restart_delivers_distinct_item(tmp_path, monkeypatch):
    first = SimpleNamespace(
        link="https://example.test/first",
        title="first",
        summary="",
        published="2026-07-24T06:00:00Z",
    )
    second = SimpleNamespace(
        link="https://example.test/second",
        title="second",
        summary="",
        published="2026-07-24T05:00:00Z",
    )
    entries = [[first], [first, second]]
    monkeypatch.setattr(
        "feeds.rss_monitor.feedparser.parse",
        lambda _url: _parsed(*entries.pop(0)),
    )

    delivered = []
    await _run_one_rss_cycle(delivered, tmp_path / "rss_seen_ids.json", monkeypatch)
    await _run_one_rss_cycle(delivered, tmp_path / "rss_seen_ids.json", monkeypatch)

    assert [item.headline for item in delivered] == ["first", "second"]
~~~

- [ ] **Step 4: Implement RSS lifecycle wiring**

~~~python
RSS_SEEN_STATE_PATH = STATE_ROOT / "ingest_seen" / "rss_seen_ids.json"


async def run_rss_monitor(
    callback: Callable[[NewsItem], Awaitable[None]],
    feeds: list[str] | None = None,
    poll_interval: int = RSS_POLL_INTERVAL_SECONDS,
    seen_state_path: Path = RSS_SEEN_STATE_PATH,
) -> None:
    seen = load_seen_ids(seen_state_path, MAX_SEEN)
    while True:
        await asyncio.gather(*(poll_feed(url, callback, seen) for url in feeds), return_exceptions=True)
        checkpoint_seen_ids(seen_state_path, seen, MAX_SEEN)
        await asyncio.sleep(poll_interval)
~~~

Let a checkpoint OSError propagate to the monitor's existing task supervisor rather than silently reporting a successful durable checkpoint. The prior in-memory data remains valid for the current process.

- [ ] **Step 5: Run RSS tests to verify GREEN**

Run: CI=1 .venv/bin/python -m pytest -q tests/test_rss_monitor.py tests/test_seen_state.py

Expected: PASS.

- [ ] **Step 6: Commit Task 2**

~~~bash
git add feeds/rss_monitor.py tests/test_rss_monitor.py
git commit -m "fix: retain rss seen IDs across restarts"
~~~

### Task 3: Persist Search-News Seen IDs Without Sharing RSS State

**Files:**
- Modify: feeds/search_news_monitor.py lines 319-431.
- Modify: tests/test_search_news_monitor.py.
- Modify: .gitignore.

**Interfaces:**
- Consumes: load_seen_ids and checkpoint_seen_ids from feeds.seen_state.
- Produces: run_search_news_monitor(callback, get_markets, poll_interval=SEARCH_POLL_INTERVAL, queue_depth_fn=None, get_series_metadata=None, seen_state_path=SEARCH_SEEN_STATE_PATH) -> None.
- Preserves: SEARCH_MAX_SEEN == 2_000, existing query generation, article caps, and callback ordering.

- [ ] **Step 1: Write a failing search-path isolation test**

~~~python
def test_search_seen_state_path_is_distinct_from_rss_path():
    from feeds.rss_monitor import RSS_SEEN_STATE_PATH
    from feeds.search_news_monitor import SEARCH_SEEN_STATE_PATH

    assert SEARCH_SEEN_STATE_PATH != RSS_SEEN_STATE_PATH
    assert SEARCH_SEEN_STATE_PATH.name == "search_seen_ids.json"
~~~

- [ ] **Step 2: Run the test to verify RED**

Run: CI=1 .venv/bin/python -m pytest -q tests/test_search_news_monitor.py::test_search_seen_state_path_is_distinct_from_rss_path

Expected: FAIL because the search monitor has no checkpoint path.

- [ ] **Step 3: Implement search lifecycle wiring and ignore rule**

~~~python
SEARCH_SEEN_STATE_PATH = STATE_ROOT / "ingest_seen" / "search_seen_ids.json"


async def run_search_news_monitor(
    callback: Callable[[NewsItem], Awaitable[None]],
    get_markets: Callable[[], Sequence[KalshiMarket]],
    poll_interval: int = SEARCH_POLL_INTERVAL,
    queue_depth_fn: Callable[[], float] | None = None,
    get_series_metadata: Callable[[], dict[str, KalshiSeriesMetadata]] | None = None,
    seen_state_path: Path = SEARCH_SEEN_STATE_PATH,
) -> None:
    seen = load_seen_ids(seen_state_path, SEARCH_MAX_SEEN)
    while True:
        # Existing query and engine loop remains unchanged.
        checkpoint_seen_ids(seen_state_path, seen, SEARCH_MAX_SEEN)
        await asyncio.sleep(poll_interval)
~~~

Add logs/state/ingest_seen/ to .gitignore so normal runtime checkpoints do not pollute source commits.

- [ ] **Step 4: Run search and regression tests to verify GREEN**

Run: CI=1 .venv/bin/python -m pytest -q tests/test_search_news_monitor.py tests/test_rss_monitor.py tests/test_stale_news_parity.py tests/test_seen_state.py

Expected: PASS.

- [ ] **Step 5: Commit Task 3**

~~~bash
git add feeds/search_news_monitor.py tests/test_search_news_monitor.py .gitignore
git commit -m "fix: retain search news seen IDs across restarts"
~~~

### Task 4: Whole-Slice Verification and Runtime Readiness

**Files:**
- Modify: docs/superpowers/specs/2026-07-24-restart-safe-ingest-seen-state-design.md only if verification reveals a design inconsistency.

- [ ] **Step 1: Run focused lint and test suite**

Run:

~~~bash
.venv/bin/python -m ruff check feeds/seen_state.py feeds/rss_monitor.py feeds/search_news_monitor.py tests/test_seen_state.py tests/test_rss_monitor.py tests/test_search_news_monitor.py
git diff --check origin/main...HEAD
CI=1 .venv/bin/python -m pytest -q tests/test_seen_state.py tests/test_rss_monitor.py tests/test_search_news_monitor.py tests/test_stale_news_parity.py
~~~

Expected: all commands exit 0.

- [ ] **Step 2: Obtain independent adversarial review**

Review must check checkpoint corruption behavior, atomic replacement failure, independent RSS/search paths, cycle-boundary durability, state-file privacy, and accidental changes to freshness or trading paths.

- [ ] **Step 3: Publish and activate only after review**

Open a PR, require required CI, verify any replay-gate failure before using the previously authorized empty-corpus override, merge, fast-forward the root checkout without touching user runtime artifacts, restart with zsh -ic restartbot, and observe one restart boundary. Acceptance is no replayed stale burst from retained IDs, fresh passes still possible, and zero paper/live order events.
