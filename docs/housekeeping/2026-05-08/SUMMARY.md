# Phase-1 Housekeeping Audit — Summary

**Date:** 2026-05-08
**Branch:** `main` @ `bc63b3b`
**Scope:** all code excluding `/trading` (domain risk), all governance markdown files, harness surface
**Reports:** 7 (5 agent reports + 2 main-thread skill reports)

---

## Severity / Effort Matrix

`Severity`: **P0** = correctness or security risk · **P1** = high-priority maintainability or undocumented load-bearing pattern · **P2** = medium · **P3** = polish.
`Effort`: **S** ≤ 1 hour · **M** ≤ 1 day · **L** > 1 day.
`Type`: code | doc | config | harness.

---

## P0 — Correctness / Security

| # | Finding | Effort | Type | Source |
|---|---------|--------|------|--------|
| — | None. Ruff clean, no SQL injection, no hardcoded secrets, no eval/exec misuse, all 4 load-bearing Kalshi/qwen3 patterns confirmed-current. | — | — | [security-audit.md](security-audit.md), [python-quality.md](python-quality.md) |

---

## P1 — High Priority

| # | Finding | Effort | Type | Source |
|---|---------|--------|------|--------|
| **P1-01** | Silent auth bypass: `_sign()` in `kalshi/rest_client.py:98–100` and `_build_ws_auth_headers()` in `kalshi/websocket_client.py:96–98` swallow signing exceptions and let unsigned/incomplete-header requests proceed. A bad PEM at startup should be fatal, not a per-request silent degradation. | S | code | [security-audit.md §P1](security-audit.md) |
| **P1-02** | `analysis/signal_analyzer.py` has two oversized functions: `_ollama_estimate_detailed` (223 lines, line 649) and `estimate_probability` (263 lines, line 1001). High cognitive load + cross-cutting concerns (HTTP retry, JSON extraction, blending, telemetry). | M | code | [python-quality.md §P1](python-quality.md) |
| **P1-03** | `utils/logger.py:789` `log_signal_analysis_detail` accepts **46 keyword-only parameters**. Caller drift cannot be statically caught. Replace with a `TypedDict`/`@dataclass`. | M | code | [python-quality.md §P1](python-quality.md) |
| **P1-04** | `feeds/subreddit_selector.py:136` and `:152` — `except Exception: pass` silently swallows SQLite write failures for probe-ts and candidate-suppression. Suppressed subreddits can re-enter rotation; probe counts vanish without trace. | S | code | [python-quality.md §P1](python-quality.md) |
| **P1-05** | `analysis/evidence_scorer.py:48` docstring says "word trigrams" but `_NGRAM_SIZE = 2` → bigrams. Anyone calibrating `NGRAM_OVERLAP_THRESHOLD` from the docstring will get the wrong sensitivity. | S | doc | [comment-health.md](comment-health.md) |
| **P1-06** | `analysis/__init__.py:19` comment says `# after $50 hard cap` — the cap is dynamic (`cfg.dynamic_max_bet(notional)`). CLAUDE.md gotcha explicitly warns about this mismatch. | S | doc | [comment-health.md](comment-health.md) |
| **P1-07** | `analysis/kelly.py:159` docstring says "Rounds down to stay within budget" but `max(1, int(...))` returns 1 even when a single contract exceeds the budget — i.e., the function may **exceed** the budget, not round down. | S | doc | [comment-health.md](comment-health.md) |
| **P1-08** | `kalshi-bot/CLAUDE.md` has no gotcha for `governance/prompts.py:27–31` anchor_rate polarity block (PROFIT-GOV-002 fix). Removing those lines silently re-introduces qwen3 rubber-stamp regression. | S | doc | [claude-md-audit.md M-1](claude-md-audit.md) |
| **P1-09** | `think: False` gotcha in `kalshi-bot/CLAUDE.md` doesn't warn that `analysis/signal_analyzer.py` uses the OpenAI-compat `/v1/chat/completions` endpoint where `think: False` is ignored. Any future qwen3 swap there requires switching endpoints, not just adding the flag. | S | doc | [claude-md-audit.md M-2](claude-md-audit.md) |

---

## P2 — Medium Priority

