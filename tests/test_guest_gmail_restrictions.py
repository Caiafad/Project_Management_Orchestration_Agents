"""
Gmail is withheld from trial guests.

gmail.modify is a Google restricted scope: only accounts on the OAuth consent
screen's test-user list may grant it, and publishing to everyone requires a paid
third-party security assessment. A guest who clicked "Connect Gmail" would reach
Google's "app is blocked" page, so the capability is removed for guests rather
than left to fail.
"""

import pytest

from agents.external_comms import ExternalCommsAgent
from agents.internal_comms import GMAIL_TOOL_NAMES, InternalCommsAgent
from event_orchestrator import EventOrchestrator


def tool_names(agent):
    return {t["name"] for t in agent.tools}


def test_guest_internal_comms_keeps_slack_but_loses_email():
    guest = InternalCommsAgent(username="guest-abc123", is_guest=True)
    names = tool_names(guest)

    assert not (names & GMAIL_TOOL_NAMES), "guests must have no email tools"
    assert {"send_slack_message", "send_slack_dm", "list_slack_channels"} <= names
    # Handlers must be withheld too — a tool the model cannot see could still be
    # invoked by a malformed or replayed call.
    assert not (set(guest.tool_handlers) & GMAIL_TOOL_NAMES)


def test_signed_in_internal_comms_keeps_email():
    user = InternalCommsAgent(username="david", is_guest=False)
    # draft_email belongs to External Comms; Internal Comms has the other three.
    assert {"send_email", "read_emails", "search_emails"} <= tool_names(user)


def test_guest_prompt_tells_the_model_email_is_unavailable():
    guest = InternalCommsAgent(username="guest-abc123", is_guest=True)
    assert "EMAIL UNAVAILABLE" in guest.system_prompt
    assert "never claim an email was sent" in guest.system_prompt


def test_external_comms_is_unavailable_to_guests():
    # External Comms is email-only, so it is dropped from routing entirely.
    guest_orch = EventOrchestrator(lambda e: None, username="guest-abc123", is_guest=True)
    assert "delegate_to_external_comms" not in {t["name"] for t in guest_orch.tools}


def test_external_comms_remains_for_signed_in_users():
    orch = EventOrchestrator(lambda e: None, username="david", is_guest=False)
    names = {t["name"] for t in orch.tools}
    assert "delegate_to_external_comms" in names
    assert "delegate_to_internal_comms" in names


def test_guest_orchestrator_keeps_every_document_agent():
    guest_orch = EventOrchestrator(lambda e: None, username="guest-abc123", is_guest=True)
    names = {t["name"] for t in guest_orch.tools}
    for tool in ("delegate_to_project_planning", "delegate_to_scope_definition",
                 "delegate_to_financial_manager", "delegate_to_business_manager",
                 "delegate_to_prioritization", "delegate_to_project_orchestration"):
        assert tool in names, f"{tool} must still be available to trial users"


def test_external_comms_agent_itself_still_constructs_for_guests():
    # Nothing should crash if it is constructed directly; it is simply unrouted.
    agent = ExternalCommsAgent(username="guest-abc123", is_guest=True)
    assert agent.model
