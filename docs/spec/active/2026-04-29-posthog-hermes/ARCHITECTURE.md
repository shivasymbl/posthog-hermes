---
document_type: architecture
project_id: SPEC-2026-04-29-001
version: 1.0.0
last_updated: 2026-04-29T00:00:00Z
status: draft
---

# posthog-hermes — Technical Architecture

## System Overview

`posthog-hermes` is a Python module loaded by the Hermes Agent runtime as a plugin. It registers seven lifecycle hooks (`pre_api_request`, `post_api_request`, `pre_llm_call`, `post_llm_call`, `pre_tool_call`, `post_tool_call`, `on_session_end`) and emits PostHog events from those hooks via the official `posthog` Python SDK.

It holds **no persistent state**, no daemon threads, no listening sockets. It is purely event-driven: a hook fires, the plugin consults its in-memory state map, builds a PostHog event dict, and hands it to the SDK's batched async dispatcher.

### Architecture Diagram

```
+--------------------------------------------------------------------------+
|                          Hermes Agent Process                            |
|                                                                          |
|  +-------------+    +-------------+    +-------------+                   |
|  |  User msg   |--->|   Agent     |--->|  LLM call   |                   |
|  +-------------+    |   loop      |    +-------------+                   |
|                     +------+------+           |                          |
|                            |                  v                          |
|                            |          +-------------+                    |
|                            |          | Tool calls  |                    |
|                            |          +-------------+                    |
|                            |                                             |
|                            v                                             |
|              +---------------------------+                               |
|              |   Hermes plugin loader    |                               |
|              |   fires lifecycle hooks   |                               |
|              +-------------+-------------+                               |
|                            |                                             |
|             +--------------+--------------+                              |
|             | hooks (kwargs)              |                              |
|             |  - pre_api_request          |                              |
|             |  - post_api_request         |                              |
|             |  - pre_tool_call            |                              |
|             |  - post_tool_call           |                              |
|             |  - on_session_end           |                              |
|             +--------------+--------------+                              |
|                            |                                             |
|                            v                                             |
|  +---------------------------------------------------------------------+ |
|  |  posthog-hermes plugin (~/.hermes/plugins/posthog/)                 | |
|  |                                                                     | |
|  |  +------------+   +-----------+   +-----------+   +-------------+   | |
|  |  | __init__.py|-->| state.py  |-->| events.py |-->| posthog SDK |---|---> PostHog Cloud / self-hosted
|  |  | hook       |   | dataclass |   | event     |   | (batched   |   | |
|  |  | handlers   |   | + module  |   | builders  |   |  async)    |   | |
|  |  +------------+   |   state   |   +-----------+   +-------------+   | |
|  |                   +-----------+                                     | |
|  +---------------------------------------------------------------------+ |
+--------------------------------------------------------------------------+
```

### Key Design Decisions

See `DECISIONS.md` for full rationale. Summary:

- **D-001**: Multi-file architecture (`__init__.py`, `state.py`, `events.py`, `helpers.py`) — modeled on `briancaffey/hermes-otel`
- **D-002**: Use canonical PostHog `$ai_*` schema, **fix** the `cache_*` prefix bug from posthog-openclaw
- **D-003**: Module-level mutable state guarded by a single threading.Lock (Hermes plugins are loaded once per process)
- **D-004**: Lazy import of Hermes internals (`agent.usage_pricing`) inside try/except — fail-open
- **D-005**: Dual hook registration (`pre/post_api_request` AND `pre/post_llm_call`) for cross-version compat
- **D-006**: PostHog Python SDK 7.x — `capture(event, distinct_id=..., properties=...)` signature (breaking change from older docs)
- **D-007**: `$ai_trace` emission strategy is configurable: `message` (end of turn, default) or `session` (end of Hermes session)
- **D-008**: No background threads; stale-state cleanup runs synchronously inside hook handlers

## Component Design

### Component: `__init__.py` — Plugin Entry & Hook Handlers

