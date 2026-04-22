"""Donate dialog.

Vibrant, frameless dialog asking the user to send Tibia Coins to
``Sydnee Sweeney`` in-game. No network, no payment flow - it's purely
visual + a clipboard helper for the character name.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPaintEvent,
)
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

CHARACTER_NAME = "Sydnee Sweeney"


class DonateDialog(QDialog):
    """Frameless rounded dialog with a gradient background."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Donate")
        self.setModal(True)
        self.setFixedSize(480, 360)
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(36, 32, 36, 28)
        root.setSpacing(14)

        heart = QLabel("\u2665", self)
        heart_font = QFont()
        heart_font.setPointSize(36)
        heart.setFont(heart_font)
        heart.setStyleSheet("color: #ff7eb9;")
        heart.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(heart)

        headline = QLabel("Enjoying TibiaVision-Linux?", self)
        headline_font = QFont()
        headline_font.setPointSize(20)
        headline_font.setBold(True)
        headline.setFont(headline_font)
        headline.setStyleSheet("color: white;")
        headline.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(headline)

        subhead = QLabel("Send Tibia Coins in-game to:", self)
        subhead_font = QFont()
        subhead_font.setPointSize(11)
        subhead.setFont(subhead_font)
        subhead.setStyleSheet("color: rgba(255,255,255,0.8);")
        subhead.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(subhead)

        badge = QLabel(CHARACTER_NAME, self)
        badge_font = QFont()
        badge_font.setPointSize(18)
        badge_font.setBold(True)
        badge.setFont(badge_font)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setStyleSheet(
            "QLabel {"
            " color: #2a1500;"
            " background: qlineargradient(x1:0, y1:0, x2:1, y2:0,"
            "   stop:0 #ffd86b, stop:1 #ffb23a);"
            " border-radius: 18px;"
            " padding: 8px 24px;"
            "}"
        )
        badge_row = QHBoxLayout()
        badge_row.addStretch(1)
        badge_row.addWidget(badge)
        badge_row.addStretch(1)
        root.addLayout(badge_row)

        self._copy_btn = QPushButton("Copy character name", self)
        self._copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._copy_btn.setStyleSheet(
            "QPushButton {"
            " color: white;"
            " background: rgba(255,255,255,0.12);"
            " border: 1px solid rgba(255,255,255,0.35);"
            " border-radius: 10px;"
            " padding: 8px 18px;"
            " font-size: 12px;"
            "}"
            "QPushButton:hover { background: rgba(255,255,255,0.22); }"
        )
        self._copy_btn.clicked.connect(self._on_copy)
        copy_row = QHBoxLayout()
        copy_row.addStretch(1)
        copy_row.addWidget(self._copy_btn)
        copy_row.addStretch(1)
        root.addLayout(copy_row)

        footer = QLabel("Thank you for keeping Linux Tibia alive.", self)
        footer.setStyleSheet("color: rgba(255,255,255,0.6); font-size: 11px;")
        footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(footer)

        close_btn = QPushButton("Close", self)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(
            "QPushButton {"
            " color: rgba(255,255,255,0.7);"
            " background: transparent;"
            " border: none;"
            " font-size: 12px;"
            "}"
            "QPushButton:hover { color: white; }"
        )
        close_btn.clicked.connect(self.accept)
        close_row = QHBoxLayout()
        close_row.addStretch(1)
        close_row.addWidget(close_btn)
        close_row.addStretch(1)
        root.addLayout(close_row)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        path = QPainterPath()
        path.addRoundedRect(self.rect().adjusted(0, 0, -1, -1), 16, 16)

        gradient = QLinearGradient(0, 0, 0, self.height())
        gradient.setColorAt(0.0, QColor("#1a1030"))
        gradient.setColorAt(1.0, QColor("#3a0d4a"))

        painter.setPen(Qt.PenStyle.NoPen)
        painter.fillPath(path, gradient)

    def _on_copy(self) -> None:
        clipboard = QApplication.clipboard()
        if clipboard is None:
            return
        clipboard.setText(CHARACTER_NAME)
        original_text = "Copy character name"
        self._copy_btn.setText("Copied!")
        QTimer.singleShot(1500, lambda: self._copy_btn.setText(original_text))
