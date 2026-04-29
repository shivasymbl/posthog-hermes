"""Utility functions for posthog-hermes plugin."""
from __future__ import annotations

import json
import logging
import os
import secrets
from typing import Any, Optional

logger = logging.getLogger(__name__)


def generate_trace_id() -> str:
    return secrets.token_hex(16)


def generate_span_id() -> str:
    return secrets.token_hex(8)


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def env_bool(*names: str) -> bool:
    for name in names:
        val = env(name).lower()
        if val in {"1", "true", "yes", "on"}:
            return True
    return False


def env_int(name: str, default: int) -> int:
    try:
        return int(env(name, str(default)))
    except ValueError:
        return default


def env_float(name: str, default: float) -> float:
    try:
        return float(env(name, str(default)))
    except ValueError:
        return default


def safe_value(value: Any, max_chars: Optional[int] = None) -> Any:
    """Truncate long strings; JSON-coerce non-serialisable objects."""
    if max_chars is None:
        max_chars = env_int("POSTHOG_MAX_FIELD_CHARS", 12000)
    if value is None or isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, str):
        if len(value) > max_chars:
            return value[:max_chars] + f"… [{len(value) - max_chars} chars truncated]"
        return value
    try:
        s = json.dumps(value, default=str)
        if len(s) > max_chars:
            return s[:max_chars] + "…"
        return json.loads(s)
    except Exception:
        return str(value)[:max_chars]


def extract_usage_and_cost(
    usage: Any,
    response: Any,
    *,
    model: str,
    provider: str,
    base_url: str,
    api_mode: str,
) -> tuple[Optional[int], Optional[int], Optional[int], Optional[int], Optional[int], Optional[float]]:
    """Return (input_tokens, output_tokens, cache_read, cache_write, reasoning, cost_usd).

    Prefers Hermes-normalised `usage` dict; falls back to raw response.usage.
    Lazily imports agent.usage_pricing so plugin loads even on older Hermes.
    """
    input_tokens = output_tokens = cache_read = cache_write = reasoning = None
    cost_usd: Optional[float] = None

    if isinstance(usage, dict) and usage:
        input_tokens = usage.get("input_tokens")
        output_tokens = usage.get("output_tokens") or usage.get("completion_tokens")
        cache_read = usage.get("cache_read_tokens")
        cache_write = usage.get("cache_write_tokens")
        reasoning = usage.get("reasoning_tokens")
    elif response is not None:
        raw = getattr(response, "usage", None)
        if raw:
            try:
                from agent.usage_pricing import normalize_usage  # type: ignore[import]
                canonical = normalize_usage(raw, provider=provider, api_mode=api_mode)
                input_tokens = canonical.input_tokens
                output_tokens = canonical.output_tokens
                cache_read = canonical.cache_read_tokens
                cache_write = canonical.cache_write_tokens
                reasoning = getattr(canonical, "reasoning_tokens", None)
            except Exception as exc:
                if env_bool("HERMES_POSTHOG_DEBUG"):
                    logger.debug("posthog-hermes: usage normalize failed: %s", exc)

    if input_tokens is not None or output_tokens is not None:
        try:
            from agent.usage_pricing import CanonicalUsage, estimate_usage_cost, get_pricing_entry  # type: ignore[import]
            from decimal import Decimal
            _ONE_M = Decimal("1000000")
            cu = CanonicalUsage(
                input_tokens=input_tokens or 0,
                output_tokens=output_tokens or 0,
                cache_read_tokens=cache_read or 0,
                cache_write_tokens=cache_write or 0,
            )
            entry = get_pricing_entry(model, provider=provider, base_url=base_url)
            if entry and entry.input_cost_per_million is not None:
                total = Decimal(0)
                if input_tokens and entry.input_cost_per_million:
                    total += Decimal(input_tokens) * entry.input_cost_per_million / _ONE_M
                if output_tokens and entry.output_cost_per_million:
                    total += Decimal(output_tokens) * entry.output_cost_per_million / _ONE_M
                cost_usd = float(total)
            else:
                cost = estimate_usage_cost(model, cu, provider=provider, base_url=base_url, api_key="")
                if cost.amount_usd is not None:
                    cost_usd = float(cost.amount_usd)
        except Exception as exc:
            if env_bool("HERMES_POSTHOG_DEBUG"):
                logger.debug("posthog-hermes: cost estimation failed: %s", exc)

    return input_tokens, output_tokens, cache_read, cache_write, reasoning, cost_usd
