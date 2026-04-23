"""HuntRefresher rate-limiting and gating semantics."""

from __future__ import annotations

from pathlib import Path

from tvlinux.hunt_mode import CopyAnchor, HuntModeManager
from tvlinux.hunt_refresh import HuntRefresher


def _make(tmp_path: Path, *, active: bool = True, interval: int = 60):
    mgr = HuntModeManager(path=tmp_path / "m.json")
    mgr.update_config(
        active=active,
        min_refresh_interval_sec=interval,
        copy_anchor_hunt=CopyAnchor(100, 200, 100, 240),
    )
    calls: list[list[str]] = []

    def fake_run(cmd, *_a, **_k):  # type: ignore[no-untyped-def]
        calls.append(list(cmd))

        class R:
            returncode = 0

        return R()

    r = HuntRefresher(mgr, _subprocess_run=fake_run)
    # Force tool availability so unit tests never hit subprocess detection.
    r._status = type(r._status)(tool="xdotool", available=True)  # type: ignore[assignment]
    return mgr, r, calls


def test_fire_once_skipped_when_mode_off(tmp_path: Path, qapp) -> None:
    _mgr, r, calls = _make(tmp_path, active=False)
    skips: list[tuple[str, str]] = []
    r.skipped.connect(lambda rr, why: skips.append((rr, why)))
    assert r.fire_once("test") is False
    assert calls == []
    assert skips and skips[0][1] == "hunt_mode_off"


def test_fire_once_fires_then_rate_limits(tmp_path: Path, qapp) -> None:
    _mgr, r, calls = _make(tmp_path, active=True, interval=60)
    fired: list[tuple[str, float]] = []
    r.fired.connect(lambda rr, ts: fired.append((rr, ts)))

    assert r.fire_once("k1") is True
    assert calls  # xdotool was called at least once
    assert fired
    calls.clear()

    # Immediately after, it should rate-limit.
    assert r.fire_once("k2") is False
    assert calls == []


def test_fire_once_respects_zero_interval(tmp_path: Path, qapp) -> None:
    _mgr, r, _calls = _make(tmp_path, active=True, interval=0)
    assert r.fire_once("a") is True
    assert r.fire_once("b") is True  # no rate limit when interval is 0


def test_fire_once_skips_without_anchor(tmp_path: Path, qapp) -> None:
    mgr = HuntModeManager(path=tmp_path / "m.json")
    mgr.update_config(active=True, min_refresh_interval_sec=60)
    r = HuntRefresher(mgr, _subprocess_run=lambda *_a, **_k: None)
    r._status = type(r._status)(tool="xdotool", available=True)  # type: ignore[assignment]
    skips: list[tuple[str, str]] = []
    r.skipped.connect(lambda rr, why: skips.append((rr, why)))
    assert r.fire_once("n") is False
    assert skips and skips[0][1] == "no_anchor"
