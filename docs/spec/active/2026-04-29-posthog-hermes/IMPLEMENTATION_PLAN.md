---
document_type: implementation_plan
project_id: SPEC-2026-04-29-001
version: 1.0.0
last_updated: 2026-04-29T00:00:00Z
status: draft
estimated_effort: "Engineering-complete: 16-24 hours (Phases 1-3). Release-complete: 24-36 hours (all 4 phases including docs, dashboards, CI, announcements, external validation). Estimate assumes Hermes install target is available from Phase 1."
---

# posthog-hermes — Implementation Plan

## Overview

Four phases over 2-3 working days, single contributor (no team coordination overhead). Each phase is independently shippable: Phase 1 produces a plugin that loads and emits one event type; Phase 4 produces a public, documented, tested v0.1.0 release.

The implementation uses the existing scaffold left over from the earlier exploration (which we deleted from `shivasymbl` — this rebuilds under `shivanathd` per project plan).

## Team & Resources

| Role | Responsibility | Allocation |
|------|----------------|------------|
| Plugin author (shivanathd) | Implementation, testing, docs | 100% |
| Reviewer (TBD) | Code review before v0.1.0 tag | ~2 hours |

## Phase Summary

| Phase | Duration | Key Deliverables |
|-------|----------|------------------|
| Phase 1: Foundation | 3-4 hours | Repo scaffolded under shivanathd; plugin.yaml, register(ctx) loads in Hermes; `$ai_generation` emitted with stub data |
| Phase 2: Core Schema | 5-7 hours | All 3 event types emit with correct canonical schema; token + cost extraction working; privacy mode |
| Phase 3: Robustness | 4-6 hours | Test suite ≥80% coverage; stale cleanup; trace grouping modes; integration test on Jarvis |
| Phase 4: Release Polish | 4-7 hours | README, examples, GitHub Actions CI, v0.1.0 tag, announcement materials |

---

## Phase 1: Foundation

**Duration**: 3-4 hours
**Goal**: Repo exists under `shivanathd/posthog-hermes`, plugin loads in Hermes without errors, single hook fires and emits one event.
**Prerequisites**:
- Spec approved via `/claude-spec:approve`
- User explicitly requests `use shivanathd` to switch GitHub identity
- PAT for shivanathd is in macOS keychain (already done, slot `github-pat-shivanathd`)
- **BLOCKING**: Hermes must be installed on a live droplet for Phase 1 smoke test. The Jarvis → Hermes migration (from OpenClaw) has not yet happened. This migration is a prerequisite for Phase 1 Task 1.4 and Phase 3 Task 3.4. Do not begin Phase 1 until a Hermes install target is confirmed.

### Tasks

#### Task 1.1: Create public repo under shivanathd

- **Description**: Create `shivanathd/posthog-hermes` as a public GitHub repo, MIT licensed
- **Estimated Effort**: 0.5 hours
- **Dependencies**: None
- **Acceptance Criteria**:
  - [ ] Repo exists at https://github.com/shivanathd/posthog-hermes
  - [ ] Public visibility
  - [ ] MIT license file present
  - [ ] `.gitignore` covers Python artifacts (`__pycache__`, `*.pyc`, `.pytest_cache`, `.venv`, etc.)
  - [ ] README.md placeholder exists (filled in Phase 4)
  - [ ] Default branch is `main`
- **Notes**: Use the macOS keychain PAT via `gh` CLI: `security find-generic-password -s "github-pat-shivanathd" -a "shivanathd" -w` to retrieve

#### Task 1.2: Scaffold plugin file structure

- **Description**: Create the 4-file plugin layout described in ARCHITECTURE.md
- **Estimated Effort**: 0.5 hours
- **Dependencies**: 1.1
- **Acceptance Criteria**:
  - [ ] `plugin.yaml` with manifest fields (name, version 0.1.0, description, author, requires_env, provides_hooks)
  - [ ] `__init__.py` with `register(ctx)` stub that registers all 6 hooks
  - [ ] `state.py` with `RunState` and `SessionState` dataclasses
  - [ ] `events.py` with `build_ai_generation`, `build_ai_span`, `build_ai_trace` function stubs returning placeholder dicts
  - [ ] `helpers.py` with `generate_trace_id`, `generate_span_id`, env helpers

#### Task 1.3: Implement client lazy-init and basic logging

