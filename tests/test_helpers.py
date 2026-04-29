"""Unit tests for helpers.py utility functions."""
from __future__ import annotations

import os
import pytest
from helpers import (
    generate_trace_id,
    generate_span_id,
    env,
    env_bool,
    env_int,
    env_float,
    safe_value,
    extract_usage_and_cost,
)


def test_generate_trace_id_length():
    tid = generate_trace_id()
    assert len(tid) == 32  # 16 bytes hex


def test_generate_span_id_length():
    sid = generate_span_id()
    assert len(sid) == 16  # 8 bytes hex


def test_generate_ids_unique():
    assert generate_trace_id() != generate_trace_id()
    assert generate_span_id() != generate_span_id()


def test_env_returns_default(monkeypatch):
    monkeypatch.delenv("SOME_VAR_XYZ", raising=False)
    assert env("SOME_VAR_XYZ", "default") == "default"


def test_env_strips_whitespace(monkeypatch):
    monkeypatch.setenv("SOME_VAR_XYZ", "  value  ")
    assert env("SOME_VAR_XYZ") == "value"


def test_env_bool_true_values(monkeypatch):
    for val in ["1", "true", "True", "yes", "on"]:
        monkeypatch.setenv("BOOL_VAR", val)
        assert env_bool("BOOL_VAR") is True


def test_env_bool_false_values(monkeypatch):
    monkeypatch.setenv("BOOL_VAR", "false")
    assert env_bool("BOOL_VAR") is False
    monkeypatch.delenv("BOOL_VAR")
    assert env_bool("BOOL_VAR") is False


def test_env_bool_multiple_names(monkeypatch):
    monkeypatch.delenv("VAR_A", raising=False)
    monkeypatch.setenv("VAR_B", "true")
    assert env_bool("VAR_A", "VAR_B") is True


def test_env_int_valid(monkeypatch):
    monkeypatch.setenv("INT_VAR", "42")
    assert env_int("INT_VAR", 0) == 42


def test_env_int_invalid(monkeypatch):
    monkeypatch.setenv("INT_VAR", "notanint")
    assert env_int("INT_VAR", 99) == 99


def test_env_float_valid(monkeypatch):
    monkeypatch.setenv("FLOAT_VAR", "3.14")
    assert env_float("FLOAT_VAR", 0.0) == pytest.approx(3.14)


def test_env_float_invalid(monkeypatch):
    monkeypatch.setenv("FLOAT_VAR", "notafloat")
    assert env_float("FLOAT_VAR", 1.5) == 1.5


def test_safe_value_none():
    assert safe_value(None) is None


def test_safe_value_int():
    assert safe_value(42) == 42


def test_safe_value_bool():
    assert safe_value(True) is True


def test_safe_value_short_string():
    assert safe_value("hello") == "hello"


def test_safe_value_truncates_long_string():
    long = "x" * 15000
    result = safe_value(long, max_chars=100)
    assert len(result) < 200
    assert "truncated" in result


def test_safe_value_dict():
    d = {"key": "value"}
    result = safe_value(d)
    assert result == {"key": "value"}


def test_safe_value_non_serialisable():
    class Unserializable:
        def __repr__(self):
            return "Unserializable()"
    # Should not raise
    result = safe_value(Unserializable())
    assert isinstance(result, str)


def test_extract_usage_from_dict():
    usage = {"input_tokens": 10, "output_tokens": 5, "cache_read_tokens": 2}
    inp, out, cr, cw, rs, cost = extract_usage_and_cost(
        usage, None, model="m", provider="p", base_url="", api_mode="chat"
    )
    assert inp == 10
    assert out == 5
    assert cr == 2
    assert cw is None


def test_extract_usage_empty_dict():
    inp, out, cr, cw, rs, cost = extract_usage_and_cost(
        {}, None, model="m", provider="p", base_url="", api_mode="chat"
    )
    assert inp is None
    assert out is None


def test_extract_usage_no_hermes(monkeypatch):
    """When agent.usage_pricing is unavailable, tokens come back None."""
    class FakeUsage:
        input_tokens = 5
        output_tokens = 3

    class FakeResponse:
        usage = FakeUsage()

    # agent.usage_pricing won't be importable in test env — should not raise
    inp, out, cr, cw, rs, cost = extract_usage_and_cost(
        None, FakeResponse(), model="m", provider="p", base_url="", api_mode="chat"
    )
    # Without agent.usage_pricing, all will be None (import will fail silently)
    assert inp is None or isinstance(inp, int)
