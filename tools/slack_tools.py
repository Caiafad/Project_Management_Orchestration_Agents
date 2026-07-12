import json
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from config import SLACK_BOT_TOKEN


def _get_slack_client():
    """Return an authenticated Slack WebClient."""
    if not SLACK_BOT_TOKEN:
        raise ValueError("SLACK_BOT_TOKEN not configured in .env")
    return WebClient(token=SLACK_BOT_TOKEN)


def send_slack_message(channel: str, text: str) -> str:
    """Send a message to a Slack channel."""
    try:
        client = _get_slack_client()
        response = client.chat_postMessage(channel=channel, text=text)
        return json.dumps({"status": "sent", "channel": channel, "ts": response["ts"]})
    except SlackApiError as e:
        return json.dumps({"error": str(e.response["error"])})
    except Exception as e:
        return json.dumps({"error": str(e)})


def send_slack_dm(user_id: str, text: str) -> str:
    """Send a direct message to a Slack user."""
    try:
        client = _get_slack_client()
        conversation = client.conversations_open(users=[user_id])
        channel_id = conversation["channel"]["id"]
        response = client.chat_postMessage(channel=channel_id, text=text)
        return json.dumps({"status": "sent", "user_id": user_id, "ts": response["ts"]})
    except SlackApiError as e:
        return json.dumps({"error": str(e.response["error"])})
    except Exception as e:
        return json.dumps({"error": str(e)})


def list_slack_channels() -> str:
    """List available Slack channels the bot has access to."""
    try:
        client = _get_slack_client()
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
