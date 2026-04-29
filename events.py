"""Pure event-builder functions for posthog-hermes.

Each function returns a dict: {"event": str, "distinct_id": str, "properties": dict}.
No side effects. No imports of plugin state. Easily unit-testable.
"""
from __future__ import annotations

from typing import Any, Optional

try:
    from .state import RunState, SessionState
    from .helpers import generate_span_id, safe_value
except ImportError:
    from state import RunState, SessionState  # type: ignore[no-redef]
    from helpers import generate_span_id, safe_value  # type: ignore[no-redef]

VERSION = "0.1.0"

_COMMON = {
    "$ai_lib": "posthog-hermes",
    "$ai_lib_version": VERSION,
    "$ai_framework": "hermes",
}


def build_ai_generation(
    *,
    run: RunState,
    output_text: Optional[str],
    input_tokens: Optional[int],
    output_tokens: Optional[int],
    cache_read_tokens: Optional[int],
    cache_write_tokens: Optional[int],
    reasoning_tokens: Optional[int],
    total_cost_usd: Optional[float],
    latency: float,
    finish_reason: str,
    response: Any = None,
    privacy_mode: bool,
) -> dict:
    distinct_id = run.session_id or run.task_id or "unknown"
    is_error = finish_reason == "error"
    error_msg: Optional[str] = None
    if is_error:
        error_msg = str(getattr(response, "error", finish_reason)) if response else finish_reason

    props: dict[str, Any] = {
        "$ai_trace_id": run.trace_id,
        "$ai_session_id": run.session_id or None,
        "$ai_span_id": run.span_id,
        "$ai_model": run.model,
        "$ai_provider": run.provider,
        "$ai_base_url": run.base_url or None,
        "$ai_input": safe_value(run.input) if not privacy_mode and run.input is not None else None,
        "$ai_output_choices": (
            [{"role": "assistant", "content": output_text}]
            if not privacy_mode and output_text
            else None
        ),
        "$ai_input_tokens": input_tokens,
        "$ai_output_tokens": output_tokens,
        # $ai_ prefix is required — openclaw omits it (bug we fix)
        "$ai_cache_read_input_tokens": cache_read_tokens,
        "$ai_cache_creation_input_tokens": cache_write_tokens,
        "$ai_reasoning_tokens": reasoning_tokens,
        "$ai_latency": round(latency, 3),
        "$ai_total_cost_usd": total_cost_usd,
        "$ai_is_error": is_error,
        "$ai_error": error_msg,
        "$ai_channel": run.channel or None,
        **_COMMON,
    }
    http_status = getattr(response, "status_code", None)
    if http_status is not None:
        props["$ai_http_status"] = http_status

    return {"event": "$ai_generation", "distinct_id": distinct_id, "properties": props}


def build_ai_span(
    *,
    trace_id: str,
    parent_span_id: Optional[str],
    tool_name: str,
    args: Any,
    result: Any,
    duration_s: Optional[float],
    session_id: str,
    task_id: str,
    privacy_mode: bool,
) -> dict:
    distinct_id = session_id or task_id or "unknown"
    is_error = isinstance(result, dict) and result.get("error") is not None

    return {
        "event": "$ai_span",
        "distinct_id": distinct_id,
        "properties": {
            "$ai_trace_id": trace_id,
            "$ai_session_id": session_id or None,
            "$ai_span_id": generate_span_id(),
            "$ai_parent_id": parent_span_id or None,
            "$ai_span_name": tool_name,
            "$ai_input_state": safe_value(args) if not privacy_mode else None,
            "$ai_output_state": safe_value(result) if not privacy_mode else None,
            "$ai_latency": round(duration_s, 3) if duration_s is not None else None,
            "$ai_is_error": is_error,
            "$ai_error": None,
            **_COMMON,
        },
    }


def build_ai_trace(
    *,
    trace_id: str,
    session_id: str,
    total_input_tokens: Optional[int],
    total_output_tokens: Optional[int],
    latency: float,
    channel: Optional[str],
) -> dict:
    distinct_id = session_id or "unknown"
    return {
        "event": "$ai_trace",
        "distinct_id": distinct_id,
        "properties": {
            "$ai_trace_id": trace_id,
            "$ai_session_id": session_id or None,
            "$ai_latency": round(latency, 3),
            "$ai_total_input_tokens": total_input_tokens,
            "$ai_total_output_tokens": total_output_tokens,
            "$ai_is_error": False,
            "$ai_error": None,
            "$ai_channel": channel or None,
            **_COMMON,
        },
    }
