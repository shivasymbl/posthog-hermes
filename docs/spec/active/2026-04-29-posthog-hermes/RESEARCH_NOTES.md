---
document_type: research
project_id: SPEC-2026-04-29-001
last_updated: 2026-04-29T00:00:00Z
---

# posthog-hermes — Research Notes

## Research Summary

Research focused on three concrete questions, all directly answered:

1. **What is the current PostHog Python SDK API?** → 7.x, with a breaking change on `capture()` signature (event-first positional, distinct_id-as-kwarg).
2. **What is the canonical PostHog `$ai_*` event schema?** → Documented at `posthog-js-lite/posthog-ai/src/utils.ts` and posthog.com docs. Includes properties not in posthog-openclaw.
3. **What does posthog-openclaw differ on, and is any of it a bug?** → Yes: `cache_*` token properties are missing the `$ai_` prefix in openclaw, breaking PostHog UI cache cost views.

These findings directly informed ADR-002 (canonical schema with bug fix) and ADR-006 (SDK 7.x signature).

## Codebase Analysis

### Reference Plugins Examined

| Repo | Purpose | Relevance |
|------|---------|-----------|
| `PostHog/posthog-openclaw` | TypeScript reference port for OpenClaw | Source-of-truth for event schema mapping (with documented bug) |
| `NousResearch/hermes-agent/plugins/observability/langfuse/` | In-tree Hermes plugin | **Ground truth** for Hermes hook API and signatures |
| `briancaffey/hermes-otel` | Community OTel plugin | Best multi-file architectural reference |
| `GuanceCloud/hermes-otel-plugin` | Alternative OTel plugin | Manifest field reference (uses `manifest_version: 1`, `provides_hooks` field) |
| `42-evey/hermes-plugins/evey-telemetry` | Single-file telemetry plugin | Sanity check on minimal viable shape |
| `NousResearch/hermes-agent/plugins/__init__.py` | Plugin loader | Confirms only `observability/langfuse` is in-tree (single observability plugin pre-this) |

### Hermes Hook API (verified from langfuse plugin)

The Langfuse plugin's `__init__.py` is the most authoritative source for hook signatures since it's in-tree and known-good. Key extraction:

**`pre_api_request` / `pre_llm_call`** kwargs (subset relevant to us):
```
task_id, session_id, platform, model, provider, base_url, api_mode,
api_call_count, messages, turn_type, conversation_history, user_message
```
- `messages` is a `list[dict]` for the legacy/per-API-call variant
- `messages` is `None` for the v0.11 turn-scoped variant (used for context injection)
- The Langfuse plugin uses `if not isinstance(messages, list): return` to skip the second variant — we do the same

**`post_api_request` / `post_llm_call`** kwargs:
```
task_id, session_id, provider, base_url, api_mode, model, api_call_count,
assistant_message, response, api_duration, finish_reason, usage,
assistant_content_chars, assistant_tool_call_count, assistant_response
```
- `usage` is a Hermes-normalized dict (keys: `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_tokens`, `reasoning_tokens`)
- `response` is the raw provider response, has `.usage` attribute (use `agent.usage_pricing.normalize_usage(raw, ...)` to canonicalize)
- `assistant_message` has `.content`, `.tool_calls`
- `assistant_response` is the plain string content of the final assistant message

**`pre_tool_call` / `post_tool_call`** kwargs:
```
tool_name, args, [result], task_id, session_id, tool_call_id
```
- `result` is on `post_*` only
- `tool_call_id` links the pre and post fires
- **No `duration_ms`** — the Langfuse plugin tracks duration by recording `time.time()` in `pre_tool_call` and computing in `post_tool_call`. We follow this pattern.

### Key Hermes Internal: `agent.usage_pricing`

Path: `agent/usage_pricing.py` in the Hermes repo

Functions we depend on (lazily imported per ADR-004):
- `normalize_usage(raw_usage, provider, api_mode) -> CanonicalUsage` — converts provider-specific usage objects to a consistent shape
- `estimate_usage_cost(model, canonical_usage, provider, base_url, api_key) -> Cost` — returns `.amount_usd`
- `get_pricing_entry(model, provider, base_url) -> PricingEntry | None` — exposes per-token-type pricing for breakdown
- `CanonicalUsage` dataclass with `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_tokens`, `reasoning_tokens` fields

The Langfuse plugin's `_usage_and_cost` function is a direct reference for how to use these.

## Technical Research

### PostHog Python SDK 7.x — The Breaking Change

Source: https://github.com/PostHog/posthog-python (master at the time of research, version 7.13.1)

**Critical signature change** in `Client.capture()`:

Old (pre-7.x):
```python
posthog.capture(distinct_id, event, properties=...)
```

New (7.x+):
```python
posthog.capture(event, distinct_id=..., properties=...)
```

If we wrote our plugin against the old signature (which most older docs/tutorials still show), it would silently send events with the wrong `distinct_id` — they'd be the `event` string, with `event` in `distinct_id`. **This is a real footgun.**

