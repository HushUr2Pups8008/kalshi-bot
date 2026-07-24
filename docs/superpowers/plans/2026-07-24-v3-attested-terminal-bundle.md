# V3 Attested Terminal Bundle Plan

1. Re-freeze a compatible protocol before collection. Its evidence contract must
   bind the persisted book, LLM payload, receipt-attestation envelope, and their
   hashes; the current v1 verifier is structural-only and cannot prove those
   relationships after the fact.
2. Add RED tests for an offline-only v3 append API that rejects bare candidate
   payloads, untrusted protected-history receipts, protocol-binding drift,
   out-of-window/backdated decisions, market/side/LLM mismatches, and writes to
   v1/v2 or `paper_trades.db`.
3. Add typed protected-history and authoritative-settlement receipt inputs. Bind
   each decision-time receipt to the locked protocol, a dedicated isolated paper
   account, Kalshi `rest_detail`, `NO` side, G1-G6 pass, and sole G7 failure.
4. Recompute entry fees from the frozen fee artifact and terminal cashflows from
   the official settlement payload. Reject fee-version drift, refunds/voids,
   receipt drift, and unreconciled gross or fee-net P&L.
5. In one `BEGIN IMMEDIATE` transaction, validate the existing v3 binding and
   chain head; atomically append candidate, LLM provenance, paper execution,
   settlement, fee receipt, evaluation, and evidence rows. Roll back every table
   on any failure. Keep initialization fail-closed for unverified populated DBs.
6. Add RED tests for the first record, a second linked record, bad previous hash,
   duplicate identities, partial-write rollback, and continued promotion refusal.
7. Run focused v3/protocol/economics tests, independent adversarial review, and
   full repository CI. Do not wire `main.py`, change service/runtime state, enable
   fee-net paper accounting, import historical logs, or enable promotion.

Operator prerequisite: before cohort collection, place the exact protocol and
attestation artifact on a trusted protected ref and run an isolated decision-time
witness. Branch-local code or retrospective v2/log imports do not constitute
protected OOS history.
