# Changelog

## [Approved] - 2026-04-29
- Spec approved after zen (GPT-5.4) + independent review
- 9 bugs found and fixed in spec before approval
- Ready for implementation

## [Unreleased]

### Added
- Initial project scaffold at `docs/spec/active/2026-04-29-posthog-hermes/`
- README.md with project metadata
- Elicitation phase started — gap-focused questioning given the detailed seed

### Decided (Phase 1 elicitation)
- **Distribution**: Drop-in plugin folder under `~/.hermes/plugins/posthog/` (the Hermes-recommended way per official docs). Repo cloneable from `shivanathd/posthog-hermes`. No pip package required for v1.
- **PostHog relationship**: Community fork — independent project that mirrors the `posthog-openclaw` event schema. No coordination with PostHog upfront.
- **`$ai_trace` emission**: Configurable via `POSTHOG_TRACE_GROUPING=message|session` (default `message`). Mirrors posthog-openclaw's behavior.
- **Hermes version**: Best-effort — target Hermes ≥0.11.0 with dual-hook registration (`pre/post_api_request` preferred, `pre/post_llm_call` as fallback). No formal version guarantees.

### Context received
- Hermes plugin canonical shape: `plugin.yaml` + `__init__.py` (+ optional `schemas.py`, `tools.py`).
- Plugin manifest fields: `name`, `version`, `description`, `provides_tools`, `provides_hooks`, `requires_env`.
- Plugin lifecycle: drop in `~/.hermes/plugins/<name>/`, opt-in via `hermes plugins enable <name>` or adding to `plugins.enabled` in `~/.hermes/config.yaml`.
- Hook registration: `ctx.register_hook(hook_name, handler)` inside `register(ctx)`.
- Tool handlers (not used by this plugin) follow `def handler(args: dict, **kwargs) -> str` with JSON-string return.
- Reference repos surveyed: posthog-openclaw, NousResearch/hermes-agent (Langfuse plugin in-tree), briancaffey/hermes-otel, GuanceCloud/hermes-otel-plugin, 42-evey/hermes-plugins.

### Research findings (Phase 2)
- **posthog-openclaw schema bug discovered**: `cache_read_input_tokens` / `cache_creation_input_tokens` are missing the `$ai_` prefix. PostHog UI cache cost views break silently. Fix documented in ADR-002.
- **PostHog Python SDK 7.x breaking change**: `capture(event, distinct_id=..., properties=...)` — different from older 3.x positional signature. Captured in ADR-006.
- **Canonical schema additions** vs openclaw: `$ai_reasoning_tokens`, `$ai_input_cost_usd`, `$ai_output_cost_usd`, `$ai_http_status`, `$ai_base_url` are all available and useful. We add them.
- **Hermes hook API verified** from in-tree Langfuse plugin source: per-LLM-call hooks (`pre/post_api_request`) preferred over per-turn (`pre/post_llm_call`); skip pattern handles dual-fire.

## [1.0.0] - 2026-04-29

### Added
- REQUIREMENTS.md — 15 P0 / 6 P1 / 4 P2 functional requirements; full non-functional and risk analysis
- ARCHITECTURE.md — 4-file Python plugin design; data flow; PostHog SDK 7.x integration; configuration reference
- IMPLEMENTATION_PLAN.md — 4 phases, ~20 tasks, 16-24 hour total estimate, dependency graph
- DECISIONS.md — 8 ADRs covering architecture, schema fix, state model, lazy imports, hook compatibility, SDK signature, trace grouping, cleanup strategy
- RESEARCH_NOTES.md — verified Hermes hook API, PostHog `$ai_*` schema diff, dependency analysis, source links

### Status
- README.md status moved from `draft` → `in-review`
- Awaiting user review and `/claude-spec:approve posthog-hermes`

## [1.0.1] - 2026-04-29 (Post-review fixes)

### Fixed — bugs found by zen (GPT-5.4) + independent analysis, cross-validated

| # | Severity | Fix |
|---|----------|-----|
| BUG-1 | HIGH | FR-004 acceptance criteria: corrected `cache_read_input_tokens` → `$ai_cache_read_input_tokens` and `cache_creation_input_tokens` → `$ai_cache_creation_input_tokens` in REQUIREMENTS.md |
| BUG-2 | HIGH | `on_session_end` hook added to: `register(ctx)` example in DECISIONS.md (ADR-005), `provides_hooks` in ARCHITECTURE.md plugin.yaml, system overview hook list, Task 2.5 acceptance criteria in IMPLEMENTATION_PLAN.md. Session mode was silently broken without this. |
| BUG-3 | MEDIUM | FR-007 acceptance criteria: removed `$ai_stop_reason` (contradicted ADR-002 which drops it as non-canonical); updated to `$ai_is_error` + `$ai_error` only |
| BUG-4 | MEDIUM | REQUIREMENTS.md SDK version: corrected `≥ 4.x` → `>=7.0,<8` to match ADR-006 and RESEARCH_NOTES |
| BUG-5 | MEDIUM | State dict location: clarified in ARCHITECTURE.md that `_RUN_STATE`, `_SESSION_STATE`, `_TOOL_START_TIMES`, `_STATE_LOCK` live in `state.py` (not `__init__.py`); only `_POSTHOG_CLIENT` is in `__init__.py` |
| BUG-6 | LOW | FR-011 privacy mode: added `$ai_input_state` / `$ai_output_state` (tool spans) to acceptance criteria alongside `$ai_input` / `$ai_output_choices` (generation events) |

### Added
- Task 3.0 in IMPLEMENTATION_PLAN.md: `requirements.txt` + `pyproject.toml` for runtime dep declaration (`posthog>=7.0,<8`)
- Explicit BLOCKING prerequisite note in Phase 1: Jarvis → Hermes migration must complete before Phase 1 Task 1.4 integration smoke test
- Estimate split: engineering-complete (16-24h, Phases 1-3) vs release-complete (24-36h, all 4 phases)