- **Purpose**: Register hooks with Hermes and dispatch hook events to event builders
- **Responsibilities**:
  - Implement `register(ctx)` (Hermes plugin entry point)
  - Lazy-init the PostHog SDK client on first hook fire (with module-level cache)
  - Define hook handlers: `on_pre_api_request`, `on_post_api_request`, `on_pre_tool_call`, `on_post_tool_call`
  - Coordinate state lookup → event build → SDK capture
  - Read configuration from environment variables
  - Wrap every handler body in try/except — never let exceptions escape to the agent
- **Interfaces**:
  - Inbound: Hermes calls `register(ctx)`; subsequent kwargs-based hook calls
  - Outbound: `posthog.Posthog.capture()` (batched), Python `logging`
- **Dependencies**: `state.py`, `events.py`, `helpers.py`, optional `posthog` SDK

### Component: `state.py` — Data Structures

- **Purpose**: Define mutable state types and the lock that guards them
- **Responsibilities**:
  - `RunState` dataclass — per-LLM-call ephemeral state (trace_id, span_id, start_time, model, provider, channel, etc.)
  - `SessionState` dataclass — per-task/session state (trace_id, total tokens, current_generation_span_id, last_output_at)
  - Module-level dicts: `_RUN_STATE`, `_SESSION_STATE`, `_TOOL_START_TIMES`
  - Module-level `_STATE_LOCK = threading.Lock()`
  - Stale-run cleanup helper (TTL = 5 minutes)
- **Interfaces**:
  - Internal — only `__init__.py` imports
- **Dependencies**: `dataclasses`, `threading`, `time`

### Component: `events.py` — Event Builders

- **Purpose**: Pure functions that build PostHog event property dicts
- **Responsibilities**:
  - `build_ai_generation(...)` → returns `(distinct_id, event_name, properties)` for LLM call
  - `build_ai_span(...)` → returns same for tool call
  - `build_ai_trace(...)` → returns same for turn / session rollup
  - Handle privacy mode (strip `$ai_input` and `$ai_output_choices`)
  - Truncate long fields per `POSTHOG_MAX_FIELD_CHARS`
  - Use canonical `$ai_*` property names (with `$ai_cache_read_input_tokens` / `$ai_cache_creation_input_tokens` — fixed from openclaw)
- **Interfaces**:
  - Pure functions, no side effects, easily unit-testable
- **Dependencies**: `state.RunState`, `helpers`

### Component: `helpers.py` — Utilities

- **Purpose**: Small leaf-level utilities
- **Responsibilities**:
  - `generate_trace_id()` — 16 hex chars
  - `generate_span_id()` — 8 hex chars
  - `safe_value(value, max_chars)` — truncate & JSON-coerce
  - `extract_usage_and_cost(usage_dict, response, ...)` — wraps `agent.usage_pricing` calls inside try/except, returns (input_tokens, output_tokens, cache_read, cache_write, reasoning, cost_usd)
  - `env_bool(name)`, `env(name, default)`, `env_int(name, default)` — env helpers
- **Dependencies**: stdlib only (plus lazy `agent.usage_pricing`)

### Component: `plugin.yaml` — Manifest

- **Purpose**: Declare plugin metadata to Hermes
- **Responsibilities**: Name, version, description, `provides_hooks`, `requires_env`
- **Interfaces**: Read by Hermes plugin loader at startup

```yaml
name: posthog
version: "0.1.0"
description: "PostHog LLM Analytics for Hermes — emits $ai_generation, $ai_span, $ai_trace events. Install via `hermes plugins enable posthog`."
author: "@shivanathd"
requires_env:
  - POSTHOG_API_KEY
provides_hooks:
  - pre_api_request
  - post_api_request
  - pre_llm_call
  - post_llm_call
  - pre_tool_call
  - post_tool_call
  - on_session_end    # required for POSTHOG_TRACE_GROUPING=session mode
```

## Data Design

### Data Models

```python
# state.py

@dataclass
class RunState:
    trace_id: str            # UUID-like 16-byte hex; shared across calls in same trace
    span_id: str             # 8-byte hex; unique per LLM call
    start_time: float        # monotonic time.time() when pre_* fired
    model: str
    provider: str
    base_url: str
    api_mode: str            # "chat" | "responses" | etc.
    input: list[dict] | None # API messages list, None if privacy mode on
    session_id: str          # Hermes session_id verbatim
    channel: str             # "slack" | "telegram" | "web" | etc.
    task_id: str             # Hermes task_id (per-LLM-call)

@dataclass
class SessionState:
    trace_id: str            # rotated per trace_grouping rule
    session_id: str          # Hermes session_id (windowed in `message` mode)
    start_time_ms: float
    last_output_at_ms: float                    # timestamp of last $ai_generation
    current_generation_span_id: str | None      # parent for tool spans within this gen
    total_input_tokens: int | None              # accumulated for $ai_trace
    total_output_tokens: int | None
```

