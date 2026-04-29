---
document_type: requirements
project_id: SPEC-2026-04-29-001
version: 1.0.0
last_updated: 2026-04-29T00:00:00Z
status: draft
---

# posthog-hermes — Product Requirements Document

## Executive Summary

`posthog-hermes` is a community-built observability plugin for the [Hermes Agent](https://github.com/NousResearch/hermes-agent) runtime. It emits PostHog LLM Analytics events (`$ai_generation`, `$ai_span`, `$ai_trace`) for every model call, tool invocation, and conversational turn, giving operators a turnkey way to send Hermes agent traces into PostHog dashboards.

The plugin is a Python port of PostHog's official [`posthog-openclaw`](https://github.com/PostHog/posthog-openclaw) plugin. It mirrors the same event schema and configuration knobs but is rebuilt against Hermes's hook-based plugin API instead of OpenClaw's diagnostic event bus. It is structurally modeled on existing in-tree Hermes observability plugins (`plugins/observability/langfuse/`) and the community OTel plugins (`briancaffey/hermes-otel`, `GuanceCloud/hermes-otel-plugin`).

Success means a Hermes operator can clone this repo into `~/.hermes/plugins/posthog/`, set two environment variables, and immediately see their agent's LLM calls and tool spans in their PostHog instance with no code changes.

## Problem Statement

### The Problem

Hermes Agent currently has one in-tree observability plugin (Langfuse) and two community OTel plugins. There is **no PostHog integration**, despite PostHog having a first-class LLM Analytics product and an officially-blessed OpenClaw counterpart. Hermes operators who already use PostHog for product analytics, error tracking, or session replay must either:

1. Maintain a custom shell hook that POSTs to PostHog on every event (fragile, no schema conformance)
2. Run two observability stacks (PostHog for product, Langfuse/OTel for LLM) in parallel
3. Forgo LLM observability entirely

### Impact

- Hermes operators who pay for PostHog see their agent traffic as a black box
- Cost attribution per agent / per session / per model is impossible without manual tooling
- Tool-level latency and error rates aren't visible without writing observability code
- Migration paths from OpenClaw → Hermes are blocked for any team that depended on PostHog dashboards

### Current State

- `posthog-openclaw` (TypeScript, official): full PostHog integration for OpenClaw, but doesn't run on Hermes
- `plugins/observability/langfuse/` (Python, in-tree): Langfuse-only
- `briancaffey/hermes-otel` and `GuanceCloud/hermes-otel-plugin` (Python, community): OTel-only, can route to many backends but PostHog is not a documented OTel-supported backend

## Goals and Success Criteria

### Primary Goal

Ship a drop-in Python plugin that emits PostHog `$ai_generation`, `$ai_span`, and `$ai_trace` events from any Hermes Agent install with zero configuration beyond a `POSTHOG_API_KEY`.

### Success Metrics

| Metric | Target | Measurement Method |
|--------|--------|--------------------|
| Time-to-first-event after install | < 5 minutes | Manual install on Jarvis droplet, time from `hermes plugins enable posthog` to first event in PostHog UI |
| Event schema conformance | 100% of `$ai_*` properties match canonical schema | Side-by-side diff against `posthog-openclaw/src/events.ts` and PostHog LLM Analytics docs |
| Test coverage | ≥ 80% of `__init__.py` + `events.py` | `pytest --cov` |
| Token-count accuracy | Within 1% of Hermes's internal `usage_pricing` totals | Compare event payload to Hermes session DB after a 10-message run |
| Event capture overhead | < 5ms p95 per hook invocation | Microbenchmark with `pytest-benchmark` against a no-op Posthog mock |
| Hook-failure isolation | 0 plugin exceptions reach the Hermes agent loop | Forced PostHog SDK failure during integration test must not abort the agent |

### Non-Goals (Explicit Exclusions)

- ❌ Pip package on PyPI (drop-in plugin folder is the Hermes-canonical install path; pip support deferred to v2 if demand exists)
- ❌ Tool registration (`provides_tools` in manifest) — the plugin is hooks-only, mirroring posthog-openclaw
- ❌ Slash commands or CLI subcommands — out of scope
- ❌ Custom dashboards or saved insights in PostHog — users configure their own
- ❌ Bidirectional sync (reading from PostHog) — write-only
- ❌ Migration tooling for existing OpenClaw event histories — net-new events only
- ❌ Support for OpenClaw — this is a new project, not a dual-target plugin
- ❌ Forking `posthog-openclaw` (different language, different host runtime; new repo is cleaner)

## User Analysis

### Primary Users

**Persona 1: Hermes Operator (Self-Hoster)**
- **Who**: Engineers running Hermes on personal infrastructure (DigitalOcean droplets, home servers, Macs) for personal AI assistants, marketing bots, or research agents
- **Needs**: Cost visibility, latency monitoring, debugging when the agent misbehaves, attribution of spend across multiple agents
- **Context**: Already pay for PostHog Cloud or self-host PostHog; want one observability stack across their product and AI tooling

**Persona 2: PostHog User Adopting Hermes**
- **Who**: Engineers who already use PostHog for product analytics and are evaluating Hermes for AI agent workloads
- **Needs**: A single observability story; reluctance to learn Langfuse/OTel just for AI traces
- **Context**: Decision-makers who would adopt Hermes faster if PostHog support exists

**Persona 3: OpenClaw → Hermes Migrator**
- **Who**: Teams running `posthog-openclaw` who are moving to Hermes (e.g., the Jarvis migration this project is part of)
- **Needs**: Zero-disruption swap so their existing PostHog dashboards keep working
- **Context**: Already have PostHog projects, dashboards, alerts — need event schema parity

### User Stories

1. As a **Hermes operator**, I want to install a PostHog plugin in under five minutes so that I can monitor my agent without building custom telemetry.
2. As an **operator**, I want my LLM cost per agent / per model / per session to appear in PostHog dashboards so that I can spot runaway spend.
3. As an **operator**, I want failed tool calls and LLM errors surfaced as `$ai_is_error=true` events so that I can alert on them in PostHog.
4. As a **PostHog user**, I want the events to use the same `$ai_*` schema as `posthog-openclaw` so that any dashboard or insight I built for OpenClaw works against Hermes data.
5. As an **operator handling sensitive data**, I want a privacy-mode flag that strips message content from events so that I can capture metadata without leaking PII.
6. As a **migrator**, I want trace grouping behavior identical to `posthog-openclaw` (`message` vs `session`) so that my existing trace-level dashboards keep working.
7. As a **plugin author**, I want the plugin to fail open if the PostHog SDK is missing or misconfigured so that a bad config doesn't take down my agent.

## Functional Requirements

### Must Have (P0)

| ID | Requirement | Rationale | Acceptance Criteria |
|----|-------------|-----------|---------------------|
| FR-001 | Emit `$ai_generation` event for every LLM API call | Core observability primitive | One event per `post_api_request` hook fire (or per `post_llm_call` fallback). Properties match canonical schema. |
| FR-002 | Emit `$ai_span` event for every tool call | Tool latency & failure tracking | One event per `post_tool_call` hook fire. Includes parent span ID, duration, error state. |
| FR-003 | Emit `$ai_trace` event at end of message turn or session | Trace-level rollup for dashboards | Configurable via `POSTHOG_TRACE_GROUPING`. Default `message` (end-of-turn). `session` mode emits at `on_session_end` only. |
| FR-004 | Capture token usage (input, output, cache_read, cache_write) | Cost attribution | `$ai_input_tokens`, `$ai_output_tokens`, `$ai_cache_read_input_tokens`, `$ai_cache_creation_input_tokens` populated from Hermes `usage` dict (note: `$ai_` prefix required — openclaw emits these without the prefix, which is a bug we fix) |
| FR-005 | Estimate cost in USD via Hermes `agent.usage_pricing` | Per-event cost without manual pricing tables | `$ai_total_cost_usd` populated when pricing entry available; null otherwise |
| FR-006 | Capture LLM latency from `api_duration` | Performance dashboards | `$ai_latency` populated in seconds (float) |
| FR-007 | Capture finish reason and error state | Error & truncation analysis | `$ai_is_error=true` when finish_reason == "error"; `$ai_error` populated with error message when available. Note: `$ai_stop_reason` is NOT emitted — it is not in the canonical PostHog schema (see ADR-002). |
| FR-008 | Tag events with `$ai_lib=posthog-hermes` and `$ai_framework=hermes` | Multi-source dashboards | Every event includes `$ai_lib`, `$ai_lib_version`, `$ai_framework` |
| FR-009 | Support `POSTHOG_API_KEY` env var (required) and `POSTHOG_HOST` (optional, default US cloud) | Cloud + self-hosted PostHog | Plugin reads from env on first hook fire; missing key disables plugin gracefully |
| FR-010 | Drop-in folder install | Standard Hermes install path | Cloning repo into `~/.hermes/plugins/posthog/` + `hermes plugins enable posthog` is the only install step |
| FR-011 | Privacy mode strips message content | PII / sensitive workloads | `POSTHOG_PRIVACY_MODE=true` nulls `$ai_input` and `$ai_output_choices` on `$ai_generation` events; nulls `$ai_input_state` and `$ai_output_state` on `$ai_span` events; all metadata (tokens, latency, model) still captured |
| FR-012 | Fail-open on PostHog SDK missing or misconfigured | Plugin must never crash agent | `try: from posthog import Posthog except: Posthog = None`. All hooks no-op cleanly when client is None. |
| FR-013 | Dual-hook registration for cross-version compat | Hermes 0.10 ↔ 0.11 differences | Register both `pre/post_api_request` and `pre/post_llm_call`. Skip turn-scoped `pre_llm_call` calls (no `messages` list). |
| FR-014 | Channel attribution from `platform` kwarg | Multi-channel agent dashboards | `$ai_channel` populated from Hermes `platform` arg (slack, telegram, web, etc.) |
| FR-015 | Trace ID stable across multiple LLM calls in same turn | Causal trace grouping | A single user message that triggers N LLM calls + M tool calls produces one `$ai_trace_id` shared across all events |

### Should Have (P1)

| ID | Requirement | Rationale | Acceptance Criteria |
|----|-------------|-----------|---------------------|
| FR-101 | Session windowing with `POSTHOG_SESSION_WINDOW_MINUTES` | Prevent perpetual session traces | After N minutes of inactivity, rotate trace and session ID. Default 60 min. |
| FR-102 | Per-event distinct_id from `session_id` (fallback `task_id`) | PostHog session grouping | `client.capture(distinct_id=...)` uses session_id ?? task_id ?? "unknown" |
| FR-103 | Stale-run cleanup (5-minute timeout) | Bounded memory growth | Module-level state map evicts entries older than 5 minutes on every hook fire |
| FR-104 | Token totals accumulated per trace | `$ai_trace` rollup accuracy | `$ai_total_input_tokens` and `$ai_total_output_tokens` sum across all generations in the trace |
| FR-105 | Verbose debug logging via `HERMES_POSTHOG_DEBUG=true` | Operator troubleshooting | When set, log every event capture decision and any swallowed exceptions |
| FR-106 | Truncate long input/output to 12,000 chars (configurable) | PostHog payload limits | `POSTHOG_MAX_FIELD_CHARS` env var; default 12000 |

### Nice to Have (P2)

| ID | Requirement | Rationale | Acceptance Criteria |
|----|-------------|-----------|---------------------|
| FR-201 | Sample rate control via `POSTHOG_SAMPLE_RATE` | Cost control on high-traffic agents | Float 0.0–1.0; randomly drop events below threshold |
| FR-202 | Custom static properties via `POSTHOG_DEFAULT_PROPERTIES` (JSON) | Multi-tenant labeling | Merge into every event's properties dict |
| FR-203 | `on_session_start` hook integration | Session lifecycle events | Optionally emit a `$ai_session_started` custom event |
| FR-204 | OpenAI-style finish_reason normalization | Schema parity with non-Hermes ecosystems | Map Hermes `finish_reason` values to PostHog conventions (stop, length, tool_calls, error) |

## Non-Functional Requirements

### Performance
- Per-hook overhead: < 5ms p95 (capture is buffered by PostHog SDK; no blocking I/O on hot path)
- Memory: bounded by stale-run TTL; no unbounded state growth
- PostHog SDK uses 20-event batching with 10-second flush by default — no synchronous network calls per event

### Security
- Never log API keys, never include them in event properties
- `POSTHOG_PRIVACY_MODE` must strip both `$ai_input` and `$ai_output_choices` (not just one)
- No outbound traffic when `POSTHOG_API_KEY` is unset (plugin disables itself)
- `requires_env: [POSTHOG_API_KEY]` in `plugin.yaml` so Hermes itself gates loading

### Reliability
- Plugin must never raise an exception that propagates to the Hermes agent loop
- All hook handlers wrap their bodies in `try/except` and log on failure
- Stale state cleanup runs on every hook fire — no background threads, no daemons

### Maintainability
- Multi-file architecture (`__init__.py`, `events.py`, `state.py`, `helpers.py`) modeled on `briancaffey/hermes-otel`
- All event-building logic in `events.py` — pure functions, easy to unit-test
- Hook handlers in `__init__.py` are thin wrappers around `events.py` + state mutation
- Type hints throughout; runs cleanly under `pyright --strict` in basic mode

### Compatibility
- **Hermes**: Best-effort; target ≥ 0.11.0. Dual-hook registration provides graceful degradation on older versions.
- **Python**: 3.11+ (matches Hermes baseline)
- **PostHog SDK**: `posthog>=7.0,<8` — 7.x introduced a breaking change in `capture()` signature (see ADR-006); do not target 4.x or 6.x
- **PostHog backend**: Cloud (US/EU) and self-hosted via `POSTHOG_HOST`

## Technical Constraints

- Plugin must conform to Hermes plugin manifest schema (`name`, `version`, `description`, `provides_hooks`, `requires_env`)
- Plugin must implement `register(ctx)` entry point in `__init__.py`
- Hook handlers use `**kwargs` signature (Hermes plugin contract)
- No shared state between plugin instances (Hermes loads plugin once per process)
- Must not modify Hermes config or other plugins' state

## Dependencies

### Runtime Dependencies
- `posthog` (Python SDK, optional — fail-open if missing)

### Build/Test Dependencies
- `pytest`, `pytest-cov`, `pytest-benchmark`
- `pyright` (type checking)
- `ruff` (lint + format)

### Hermes Internal Dependencies (introspected, not imported via package)
- `agent.usage_pricing.normalize_usage` — for converting raw provider usage to canonical
- `agent.usage_pricing.estimate_usage_cost` — for USD cost estimation
- `agent.usage_pricing.get_pricing_entry` — for per-token-type cost breakdown

These are imported lazily inside try/except so the plugin still works on Hermes versions that move them.

## Risks and Mitigations

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| Hermes hook signature changes break the plugin | Medium | High | Use `**kwargs` everywhere, dual-register hooks, integration test against a known-good Hermes commit |
| PostHog SDK version incompatibility | Low | Medium | Pin minimum SDK version in README; fail-open on import error |
| Token counts from `usage` dict don't match `agent.usage_pricing` | Medium | Medium | Prefer `usage` dict (Hermes-normalized); only fall back to raw `response.usage` if dict is empty |
| `$ai_*` schema drifts from canonical PostHog spec | Low | High | Single source-of-truth tests against fixture events from `posthog-openclaw`; CI diff on schema changes |
| Plugin holds state across Hermes session resets | Medium | Low | Listen for `on_session_reset` (when available); always TTL-evict stale entries |
| User leaks PII despite privacy mode | Low | High | Default privacy mode OFF (matches posthog-openclaw); README prominently documents tradeoff; `POSTHOG_PRIVACY_MODE` strips ALL content fields atomically |
| Repo abandoned, breaks on future Hermes versions | Medium | Medium | MIT license, clear ownership in README, openness to PRs and forking |
| PostHog rejects the project name / branding | Low | Low | "Community fork, not officially supported" disclaimer in README; rename if requested |

## Open Questions

- [ ] Does Hermes ever fire `pre_api_request` without a corresponding `post_api_request` (e.g. on streaming abort)? If so, stale-run cleanup must handle it.
- [ ] Should `assistant_message.tool_calls` be captured as a separate `$ai_span` per tool, or inferred from `post_tool_call` fires? (Current plan: rely on `post_tool_call` only — it's the source of truth.)
- [ ] What happens when Hermes runs in batch mode (`batch_runner.py`)? Are session_id semantics the same? — Defer to integration testing.
- [ ] Is there a Hermes `on_plugin_unload` lifecycle hook for clean shutdown? Need to check.

## Appendix

### Glossary

| Term | Definition |
|------|------------|
| Hook | A Hermes plugin extension point that fires at lifecycle events (e.g., `pre_api_request`) |
| Trace | A logical grouping of related LLM calls, tool calls, and final response |
| Generation | A single LLM API call (input → output) |
| Span | A single tool call or sub-operation within a trace |
| Distinct ID | PostHog's user identifier; we use Hermes session_id as the proxy |
| Drop-in plugin | A plugin installed by copying files to `~/.hermes/plugins/<name>/`, no package manager |

### References

- [posthog-openclaw repo](https://github.com/PostHog/posthog-openclaw) — TypeScript reference implementation
- [posthog-openclaw events.ts](https://github.com/PostHog/posthog-openclaw/blob/main/src/events.ts) — canonical event property reference
- [Hermes Agent repo](https://github.com/NousResearch/hermes-agent) — target runtime
- [Langfuse plugin (in-tree)](https://github.com/NousResearch/hermes-agent/tree/main/plugins/observability/langfuse) — verified hook API reference
- [briancaffey/hermes-otel](https://github.com/briancaffey/hermes-otel) — multi-file architectural reference + test patterns
- [GuanceCloud/hermes-otel-plugin](https://github.com/GuanceCloud/hermes-otel-plugin) — alternative plugin manifest reference
- [PostHog Python SDK docs](https://posthog.com/docs/libraries/python) — `Posthog` constructor and `capture()` API
- [PostHog LLM Analytics docs](https://posthog.com/docs/llm-analytics) — `$ai_*` event schema
