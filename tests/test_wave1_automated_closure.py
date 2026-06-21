"""I-8 Wave-1 automated closure plugin — tests.

The I-8 plugin lives in ``tests/conftest.py``. It reads the wave-1
observation-plan markdown file and, for each xfail-strict mark on a
wave-1 canary, removes the mark if the evidence file contains a
``<TICKET> Nh validation: PASS`` line.

These tests use the built-in ``pytester`` fixture to run pytest in a
synthetic sandbox, so the real wave-1 canaries (and the real
observation-plan markdown file) are NOT touched. Each pytester sandbox
gets its own conftest, copied verbatim from the project's
``tests/conftest.py``, so we exercise the production plugin.

Reference: docs/superpowers/specs/2026-05-23-paper-mode-rapid-learning-framework-design.md
§3 I-8.
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

pytest_plugins = ["pytester"]


_REPO_CONFTEST_PATH = Path(__file__).resolve().parent / "conftest.py"


def _install_repo_conftest(pytester: pytest.Pytester) -> None:
    """Copy the real tests/conftest.py into the pytester sandbox.

    pytester's own conftest is a no-op stub; we want to exercise the
    *real* I-8 plugin. Copying verbatim guarantees that as the production
    plugin evolves (e.g. when I-7 replaces the markdown source), this
    test module stays aligned without manual sync.
    """
    pytester.makeconftest(_REPO_CONFTEST_PATH.read_text(encoding="utf-8"))


def _write_evidence(pytester: pytest.Pytester, body: str) -> None:
    """Create the observation-plan markdown at the path the plugin reads."""
    target = pytester.path / "docs" / "_archive" / "governance" / "wave-1-post-deploy-observation-plan.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")


def _canary_test_module(extra: str = "") -> str:
    """Return a module string that mirrors the real wave-1 canary tests.

    Each test asserts both that the evidence file contains the ticket
    AND that it contains the PASS marker. With xfail-strict in place,
    the absence of the PASS marker is the expected-failure state; once
    the plugin strips xfail (because the PASS line landed), the test
    runs and passes on its own merit.
    """
    return dedent(
        """
        from __future__ import annotations
        from pathlib import Path
        import pytest

        EVIDENCE = Path("docs/_archive/governance/wave-1-post-deploy-observation-plan.md")


        @pytest.mark.xfail(
            strict=True,
            reason="OBS-005 deploy not landed; 24h cooldown validation window has no post-deploy evidence yet.",
        )
        def test_obs005():
            text = EVIDENCE.read_text(encoding="utf-8")
            assert "OBS-005" in text
            assert "OBS-005 24h validation: PASS" in text


        @pytest.mark.xfail(
            strict=True,
            reason="MATCH-001 deploy not landed; 72h token-guard validation window has no post-deploy evidence yet.",
        )
        def test_match001():
            text = EVIDENCE.read_text(encoding="utf-8")
            assert "MATCH-001" in text
            assert "MATCH-001 72h validation: PASS" in text


        @pytest.mark.xfail(
            strict=True,
            reason="OBS-003 deploy not landed; 48h SKIPPED-emission validation window has no post-deploy evidence yet.",
        )
        def test_obs003():
            text = EVIDENCE.read_text(encoding="utf-8")
            assert "OBS-003" in text
            assert "OBS-003 48h validation: PASS" in text


        @pytest.mark.xfail(
            strict=True,
            reason="EXEC-002 deploy not landed; 7d series-correlation validation window has no post-deploy evidence yet.",
        )
        def test_exec002():
            text = EVIDENCE.read_text(encoding="utf-8")
            assert "EXEC-002" in text
            assert "EXEC-002 7d validation: PASS" in text
        """
    ) + extra


# ---------------------------------------------------------------------------
# Scenario 1 — no evidence file at all
# ---------------------------------------------------------------------------


def test_no_evidence_file_leaves_all_canaries_xfail(pytester: pytest.Pytester) -> None:
    """Fail-soft: a missing evidence file must not raise — the plugin
    simply leaves every wave-1 xfail-strict mark intact.
    """
    _install_repo_conftest(pytester)
    pytester.makepyfile(test_wave1=_canary_test_module())
    # Deliberately do NOT call _write_evidence — file is absent.
    result = pytester.runpytest("-q")
    result.assert_outcomes(xfailed=4)


# ---------------------------------------------------------------------------
# Scenario 2 — single-canary PASS line strips only that canary's xfail
# ---------------------------------------------------------------------------


def test_obs005_pass_line_closes_obs005_only(pytester: pytest.Pytester) -> None:
    """A PASS line for OBS-005 must remove that canary's xfail mark
    (so the test runs and passes on its own merit) and leave the other
    three wave-1 canaries xfail-strict.
    """
    _install_repo_conftest(pytester)
    pytester.makepyfile(test_wave1=_canary_test_module())
    _write_evidence(
        pytester,
        "Wave-1 closure log\n\nOBS-005 24h validation: PASS\n",
    )
    result = pytester.runpytest("-q")
    # 1 passed (OBS-005, since the evidence file now contains the marker),
    # 3 still xfailed (the other wave-1 canaries).
    result.assert_outcomes(passed=1, xfailed=3)


# ---------------------------------------------------------------------------
# Scenario 3 — all four canaries PASS
# ---------------------------------------------------------------------------


def test_all_four_canaries_pass_lines_close_all(pytester: pytest.Pytester) -> None:
    """When every wave-1 ticket has a PASS line, every xfail mark is
    stripped and every canary runs as a normal regression guard.
    """
    _install_repo_conftest(pytester)
    pytester.makepyfile(test_wave1=_canary_test_module())
    _write_evidence(
        pytester,
        dedent(
            """
            Wave-1 closure log

            OBS-005 24h validation: PASS
            MATCH-001 72h validation: PASS
            OBS-003 48h validation: PASS
            EXEC-002 7d validation: PASS
            """
        ),
    )
    result = pytester.runpytest("-q")
    result.assert_outcomes(passed=4)


# ---------------------------------------------------------------------------
# Scenario 4 — duration variations are tolerated by the regex
# ---------------------------------------------------------------------------


def test_duration_variations_are_tolerated(pytester: pytest.Pytester) -> None:
    """The plugin must accept any ``\\d+[hd]`` duration; the canary's
    *self-test* still requires the exact marker string from its source.
    We exercise the plugin-side regex here by writing each canary's
    expected duration AND adding extra "weird" PASS lines that should
    NOT collide with other tickets.
    """
    _install_repo_conftest(pytester)
    pytester.makepyfile(test_wave1=_canary_test_module())
    _write_evidence(
        pytester,
        dedent(
            """
            OBS-005 24h validation: PASS
            MATCH-001 72h validation: PASS
            OBS-003 48h validation: PASS
            EXEC-002 7d validation: PASS
            UNRELATED-TICKET 999h validation: PASS
            """
        ),
    )
    result = pytester.runpytest("-q")
    result.assert_outcomes(passed=4)


# ---------------------------------------------------------------------------
# Scenario 5 — whitespace + case tolerance on the PASS line
# ---------------------------------------------------------------------------


def test_whitespace_and_case_variations(pytester: pytest.Pytester) -> None:
    """Plugin regex is case-insensitive and tolerates extra whitespace
    between the ticket, the duration, and the PASS token. The canary
    self-tests still need the exact source-form marker, so we include
    that AND a sloppy duplicate; both should match the plugin regex.
    """
    _install_repo_conftest(pytester)
    pytester.makepyfile(test_wave1=_canary_test_module())
    _write_evidence(
        pytester,
        dedent(
            """
            obs-005    24h    validation:    pass
            OBS-005 24h validation: PASS
            """
        ),
    )
    result = pytester.runpytest("-q")
    # Only OBS-005 has its self-test marker satisfied; other three xfail.
    result.assert_outcomes(passed=1, xfailed=3)


# ---------------------------------------------------------------------------
# Scenario 6 — non-wave-1 xfail marks are never touched
# ---------------------------------------------------------------------------


def test_non_wave1_xfail_marks_are_preserved(pytester: pytest.Pytester) -> None:
    """An xfail with a reason that does NOT match the wave-1 pattern
    (no canary ticket, or no ``deploy not landed`` clause) must keep
    its xfail mark even when the evidence file contains PASS lines for
    every wave-1 canary.
    """
    _install_repo_conftest(pytester)
    extra = dedent(
        """

        @pytest.mark.xfail(strict=True, reason="totally unrelated bug; nothing to do with wave-1")
        def test_unrelated_xfail_must_remain():
            assert False  # would fail if unmarked -> xfail stays the only safe state


        @pytest.mark.xfail(
            strict=True,
            reason="OBS-005 deploy WAS landed but flaky on slow CI; keep xfail",
        )
        def test_obs005_no_deploy_clause_means_no_match():
            # Reason mentions OBS-005 but lacks 'deploy not landed' -> plugin
            # must NOT strip the xfail.
            assert False
        """
    )
    pytester.makepyfile(test_wave1=_canary_test_module(extra=extra))
    _write_evidence(
        pytester,
        dedent(
            """
            OBS-005 24h validation: PASS
            MATCH-001 72h validation: PASS
            OBS-003 48h validation: PASS
            EXEC-002 7d validation: PASS
            """
        ),
    )
    result = pytester.runpytest("-q")
    # 4 wave-1 canaries pass, 2 unrelated xfails remain xfailed.
    result.assert_outcomes(passed=4, xfailed=2)


# ---------------------------------------------------------------------------
# Scenario 7 — malformed evidence file does not crash collection
# ---------------------------------------------------------------------------


def test_malformed_evidence_file_does_not_crash_collection(pytester: pytest.Pytester) -> None:
    """The plugin must be fail-soft against any read error. We simulate
    a binary-garbage file (invalid UTF-8). If the plugin crashed, the
    pytest run would error rather than report xfailed outcomes; we
    assert the safe outcome (every canary still xfailed).
    """
    _install_repo_conftest(pytester)
    pytester.makepyfile(test_wave1=_canary_test_module())
    target = (
        pytester.path
        / "docs"
        / "_archive"
        / "governance"
        / "wave-1-post-deploy-observation-plan.md"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    # Write raw bytes that are not valid UTF-8.
    target.write_bytes(b"\xff\xfe\x00\x01garbage\xc3\x28more")
    result = pytester.runpytest("-q")
    result.assert_outcomes(xfailed=4)
