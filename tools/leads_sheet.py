"""
Best-effort lead capture for trial signups — appends a row to the
"PM Agent Trial Leads" Google Sheet via the Sheets v4 API.

Authenticates with Application Default Credentials (the same keyless pattern
already used for google.cloud.storage / discoveryengine in this app) — no
OAuth flow or key file. The target Sheet must be shared with the Cloud Run
service's runtime service account as an Editor.

Failures here must never block a trial signup, so every entry point catches
and logs rather than raises.
"""

import logging
from datetime import datetime, timezone

import google.auth
from googleapiclient.discovery import build

from config import TRIAL_LEADS_SHEET_ID

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
SHEET_RANGE = "Sheet1!A:C"


def _sheets_service():
    credentials, _ = google.auth.default(scopes=SCOPES)
    return build("sheets", "v4", credentials=credentials)


def append_lead(email: str, guest_id: str) -> None:
    """Append [email, timestamp, guest_id] to the leads sheet. Best-effort."""
    if not TRIAL_LEADS_SHEET_ID:
        log.warning("TRIAL_LEADS_SHEET_ID not configured — lead not captured",
                    extra={"event": "lead_skipped", "lead_email": email})
        return
    try:
        service = _sheets_service()
        row = [[email, datetime.now(timezone.utc).isoformat(), guest_id]]
        service.spreadsheets().values().append(
            spreadsheetId=TRIAL_LEADS_SHEET_ID,
            range=SHEET_RANGE,
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": row},
        ).execute()
    except Exception as e:
        log.warning("failed to append lead to sheet: %s", e,
                    extra={"event": "lead_append_failed", "lead_email": email, "guest": guest_id})
