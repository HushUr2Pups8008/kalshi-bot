import logging
import os
import shutil
import time
import uuid
from pathlib import Path

from utils.logger import _DailyRotatingFileHandler, _first_log_timestamp, _maybe_rotate_stale, _utc_formatter


def _tmp_root() -> Path:
    root = Path(__file__).resolve().parent / "_tmp_logger_rotation" / uuid.uuid4().hex
    root.mkdir(parents=True, exist_ok=False)
    return root


def _make_logger(name: str, handler: logging.Handler) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.handlers = []
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.addHandler(handler)
    return logger


def test_manual_rollover_preserves_archive_and_continues_logging():
    root = _tmp_root()
    active_path = root / "bot.log"

    try:
        active_handler = _DailyRotatingFileHandler(
            active_path,
            when="midnight",
            backupCount=7,
            encoding="utf-8",
            utc=True,
        )
        active_handler.setFormatter(_utc_formatter("%(asctime)s UTC %(levelname)-8s %(name)-20s %(message)s"))

        # Separate handler instance simulates an external manual-rotation trigger
        # while the original writer keeps its file handle open.
        rotator_handler = _DailyRotatingFileHandler(
            active_path,
            when="midnight",
            backupCount=7,
            encoding="utf-8",
            utc=True,
        )
        rotator_handler.setFormatter(_utc_formatter("%(asctime)s UTC %(levelname)-8s %(name)-20s %(message)s"))

        logger = _make_logger("test.logger.rotation", active_handler)
        try:
            logger.info("before rotation")
            rotator_handler.force_rollover()
            logger.info("after rotation")
        finally:
            active_handler.close()
            rotator_handler.close()
            logger.handlers = []

        archives = sorted(path for path in root.glob("bot.log.*") if path.is_file())
        assert archives, "expected an archived bot.log file after manual rollover"

        archived_text = archives[-1].read_text(encoding="utf-8")
        active_text = active_path.read_text(encoding="utf-8")

        assert "before rotation" in archived_text
        assert "after rotation" in active_text
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_force_rollover_rotates_peer_handler_too():
    root = _tmp_root()
    bot_path = root / "bot.log"
    err_path = root / "errors.log"
    try:
        bot_handler = _DailyRotatingFileHandler(
            bot_path,
            when="midnight",
            backupCount=7,
            encoding="utf-8",
            utc=True,
        )
        err_handler = _DailyRotatingFileHandler(
            err_path,
            when="midnight",
            backupCount=7,
            encoding="utf-8",
            utc=True,
        )
        bot_handler._peers.append(err_handler)

        bot_logger = _make_logger("test.logger.rotation.bot", bot_handler)
        err_logger = _make_logger("test.logger.rotation.err", err_handler)

        bot_logger.info("bot before rotation")
        err_logger.error("err before rotation")

        bot_handler.force_rollover()

        bot_logger.info("bot after rotation")
        err_logger.error("err after rotation")

        assert list(root.glob("bot.log.*"))
        assert list(root.glob("errors.log.*"))
        assert "bot after rotation" in bot_path.read_text(encoding="utf-8")
        assert "err after rotation" in err_path.read_text(encoding="utf-8")
    finally:
        bot_handler.close()
        err_handler.close()
        bot_logger.handlers = []
        err_logger.handlers = []
        shutil.rmtree(root, ignore_errors=True)


