from agents.base_agent import BaseAgent
from tools.slack_tools import send_slack_message, send_slack_dm, list_slack_channels


SYSTEM_PROMPT = """You are the Internal Communications Agent — an expert scrum master and internal team communications specialist.

Your responsibilities:
- Communicate with the internal project team via email and Slack
- Facilitate scrum ceremonies (standups, sprint planning, retrospectives)
- Ensure team members with dependencies are aligned and communicating
- Send status updates, meeting summaries, and action items
- Draft internal announcements and team communications
- Monitor and follow up on team communications
- Help resolve blockers by connecting the right people

You have access to the following tools:
1. send_email - Send emails to team members (from the current user's own Gmail account)
2. read_emails - Read recent emails from the current user's inbox
3. search_emails - Search for specific email threads
4. send_slack_message - Post messages to Slack channels
5. send_slack_dm - Send direct messages to team members on Slack
6. list_slack_channels - List available Slack channels

When communicating:
- Be clear, concise, and action-oriented
- Always include context so recipients understand why they're being contacted
- For standups: ask for yesterday's work, today's plan, and blockers
- For dependency coordination: be specific about what's needed and by when
- Use Slack for quick updates and email for formal communications
- Follow up on unanswered blockers proactively
- Keep a professional but approachable tone

Always confirm the message content with the orchestrator before sending."""

TOOLS = [
    {
        "name": "send_email",
        "description": "Send an email to a team member from the current user's Gmail account.",
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient email address"},
                "subject": {"type": "string", "description": "Email subject line"},
                "body": {"type": "string", "description": "Email body text"},
            },
            "required": ["to", "subject", "body"],
        },
    },
    {
        "name": "read_emails",
        "description": "Read recent emails from the current user's inbox.",
        "input_schema": {
            "type": "object",
            "properties": {
                "max_results": {"type": "integer", "description": "Maximum number of emails to return", "default": 10},
                "query": {"type": "string", "description": "Optional Gmail search query", "default": ""},
            },
        },
    },
    {
        "name": "search_emails",
        "description": "Search for specific emails using Gmail search syntax.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Gmail search query"},
                "max_results": {"type": "integer", "description": "Maximum results", "default": 10},
            },
            "required": ["query"],
        },
    },
    {
        "name": "send_slack_message",
        "description": "Send a message to a Slack channel.",
        "input_schema": {
            "type": "object",
            "properties": {
                "channel": {"type": "string", "description": "Slack channel name or ID (e.g., '#general')"},
                "text": {"type": "string", "description": "Message text"},
            },
            "required": ["channel", "text"],
        },
    },
    {
        "name": "send_slack_dm",
        "description": "Send a direct message to a team member on Slack.",
        "input_schema": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string", "description": "Slack user ID"},
                "text": {"type": "string", "description": "Message text"},
            },
            "required": ["user_id", "text"],
        },
    },
    {
        "name": "list_slack_channels",
        "description": "List available Slack channels the bot has access to.",
        "input_schema": {"type": "object", "properties": {}},
    },
]


# Gmail needs Google's approval of a restricted scope (gmail.modify), which only
# covers accounts on the OAuth consent screen's test-user list. Trial guests are
# strangers, so email tools are withheld from them and only Slack remains.
GMAIL_TOOL_NAMES = {"send_email", "read_emails", "search_emails", "draft_email"}

GUEST_PROMPT_NOTE = """

TRIAL SESSION — EMAIL UNAVAILABLE:
You have no email tools in this session. Do not offer to send, draft, read or
search email, and never claim an email was sent. Use Slack for everything, and
if the user asks for email, say plainly that email is not available in the trial
and offer to post the message to Slack instead."""


class InternalCommsAgent(BaseAgent):
    TIER = "fast"
    AGENT_KEY = "INTERNAL_COMMS"

    def __init__(self, username: str = None, is_guest: bool = False):
        from tools.gmail_tools import send_email, read_emails, search_emails

        tool_handlers = {
            "send_email":         lambda to, subject, body, **_:      send_email(to, subject, body, username=username),
            "read_emails":        lambda max_results=10, query="", **_: read_emails(max_results, query, username=username),
            "search_emails":      lambda query, max_results=10, **_:  search_emails(query, max_results, username=username),
            "send_slack_message": lambda channel, text, **_:          send_slack_message(channel, text, username=username),
            "send_slack_dm":      lambda user_id, text, **_:          send_slack_dm(user_id, text, username=username),
            "list_slack_channels":lambda **_:                          list_slack_channels(username=username),
        }

        tools, prompt = TOOLS, SYSTEM_PROMPT
        if is_guest:
            tools = [t for t in TOOLS if t["name"] not in GMAIL_TOOL_NAMES]
            tool_handlers = {k: v for k, v in tool_handlers.items() if k not in GMAIL_TOOL_NAMES}
            prompt = SYSTEM_PROMPT + GUEST_PROMPT_NOTE

        super().__init__(
            name="Internal Communications Agent",
            system_prompt=prompt,
            tools=tools,
            tool_handlers=tool_handlers,
            is_guest=is_guest,
        )
