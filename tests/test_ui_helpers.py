"""Tests for UI helper factories."""

from __future__ import annotations

from tvlinux.ui_helpers import EmptyState, empty_state


def test_empty_state_without_action(qapp):
    from PySide6.QtWidgets import QPushButton

    widget = empty_state(
        icon_name="layers",
        title="No regions yet",
        subtitle="Create your first region.",
    )
    assert isinstance(widget, EmptyState)
    # No action_label -> no CTA button was built; this is the
    # "informational" form of the empty state used when the user
    # would reach the action via the page header instead.
    assert widget.findChildren(QPushButton) == []


def test_empty_state_with_action_fires_signal(qapp):
    fired: list[int] = []

    def on_action() -> None:
        fired.append(1)

    widget = empty_state(
        icon_name="layers",
        title="No regions yet",
        subtitle="Create your first region.",
        action_label="Create",
        on_action=on_action,
    )
    assert isinstance(widget, EmptyState)
    widget.action_clicked.emit()
    assert fired == [1]


def test_empty_state_falls_back_to_bullet_for_missing_icon(qapp):
    # "no-such-icon" is not a bundled asset. The helper must still
    # render instead of throwing; we verify it by finding the fallback
    # circle glyph in a QLabel child.
    widget = empty_state(
        icon_name="no-such-icon",
        title="Nothing",
        subtitle="Really nothing.",
    )
    from PySide6.QtWidgets import QLabel

    texts = [lbl.text() for lbl in widget.findChildren(QLabel)]
    assert any("\u25cb" in t for t in texts)