- **Description**: PostHog SDK lazy import; cached client; debug logging
- **Estimated Effort**: 1 hour
- **Dependencies**: 1.2
- **Acceptance Criteria**:
  - [ ] `_get_client()` returns a `Posthog` instance or `None`
  - [ ] `posthog` import wrapped in try/except, falls back to `Posthog = None`
  - [ ] Missing `POSTHOG_API_KEY` logs a single warning and returns `None` cached as `_INIT_FAILED`
  - [ ] SDK version check rejects `posthog<7.0` with a clear error message
  - [ ] `HERMES_POSTHOG_DEBUG=true` enables verbose logging

#### Task 1.4: First end-to-end event capture (smoke test)

- **Description**: Wire `on_post_api_request` to emit a stub `$ai_generation` event
- **Estimated Effort**: 1.5 hours
- **Dependencies**: 1.3
- **Acceptance Criteria**:
  - [ ] Plugin clones into `~/.hermes/plugins/posthog/` on a Hermes install
  - [ ] `hermes plugins enable posthog` succeeds
  - [ ] `hermes gateway start` loads the plugin (no errors in logs)
  - [ ] One real LLM call from Hermes produces one `$ai_generation` event in PostHog UI
  - [ ] Event has at least: `$ai_model`, `$ai_provider`, `$ai_lib=posthog-hermes`, `$ai_session_id`

### Phase 1 Deliverables
- [ ] Public GitHub repo at `shivanathd/posthog-hermes`
- [ ] 4-file plugin scaffold committed
- [ ] Smoke-test event visible in PostHog UI
- [ ] Tagged `v0.0.1-alpha` (private milestone, no announcement)

### Phase 1 Exit Criteria
- [ ] Plugin loads on Hermes ≥0.11.0 without errors
- [ ] At least one real event captured end-to-end
- [ ] Repo accessible to reviewers

---

## Phase 2: Core Schema Implementation

**Duration**: 5-7 hours
**Goal**: All three event types emit with the full canonical PostHog `$ai_*` schema, including the prefix bug fix from ADR-002.

### Tasks

#### Task 2.1: Implement `state.py` fully

- **Description**: Complete `RunState`, `SessionState`, module-level dicts, `_STATE_LOCK`, helper functions (`_task_key`, `_run_key`, `_get_or_create_session`)
- **Estimated Effort**: 1 hour
- **Dependencies**: Phase 1 complete
- **Acceptance Criteria**:
  - [ ] All dataclass fields match ARCHITECTURE.md
  - [ ] Stale-run cleanup helper (`_cleanup_stale_runs(ttl_seconds=300)`) implemented
  - [ ] `_get_or_create_session` honors `POSTHOG_TRACE_GROUPING` (rotates trace in `message` mode after emission; persists in `session` mode)
  - [ ] Session window timeout logic (`POSTHOG_SESSION_WINDOW_MINUTES`) wired

#### Task 2.2: Implement `helpers.extract_usage_and_cost`

- **Description**: Pull tokens from `usage` dict (preferred) or raw `response.usage`; estimate per-type cost via `agent.usage_pricing`
- **Estimated Effort**: 1 hour
- **Dependencies**: 2.1
- **Acceptance Criteria**:
  - [ ] Returns 6-tuple: `(input_tokens, output_tokens, cache_read, cache_write, reasoning, cost_usd)`
  - [ ] Lazy imports `agent.usage_pricing` inside try/except
  - [ ] Returns all-None tuple if Hermes module unavailable
  - [ ] Per-type cost breakdown via `get_pricing_entry` when pricing entry exists
  - [ ] Falls back to `estimate_usage_cost(...).amount_usd` when entry missing

#### Task 2.3: Implement `events.build_ai_generation` with canonical schema

- **Description**: Full property dict matching ADR-002
- **Estimated Effort**: 1.5 hours
- **Dependencies**: 2.2
- **Acceptance Criteria**:
  - [ ] All P0 properties from REQUIREMENTS.md FR-001, FR-004–FR-008 populated
  - [ ] `$ai_cache_read_input_tokens` and `$ai_cache_creation_input_tokens` use `$ai_` prefix (NOT openclaw bug)
  - [ ] `$ai_reasoning_tokens` populated from usage dict
  - [ ] `$ai_input_cost_usd`, `$ai_output_cost_usd`, `$ai_total_cost_usd` from pricing breakdown
  - [ ] `$ai_base_url`, `$ai_http_status` populated when available
  - [ ] Privacy mode strips `$ai_input` and `$ai_output_choices` to None
  - [ ] Long fields truncated per `POSTHOG_MAX_FIELD_CHARS`

#### Task 2.4: Implement `events.build_ai_span` and `events.build_ai_trace`

