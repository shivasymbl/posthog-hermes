---
document_type: decisions
project_id: SPEC-2026-04-29-001
---

# posthog-hermes — Architecture Decision Records

## ADR-001: Multi-file architecture, modeled on briancaffey/hermes-otel

**Date**: 2026-04-29
**Status**: Accepted
**Deciders**: shivanathd

### Context

A Hermes plugin can be a single `__init__.py` (the official minimal example) or split across multiple files. We surveyed three real plugins:

- `42-evey/hermes-plugins/evey-telemetry`: single `__init__.py` + `plugin.yaml` (~50 lines total)
- `NousResearch/hermes-agent/plugins/observability/langfuse`: single 875-line `__init__.py`
- `briancaffey/hermes-otel`: 11+ Python files (`__init__.py`, `hooks.py`, `tracer.py`, `session_state.py`, `span_tracker.py`, `backends.py`, `helpers.py`, etc.)

`posthog-openclaw` is also multi-file (`plugin.ts`, `events.ts`, `types.ts`, `utils.ts`).

### Decision

Adopt a 4-file Python plugin structure:
- `__init__.py` — `register(ctx)` + hook handlers (thin coordination layer)
- `state.py` — dataclasses + module-level state dicts
- `events.py` — pure event-builder functions
- `helpers.py` — small utilities (id generation, env parsing, usage extraction)

### Consequences

**Positive:**
- Clear separation between hook coordination and event construction
- `events.py` is pure functions → trivially unit-testable
- Future contributors can find code by domain
- Aligns with the most production-grade reference (briancaffey)

**Negative:**
- Slight overhead vs single-file for new readers
- Slightly more files to drop into `~/.hermes/plugins/posthog/`

**Neutral:**
- Hermes loads all `*.py` files in the plugin dir, so structure has no runtime cost

### Alternatives Considered

1. **Single `__init__.py`**: Matches the 42-evey style and the official minimal example. **Rejected** because Langfuse already shows that grows to 875 lines; observability plugins are inherently multi-concern.
2. **briancaffey-style 11-file split**: Includes a `backends.py` for pluggable export targets. **Rejected** because we have one backend (PostHog) and don't want to over-abstract.

---

## ADR-002: Use canonical PostHog `$ai_*` schema; fix the cache_* prefix bug from posthog-openclaw

**Date**: 2026-04-29
**Status**: Accepted
**Deciders**: shivanathd

### Context

