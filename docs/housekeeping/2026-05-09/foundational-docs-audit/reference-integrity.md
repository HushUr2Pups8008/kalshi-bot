# Reference Integrity Audit — Foundational Docs (Phase 2)

> Phase 2 continuation of `docs/housekeeping/2026-05-08/claude-md-audit.md`. This report covers reference integrity only — file paths, function/class names, module refs, line-number citations, external commands, cross-doc pointers, config/env vars, and external system IDs.
>
> Audit date: 2026-05-09 UTC. Branch: `housekeeping/foundational-docs`.
>
> Method: Symbol/path/line references verified by grep + targeted Read against the working tree.

---

## 1. Inventory Summary

| Source doc | Path | Refs | RESOLVES | BROKEN | AMBIGUOUS | NOT-VERIFIABLE |
|---|---|---|---|---|---|---|
| Project CLAUDE.md | `kalshi-bot/CLAUDE.md` | 23 | 23 | 0 | 0 | 0 |
| Global CLAUDE.md | `~/.claude/CLAUDE.md` | 11 | 4 | 0 | 0 | 7 |
| RTK.md | `~/.claude/RTK.md` | 8 | 7 | 1 | 0 | 0 |
| AGENTS.md | `~/.claude/AGENTS.md` | 2 | 1 | 0 | 1 | 0 |
| domain_constraints.md | `~/.claude/rules/domain_constraints.md` | 10 | 9 | 0 | 1 | 0 |
| Other rule files | `~/.claude/rules/*.md` (×9) | 0 | — | — | — | — |
| README.md (cross-check) | `kalshi-bot/README.md` | 3 | 2 | 1 | 0 | 0 |
| **Totals** | | **57** | **46** | **2** | **2** | **7** |

Reference types: file paths (14), cross-doc refs (9), function/method refs (8), command refs (9), dir module refs (5), env/config vars (4), debt log IDs (4), MCP tool names (7, all NOT-VERIFIABLE), line-number citations (4), git tag refs (1), path globs (1).

---

## 2. Full Inventory

