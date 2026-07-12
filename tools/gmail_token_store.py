"""
Per-user Gmail OAuth token storage backed by Google Cloud Storage.

Tokens are stored as JSON at:  gs://<BUCKET>/tokens/<username>.json

The Cloud Run service account (roles/storage.objectAdmin on the bucket)
provides credentials automatically — no extra auth setup needed.
"""

import json
import os

BUCKET_NAME = os.environ.get("GMAIL_TOKEN_BUCKET", "pm-agent-gmail-tokens")


def _blob(username: str):
    from google.cloud import storage
    return storage.Client().bucket(BUCKET_NAME).blob(f"tokens/{username}.json")


def load_token(username: str) -> dict | None:
    """Return stored token dict for user, or None if not found."""
    try:
        blob = _blob(username)
        if not blob.exists():
            return None
        return json.loads(blob.download_as_text())
    except Exception:
        return None


def save_token(username: str, token_dict: dict):
    """Write token dict for user to GCS."""
    _blob(username).upload_from_string(
        json.dumps(token_dict), content_type="application/json"
    )


def has_token(username: str) -> bool:
    """Return True if user has a stored token."""
    try:
        return _blob(username).exists()
    except Exception:
        return False
