"""
Per-user Slack OAuth token storage backed by Google Cloud Storage.

Mirrors tools/gmail_token_store.py. Tokens live in the same bucket under a
separate prefix:  gs://<BUCKET>/slack-tokens/<username>.json

Stored dict is the relevant subset of Slack's oauth.v2.access response:
  {"access_token": "xoxb-...", "team_id": "...", "team_name": "...", "bot_user_id": "..."}
"""

import json
import os

BUCKET_NAME = os.environ.get("GMAIL_TOKEN_BUCKET", "pm-agent-gmail-tokens")


def _blob(username: str):
    from google.cloud import storage
    return storage.Client().bucket(BUCKET_NAME).blob(f"slack-tokens/{username}.json")


def load_token(username: str) -> dict | None:
    try:
        blob = _blob(username)
        if not blob.exists():
            return None
        return json.loads(blob.download_as_text())
    except Exception:
        return None


def save_token(username: str, token_dict: dict):
    _blob(username).upload_from_string(
        json.dumps(token_dict), content_type="application/json"
    )


def has_token(username: str) -> bool:
    try:
        return _blob(username).exists()
    except Exception:
        return False


def delete_token(username: str) -> bool:
    try:
        blob = _blob(username)
        if blob.exists():
            blob.delete()
            return True
    except Exception:
        pass
    return False
