"""Dark theme (Qt stylesheet).

Kept as a single string so it's trivial to override or ship as a user-editable
``style.qss`` in a future release.
"""

from __future__ import annotations

DARK_QSS = """
* {
    font-family: "Inter", "Noto Sans", "Segoe UI", sans-serif;
}

QWidget {
    background-color: #181a1f;
    color: #e6e6e6;
}

QMainWindow, QDialog {
    background-color: #15171c;
}

QLabel {
    background: transparent;
}

QPushButton {
    background-color: #252832;
    color: #e6e6e6;
    border: 1px solid #2f333f;
    border-radius: 8px;
    padding: 6px 14px;
    min-height: 18px;
}
QPushButton:hover { background-color: #2d313d; }
QPushButton:pressed { background-color: #1c1e26; }
QPushButton:disabled {
    color: #666;
    background-color: #1b1d24;
    border-color: #232632;
}
QPushButton[accent="true"] {
    background-color: #0f8fbf;
    border-color: #0d7ca8;
    color: white;
}
QPushButton[accent="true"]:hover { background-color: #15a5d8; }
QPushButton[accent="true"]:pressed { background-color: #0b6a8f; }
QPushButton[danger="true"] {
    background-color: #b83d3d;
    border-color: #8c2a2a;
    color: white;
}
QPushButton[danger="true"]:hover { background-color: #d14848; }

QListWidget, QListView, QTreeView {
    background-color: #1d2029;
    border: 1px solid #272a35;
    border-radius: 8px;
    padding: 4px;
}
QListWidget::item {
    padding: 6px 8px;
    border-radius: 6px;
}
QListWidget::item:selected {
    background-color: #0f8fbf;
    color: white;
}
QListWidget::item:hover:!selected {
    background-color: #262a35;
}

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QTextEdit {
    background-color: #1d2029;
    border: 1px solid #2a2d38;
    border-radius: 6px;
    padding: 4px 8px;
    selection-background-color: #0f8fbf;
}
QComboBox::drop-down { border: 0; width: 20px; }

QSlider::groove:horizontal {
    height: 6px;
    background: #2a2d38;
    border-radius: 3px;
}
QSlider::handle:horizontal {
    background: #0f8fbf;
    width: 14px;
    height: 14px;
    margin: -5px 0;
    border-radius: 7px;
}
QSlider::sub-page:horizontal { background: #0f8fbf; border-radius: 3px; }

QCheckBox { spacing: 6px; }
QCheckBox::indicator {
    width: 16px; height: 16px;
    border: 1px solid #3a3e4a;
    border-radius: 4px;
    background: #1d2029;
}
QCheckBox::indicator:checked {
    background: #0f8fbf;
    border-color: #0f8fbf;
    image: none;
}

QMenu {
    background-color: #1d2029;
    border: 1px solid #272a35;
    padding: 4px;
}
QMenu::item {
    padding: 6px 16px;
    border-radius: 4px;
}
QMenu::item:selected { background-color: #0f8fbf; color: white; }

QStatusBar {
    background-color: #13151a;
    color: #9aa0b0;
}

QToolTip {
    background-color: #1d2029;
    color: #e6e6e6;
    border: 1px solid #2a2d38;
    padding: 4px 6px;
}

QGroupBox {
    border: 1px solid #272a35;
    border-radius: 8px;
    margin-top: 10px;
    padding-top: 12px;
    font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
    color: #b0b6c8;
}
"""


def apply(app) -> None:  # type: ignore[no-untyped-def]
    app.setStyleSheet(DARK_QSS)
