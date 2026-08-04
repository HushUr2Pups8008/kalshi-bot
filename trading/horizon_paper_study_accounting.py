"""Pure fee-schedule evaluation for the isolated horizon paper study."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path

from trading.horizon_paper_study_manifest import (
    HORIZON_PAPER_STUDY_MANIFEST_FILENAME,
    HorizonPaperStudyManifest,
)


class HorizonStudyAccountingError(ValueError):
    """Raised when study-local accounting inputs are structurally unsafe."""


class HorizonStudyAccounting:
    """Pure pinned-schedule evaluator for study-local settlements."""

    def __init__(self, manifest_path: Path | str) -> None:
        self.manifest_path = Path(manifest_path).expanduser().resolve(strict=False)
        if self.manifest_path.name != HORIZON_PAPER_STUDY_MANIFEST_FILENAME:
            raise HorizonStudyAccountingError("study manifest path is invalid")
        payload = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        self.manifest = HorizonPaperStudyManifest.from_dict(payload)
        self.study_root = self.manifest_path.parent
        self.fee_schedule_path = self.study_root / "fee_schedule.json"

    def evaluate_settlement(self, trade: dict[str, object]) -> dict[str, object]:
        schedule = self._read_schedule()
        base = {
            "fee_schedule_sha256": self.manifest.fee_schedule_sha256,
            "entry_fee_provenance": "unscorable",
            "settlement_fee_provenance": "unscorable",
            "modeled_fee_net_pnl_cents": None,
        }
        if schedule is None:
            return {"accounting_state": "unscorable", **base}
        try:
            executed_at = _parse_utc(trade["executed_at_utc"])
            observed_at = _parse_utc(trade["observed_at_utc"])
            effective_from = _parse_utc(schedule["effective_from_utc"])
            effective_to = _parse_utc(schedule["effective_to_utc"])
            entry_fee = _evaluate_fee(schedule.get("entry_fee_function"))
            settlement_fee = _evaluate_fee(schedule.get("settlement_fee_function"))
            if (
                executed_at < effective_from
                or executed_at > effective_to
                or observed_at < effective_from
                or observed_at > effective_to
                or entry_fee is None
                or settlement_fee is None
            ):
                return {"accounting_state": "unscorable", **base}
            gross_pnl = _require_int(trade["gross_pnl_cents"], "gross_pnl_cents")
        except (KeyError, TypeError, ValueError):
            return {"accounting_state": "unscorable", **base}
        return {
            "accounting_state": "modeled_pinned_schedule",
            "fee_schedule_sha256": self.manifest.fee_schedule_sha256,
            "entry_fee_provenance": "modeled_pinned_schedule",
            "settlement_fee_provenance": "modeled_pinned_schedule",
            "modeled_fee_net_pnl_cents": gross_pnl - entry_fee - settlement_fee,
        }

    def _read_schedule(self) -> dict[str, object] | None:
        if not self.fee_schedule_path.exists():
            return None
        try:
            text = self.fee_schedule_path.read_text(encoding="utf-8")
            if not text:
                return None
            payload = json.loads(text)
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        return payload


def _parse_utc(value: object) -> datetime:
    if not isinstance(value, str):
        raise HorizonStudyAccountingError("utc datetime is invalid")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _evaluate_fee(value: object) -> int | None:
    if not isinstance(value, dict):
        return None
    if value.get("kind") != "fixed_cents":
        return None
    amount = value.get("amount")
    return _require_int(amount, "fixed fee amount")


def _require_int(value: object, label: str) -> int:
    if type(value) is not int:
        raise HorizonStudyAccountingError(f"{label} is invalid")
    return value
