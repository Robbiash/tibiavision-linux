"""Hunt History page -- log of past hunts + loot split.

Sources:

- **Automatic** -- whenever Hunt Mode is active and the ``ClipboardWatcher``
  captures a :class:`HuntSession`, the app pushes a :class:`HuntRecord` to the
  store (see ``app.py`` wiring). The page reflects the store through
  :meth:`refresh`.
- **Manual paste** -- pressing "Paste session" opens a dialog where the user
  pastes raw Hunt Analyser text. It is parsed with
  :func:`tvlinux.hunt_parser.parse_hunt_analyser` and stored.
- **Loot split** -- selecting a row or clicking "Loot split" opens a dialog
  where the user can type party balances (or paste a Party Hunt text) and
  immediately see who pays whom.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..hunt_history import HuntHistoryStore, HuntRecord
from ..hunt_parser import parse_hunt_analyser, parse_party_hunt
from ..loot_split import format_transfers_block, split, split_from_party
from ..stats_math import humanize_duration, humanize_gp
from ..theme import TOKENS
from ..ui_helpers import card, muted_label, pill_button, section_label

COLS = ["Date", "Character", "Duration", "XP/h", "Profit", "Damage/h", "Notes"]


class HuntHistoryPage(QWidget):
    """Two-pane layout: left table of records, right detail + actions."""

    record_changed = Signal(object)  # HuntRecord

    def __init__(self, store: HuntHistoryStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._store = store
        self._selected_id: UUID | None = None
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        s = TOKENS.spacing
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(s.md)

        toolbar = QHBoxLayout()
        self._btn_paste = pill_button("Paste session", variant="primary", parent=self)
        self._btn_paste.clicked.connect(self._open_paste_dialog)
        toolbar.addWidget(self._btn_paste)
        self._btn_split = pill_button("Loot split", parent=self)
        self._btn_split.clicked.connect(self._open_split_dialog)
        toolbar.addWidget(self._btn_split)
        self._btn_delete = pill_button("Delete", variant="danger", parent=self)
        self._btn_delete.clicked.connect(self._delete_selected)
        toolbar.addWidget(self._btn_delete)
        toolbar.addStretch(1)
        self._summary_label = QLabel("No sessions yet.", self)
        self._summary_label.setProperty("role", "muted")
        toolbar.addWidget(self._summary_label)
        outer.addLayout(toolbar)

        body = QHBoxLayout()

        table_card = card(self)
        tcl = table_card.body_layout
        tcl.addWidget(section_label("SESSIONS", table_card))
        self._table = QTableWidget(0, len(COLS), table_card)
        self._table.setHorizontalHeaderLabels(COLS)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(True)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)
        self._table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        tcl.addWidget(self._table)
        body.addWidget(table_card, 3)

        detail_card = card(self)
        dcl = detail_card.body_layout
        dcl.addWidget(section_label("DETAIL", detail_card))
        self._detail_label = QLabel("Select a session to view details.", detail_card)
        self._detail_label.setWordWrap(True)
        self._detail_label.setTextFormat(Qt.TextFormat.RichText)
        dcl.addWidget(self._detail_label)

        dcl.addWidget(muted_label("Notes", detail_card, wrap=False))
        self._notes = QPlainTextEdit(detail_card)
        self._notes.setPlaceholderText("Add notes about this hunt (loot rolls, tactics, etc.)")
        self._notes.textChanged.connect(self._on_notes_changed)
        dcl.addWidget(self._notes)

        body.addWidget(detail_card, 2)
        outer.addLayout(body, 1)

    # -- Public API -------------------------------------------------------

    def refresh(self) -> None:
        records = self._store.all()
        self._table.setRowCount(len(records))
        for row, rec in enumerate(records):
            ts = datetime.fromtimestamp(rec.captured_at).strftime("%Y-%m-%d %H:%M")
            cells = [
                ts,
                rec.character or "-",
                humanize_duration(int(rec.session_sec)),
                humanize_gp(int(rec.xp_per_h)),
                humanize_gp(int(rec.balance)),
                humanize_gp(int(rec.damage_per_h)),
                (rec.notes[:48] + "...") if len(rec.notes) > 48 else rec.notes,
            ]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(str(text))
                if col in (2, 3, 4, 5):
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                item.setData(Qt.ItemDataRole.UserRole, rec.id)
                self._table.setItem(row, col, item)
        self._update_summary()
        self._refresh_detail()

    def _update_summary(self) -> None:
        records = self._store.all()
        if not records:
            self._summary_label.setText("No sessions yet.")
            return
        total_profit = sum(r.balance for r in records)
        total_sec = sum(r.session_sec for r in records)
        self._summary_label.setText(
            f"{len(records)} sessions | total profit {humanize_gp(total_profit)} "
            f"| time {humanize_duration(total_sec)}"
        )

    # -- Selection + detail ----------------------------------------------

    def _on_selection_changed(self) -> None:
        items = self._table.selectedItems()
        if not items:
            self._selected_id = None
            self._refresh_detail()
            return
        rid = items[0].data(Qt.ItemDataRole.UserRole)
        if isinstance(rid, UUID):
            self._selected_id = rid
        else:
            try:
                self._selected_id = UUID(str(rid))
            except ValueError:
                self._selected_id = None
        self._refresh_detail()

    def _selected_record(self) -> HuntRecord | None:
        if self._selected_id is None:
            return None
        return self._store.get(self._selected_id)

    def _refresh_detail(self) -> None:
        rec = self._selected_record()
        if rec is None:
            self._detail_label.setText("Select a session to view details.")
            self._notes.blockSignals(True)
            self._notes.setPlainText("")
            self._notes.blockSignals(False)
            return
        ts = datetime.fromtimestamp(rec.captured_at).strftime("%Y-%m-%d %H:%M")
        html = (
            f"<b>{rec.character or 'Unknown'}</b> &middot; {ts}<br>"
            f"Session {humanize_duration(int(rec.session_sec))} &middot; "
            f"XP {humanize_gp(int(rec.xp_gain))} "
            f"({humanize_gp(int(rec.xp_per_h))}/h)<br>"
            f"Loot {humanize_gp(int(rec.loot))} "
            f"&minus; Supplies {humanize_gp(int(rec.supplies))} "
            f"= <b>Profit {humanize_gp(int(rec.balance))}</b><br>"
            f"Damage {humanize_gp(int(rec.damage_per_h))}/h &middot; "
            f"Healing {humanize_gp(int(rec.healing_per_h))}/h"
        )
        self._detail_label.setText(html)
        self._notes.blockSignals(True)
        self._notes.setPlainText(rec.notes)
        self._notes.blockSignals(False)

    def _on_notes_changed(self) -> None:
        rec = self._selected_record()
        if rec is None:
            return
        rec.notes = self._notes.toPlainText()
        self._store.update(rec)
        self._update_summary()
        # Refresh the notes column without re-emitting signals / losing selection.
        row = self._table.currentRow()
        if row >= 0:
            text = (rec.notes[:48] + "...") if len(rec.notes) > 48 else rec.notes
            item = self._table.item(row, len(COLS) - 1)
            if item is not None:
                item.setText(text)

    def _delete_selected(self) -> None:
        rec = self._selected_record()
        if rec is None:
            return
        self._store.remove(rec.id)
        self._selected_id = None
        self.refresh()

    # -- Dialogs -----------------------------------------------------------

    def _open_paste_dialog(self) -> None:
        text = _prompt_paste(self, "Paste Hunt Analyser text")
        if not text:
            return
        session = parse_hunt_analyser(text)
        if session is None:
            self._summary_label.setText(
                "Could not parse the pasted text as a Hunt Analyser session."
            )
            return
        rec = HuntRecord.from_session(session, raw_text=text)
        self._store.add(rec)
        self.refresh()

    def _open_split_dialog(self) -> None:
        dlg = LootSplitDialog(self)
        dlg.exec()

    # -- Store event hooks ------------------------------------------------

    def record_added(self, _record: HuntRecord) -> None:
        """Called by app wiring when the ClipboardWatcher auto-logs a hunt."""
        self.refresh()


# -- Helpers ---------------------------------------------------------------


def _prompt_paste(parent: QWidget, title: str) -> str:
    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    dlg.resize(520, 360)
    layout = QVBoxLayout(dlg)
    layout.addWidget(QLabel("Paste the raw clipboard text here:", dlg))
    edit = QPlainTextEdit(dlg)
    clip = QGuiApplication.clipboard()
    if clip is not None:
        edit.setPlainText(clip.text() or "")
    layout.addWidget(edit, 1)
    bb = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
        parent=dlg,
    )
    bb.accepted.connect(dlg.accept)
    bb.rejected.connect(dlg.reject)
    layout.addWidget(bb)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return ""
    return edit.toPlainText()


class LootSplitDialog(QDialog):
    """Paste a Party Hunt text or type balances manually to see the split."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Loot split")
        self.resize(560, 480)
        layout = QVBoxLayout(self)

        layout.addWidget(
            QLabel(
                "Paste Party Hunt text, or type each member on its own line as 'Name: balance'.",
                self,
            )
        )
        self._input = QPlainTextEdit(self)
        clip = QGuiApplication.clipboard()
        if clip is not None:
            self._input.setPlainText(clip.text() or "")
        layout.addWidget(self._input, 1)

        btn_row = QHBoxLayout()
        self._btn_calc = pill_button("Calculate", variant="primary", parent=self)
        self._btn_calc.clicked.connect(self._calculate)
        btn_row.addWidget(self._btn_calc)
        self._btn_copy = pill_button("Copy result", parent=self)
        self._btn_copy.clicked.connect(self._copy_result)
        btn_row.addWidget(self._btn_copy)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        self._output = QTextEdit(self)
        self._output.setReadOnly(True)
        layout.addWidget(self._output, 1)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, parent=self)
        bb.rejected.connect(self.reject)
        layout.addWidget(bb)

    def _calculate(self) -> None:
        text = self._input.toPlainText()
        session = parse_party_hunt(text)
        if session is not None and session.members:
            transfers = split_from_party(session)
        else:
            pairs = _parse_manual_pairs(text)
            if not pairs:
                self._output.setPlainText(
                    "Could not parse any members. Expected 'Name: balance' per line "
                    "or a Party Hunt paste."
                )
                return
            transfers = split(pairs)

        fair = transfers[0].fair_share if transfers else 0
        lines = [f"Fair share per member: {humanize_gp(fair)}", ""]
        for t in transfers:
            arrow = {"pay": "->", "receive": "<-", "even": "="}[t.direction]
            lines.append(
                f"{t.name}: balance {humanize_gp(t.balance)} "
                f"{arrow} transfer {humanize_gp(abs(t.transfer))}"
            )
        lines.append("")
        lines.append("Chat-friendly transfer lines:")
        lines.append(format_transfers_block(transfers))
        self._output.setPlainText("\n".join(lines))

    def _copy_result(self) -> None:
        clip = QGuiApplication.clipboard()
        if clip is not None:
            clip.setText(self._output.toPlainText())


def _parse_manual_pairs(text: str) -> list[tuple[str, int]]:
    pairs: list[tuple[str, int]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or ":" not in line:
            continue
        name, value = line.split(":", 1)
        name = name.strip()
        value = value.strip().replace(",", "").replace(".", "").replace(" ", "")
        if value.lower().endswith("gp"):
            value = value[:-2]
        sign = 1
        if value.startswith("-"):
            sign = -1
            value = value[1:]
        if not value.isdigit():
            continue
        pairs.append((name, sign * int(value)))
    return pairs


__all__ = ["HuntHistoryPage", "LootSplitDialog"]
