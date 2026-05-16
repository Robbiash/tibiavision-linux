"""Entry point: ``python -m tvlinux`` / ``tvlinux``."""

from __future__ import annotations

import argparse
import os
import signal
import sys

from . import __app_id__, __app_name__, __version__, app_icon_path
from .logging_config import configure_logging, get_logger


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="tvlinux",
        description=(
            f"{__app_name__} - a screen-mirroring overlay for Tibia on Linux. "
            "Defaults to the XWayland backend so mirrors can use X11 "
            "override-redirect and stay above a fullscreen/focused Tibia "
            "window regardless of compositor policy."
        ),
    )
    parser.add_argument("--version", action="version", version=f"{__app_name__} {__version__}")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Minimum log level.",
    )
    parser.add_argument(
        "--force-wayland",
        action="store_true",
        help=(
            "Use the native Wayland backend with wlr-layer-shell instead of "
            "the XWayland + override-redirect default. Requires a working "
            "layer-shell-qt install whose Qt ABI matches the PySide6 in use; "
            "if layer-shell-qt fails to load, mirrors fall back to xdg-shell "
            "and the compositor may stack them below a focused Tibia window."
        ),
    )
    parser.add_argument(
        "--force-x11",
        action="store_true",
        help=argparse.SUPPRESS,  # kept as an alias; xcb is already the default
    )
    parser.add_argument(
        "--no-portal",
        action="store_true",
        help=(
            "Skip the XDG ScreenCast portal. Diagnostic flag only -- no "
            "fallback capture pipeline is wired up, so the app will start "
            "without any live frames."
        ),
    )
    return parser.parse_args(argv)


def _select_qt_backend(force_wayland: bool, log) -> str:  # type: ignore[no-untyped-def]
    """Pick the Qt platform plugin and return the effective value.

    Rules:

    * If ``QT_QPA_PLATFORM`` is already set (tests use ``offscreen``,
      users may explicitly want ``wayland`` or ``xcb``), respect it
      verbatim -- never clobber an explicit user/tooling choice.
    * If ``--force-wayland`` is passed on a Wayland session, let Qt
      auto-select its Wayland plugin (do not set the env var) so the
      layer-shell integration in :mod:`tvlinux.layer_shell` can take
      over.
    * Otherwise default to ``xcb``. This is the critical change: on
      KDE Plasma 6 Wayland (and basically every major desktop), an
      xdg-toplevel cannot be reliably kept above a fullscreen/focused
      Win32-under-Wine game like Tibia, because compositor stacking is
      policy-driven and WindowStaysOnTopHint is a hint, not a
      guarantee. XWayland override-redirect bypasses the WM entirely
      and *is* a guarantee -- the same mechanism Discord/Steam/MangoHud
      use for their overlays.

    Tibia itself is a Wine app and therefore always runs on XWayland,
    so running our overlay on the same X server has no
    capture/latency downside.
    """
    existing = os.environ.get("QT_QPA_PLATFORM")
    if existing:
        log.info("backend.respecting_existing_qt_qpa_platform", value=existing)
        return existing

    if force_wayland and _is_wayland_session():
        log.info("backend.wayland_forced_by_flag")
        return "wayland"

    os.environ["QT_QPA_PLATFORM"] = "xcb"
    log.info("backend.xcb_selected_for_override_redirect")
    return "xcb"


def _is_wayland_session() -> bool:
    return (
        bool(os.environ.get("WAYLAND_DISPLAY"))
        or os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
    )


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(list(sys.argv[1:] if argv is None else argv))
    configure_logging(args.log_level)
    log = get_logger(__name__)
    log.info("starting", app_id=__app_id__, version=__version__)

    # --force-x11 is a legacy alias; xcb is already the default. The flag
    # still resolves to xcb for backwards compatibility with any launcher
    # scripts users may have in the wild.
    force_wayland = args.force_wayland and not args.force_x11

    backend = _select_qt_backend(force_wayland=force_wayland, log=log)

    # QtWebEngine (Chromium) needs GPU disabled + sandbox off inside
    # containerised environments (distrobox, flatpak, Docker). Without
    # --disable-gpu the renderer crashes on the host GPU stack; without
    # --no-sandbox Chromium's namespace sandbox fights the container's
    # own sandbox. We INTENTIONALLY leave SwiftShader enabled so
    # Chromium has a software rasterizer to fall back to; passing
    # --disable-software-rasterizer alongside --disable-gpu leaves the
    # renderer with no rasterizer at all and the whole compositor goes
    # black. setdefault so users can still override manually.
    os.environ.setdefault(
        "QTWEBENGINE_CHROMIUM_FLAGS",
        "--no-sandbox --disable-gpu",
    )

    # Import Qt lazily so --version / --help don't pay the startup cost,
    # and so _select_qt_backend above runs *before* any Qt module is
    # imported -- QT_QPA_PLATFORM is only honored at QGuiApplication
    # construction time.
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication, QIcon
    from PySide6.QtWidgets import QApplication

    QGuiApplication.setApplicationDisplayName(__app_name__)
    QApplication.setApplicationName(__app_name__)
    QApplication.setApplicationVersion(__version__)
    QApplication.setDesktopFileName(__app_id__)
    QApplication.setOrganizationName("tibiavision-linux")
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)

    # Only prepare layer-shell when the user has explicitly opted into the
    # Wayland backend. Qt's Wayland platform plugin reads
    # QT_WAYLAND_SHELL_INTEGRATION exactly once, at QGuiApplication
    # construction time, so this call must happen before QApplication(...)
    # below and is a no-op on xcb.
    layer_shell_ready = False
    if backend == "wayland" or (force_wayland and _is_wayland_session()):
        from .layer_shell import prepare_qt_integration

        layer_shell_ready = prepare_qt_integration()
        if layer_shell_ready:
            log.info("layer_shell.qt_integration_selected")
        else:
            log.warning("layer_shell.prepare_failed_fallback_xdg_shell")

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(QIcon(app_icon_path()))
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    from .app import Application

    tvlinux = Application(
        use_portal=not args.no_portal,
        overlay_backend=_describe_overlay_backend(
            backend=backend, layer_shell_ready=layer_shell_ready
        ),
    )
    tvlinux.start()
    return app.exec()


def _describe_overlay_backend(*, backend: str, layer_shell_ready: bool) -> str:
    """Human-readable label for the control-panel 'Overlay mode' line.

    Exposed so the UI can display one of the three meaningful states
    rather than making the user guess. The label is also emitted in the
    logs at INFO, so bug reports self-describe.
    """
    if backend == "xcb":
        return "XWayland (override-redirect)"
    if backend == "wayland" and layer_shell_ready:
        return "Wayland (layer-shell)"
    if backend == "wayland":
        return "Wayland (best-effort, stacking not guaranteed)"
    return f"custom ({backend})"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
