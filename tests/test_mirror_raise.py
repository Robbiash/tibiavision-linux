"""Tests for the "raise new mirror above the game" logic in app.py.

Instantiating the full :class:`tvlinux.app.Application` from a unit test
is expensive -- it wires up portals, capture threads, and IO -- so we
don't. Instead we pull the raise-dance method off the class and bind
it to a tiny stub that exposes only what the method needs:
``_mirrors`` (dict of id -> mirror) and ``_pending_raise_ids``.

This keeps the test surface small and deterministic: we can assert
exactly how many times ``raise_``/``activateWindow`` were called and
at which deferred stages, which is what the bug fix promises.
"""

from __future__ import annotations

from uuid import uuid4

from PySide6.QtCore import QTimer

from tvlinux.app import Application


class _FakeWindow:
    """Marker object returned by focusWindowChanged to represent a
    focus target that *belongs to our app*. The handler under test
    only cares about ``None`` vs not-``None``, so this can be opaque."""


class _FakeMirror:
    """Stand-in for MirrorWindow exposing just the methods we assert on."""

    def __init__(self) -> None:
        self.raise_calls = 0
        self.activate_calls = 0
        self._visible = True

    def isVisible(self) -> bool:
        return self._visible

    def raise_(self) -> None:
        self.raise_calls += 1

    def activateWindow(self) -> None:
        self.activate_calls += 1


class _Stub:
    """Minimal receiver for ``Application._raise_mirror_above_game`` &
    friends. We bind the internal helpers (``_reraise_sequence``,
    ``_raise_all_mirrors``) as methods on this stub so when the
    public entry points call them on ``self`` we exercise the real
    logic and not mocks. This mirrors how ``Application`` composes
    internally and lets us keep the test surface tiny."""

    def __init__(self, mirror: _FakeMirror, region_id) -> None:
        self._mirrors = {region_id: mirror}
        self._pending_raise_ids: set = {region_id}
        # Mirror ids promoted to a wlr-layer-shell overlay surface;
        # _raise_all_mirrors skips these. Empty here: we test the
        # pre-promotion (xdg-shell) path by default.
        self._layer_shell_promoted: set = set()

    # Bound to the real implementations so that _raise_mirror_above_game
    # and _on_focus_window_changed find them on ``self``. Using the
    # class-level method descriptors keeps the stub self-contained
    # and avoids pulling in Application.__init__ (which wires portals,
    # capture threads, signals, etc.).
    _reraise_sequence = Application._reraise_sequence
    _raise_all_mirrors = Application._raise_all_mirrors


def _wait(qapp, ms: int) -> None:
    """Spin the event loop for ``ms`` milliseconds so QTimer fires."""
    loop_done = [False]
    QTimer.singleShot(ms, lambda: loop_done.__setitem__(0, True))
    while not loop_done[0]:
        qapp.processEvents()


def test_raise_mirror_above_game_fires_three_times(qapp):
    region_id = uuid4()
    mirror = _FakeMirror()
    stub = _Stub(mirror, region_id)

    # Bind the unbound method to our stub. We use the class attribute
    # rather than a live Application instance because this function is
    # self-contained -- it only reads/writes the two attributes set on
    # the stub.
    Application._raise_mirror_above_game(stub, region_id)

    # Immediate raise happens synchronously.
    assert mirror.raise_calls == 1
    assert mirror.activate_calls == 1

    # After the event loop next idles, the 0 ms singleShot runs.
    _wait(qapp, 20)
    assert mirror.raise_calls == 2
    assert mirror.activate_calls == 2

    # After the 250 ms delay the final re-raise runs and the pending
    # id is cleared so we don't leak stale state.
    _wait(qapp, 300)
    assert mirror.raise_calls == 3
    assert mirror.activate_calls == 3
    assert region_id not in stub._pending_raise_ids


def test_raise_mirror_above_game_stops_if_mirror_gone(qapp):
    """If the user deletes the region before the deferred raises run,
    we must not blow up accessing a missing mirror."""
    region_id = uuid4()
    mirror = _FakeMirror()
    stub = _Stub(mirror, region_id)

    Application._raise_mirror_above_game(stub, region_id)
    assert mirror.raise_calls == 1

    # Simulate "mirror gone" between immediate raise and deferred ones.
    del stub._mirrors[region_id]

    _wait(qapp, 300)
    # No additional raises should be recorded on the now-orphaned mirror.
    assert mirror.raise_calls == 1
    # The final handler still clears the pending id so later picker
    # uses don't inherit stale bookkeeping.
    assert region_id not in stub._pending_raise_ids


