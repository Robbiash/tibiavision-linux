"""wlr-layer-shell-v1 integration via LayerShellQt.

Promotes a :class:`QWidget` (specifically, a freshly-constructed
:class:`~tvlinux.mirror_window.MirrorWindow`) to the compositor's
**overlay** layer so the mirror visibly sits above fullscreen Tibia on
any compositor that implements the ``wlr-layer-shell-v1`` Wayland
protocol -- KDE Plasma 6 / KWin, Sway, Hyprland. On GNOME / Mutter,
X11 sessions, or anywhere the library is missing, :func:`is_available`
returns ``False`` and :func:`promote_to_overlay` is a no-op; the
caller's reactive "stay above Tibia" fallback (see
:meth:`tvlinux.app.Application._raise_all_mirrors`) takes over.

Why ctypes rather than a native extension module
------------------------------------------------
The Flatpak already ships on ``org.kde.Platform//6.7``, which bundles
``libLayerShellQtInterface.so.6`` out of the box. Loading it via
``ctypes`` keeps the packaging surface at "zero new dependencies"
while still giving us the one Wayland protocol we actually need. The
Itanium C++ ABI is standard on Linux (both GCC and Clang produce the
same mangled names), so the explicit symbol names below are portable
across toolchains for the library versions shipped by the KDE runtime.

If LayerShellQt ever breaks ABI or the symbols change, every entry
point here degrades gracefully to ``False`` / no-op and the rest of
the app keeps working via the Phase 1 re-raise path.
"""

from __future__ import annotations

import ctypes
import os
from ctypes import c_int, c_int32, c_uint, c_void_p
from typing import TYPE_CHECKING

from .logging_config import get_logger

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget

log = get_logger(__name__)


# LayerShellQt::Window::Layer
_LAYER_OVERLAY = 3  # above fullscreen windows by protocol design

# LayerShellQt::Window::KeyboardInteractivity
# None = the layer surface NEVER receives keyboard input. This is
# non-negotiable for an overlay that sits on top of Tibia: if the
# compositor routes keypresses to us, the user's WASD / spell hotkeys
# / function keys silently fail to reach the game. The mirror has no
# keyboard UX of its own worth the trade-off (Delete-to-remove still
# works from the control panel's region list, and from the mirror
# itself when it's unlocked -- at which point it is no longer
# input-transparent and Qt delivers key events normally through the
# xdg-toplevel path, not layer-shell).
_KB_NONE = 0

# Don't reserve screen real-estate; behave like a free-floating panel.
_EXCLUSIVE_ZONE_NONE = -1

# Soname candidates. The runtime name ``libLayerShellQtInterface.so.6`` is
# what ships in the KDE Platform 6.x Flatpak runtime and Fedora's
# ``kf6-layer-shell-qt`` package. The plain-soname fallback covers dev
# builds where only ``libLayerShellQtInterface.so`` is installed.
_SONAME_CANDIDATES = (
    "libLayerShellQtInterface.so.6",
    "libLayerShellQtInterface.so",
)

# Itanium C++ ABI mangled symbol names. Verified against
# ``nm -D /usr/lib64/libLayerShellQtInterface.so.6`` on Fedora 43
# (layer-shell-qt 6.6.4). The namespace "LayerShellQt" is 12 characters
# long, which is the source of all the "12" prefixes below.
_SYM_WINDOW_GET = "_ZN12LayerShellQt6Window3getEP7QWindow"
_SYM_WINDOW_SET_LAYER = "_ZN12LayerShellQt6Window8setLayerENS0_5LayerE"
_SYM_WINDOW_SET_EXCLUSIVE_ZONE = "_ZN12LayerShellQt6Window16setExclusiveZoneEi"
_SYM_WINDOW_SET_KB_INTERACTIVITY = (
    "_ZN12LayerShellQt6Window24setKeyboardInteractivityENS0_21KeyboardInteractivityE"
)
_SYM_SHELL_USE_LAYER_SHELL = "_ZN12LayerShellQt5Shell13useLayerShellEv"