- **Description**: Tool-call span builder + trace rollup builder
- **Estimated Effort**: 1 hour
- **Dependencies**: 2.3
- **Acceptance Criteria**:
  - [ ] `build_ai_span` includes `$ai_trace_id`, `$ai_parent_id` (from session's `current_generation_span_id`), `$ai_span_name`, `$ai_input_state`, `$ai_output_state`, `$ai_latency`, `$ai_is_error`, `$ai_error`
  - [ ] `build_ai_trace` includes `$ai_trace_id`, `$ai_session_id`, `$ai_total_input_tokens`, `$ai_total_output_tokens`, `$ai_latency` (turn or session duration), `$ai_channel`
  - [ ] Both honor privacy mode

#### Task 2.5: Wire all hook handlers in `__init__.py`

- **Description**: Connect `on_pre_api_request`, `on_post_api_request`, `on_pre_tool_call`, `on_post_tool_call` to state + events
- **Estimated Effort**: 1.5 hours
- **Dependencies**: 2.4
- **Acceptance Criteria**:
  - [ ] `on_pre_api_request` skips when `messages` is not a list (v0.11 turn-scoped variant)
  - [ ] All handlers wrapped in outer try/except with WARNING-level log
  - [ ] `on_post_api_request` emits `$ai_trace` when no pending tool calls (in `message` mode)
  - [ ] `on_session_end` registered in `register(ctx)` — emits `$ai_trace` in `session` mode; `on_session_end` also present in plugin.yaml `provides_hooks`
  - [ ] Hook signatures match Langfuse plugin reference (kwargs-only with `**_`)

### Phase 2 Deliverables
- [ ] Full canonical event schema emitting from all 3 event types
- [ ] Privacy mode functional
- [ ] Token + cost extraction working
- [ ] Tagged `v0.0.2-alpha`

### Phase 2 Exit Criteria
- [ ] Manual test on Hermes shows correct events for: 1 LLM-only turn, 1 LLM+tool turn, 1 multi-turn session
- [ ] Cache token properties have `$ai_` prefix (verified in PostHog UI)
- [ ] Cost USD populated correctly per pricing tier

---

## Phase 3: Robustness & Test Coverage

**Duration**: 4-6 hours
**Goal**: ≥80% test coverage; reliable under failure modes; both trace grouping modes work end-to-end.

### Tasks

#### Task 3.0: Add `requirements.txt` and `pyproject.toml`

- **Description**: Runtime dep declaration so users can `pip install -r requirements.txt` and CI can install deps
- **Estimated Effort**: 0.25 hours
- **Dependencies**: Phase 2 complete
- **Acceptance Criteria**:
  - [ ] `requirements.txt` in plugin root: `posthog>=7.0,<8`
  - [ ] `pyproject.toml` with `[project.optional-dependencies] runtime = ["posthog>=7.0,<8"]` and dev deps
  - [ ] CI uses `pip install -r requirements.txt` before running tests

#### Task 3.1: Set up test infrastructure

- **Description**: `pytest` config, fixtures, mock Hermes ctx, mock PostHog client
- **Estimated Effort**: 1 hour
- **Dependencies**: Task 3.0 complete
- **Acceptance Criteria**:
  - [ ] `pyproject.toml` with dev deps: `pytest`, `pytest-cov`, `pyright`, `ruff`
  - [ ] `tests/conftest.py` with fixtures:
    - `mock_ctx` — captures hook registrations
    - `mock_posthog` — replaces `_POSTHOG_CLIENT` with `MagicMock`
    - `reset_state` — clears module-level state between tests
  - [ ] `pytest.ini` config with `--cov-fail-under=80`

#### Task 3.2: Unit tests for `events.py`

- **Description**: Pure function tests, table-driven where possible
- **Estimated Effort**: 1 hour
- **Dependencies**: 3.1
- **Acceptance Criteria**:
  - [ ] `build_ai_generation` tested for: full input, privacy mode, missing tokens, missing cost, error finish_reason
  - [ ] `build_ai_span` tested for: with parent_span, no parent_span, error result, privacy mode
  - [ ] `build_ai_trace` tested for: with totals, no totals, no channel
  - [ ] Schema snapshot tests against `tests/fixtures/expected_*.json`

#### Task 3.3: Unit tests for hook handlers

- **Description**: Test full hook lifecycle with mocked SDK
- **Estimated Effort**: 1.5 hours
- **Dependencies**: 3.2
- **Acceptance Criteria**:
  - [ ] `on_pre_api_request` skips when `messages` is None
  - [ ] `on_post_api_request` emits `$ai_generation`
  - [ ] `on_post_api_request` emits `$ai_trace` when no tool calls (message mode)
  - [ ] `on_post_api_request` does NOT emit `$ai_trace` when tool calls pending
  - [ ] Tool span has correct `$ai_parent_id` from active generation
  - [ ] Hooks wrap exceptions, never propagate (test with `mock_posthog.capture.side_effect = Exception`)
  - [ ] Stale cleanup evicts entries older than 5 min

#### Task 3.4: Integration test on real Hermes

- **Description**: Run plugin against an actual Hermes install, verify event flow
- **Estimated Effort**: 1 hour
- **Dependencies**: 3.3
- **Acceptance Criteria**:
  - [ ] Test on Jarvis droplet (post-Hermes-migration scenario)
  - [ ] 10-message conversation with mixed LLM-only and LLM+tool turns
  - [ ] All events visible in PostHog test project
  - [ ] No exceptions in `journalctl --user -u hermes-gateway`
  - [ ] Memory stable (RSS doesn't grow >5MB after 100 events)

#### Task 3.5: Test both trace grouping modes

- **Description**: Verify `message` and `session` modes produce expected event counts
- **Estimated Effort**: 0.5 hours
- **Dependencies**: 3.4
- **Acceptance Criteria**:
  - [ ] `POSTHOG_TRACE_GROUPING=message` + 5 user turns → 5 `$ai_trace` events
  - [ ] `POSTHOG_TRACE_GROUPING=session` + 5 user turns + session end → 1 `$ai_trace` event
  - [ ] Token totals in `session` mode match sum across turns

### Phase 3 Deliverables
- [ ] Test suite at ≥80% coverage
- [ ] Integration test passes on Jarvis
- [ ] Tagged `v0.0.3-beta`

### Phase 3 Exit Criteria
- [ ] `pytest --cov` shows ≥80% on `__init__.py`, `events.py`, `state.py`
- [ ] No exceptions during 1-hour real-world Hermes run
- [ ] Both trace grouping modes verified

---

## Phase 4: Release Polish

**Duration**: 4-7 hours
**Goal**: Public v0.1.0 release with docs, CI, and announcement-ready materials.

### Tasks

#### Task 4.1: Write README.md

- **Description**: Comprehensive README following Hermes plugin conventions
- **Estimated Effort**: 2 hours
- **Dependencies**: Phase 3 complete
- **Acceptance Criteria**:
  - [ ] One-paragraph description
  - [ ] "Why use this" section (cost visibility, dashboards, etc.)
  - [ ] Quick install: `git clone ... ~/.hermes/plugins/posthog && hermes plugins enable posthog`
  - [ ] Configuration table (all env vars from REQUIREMENTS.md)
  - [ ] Event schema reference (full property list per event type)
  - [ ] **Diff vs posthog-openclaw section** — documents the canonical schema fixes from ADR-002
  - [ ] Troubleshooting section
  - [ ] Link to PostHog LLM Analytics docs
  - [ ] Privacy mode prominent disclaimer
  - [ ] License + contribution policy

#### Task 4.2: GitHub Actions CI

- **Description**: Lint + test on every push
- **Estimated Effort**: 1 hour
- **Dependencies**: 4.1
- **Acceptance Criteria**:
  - [ ] `.github/workflows/ci.yml` runs on push and PR
  - [ ] `ruff check`, `ruff format --check`
  - [ ] `pyright`
  - [ ] `pytest --cov --cov-fail-under=80`
  - [ ] Python 3.11 + 3.12 matrix
  - [ ] Status badges in README

#### Task 4.3: Examples directory

- **Description**: Working examples of common configs
- **Estimated Effort**: 1 hour
- **Dependencies**: 4.1
- **Acceptance Criteria**:
  - [ ] `examples/basic.env` — minimum config (just `POSTHOG_API_KEY`)
  - [ ] `examples/privacy-mode.env` — with `POSTHOG_PRIVACY_MODE=true`
  - [ ] `examples/session-mode.env` — with `POSTHOG_TRACE_GROUPING=session`
  - [ ] `examples/self-hosted.env` — with `POSTHOG_HOST` override
  - [ ] `examples/dashboards/` — JSON exports of suggested PostHog dashboards (cost, latency, error rate)

#### Task 4.4: File issue against posthog-openclaw

- **Description**: Report the `cache_*` prefix bug upstream (ADR-002)
- **Estimated Effort**: 0.5 hours
- **Dependencies**: 4.1
- **Acceptance Criteria**:
  - [ ] Issue filed at `PostHog/posthog-openclaw/issues`
  - [ ] Includes reproduction steps
  - [ ] References our README's diff section
  - [ ] Offers PR if wanted

#### Task 4.5: v0.1.0 release

- **Description**: Tag, announce, archive
- **Estimated Effort**: 1.5 hours
- **Dependencies**: 4.1–4.4
- **Acceptance Criteria**:
  - [ ] `git tag v0.1.0` and `git push --tags`
  - [ ] GitHub Release with auto-generated changelog
  - [ ] CHANGELOG.md in repo (Keep a Changelog format)
  - [ ] Announcement on:
    - [ ] Hermes Discord (#plugins channel if exists)
    - [ ] PostHog community Slack (#integrations)
    - [ ] hermes-agent GitHub Discussions
  - [ ] Move spec to `docs/spec/completed/` via `/claude-spec:complete`

### Phase 4 Deliverables
- [ ] README, examples, CI green
- [ ] v0.1.0 tagged and announced
- [ ] Upstream issue filed
- [ ] Spec marked `completed`

### Phase 4 Exit Criteria
- [ ] Repo public at https://github.com/shivanathd/posthog-hermes with CI badge passing
- [ ] At least one external operator confirms successful install via the README
- [ ] PostHog dashboards work with our event schema

---

## Dependency Graph

```
Phase 1 (Foundation)
  Task 1.1 (repo) ──┐
                    ├─> Task 1.2 (scaffold) ──> Task 1.3 (lazy init) ──> Task 1.4 (smoke test)
  PAT in keychain ──┘                                                          │
                                                                               v
Phase 2 (Schema)                                                          Phase 2 entry
  Task 2.1 (state) ──> Task 2.2 (usage extract) ──> Task 2.3 (generation) ──> Task 2.4 (span+trace) ──> Task 2.5 (handlers)
                                                                                                              │
                                                                                                              v
Phase 3 (Robustness)                                                                                     Phase 3 entry
  Task 3.1 (test infra) ──> Task 3.2 (events tests) ──> Task 3.3 (handler tests) ──> Task 3.4 (integration) ──> Task 3.5 (grouping tests)
                                                                                                                          │
                                                                                                                          v
Phase 4 (Release)                                                                                                    Phase 4 entry
  Task 4.1 (README) ──┬─> Task 4.2 (CI)
                      ├─> Task 4.3 (examples)
                      ├─> Task 4.4 (upstream issue)
                      └─> Task 4.5 (v0.1.0 release)  [last; depends on all 4.1-4.4]
```

## Risk Mitigation Tasks

| Risk (from REQUIREMENTS.md) | Mitigation Task | When |
|------|-----------------|------|
| Hermes hook signature changes | Task 3.4 (integration test) verifies against real Hermes | Phase 3 |
| Token counts don't match `agent.usage_pricing` | Task 2.2 prefers `usage` dict; integration test cross-checks | Phase 2-3 |
| Schema drifts from canonical | Task 3.2 snapshot tests; Task 4.4 upstream issue keeps openclaw aligned | Phase 3-4 |
| Plugin holds state across resets | Task 3.3 test covers stale cleanup | Phase 3 |
| User leaks PII | Task 4.1 README disclaimer + Task 3.2 privacy mode tests | Phase 3-4 |

## Testing Checklist (cumulative)

- [ ] Unit tests for `events.py` (Task 3.2)
- [ ] Unit tests for hook handlers (Task 3.3)
- [ ] Integration test on Jarvis (Task 3.4)
- [ ] Trace grouping mode tests (Task 3.5)
- [ ] Schema snapshot tests (Task 3.2)
- [ ] Privacy mode tests (Task 3.2)
- [ ] Stale cleanup tests (Task 3.3)
- [ ] Memory stability test (Task 3.4)
- [ ] CI on Python 3.11 + 3.12 (Task 4.2)

## Documentation Tasks
- [ ] README.md (Task 4.1)
- [ ] Examples directory (Task 4.3)
- [ ] CHANGELOG.md (Task 4.5)
- [ ] Spec moved to completed (Task 4.5)

## Launch Checklist
- [ ] All tests passing
- [ ] CI green on `main`
- [ ] README screenshots / dashboards
- [ ] Privacy mode tested with real PII fixture
- [ ] Announcement post drafted
- [ ] v0.1.0 tag pushed

## Post-Launch
- [ ] Monitor PostHog community Slack for first issues (24-48h)
- [ ] Respond to upstream PostHog issue feedback
- [ ] Watch for first external contributors
- [ ] After 30 days: collect feedback, plan v0.2.0
