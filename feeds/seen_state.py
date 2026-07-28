"""Durable, bounded checkpoints for ingest seen-ID state."""

from __future__ import annotations

import json
import logging
import os
import re
from collections import OrderedDict
from collections.abc import Iterable
from pathlib import Path


_SCHEMA_VERSION = 1
_SHA256_ID = re.compile(r"[0-9a-f]{64}")
logger = logging.getLogger(__name__)


def _bounded_valid_ids(ids: Iterable[object], max_seen: int) -> OrderedDict[str, None]:
    """Keep valid IDs in insertion order, dropping the oldest over the cap."""
    valid: OrderedDict[str, None] = OrderedDict()
    if max_seen <= 0:
        return valid

    for value in ids:
        if isinstance(value, str) and _SHA256_ID.fullmatch(value):
            valid[value] = None
            valid.move_to_end(value)
            if len(valid) > max_seen:
                valid.popitem(last=False)
    return valid


def load_seen_ids(path: Path, max_seen: int) -> OrderedDict[str, None]:
    """Load a valid checkpoint, failing open when the state cannot be read."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("invalid checkpoint schema")
        version = payload.get("version")
        if type(version) is not int or version != _SCHEMA_VERSION:
            raise ValueError("invalid checkpoint schema")
        ids = payload.get("ids")
        if not isinstance(ids, list):
            raise ValueError("invalid checkpoint ids")
        return _bounded_valid_ids(ids, max_seen)
    except FileNotFoundError:
        return OrderedDict()
    except (OSError, TypeError, ValueError):
        logger.warning("Unable to load seen-ID checkpoint; starting empty.")
        return OrderedDict()


def checkpoint_seen_ids(path: Path, seen: OrderedDict[str, None], max_seen: int) -> None:
    """Atomically persist the valid, newest seen IDs at the configured cap."""
    bounded = _bounded_valid_ids(seen.keys(), max_seen)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(
        json.dumps({"version": _SCHEMA_VERSION, "ids": list(bounded)}),
        encoding="utf-8",
    )
    try:
        os.replace(temp_path, path)
    except OSError:
        temp_path.unlink(missing_ok=True)
        raise
