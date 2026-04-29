---
project_id: SPEC-2026-04-29-001
project_name: "PostHog LLM Analytics Plugin for Hermes Agent"
slug: posthog-hermes
status: approved
created: 2026-04-29T00:00:00Z
approved: 2026-04-29T11:30:00Z
approved_by: "shivasymbl <sdevinarayanan@asymbl.com>"
started: null
completed: null
expires: 2026-07-28T00:00:00Z
superseded_by: null
tags: [observability, posthog, hermes, plugin, llm-analytics, python]
stakeholders: [shivanathd]
---

# PostHog LLM Analytics Plugin for Hermes Agent

## Status: In Review (awaiting `/claude-spec:approve`)

A Python plugin for the Hermes Agent runtime that emits PostHog `$ai_generation`, `$ai_span`, and `$ai_trace` events — porting the event schema from PostHog's official OpenClaw plugin to Hermes's hook-based architecture.

## Quick Links

- **Source repo (this project):** `/Users/sdevinarayanan/Asymbl/posthog-hermes/` (will be published as `shivanathd/posthog-hermes`)
- **Reference (TypeScript/OpenClaw):** https://github.com/PostHog/posthog-openclaw
- **Architectural inspiration:** https://github.com/briancaffey/hermes-otel, https://github.com/GuanceCloud/hermes-otel-plugin
- **Hermes Langfuse plugin (verified hook API):** `NousResearch/hermes-agent/plugins/observability/langfuse/`

## Documents

- [REQUIREMENTS.md](./REQUIREMENTS.md) — 15 P0 / 6 P1 / 4 P2 functional requirements; full NFRs and risks
- [ARCHITECTURE.md](./ARCHITECTURE.md) — 4-file Python plugin design; data flow; PostHog SDK 7.x integration
- [IMPLEMENTATION_PLAN.md](./IMPLEMENTATION_PLAN.md) — 4 phases, ~20 tasks, 16-24 hours total estimate
- [DECISIONS.md](./DECISIONS.md) — 8 ADRs (architecture, schema fix, hooks, SDK, etc.)
- [RESEARCH_NOTES.md](./RESEARCH_NOTES.md) — Hermes hook API verification, PostHog schema diff, dependency analysis
- [CHANGELOG.md](./CHANGELOG.md)

## Key Findings (worth highlighting)

- **posthog-openclaw has a schema bug**: `cache_read_input_tokens` and `cache_creation_input_tokens` are missing the `$ai_` prefix → PostHog UI cache cost views silently break. **Our port fixes this** (see ADR-002).
- **PostHog Python SDK 7.x changed `capture()` signature** — `event` is now first positional, `distinct_id` is a kwarg. Older docs/tutorials are wrong (see ADR-006).
- **Token + cost data IS available** in Hermes hooks via the `usage` dict and `agent.usage_pricing` module — earlier analysis suggested this was a gap, but the in-tree Langfuse plugin shows the correct extraction pattern.
- **Drop-in plugin folder is the canonical install method** for Hermes plugins, not pip — confirmed via official docs and 36+ community plugins surveyed.
