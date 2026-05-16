"""Quest Browser page — launches the tibia-reborn web app in the user's browser.

The earlier Phase 4 design embedded the Next.js app in a QWebEngineView. On a
distrobox where the host GPU stack isn't exposed, Chromium falls back to
SwiftShader software rendering, which combined with ``next dev``'s on-demand
compilation pegs the CPU and feels like the machine is crashing. We keep the
``TibiaRebornServer`` background process so the user still only sees one app
to launch, but the actual page is opened in their default browser where it
gets host-GPU rendering for free.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..tibia_reborn_server import TibiaRebornServer

__all__ = ["QuestBrowserPage"]


class QuestBrowserPage(QWidget):
    """Status page that runs tibia-reborn in the background and opens it externally."""

    def __init__(self, server: TibiaRebornServer, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._server = server
        self._started = False
        self._auto_opened = False

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)

        self._status_bar = self._build_status_bar()
        self._layout.addWidget(self._status_bar)

        self._body = self._build_body()
        self._layout.addWidget(self._body, 1)

        server.ready.connect(self._on_server_ready)
        server.failed.connect(self._on_server_failed)

    # -- UI builders ----------------------------------------------------------

    def _build_status_bar(self) -> QWidget:
        bar = QWidget(self)
        bar.setObjectName("QuestBrowserStatusBar")
        bar.setFixedHeight(36)
        row = QHBoxLayout(bar)
        row.setContentsMargins(12, 0, 12, 0)
        row.setSpacing(8)

        self._status_label = QLabel("Starting tibia-reborn...", bar)
        self._status_label.setProperty("role", "muted")
        row.addWidget(self._status_label, 1)

        self._restart_btn = QPushButton("Restart server", bar)
        self._restart_btn.setProperty("variant", "ghost")
        self._restart_btn.setFixedHeight(26)
        self._restart_btn.clicked.connect(self._restart)
        self._restart_btn.hide()
        row.addWidget(self._restart_btn)

        return bar

    def _build_body(self) -> QWidget:
        w = QWidget(self)
        col = QVBoxLayout(w)
        col.setAlignment(Qt.AlignmentFlag.AlignCenter)
        col.setSpacing(14)

        self._headline = QLabel("Starting tibia-reborn quest browser...", w)
        self._headline.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._headline.setStyleSheet("font-size: 14pt;")
        col.addWidget(self._headline)

        self._subline = QLabel("", w)
        self._subline.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._subline.setStyleSheet("font-size: 11pt; color: #9aa0a6;")
        self._subline.setWordWrap(True)
        col.addWidget(self._subline)

        self._open_btn = QPushButton("Open in browser", w)
        self._open_btn.setProperty("variant", "primary")
        self._open_btn.setFixedWidth(180)
        self._open_btn.clicked.connect(self._open_external)
        self._open_btn.setEnabled(False)
        col.addWidget(self._open_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        self._retry_btn = QPushButton("Retry", w)
        self._retry_btn.setProperty("variant", "ghost")
        self._retry_btn.setFixedWidth(120)
        self._retry_btn.hide()
        self._retry_btn.clicked.connect(self._retry)
        col.addWidget(self._retry_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        return w

    # -- Server signal handlers -----------------------------------------------

    def _on_server_ready(self) -> None:
        self._status_label.setText(f"Quest browser ready — {self._server.url}")
        self._restart_btn.show()
        self._headline.setText("Quest browser is ready.")
        self._subline.setText(
            f"Opens in your default browser at {self._server.url}.\n"
            "Tibia-reborn keeps running in the background while TibiaVision is open."
        )
        self._open_btn.setEnabled(True)
        self._retry_btn.hide()
        if not self._auto_opened:
            self._auto_opened = True
            self._open_external()

    def _on_server_failed(self, reason: str) -> None:
        self._status_label.setText("Server failed to start")
        self._headline.setText("Quest browser unavailable")
        self._headline.setStyleSheet("font-size: 14pt; color: #e53935;")
        self._subline.setText(reason)
        self._open_btn.setEnabled(False)
        self._retry_btn.show()

    # -- Button handlers -------------------------------------------------------

    def _open_external(self) -> None:
        QDesktopServices.openUrl(QUrl(self._server.url))

    def _retry(self) -> None:
        self._headline.setText("Starting tibia-reborn quest browser...")
        self._headline.setStyleSheet("font-size: 14pt;")
        self._subline.setText("")
        self._retry_btn.hide()
        self._status_label.setText("Starting tibia-reborn...")
        self._auto_opened = False
        self._server.start()

    def _restart(self) -> None:
        self._auto_opened = False
        self._server.stop()
        self._headline.setText("Restarting tibia-reborn...")
        self._headline.setStyleSheet("font-size: 14pt;")
        self._subline.setText("")
        self._status_label.setText("Restarting tibia-reborn...")
        self._open_btn.setEnabled(False)
        self._server.start()

    # -- Lazy start -----------------------------------------------------------

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        if not self._started:
            self._started = True
            self._server.start()