class _LayerShellLib:
    """Lazy, idempotent loader + typed function pointer cache.

    Instantiated at most once per process via :meth:`load`. All ctypes
    ``argtypes`` / ``restype`` annotations are set eagerly so callers
    pass ordinary Python ``int``s and receive sensible return values
    instead of wrestling with raw ``c_void_p`` bookkeeping.
    """

    _instance: _LayerShellLib | None = None
    _load_failed: bool = False

    def __init__(self, lib: ctypes.CDLL) -> None:
        self._lib = lib

        self._window_get = lib[_SYM_WINDOW_GET]
        self._window_get.argtypes = [c_void_p]
        self._window_get.restype = c_void_p

        self._set_layer = lib[_SYM_WINDOW_SET_LAYER]
        self._set_layer.argtypes = [c_void_p, c_int]
        self._set_layer.restype = None

        self._set_exclusive_zone = lib[_SYM_WINDOW_SET_EXCLUSIVE_ZONE]
        self._set_exclusive_zone.argtypes = [c_void_p, c_int32]
        self._set_exclusive_zone.restype = None

        self._set_kb_interactivity = lib[_SYM_WINDOW_SET_KB_INTERACTIVITY]
        self._set_kb_interactivity.argtypes = [c_void_p, c_uint]
        self._set_kb_interactivity.restype = None

        self._use_layer_shell = lib[_SYM_SHELL_USE_LAYER_SHELL]
        self._use_layer_shell.argtypes = []
        self._use_layer_shell.restype = None

    @classmethod
    def load(cls) -> _LayerShellLib | None:
        if cls._instance is not None:
            return cls._instance
        if cls._load_failed:
            return None
        for soname in _SONAME_CANDIDATES:
            try:
                lib = ctypes.CDLL(soname, mode=ctypes.RTLD_GLOBAL)
            except OSError:
                continue
            try:
                cls._instance = cls(lib)
                log.info("layer_shell.loaded", soname=soname)
                return cls._instance
            except (AttributeError, KeyError, OSError) as e:
                log.warning("layer_shell.symbol_missing", soname=soname, error=str(e))
                cls._load_failed = True
                return None
        cls._load_failed = True
        return None


def _is_wayland_session() -> bool:
    """True iff we appear to be running on a Wayland display server.

    ``WAYLAND_DISPLAY`` is set by every Wayland session I've seen; we
    also check ``XDG_SESSION_TYPE`` for belt-and-suspenders. The
    ``wlr-layer-shell-v1`` protocol only exists on Wayland, so an X11
    or XWayland session must fall through to the reactive re-raise
    path regardless of whether the library loads.
    """
    if os.environ.get("WAYLAND_DISPLAY"):
        return True
    return os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"


_prepared: bool = False
_prepare_result: bool = False


def prepare_qt_integration() -> bool:
    """Select layer-shell as the Qt Wayland shell integration.

    **MUST be called before ``QGuiApplication`` is constructed.** Qt's
    Wayland platform plugin reads ``QT_WAYLAND_SHELL_INTEGRATION``
    exactly once, at ``QGuiApplication`` construction time, to pick
    between the default ``xdg-shell`` integration and the
    ``layer-shell`` one. After that, the choice is frozen for the
    life of the process; calling this function later is a guaranteed
    no-op, every QWindow will be an ``xdg-toplevel``, and
    ``LayerShellQt::Window::get()`` will silently return an object
    whose settings are never applied to any real Wayland surface.
    That was the bug in the first iteration of this module: the call
    happened from ``_create_mirror`` post-``QApplication``, so
    everything downstream silently no-op'd.

    Strategy:

    1. Bail if we're not on a Wayland session (the protocol doesn't
       apply anywhere else).
    2. Set ``QT_WAYLAND_SHELL_INTEGRATION=layer-shell`` unconditionally
       -- this is enough on its own for Qt's Wayland plugin to switch
       integrations, regardless of whether we can load our ctypes
       bindings. It's also what ``LayerShellQt::Shell::useLayerShell()``
       does internally in current upstream.
    3. Best-effort dlopen the LayerShellQt library so later
       ``promote_to_overlay`` calls can configure the layer/
       keyboard-interactivity via ``Window::get()``. If the library
       can't load (Qt version mismatch in a dev env, missing in a
       minimal container, etc.) Qt's shell-integration plugin will
       still try to link to it when the plugin loads at
       ``QApplication`` time; if the plugin also fails to load, Qt
       transparently falls back to ``xdg-shell``.

    Returns ``True`` iff Qt will use the layer-shell integration.
    Idempotent.
    """
    global _prepared, _prepare_result
    if _prepared:
        return _prepare_result
    _prepared = True

    if not _is_wayland_session():
        _prepare_result = False
        return False

    os.environ["QT_WAYLAND_SHELL_INTEGRATION"] = "layer-shell"

    lib = _LayerShellLib.load()
    if lib is not None:
        try:
            lib._use_layer_shell()
        except OSError as e:
            log.warning("layer_shell.use_layer_shell_failed", error=str(e))

    _prepare_result = True
    log.info("layer_shell.prepared", lib_loaded=lib is not None)
    return True


