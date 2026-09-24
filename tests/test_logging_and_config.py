import json
import logging

import pytest

import config
from logging_setup import (CloudRunFormatter, ContextFilter, LocalFormatter,
                           Timer, bind, context, current_context)


def make_record(msg="hello", **extra):
    record = logging.LogRecord("pm.test", logging.INFO, __file__, 1, msg, (), None)
    for key, value in extra.items():
        setattr(record, key, value)
    ContextFilter().filter(record)
    return record


@pytest.fixture(autouse=True)
def clean_context():
    with context(user="", run_id="", agent=""):
        yield


def test_cloud_run_formatter_emits_queryable_json():
    with context(user="u@example.com", run_id="abc123", agent="Financial Manager"):
        payload = json.loads(CloudRunFormatter().format(
            make_record("agent finished", event="agent_finished", status="failed", duration_ms=1200)))

    assert payload["severity"] == "INFO"
    assert payload["message"] == "agent finished"
    assert payload["run_id"] == "abc123"
    assert payload["agent"] == "Financial Manager"
    assert payload["user"] == "u@example.com"
    assert payload["event"] == "agent_finished"
    assert payload["status"] == "failed"
    assert payload["duration_ms"] == 1200


def test_cloud_run_formatter_omits_empty_context():
    payload = json.loads(CloudRunFormatter().format(make_record()))
    assert "run_id" not in payload and "agent" not in payload


def test_exception_is_captured_for_error_reporting():
    try:
        raise ValueError("boom")
    except ValueError:
        import sys
        record = logging.LogRecord("pm.test", logging.ERROR, __file__, 1, "failed", (), sys.exc_info())
        ContextFilter().filter(record)
    payload = json.loads(CloudRunFormatter().format(record))
    assert "ValueError: boom" in payload["exception"]
    assert payload["stack_trace"] == payload["exception"]


def test_local_formatter_is_readable_and_includes_extras():
    with context(run_id="abc123", agent="Planning"):
        line = LocalFormatter().format(make_record("tool call", tool="create_excel"))
    assert "[abc123 Planning]" in line
    assert "tool call" in line
    assert "tool=create_excel" in line


def test_context_restores_previous_values():
    bind(user="outer", run_id="r1")
    with context(user="inner"):
        assert current_context()["user"] == "inner"
        assert current_context()["run_id"] == "r1", "unset fields stay untouched"
    assert current_context()["user"] == "outer"


def test_timer_measures_elapsed_time():
    with Timer() as t:
        sum(range(10000))
    assert isinstance(t.ms, int) and t.ms >= 0


# ── config: the stale-.env bug these tests exist to prevent ──────────────────

def test_every_tier_resolves_to_a_model():
    for tier in ("reasoning", "fast", "lite"):
        assert config.model_for(tier)


def test_agent_override_beats_the_tier(monkeypatch):
    monkeypatch.setenv("MODEL_OVERRIDE_FINANCIAL_MANAGER", "gemini-2.5-pro")
    assert config.model_for("reasoning", "FINANCIAL_MANAGER") == "gemini-2.5-pro"
    assert config.model_for("reasoning", "PROJECT_PLANNING") == config.MODEL_REASONING


def test_orchestrator_uses_the_reasoning_tier_not_a_stale_env_pin():
    # A bare GEMINI_MODEL in .env used to silently pin the router to an old model.
    assert config.ORCHESTRATOR_MODEL == config.MODEL_REASONING


def test_guest_output_cap_is_not_throttled():
    # A cap below the model ceiling makes Gemini 3 tool calls fail as MALFORMED.
    assert config.GUEST_MAX_OUTPUT_TOKENS >= 65536
    assert config.GUEST_GEMINI_MODEL == config.MODEL_FAST


def test_agents_declare_a_valid_tier_and_key():
    from agents.project_planning import ProjectPlanningAgent
    from agents.financial_manager import FinancialManagerAgent
    from agents.prioritization import PrioritizationAgent
    from agents.internal_comms import InternalCommsAgent

    for cls in (ProjectPlanningAgent, FinancialManagerAgent, PrioritizationAgent, InternalCommsAgent):
        assert cls.TIER in config.MODEL_TIERS
        assert cls.AGENT_KEY


def test_guest_agents_use_the_fast_tier():
    from agents.financial_manager import FinancialManagerAgent
    assert FinancialManagerAgent(is_guest=True).model == config.MODEL_FAST
    assert FinancialManagerAgent().model == config.MODEL_REASONING
