"""Smoke tests for the CLI entry point."""

from __future__ import annotations

import subprocess
import sys


def test_version_exits_zero():
    res = subprocess.run(
        [sys.executable, "-m", "tvlinux", "--version"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, res.stderr
    assert "TibiaVision-Linux" in res.stdout


def test_help_exits_zero():
    res = subprocess.run(
        [sys.executable, "-m", "tvlinux", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, res.stderr
    assert "screen-mirroring" in res.stdout
