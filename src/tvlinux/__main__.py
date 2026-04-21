"""Entry point: ``python -m tvlinux`` / ``tvlinux``."""

from __future__ import annotations

import argparse
import signal
import sys

from . import __app_id__, __app_name__, __version__
from .logging_config import configure_logging, get_logger


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="tvlinux",
        description=f"{__app_name__} - a Wayland-native screen-mirroring overlay for Tibia.",
    )
    parser.add_argument("--version", action="version", version=f"{__app_name__} {__version__}")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Minimum log level.",
    )
    parser.add_argument(
        "--force-x11",
        action="store_true",
        help="Force Qt to use the X11/XWayland backend (for debugging portals).",
    )
    parser.add_argument(
        "--no-portal",
        action="store_true",
        help="Skip the XDG ScreenCast portal and use QScreen.grabWindow fallback.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(list(sys.argv[1:] if argv is None else argv))
    configure_logging(args.log_level)
    log = get_logger(__name__)
    log.info("starting", app_id=__app_id__, version=__version__)

    # Import Qt lazily so --version / --help don't pay the startup cost.
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication

    QGuiApplication.setApplicationDisplayName(__app_name__)
    QApplication.setApplicationName(__app_name__)
    QApplication.setApplicationVersion(__version__)
    QApplication.setDesktopFileName(__app_id__)
    QApplication.setOrganizationName("tibiavision-linux")
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)

    if args.force_x11:
        import os

        os.environ["QT_QPA_PLATFORM"] = "xcb"

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    from .app import Application

    tvlinux = Application(use_portal=not args.no_portal)
    tvlinux.start()
    return app.exec()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
