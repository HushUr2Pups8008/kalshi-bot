"""Launchd stdout and stderr stay bounded. Five archives, then the oldest drops."""

from __future__ import annotations

from pathlib import Path

from scripts.rotate_launchd_logs import rotate_file


def test_small_log_stays_in_place(tmp_path: Path) -> None:
    path = tmp_path / "politics.launchd.stderr.log"
    path.write_bytes(b"boot\n")
    assert rotate_file(path, max_bytes=20, keep=5) is False
    assert path.read_bytes() == b"boot\n"
    assert not Path(f"{path}.1").exists()


def test_large_log_is_copied_then_truncated_and_keeps_five(tmp_path: Path) -> None:
    path = tmp_path / "grok_paper.launchd.stderr.log"
    for generation in range(6):
        path.write_bytes(f"gen-{generation}\n".encode("ascii") * 5)
        assert rotate_file(path, max_bytes=10, keep=5) is True
        assert path.read_bytes() == b""
    archives = [Path(f"{path}.{index}").read_bytes() for index in range(1, 6)]
    assert archives[0].startswith(b"gen-5")
    assert archives[4].startswith(b"gen-1")
    assert not Path(f"{path}.6").exists()


def test_missing_log_is_a_no_op(tmp_path: Path) -> None:
    assert rotate_file(tmp_path / "missing.launchd.stdout.log", max_bytes=10, keep=5) is False