def is_available() -> bool:
    """Return ``True`` iff layer-shell promotion can be attempted.

    Requires that :func:`prepare_qt_integration` ran before
    ``QApplication`` construction **and** that the library is
    loadable so we can issue ``Window::get()`` calls.
    """
    if not _prepared or not _prepare_result:
        return False
    return _LayerShellLib.load() is not None


def promote_to_overlay(widget: QWidget) -> bool:
    """Promote ``widget`` to the layer-shell **overlay** layer.

    Precondition: :func:`prepare_qt_integration` was called before
    ``QApplication`` and returned ``True``. MUST be called **before**
    ``widget.show()``. The Wayland surface role is committed on first
    commit (which ``show()`` triggers); once committed it cannot be
    changed without tearing the surface down. Order of operations:

    1. Force the native QWindow to exist via ``winId()`` so
       ``windowHandle()`` returns a real object we can hand to the
       library. No Wayland surface is created yet at this point --
       Qt defers that to ``show()``.
    2. Fetch the raw ``QWindow*`` via shiboken6.
    3. Call ``LayerShellQt::Window::get(qwindow)`` to retrieve (and
       lazily allocate) the per-window configuration object.
    4. Configure it for a free-floating overlay panel: overlay layer,
       no anchors (anchors default to None which is what we want), no
       exclusive zone, ``OnDemand`` keyboard interactivity so
       Delete/Backspace in :meth:`MirrorWindow.keyPressEvent` still
       reaches Qt once the user clicks the mirror.

    Returns ``True`` on success. Returns ``False`` and logs at
    ``debug`` / ``warning`` level on any failure; callers should fall
    back to whatever they already do for "keep window on top" (for us,
    the reactive re-raise path in
    :meth:`tvlinux.app.Application._on_focus_window_changed`).
    """
    if not is_available():
        return False

    lib = _LayerShellLib.load()
    if lib is None:  # belt-and-suspenders; is_available() already checked
        return False

    # shiboken6 is a transitive dep of PySide6 so this import is free;
    # keep it lazy so that importing :mod:`tvlinux.layer_shell` doesn't
    # drag shiboken into the hot path when the module is never used.
    try:
        from shiboken6 import getCppPointer
    except ImportError as e:
        log.warning("layer_shell.shiboken_missing", error=str(e))
        return False

    # winId() forces creation of the QWindow without committing a
    # Wayland surface. Without this, windowHandle() is None until the
    # first show() and we'd have no pointer to configure.
    try:
        widget.winId()
    except RuntimeError as e:
        log.warning("layer_shell.winid_failed", error=str(e))
        return False

    qwindow = widget.windowHandle()
    if qwindow is None:
        log.warning("layer_shell.no_window_handle")
        return False

    try:
        qwindow_ptr = int(getCppPointer(qwindow)[0])
    except (TypeError, ValueError, RuntimeError) as e:
        log.warning("layer_shell.shiboken_ptr_failed", error=str(e))
        return False

    try:
        win = lib._window_get(c_void_p(qwindow_ptr))
        if not win:
            log.warning("layer_shell.window_get_null")
            return False
        lib._set_layer(c_void_p(win), _LAYER_OVERLAY)
        lib._set_exclusive_zone(c_void_p(win), _EXCLUSIVE_ZONE_NONE)
        lib._set_kb_interactivity(c_void_p(win), _KB_NONE)
    except OSError as e:
        log.warning("layer_shell.configure_failed", error=str(e))
        return False

    log.debug("layer_shell.promoted", qwindow=hex(qwindow_ptr))
    return True


__all__ = ["is_available", "prepare_qt_integration", "promote_to_overlay"]
