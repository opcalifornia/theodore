"""Shared test setup."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_bridge_dialogs(monkeypatch):
    # Some tests run the real Theodore Bridge script against a fake Resolve;
    # on a Mac its result dialog would otherwise pop up on the editor's screen
    # claiming a bridge is connected.
    monkeypatch.setenv("THEODORE_BRIDGE_NO_DIALOG", "1")


@pytest.fixture(autouse=True)
def _never_call_real_claude_code(monkeypatch):
    # With Claude Code installed, Theodore defaults to running on the editor's
    # Claude plan. Tests use scripted fakes and must never reach the real one.
    monkeypatch.setenv("THEODORE_LLM", "api")
