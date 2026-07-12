"""
Gmail tools — per-user OAuth token support.

Each user's token is stored in GCS (via gmail_token_store).
Pass username= to every function so the right token is loaded.
If the user hasn't connected Gmail yet, functions return a clear error JSON.
"""

import json
import base64
from email.mime.text import MIMEText

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]


def _get_gmail_service(username: str):
    """Return an authenticated Gmail service for the given user.

    Raises PermissionError with a user-friendly message if the user
    hasn't connected Gmail yet, so the agent can relay the message.
    """
    from tools.gmail_token_store import load_token, save_token

    token_data = load_token(username)
    if not token_data:
        raise PermissionError(
            "Gmail is not connected for your account. "
            "Please click 'Connect Gmail' in the sidebar and authorise access."
        )

    creds = Credentials.from_authorized_user_info(token_data, SCOPES)

    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            save_token(username, json.loads(creds.to_json()))
        else:
            raise PermissionError(
                "Your Gmail authorisation has expired. "
                "Please click 'Connect Gmail' in the sidebar to re-authorise."
            )

    return build("gmail", "v1", credentials=creds)


def send_email(to: str, subject: str, body: str, username: str = None) -> str:
    try:
        service = _get_gmail_service(username)
        message = MIMEText(body)
        message["to"] = to
        message["subject"] = subject
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        sent = service.users().messages().send(
            userId="me", body={"raw": raw}
        ).execute()
        return json.dumps({"status": "sent", "message_id": sent["id"]})
    except Exception as e:
        return json.dumps({"error": str(e)})


def read_emails(max_results: int = 10, query: str = "", username: str = None) -> str:
    try:
        service = _get_gmail_service(username)
        results = service.users().messages().list(
            userId="me", maxResults=max_results, q=query
        ).execute()
        messages = results.get("messages", [])
        emails = []
        for msg in messages:
            detail = service.users().messages().get(
                userId="me", id=msg["id"], format="metadata",
                metadataHeaders=["From", "Subject", "Date"]
            ).execute()
            headers = {h["name"]: h["value"] for h in detail["payload"]["headers"]}
            emails.append({
                "id": msg["id"],
                "from": headers.get("From", ""),
                "subject": headers.get("Subject", ""),
                "date": headers.get("Date", ""),
                "snippet": detail.get("snippet", ""),
            })
        return json.dumps(emails, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


def search_emails(query: str, max_results: int = 10, username: str = None) -> str:
    return read_emails(max_results=max_results, query=query, username=username)


def draft_email(to: str, subject: str, body: str, username: str = None) -> str:
    try:
        service = _get_gmail_service(username)
        message = MIMEText(body)
        message["to"] = to
        message["subject"] = subject
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        draft = service.users().drafts().create(
            userId="me", body={"message": {"raw": raw}}
        ).execute()
        return json.dumps({"status": "drafted", "draft_id": draft["id"]})
    except Exception as e:
        return json.dumps({"error": str(e)})
