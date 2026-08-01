# Kalshi FIX Settlement Ingress Design

## Goal

Preserve already-verified Kalshi FIX `MarketSettlementReport` (`35=UMS`)
messages in an isolated immutable ledger without changing trading, paper
settlement, accounting, readiness, or promotion behavior.

## Trust Boundary

The ingress accepts only a process-local typed envelope created by a future
authenticated FIX session. It does not implement a listener, logon, network
client, file import, or credential configuration. A stored raw message and
its hashes prove local preservation, not transport authentication or complete
fee evidence.

Every status must remain non-authoritative:

- Pagination completeness is unknown.
- Canonical settlement binding is absent.
- Fee-net P&L is unscorable.
- PaperTrader, orders, and promotion remain unchanged and ineligible.

## Storage Contract

The separate `data/kalshi_fix_settlement_ingress.db` stores raw inbound FIX
bytes, canonical parsed content, non-secret session provenance, and distinct
hashes for wire material, normalized content, and the compatibility wrapper.
It uses append-only rows, conflict retention, deterministic quarantine,
schema validation, and read-only snapshots.

The scoped receipt identity is
`(source_id, account_party_id_sha256, MarketSettlementReportID)`. Reports
must never be deduplicated by market symbol because one market can have
multiple report fragments.

## Non-Goals

- No PaperTrader or `settlement_economics` integration.
- No fee-net accounting, settlement outbox, capital guard, or readiness input.
- No main-loop, scheduler, or bot startup wiring.
- No claim that a local import is authenticated FIX evidence.

## Promotion Prerequisite

A later, separate design must prove authenticated session provenance, complete
fragment coverage, exact candidate/fill/market correlation, and atomic
receipt-backed PaperTrader accounting before any fee-net or profitability
claim can be made.
