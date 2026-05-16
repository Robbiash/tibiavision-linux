"""Quest Browser page — embeds the tibia-reborn web app in a QWebEngineView."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, QUrl
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..tibia_reborn_server import TibiaRebornServer
from ..ui_helpers import muted_label

# Deliberately NOT importing QtWebEngineWidgets at module level:
# the import alone spawns the Chromium subsystem, and on a broken
# GPU stack inside distrobox that crash propagates to the whole Qt
# compositor and blanks ShellWindow. Defer until showEvent.

__all__ = ["QuestBrowserPage"]


class QuestBrowserPage(QWidget):
    """Full-page web view that loads tibia-reborn once the dev server is ready."""

    def __init__(self, server: TibiaRebornServer, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._server = server
        self._started = False
        self._view: Any | None = None
        self._web_engine_unavailable = False

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)

        # Status bar at the top (hidden once loaded)
        self._status_bar = self._build_status_bar()
        self._layout.addWidget(self._status_bar)

        # Stacked: loading placeholder vs live web view (view added lazily)
        self._stack = QStackedWidget(self)
        self._placeholder = self._build_placeholder()
        self._stack.addWidget(self._placeholder)
        self._layout.addWidget(self._stack, 1)

        # Wire server signals
        server.ready.connect(self._on_server_ready)
        server.failed.connect(self._on_server_failed)

    def _ensure_view(self) -> bool:
        """Lazily build the QWebEngineView. Returns False if WebEngine isn't usable."""
        if self._view is not None:
            return True
        if self._web_engine_unavailable:
            return False
        try:
            from PySide6.QtWebEngineWidgets import QWebEngineView
        except ImportError:
            self._web_engine_unavailable = True
            self._show_unavailable()
            return False
        self._view = QWebEngineView(self)
        self._view.setUrl(QUrl("about:blank"))
        self._stack.addWidget(self._view)
        return True

    def _show_unavailable(self) -> None:
        unavailable = self._build_unavailable_state()
        self._stack.addWidget(unavailable)
        self._stack.setCurrentWidget(unavailable)
        self._status_label.setText("Quest Browser unavailable")

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

        self._reload_btn = QPushButton("Reload", bar)
        self._reload_btn.setProperty("variant", "ghost")
        self._reload_btn.setFixedHeight(26)
        self._reload_btn.clicked.connect(self._reload)
        self._reload_btn.hide()
        row.addWidget(self._reload_btn)

        self._open_btn = QPushButton("Open in browser", bar)
        self._open_btn.setProperty("variant", "ghost")
        self._open_btn.setFixedHeight(26)
        self._open_btn.clicked.connect(self._open_external)
        self._open_btn.hide()
        row.addWidget(self._open_btn)

        return bar

    def _build_placeholder(self) -> QWidget:
        w = QWidget(self)
        col = QVBoxLayout(w)
        col.setAlignment(Qt.AlignmentFlag.AlignCenter)
        col.setSpacing(12)

        self._placeholder_label = QLabel("Starting tibia-reborn quest browser...", w)
        self._placeholder_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder_label.setStyleSheet("font-size: 14pt;")
        col.addWidget(self._placeholder_label)

        self._retry_btn = QPushButton("Retry", w)
        self._retry_btn.setProperty("variant", "primary")
        self._retry_btn.setFixedWidth(120)
        self._retry_btn.hide()
        self._retry_btn.clicked.connect(self._retry)
        col.addWidget(self._retry_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        return w

    def _build_unavailable_state(self) -> QWidget:
        w = QWidget(self)
        col = QVBoxLayout(w)
        col.setAlignment(Qt.AlignmentFlag.AlignCenter)
        col.setSpacing(8)
        title = QLabel("Quest Browser unavailable", w)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 14pt; font-weight: bold;")
        col.addWidget(title)
        sub = muted_label(
            "PySide6-WebEngine is not installed.\nRun: pip install PySide6-WebEngine",
            w,
        )
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        col.addWidget(sub)
        return w

    # -- Server signal handlers -----------------------------------------------

    def _on_server_ready(self) -> None:
        if not self._ensure_view():
            return
        self._status_label.setText(f"Quest browser — {self._server.url}")
        self._reload_btn.show()
        self._open_btn.show()
        assert self._view is not None
        self._view.setUrl(QUrl(self._server.url))
        self._stack.setCurrentWidget(self._view)

    def _on_server_failed(self, reason: str) -> None:
        self._placeholder_label.setText(f"Quest browser unavailable:\n{reason}")
        self._placeholder_label.setStyleSheet("font-size: 11pt; color: #e53935;")
        self._retry_btn.show()
        self._status_label.setText("Server failed to start")
        self._stack.setCurrentWidget(self._placeholder)

    # -- Button handlers -------------------------------------------------------

    def _reload(self) -> None:
        if self._view is not None:
            self._view.reload()

    def _open_external(self) -> None:
        from PySide6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl(self._server.url))

    def _retry(self) -> None:
        self._placeholder_label.setText("Starting tibia-reborn quest browser...")
        self._placeholder_label.setStyleSheet("font-size: 14pt;")
        self._retry_btn.hide()
        self._status_label.setText("Starting tibia-reborn...")
        self._server.start()

    # -- Lazy start -----------------------------------------------------------

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        if not self._started:
            self._started = True
            self._server.start()
