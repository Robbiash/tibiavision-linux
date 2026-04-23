"""About page -- safety statement + version.

Same content as :class:`tvlinux.about_dialog.AboutDialog` but embedded as a
page so the sidebar nav has a permanent home for it (rather than a modal the
user has to dismiss).
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .. import __app_name__, __version__
from ..theme import TOKENS
from ..ui_helpers import card, pill_button, section_label

_STATEMENT = """
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
  <li>No input injection that reaches the game (Hunt Mode's passive key listener is observer-only).</li>
</ul>
<p>
  That said, external software is <b>not officially supported</b> by CipSoft. Use at
  your own risk; close this app before contacting Tibia support about client issues.
</p>
"""


class AboutPage(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        s = TOKENS.spacing
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(s.md)

        c = card(self)
        layout = c.body_layout
        layout.addWidget(section_label("ABOUT", c))

        title = QLabel(f"{__app_name__}", c)
        title.setStyleSheet(
            f"font-size: {TOKENS.type.size_display}pt; " f"font-weight: {TOKENS.type.weight_bold};"
        )
        layout.addWidget(title)
        layout.addWidget(QLabel(f"Version {__version__}", c))

        text = QLabel(_STATEMENT, c)
        text.setWordWrap(True)
        text.setTextFormat(Qt.TextFormat.RichText)
        text.setAlignment(Qt.AlignmentFlag.AlignTop)
        text.setOpenExternalLinks(True)
        layout.addWidget(text)

        row = QHBoxLayout()
        btn = pill_button("Read full safety doc", variant="primary", parent=c)
        btn.clicked.connect(self._open_safety_doc)
        row.addWidget(btn)
        row.addStretch(1)
        layout.addLayout(row)

        outer.addWidget(c)
        outer.addStretch(1)

    def _open_safety_doc(self) -> None:
        QDesktopServices.openUrl(
            QUrl("https://github.com/tibiavision-linux/tibiavision-linux/blob/main/docs/safety.md")
        )


__all__ = ["AboutPage"]