Constructor — the relevant kwargs for our use:
```python
Posthog(
    project_api_key=...,
    host="https://us.i.posthog.com",
    flush_at=20,            # default 100; we lower for faster ingest of small bursts
    flush_interval=10,      # default 0.5; we raise for less network chatter
    on_error=callback,      # for our debug logger
    sync_mode=False,        # default False; we keep async batched
    timeout=15,             # default 15s
    disable_geoip=True,     # we don't need geoip for agent traffic
)
```

Lifecycle methods we use:
- `capture(event, distinct_id=..., properties=...)`
- `flush()` — block until queue drains (optional in our hooks; we let SDK auto-batch)
- `shutdown()` — flush + stop consumer; SDK registers via `atexit`, so we don't call it manually

### Canonical PostHog `$ai_*` Schema

Sources:
- `posthog-js-lite/posthog-ai/src/utils.ts` — `sendEventToPosthog` function (canonical SDK reference)
- https://posthog.com/docs/ai-engineering — official docs
- https://posthog.com/docs/llm-analytics — LLM analytics product docs

The canonical `$ai_generation` properties (from `posthog-ai/src/utils.ts`):

```
$ai_provider                $ai_model                   $ai_model_parameters
$ai_input                   $ai_output_choices          $ai_http_status
$ai_input_tokens            $ai_output_tokens           $ai_reasoning_tokens
$ai_cache_read_input_tokens $ai_cache_creation_input_tokens
$ai_latency                 $ai_trace_id                $ai_base_url
$ai_tools                   $ai_is_error                $ai_error
$ai_input_cost_usd          $ai_output_cost_usd         $ai_total_cost_usd
$process_person_profile
```

For `$ai_span` and `$ai_trace`, the canonical schema is **less centralized** — these event types exist (the LangChain handler emits them) but their property sets aren't defined in a single canonical file. They're effectively "any `$ai_*` property the UI knows how to render."

Common `$ai_span` properties (from posthog-openclaw + LangChain handler):
```
$ai_trace_id $ai_session_id $ai_span_id $ai_parent_id $ai_span_name
$ai_input_state $ai_output_state $ai_latency $ai_is_error $ai_error
```

Common `$ai_trace` properties:
```
$ai_trace_id $ai_session_id $ai_latency
$ai_total_input_tokens $ai_total_output_tokens
$ai_is_error $ai_error
```

### posthog-openclaw vs Canonical — Complete Diff

