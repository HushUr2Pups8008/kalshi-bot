"""Hard boundary between local paper delivery and external realized profit."""

from __future__ import annotations

from pathlib import Path


def independent_realized_profit_evidence_available(*, db_path: Path | str) -> bool:
    """Return whether an external execution/cash-settlement ledger is integrated.

    The local paper database and its settlement outbox only prove local delivery
    effects. They cannot verify venue execution or real-money settlement. No
    external ledger is bound into this runtime yet, so callers must fail closed.
    """

    del db_path
    return False
