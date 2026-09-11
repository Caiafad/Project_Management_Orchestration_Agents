"""
Slack tools — per-user OAuth token support.

Each user's bot token (from the "Add to Slack" OAuth flow in web_app.py) is
stored in GCS via slack_token_store. Pass username= so the right workspace is
used. The global SLACK_BOT_TOKEN from .env remains as a fallback for regular
(non-guest) users only — trial sessions must never post into that workspace.
"""

import json
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from config import SLACK_BOT_TOKEN

NOT_CONNECTED_MSG = (
    "Slack is not connected for your account. "
    "Please click 'Connect Slack' in the sidebar and authorise access to your workspace."
)


def _get_slack_client(username: str | None = None) -> WebClient:
    """Return a WebClient for this user's workspace.

    Raises PermissionError with a user-friendly message if no token is
    available, so the agent can relay it instead of failing opaquely.
    """
    from tools.slack_token_store import load_token
    from auth import is_guest

    if username:
        token_data = load_token(username)
        if token_data and token_data.get("access_token"):
            return WebClient(token=token_data["access_token"])
        if is_guest(username):
            raise PermissionError(NOT_CONNECTED_MSG)

    if not SLACK_BOT_TOKEN:
        raise PermissionError(NOT_CONNECTED_MSG)
    return WebClient(token=SLACK_BOT_TOKEN)


def send_slack_message(channel: str, text: str, username: str | None = None) -> str:
    """Send a message to a Slack channel."""
    try:
        client = _get_slack_client(username)
        response = client.chat_postMessage(channel=channel, text=text)
        return json.dumps({"status": "sent", "channel": channel, "ts": response["ts"]})
    except SlackApiError as e:
        return json.dumps({"error": str(e.response["error"])})
    except Exception as e:
        return json.dumps({"error": str(e)})


def send_slack_dm(user_id: str, text: str, username: str | None = None) -> str:
    """Send a direct message to a Slack user."""
    try:
        client = _get_slack_client(username)
        conversation = client.conversations_open(users=[user_id])
        channel_id = conversation["channel"]["id"]
        response = client.chat_postMessage(channel=channel_id, text=text)
        return json.dumps({"status": "sent", "user_id": user_id, "ts": response["ts"]})
    except SlackApiError as e:
        return json.dumps({"error": str(e.response["error"])})
    except Exception as e:
        return json.dumps({"error": str(e)})


def list_slack_channels(username: str | None = None) -> str:
    """List available Slack channels the bot has access to."""
    try:
        client = _get_slack_client(username)
        response = client.conversations_list(types="public_channel,private_channel")
        channels = [
            {"id": ch["id"], "name": ch["name"]}
            for ch in response["channels"]
        ]
        return json.dumps(channels, indent=2)
    except SlackApiError as e:
        return json.dumps({"error": str(e.response["error"])})
    except Exception as e:
        return json.dumps({"error": str(e)})
