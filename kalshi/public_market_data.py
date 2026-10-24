"""Kalshi public identifier grammar.

The credential-free market reader is gone. ``is_safe_kalshi_identifier``
stays because the REST client and the paper trader still reject a ticker
before they interpolate it into a path.
"""

from __future__ import annotations

import re

_IDENTIFIER = re.compile(r"[A-Z0-9][A-Z0-9._-]*")


def is_safe_kalshi_identifier(value: object) -> bool:
    """Return whether value matches the public API's identifier grammar."""
    return isinstance(value, str) and _IDENTIFIER.fullmatch(value) is not None