Research turned up a real schema bug in `posthog-openclaw/src/events.ts`:
- It emits `cache_read_input_tokens` and `cache_creation_input_tokens` **without the `$ai_` prefix**
- The canonical PostHog LLM Analytics schema (verified from `posthog-js-lite/posthog-ai/src/utils.ts` and the official docs at https://posthog.com/docs/llm-analytics) uses `$ai_cache_read_input_tokens` and `$ai_cache_creation_input_tokens`
- The PostHog UI's cost attribution and cache analytics views rely on the `$ai_` prefix to discover these properties

posthog-openclaw is the upstream reference for our schema, but it has this bug. We have a choice: replicate the bug for "schema parity" or fix it.

The canonical schema also includes properties that `posthog-openclaw` doesn't emit: `$ai_reasoning_tokens`, `$ai_input_cost_usd`, `$ai_output_cost_usd`, `$ai_http_status`, `$ai_base_url`, `$ai_model_parameters`, `$ai_tools`.

### Decision

Use the canonical PostHog `$ai_*` schema. Specifically:

1. **Fix the prefix bug**: emit `$ai_cache_read_input_tokens` and `$ai_cache_creation_input_tokens`
2. **Add canonical fields where data is available**:
   - `$ai_reasoning_tokens` — from Hermes `usage` dict (`reasoning_tokens` key)
   - `$ai_input_cost_usd` and `$ai_output_cost_usd` — from `agent.usage_pricing.get_pricing_entry`
   - `$ai_base_url` — from `base_url` kwarg
   - `$ai_http_status` — from `getattr(response, "status_code", None)` when available
3. **Drop non-canonical fields**:
   - `$ai_stop_reason` — not in canonical; `$ai_is_error` + `$ai_error` cover the same need
4. **Keep openclaw extensions where useful** (these are accepted as custom properties by the PostHog UI):
   - `$ai_lib`, `$ai_lib_version`, `$ai_framework`, `$ai_channel`
   - `$ai_span_id`, `$ai_parent_id`, `$ai_span_name` — used by `$ai_span` events
5. **Document the deviation** in README so anyone diff'ing against `posthog-openclaw` sees why.

### Consequences

**Positive:**
- PostHog UI cache cost views work correctly (the openclaw bug is silent — events flow but cost attribution is wrong)
- Better dashboards via `$ai_input_cost_usd` / `$ai_output_cost_usd` per-type breakdown
- `$ai_reasoning_tokens` captured for o1/Claude reasoning models — important for Hermes since reasoning models are common
- Forward-compatible with future PostHog schema additions

**Negative:**
- Dashboards built specifically against `posthog-openclaw` event shapes (with the bug) will show 0 cache tokens until updated to use `$ai_cache_*` names
- Slight risk of being "out of sync" with openclaw if PostHog updates either side

**Neutral:**
- Canonical and openclaw schemas overlap on 90% of properties; user-facing impact is minimal

### Alternatives Considered

1. **Replicate openclaw exactly (including the bug)**: **Rejected** — propagating a known bug to a community port would be a disservice. We file a separate issue against posthog-openclaw.
2. **Emit BOTH `cache_*` and `$ai_cache_*`**: **Rejected** — duplicates events into PostHog's storage; no operational benefit.

### Follow-up

- Open issue against `PostHog/posthog-openclaw` referencing this finding
- Note in our README that we differ from openclaw on this point

---

## ADR-003: Module-level mutable state with single threading.Lock

**Date**: 2026-04-29
**Status**: Accepted
**Deciders**: shivanathd

### Context

The plugin needs to correlate `pre_api_request` with `post_api_request` (separated by an LLM API call), and `pre_tool_call` with `post_tool_call` (separated by tool execution). This requires per-call ephemeral state.

Three options for state storage:
1. Module-level dicts guarded by a single Lock (Langfuse plugin does this)
2. A dedicated state class instantiated in `register(ctx)` and captured in closures
3. Per-handler state in a `dict[task_key, ...]` carried via `ctx` (but Hermes doesn't support this)

### Decision

Use module-level mutable dicts (`_RUN_STATE`, `_SESSION_STATE`, `_TOOL_START_TIMES`) guarded by a single `_STATE_LOCK = threading.Lock()`.

Cleanup runs synchronously at the start of each hook fire, evicting entries with `last_updated_at` older than 5 minutes.

### Consequences

**Positive:**
- Matches the proven pattern from `plugins/observability/langfuse/`
- Trivial to reason about; no closure capture, no class instantiation
- One lock means no deadlock risk
- Module-level scope is exactly the lifetime we want (per-process)

**Negative:**
- Module-level state is harder to reset in tests — must explicitly clear dicts in test fixtures (we'll provide a `_reset_for_tests()` helper)
- Single lock could become contention point under extreme parallelism (not a real risk for Hermes single-process)

**Neutral:**
- Hermes plugin instances are not serialized, hot-reloaded, or spawned in subprocesses, so module state is fine

### Alternatives Considered

1. **Class-based state with closure capture**: More "Pythonic" but adds indirection without benefit; rejected.
2. **`threading.local()` for per-thread state**: Hermes doesn't run hooks across threads (asyncio single-threaded model), so this would just be slower module-level state.
3. **`weakref` for auto-eviction**: Overkill; explicit TTL is clearer.

---

## ADR-004: Lazy imports for optional and Hermes-internal dependencies

**Date**: 2026-04-29
**Status**: Accepted
**Deciders**: shivanathd

### Context

Two dependencies are not guaranteed to be present:
- `posthog` (Python SDK) — operator must `pip install posthog` separately; we should fail open if not installed
- `agent.usage_pricing` — Hermes internal module; may move or be removed in future versions

If we `import posthog` at module top-level and the SDK is missing, the plugin crashes with `ModuleNotFoundError` and Hermes refuses to load it.

### Decision

Use the same pattern as the in-tree Langfuse plugin:

```python
# Top-level — try once
try:
    from posthog import Posthog
except Exception:
    Posthog = None

# Inside function — try lazily
def _extract_usage_and_cost(...):
    try:
        from agent.usage_pricing import normalize_usage, estimate_usage_cost
        ...
    except Exception:
        return (None, None, None, None, None, None)
```

A sentinel `_INIT_FAILED = object()` caches "we tried and it didn't work" so subsequent hook fires fast-return without re-trying.

### Consequences

**Positive:**
- Plugin loads cleanly even when `posthog` SDK isn't installed
- Hooks become no-ops; agent continues normally
- Forward-compatible if Hermes restructures `agent.usage_pricing`

**Negative:**
- Slight cognitive overhead — readers must remember the lazy pattern
- Errors during pricing extraction are silently swallowed (mitigated by debug log)

**Neutral:**
- Standard pattern in the Hermes plugin ecosystem

### Alternatives Considered

1. **Hard requirement on `posthog`**: **Rejected** — would force Hermes operators to install a dep they may not need (or may not want, if they're testing the plugin without a PostHog key).
2. **Vendoring `agent.usage_pricing` logic**: **Rejected** — pricing tables get stale; better to delegate to Hermes.

---

## ADR-005: Dual hook registration for cross-version Hermes compatibility

**Date**: 2026-04-29
**Status**: Accepted
**Deciders**: shivanathd

### Context

Hermes underwent a hook refactor between v0.10 and v0.11:
- **v0.10**: `pre_llm_call` / `post_llm_call` fire once per LLM API call with the full `messages` list
- **v0.11+**: `pre_api_request` / `post_api_request` fire once per LLM API call (preferred); `pre_llm_call` / `post_llm_call` still fire once per turn but for context injection (no `messages` list)

Registering only `pre_api_request` would break on v0.10. Registering only `pre_llm_call` would create orphan traces on v0.11.

The Langfuse plugin solves this by registering both and using a runtime check (`if not isinstance(messages, list): return`) to skip the v0.11 turn-scoped variant.

### Decision

Register both hook variants, mirror the Langfuse skip pattern:

```python
def register(ctx):
    ctx.register_hook("pre_api_request", on_pre_api_request)
    ctx.register_hook("post_api_request", on_post_api_request)
    ctx.register_hook("pre_llm_call", on_pre_api_request)    # alias for Hermes <0.11
    ctx.register_hook("post_llm_call", on_post_api_request)  # alias for Hermes <0.11
    ctx.register_hook("pre_tool_call", on_pre_tool_call)
    ctx.register_hook("post_tool_call", on_post_tool_call)
    ctx.register_hook("on_session_end", on_session_end)      # required for session-mode $ai_trace

def on_pre_api_request(*, messages=None, **kwargs):
    if not isinstance(messages, list):
        return  # context-injection fire on v0.11; skip
    ...
```

### Consequences

**Positive:**
- Plugin works on Hermes v0.10 and v0.11+
- No special-casing in operator config
- Aligns with Langfuse plugin behavior — operators see consistent semantics across observability plugins

**Negative:**
- Two hook registrations for the same handler is mildly redundant
- If Hermes changes the hook signature again, the skip check needs updating

**Neutral:**
- Hermes is permissive — registering a hook the runtime doesn't fire is a no-op

### Alternatives Considered

1. **Detect Hermes version at register time, register conditionally**: **Rejected** — adds complexity, no version constant available, fragile.
2. **Only register `post_api_request` (preferred path)**: **Rejected** — breaks v0.10 entirely; one of our user personas is the OpenClaw → Hermes migrator who may be on older Hermes.

---

## ADR-006: PostHog Python SDK 7.x — `capture(event, distinct_id=..., properties=...)`

**Date**: 2026-04-29
**Status**: Accepted
**Deciders**: shivanathd

### Context

PostHog Python SDK 7.x (current stable as of 2026-04, version 7.13.1) introduced a **breaking change** to `Posthog.capture()`:
- **Pre-7.x**: `capture(distinct_id, event, properties=...)` — `distinct_id` first positional
- **7.x+**: `capture(event, distinct_id=..., properties=...)` — `event` first positional, `distinct_id` is now a kwarg

The change is documented at `https://github.com/PostHog/posthog-python/blob/master/CHANGELOG.md`.

`posthog-openclaw` uses the Node.js SDK with a different signature, so it's not a useful reference here.

### Decision

Target PostHog Python SDK ≥ 7.0, use the new signature exclusively:

```python
client.capture(
    "$ai_generation",
    distinct_id=run.session_id,
    properties={...},
)
```

Document minimum version in README.

### Consequences

**Positive:**
- Forward-compatible with the active PostHog SDK line
- Cleaner, more readable call sites (event name is the focal point)

**Negative:**
- Operators on older `posthog<7.0` will see `TypeError`. We surface this with a clear log message in `_get_client()`:
  ```
  posthog-hermes: posthog SDK version 6.x detected; this plugin requires >=7.0. Run `pip install -U posthog`.
  ```
- Pre-flight version check adds ~5 lines

**Neutral:**
- 7.x has been stable since late 2025

### Alternatives Considered

1. **Support both signatures via runtime detection**: **Rejected** — adds branching forever; better to require modern SDK.
2. **Use `posthog<7` with the old signature**: **Rejected** — pinning to a deprecated major is technical debt.

---

## ADR-007: `$ai_trace` emission strategy is configurable (`message` vs `session`)

**Date**: 2026-04-29
**Status**: Accepted
**Deciders**: shivanathd

### Context

`posthog-openclaw` emits `$ai_trace` once per "message processed" diagnostic event — i.e., once per user turn. This is the granularity that matches PostHog's trace concept best.

Hermes has no diagnostic event bus, but it does have `on_session_end` and `on_session_finalize` hooks that fire once per Hermes session. A session can contain many user turns.

Two emission strategies:
- **Per-turn** (matches openclaw): Emit `$ai_trace` from `post_api_request` when the assistant has no pending tool calls (Langfuse plugin uses this approach for trace teardown)
- **Per-session**: Emit `$ai_trace` from `on_session_end` only

Per-turn produces more granular traces (better for debugging individual interactions). Per-session produces more focused traces (better for analyzing whole conversations).

### Decision

Make it configurable via `POSTHOG_TRACE_GROUPING=message|session`. Default to `message` (matches openclaw).

In `message` mode:
- `$ai_trace` fires inside `on_post_api_request` when `assistant_tool_call_count == 0` and `assistant_message.tool_calls` is empty
- `_SESSION_STATE[task_key]` is popped after emission
- New trace_id is generated on next `pre_api_request`

In `session` mode:
- `$ai_trace` fires only from `on_session_end`
- Single `_SESSION_STATE` entry persists across many LLM calls
- Token totals accumulate for the entire session
- Trace rotation only happens on session window timeout (`POSTHOG_SESSION_WINDOW_MINUTES`)

### Consequences

**Positive:**
- Operators choose the granularity that fits their analysis style
- Matches openclaw default → drop-in dashboards work
- Session mode supports long-running multi-turn conversations as one logical unit

**Negative:**
- Two code paths in `on_post_api_request`; tested and documented
- Schema is the same; only the emission timing differs

**Neutral:**
- Token counts are accumulated either way; just emitted differently

### Alternatives Considered

1. **Per-turn only**: **Rejected** — gives openclaw parity but loses session-mode flexibility for users who want it.
2. **Per-session only**: **Rejected** — diverges from openclaw without justification; harder to debug individual turns.
3. **Both, always emit both**: **Rejected** — doubles event volume with no clear analytical win.

---

## ADR-008: No background threads; synchronous stale-state cleanup

**Date**: 2026-04-29
**Status**: Accepted
**Deciders**: shivanathd

### Context

State maps need TTL eviction to prevent growth from leaked sessions. Two cleanup approaches:
1. Background thread that runs every N seconds and walks the maps
2. Synchronous cleanup at the top of each hook handler

### Decision

Synchronous cleanup. At the top of `on_pre_api_request`, walk the state maps and evict any entry with `start_time + 5min < now`. Cost is O(n) where n is bounded by ~100 entries in steady state.

### Consequences

**Positive:**
- No daemon threads = no shutdown coordination, no zombie thread risk on Hermes restart
- Predictable performance — cleanup happens deterministically with hook fires
- Simpler to test (no `time.sleep` in tests)

**Negative:**
- If hooks stop firing entirely (agent idle), stale state never cleans up. Real impact: bounded by typical state dict size (~100), trivial memory.

**Neutral:**
- The Langfuse plugin uses the same approach

### Alternatives Considered

1. **`threading.Timer` for periodic cleanup**: **Rejected** — adds shutdown coordination complexity for negligible benefit.
2. **`asyncio.create_task` for periodic cleanup**: **Rejected** — Hermes plugins don't have access to the agent's event loop without coupling.
