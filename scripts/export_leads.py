"""
Export trial leads from Firestore to the "PM Agent Trial Leads" Sheet.

Firestore is the record of leads; the Sheet is a convenience view. The Cloud Run
service account cannot write to Sheets (its token carries the cloud-platform
scope, which the Sheets API rejects), so this runs locally under your own
credentials instead.

Setup once:
    gcloud auth application-default login --scopes=\\
      openid,https://www.googleapis.com/auth/userinfo.email,\\
      https://www.googleapis.com/auth/cloud-platform,\\
      https://www.googleapis.com/auth/spreadsheets

Run:
    python scripts/export_leads.py            # print the leads
    python scripts/export_leads.py --write    # replace the Sheet's contents
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import TRIAL_LEADS_SHEET_ID
from tools.trial_store import list_leads

HEADER = ["Email", "Signed up (UTC)", "Guest session ID", "Source"]


def main(write: bool) -> int:
    leads = list_leads(limit=5000)
    if not leads:
        print("No leads captured yet.")
        return 0

    rows = [[l["email"], l["created_at"], l["guest_id"], l["source"]] for l in leads]
    print(f"{len(rows)} lead(s):")
    for row in rows[:20]:
        print("  " + " | ".join(row))
    if len(rows) > 20:
        print(f"  ... and {len(rows) - 20} more")

    if not write:
        print("\nRe-run with --write to update the Sheet.")
        return 0

    if not TRIAL_LEADS_SHEET_ID:
        print("TRIAL_LEADS_SHEET_ID is not set.")
        return 1

    import google.auth
    from googleapiclient.discovery import build

    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/spreadsheets"])
    service = build("sheets", "v4", credentials=credentials)
    service.spreadsheets().values().update(
        spreadsheetId=TRIAL_LEADS_SHEET_ID,
        range="Sheet1!A1",
        valueInputOption="RAW",
        body={"values": [HEADER] + rows},
    ).execute()
    print(f"\nWrote {len(rows)} lead(s) to the Sheet.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main("--write" in sys.argv))
