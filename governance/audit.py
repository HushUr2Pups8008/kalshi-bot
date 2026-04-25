"""Append-only JSONL audit logger with daily rotation.

Used by the governance agent (Phase 2+) to record every decision +
governance event. Phase 1 builds and tests this standalone -- the agent
that consumes it lands in Phase 2.

Rotation pattern matches utils/logger.py's bot.log convention:
  decisions.jsonl                     (current day)
  decisions.jsonl.YYYY-MM-DD          (archived days)
  decisions.jsonl.YYYY-MM-DD.gz       (after compress_after_days)
"""

from __future__ import annotations

import gzip
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


def _default_now() -> datetime:
    return datetime.now(timezone.utc)


class AuditLogger:
    """Append-only JSONL writer with UTC-midnight daily rotation.

    The writer keeps a tiny amount of state: the date of the last write.
    On each append, if the current UTC date differs from last-write date,
    rotate the existing file to `<basename>.YYYY-MM-DD` (the previous
    day) and start writing fresh.

    Compression (calling compress_old_archives()) is a separate idempotent
    step that the agent invokes once per cycle. It walks the log dir,
    gzips any archive older than `compress_after_days`. Safe to call
    repeatedly.
    """

    def __init__(
        self,
        *,
        log_dir: Path,
        basename: str = "decisions.jsonl",
        now: Callable[[], datetime] = _default_now,
        compress_after_days: int = 7,
    ) -> None:
        self._log_dir = log_dir
        self._basename = basename
        self._now = now
        self._compress_after_days = compress_after_days
        self._last_write_date: str | None = None  # YYYY-MM-DD UTC
        self._log_dir.mkdir(parents=True, exist_ok=True)

    def append(self, record: dict[str, Any]) -> None:
        """Append a single record as one JSON line.

        Rotates atomically if the day changed since the last write. The
        rotation is best-effort; if the rename fails, we log the failure
        and continue writing to the current file (don't fail to record
        a decision because rotation hit a race).

        Raises TypeError if the record contains non-JSON-serializable values.
        """
        now = self._now()
        today = now.strftime("%Y-%m-%d")
        target = self._log_dir / self._basename

        if (
            self._last_write_date is not None
            and self._last_write_date != today
            and target.exists()
        ):
            # Rotate: rename current file to <basename>.<previous_date>
            archive_path = self._log_dir / f"{self._basename}.{self._last_write_date}"
            try:
                target.rename(archive_path)
            except OSError:
                # Rotation lost a race or hit a permission error. Log and
                # continue -- recording the decision is more important than
                # rotation hygiene.
                pass

        # Let json.dumps raise TypeError naturally for non-serializable values.
        line = json.dumps(record, ensure_ascii=False) + "\n"
        with target.open("a", encoding="utf-8") as f:
            f.write(line)
        self._last_write_date = today

    def compress_old_archives(self) -> None:
        """Gzip any archive older than compress_after_days.

        Idempotent: already-compressed archives are skipped. Walks
        log_dir looking for files matching `<basename>.YYYY-MM-DD`.
        """
        now = self._now()
        for entry in self._log_dir.iterdir():
            if not entry.is_file():
                continue
            if not entry.name.startswith(f"{self._basename}."):
                continue
            if entry.name.endswith(".gz"):
                continue
            date_part = entry.name[len(self._basename) + 1 :]  # +1 for the dot
            try:
                archive_date = datetime.strptime(date_part, "%Y-%m-%d").replace(
                    tzinfo=timezone.utc
                )
            except ValueError:
                continue
            age_days = (now - archive_date).days
            if age_days < self._compress_after_days:
                continue
            compressed = entry.with_suffix(entry.suffix + ".gz")
            with entry.open("rb") as src, gzip.open(compressed, "wb") as dst:
                shutil.copyfileobj(src, dst)
            entry.unlink()
