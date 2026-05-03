# Claude Latest Commits Adversarial Review

Reviewed commits: `8906a21`, `c3630c0`, `87521e8`.

## Findings

### F1 - OBS-005 / MATCH-001 source-inspection pins are brittle to correct refactors

Severity: MEDIUM

`tests/test_executor.py` and `tests/test_market_matcher.py` mostly pin source text. That catches the narrow landing pattern, but it can fail a correct implementation that moves the sentinel into a helper/constant, formats `_last_traded.get(...)` over multiple lines, or retains old symbol names in comments while behavior is fixed. The source pins are useful as landing sentinels, but they should be paired with one runtime behavior test per contract when the implementation lands.

Recommended landing-time additions:

- OBS-005: monkeypatch `time.monotonic()` near process start and assert a never-traded ticker does not trip cooldown in both paper and live validation paths.
- MATCH-001 B': assert the ticker-only overlap case suppresses while a supporting non-ticker token case survives, without depending on private predicate names.

### F2 - EXEC-002 harness over-constrains the private state shape

Severity: MEDIUM

`tests/test_blend_task.py::test_blend_task_carries_recent_series_enqueues_state` requires `_recent_series_enqueues` to exist as an empty `dict`. That gives a clear failure message, but it hard-codes one implementation shape. A correct bounded LRU, deque, injected guard object, or module-level helper would fail even if the FISA replay behavior is correct.

The behavior tests in the same block are stronger than the state-shape pin. Keep the private-state test only if the spec intentionally mandates that exact attribute; otherwise weaken it at landing time to "BlendTask carries a same-series guard state" through behavior and reset/window observability.

### F3 - governance_monitor xfail tests leak reloaded module defaults across the test process

Severity: LOW

`tests/test_governance_monitor.py` mutates `KALSHI_HOME`, reloads `scripts.governance_monitor`, and then leaves the module object loaded with constants derived from the monkeypatched environment. `monkeypatch` restores the environment, but not `_DEFAULT_LOG` / `_DEFAULT_OVERRIDES` inside the already-imported module. Today's later tests pass explicit paths, so the damage is contained; future tests appended after these path tests could read stale defaults.

Recommended landing-time fix: reload the module again after the env patch unwinds, or isolate the default-path assertions behind a helper that imports the module in a subprocess.

### F4 - EDGE-004 lever menu sequencing now needs Codex empirics folded in

Severity: LOW

`87521e8` correctly downgrades simple Jaccard thresholding, but it was written before the source-class and post-soak simulation commits. It still treats Lever A as "Codex in flight" and sizes MATCH-001 qualitatively. The next spec polish should replace those placeholders with the landed numbers: source-class imbalance (`news` 238 joins vs `official` 9), MATCH-001 B' retained OPPORTUNITY estimate (`260 -> 87`), and Lever D's pre-LLM gate retention curve (`67/260` retained at 0.04-0.06).

## No Blocking Issues

I did not find a reason to revert any of the three commits. The xfail strategy is consistent with the post-soak preload approach. The main risk is that several tests are intentionally implementation-shaped; that is acceptable for a preload sentinel only if the production landing commit adds behavior-level assertions in the same hunk that removes strict xfail markers.
