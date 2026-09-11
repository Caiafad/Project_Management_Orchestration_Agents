"""
Trial/guest quota tracking — Firestore-backed.

Two collections:
  trial_sessions/{guest_id}      — {email, created_at, generations_used}
  trial_quota_daily/{YYYY-MM-DD} — {count}   (atomic global counter)

Guest identity itself (the "guest-xxxx" username) is minted by the caller
(web_app.py) and reused as-is for GCS/Vertex isolation — see tools/gcs_output.py.
"""

import secrets
from datetime import datetime, timedelta, timezone

from google.cloud import firestore
from config import TRIAL_SESSION_HOURS, TRIAL_MAX_GENERATIONS, TRIAL_DAILY_CAP

SESSIONS_COLLECTION = "trial_sessions"
DAILY_COLLECTION = "trial_quota_daily"


def _client() -> firestore.Client:
    return firestore.Client()


def _today_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def new_guest_id() -> str:
    return f"guest-{secrets.token_hex(6)}"


def daily_cap_reached() -> bool:
    """Read-only check of today's global generation count against the cap."""
    doc = _client().collection(DAILY_COLLECTION).document(_today_key()).get()
    count = doc.to_dict().get("count", 0) if doc.exists else 0
    return count >= TRIAL_DAILY_CAP


def create_guest(email: str) -> str:
    """Create a new trial session doc and return its guest_id."""
    guest_id = new_guest_id()
    _client().collection(SESSIONS_COLLECTION).document(guest_id).set({
        "email": email,
        "created_at": firestore.SERVER_TIMESTAMP,
        "generations_used": 0,
    })
    return guest_id


def generations_remaining(guest_id: str) -> int:
    """Read-only: generations left for this guest (0 if the session is unknown)."""
    doc = _client().collection(SESSIONS_COLLECTION).document(guest_id).get()
    if not doc.exists:
        return 0
    used = doc.to_dict().get("generations_used", 0)
    return max(TRIAL_MAX_GENERATIONS - used, 0)


def check_and_increment(guest_id: str) -> dict:
    """
    Atomically check + increment both the per-session and global-daily counters
    for a generation the guest is about to run.
    Returns {"ok": bool, "reason": "session_cap"|"daily_cap"|"expired"|None}.
    """
    client = _client()
    session_ref = client.collection(SESSIONS_COLLECTION).document(guest_id)
    daily_ref = client.collection(DAILY_COLLECTION).document(_today_key())

    @firestore.transactional
    def _txn(transaction):
        session_snap = session_ref.get(transaction=transaction)
        if not session_snap.exists:
            return {"ok": False, "reason": "expired"}

        session_data = session_snap.to_dict()
        created_at = session_data.get("created_at")
        if created_at and (datetime.now(timezone.utc) - created_at) > timedelta(hours=TRIAL_SESSION_HOURS):
            return {"ok": False, "reason": "expired"}

        if session_data.get("generations_used", 0) >= TRIAL_MAX_GENERATIONS:
            return {"ok": False, "reason": "session_cap"}

        daily_snap = daily_ref.get(transaction=transaction)
        daily_count = daily_snap.to_dict().get("count", 0) if daily_snap.exists else 0
        if daily_count >= TRIAL_DAILY_CAP:
            return {"ok": False, "reason": "daily_cap"}

        transaction.update(session_ref, {"generations_used": firestore.Increment(1)})
        transaction.set(daily_ref, {"count": firestore.Increment(1)}, merge=True)
        return {"ok": True, "reason": None}

    return _txn(client.transaction())


def purge_expired(older_than_hours: int = None) -> list:
    """Delete trial sessions older than the cutoff. Returns the list of purged guest_ids.
    Caller (web_app.py /internal/purge-guests) is responsible for also deleting
    each guest's GCS output prefix via tools.gcs_output.delete_all_files(guest_id).
    """
    hours = older_than_hours if older_than_hours is not None else TRIAL_SESSION_HOURS
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    client = _client()
    docs = client.collection(SESSIONS_COLLECTION).where("created_at", "<", cutoff).stream()

    purged = []
    for doc in docs:
        purged.append(doc.id)
        doc.reference.delete()
    return purged
