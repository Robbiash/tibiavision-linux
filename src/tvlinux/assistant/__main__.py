"""Entry point for `python -m tvlinux.assistant` / `tvassistant`."""

from __future__ import annotations

import argparse
import signal
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtWidgets import QApplication

from .. import app_icon_path
from . import __assistant_app_id__, __assistant_app_name__
from .ui import AssistantWindow


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="tvassistant",
        description=(
            "Desktop Grok-style voice assistant with local memory.\n"
            "Configure API key via XAI_API_KEY env var or the in-app connection panel."
        ),
    )
    parser.add_argument("--version", action="version", version="tvassistant 0.1.0")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    _parse_args(list(sys.argv[1:] if argv is None else argv))
    QGuiApplication.setApplicationDisplayName(__assistant_app_name__)
    QApplication.setApplicationName(__assistant_app_name__)
    QApplication.setDesktopFileName(__assistant_app_id__)
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)
    app.setWindowIcon(QIcon(app_icon_path()))
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    window = AssistantWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