| # | Finding | Effort | Type | Source |
|---|---------|--------|------|--------|
| **P2-01** | `governance/llm.py:185` `LocalQwenLLM.__init__` does not validate `base_url` is localhost. Today's caller is safe, but the constructor itself imposes no restriction. | S | code | [security-audit.md §P2](security-audit.md) |
| **P2-02** | `config.py:303–304` reads `FADE_TWEET_FEED_URLS` from env and passes directly to `feedparser.parse()` without scheme validation. `feedparser` supports `file://`, `ftp://`, etc. | S | code | [security-audit.md §P2](security-audit.md) |
| **P2-03** | `ops/launchd/*.plist.template` — no preflight check that `.env` exists before launching the bot. Missing `.env` at boot continues in degraded/unsigned state. | S | config | [security-audit.md §P2](security-audit.md) |
| **P2-04** | `.env.example` missing `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USER_AGENT`, `ANTHROPIC_API_KEY`. New-contributor onboarding fails silently — bot starts, but Reddit feed and Anthropic fallback are dark. | S | config | [workspace-surface.md NM-4](workspace-surface.md) |
| **P2-05** | `kalshi/rest_client.py:50–54` class docstring says "HMAC-SHA256". Code uses RSA-PSS. Misleads agents who read the docstring before reaching the CLAUDE.md gotcha. | S | doc | [claude-md-audit.md D-1](claude-md-audit.md) |
| **P2-06** | `~/.claude/rules/domain_constraints.md` has no `/governance` rule. Phase 3+ has real-mode flip authority through `governance/agent.py`; prompt-edit blast radius is undocumented. | S | doc | [claude-md-audit.md M-3](claude-md-audit.md) |
| **P2-07** | `analysis/source_credibility.py`, `analysis/source_stats.py`, `analysis/keyword_stats.py` own SQLite tables in `data/paper_trades.db` — violates INV-4 (analysis layer should be pure). Operational trackers belong in `tasks/`. | M | code | [architecture.md §Top finding](architecture.md) |
| **P2-08** | Duplicated `_days_to_close` helper across `analysis/market_matcher.py:271`, `analysis/market_specificity.py:99`, `analysis/regime_classifier.py:152` — verbatim copies; bug fix to one will not propagate. Each uses overly broad `except Exception`. | S | code | [python-quality.md §P2](python-quality.md) |
| **P2-09** | 14 missing type annotations on public functions — concentrated in `analysis/signal_analyzer.py` (4), `governance/decision.py` (3 `to_*` methods), `feeds/subreddit_selector.py`, `governance/prompts.py`, `governance/evidence.py`, `utils/runtime_overrides.py`, `main.py`. Also one misleading `db_path: Path = None` (declared `Path`, default `None`). | M | code | [python-quality.md §P2](python-quality.md) |
| **P2-10** | 9 plugins installed but `enabledPlugins=false` (`claude-md-management`, `code-review`, `code-simplifier`, `commit-commands`, `context7`, `feature-dev`, `frontend-design`, `github`, `playwright`). All redundant with ECC bundle. | S | harness | [workspace-surface.md NM-1](workspace-surface.md) |
| **P2-11** | 4 missing operator skills powered today by ad-hoc `awk`/`sed` allowlist entries: `kalshi-ops`, `governance-ledger`, `debt-log-triage`, `soak-summary`. | M | harness | [workspace-surface.md G-1..G-4](workspace-surface.md) |
| **P2-12** | Stale comment block `analysis/signal_analyzer.py:547–552` describing "P0.4 experiment / revert if 12h re-run shows no drop" — decision was made and committed 2026-04-24. | S | doc | [comment-health.md](comment-health.md) |
| **P2-13** | Stale doc `analysis/fade_signal.py:10–13` says price fade is a "WebSocket-based **replacement**" with "no Twitter dependency required" — both strategies run in parallel today (`main.py:876–1008`). | S | doc | [comment-health.md](comment-health.md) |
| **P2-14** | `analysis/source_stats.py:45` defines `FLUSH_INTERVAL = 60` — heavily documented in two docstrings but never referenced in any logic. Documentation-only dead constant. | S | code | [dead-code-inventory.md](dead-code-inventory.md) |
| **P2-15** | `analysis/dossier_builder.py:274` `identify_superseded` and `:295` `clear_on_resolution` are test-only (no production callers). Either wire into the pipeline or remove with the matching tests. | S | code | [dead-code-inventory.md](dead-code-inventory.md) |
| **P2-16** | `governance/decision.py` three `to_*` converter methods (`to_disabled_source`, `to_disabled_keyword`, `to_threshold_override`) — only call site at `agent.py:235` is commented out. Planned-but-unwired Task 4 interface. | S | code | [dead-code-inventory.md](dead-code-inventory.md) |
| **P2-17** | `feeds/google_news_monitor.py` not wired into `TradingBot.run()`; no deprecation marker. Either re-wire, mark `# DEPRECATED`, or remove. | S | code | [architecture.md](architecture.md) |
| **P2-18** | No `docs/CODEMAPS/` directory exists. Architectural flow lives only in 53 KB of prose in `IMPLEMENTATION_CONTRACT.md`. | M | doc | [architecture.md](architecture.md) |

