"""Pytest fixtures.

A single ``QApplication`` is created for the whole test session because Qt forbids
constructing more than one per process. Tests that don't need Qt still work with the app
present (it is inert unless events are dispatched).
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app
