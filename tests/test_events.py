"""Unit tests for events.py — pure event builders."""
from __future__ import annotations

from state import RunState, SessionState
from events import build_ai_generation, build_ai_span, build_ai_trace


def _make_run(**kwargs) -> RunState:
    defaults = dict(
        trace_id="trace123",
        span_id="span456",
        start_time=0.0,
        model="claude-haiku-4-5",
        provider="anthropic",
        base_url="",
        api_mode="chat",
        input=[{"role": "user", "content": "hello"}],
        session_id="sess1",
        channel="slack",
        task_id="task1",
    )
    defaults.update(kwargs)
    return RunState(**defaults)


def test_build_ai_generation_basic():
    run = _make_run()
    evt = build_ai_generation(
        run=run, output_text="hi", input_tokens=10, output_tokens=5,
        cache_read_tokens=None, cache_write_tokens=None, reasoning_tokens=None,
        total_cost_usd=0.001, latency=1.2, finish_reason="stop",
        privacy_mode=False,
    )
    p = evt["properties"]
    assert evt["event"] == "$ai_generation"
    assert evt["distinct_id"] == "sess1"
    assert p["$ai_model"] == "claude-haiku-4-5"
    assert p["$ai_input_tokens"] == 10
    assert p["$ai_output_tokens"] == 5
    assert p["$ai_is_error"] is False
    assert p["$ai_lib"] == "posthog-hermes"
    assert p["$ai_framework"] == "hermes"
    # Verify canonical $ai_ prefixed cache tokens
    assert "$ai_cache_read_input_tokens" in p
    assert "$ai_cache_creation_input_tokens" in p
    # Verify $ai_stop_reason is NOT emitted (dropped — not canonical)
    assert "$ai_stop_reason" not in p


def test_build_ai_generation_privacy_mode():
    run = _make_run()
    evt = build_ai_generation(
        run=run, output_text="secret", input_tokens=5, output_tokens=3,
        cache_read_tokens=None, cache_write_tokens=None, reasoning_tokens=None,
        total_cost_usd=None, latency=0.5, finish_reason="stop",
        privacy_mode=True,
    )
    p = evt["properties"]
    assert p["$ai_input"] is None
    assert p["$ai_output_choices"] is None


def test_build_ai_generation_error():
    run = _make_run(input=None)
    evt = build_ai_generation(
        run=run, output_text=None, input_tokens=None, output_tokens=None,
        cache_read_tokens=None, cache_write_tokens=None, reasoning_tokens=None,
        total_cost_usd=None, latency=0.1, finish_reason="error",
        privacy_mode=False,
    )
    assert evt["properties"]["$ai_is_error"] is True


def test_build_ai_span_with_parent():
    evt = build_ai_span(
        trace_id="trace1", parent_span_id="gen1", tool_name="read_file",
        args={"path": "/foo"}, result="content", duration_s=0.05,
        session_id="sess1", task_id="t1", privacy_mode=False,
    )
    p = evt["properties"]
    assert evt["event"] == "$ai_span"
    assert p["$ai_parent_id"] == "gen1"
    assert p["$ai_span_name"] == "read_file"
    assert p["$ai_latency"] == 0.05


def test_build_ai_span_privacy_mode():
    evt = build_ai_span(
        trace_id="t1", parent_span_id=None, tool_name="bash",
        args={"cmd": "secret"}, result="output", duration_s=0.1,
        session_id="s1", task_id="t1", privacy_mode=True,
    )
    p = evt["properties"]
    assert p["$ai_input_state"] is None
    assert p["$ai_output_state"] is None


def test_build_ai_trace():
    evt = build_ai_trace(
        trace_id="trace1", session_id="sess1",
        total_input_tokens=100, total_output_tokens=50,
        latency=3.5, channel="slack",
    )
    p = evt["properties"]
    assert evt["event"] == "$ai_trace"
    assert p["$ai_total_input_tokens"] == 100
    assert p["$ai_total_output_tokens"] == 50
    assert p["$ai_channel"] == "slack"