### Module-Level State

```python
# in state.py module scope (canonical owner — see ADR-003)
_STATE_LOCK: threading.Lock
_RUN_STATE: dict[str, RunState]               # key: f"{task_id}:{api_call_count}"
_SESSION_STATE: dict[str, SessionState]       # key: _task_key(task_id, session_id)
_TOOL_START_TIMES: dict[str, float]           # key: tool_call_id

# in __init__.py module scope
_POSTHOG_CLIENT: Posthog | None | _INIT_FAILED   # cached after first init attempt
```

State dicts and lock live in `state.py`. Only `_POSTHOG_CLIENT` lives in `__init__.py` (it is the SDK client, not agent state). Hermes loads each plugin once per process, so module-level is safe. The lock serializes access; contention is low because hook fires are interleaved, not parallel.

### Data Flow

**Path 1 — LLM call (most common path):**
```
pre_api_request fires
  ↓
__init__.on_pre_api_request(**kwargs)
  ↓ (acquire lock)
state._get_or_create_session(task_key, session_id)
  ↓
RunState(trace_id, new span_id, start_time=now, ...) → _RUN_STATE[run_key]
  ↓ (release lock)

[Hermes runs the actual LLM API call]

post_api_request fires
  ↓
__init__.on_post_api_request(**kwargs)
  ↓ (acquire lock)
run = _RUN_STATE.pop(run_key)
sess = _SESSION_STATE[task_key]
sess.last_output_at_ms = now
  ↓ (release lock)
helpers.extract_usage_and_cost(usage, response, ...)
  ↓
events.build_ai_generation(run, output, tokens, cost, ...)
  ↓
posthog_client.capture("$ai_generation", distinct_id=..., properties=...)
  ↓
[if no tool_calls in assistant_message]
events.build_ai_trace(sess, ...)
  ↓
posthog_client.capture("$ai_trace", ...)
  ↓ (acquire lock)
_SESSION_STATE.pop(task_key)        # only in 'message' grouping mode
  ↓ (release lock)
```

**Path 2 — Tool call:**
```
pre_tool_call fires
  ↓
_TOOL_START_TIMES[tool_call_id] = time.time()

[tool runs]

post_tool_call fires
  ↓
duration = time.time() - _TOOL_START_TIMES.pop(tool_call_id)
sess = _SESSION_STATE[task_key]
events.build_ai_span(sess.trace_id, sess.current_generation_span_id, tool_name, args, result, duration, ...)
  ↓
posthog_client.capture("$ai_span", distinct_id=..., properties=...)
```

### Storage Strategy

- **No persistent storage**. All state is in-process Python dicts.
- **No databases, no files written by the plugin.**
- PostHog SDK manages its own internal queue with a max size of 10,000 events (`max_queue_size` default). On overflow it drops events and logs a warning.
- `posthog.flush()` and `posthog.shutdown()` are wired to clean shutdown via `atexit` (the SDK registers this automatically).

## API Design

### Plugin → Hermes (consumed)

The plugin consumes Hermes's hook API via `ctx.register_hook(name, handler)`. Handler signatures (verified from `plugins/observability/langfuse/__init__.py`):

