"""The live identifier check stays. The unused public reader is gone."""

from __future__ import annotations

import ast
from pathlib import Path

from kalshi.public_market_data import is_safe_kalshi_identifier


def test_module_has_no_private_or_trading_imports() -> None:
    path = Path(__file__).parents[1] / "kalshi" / "public_market_data.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imports |= {
        node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    }
    forbidden = (
        "config",
        "rest_client",
        "credential",
        "sign",
        "trading",
        "executor",
        "account",
        "order",
        "balance",
        "position",
    )
    assert not [name for name in imports if any(token in name.lower() for token in forbidden)]


def test_identifier_rejects_path_traversal_and_accepts_a_series_ticker() -> None:
    """rest_client and paper_trader interpolate this into a path. A slash must not pass."""
    assert is_safe_kalshi_identifier("KXHIGHNY") is True
    assert is_safe_kalshi_identifier("KXHIGHNY-26OCT23-T60") is True
    assert is_safe_kalshi_identifier("../orders") is False
    assert is_safe_kalshi_identifier("") is False
    assert is_safe_kalshi_identifier(None) is False
    assert is_safe_kalshi_identifier("kxhighny") is False
