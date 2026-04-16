from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


_GLOB_CHARS = "*?[]"


def _parse_iso_like_ts(value: str) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    if text.endswith(" UTC"):
        text = text[:-4]
    for fmt in ("%Y-%m-%d %H:%M:%S,%f", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(text, fmt)
        except ValueError:
            continue
        return dt.replace(tzinfo=timezone.utc)
    return None


def _parse_app_log_line(line: str) -> dict[str, Any]:
    raw = line.rstrip("\r\n")
    record = {
        "ts": None,
        "level": None,
        "task": None,
        "message": raw,
        "raw": raw,
    }
    if not raw:
        return record

    if len(raw) >= 24 and raw[4] == "-" and raw[7] == "-" and raw[10] == " ":
        ts = _parse_iso_like_ts(raw[:23] + " UTC")
        if ts is not None:
            remainder = raw[24:].lstrip()
            if remainder.startswith("UTC "):
                remainder = remainder[4:]

            parts = remainder.split(None, 2)
            if len(parts) >= 2 and parts[0].isalpha():
                record["ts"] = ts
                record["level"] = parts[0].upper()
                record["task"] = parts[1].rstrip(":") or None
                record["message"] = parts[2] if len(parts) == 3 else ""
                return record

    ts_token, sep, remainder = raw.partition(" ")
    if sep:
        second_token, sep2, tail = remainder.partition(" ")
        ts = _parse_iso_like_ts(f"{ts_token} {second_token} UTC")
        if ts is not None:
            level_token, sep3, message = tail.partition(":")
            level = level_token.strip().upper()
            if sep3 and level.isalpha():
                message = message.lstrip()
                task = None
                if message.startswith("[") and "]" in message:
                    task, _, message = message[1:].partition("]")
                    task = task.strip() or None
                    message = message.lstrip()
                record["ts"] = ts
                record["level"] = level
                record["task"] = task
                record["message"] = message
                return record

    return record


def _has_glob(path: Path) -> bool:
    return any(ch in str(path) for ch in _GLOB_CHARS)


def _sort_key(path: Path) -> tuple[int, float, str]:
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    is_active = 1 if path.name == "bot.log" else 0
    return (is_active, mtime, path.name.lower())


def _resolve_app_log_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]

    if path.is_dir():
        files = [candidate for candidate in path.glob("bot.log*") if candidate.is_file()]
        return sorted(files, key=_sort_key)

    if _has_glob(path):
        parent = path.parent if str(path.parent) not in {"", "."} else Path(".")
        files = [candidate for candidate in parent.glob(path.name) if candidate.is_file()]
        return sorted(files, key=_sort_key)

    return []


def iter_app_log_records(
    path: Path,
    since: datetime | None = None,
    until: datetime | None = None,
    levels: set[str] | None = None,
) -> Iterator[dict[str, Any]]:
    normalized_levels = {level.strip().upper() for level in (levels or set()) if level.strip()}

    for file_path in _resolve_app_log_files(path):
        try:
            with file_path.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    record = _parse_app_log_line(line)
                    ts = record["ts"]
                    level = record["level"]

                    if ts is not None:
                        if since is not None and ts < since:
                            continue
                        if until is not None and ts > until:
                            continue
                    elif since is not None or until is not None:
                        pass

                    if normalized_levels and level is not None and level.upper() not in normalized_levels:
                        continue

                    yield record
        except OSError:
            continue