```python
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
    messages: Any = None,           # list[dict] for legacy turn-shape; None for context-injection variant
    turn_type: str = "user",
    conversation_history: Any = None,
    user_message: Any = None,
    **_: Any,
) -> None: ...

def on_post_api_request(
    *,
    task_id: str = "",
    session_id: str = "",
    provider: str = "",
    base_url: str = "",
    api_mode: str = "",
    model: str = "",
    api_call_count: int = 0,
    assistant_message: Any = None,           # has .content, .tool_calls
    response: Any = None,                    # raw provider response, has .usage
    api_duration: float = 0.0,
    finish_reason: str = "",
    usage: dict | None = None,               # Hermes-normalized: input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, reasoning_tokens
    assistant_content_chars: int = 0,
    assistant_tool_call_count: int = 0,
    assistant_response: str | None = None,
    **_: Any,
) -> None: ...

def on_pre_tool_call(
    *,
    tool_name: str = "",
    args: Any = None,
    task_id: str = "",
    session_id: str = "",
    tool_call_id: str = "",
    **_: Any,
) -> None: ...

def on_post_tool_call(
    *,
    tool_name: str = "",
    args: Any = None,
    result: Any = None,
    task_id: str = "",
    session_id: str = "",
    tool_call_id: str = "",
    **_: Any,
) -> None: ...
```

### Plugin → PostHog (produced)

Uses **PostHog Python SDK 7.x** (current stable as of 2026-04). Note the **breaking change** from older docs: `capture(event, distinct_id=..., properties=...)` — `event` is the first positional, `distinct_id` is now a kwarg.

```python
from posthog import Posthog

client = Posthog(
    project_api_key=os.environ["POSTHOG_API_KEY"],
    host=os.environ.get("POSTHOG_HOST", "https://us.i.posthog.com"),
    flush_at=20,
    flush_interval=10,
)

client.capture(
    "$ai_generation",
    distinct_id=run.session_id,
    properties={
        "$ai_trace_id": run.trace_id,
        "$ai_session_id": run.session_id,
        "$ai_span_id": run.span_id,
        "$ai_model": run.model,
        "$ai_provider": run.provider,
        "$ai_input": redacted_input,
        "$ai_output_choices": [{"role": "assistant", "content": output_text}],
        "$ai_input_tokens": input_tokens,
        "$ai_output_tokens": output_tokens,
        "$ai_cache_read_input_tokens": cache_read_tokens,    # NOTE: $ai_ prefix — fixed from openclaw
        "$ai_cache_creation_input_tokens": cache_write_tokens,  # NOTE: $ai_ prefix
        "$ai_reasoning_tokens": reasoning_tokens,           # NEW vs openclaw
        "$ai_latency": latency_seconds,
        "$ai_total_cost_usd": total_cost_usd,
        "$ai_input_cost_usd": input_cost_usd,               # NEW vs openclaw — per-type breakdown
        "$ai_output_cost_usd": output_cost_usd,             # NEW vs openclaw
        "$ai_base_url": run.base_url,                       # NEW vs openclaw
        "$ai_http_status": getattr(response, "status_code", None),  # NEW vs openclaw, when available
        "$ai_is_error": finish_reason == "error",
        "$ai_error": error_message_if_any,
        "$ai_lib": "posthog-hermes",
        "$ai_lib_version": VERSION,
        "$ai_framework": "hermes",
        "$ai_channel": run.channel,
        # Note: $ai_stop_reason removed — not in canonical schema, $ai_is_error + $ai_error suffice
    },
)
```

## Integration Points

### Internal Integrations

| System | Integration Type | Purpose |
|--------|-----------------|---------|
| Hermes plugin loader | Python entry point (`register(ctx)`) | Plugin registration |
| Hermes hook system | Callback registration (`ctx.register_hook`) | Receive lifecycle events |
| Hermes `agent.usage_pricing` | Lazy import inside try/except | Token normalization + cost estimation |

### External Integrations

| Service | Integration Type | Purpose |
|---------|-----------------|---------|
| PostHog Cloud (US/EU) | HTTPS POST via `posthog` Python SDK | Event ingestion |
| Self-hosted PostHog | HTTPS POST via `POSTHOG_HOST` override | Event ingestion |

## Configuration

All configuration is via environment variables, read on first hook fire (cached for the life of the process).

