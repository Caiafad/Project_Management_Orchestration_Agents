from agents.base_agent import BaseAgent

AGENT_MODEL = "gemini-2.5-flash"

SYSTEM_PROMPT = """You are the External Communications Agent — an expert in professional external stakeholder communications.

Your responsibilities:
- Handle vendor emails, client updates, and stakeholder communications
- Draft and send professional external emails
- Coordinate with the Business Manager Agent's outputs for external-facing content
- Manage client relationships through timely and professional communication
- Draft proposals, status updates, and formal correspondence
- Search and review incoming external communications

You have access to the following tools:
1. send_email - Send emails to external stakeholders (from the current user's own Gmail account)
2. read_emails - Read recent emails from the current user's inbox
3. search_emails - Search for specific email threads
4. draft_email - Create email drafts for review before sending

When communicating externally:
- Maintain the highest level of professionalism
- Be diplomatic and strategic in tone
- Never share internal team details or conflicts
- Focus on outcomes, progress, and value delivered
- Include clear next steps and expected timelines
- Use draft_email when the content should be reviewed before sending
- Always proofread for tone and accuracy

Prefer drafting emails for review rather than sending directly unless explicitly instructed to send."""

TOOLS = [
    {
        "name": "send_email",
        "description": "Send an email to an external stakeholder from the current user's Gmail. Use with caution — prefer draft_email for review first.",
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
        "name": "draft_email",
        "description": "Create an email draft in Gmail for review before sending. Preferred for external communications.",
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
]


class ExternalCommsAgent(BaseAgent):
    def __init__(self, username: str = None):
        from tools.gmail_tools import send_email, read_emails, search_emails, draft_email

        tool_handlers = {
            "send_email":    lambda to, subject, body, **_:          send_email(to, subject, body, username=username),
            "read_emails":   lambda max_results=10, query="", **_:   read_emails(max_results, query, username=username),
            "search_emails": lambda query, max_results=10, **_:      search_emails(query, max_results, username=username),
            "draft_email":   lambda to, subject, body, **_:          draft_email(to, subject, body, username=username),
        }

        super().__init__(
            name="External Communications Agent",
            system_prompt=SYSTEM_PROMPT,
            tools=TOOLS,
            tool_handlers=tool_handlers,
            model=AGENT_MODEL,
        )
