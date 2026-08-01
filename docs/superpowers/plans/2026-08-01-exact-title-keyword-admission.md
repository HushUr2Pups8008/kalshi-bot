# Exact Title Keyword Admission Implementation Plan
> For agentic workers: required sub-skill: use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox syntax.

**Goal:** Permit the existing LLM review path to inspect a news headline only when a previously matched market explicitly asks whether the Senate will vote on one named Act and the headline asserts a vote on that identical Act. The admission marker must never create a keyword-only probability shift or trade when the LLM is absent or neutral.

**Scope:** analysis/signal_analyzer.py and focused signal-analyzer tests only. No matcher threshold, research-gate, sizing, execution, runtime configuration, database, or live-trading changes.

## Task 1: Add failing exact-title admission tests

**Files:**
- Modify: tests/test_signal_analyzer.py

- [ ] Add a helper-level positive test for a matched market titled Will the Senate vote on the CLARITY Act? and a headline such as Senate leaders will vote on the CLARITY Act next week. The expected marker is exact_title_senate_vote:clarity act.
- [ ] Add a second positive test for the alternate headline shape CLARITY Act vote scheduled by Senate leaders.
- [ ] Add negative tests which assert no marker for each unsafe near-match:
  - a headline naming a different Act: GENIUS Act;
  - a generic Senate vote headline without the Act title;
  - a House market or a market using pass rather than vote on;
  - a longer Act title in the headline that only contains the market Act title as a substring.
- [ ] Add an async integration test with the enforced pre-LLM gate and weak semantic overlap. Stub llm_estimate_detailed to prove the exact positive admits LLM review, returns the marker only with a useful LLM result, and emits no scored keyword contribution.
- [ ] Add an async regression test where the same exact match has no LLM result. It must return the existing empty-keyword/no-signal tuple, preserving the no-keyword execution barrier.

Run:
    CI=1 /Users/jacobparenti/vscode/kalshi-bot/.venv/bin/python -m pytest -q tests/test_signal_analyzer.py -k 'exact_title_senate_vote or no_keyword_headline_uses_keyword_gate'

Expected before implementation: the new helper and integration tests fail because no exact-title admission marker exists.

## Task 2: Implement a fail-closed exact-title admission helper

**Files:**
- Modify: analysis/signal_analyzer.py

- [ ] Add a private helper near the pre-LLM keyword-gate code that accepts news, market, and match_meta, then returns str or None.
- [ ] Require match_meta to be a dictionary. Read only market title/subtitle text and headline; missing/non-string values return None.
- [ ] Extract an Act title only from an explicit Senate question of the form Will [the] Senate vote on [the] named Act?. Normalize case and surrounding punctuation for comparison while preserving word boundaries, so CLARITY Act never matches Digital Asset Market CLARITY Act or CLARITY Act of 2025.
- [ ] Accept headline evidence only when it contains either vote on [the] same Act or same Act vote, including the plural verb form votes on. Generic vote language, other chambers, another action verb, and another Act return None.
- [ ] Return deterministic observability marker exact_title_senate_vote:normalized act title without adding it to GEOPOLITICAL_SIGNALS, _keyword_score, or _keyword_contributions.

## Task 3: Thread marker through LLM admission without standalone signal

**Files:**
- Modify: analysis/signal_analyzer.py

- [ ] Call helper immediately after keyword_estimate. Use an internal admission_keywords value for _should_keyword_override_pre_llm_gate and _pre_llm_log_fields, so the synthetic marker can satisfy only the existing keyword admission condition.
- [ ] Keep kw_prob, keyword_signal_strength, matched signal-group counting, and contribution records based solely on configured geopolitical keywords.
- [ ] When LLM returns a useful result, include marker in returned/logged keyword list for auditability. When LLM is unavailable or neutral/non-useful and no configured keyword matched, return unchanged empty-keyword gate result.
- [ ] Preserve all existing behavior when no exact marker is present.

Run:
    CI=1 /Users/jacobparenti/vscode/kalshi-bot/.venv/bin/python -m pytest -q tests/test_signal_analyzer.py
    CI=1 /Users/jacobparenti/vscode/kalshi-bot/.venv/bin/python -m pytest -q tests/test_main_pipeline.py -k 'no_keywords or pre_llm or process_candidate'

Expected after implementation: exact title matches reach existing LLM review path; all near-matches and no-LLM cases stay fail-closed.

## Task 4: Review and commit

- [ ] Inspect git diff --check and scoped diff for accidental matcher, execution, configuration, database, or live-control changes.
- [ ] Confirm each listed positive and negative test maps to a user requirement and tests do not mock away exact-title parser.
- [ ] Commit this plan separately before code changes:
    git add docs/superpowers/plans/2026-08-01-exact-title-keyword-admission.md
    git commit -m "docs: plan exact-title keyword admission"
- [ ] Commit implementation and tests separately only after focused tests pass:
    git add analysis/signal_analyzer.py tests/test_signal_analyzer.py
    git commit -m "fix: admit exact Senate Act vote headlines"

## Plan Review

**Spec coverage:** Task 1 covers positive behavior, wrong Act, generic vote, wrong chamber/verb, substring containment, and existing empty-keyword behavior. Task 2 defines strict parsing that rejects missing or ambiguous input. Task 3 constrains marker to LLM admission rather than scoring or execution. Task 4 verifies scope and preserves independent plan/code commits.

**Placeholder scan:** No TODO, TBD, deferred implementation, generic testing directive, or unspecified code step remains.