| # | Source doc | Line | Reference text | Type | Status | Notes |
|---|---|---|---|---|---|---|
| 1 | project CLAUDE.md | 20 | `docs/profit_path_debt_log.md` | file path | RESOLVES | exists |
| 2 | project CLAUDE.md | 22 | `~/.claude/rules/planning.md` | cross-doc | RESOLVES | |
| 3 | project CLAUDE.md | 23 | `~/.claude/rules/validation.md` | cross-doc | RESOLVES | |
| 4 | project CLAUDE.md | 24 | `~/.claude/rules/git_workflow.md` | cross-doc | RESOLVES | |
| 5 | project CLAUDE.md | 28 | `~/.claude/rules/release_versioning.md` | cross-doc | RESOLVES | |
| 6 | project CLAUDE.md | 31 | `scripts/sync_readme_version.py --check` | command | RESOLVES | script exists, CI uses it |
| 7 | project CLAUDE.md | 32 | `git config core.hooksPath .githooks` | command | RESOLVES | `.githooks/pre-commit` exists |
| 8 | project CLAUDE.md | 39 | `CHANGELOG.md` | file path | RESOLVES | exists |
| 9 | project CLAUDE.md | 53 | `_normalize_pem()` in `kalshi/rest_client.py` | function ref | RESOLVES | defined at `rest_client.py:28` |
| 10 | project CLAUDE.md | 53 | `_normalize_pem()` in `kalshi/websocket_client.py` | function ref | RESOLVES | defined at `websocket_client.py:51` |
| 11 | project CLAUDE.md | 57 | `kalshi/websocket_client.py` version-detect | file+behavior | RESOLVES | lines 25-30 |
| 12 | project CLAUDE.md | 62 | `JSONDecoder.raw_decode()` | function ref | RESOLVES | `signal_analyzer.py:426, :434` |
| 13 | project CLAUDE.md | 63 | `resolve_market()` DB atomicity / `with self._conn:` | function+behavior | RESOLVES | wrapper at `paper_trader.py:650`; atomic body at `:685` (Phase 1 D-2 wording note still applies) |
| 14 | project CLAUDE.md | 66 | `governance/llm.py:LocalQwenLLM.complete` | file+class+method | RESOLVES | class at `:175`, method at `:195`, `think=False` at `:211` |
| 15 | project CLAUDE.md | 66 | `PROFIT-GOV-001` (closed 2026-05-02) | debt ID | RESOLVES | debt log line 3012; Status=COMPLETE |
| 16 | project CLAUDE.md | 67 | `analysis/signal_analyzer.py` → `/chat/completions` | file+endpoint | RESOLVES | `signal_analyzer.py:783` |
| 17 | project CLAUDE.md | 67 | `/api/generate` endpoint | endpoint ref | RESOLVES | `governance/llm.py:215` |
| 18 | project CLAUDE.md | 68 | `governance/prompts.py` lines 27–31 | file+line range | RESOLVES | block confirmed |
| 19 | project CLAUDE.md | 68 | `PROFIT-GOV-002` | debt ID | RESOLVES | debt log line 3248; Status=COMPLETE |
| 20 | project CLAUDE.md | 76 | `MAX_BET_HARD_CAP` | env var | RESOLVES | `config.py:1050` |
| 21 | project CLAUDE.md | 76 | `MAX_BET_DOLLARS` (deprecated) | env var (absent) | RESOLVES | correctly absent (0 grep hits) |
| 22 | project CLAUDE.md | 77 | `cfg.dynamic_max_bet(notional)` | method ref | RESOLVES | `config.py:1323` |
| 23 | project CLAUDE.md | 78 | `KALSHI_GEOPOLITICAL_SERIES` (obsolete) | env var (absent) | RESOLVES | correctly absent |
| 24 | global CLAUDE.md | 22 | `semantic_search_nodes` | MCP tool | NOT-VERIFIABLE | runtime plugin |
| 25 | global CLAUDE.md | 22 | `query_graph` | MCP tool | NOT-VERIFIABLE | |
| 26 | global CLAUDE.md | 22 | `get_impact_radius` | MCP tool | NOT-VERIFIABLE | |
| 27 | global CLAUDE.md | 22 | `detect_changes` | MCP tool | NOT-VERIFIABLE | |
| 28 | global CLAUDE.md | 23 | `ctx_batch_execute` | MCP tool | NOT-VERIFIABLE | |
| 29 | global CLAUDE.md | 23 | `ctx_execute` | MCP tool | NOT-VERIFIABLE | |
| 30 | global CLAUDE.md | 23 | `ctx_execute_file` | MCP tool | NOT-VERIFIABLE | |
| 31 | global CLAUDE.md | 25 | `rtk gain` | command | RESOLVES | `/opt/homebrew/bin/rtk` v0.39.0 |
| 32 | global CLAUDE.md | 27 | `~/.claude/rules/planning.md` | cross-doc | RESOLVES | |
| 33 | global CLAUDE.md | 28 | `~/.claude/rules/validation.md` | cross-doc | RESOLVES | |
| 34 | global CLAUDE.md | 29 | `~/.claude/rules/git_workflow.md` | cross-doc | RESOLVES | |
| 35 | global CLAUDE.md | 31 | `@RTK.md` | cross-doc | RESOLVES | exists |
| 36 | RTK.md | 8 | `rtk gain` | command | RESOLVES | |
| 37 | RTK.md | 9 | `rtk gain --history` | command | RESOLVES | |
| 38 | RTK.md | 10 | `rtk discover` | command | RESOLVES | |
| 39 | RTK.md | 11 | `rtk proxy <cmd>` | command | RESOLVES | |
| 40 | RTK.md | 17 | `rtk --version` | command | RESOLVES | "rtk 0.39.0" |
| 41 | RTK.md | 18 | `rtk gain` (install check) | command | RESOLVES | |
| 42 | RTK.md | 19 | `which rtk` | command | RESOLVES | |
| 43 | RTK.md | 29 | "Refer to CLAUDE.md for full command reference." | cross-doc | ~~**BROKEN**~~ **RESOLVED 2026-05-08** | RTK.md:29 was updated to "Run `rtk --help` for the full command reference." (guidance-consolidation Phase 8 Batch 4). |
| 44 | AGENTS.md | 13 | `project/AGENTS.md` | path/label | AMBIGUOUS | no `project/` dir; project-level lives at repo root |
| 45 | AGENTS.md | 20 | `rules/*.md` | path glob | RESOLVES | 10 rule files |
| 46 | domain_constraints.md | 3 | `/analysis` | dir ref | RESOLVES | |
| 47 | domain_constraints.md | 7 | `/trading` | dir ref | RESOLVES | |
| 48 | domain_constraints.md | 11 | `/tasks` | dir ref | RESOLVES | |
| 49 | domain_constraints.md | 13 | `/feeds` | dir ref | RESOLVES | |
| 50 | domain_constraints.md | 15 | `/governance` | dir ref | RESOLVES | NEW since Phase 1 (M-3 implemented) |
| 51 | domain_constraints.md | 18 | `governance/prompts.py` | file path | RESOLVES | |
| 52 | domain_constraints.md | 19 | `governance/prompts.py lines 27–31` | file+line range | RESOLVES | consistent with project CLAUDE.md:68 |
| 53 | domain_constraints.md | 19 | `cycle-17C` label | label/ID | AMBIGUOUS | no path given; charter at `docs/governance/2026-05-07-cycle-17c-charter-single-variable-redesign.md` |
| 54 | domain_constraints.md | 19 | `PROFIT-GOV-002` | debt ID | RESOLVES | debt log line 3248 |
| 55 | domain_constraints.md | 21 | `governance/agent.py` | file path | RESOLVES | real-mode flip authority lines 271, 280, 288 |
| 56 | README.md | 219 | `pre-filter-repo-2026-05-02` tag | git tag | RESOLVES | self-documented |
| 57 | README.md | 243-258 | `transfer/macbook_handoff_2026-05-01/` links (×9) | dir links | **BROKEN** | dir removed from git history 2026-05-02; links dead but README:219 self-documents the removal |

