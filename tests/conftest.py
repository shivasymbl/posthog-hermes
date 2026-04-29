"""Shared pytest fixtures for posthog-hermes tests."""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import state as _state_module


@pytest.fixture(autouse=True)
def reset_state():
    """Clear all module-level state before each test."""
    _state_module.reset_for_tests()
    yield
    _state_module.reset_for_tests()


@pytest.fixture
def mock_posthog(monkeypatch):
    """Replace the PostHog client with a MagicMock."""
    import __init__ as plugin
    client = MagicMock()
    monkeypatch.setattr(plugin, "_POSTHOG_CLIENT", client)
    monkeypatch.setattr(plugin, "_INIT_FAILED", object())  # fresh sentinel
    return client


@pytest.fixture
def mock_ctx():
    """Mock Hermes ctx that captures registered hooks."""
    hooks: dict[str, Any] = {}
    ctx = MagicMock()
    ctx.register_hook.side_effect = lambda name, fn: hooks.__setitem__(name, fn)
    ctx._hooks = hooks
    return ctx
