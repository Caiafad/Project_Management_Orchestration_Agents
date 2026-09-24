"""
Offline EventOrchestrator tests — no Gemini calls.

These lock in the guarantees the prompt alone could not provide: the project
brief reaches every downstream agent, file lists are exact, and a failed agent
is reported as failed without discarding work that already succeeded.
"""

import pytest
from google.genai import types

import event_orchestrator as eo
from agents.base_agent import AgentResult


class FakeAgent:
    def __init__(self, label, text="done", files=(), status="success"):
        self.label, self.text, self.status = label, text, status
        self.files = list(files)
        self.files_created = list(files)
        self.tool_handlers = {}
        self.received_context = None

    def run(self, task, context=""):
        self.received_context = context
        return AgentResult(text=self.text, files=self.files, status=self.status,
                           error=None if self.status == "success" else "simulated failure")


def part_call(name, **args):
    return types.Part(function_call=types.FunctionCall(name=name, args=args or {"task": "t"}))


def response_for(parts):
    return types.GenerateContentResponse(candidates=[
        types.Candidate(content=types.Content(role="model", parts=parts), finish_reason="STOP")])


@pytest.fixture
def harness(monkeypatch):
    """Wire fake agents, fake extraction and a scripted model into the orchestrator."""
    state = {"agents": {}, "script": [], "i": 0}

    monkeypatch.setattr(eo, "_create_event_agent",
                        lambda tool, emit, user, guest: state["agents"][tool])

    def fake_extract(agent_name, text, client=None, model=None):
        return state.get("deltas", {}).get(agent_name, {})
    monkeypatch.setattr(eo, "extract_delta", fake_extract)

    def fake_generate(client, model, contents, config):
        parts = state["script"][state["i"]]
        state["i"] += 1
        return response_for(parts)
    monkeypatch.setattr(eo, "_generate_with_retry", fake_generate)

    def build(agents, script, deltas=None):
        state.update(agents=agents, script=script, deltas=deltas or {}, i=0)
        events = []
        orch = eo.EventOrchestrator(lambda e: events.append(e), username="tester")
        return orch, events
    return build


def function_responses(orch):
    return [c.parts[0].function_response.response for c in orch.conversation_history
            if c.role == "user" and c.parts and c.parts[0].function_response]


def test_brief_is_injected_into_downstream_agents(harness):
    planning = FakeAgent("planning", "Plan complete.", ["Plan.xlsx"])
    financial = FakeAgent("financial", "Budget complete.", ["Budget.xlsx"])
    orch, _ = harness(
        {"delegate_to_project_planning": planning, "delegate_to_financial_manager": financial},
        [[part_call("delegate_to_project_planning", task="plan", context="iOS and Android")],
         [part_call("delegate_to_financial_manager", task="budget")],
         [types.Part(text="SUMMARY")]],
        deltas={"Project Planning Agent": {
            "project_name": "NovaBank App", "total_duration": "9 months",
            "phases": [{"name": "Discovery", "duration": "2 months"}],
            "team_roles": [{"role": "Mobile Engineers", "headcount": "4"}]}},
    )
    orch.run("plan and budget it")

    assert planning.received_context == "iOS and Android", "first agent gets the raw context"
    ctx = financial.received_context
    assert "PROJECT BRIEF" in ctx
    assert "9 months" in ctx and "Mobile Engineers: 4" in ctx and "Discovery" in ctx
    assert "Plan.xlsx" in ctx, "downstream agent must see files produced so far"


def test_success_response_carries_exact_file_list(harness):
    planning = FakeAgent("planning", "Plan complete.", ["Plan.xlsx", "Plan.docx"])
    orch, _ = harness({"delegate_to_project_planning": planning},
                      [[part_call("delegate_to_project_planning")], [types.Part(text="SUMMARY")]])
    orch.run("plan it")

    resp = function_responses(orch)[0]
    assert resp["status"] == "SUCCESS"
    assert resp["files_generated"] == ["Plan.xlsx", "Plan.docx"]


def test_failed_agent_is_reported_as_failed_with_no_files(harness):
    failing = FakeAgent("business", "", [], status="failed")
    orch, _ = harness({"delegate_to_business_manager": failing},
                      [[part_call("delegate_to_business_manager")], [types.Part(text="SUMMARY")]])
    out = orch.run("make a deck")

    assert "technical problem" in out, "a run where nothing succeeded should surface an error"
    resp = function_responses(orch)[0]
    assert resp["status"] == "AGENT_FAILED"
    assert resp["files_generated"] == []


def test_late_failure_keeps_earlier_successes(harness):
    planning = FakeAgent("planning", "Plan complete.", ["Plan.xlsx"])
    failing = FakeAgent("business", "", [], status="failed")
    orch, _ = harness(
        {"delegate_to_project_planning": planning, "delegate_to_business_manager": failing},
        [[part_call("delegate_to_project_planning")],
         [part_call("delegate_to_business_manager")],
         [types.Part(text="SUMMARY")]],
    )
    out = orch.run("plan it then make a deck")

    assert out == "SUMMARY", "an earlier success must not be discarded by a later failure"
    assert [f.filename for f in orch.state.files] == ["Plan.xlsx"]
    warnings = [c.parts[0].text for c in orch.conversation_history
                if c.role == "user" and c.parts and (c.parts[0].text or "").startswith("SYSTEM NOTICE")]
    assert warnings and "Business Manager" in warnings[0]


def test_partial_files_from_failed_agent_are_surfaced(harness):
    partial = FakeAgent("financial", "", ["Budget.xlsx"], status="failed")
    planning = FakeAgent("planning", "Plan complete.", ["Plan.xlsx"])
    orch, _ = harness(
        {"delegate_to_project_planning": planning, "delegate_to_financial_manager": partial},
        [[part_call("delegate_to_project_planning")],
         [part_call("delegate_to_financial_manager")],
         [types.Part(text="SUMMARY")]],
    )
    orch.run("plan and budget")

    resp = function_responses(orch)[-1]
    assert resp["status"] == "AGENT_FAILED"
    assert resp["files_generated"] == ["Budget.xlsx"]
    assert "Budget.xlsx" in resp["instruction"]


def test_turn_limit_stops_a_looping_orchestrator(harness):
    agent = FakeAgent("planning", "again", [])
    orch, _ = harness({"delegate_to_project_planning": agent},
                      [[part_call("delegate_to_project_planning")]] * 40)
    out = orch.run("loop forever")

    assert "allowed number of steps" in out