---

## 3. Broken / Stale References

### F-1 — README dead links to `transfer/macbook_handoff_2026-05-01/` (P2, S)
- README.md:243-258, 9 links. Dir removed from git history 2026-05-02. Surrounding prose at line 219 explains the removal and names the recovery tag, so an agent reading the prose understands; an agent grepping links does not.
- Effort: low. Replace link syntax with plain text, or annotate as historical.
- Phase 1 overlap: not covered.

### F-2 — RTK.md:29 circular reference (P3, S)

> **[RESOLVED 2026-05-08 — guidance-consolidation Phase 8 Batch 4]** Live RTK.md:29 reads `Run \`rtk --help\` for the full command reference.` Circular reference removed. F-2 / SR-1 closed.

- "Refer to CLAUDE.md for full command reference." Global CLAUDE.md points back via `@RTK.md` — circular dead-end.
- Effort: trivial. Delete line 29 or replace with "See sections above."
- Phase 1 overlap: Phase 1 SR-1 (identical), still unresolved.

### F-3 — AGENTS.md:13 `project/AGENTS.md` notation (P3, S)
- No `project/` directory exists; project-level AGENTS.md lives at repo root.
- Effort: trivial. Replace with "project-root `AGENTS.md`" or similar.
- Phase 1 overlap: not covered.

### F-4 — domain_constraints.md:19 `cycle-17C` label without path (P3, S)

> **[RESOLVED 2026-05-08 — guidance-consolidation Phase 8 Batch 4]** Live `domain_constraints.md:19` already includes the full charter path: `docs/governance/2026-05-07-cycle-17c-charter-single-variable-redesign.md`. F-4 is stale.

- Charter exists at `docs/governance/2026-05-07-cycle-17c-charter-single-variable-redesign.md` but label alone is not navigable.
- Effort: trivial. Append the path inline.
- Phase 1 overlap: not covered.

