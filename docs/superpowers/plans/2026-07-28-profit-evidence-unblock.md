# Profit Evidence Unblock Plan

1. Add RED coverage in `tests/test_open_paper_settlement_audit.py` for a
   hash-attested SQLite artifact supplied after external writer quiescence,
   canonical unresolved-row hash, and a report-body hash. Do not snapshot a live
   database: reject journal, WAL, and shared-memory sidecars; verify the input
   hash before and after immutable reads; and fail closed on integrity or drift.
   The hash attests file identity only; the operator-owned quiescence prerequisite
   remains an explicit runtime handoff, not proof supplied by this command.
   An authoritative terminal receipt remains observation-only and the command
   never resolves a trade.
2. Trace G7 shadow-capture inputs from decision-time facts. Preserve only the
   already-computed Polymarket sizing provenance and assert that it cannot alter
   route admission or sizing. Keep absent book, fee, and settlement-source facts
   explicitly unscorable; do not fetch later data, relax G7, or infer candidates.
3. Run focused audit/shadow suites, static checks, a second financial-path
   review, and the broader test suite. Commit evidence-only code on this branch.
   Do not merge, restart, modify the production databases, enable fee-net
   accounting, or enable live trading.