| Env Var | Required | Default | Purpose |
|---------|----------|---------|---------|
| `POSTHOG_API_KEY` | Yes | — | Project API key (`phc_...`); plugin disables itself if missing |
| `POSTHOG_HOST` | No | `https://us.i.posthog.com` | PostHog instance URL |
| `POSTHOG_PRIVACY_MODE` | No | `false` | If `true`, strip `$ai_input` and `$ai_output_choices` |
| `POSTHOG_TRACE_GROUPING` | No | `message` | `message` = trace per turn; `session` = trace per Hermes session |
| `POSTHOG_SESSION_WINDOW_MINUTES` | No | `60` | Inactivity threshold for trace rotation |
| `POSTHOG_MAX_FIELD_CHARS` | No | `12000` | Max chars per string field; truncated with marker |
| `POSTHOG_SAMPLE_RATE` | No | `1.0` | (P2) Drop events probabilistically |
| `POSTHOG_DEFAULT_PROPERTIES` | No | — | (P2) JSON dict merged into every event |
| `HERMES_POSTHOG_DEBUG` | No | `false` | Verbose logging |

## Security Design

### Authentication
- Plugin authenticates to PostHog via `POSTHOG_API_KEY` (project key, write-only). No personal API key required for capture.

### Authorization
- Plugin assumes the project key has `$capture` permission (default for project keys).
- Plugin never reads from PostHog (no `personal_api_key` configured).

### Data Protection
- `POSTHOG_PRIVACY_MODE` strips both `$ai_input` and `$ai_output_choices` atomically (single `if privacy_mode:` branch in `events.py` covers both).
- Tool call args/results are also stripped under privacy mode (`$ai_input_state` / `$ai_output_state` — though we use `$ai_input`/`$ai_output_choices` consistently).
- API key is never logged. Debug mode logs config presence (yes/no) but not values.
- No outbound traffic when `POSTHOG_API_KEY` is unset (plugin disables itself; `requires_env` in manifest gates loading).

### Threat Model

| Threat | Mitigation |
|--------|------------|
| Leaked PostHog key in logs | Never log key; debug mode only logs presence |
| PII in `$ai_input` events | Privacy mode strips all content; documented prominently in README |
| Plugin crashes Hermes agent | Every hook handler wrapped in try/except; logs but doesn't propagate |
| State growth from leaked sessions | TTL eviction every hook fire (5-min default) |
| Slow PostHog endpoint blocks hooks | SDK is async/batched; max wait per `flush_interval=10s` is non-blocking |

## Performance Considerations

### Expected Load

A typical Hermes Agent (e.g., Jarvis on the audit) handles:
- 50–500 LLM calls per day (peak ~30/hour)
- 100–2000 tool calls per day
- Single-process (no parallel sessions)

### Performance Targets

| Metric | Target | Rationale |
|--------|--------|-----------|
| Per-hook overhead | < 5ms p95 | Capture is buffered; only state mutation is on hot path |
| Memory footprint | < 5MB steady-state | Bounded by 5-min TTL on state maps |
| Event capture latency (queue → wire) | < 30s | SDK default `flush_interval=10s` + `flush_at=20` |
| Cold start | < 100ms | First hook fire imports SDK + initializes client |

### Optimization Strategies

- **Defer SDK import**: `from posthog import Posthog` happens inside `_get_client()`, only on first hook fire.
- **Defer Hermes internal imports**: `from agent.usage_pricing import ...` inside the helper, inside try/except.
- **Lock contention minimization**: Lock held only during dict mutations, never during SDK calls or event building.
- **No background threads**: Stale cleanup runs synchronously inside hook fires (cheap O(n) where n is bounded).

## Reliability & Operations

### Availability Target
- Plugin must never reduce Hermes Agent availability. SLO: zero plugin-induced agent crashes.

### Failure Modes

| Failure | Impact | Recovery |
|---------|--------|----------|
| `posthog` SDK not installed | Plugin disables (logs warning); agent continues | User runs `pip install posthog` |
| `POSTHOG_API_KEY` missing | Plugin disables (logs warning) | User sets env var, restarts Hermes |
| PostHog endpoint unreachable | SDK queues internally; events drop after `max_queue_size=10000` | Transient — recovers on reconnect |
| `agent.usage_pricing` import fails (older Hermes) | Token cost fields are null; tokens still captured | None needed — graceful degradation |
| Hook signature mismatch (Hermes upgrade) | `**kwargs` swallows new args; old args still consumed | Forward-compatible; user reports if a field is missing |
| Plugin handler raises | Caught by outer try/except; logged at WARNING; agent continues | Bug — file issue |

### Monitoring & Alerting

