"""About / Safety dialog.

Mirrors TibiaVision's "Official Statement" page: a one-glance explanation of what the
app does and doesn't do, with a link to the full safety document.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from . import __app_name__, __version__

_STATEMENT = """
<h2>{name}</h2>
<p><b>Version {version}</b></p>

<p><b>Official Statement</b></p>
<p>
  TibiaVision-Linux is a <i>passive</i>, read-only screen-mirroring utility. It enhances
  your display setup without any interaction with the Tibia game client, BattlEye
  anti-cheat system, or game servers. It operates using only the standard Linux
  <i>XDG Desktop Portal</i> (<code>org.freedesktop.portal.ScreenCast</code>) and the
  PipeWire media framework already shipped with Bazzite, Fedora, and every modern
  Linux desktop.
</p>

<p>
  The technology is equivalent to using OBS Studio, Discord screen share, or the
  built-in KDE/GNOME screenshot tools, all of which are completely legitimate and do
  not interfere with Tibia or BattlEye in any way.
</p>

<ul>
  <li>No memory reading or modification.</li>
  <li>No process or library injection.</li>
  <li>No API hooking.</li>
  <li>No file-system access to the Tibia client.</li>
  <li>No network interaction with the game servers.</li>
  <li>No input injection or automation of any kind.</li>
</ul>

<p>
  That said, external software is <b>not officially supported</b> by CipSoft. Use at
  your own risk; close this app before contacting Tibia support about client issues.
</p>
"""


class AboutDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"About {__app_name__}")
        self.resize(520, 520)
        self.setModal(True)

        text = QLabel(_STATEMENT.format(name=__app_name__, version=__version__), self)
        text.setWordWrap(True)
        text.setTextFormat(Qt.TextFormat.RichText)
        text.setAlignment(Qt.AlignmentFlag.AlignTop)
        text.setOpenExternalLinks(True)

        buttons = QDialogButtonBox(self)
        safety_btn = buttons.addButton("Read full safety doc", QDialogButtonBox.ButtonRole.HelpRole)
        buttons.addButton(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        buttons.clicked.connect(
            lambda b: self._open_safety_doc() if b is safety_btn else None  # type: ignore[func-returns-value]
        )

        layout = QVBoxLayout(self)
        layout.addWidget(text, 1)
        layout.addWidget(buttons)

    def _open_safety_doc(self) -> None:
        from PySide6.QtCore import QUrl

        QDesktopServices.openUrl(
            QUrl("https://github.com/tibiavision-linux/tibiavision-linux/blob/main/docs/safety.md")
        )
