"""Module-level mutable state for posthog-hermes plugin.

All state dicts and the lock live here (canonical owner per ADR-003).
Only _POSTHOG_CLIENT lives in __init__.py.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

_STATE_LOCK = threading.Lock()
_RUN_STATE: dict[str, "RunState"] = {}        # key: f"{task_id}:{api_call_count}"
_SESSION_STATE: dict[str, "SessionState"] = {}  # key: _task_key(task_id, session_id)
_TOOL_START_TIMES: dict[str, float] = {}       # key: tool_call_id

STALE_TTL_SECONDS = 300  # 5 minutes


@dataclass
class RunState:
    trace_id: str
    span_id: str
    start_time: float
    model: str
    provider: str
    base_url: str
    api_mode: str
    input: Optional[list[Any]]
    session_id: str
    channel: str
    task_id: str


@dataclass
class SessionState:
    trace_id: str
    session_id: str
    start_time_ms: float
    last_output_at_ms: float = field(default=0.0)
    current_generation_span_id: Optional[str] = None
    total_input_tokens: Optional[int] = None
    total_output_tokens: Optional[int] = None


def cleanup_stale_runs() -> None:
    """Evict RunState entries older than STALE_TTL_SECONDS. Call at top of hook handlers."""
    now = time.time()
    cutoff = now - STALE_TTL_SECONDS
    with _STATE_LOCK:
        stale_runs = [k for k, v in _RUN_STATE.items() if v.start_time < cutoff]
        for k in stale_runs:
            del _RUN_STATE[k]
        stale_sessions = [k for k, v in _SESSION_STATE.items() if v.last_output_at_ms / 1000 < cutoff and v.last_output_at_ms > 0]
        for k in stale_sessions:
            del _SESSION_STATE[k]


def reset_for_tests() -> None:
    """Clear all state. Call in pytest fixtures."""
    with _STATE_LOCK:
        _RUN_STATE.clear()
        _SESSION_STATE.clear()
        _TOOL_START_TIMES.clear()