def test_raise_mirror_above_game_skips_hidden_mirror(qapp):
    """A hidden mirror (``isVisible() is False``) should not be raised
    on the deferred passes -- the user explicitly asked for it to be
    hidden and we must respect that."""
    region_id = uuid4()
    mirror = _FakeMirror()
    stub = _Stub(mirror, region_id)

    Application._raise_mirror_above_game(stub, region_id)
    mirror._visible = False  # hidden before deferred passes fire

    _wait(qapp, 300)
    # Only the immediate raise counts -- the 0 ms and 250 ms callbacks
    # see a hidden window and do nothing.
    assert mirror.raise_calls == 1
    assert region_id not in stub._pending_raise_ids


# -- Focus-change reactive re-raise ----------------------------------------


def test_focus_leaving_app_re_raises_all_visible_mirrors(qapp):
    """When the user clicks into Tibia, Qt reports
    ``focusWindowChanged(None)`` because no window in *our* process
    holds focus anymore. That's the cue to push every visible mirror
    back up; hidden mirrors must be skipped."""
    shown = _FakeMirror()
    hidden = _FakeMirror()
    hidden._visible = False

    stub = _Stub(shown, uuid4())
    stub._mirrors[uuid4()] = hidden

    # Focus went away from our app.
    Application._on_focus_window_changed(stub, None)

    # Immediate raise on visible mirrors only.
    assert shown.raise_calls == 1
    assert shown.activate_calls == 1
    assert hidden.raise_calls == 0
    assert hidden.activate_calls == 0

    # Let the deferred 0 ms + 250 ms stages run.
    _wait(qapp, 300)
    assert shown.raise_calls == 3
    assert shown.activate_calls == 3
    assert hidden.raise_calls == 0


def test_focus_staying_in_app_does_not_re_raise(qapp):
    """If Qt reports a non-None focus window, focus is still inside
    our app (the user clicked a mirror, the control panel, or the
    picker). Re-raising in that case would fight the user's own
    click gesture, so the handler must be a no-op."""
    mirror = _FakeMirror()
    stub = _Stub(mirror, uuid4())

    Application._on_focus_window_changed(stub, _FakeWindow())

    assert mirror.raise_calls == 0
    assert mirror.activate_calls == 0

    _wait(qapp, 300)
    assert mirror.raise_calls == 0


def test_application_active_state_re_raises_all_visible_mirrors(qapp):
    """Alt-tabbing back into our app (``ApplicationActive``) should
    also pull mirrors forward. Other state transitions are ignored."""
    from PySide6.QtCore import Qt

    shown = _FakeMirror()
    stub = _Stub(shown, uuid4())

    # Non-active transitions are a no-op.
    Application._on_app_state_changed(stub, Qt.ApplicationState.ApplicationInactive)
    assert shown.raise_calls == 0

    # Active transition triggers the full sequence.
    Application._on_app_state_changed(stub, Qt.ApplicationState.ApplicationActive)
    assert shown.raise_calls == 1
    _wait(qapp, 300)
    assert shown.raise_calls == 3


def test_layer_shell_promoted_mirrors_are_skipped_on_re_raise(qapp):
    """Mirrors that were successfully promoted to a layer-shell overlay
    surface must NOT be re-raised: the protocol already guarantees
    they sit above fullscreen windows, and raise_()/activateWindow()
    on a layer-shell surface is a protocol-level no-op on most
    compositors (wasted work, possibly noisy in logs)."""
    promoted = _FakeMirror()
    vanilla = _FakeMirror()

    promoted_id = uuid4()
    vanilla_id = uuid4()

    stub = _Stub(promoted, promoted_id)
    stub._mirrors[vanilla_id] = vanilla
    stub._layer_shell_promoted.add(promoted_id)

    Application._on_focus_window_changed(stub, None)
    _wait(qapp, 300)

    # The promoted mirror is never raised.
    assert promoted.raise_calls == 0
    assert promoted.activate_calls == 0
    # The vanilla mirror goes through the full 3-stage dance.
    assert vanilla.raise_calls == 3
    assert vanilla.activate_calls == 3