---

## P3 — Polish

| # | Finding | Effort | Type | Source |
|---|---------|--------|------|--------|
| **P3-01** | `.gitignore:75–76` redundant `*_pat.txt` glob + specific `JRP_GamingDesktop_pat.txt`. | S | config | [security-audit.md §P3](security-audit.md) |
| **P3-02** | `kalshi/rest_client.py:149–157` error-body redaction is keyword-scan only. Cap log body length before redaction as defense-in-depth. | S | code | [security-audit.md §P3](security-audit.md) |
| **P3-03** | `.gitlab-ci.yml:24` `image: python:3.14-slim` not pinned to digest. | S | config | [security-audit.md §P3](security-audit.md) |
| **P3-04** | `main.py:457` `from pathlib import Path as _Path` inside method body — promote to module-level import. | S | code | [python-quality.md §P3](python-quality.md) |
| **P3-05** | `utils/reporting_helpers.py:84` uses `os.fspath` + `os.path.*` without the explanatory Windows/3.14 comment that other files have. Risk: future "fix" reintroduces a Windows regression. | S | doc | [python-quality.md §P3](python-quality.md) |
| **P3-06** | `analysis/regime_classifier.py:178` references `lessons.md` — file does not exist anywhere in repo. | S | doc | [comment-health.md](comment-health.md) |
| **P3-07** | `analysis/signal_analyzer.py:37` docstring says "Cycle-15B diagnostics" — currently Cycle-17C. | S | doc | [comment-health.md](comment-health.md) |
| **P3-08** | `~/.claude/RTK.md:29` "Refer to CLAUDE.md for full command reference" — global CLAUDE.md has no command reference; circular pointer. | S | doc | [claude-md-audit.md SR-1](claude-md-audit.md) |
| **P3-09** | Working Style / Bug-Fixing / Continuous Improvement sections are ~90% verbatim copies between `~/.claude/CLAUDE.md` and `kalshi-bot/CLAUDE.md`. | S | doc | [claude-md-audit.md R-1..R-4](claude-md-audit.md) |
| **P3-10** | `~/CLAUDE.md` missing H1 title; `kalshi-bot/CLAUDE.md` Critical Gotchas use bold-inline instead of Trigger/Action. | S | doc | [claude-md-audit.md S-1, S-2](claude-md-audit.md) |
| **P3-11** | Missing `_normalize_pem()` duplication note in CLAUDE.md gotcha (the two impls are identical copies; bug fixes won't propagate). | S | doc | [claude-md-audit.md M-4](claude-md-audit.md) |
| **P3-12** | `analysis/signal_analyzer.py:890` hardcoded `model="claude-haiku-4-5-20251001"` — fallback fails silently when Anthropic deprecates the model. | S | code | [claude-md-audit.md M-5](claude-md-audit.md) |
| **P3-13** | `.claude/settings.local.json` has 152 allow entries with many one-off `sed -n` / specific PIDs / `/tmp/verify_*.sh`. After P2-11 lands, many become unnecessary. | S | harness | [workspace-surface.md NM-3](workspace-surface.md) |
| **P3-14** | GateGuard escape hatch (`ECC_GATEGUARD=off`, `ECC_DISABLED_HOOKS`) not documented in any CLAUDE.md / rule file. This audit hit it twice. | S | doc | [workspace-surface.md NM-5](workspace-surface.md) |
| **P3-15** | 8 high-confidence dead scripts in `scripts/` with no callers (full list in dead-code inventory). | S | code | [dead-code-inventory.md](dead-code-inventory.md) |
| **P3-16** | Missing `.env.*` glob in `.gitignore` (covers `.env.local`, `.env.production`, etc.) — currently only literal `.env` is ignored. | S | config | [security-audit.md](security-audit.md) |

---

## Open Questions (Require User Judgment)

1. **Domain-purity refactor (P2-07).** Move `source_credibility`, `source_stats`, `keyword_stats` from `analysis/` to `tasks/` (or a new `tasks/stats/` sub-package)? This restores INV-4 layer purity and unblocks unit testing without DB fixtures, but it's a multi-file rename touching ~10 callsites. **Question:** worth the rename effort, or accept the boundary leak?
2. **Dead/orphan code disposition (P2-15, P2-16, P2-17).** `dossier_builder` test-only methods, `governance/decision.py` `to_*` converters with commented-out wiring, and `feeds/google_news_monitor.py` not wired to `TradingBot.run()`. **Question:** which are abandoned (delete) vs. planned-future (keep + add a `# TODO:` and debt-log entry)?
3. **Plugin cleanup (P2-10).** Uninstall the 9 disabled-but-installed plugins, or keep them around in case the operator wants to flip them back on? Zero runtime cost; zero functional benefit.
4. **CLAUDE.md style (P3-09 / P3-10).** Adopt strict Trigger/Action format for gotchas (matches rule files; rule-engine-friendly) or keep current bold-inline (more readable for prose explanations)?
5. **Domain constraint for `/governance` (P2-06).** Operator may have intentionally scoped `domain_constraints.md` to the signal pipeline only, with phase runbooks owning governance. **Question:** add the rule, or is it deliberately omitted?
6. **GitLab MCP (workspace-surface MI-1).** Worth adding a structured GitLab MCP, or is the existing `glab *` Bash allowlist enough?

---

## Recommended Phase 2 Order

If selective remediation begins, this is the order with highest ratio of risk-reduction to effort:

1. **P1-01 silent auth bypass** — pure security, S effort, single-file change in two places.
2. **P1-04 silent SQLite failures** — observability gain, S effort.
3. **P1-08 + P1-09 + P2-06 CLAUDE.md governance gotchas + domain constraint** — single edit to two markdown files, prevents the next qwen3 regression.
4. **P2-04 .env.example secret-key gap** — onboarding fix, S effort, zero risk.
5. **P1-05/06/07 docstring corrections** — three S edits, pure doc fix.
6. **P2-05 / D-1 docstring HMAC-SHA256 → RSA-PSS** — single edit, zero behavioral change.
7. Defer: P1-02/03 (oversized functions and 46-kwarg logger) — refactor risk warrants brainstorming before edits.
8. Defer: P2-07 layer purity — multi-file rename, requires user decision (Open Question #1).

---

## Cross-Cutting Patterns

- **Silent failures** are the dominant risk class: silent auth bypass (P1-01), silent SQLite swallows (P1-04), silent Anthropic-model deprecation (P3-12), silent missing `.env` (P2-03), silent governance regression risk if anchor_rate block is touched (P1-08).
- **Documentation rot** is concentrated in 3 places: comments that describe earlier-state behavior (`analysis/__init__.py`, `analysis/kelly.py`, `analysis/evidence_scorer.py`), CLAUDE.md gotchas missing newer fixes (PROFIT-GOV-002, PROFIT-LLM-001 nuance), and dead cross-references (`lessons.md`, `RTK.md` circular pointer).
- **Layer-purity drift** is real but contained: `analysis/` operational trackers own DB tables (architecture finding); other layers respect their boundaries.
- **Type-hint coverage** is excellent overall (~95% repo-wide; 100% in `kalshi/`); the gap is concentrated in `governance/` (87%) and `analysis/signal_analyzer.py`.

---

Phase 1 audit complete. Review SUMMARY.md before approving Phase 2 (selective remediation).
