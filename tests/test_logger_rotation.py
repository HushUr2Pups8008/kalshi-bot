import logging
import shutil
import uuid
from pathlib import Path

from utils.logger import _DailyRotatingFileHandler, _utc_formatter


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
