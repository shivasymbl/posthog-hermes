# posthog-hermes

> PostHog LLM Analytics plugin for [Hermes Agent](https://github.com/NousResearch/hermes-agent) — a Python port of [`posthog-openclaw`](https://github.com/PostHog/posthog-openclaw).

Emits `$ai_generation`, `$ai_span`, and `$ai_trace` events to PostHog for every LLM call, tool invocation, and conversational turn in your Hermes Agent instance.

[![CI](https://github.com/shivasymbl/posthog-hermes/actions/workflows/ci.yml/badge.svg)](https://github.com/shivasymbl/posthog-hermes/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

## Why use this

- **Cost visibility** — `$ai_total_cost_usd` per model call, accumulated per session
- **Latency monitoring** — `$ai_latency` on every LLM call and tool span
- **Error tracking** — `$ai_is_error=true` on failed calls; alert in PostHog
- **Tool observability** — `$ai_span` events with parent linkage for every tool call
- **Privacy mode** — strip all LLM content from events while keeping metadata
- **Migrating from OpenClaw?** — same event schema as `posthog-openclaw`, compatible dashboards

> **Schema fix included:** `posthog-openclaw` emits `cache_read_input_tokens` without the `$ai_` prefix, breaking PostHog's cache cost views. This plugin uses the canonical `$ai_cache_read_input_tokens` / `$ai_cache_creation_input_tokens` names.

---

## Install

```bash
# 1. Clone into your Hermes plugins directory
git clone https://github.com/shivasymbl/posthog-hermes ~/.hermes/plugins/posthog

# 2. Install the PostHog SDK
pip install "posthog>=7.0,<8"

# 3. Add your API key to ~/.hermes/.env
echo "POSTHOG_API_KEY=phc_xxxxxxxxxx" >> ~/.hermes/.env

# 4. Enable the plugin
hermes plugins enable posthog

# 5. Restart your Hermes gateway
hermes gateway restart
```

---

## Configuration

All configuration is via environment variables in `~/.hermes/.env`.

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `POSTHOG_API_KEY` | **Yes** | — | PostHog project API key (`phc_...`) |
| `POSTHOG_HOST` | No | `https://us.i.posthog.com` | PostHog instance URL (for self-hosted) |
| `POSTHOG_PRIVACY_MODE` | No | `false` | Strip LLM input/output content from events |
| `POSTHOG_TRACE_GROUPING` | No | `message` | `message` = one trace per turn; `session` = one trace per session |
| `POSTHOG_SESSION_WINDOW_MINUTES` | No | `60` | Inactivity timeout before trace rotation |
| `POSTHOG_MAX_FIELD_CHARS` | No | `12000` | Max characters per string field |
| `HERMES_POSTHOG_DEBUG` | No | `false` | Verbose logging for troubleshooting |

---

## Events emitted

### `$ai_generation` — per LLM API call

| Property | Description |
|----------|-------------|
| `$ai_trace_id` | Trace ID shared across all events in a turn |
| `$ai_span_id` | Unique ID for this generation |
| `$ai_model` | Model name (e.g. `claude-haiku-4-5`) |
| `$ai_provider` | Provider (e.g. `anthropic`, `openrouter`) |
| `$ai_input` | Input messages (null in privacy mode) |
| `$ai_output_choices` | Assistant response (null in privacy mode) |
| `$ai_input_tokens` | Input token count |
| `$ai_output_tokens` | Output token count |
| `$ai_cache_read_input_tokens` | Cache read tokens (`$ai_` prefix — see note above) |
| `$ai_cache_creation_input_tokens` | Cache write tokens |
| `$ai_reasoning_tokens` | Reasoning tokens (for thinking models) |
| `$ai_latency` | API call duration in seconds |
| `$ai_total_cost_usd` | Estimated cost in USD |
| `$ai_is_error` | `true` if the call failed |
| `$ai_channel` | Messaging platform (slack, telegram, web, etc.) |
| `$ai_lib` | `posthog-hermes` |
| `$ai_framework` | `hermes` |

### `$ai_span` — per tool call

| Property | Description |
|----------|-------------|
| `$ai_trace_id` | Parent trace ID |
| `$ai_parent_id` | Parent generation span ID |
| `$ai_span_name` | Tool name |
| `$ai_input_state` | Tool args (null in privacy mode) |
| `$ai_output_state` | Tool result (null in privacy mode) |
| `$ai_latency` | Tool execution duration in seconds |

### `$ai_trace` — per turn (message mode) or per session (session mode)

| Property | Description |
|----------|-------------|
| `$ai_trace_id` | Trace ID |
| `$ai_total_input_tokens` | Summed input tokens across all generations |
| `$ai_total_output_tokens` | Summed output tokens across all generations |
| `$ai_latency` | Total turn/session duration |
| `$ai_channel` | Messaging platform |

---

## Differences from posthog-openclaw

| Aspect | posthog-openclaw | posthog-hermes |
|--------|-----------------|----------------|
| Runtime | OpenClaw (Node.js gateway) | Hermes Agent (Python) |
| Language | TypeScript | Python 3.11+ |
| `cache_*` token properties | `cache_read_input_tokens` ⚠️ | `$ai_cache_read_input_tokens` ✅ |
| `$ai_reasoning_tokens` | Not emitted | Emitted when available |
| `$ai_input_cost_usd` / `$ai_output_cost_usd` | Not emitted | Emitted via Hermes pricing module |
| `$ai_stop_reason` | Emitted | Dropped (not in canonical schema) |
| Session mode | Configurable | Configurable (same knob) |

---

## Hermes version compatibility

| Hermes version | LLM generation hooks | Session end hook |
|----------------|---------------------|-----------------|
| ≥ 0.11 | `pre/post_api_request` (preferred) | `on_session_end` |
| 0.10 | `pre/post_llm_call` (fallback) | Not available |

The plugin registers both hook variants automatically. Session mode (`POSTHOG_TRACE_GROUPING=session`) requires Hermes ≥0.11.

---

## Troubleshooting

**No events in PostHog**
- Check `POSTHOG_API_KEY` is set: `hermes plugins list` should show `posthog` as enabled
- Enable debug logging: `HERMES_POSTHOG_DEBUG=true` in `~/.hermes/.env`, then restart

**Cache cost views show zero**
- You may have data from `posthog-openclaw` with the unprefixed `cache_*` properties. The fix is in this plugin — new events will use `$ai_cache_*` names. Existing OpenClaw data needs a PostHog property rename transformation.

**`posthog SDK version X detected; requires >=7.0`**
- Run: `pip install -U posthog`

---

## Development

```bash
# Install dev deps
pip install "posthog>=7.0,<8" pytest pytest-cov pyright ruff

# Run tests
cd ~/.hermes/plugins/posthog
pytest --cov --cov-report=term-missing

# Lint
ruff check . && ruff format --check .
```

---

## License

MIT — forked from [PostHog/posthog-openclaw](https://github.com/PostHog/posthog-openclaw).
