"""Unit tests for hook handlers in __init__.py."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import time

import __init__ as plugin


@pytest.fixture
def client(monkeypatch):
    c = MagicMock()
    monkeypatch.setattr(plugin, "_POSTHOG_CLIENT", c)
    return c


def test_register_all_hooks(mock_ctx):
    plugin.register(mock_ctx)
    registered = set(mock_ctx._hooks.keys())
    assert "pre_api_request" in registered
    assert "post_api_request" in registered
    assert "pre_llm_call" in registered
    assert "post_llm_call" in registered
    assert "pre_tool_call" in registered
    assert "post_tool_call" in registered
    assert "on_session_end" in registered


def test_pre_api_request_skips_non_list_messages(client):
    """Turn-scoped pre_llm_call variant (messages=None) must be a no-op."""
    plugin.on_pre_api_request(task_id="t1", session_id="s1", messages=None)
    # State should be empty — no RunState created
    from state import _RUN_STATE
    assert len(_RUN_STATE) == 0


def test_full_generation_turn(client):
    """Single LLM call with no tool calls should emit $ai_generation + $ai_trace."""
    plugin.on_pre_api_request(
        task_id="t1", session_id="s1", platform="slack",
        model="claude-haiku-4-5", provider="anthropic", base_url="",
        api_mode="chat", api_call_count=0, messages=[{"role": "user", "content": "hi"}],
    )
    plugin.on_post_api_request(
        task_id="t1", session_id="s1", provider="anthropic", base_url="",
        api_mode="chat", model="claude-haiku-4-5", api_call_count=0,
        assistant_response="hello", api_duration=0.5, finish_reason="stop",
        assistant_tool_call_count=0,
    )
    calls = client.capture.call_args_list
    events = [c[0][0] for c in calls]  # first positional arg is event name
    assert "$ai_generation" in events
    assert "$ai_trace" in events


def test_generation_with_tool_does_not_emit_trace(client):
    """When assistant has tool calls pending, $ai_trace must NOT be emitted."""
    plugin.on_pre_api_request(
        task_id="t1", session_id="s1", platform="slack",
        model="m", provider="p", base_url="", api_mode="chat",
        api_call_count=0, messages=[{"role": "user", "content": "x"}],
    )
    plugin.on_post_api_request(
        task_id="t1", session_id="s1", provider="p", base_url="",
        api_mode="chat", model="m", api_call_count=0,
        finish_reason="tool_calls", assistant_tool_call_count=1,
    )
    events = [c[0][0] for c in client.capture.call_args_list]
    assert "$ai_generation" in events
    assert "$ai_trace" not in events


def test_tool_span_emitted(client):
    """Tool call should emit one $ai_span event."""
    # Set up a session first
    plugin.on_pre_api_request(
        task_id="t1", session_id="s1", platform="slack",
        model="m", provider="p", base_url="", api_mode="chat",
        api_call_count=0, messages=[{"role": "user", "content": "x"}],
    )
    plugin.on_pre_tool_call(tool_name="bash", task_id="t1", session_id="s1", tool_call_id="tc1")
    plugin.on_post_tool_call(
        tool_name="bash", args={"cmd": "ls"}, result="file.txt",
        task_id="t1", session_id="s1", tool_call_id="tc1",
    )
    events = [c[0][0] for c in client.capture.call_args_list]
    assert "$ai_span" in events


def test_hook_exception_does_not_propagate(monkeypatch):
    """Exceptions inside hooks must be swallowed."""
    monkeypatch.setattr(plugin, "_POSTHOG_CLIENT", MagicMock(capture=MagicMock(side_effect=RuntimeError("boom"))))
    # Should NOT raise
    plugin.on_pre_api_request(
        task_id="t1", session_id="s1", platform="slack",
        model="m", provider="p", base_url="", api_mode="chat",
        api_call_count=0, messages=[{"role": "user", "content": "x"}],
    )
    plugin.on_post_api_request(
        task_id="t1", session_id="s1", provider="p", base_url="",
        api_mode="chat", model="m", api_call_count=0,
        finish_reason="stop", assistant_tool_call_count=0,
    )  # capture will raise but handler must catch


def test_session_mode_emits_trace_on_session_end(client, monkeypatch):
    """session mode: $ai_trace fires from on_session_end, not mid-turn."""
    monkeypatch.setenv("POSTHOG_TRACE_GROUPING", "session")
    plugin.on_pre_api_request(
        task_id="t1", session_id="s1", platform="",
        model="m", provider="p", base_url="", api_mode="chat",
        api_call_count=0, messages=[{"role": "user", "content": "x"}],
    )
    plugin.on_post_api_request(
        task_id="t1", session_id="s1", provider="p", base_url="",
        api_mode="chat", model="m", api_call_count=0,
        finish_reason="stop", assistant_tool_call_count=0,
    )
    # No $ai_trace yet in session mode
    events_before = [c[0][0] for c in client.capture.call_args_list]
    assert "$ai_trace" not in events_before

    plugin.on_session_end(session_id="s1", task_id="t1")
    events_after = [c[0][0] for c in client.capture.call_args_list]
    assert "$ai_trace" in events_after
    monkeypatch.delenv("POSTHOG_TRACE_GROUPING")
