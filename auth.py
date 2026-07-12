"""
auth.py — Authentication for the Project Management Agent web app.

Users are stored as bcrypt-hashed passwords in:
  - Local dev : users.json  (managed via manage_users.py)
  - Production: USERS_JSON environment variable (JSON string)

Sessions are signed cookies using itsdangerous (7-day expiry).
SECRET_KEY environment variable must be set in production.
"""

import json
import os
from pathlib import Path

import hashlib
import hmac
import secrets

from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from fastapi import Request
from fastapi.responses import RedirectResponse

# ── Config ────────────────────────────────────────────────────────────────────
SECRET_KEY      = os.environ.get("SECRET_KEY", "dev-secret-change-in-production")
SESSION_MAX_AGE = 60 * 60 * 24 * 7   # 7 days in seconds
COOKIE_SECURE   = os.environ.get("COOKIE_SECURE", "true").lower() == "true"

_serializer = URLSafeTimedSerializer(SECRET_KEY)
USERS_FILE  = Path(__file__).parent / "users.json"

# ── Password hashing (stdlib pbkdf2 — no bcrypt dependency) ──────────────────
def _hash_password(password: str, salt: str | None = None) -> str:
    """Return 'salt$hash' string using PBKDF2-HMAC-SHA256."""
    if salt is None:
        salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 260_000)
    return f"{salt}${dk.hex()}"

def _verify_password(password: str, stored: str) -> bool:
    try:
        salt, _ = stored.split("$", 1)
        expected = _hash_password(password, salt)
        return hmac.compare_digest(expected, stored)
    except Exception:
        return False


# ── User store ────────────────────────────────────────────────────────────────
def load_users() -> dict:
    """Return {username: hashed_password} from env var or users.json."""
    env = os.environ.get("USERS_JSON")
    if env:
        return json.loads(env)
    if USERS_FILE.exists():
        return json.loads(USERS_FILE.read_text(encoding="utf-8"))
    return {}


def save_users(users: dict):
    """Persist users to users.json (local dev only)."""
    USERS_FILE.write_text(json.dumps(users, indent=2), encoding="utf-8")


def add_user(username: str, password: str):
    users = load_users()
    users[username.strip().lower()] = _hash_password(password)
    save_users(users)


def remove_user(username: str):
    users = load_users()
    users.pop(username.strip().lower(), None)
    save_users(users)


# ── Password verification ─────────────────────────────────────────────────────
def verify_login(username: str, password: str) -> bool:
    users = load_users()
    hashed = users.get(username.strip().lower())
    if not hashed:
        return False
    return _verify_password(password, hashed)


# ── Session tokens ────────────────────────────────────────────────────────────
def create_session(username: str) -> str:
    return _serializer.dumps({"u": username.strip().lower()})


def decode_session(token: str | None) -> str | None:
    """Return username if token is valid and not expired, else None."""
    if not token:
        return None
    try:
        data = _serializer.loads(token, max_age=SESSION_MAX_AGE)
        return data.get("u")
    except (BadSignature, SignatureExpired):
        return None


# ── Request helpers ───────────────────────────────────────────────────────────
def get_user_from_request(request: Request) -> str | None:
    """Extract and validate session cookie from an HTTP request."""
    return decode_session(request.cookies.get("session"))


def get_user_from_ws_headers(headers) -> str | None:
    """
    Extract and validate session cookie from WebSocket upgrade headers.
    FastAPI's WebSocket.headers is a Headers object (iterable of (name, value)).
    """
    cookie_header = headers.get("cookie", "")
    for part in cookie_header.split(";"):
        name, _, value = part.strip().partition("=")
        if name.strip() == "session":
            return decode_session(value.strip())
    return None


def require_auth(request: Request) -> str:
    """
    FastAPI dependency — returns username or redirects to /login.
    Use as: Depends(require_auth)
    """
    user = get_user_from_request(request)
    if not user:
        # Raise with redirect headers; FastAPI will return this response
        from fastapi import HTTPException
        raise HTTPException(
            status_code=307,
            headers={"Location": "/login"},
        )
    return user


def make_session_cookie(response, username: str):
    """Attach a signed session cookie to a response."""
    response.set_cookie(
        key="session",
        value=create_session(username),
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="lax",
        max_age=SESSION_MAX_AGE,
    )
    return response


def clear_session_cookie(response):
    """Remove the session cookie."""
    response.delete_cookie("session")
    return response