The plugin itself doesn't expose metrics endpoints. Operators should monitor on the **PostHog side**:
- Event volume (drops indicate plugin failure)
- Error events (`$ai_is_error=true` rate)
- Cost trend (`$ai_total_cost_usd` sum)

Operator-side debugging:
- `HERMES_POSTHOG_DEBUG=true` enables verbose log output
- Hermes plugin status: `hermes plugins list` shows enabled plugins

## Testing Strategy

### Unit Testing
- **Coverage target**: ≥ 80% on `__init__.py` + `events.py`
- **Framework**: `pytest` + `pytest-cov`
- **Approach**: Mock the PostHog client (replace `_POSTHOG_CLIENT` with a `MagicMock`); assert on `client.capture` calls
- **Coverage**:
  - Each event builder (`build_ai_generation`, `build_ai_span`, `build_ai_trace`)
  - Each hook handler (assert state transitions + capture calls)
  - Privacy mode (assert content fields are null)
  - Trace grouping (assert different behavior in `message` vs `session` modes)
  - Stale cleanup (assert eviction after TTL)
  - Error paths (PostHog SDK raises, agent.usage_pricing import fails, etc.)

### Integration Testing
- **Fixture**: Mock Hermes `ctx` with `register_hook` capturing handlers
- **Scenarios**:
  - Single LLM call → `$ai_generation` + `$ai_trace`
  - LLM call → tool call → LLM call → `$ai_generation` + `$ai_span` + `$ai_generation` + `$ai_trace` (proper parent linkage)
  - 50-call run with mixed sessions → no state leak
  - PostHog SDK raises mid-capture → plugin recovers, no data loss for next event

### Schema Conformance Testing
- **Fixture**: Frozen JSON examples of expected `$ai_generation`, `$ai_span`, `$ai_trace` payloads
- **Assertion**: `pytest` snapshot tests; CI fails on schema drift
- **Source**: `posthog-openclaw/src/events.test.ts` is the reference schema (with our prefix fixes documented)

## Deployment Considerations

### Environment Requirements

The plugin runs in-process with Hermes. No additional infrastructure required.

- **Python**: 3.11+
- **OS**: Anywhere Hermes runs (Linux, macOS, Windows via Hermes guidance)
- **Network**: Outbound HTTPS to PostHog host

### Configuration Management

Operator workflow:
```bash
# 1. Clone the plugin into Hermes plugins dir
git clone https://github.com/shivanathd/posthog-hermes ~/.hermes/plugins/posthog

# 2. Install the PostHog SDK (will move to a requirements.txt in the plugin)
pip install posthog

# 3. Set credentials
echo "POSTHOG_API_KEY=phc_xxxxxxxxxx" >> ~/.hermes/.env
echo "POSTHOG_HOST=https://us.i.posthog.com" >> ~/.hermes/.env  # or self-hosted URL

# 4. Enable
hermes plugins enable posthog

# 5. Restart Hermes gateway
hermes gateway restart  # or systemctl --user restart hermes-gateway
```

### Rollout Strategy
- **Stage 1 (Jarvis migration)**: Deploy alongside Langfuse plugin; validate event parity in PostHog UI
- **Stage 2 (public release)**: Tag v0.1.0 in GitHub, announce on Hermes Discord, hermes-agent GitHub Discussions, PostHog Slack
- **Stage 3 (v1.0)**: After 30 days of community feedback and at least 3 production deployments

### Rollback Plan
- `hermes plugins disable posthog` — instant disable
- `rm -rf ~/.hermes/plugins/posthog` — full uninstall
- Plugin holds no persistent state, so removal is clean

## Future Considerations (Out of Scope for v1)

- **MCP server bridge**: Expose plugin metrics via MCP for agent self-observability
- **Tool registration**: Add `posthog_query_traces` tool so the agent can read its own observability data
- **Slash commands**: `/posthog-status`, `/posthog-flush`
- **Pip packaging**: `pip install posthog-hermes` if community demand warrants
- **PostHog Recordings**: Send Slack/Telegram conversation transcripts as session recordings
- **PostHog Feature Flags**: Read flags into agent context (requires `personal_api_key` flow)
- **Group analytics**: Tag events with `groups={"agent_id": "jarvis"}` for multi-agent installs
