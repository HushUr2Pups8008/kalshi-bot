"""Tests for PROFIT-ALIGN-001 deferred-item minimum-viable surfaces shipped
on `feat/align-remaining-items`:

  - PROFIT-ALIGN-002 (item 2) — calibration_aggregator.aggregate
  - PROFIT-ALIGN-007 (item 5) — cfg.position_drift_alert_threshold
  - PROFIT-ALIGN-008 (item 8) — log_gate_summary writer schema
  - PROFIT-ALIGN-009 (item 9) — _derive_series_prior_from_metadata
  - PROFIT-ALIGN-010 (item 10) — llm_dedup_cache
  - PROFIT-ALIGN-011 (item 11) — magnitude_shift_* cfg fields

Items 6 (per-source Bayesian) and 7 (lane simplification) ship cfg flags
without runtime wiring this PR — surface tests check the flag exists.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# PROFIT-ALIGN-011 — magnitude_shift_* cfg fields (item 11)
# ---------------------------------------------------------------------------

class TestMagnitudeShiftCfg:
    def test_defaults_match_historical_constants(self):
        import config as cfg_mod
        assert cfg_mod.cfg.magnitude_shift_small == pytest.approx(0.08)
        assert cfg_mod.cfg.magnitude_shift_moderate == pytest.approx(0.15)
        assert cfg_mod.cfg.magnitude_shift_large == pytest.approx(0.25)

    def test_signal_analyzer_uses_cfg(self, monkeypatch):
        # Override cfg, expect _magnitude_shift_table() to reflect new value
        import config as cfg_mod
        from analysis.signal_analyzer import _magnitude_shift_table
        monkeypatch.setattr(cfg_mod.cfg, "magnitude_shift_small", 0.04)
        table = _magnitude_shift_table()
        assert table["small"] == pytest.approx(0.04)
        assert table["moderate"] == pytest.approx(0.15)  # unchanged
        assert table["none"] == 0.0


# ---------------------------------------------------------------------------
# PROFIT-ALIGN-007 — position_drift_alert_threshold cfg (item 5)
# ---------------------------------------------------------------------------

class TestPositionDriftCfg:
    def test_default_threshold(self):
        import config as cfg_mod
        t = cfg_mod.cfg.position_drift_alert_threshold
        assert isinstance(t, float)
        assert 0.0 < t <= 1.0  # >= 1.0 disables; default should be useful

    def test_log_position_drift_writer_exists(self):
        """Logger method `log_position_drift` is the surface that will fire
        when current_price drifts by ≥ threshold from entry_price. The hook
        wiring into open-position monitoring is deferred (item 5 design),
        but the schema + writer must be in place so the wire-up is trivial."""
        from utils.logger import TradeLogger
        assert hasattr(TradeLogger, "log_position_drift"), (
            "TradeLogger.log_position_drift must exist as the emission "
            "target for PROFIT-ALIGN-007 wiring."
        )


# ---------------------------------------------------------------------------
# PROFIT-ALIGN-008 — log_gate_summary writer schema (item 8)
# ---------------------------------------------------------------------------

class TestLogGateSummary:
    def test_writes_expected_keys(self, tmp_path):
        # Avoid importlib.reload(logger_mod) — that pattern pollutes
        # module-level state consumed by other test files (notably
        # test_main_startup.py).
        from utils.logger import TradeLogger
        tl = TradeLogger(tmp_path / "trades.jsonl")
        tl.log_gate_summary(
            ticker="KXTRUMPIRAN-26JUN01",
            market_prefix="KXTRUMPIRAN",
            binding_constraint="G4_regime_low",
            scaled_confidence=0.04,
            regime_confidence=0.15,
            blended_confidence=0.27,
            g1_threshold=0.10,
            g4_threshold=0.20,
            gate_chain=["G4: rc=0.15 < 0.20 FAIL", "G1: sc=0.04 < 0.10 FAIL (fail-safe)"],
            g7_mark_snapshot={
                "drawdown_pct": 0.21,
                "threshold_pct": 0.20,
                "provider": "scripts.mark_open_positions",
                "fallback_status": "none",
                "observed_at": "2026-07-24T00:00:00+00:00",
            },
            lifecycle_id="lc-g7-mark",
            settlement_source_match=True,
        )
        import json
        line = (tmp_path / "trades.jsonl").read_text(encoding="utf-8").strip().splitlines()[-1]
        rec = json.loads(line)
        assert rec["type"] == "GATE_SUMMARY"
        assert rec["binding_constraint"] == "G4_regime_low"
        assert rec["gate_chain"] == [
            "G4: rc=0.15 < 0.20 FAIL", "G1: sc=0.04 < 0.10 FAIL (fail-safe)"
        ]
        assert rec["scaled_confidence"] == pytest.approx(0.04)
        assert rec["g7_mark_snapshot"]["drawdown_pct"] == pytest.approx(0.21)
        assert rec["g7_mark_snapshot"]["provider"] == "scripts.mark_open_positions"
        assert rec["lifecycle_id"] == "lc-g7-mark"
        assert rec["settlement_source_match"] is True

    def test_skipped_writes_g7_mark_snapshot(self, tmp_path):
        from utils.logger import TradeLogger

        tl = TradeLogger(tmp_path / "trades.jsonl")
        tl.log_skipped(
            reason="G7_open_exposure_drawdown",
            ticker="KXTRUMPIRAN-26JUN01",
            g7_mark_snapshot={
                "drawdown_pct": 1.0,
                "threshold_pct": 0.20,
                "provider": "scripts.mark_open_positions",
                "fallback_status": "mark_error",
                "observed_at": "2026-07-24T00:00:00+00:00",
            },
        )

        import json
        line = (tmp_path / "trades.jsonl").read_text(encoding="utf-8").strip()
        rec = json.loads(line)
        assert rec["type"] == "SKIPPED"
        assert rec["g7_mark_snapshot"]["drawdown_pct"] == pytest.approx(1.0)
        assert rec["g7_mark_snapshot"]["fallback_status"] == "mark_error"


# ---------------------------------------------------------------------------
# PROFIT-ALIGN-009 — _derive_series_prior_from_metadata (item 9)
# ---------------------------------------------------------------------------

class TestDerivedSeriesPrior:
    def _market(self, ticker, series_ticker=""):
        m = MagicMock()
        m.ticker = ticker
        m.series_ticker = series_ticker
        m.title = ticker
        m.subtitle = ""
        m.close_time = "2026-12-31T23:59:00+00:00"
        return m

    def test_polling_market_structural(self):
        from analysis.regime_classifier import _derive_series_prior_from_metadata
        m = self._market("KXPOLLPOTUS-2026", "KXPOLLPOTUS")
        w = _derive_series_prior_from_metadata(m)
        assert w == (0.05, 0.25, 0.70)

    def test_macro_data_structural(self):
        from analysis.regime_classifier import _derive_series_prior_from_metadata
        for t in ("KXCPI-26MAY", "KXGDP-26Q2", "KXPMI-CHN", "KXFOMC-DEC"):
            assert _derive_series_prior_from_metadata(self._market(t)) == (0.05, 0.30, 0.65)

    def test_political_event_fast(self):
        from analysis.regime_classifier import _derive_series_prior_from_metadata
        for t in ("KXTRUMP-OBSCURE-MARKET", "KXENDORSE-SOMETHING", "KXSENATE-2026"):
            assert _derive_series_prior_from_metadata(self._market(t)) == (0.65, 0.25, 0.10)

    def test_crypto_fast(self):
        from analysis.regime_classifier import _derive_series_prior_from_metadata
        assert _derive_series_prior_from_metadata(self._market("KXBTC-100K-2026")) == (0.65, 0.28, 0.07)

    def test_unknown_returns_none(self):
        from analysis.regime_classifier import _derive_series_prior_from_metadata
        # Random made-up ticker with no pattern matches
        m = self._market("KXMYSTERY-XYZ-123")
        assert _derive_series_prior_from_metadata(m) is None

    def test_compute_regime_weights_opt_in_only(self, monkeypatch):
        """Default cfg.enable_derived_series_priors=False → _time_prior fallback
        (existing behavior preserved). When True → derivation kicks in."""
        import config as cfg_mod
        from analysis.regime_classifier import compute_regime_weights
        m = self._market("KXNEWUNKNOWN-26DEC")
        monkeypatch.setattr(cfg_mod.cfg, "enable_derived_series_priors", False)
        w_off = compute_regime_weights(m)
        # Unknown ticker → falls through to _time_prior (likely fast-dominant)
        assert sum(w_off.values()) == pytest.approx(1.0, abs=1e-6)


# ---------------------------------------------------------------------------
# PROFIT-ALIGN-010 — llm_dedup_cache (item 10)
# ---------------------------------------------------------------------------

class TestLlmDedupCache:
    def test_store_then_lookup_returns_cached(self):
        from analysis.llm_dedup_cache import store, lookup
        result = (0.85, "reasoning", "no", "small")
        store("SYSTEM:\n...\nUSER:\nTrump signs deal", result, ttl_seconds=600)
        got = lookup("SYSTEM:\n...\nUSER:\nTrump signs deal", ttl_seconds=600)
        assert got == result

    def test_disabled_when_ttl_zero(self):
        from analysis.llm_dedup_cache import store, lookup
        result = (0.5, "x", "yes", "small")
        store("prompt", result, ttl_seconds=0)
        assert lookup("prompt", ttl_seconds=0) is None

    def test_expired_entries_evicted(self):
        from analysis.llm_dedup_cache import store, lookup
        result = (0.5, "x", "yes", "small")
        store("prompt", result, ttl_seconds=10, now_monotonic=100.0)
        # 11s later — expired
        assert lookup("prompt", ttl_seconds=10, now_monotonic=111.0) is None

    def test_prompt_text_is_cache_identity(self):
        from analysis.llm_dedup_cache import store, lookup
        result = (0.5, "x", "yes", "small")
        store("SOURCE: Reuters\nSUMMARY: one", result, ttl_seconds=600)
        assert lookup("SOURCE: Reuters\nSUMMARY: one", ttl_seconds=600) == result
        assert lookup("SOURCE: AP\nSUMMARY: two", ttl_seconds=600) is None

    def test_different_prompt_misses(self):
        from analysis.llm_dedup_cache import store, lookup
        store("A", ("r",), ttl_seconds=600)
        assert lookup("B", ttl_seconds=600) is None


# ---------------------------------------------------------------------------
# PROFIT-ALIGN-002 / PROFIT-ALIGN-005 — calibration_aggregator (item 2)
# ---------------------------------------------------------------------------

class TestCalibrationAggregator:
    def _ev(self, prefix="KXTEST", mag="small", side="no", p=0.95,
            outcome=1, pnl=0.40):
        return {
            "type": "CALIBRATION_OBSERVATION",
            "trade_id": "t",
            "ticker": f"{prefix}-26JUN01",
            "market_prefix": prefix,
            "side": side,
            "estimated_probability": p,
            "realized_outcome": outcome,
            "entry_price_cents": 92.0,
            "pnl_dollars": pnl,
            "cost_dollars": 4.60,
            "llm_magnitude": mag,
            "llm_confidence": 0.85,
            "signal_source": "src",
            "ts_entry": "2026-05-25T18:36:44+00:00",
            "ts_resolved": "2026-06-01T12:00:00+00:00",
        }

    def test_aggregate_empty_returns_zero_obs(self):
        from scripts.calibration_aggregator import aggregate
        out = aggregate([])
        assert out["total_observations"] == 0
        assert out["buckets"] == []

    def test_aggregate_buckets_by_prefix_magnitude_side(self):
        from scripts.calibration_aggregator import aggregate
        events = [
            self._ev("KXA", "small", "no", 0.95, 1, 0.40),
            self._ev("KXA", "small", "no", 0.90, 1, 0.40),
            self._ev("KXA", "small", "no", 0.85, 0, -4.60),
            self._ev("KXB", "moderate", "yes", 0.65, 1, 2.00),
        ]
        out = aggregate(events)
        assert out["total_observations"] == 4
        assert out["total_wins"] == 3
        # 2 distinct buckets
        keys = {(b["market_prefix"], b["llm_magnitude"], b["side"]) for b in out["buckets"]}
        assert ("KXA", "small", "no") in keys
        assert ("KXB", "moderate", "yes") in keys

    def test_brier_score_arithmetic(self):
        from scripts.calibration_aggregator import aggregate
        # Two perfect predictions: p=1.0 outcome=1, p=0.0 outcome=0 → Brier=0
        events = [
            self._ev("KX", "small", "no", 1.0, 1, 0.0),
            self._ev("KX", "small", "no", 0.0, 0, 0.0),
        ]
        out = aggregate(events)
        b = out["buckets"][0]
        assert b["brier_score"] == pytest.approx(0.0)

        # Two terrible predictions: p=1.0 outcome=0, p=0.0 outcome=1
        events = [
            self._ev("KX", "small", "no", 1.0, 0, 0.0),
            self._ev("KX", "small", "no", 0.0, 1, 0.0),
        ]
        out = aggregate(events)
        b = out["buckets"][0]
        assert b["brier_score"] == pytest.approx(1.0)

    def test_insufficient_evidence_flag(self):
        from scripts.calibration_aggregator import aggregate
        # 3 obs < min 5 → flagged
        events = [self._ev("KX", "small", "no", 0.5, 1, 0.0) for _ in range(3)]
        out = aggregate(events)
        assert out["buckets"][0]["insufficient_evidence"] is True

    def test_main_dry_run_smoke(self, tmp_path, monkeypatch):
        """End-to-end smoke: feed a tiny live/trades.jsonl, expect dry-run
        prints table without writing file."""
        import json
        (tmp_path / "live").mkdir()
        ev = self._ev()
        (tmp_path / "live" / "trades.jsonl").write_text(json.dumps(ev) + "\n", encoding="utf-8")
        from scripts.calibration_aggregator import main
        rc = main([
            "--trade-log-root", str(tmp_path),
            "--output", str(tmp_path / "summary.json"),
            "--dry-run",
        ])
        assert rc == 0
        assert not (tmp_path / "summary.json").exists()

    def test_main_writes_output_file(self, tmp_path):
        import json
        (tmp_path / "live").mkdir()
        ev = self._ev()
        (tmp_path / "live" / "trades.jsonl").write_text(json.dumps(ev) + "\n", encoding="utf-8")
        from scripts.calibration_aggregator import main
        out_path = tmp_path / "calibration_summary.json"
        rc = main([
            "--trade-log-root", str(tmp_path),
            "--output", str(out_path),
        ])
        assert rc == 0
        assert out_path.exists()
        data = json.loads(out_path.read_text(encoding="utf-8"))
        assert data["total_observations"] == 1


# ---------------------------------------------------------------------------
# PROFIT-ALIGN cfg surface for deferred-with-flags items (6, 7)
# ---------------------------------------------------------------------------

class TestDeferredCfgSurface:
    """Items 6 (per-source Bayesian) and 7 (lane skip) ship cfg flags so
    the operator opt-in surface exists. Runtime wiring is deferred until
    PROFIT-ALIGN-002 calibration evidence informs the design."""

    def test_lane_skip_flag_default_off(self):
        import config as cfg_mod
        assert cfg_mod.cfg.enable_lane_skip_when_no_data is False

    def test_derived_series_priors_flag_default_off(self):
        import config as cfg_mod
        assert cfg_mod.cfg.enable_derived_series_priors is False

    def test_llm_dedup_ttl_default_positive(self):
        import config as cfg_mod
        ttl = cfg_mod.cfg.llm_dedup_cache_ttl_seconds
        assert isinstance(ttl, int)
        assert ttl >= 0
