"""posthog-hermes — Hermes Agent plugin for PostHog LLM Analytics.

Emits $ai_generation, $ai_span, and $ai_trace events to PostHog for every
LLM call, tool call, and conversational turn.

Required env vars:
  POSTHOG_API_KEY  — PostHog project API key (phc_...)

Optional:
  POSTHOG_HOST                   — default https://us.i.posthog.com
  POSTHOG_PRIVACY_MODE           — "true" to strip LLM content from events
  POSTHOG_TRACE_GROUPING         — "message" (default) or "session"
  POSTHOG_SESSION_WINDOW_MINUTES — inactivity window for trace rotation (default 60)
  POSTHOG_MAX_FIELD_CHARS        — max chars per string field (default 12000)
  HERMES_POSTHOG_DEBUG           — "true" for verbose logging
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

try:
    from posthog import Posthog
    import posthog as _posthog_module
    _POSTHOG_MAJOR = int(getattr(_posthog_module, "__version__", "7.0").split(".")[0])
except Exception:
    Posthog = None  # type: ignore[assignment,misc]
    _POSTHOG_MAJOR = 0

try:
    from .events import build_ai_generation, build_ai_span, build_ai_trace
    from .helpers import (
        env, env_bool, env_float, env_int,
        extract_usage_and_cost,
        generate_span_id, generate_trace_id,
    )
    from .state import (
        RunState, SessionState,
        _STATE_LOCK, _RUN_STATE, _SESSION_STATE, _TOOL_START_TIMES,
        cleanup_stale_runs,
    )
except ImportError:
    from events import build_ai_generation, build_ai_span, build_ai_trace  # type: ignore[no-redef]
    from helpers import (  # type: ignore[no-redef]
        env, env_bool, env_float, env_int,
        extract_usage_and_cost,
        generate_span_id, generate_trace_id,
    )
    from state import (  # type: ignore[no-redef]
        RunState, SessionState,
        _STATE_LOCK, _RUN_STATE, _SESSION_STATE, _TOOL_START_TIMES,
        cleanup_stale_runs,
    )

_POSTHOG_CLIENT = None
_INIT_FAILED = object()
_CLIENT_LOCK = threading.Lock()


# -- Config helpers --

def _privacy_mode() -> bool:
    return env_bool("POSTHOG_PRIVACY_MODE")


def _trace_grouping() -> str:
    return "session" if env("POSTHOG_TRACE_GROUPING") == "session" else "message"


def _session_window_ms() -> float:
    return env_float("POSTHOG_SESSION_WINDOW_MINUTES", 60.0) * 60_000


def _debug(msg: str, *args: Any) -> None:
    if env_bool("HERMES_POSTHOG_DEBUG"):
        logger.info("posthog-hermes: " + msg, *args)


# -- Client lifecycle --

def _get_client() -> Optional[Posthog]:  # type: ignore[return]
    global _POSTHOG_CLIENT
    with _CLIENT_LOCK:
        if _POSTHOG_CLIENT is _INIT_FAILED:
            return None
        if _POSTHOG_CLIENT is not None:
            return _POSTHOG_CLIENT  # type: ignore[return-value]
        if Posthog is None:
            logger.warning(
                "posthog-hermes: posthog SDK not installed. "
                "Run: pip install 'posthog>=7.0,<8'"
            )
            _POSTHOG_CLIENT = _INIT_FAILED
            return None
        if _POSTHOG_MAJOR < 7:
            logger.warning(
                "posthog-hermes: posthog SDK v%s detected; requires >=7.0. "
                "Run: pip install -U posthog", _POSTHOG_MAJOR
            )
            _POSTHOG_CLIENT = _INIT_FAILED
            return None
        api_key = env("POSTHOG_API_KEY")
        if not api_key:
            logger.warning(
                "posthog-hermes: POSTHOG_API_KEY not set — events will not be captured"
            )
            _POSTHOG_CLIENT = _INIT_FAILED
            return None
        host = env("POSTHOG_HOST", "https://us.i.posthog.com")
        try:
            _POSTHOG_CLIENT = Posthog(
                api_key,
                host=host,
                flush_at=20,
                flush_interval=10,
                disable_geoip=True,
            )
            _debug("client initialised (host=%s)", host)
        except Exception as exc:
            logger.warning("posthog-hermes: could not initialise client: %s", exc)
            _POSTHOG_CLIENT = _INIT_FAILED
            return None
        return _POSTHOG_CLIENT  # type: ignore[return-value]


# -- State helpers --

def _task_key(task_id: str, session_id: str) -> str:
    if task_id:
        return task_id
    if session_id:
        return f"session:{session_id}"
    import threading as _t
    return f"thread:{_t.get_ident()}"


def _run_key(task_id: str, api_call_count: int) -> str:
    return f"{task_id}:{api_call_count}"


def _get_or_create_session(task_key: str, session_id: str) -> SessionState:
    now_ms = time.time() * 1000
    state = _SESSION_STATE.get(task_key)

    if _trace_grouping() == "session" and state is not None:
        if now_ms - state.last_output_at_ms < _session_window_ms():
            return state
        # Timed-out — rotate trace
        state = SessionState(
            trace_id=generate_trace_id(),
            session_id=session_id,
            start_time_ms=now_ms,
        )
        _SESSION_STATE[task_key] = state
        return state

    if state is None:
        state = SessionState(
            trace_id=generate_trace_id(),
            session_id=session_id,
            start_time_ms=now_ms,
        )
        _SESSION_STATE[task_key] = state
    return state


# -- Hook handlers --

def on_pre_api_request(
    *,
    task_id: str = "",
    session_id: str = "",
    platform: str = "",
    model: str = "",
    provider: str = "",
    base_url: str = "",
    api_mode: str = "",
    api_call_count: int = 0,
    messages: Any = None,
    **_: Any,
) -> None:
    try:
        # Skip context-injection variant of pre_llm_call (no messages list)
        if not isinstance(messages, list):
            return
        client = _get_client()
        if client is None:
            return

        cleanup_stale_runs()
        tkey = _task_key(task_id, session_id)
        rkey = _run_key(task_id, api_call_count)

        with _STATE_LOCK:
            sess = _get_or_create_session(tkey, session_id)
            span_id = generate_span_id()
            sess.current_generation_span_id = span_id
            _RUN_STATE[rkey] = RunState(
                trace_id=sess.trace_id,
                span_id=span_id,
                start_time=time.time(),
                model=model,
                provider=provider,
                base_url=base_url,
                api_mode=api_mode,
                input=list(messages) if not _privacy_mode() else None,
                session_id=session_id,
                channel=platform,
                task_id=task_id,
            )
        _debug("pre_api_request: run_key=%s trace=%s", rkey, sess.trace_id)
    except Exception as exc:
        logger.warning("posthog-hermes: on_pre_api_request error: %s", exc)


def on_post_api_request(
    *,
    task_id: str = "",
    session_id: str = "",
    provider: str = "",
    base_url: str = "",
    api_mode: str = "",
    model: str = "",
    api_call_count: int = 0,
    assistant_message: Any = None,
    response: Any = None,
    api_duration: float = 0.0,
    finish_reason: str = "",
    usage: Any = None,
    assistant_tool_call_count: int = 0,
    assistant_response: Any = None,
    **_: Any,
) -> None:
    try:
        client = _get_client()
        if client is None:
            return

        tkey = _task_key(task_id, session_id)
        rkey = _run_key(task_id, api_call_count)

        with _STATE_LOCK:
            run = _RUN_STATE.pop(rkey, None)
            sess = _SESSION_STATE.get(tkey)

        if run is None or sess is None:
            return

        now_ms = time.time() * 1000
        sess.last_output_at_ms = now_ms

        input_tokens, output_tokens, cache_read, cache_write, reasoning, cost_usd = extract_usage_and_cost(
            usage, response,
            model=model, provider=provider, base_url=base_url, api_mode=api_mode,
        )

        if input_tokens:
            sess.total_input_tokens = (sess.total_input_tokens or 0) + input_tokens
        if output_tokens:
            sess.total_output_tokens = (sess.total_output_tokens or 0) + output_tokens

        output_text: Optional[str] = None
        if not _privacy_mode():
            if assistant_response is not None:
                output_text = str(assistant_response)
            elif assistant_message is not None:
                output_text = str(getattr(assistant_message, "content", "") or "")

        latency = api_duration if api_duration > 0 else (time.time() - run.start_time)

        gen_evt = build_ai_generation(
            run=run,
            output_text=output_text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
            reasoning_tokens=reasoning,
            total_cost_usd=cost_usd,
            latency=latency,
            finish_reason=finish_reason,
            response=response,
            privacy_mode=_privacy_mode(),
        )
        client.capture(gen_evt["event"], distinct_id=gen_evt["distinct_id"], properties=gen_evt["properties"])
        _debug("captured $ai_generation: trace=%s", run.trace_id)

        # Emit $ai_trace at end of turn (message mode) when no tool calls pending
        has_tools = (assistant_tool_call_count > 0) or bool(
            getattr(assistant_message, "tool_calls", None)
        )
        if not has_tools and _trace_grouping() == "message":
            turn_latency = (now_ms - sess.start_time_ms) / 1000
            trace_evt = build_ai_trace(
                trace_id=sess.trace_id,
                session_id=session_id,
                total_input_tokens=sess.total_input_tokens,
                total_output_tokens=sess.total_output_tokens,
                latency=turn_latency,
                channel=run.channel,
            )
            client.capture(trace_evt["event"], distinct_id=trace_evt["distinct_id"], properties=trace_evt["properties"])
            _debug("captured $ai_trace (message mode): trace=%s", sess.trace_id)
            with _STATE_LOCK:
                _SESSION_STATE.pop(tkey, None)
    except Exception as exc:
        logger.warning("posthog-hermes: on_post_api_request error: %s", exc)


def on_pre_tool_call(
    *,
    tool_name: str = "",
    task_id: str = "",
    session_id: str = "",  # noqa: ARG001
    tool_call_id: str = "",
    **_: Any,
) -> None:
    try:
        key = tool_call_id or f"{task_id}:{tool_name}:{time.time_ns()}"
        with _STATE_LOCK:
            _TOOL_START_TIMES[key] = time.time()
    except Exception as exc:
        logger.warning("posthog-hermes: on_pre_tool_call error: %s", exc)


def on_post_tool_call(
    *,
    tool_name: str = "",
    args: Any = None,
    result: Any = None,
    task_id: str = "",
    session_id: str = "",
    tool_call_id: str = "",
    **_: Any,
) -> None:
    try:
        client = _get_client()
        if client is None:
            return

        tkey = _task_key(task_id, session_id)
        with _STATE_LOCK:
            sess = _SESSION_STATE.get(tkey)
            start_time = _TOOL_START_TIMES.pop(tool_call_id or "", None)

        if sess is None:
            return

        duration_s = (time.time() - start_time) if start_time is not None else None
        span_evt = build_ai_span(
            trace_id=sess.trace_id,
            parent_span_id=sess.current_generation_span_id,
            tool_name=tool_name,
            args=args,
            result=result,
            duration_s=duration_s,
            session_id=session_id,
            task_id=task_id,
            privacy_mode=_privacy_mode(),
        )
        client.capture(span_evt["event"], distinct_id=span_evt["distinct_id"], properties=span_evt["properties"])
        _debug("captured $ai_span: tool=%s trace=%s", tool_name, sess.trace_id)
    except Exception as exc:
        logger.warning("posthog-hermes: on_post_tool_call error: %s", exc)


def on_session_end(
    *,
    session_id: str = "",
    task_id: str = "",
    **_: Any,
) -> None:
    """Emit $ai_trace when session ends (session-grouping mode only)."""
    try:
        if _trace_grouping() != "session":
            return
        client = _get_client()
        if client is None:
            return

        tkey = _task_key(task_id, session_id)
        with _STATE_LOCK:
            sess = _SESSION_STATE.pop(tkey, None)

        if sess is None:
            return

        now_ms = time.time() * 1000
        session_latency = (now_ms - sess.start_time_ms) / 1000
        trace_evt = build_ai_trace(
            trace_id=sess.trace_id,
            session_id=session_id,
            total_input_tokens=sess.total_input_tokens,
            total_output_tokens=sess.total_output_tokens,
            latency=session_latency,
            channel=None,
        )
        client.capture(trace_evt["event"], distinct_id=trace_evt["distinct_id"], properties=trace_evt["properties"])
        _debug("captured $ai_trace (session mode): trace=%s", sess.trace_id)
    except Exception as exc:
        logger.warning("posthog-hermes: on_session_end error: %s", exc)


def register(ctx: Any) -> None:  # noqa: ANN001
    """Hermes plugin entry point. Called once at plugin load."""
    # Register for both hook variants — pre/post_api_request fire per LLM API
    # call (Hermes ≥0.11 preferred); pre/post_llm_call are per-turn fallbacks
    # for Hermes <0.11. Handlers skip context-injection fires via messages check.
    ctx.register_hook("pre_api_request", on_pre_api_request)
    ctx.register_hook("post_api_request", on_post_api_request)
    ctx.register_hook("pre_llm_call", on_pre_api_request)
    ctx.register_hook("post_llm_call", on_post_api_request)
    ctx.register_hook("pre_tool_call", on_pre_tool_call)
    ctx.register_hook("post_tool_call", on_post_tool_call)
    ctx.register_hook("on_session_end", on_session_end)
