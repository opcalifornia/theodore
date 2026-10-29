"""Shared test setup."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_bridge_dialogs(monkeypatch):
    # Some tests run the real Theodore Bridge script against a fake Resolve;
    # on a Mac its result dialog would otherwise pop up on the editor's screen
    # claiming a bridge is connected.
    monkeypatch.setenv("THEODORE_BRIDGE_NO_DIALOG", "1")