def test_startup_rotation_archives_stale_log_content():
    """_maybe_rotate_stale archives a log file whose mtime is before the current period.

    Reproduces the macOS dev-machine scenario: the bot ran yesterday (or earlier),
    was stopped before midnight, and is now being restarted.  The active bot.log
    has content from a previous period and must be archived before new content is
    written so that the new session starts with a clean log.
    """
    root = _tmp_root()
    bot_path = root / "bot.log"
    try:
        handler = _DailyRotatingFileHandler(
            bot_path,
            when="midnight",
            backupCount=7,
            encoding="utf-8",
            utc=True,
        )
        handler.setFormatter(_utc_formatter("%(asctime)s UTC %(levelname)-8s %(name)-20s %(message)s"))
        logger = _make_logger("test.startup.rotation", handler)

        try:
            logger.info("stale content from a previous session")
            handler.flush()

            # Back-date the file's mtime to two days before the current period start
            # so _maybe_rotate_stale identifies it as belonging to an older period.
            period_start = handler.rolloverAt - handler.interval
            stale_mtime = period_start - 86_400  # one full day before period start
            os.utime(bot_path, (stale_mtime, stale_mtime))

            _maybe_rotate_stale(handler)
        finally:
            handler.close()
            logger.handlers = []

        archives = list(root.glob("bot.log.*"))
        assert archives, "expected an archived bot.log after _maybe_rotate_stale()"

        archived_text = archives[0].read_text(encoding="utf-8")
        assert "stale content from a previous session" in archived_text

        # Active file must be empty (truncated) after stale rotation.
        assert not bot_path.exists() or bot_path.stat().st_size == 0
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_rollover_at_advances_after_archive_already_exists():
    """rolloverAt is updated even when Python 3.14 doRollover() returns early.

    Reproduces the hotloop: --rotate-logs is called during the day, creating
    bot.log.YYYY-MM-DD.  When midnight arrives, doRollover() finds the archive
    already exists and returns early *without* updating rolloverAt.
    shouldRollover() then returns True on every subsequent emit(), and every
    emit() triggers a no-op doRollover() cycle forever.

    The fix in _rollover_self() must advance rolloverAt even in that case.
    """
    root = _tmp_root()
    bot_path = root / "bot.log"
    try:
        handler = _DailyRotatingFileHandler(
            bot_path,
            when="midnight",
            backupCount=7,
            encoding="utf-8",
            utc=True,
        )
        logger = _make_logger("test.rollover.advance", handler)

        try:
            logger.info("before first rollover")
            # First force_rollover: simulates --rotate-logs run during the day.
            # Creates the archive for the current period.
            handler.force_rollover()

            # Manually wind rolloverAt back to simulate midnight arriving with the
            # archive already present (the exact Python 3.14 trigger condition).
            handler.rolloverAt = int(time.time()) - 1  # in the past

            # Call _rollover_self() directly — this is what doRollover() calls when
            # the midnight trigger fires.  The archive exists, so Python 3.14's
            # super().doRollover() returns early without updating rolloverAt.
            # Our fix must advance it.
            handler._rollover_self()

            # rolloverAt must now be in the future.
            assert handler.rolloverAt > int(time.time()), (
                "rolloverAt was not advanced after early-return doRollover(); "
                "shouldRollover() would return True forever (hotloop)"
            )
        finally:
            handler.close()
            logger.handlers = []
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_forced_rollover_cycle_no_hotloop():
    """Multiple consecutive force_rollover() calls must not leave rolloverAt in the past.

    Ensures that calling force_rollover() more than once (e.g. manual --rotate-logs
    invocations, or test teardown) does not create the hotloop condition.
    """
    root = _tmp_root()
    bot_path = root / "bot.log"
    try:
        handler = _DailyRotatingFileHandler(
            bot_path,
            when="midnight",
            backupCount=7,
            encoding="utf-8",
            utc=True,
        )
        logger = _make_logger("test.multi.force", handler)
        try:
            logger.info("initial content")

            for _ in range(3):
                handler.force_rollover()
                logger.info("content after a force_rollover")
                # After every force_rollover rolloverAt must remain in the future.
                assert handler.rolloverAt > int(time.time()), (
                    "rolloverAt fell behind current time after force_rollover()"
                )
        finally:
            handler.close()
            logger.handlers = []
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_maybe_rotate_stale_does_not_rotate_at_exact_period_boundary():
    """MAC-TEST-004: mtime == period_start must NOT trigger rotation.

    The implementation uses strict `<` so a file written exactly at the
    period boundary belongs to the current period and must be kept as-is.
    On macOS APFS with nanosecond precision, a file written during a near-
    midnight startup could land exactly on the boundary epoch second.
    """
    root = _tmp_root()
    bot_path = root / "bot.log"
    try:
        handler = _DailyRotatingFileHandler(
            bot_path,
            when="midnight",
            backupCount=7,
            encoding="utf-8",
            utc=True,
        )
        handler.setFormatter(_utc_formatter("%(asctime)s UTC %(levelname)-8s %(name)-20s %(message)s"))
        logger = _make_logger("test.boundary.exact", handler)
        try:
            logger.info("content at exact boundary")
            handler.flush()

            period_start = handler.rolloverAt - handler.interval
            # mtime exactly at period_start — belongs to current period, must not rotate
            os.utime(bot_path, (period_start, period_start))

            _maybe_rotate_stale(handler)
        finally:
            handler.close()
            logger.handlers = []

        archives = list(root.glob("bot.log.*"))
        assert not archives, (
            "mtime == period_start must NOT trigger rotation; "
            "file belongs to the current period"
        )
        assert bot_path.exists() and bot_path.stat().st_size > 0
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_maybe_rotate_stale_rotates_when_mtime_is_one_second_before_period_boundary():
    """MAC-TEST-004: mtime == period_start - 1 MUST trigger rotation.

    A file written one second before the period boundary belongs to the
    previous period and must be archived.  This is the complement of the
    exact-boundary test above and pins the strict `<` comparison.
    """
    root = _tmp_root()
    bot_path = root / "bot.log"
    try:
        handler = _DailyRotatingFileHandler(
            bot_path,
            when="midnight",
            backupCount=7,
            encoding="utf-8",
            utc=True,
        )
        handler.setFormatter(_utc_formatter("%(asctime)s UTC %(levelname)-8s %(name)-20s %(message)s"))
        logger = _make_logger("test.boundary.minus1", handler)
        try:
            logger.info("content one second before boundary")
            handler.flush()

            period_start = handler.rolloverAt - handler.interval
            # mtime one second before period_start — belongs to previous period, must rotate
            os.utime(bot_path, (period_start - 1, period_start - 1))

            _maybe_rotate_stale(handler)
        finally:
            handler.close()
            logger.handlers = []

        archives = list(root.glob("bot.log.*"))
        assert archives, (
            "mtime == period_start - 1 MUST trigger rotation; "
            "file belongs to the previous period"
        )
        archived_text = archives[0].read_text(encoding="utf-8")
        assert "content one second before boundary" in archived_text
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_first_log_timestamp_parses_correctly():
    """_first_log_timestamp returns the UTC epoch of the first non-comment log line."""
    root = _tmp_root()
    p = root / "bot.log"
    try:
        # Banner + normal log line; function must skip banner and parse the log line
        p.write_text(
            "# ===== v1.0 | env=demo =====\n"
            "2026-04-19 23:59:00,123 UTC INFO     main                 test message\n",
            encoding="utf-8",
        )
        ts = _first_log_timestamp(p)
        assert ts is not None
        from datetime import datetime, timezone
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        assert dt.year == 2026 and dt.month == 4 and dt.day == 19
        assert dt.hour == 23 and dt.minute == 59
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_rotation_guard_scenario_force_rollover_resolves_missed_rotation():
    """Rotation guard scenario: shouldRollover() True → force_rollover() fixes it.

    Simulates the state where emit-triggered rotation missed (rolloverAt is in the
    past) and the midnight guard task fires.  Calling force_rollover() must:
      - create the archive
      - leave bot.log empty
      - advance rolloverAt so shouldRollover() returns False afterward
    """
    root = _tmp_root()
    bot_path = root / "bot.log"
    try:
        handler = _DailyRotatingFileHandler(
            bot_path,
            when="midnight",
            backupCount=7,
            encoding="utf-8",
            utc=True,
        )
        logger = _make_logger("test.guard.scenario", handler)
        try:
            logger.info("content that should have been archived at midnight")
            handler.flush()

            # Wind rolloverAt to the past, simulating a missed midnight rotation
            handler.rolloverAt = int(time.time()) - 1
            assert handler.shouldRollover(None), "precondition: shouldRollover must be True before guard fires"

            # Guard task fires and calls rotate_logs() equivalent
            handler.force_rollover()

            # After forced rotation, shouldRollover must be False
            assert not handler.shouldRollover(None), (
                "shouldRollover must return False after forced rotation; "
                "rolloverAt was not advanced"
            )
            # Archive must exist
            archives = list(root.glob("bot.log.*"))
            assert archives, "force_rollover must create an archive"
        finally:
            handler.close()
            logger.handlers = []
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_startup_rotation_uses_first_log_timestamp_when_mtime_is_current():
    """Rotate prior-period content even if the active file's mtime is current.

    This covers the macOS long-run/restart pattern where a log contains content
    from the previous UTC day, but later writes made the file mtime look fresh.
    """
    root = _tmp_root()
    bot_path = root / "bot.log"
    try:
        handler = _DailyRotatingFileHandler(
            bot_path,
            when="midnight",
            backupCount=7,
            encoding="utf-8",
            utc=True,
        )
        try:
            period_start = handler.rolloverAt - handler.interval
            stale = time.strftime(
                "%Y-%m-%d %H:%M:%S,000",
                time.gmtime(period_start - 60),
            )
            bot_path.write_text(
                "# banner line is ignored\n"
                f"{stale} UTC INFO     main                 prior-period content\n",
                encoding="utf-8",
            )
            os.utime(bot_path, (period_start + 60, period_start + 60))

            _maybe_rotate_stale(handler)
        finally:
            handler.close()

        archives = list(root.glob("bot.log.*"))
        assert archives, "first log timestamp from prior period must trigger rotation"
        assert "prior-period content" in archives[0].read_text(encoding="utf-8")
        assert not bot_path.exists() or bot_path.stat().st_size == 0
    finally:
        shutil.rmtree(root, ignore_errors=True)
