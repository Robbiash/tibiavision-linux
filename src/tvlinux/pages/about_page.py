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
  TibiaVision-Linux is a <i>passive</i>, read-only utility. It never sends input to Tibia,
  never touches Tibia's memory or network, and never writes any Tibia file. Captured pixels
  come from the standard Linux <i>XDG Desktop Portal</i>
  (<code>org.freedesktop.portal.ScreenCast</code>) and the PipeWire media framework, the
  same APIs used by OBS Studio, Discord screen share, and the built-in KDE/GNOME screenshot
  tools.
</p>
<p><b>What we never do</b></p>
<ul>
  <li>No memory reading or modification.</li>
  <li>No process or library injection.</li>
  <li>No API hooking.</li>
  <li>No writing, modifying, or deleting any Tibia file.</li>
  <li>No network interaction with the game servers.</li>
  <li>No keystrokes, clicks, or any input sent to the Tibia window.</li>
  <li>No observation of keys you press outside the app (no global key logger).</li>
</ul>
<p><b>What we do read, so the story matches the code</b></p>
<ul>
  <li>
    <b>Screen pixels</b> of the window or region you explicitly picked in the portal prompt,
    used for the mirror windows and (optionally) for the HUD panels.
  </li>
  <li>
    <b>Tibia's <code>clientoptions.json</code></b> file, <i>read-only</i>, so the hotbar
    panel can show the hotkeys <i>you</i> configured in Tibia and the app can notice when
    you switch characters. We never write this file.
  </li>
  <li>
    <b>The OS clipboard</b>, parsed only after <i>you</i> press Tibia's own "Copy to
    clipboard" menu on the Hunt Analyser or Party Hunt widget. Hunt Mode must be ON for
    this; when OFF the clipboard is ignored completely.
  </li>
</ul>
<p>
  External software is <b>not officially supported</b> by CipSoft. Use at your own risk;
  close this app before contacting Tibia support about client issues.
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
            f"font-size: {TOKENS.type.size_display}pt; font-weight: {TOKENS.type.weight_bold};"
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