Properties **emitted by openclaw but not in canonical schema**:
- `$ai_session_id` — used in posthog-ai integrations (LangChain handler) but not the bare-OpenAI wrapper. Acceptable as a custom-namespaced property.
- `$ai_span_name`, `$ai_parent_id`, `$ai_span_id` — span event properties; standard but not in core utils.ts.
- `$ai_stop_reason` — NOT used by canonical schema or PostHog UI. We **drop** this.
- `$ai_input_state`, `$ai_output_state` — span-specific input/output. Used in openclaw for tool spans; canonical uses `$ai_input` / `$ai_output_choices` consistently for both. We follow openclaw on this for span events (it's the more granular approach).
- `$ai_total_input_tokens`, `$ai_total_output_tokens` — only on `$ai_trace`, sums across the trace's generations. Canonical schema uses just `$ai_input_tokens` / `$ai_output_tokens` for traces; we keep the openclaw "total" naming since it's more accurate semantically and the UI treats them the same.
- `$ai_lib`, `$ai_lib_version`, `$ai_framework`, `$ai_channel`, `$ai_agent_id` — custom metadata, accepted as properties.

Properties **emitted by openclaw with WRONG names** (the bug):
- `cache_read_input_tokens` → should be `$ai_cache_read_input_tokens`
- `cache_creation_input_tokens` → should be `$ai_cache_creation_input_tokens`

**Impact of the bug**: PostHog's LLM Analytics UI looks for properties starting with `$ai_*` to populate cache cost views, cache hit rate dashboards, and cost-by-token-type charts. Properties without the prefix appear as raw event properties but aren't picked up by the LLM Analytics product views. So openclaw users see input/output cost correctly but cache costs always show as zero.

Properties **in canonical but missing from openclaw**:
- `$ai_reasoning_tokens` — important for reasoning models (o1, Claude with extended thinking). We **add**.
- `$ai_input_cost_usd`, `$ai_output_cost_usd` — per-type cost breakdown. Openclaw only emits `$ai_total_cost_usd`. We **add** via `agent.usage_pricing.get_pricing_entry`.
- `$ai_http_status` — useful for debugging. We **add** when available from `response`.
- `$ai_base_url` — useful for distinguishing multi-provider setups. We **add**.
- `$ai_model_parameters` — model temperature, max_tokens, etc. **Defer** to v0.2 (not in Hermes hook payload directly).
- `$ai_tools` — list of tools available at the time of the call. **Defer** (would need to extract from `messages` `tools` field).

## Competitive Analysis

### Similar Solutions

| Solution | Strengths | Weaknesses | Applicability |
|----------|-----------|------------|---------------|
| `posthog-openclaw` (TypeScript) | Official; full PostHog feature parity for OpenClaw | Wrong host runtime; has the cache_* bug | Reference for event schema only |
| `briancaffey/hermes-otel` | Multi-backend (Phoenix, Langfuse, LangSmith); production architecture | OTel-only (PostHog isn't an OTel-supported export) | Architectural reference only |
| `GuanceCloud/hermes-otel-plugin` | All Hermes hooks (including `on_session_*`); clean manifest | OTel-only | Manifest reference only |
| `NousResearch/hermes-agent` Langfuse plugin | In-tree, blessed, currently maintained | Single-file (875 lines); Langfuse-only | **Ground truth** for hook API |
| `42-evey/hermes-plugins` | 36+ Hermes plugins to study | Most are minimal scaffolds | Sanity check on canonical shape |

### Lessons Learned from Others

- **Langfuse plugin**: Module-level state with single Lock works well for Hermes's single-process model. Skip pattern (`if not isinstance(messages, list): return`) is the right way to handle the dual-fire of `pre_llm_call` between v0.10 and v0.11. Try/except around the whole hook body is mandatory.
- **briancaffey/hermes-otel**: A pluggable backend abstraction (`backends.py`) is overkill when there's only one target. We don't replicate that abstraction. Their `session_state.py` and `span_tracker.py` separation IS worth replicating — it makes per-session bookkeeping testable in isolation.
- **GuanceCloud/hermes-otel-plugin**: Their `manifest_version: 1` field doesn't appear in the Langfuse plugin's manifest, so it's optional. We omit it.
- **42-evey style (single-file)**: For an observability plugin with state + multiple event types, single-file becomes hard to read. We rejected.

## Dependency Analysis

### Recommended Dependencies (runtime)

| Dependency | Version | Purpose | License |
|------------|---------|---------|---------|
| `posthog` | `>=7.0,<8.0` | PostHog Python SDK | MIT |

### Recommended Dependencies (dev)

| Dependency | Version | Purpose | License |
|------------|---------|---------|---------|
| `pytest` | `>=8.0` | Test framework | MIT |
| `pytest-cov` | `>=5.0` | Coverage | MIT |
| `pytest-benchmark` | `>=4.0` | Performance microbenchmarks (optional) | BSD |
| `pyright` | `>=1.1` | Type checking | MIT |
| `ruff` | `>=0.4` | Lint + format | MIT |

### Dependency Risks

- **`posthog` SDK 7.x → 8.x**: PostHog's SDK has had three major versions in three years. We pin `<8` and document the upgrade path; bumping is a v0.2 task when 8.x stabilizes.
- **`agent.usage_pricing` import path**: Hermes-internal. If the path changes in a future Hermes version, our lazy import returns `None`-tuple and tokens still flow (just without per-type cost breakdown). Acceptable degradation.

## Open Questions from Research

- [ ] Does Hermes ever fire `pre_api_request` without a corresponding `post_api_request` (streaming abort, network failure)? — Need to test on real Hermes during Phase 3 integration test
- [ ] What does Hermes do when an LLM call returns 429? Is there a `post_api_request` with `finish_reason="error"` or just no fire? — Phase 3
- [ ] Does Hermes batch mode (`batch_runner.py`) fire hooks the same way as interactive mode? — Defer until/unless someone reports it broken
- [ ] When do `$ai_session_id` and `$ai_trace_id` differ in the canonical schema? Our model has them differ in `message` mode (one trace per turn, one session_id per Hermes session) and equal in `session` mode — is that the convention? — PostHog docs are ambiguous; we go with the openclaw convention

## Sources

- https://github.com/PostHog/posthog-openclaw — TS reference plugin (with `cache_*` schema bug)
- https://github.com/PostHog/posthog-openclaw/blob/main/src/events.ts — exact event schema reference
- https://github.com/PostHog/posthog-python — Python SDK source; CHANGELOG documents 7.x signature change
- https://pypi.org/pypi/posthog/json — current version (7.13.1 as of 2026-04-24)
- https://github.com/PostHog/posthog-js-lite/blob/main/posthog-ai/src/utils.ts — canonical `$ai_*` schema source-of-truth
- https://posthog.com/docs/ai-engineering — official LLM observability docs
- https://posthog.com/docs/llm-analytics — LLM Analytics product docs
- https://github.com/NousResearch/hermes-agent — Hermes Agent runtime
- https://github.com/NousResearch/hermes-agent/tree/main/plugins/observability/langfuse — verified hook API reference (in-tree)
- https://github.com/NousResearch/hermes-agent/blob/main/agent/usage_pricing.py — pricing module we lazily import
- https://github.com/briancaffey/hermes-otel — multi-file architecture reference
- https://github.com/briancaffey/hermes-otel/blob/main/DESIGN.md — design doc patterns to follow
- https://github.com/GuanceCloud/hermes-otel-plugin — manifest reference
- https://github.com/42-evey/hermes-plugins — community plugin gallery (minimal-shape sanity check)
- https://github.com/PostHog/posthog-python/blob/master/posthog/client.py — `Client` class source for verified `capture()` signature