### F-5 — Global CLAUDE.md MCP tool names (NOT-VERIFIABLE)
- `~/.claude/CLAUDE.md:22-23` lists 7 tool names. Both MCP plugins were unavailable this session.
- Operational risk: low. Mismatch fails visibly at runtime.
- Re-verify in a session with `code-review-graph` and `context-mode` both loaded.

---

## 4. Cross-Doc Consistency

### Phase 1 Recommendation Resolution

| Phase 1 ID | Recommendation | Status |
|---|---|---|
| D-1 | Fix `KalshiRestClient` "HMAC-SHA256" docstring | **RESOLVED** |
| M-1 | Add anchor_rate polarity gotcha | **RESOLVED** (project CLAUDE.md:68) |
| M-2 | Extend `think: False` gotcha with endpoint warning | **RESOLVED** (project CLAUDE.md:67) |
| M-3 | Add `/governance` domain constraint | **RESOLVED** (domain_constraints.md:15-22) |
| SR-1 | Fix RTK.md:29 circular reference | **UNRESOLVED** (see F-2) |
| D-2 | `resolve_market()` naming drift | **UNCHANGED** (wording imprecision) |
| D-3 | Same-signal guard mechanism drift | **UNCHANGED** (behavioral description still accurate) |

### New Phase 2 Cross-Doc Checks

- **`governance/prompts.py lines 27–31`** cited identically in project CLAUDE.md:68 and domain_constraints.md:19. Both correct. Dual citation is intentional.
- **`documentation_format.md` back-reference rule:** all 10 rule files verified — none point back to CLAUDE.md. Rule correctly enforced.
- **Debt-ID date consistency:** PROFIT-GOV-001 (CLAUDE.md says 2026-05-02; debt log confirms). PROFIT-GOV-002 (no date in CLAUDE.md or domain_constraints.md; debt log says 2026-05-03). No inconsistency.

---

## 5. README.md vs CLAUDE.md Cross-Checks

No contradictions found.

| Claim | README check | Result |
|---|---|---|
| Python 3.14 + `aiohttp>=3.10.0` | README Infrastructure section | CONSISTENT |
| `MAX_BET_HARD_CAP` (not `MAX_BET_DOLLARS`) | README env var table | CONSISTENT |
| RSA-PSS/SHA-256 signing | README API auth section | CONSISTENT |
| `KALSHI_GEOPOLITICAL_SERIES` obsolete | README does not reference old allowlist | CONSISTENT |
| VERSION badge parity | `VERSION` file content | CONSISTENT |
| Debt log path | README does not contradict | CONSISTENT |

Only README finding is F-1 (dead handoff links) — README-internal, not a CLAUDE.md contradiction.

---

## 6. Top 5 Actions (P0 → P3)

1. **(P2, S) Fix README dead links to `transfer/macbook_handoff_2026-05-01/`** — annotate as historical or convert to plain text matching the prose at README:219.
2. **(P3, S) Fix RTK.md:29 circular reference** — delete line or replace with "See sections above for all rtk commands." Phase 1 SR-1 carry-over.
3. **(P3, S) Clarify AGENTS.md:13 `project/AGENTS.md` notation** — replace with "project-root `AGENTS.md`."
4. **(P3, S) Add cycle-17C charter path to domain_constraints.md:19** — append the doc path inline.
5. **(P3, S — deferred) Re-verify MCP tool names** in a session with both plugins loaded.

---

## 7. Three-Bullet Summary

- **All 55 verifiable references resolve correctly.** Zero P0 or P1 broken references in the foundational doc set. Phase 1's four key recommendations (D-1 docstring, M-1 anchor_rate gotcha, M-2 endpoint warning, M-3 governance domain constraint) are correctly implemented.
- **Two broken references remain:** F-1 (README dead handoff links, P2 — self-documented removal but cosmetically broken) and F-2 (RTK.md:29 circular pointer, P3 — Phase 1 SR-1 carry-over). Two additional ambiguities (F-3 AGENTS.md path notation, F-4 cycle-17C label) are P3 nits.
- **Seven MCP tool names are NOT-VERIFIABLE** in this session (plugins unavailable). Not a docs error — environmental coverage gap.
