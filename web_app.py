"""
Project Management Agent — Web Application

FastAPI server with authentication, WebSocket streaming, and file downloads.

Run with:  python web_app.py
Then open: http://localhost:8000
"""

import sys
import os
import re
import json
import logging
import queue
import hmac
import threading
import asyncio
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request, Depends, Form, UploadFile, File
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
import uvicorn

from config import (
    OUTPUT_DIR, user_output_dir, PORT, TRIAL_SESSION_HOURS, TRIAL_MAX_GENERATIONS, PURGE_SECRET,
    SLACK_CLIENT_ID, SLACK_CLIENT_SECRET, SLACK_REDIRECT_URI, SLACK_OAUTH_SCOPES,
)
from auth import (
    require_auth, get_user_from_ws_headers, verify_login,
    make_session_cookie, clear_session_cookie, is_guest,
)
from event_orchestrator import EventOrchestrator
from logging_setup import configure as configure_logging, bind as bind_log_context

# Configure before anything else logs, so uvicorn's handlers are replaced too.
configure_logging()
log = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
GMAIL_CREDENTIALS_PATH = os.environ.get("GOOGLE_CREDENTIALS_PATH", "credentials/google_credentials.json")

app = FastAPI(title="Project Management Agent")

ICON_MAP = {".docx": "word", ".xlsx": "excel", ".pptx": "powerpoint"}


# ── Auth routes (no auth required) ───────────────────────────────────────────

@app.get("/login")
async def login_page(request: Request):
    # If already logged in, redirect to app
    token = request.cookies.get("session")
    from auth import decode_session
    if decode_session(token):
        return RedirectResponse("/", status_code=302)
    return FileResponse("static/login.html")


@app.post("/login")
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    if not verify_login(username, password):
        return RedirectResponse("/login?error=1", status_code=303)
    response = RedirectResponse("/", status_code=303)
    make_session_cookie(response, username.strip().lower())
    return response


@app.get("/logout")
async def logout():
    response = RedirectResponse("/login", status_code=302)
    clear_session_cookie(response)
    return response


# ── Trial (guest) routes ──────────────────────────────────────────────────────

@app.get("/trial")
async def trial_page(request: Request):
    from auth import decode_session
    if decode_session(request.cookies.get("session")):
        return RedirectResponse("/", status_code=302)
    return FileResponse("static/trial.html")


@app.post("/trial")
async def trial_start(email: str = Form(...)):
    """Email-gated trial entry: mint a guest session (no password) and record the lead."""
    email = email.strip().lower()
    if not _EMAIL_RE.match(email) or len(email) > 254:
        return RedirectResponse("/trial?error=email", status_code=303)

    from tools.trial_store import daily_cap_reached, create_guest
    from tools.leads_sheet import append_lead

    try:
        if daily_cap_reached():
            # Degrade to lead capture only — never a broken page.
            from tools.trial_store import record_lead
            record_lead(email, "", source="capacity-waitlist")
            append_lead(email, "capacity-waitlist")
            log.info("trial at capacity — lead captured for the waitlist",
                     extra={"event": "trial_waitlisted", "lead_email": email})
            return RedirectResponse("/trial?full=1", status_code=303)
        guest_id = create_guest(email)   # also records the lead in Firestore
    except Exception as e:
        log.exception("could not create trial session",
                      extra={"event": "trial_signup_failed", "lead_email": email})
        return RedirectResponse("/trial?error=unavailable", status_code=303)

    log.info("trial session created",
             extra={"event": "trial_signup", "guest": guest_id, "lead_email": email})
    append_lead(email, guest_id)

    response = RedirectResponse("/", status_code=303)
    make_session_cookie(response, guest_id, max_age=TRIAL_SESSION_HOURS * 3600)
    return response


@app.get("/api/whoami")
async def whoami(username: str = Depends(require_auth)):
    info = {"username": username, "is_guest": is_guest(username)}
    if info["is_guest"]:
        from tools.trial_store import generations_remaining
        info["trial_max_generations"] = TRIAL_MAX_GENERATIONS
        try:
            info["trial_remaining"] = generations_remaining(username)
        except Exception:
            info["trial_remaining"] = None
    return info


@app.post("/internal/purge-guests")
async def purge_guests(request: Request):
    """Nightly cleanup (Cloud Scheduler): delete expired guest sessions + their GCS output."""
    provided = request.headers.get("x-purge-secret", "")
    if not PURGE_SECRET or not hmac.compare_digest(provided, PURGE_SECRET):
        raise HTTPException(status_code=403, detail="Forbidden")

    from tools.trial_store import purge_expired
    from tools.gcs_output import delete_all_files
    from tools.user_datastore import delete_user_workspace
    from tools import gmail_token_store, slack_token_store

    purged = purge_expired()
    summary = {"sessions_purged": len(purged), "files_deleted": 0,
               "uploads_deleted": 0, "datastores_deleted": 0, "tokens_deleted": 0}
    for guest_id in purged:
        try:
            summary["files_deleted"] += delete_all_files(guest_id)
        except Exception as e:
            log.warning("could not delete GCS files for %s: %s", guest_id, e,
                        extra={"event": "purge_files_failed", "guest": guest_id})
        ws = delete_user_workspace(guest_id)
        summary["uploads_deleted"] += ws["uploads_deleted"]
        summary["datastores_deleted"] += int(ws["datastore_deleted"])
        summary["tokens_deleted"] += int(gmail_token_store.delete_token(guest_id))
        summary["tokens_deleted"] += int(slack_token_store.delete_token(guest_id))
    return summary


# ── Protected routes ──────────────────────────────────────────────────────────

@app.get("/")
async def root(username: str = Depends(require_auth)):
    return FileResponse("static/index.html")


@app.get("/api/gmail/status")
async def gmail_status(username: str = Depends(require_auth)):
    """Return whether the current user has a connected Gmail token."""
    from tools.gmail_token_store import has_token
    return {"connected": has_token(username)}


def _gmail_redirect_uri() -> str:
    """Read the redirect URI directly from the credentials file so it always
    matches exactly what is registered in Google Cloud Console."""
    import json as _json
    with open(GMAIL_CREDENTIALS_PATH) as f:
        data = _json.load(f)
    client = data.get("web") or data.get("installed") or {}
    uris = client.get("redirect_uris", [])
    if not uris:
        raise RuntimeError("No redirect_uris found in credentials file.")
    return uris[0]


@app.get("/oauth/gmail/start")
async def gmail_oauth_start(request: Request, username: str = Depends(require_auth)):
    """Redirect user to Google's consent screen to authorise Gmail access."""
    from google_auth_oauthlib.flow import Flow
    flow = Flow.from_client_secrets_file(
        GMAIL_CREDENTIALS_PATH, scopes=GMAIL_SCOPES,
        redirect_uri=_gmail_redirect_uri(),
    )
    auth_url, state = flow.authorization_url(
        access_type="offline", prompt="consent", include_granted_scopes="true"
    )
    cookie_secure = os.environ.get("COOKIE_SECURE", "true").lower() == "true"
    response = RedirectResponse(auth_url, status_code=302)
    response.set_cookie("oauth_state", state, httponly=True, secure=cookie_secure, max_age=600)
    # Newer google-auth-oauthlib adds PKCE automatically; persist the verifier
    # so the callback (which creates a fresh Flow) can complete the exchange.
    code_verifier = getattr(flow, "code_verifier", None)
    if code_verifier:
        response.set_cookie("oauth_cv", code_verifier, httponly=True, secure=cookie_secure, max_age=600)
    return response


@app.get("/oauth/gmail/callback")
async def gmail_oauth_callback(
    request: Request,
    code: str = None,
    state: str = None,
    username: str = Depends(require_auth),
):
    """Exchange auth code for token and store it in GCS for this user."""
    from google_auth_oauthlib.flow import Flow
    from tools.gmail_token_store import save_token

    stored_state = request.cookies.get("oauth_state")
    if not stored_state or stored_state != state:
        raise HTTPException(status_code=400, detail="Invalid OAuth state — please try again.")

    flow = Flow.from_client_secrets_file(
        GMAIL_CREDENTIALS_PATH, scopes=GMAIL_SCOPES,
        redirect_uri=_gmail_redirect_uri(), state=state,
    )
    # Pass PKCE code_verifier if it was stored during /start
    code_verifier = request.cookies.get("oauth_cv")
    fetch_kwargs = {"code": code}
    if code_verifier:
        fetch_kwargs["code_verifier"] = code_verifier
    flow.fetch_token(**fetch_kwargs)

    import json as _json
    save_token(username, _json.loads(flow.credentials.to_json()))

    response = RedirectResponse("/?gmail=connected", status_code=302)
    response.delete_cookie("oauth_state")
    response.delete_cookie("oauth_cv")
    return response


# ── Slack OAuth (per-user "Add to Slack") ────────────────────────────────────

@app.get("/api/slack/status")
async def slack_status(username: str = Depends(require_auth)):
    """Return whether the current user has connected their own Slack workspace."""
    from tools.slack_token_store import load_token
    token = load_token(username)
    return {
        "connected": bool(token),
        "team_name": token.get("team_name") if token else None,
        "configured": bool(SLACK_CLIENT_ID and SLACK_CLIENT_SECRET and SLACK_REDIRECT_URI),
    }


@app.get("/oauth/slack/start")
async def slack_oauth_start(username: str = Depends(require_auth)):
    """Redirect user to Slack's consent screen to install the app in their workspace."""
    if not (SLACK_CLIENT_ID and SLACK_CLIENT_SECRET and SLACK_REDIRECT_URI):
        raise HTTPException(status_code=503, detail="Slack OAuth is not configured on this server.")
    import secrets
    from urllib.parse import urlencode
    state = secrets.token_urlsafe(24)
    params = urlencode({
        "client_id": SLACK_CLIENT_ID,
        "scope": SLACK_OAUTH_SCOPES,
        "redirect_uri": SLACK_REDIRECT_URI,
        "state": state,
    })
    cookie_secure = os.environ.get("COOKIE_SECURE", "true").lower() == "true"
    response = RedirectResponse(f"https://slack.com/oauth/v2/authorize?{params}", status_code=302)
    response.set_cookie("slack_oauth_state", state, httponly=True, secure=cookie_secure, max_age=600)
    return response


@app.get("/oauth/slack/callback")
async def slack_oauth_callback(
    request: Request,
    code: str = None,
    state: str = None,
    error: str = None,
    username: str = Depends(require_auth),
):
    """Exchange the code for a workspace bot token and store it for this user."""
    if error:
        return RedirectResponse("/?slack=denied", status_code=302)

    stored_state = request.cookies.get("slack_oauth_state")
    if not stored_state or not code or stored_state != state:
        raise HTTPException(status_code=400, detail="Invalid OAuth state — please try again.")

    from slack_sdk import WebClient
    from slack_sdk.errors import SlackApiError
    from tools.slack_token_store import save_token

    try:
        result = WebClient().oauth_v2_access(
            client_id=SLACK_CLIENT_ID,
            client_secret=SLACK_CLIENT_SECRET,
            code=code,
            redirect_uri=SLACK_REDIRECT_URI,
        )
    except SlackApiError as e:
        raise HTTPException(status_code=400, detail=f"Slack authorisation failed: {e.response.get('error')}")

    save_token(username, {
        "access_token": result["access_token"],
        "team_id": result.get("team", {}).get("id"),
        "team_name": result.get("team", {}).get("name"),
        "bot_user_id": result.get("bot_user_id"),
    })

    response = RedirectResponse("/?slack=connected", status_code=302)
    response.delete_cookie("slack_oauth_state")
    return response


@app.get("/api/files")
async def list_files(username: str = Depends(require_auth)):
    """Return all files belonging to this user from GCS, newest first."""
    try:
        from tools.gcs_output import list_files as _gcs_list
        return _gcs_list(username)
    except Exception:
        # Fallback to this user's local folder during local dev or if GCS is unavailable
        files = []
        user_dir = user_output_dir(username)
        if user_dir.exists():
            for f in sorted(user_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
                if f.is_file() and f.suffix in ICON_MAP:
                    files.append({
                        "filename": f.name,
                        "doc_type": ICON_MAP[f.suffix],
                        "size_kb": round(f.stat().st_size / 1024, 1),
                        "modified": f.stat().st_mtime,
                    })
        return files


@app.delete("/api/files")
async def clear_files(username: str = Depends(require_auth)):
    """Delete all output files for this user."""
    try:
        from tools.gcs_output import delete_all_files as _gcs_delete
        count = _gcs_delete(username)
    except Exception:
        count = 0
        user_dir = user_output_dir(username)
        if user_dir.exists():
            for f in user_dir.iterdir():
                if f.is_file() and f.suffix in ICON_MAP:
                    f.unlink(missing_ok=True)
                    count += 1
    return {"deleted": count}


@app.delete("/api/files/{filename:path}")
async def delete_one_file(filename: str, username: str = Depends(require_auth)):
    """Delete a single output file for this user."""
    safe_name = Path(filename).name
    if not safe_name:
        raise HTTPException(status_code=400, detail="Invalid filename")
    try:
        from tools.gcs_output import delete_file as _gcs_del
        _gcs_del(username, safe_name)
    except Exception:
        user_dir = user_output_dir(username).resolve()
        local = (user_dir / safe_name).resolve()
        if str(local).startswith(str(user_dir)) and local.exists():
            local.unlink(missing_ok=True)
    return {"deleted": safe_name}



# ── Project Document RAG endpoints ───────────────────────────────────────────

@app.get("/api/project-docs")
async def list_project_docs(username: str = Depends(require_auth)):
    """Return the user's uploaded project documents."""
    from tools.user_datastore import list_user_documents
    return list_user_documents(username)


@app.post("/api/project-docs")
async def upload_project_doc(
    file: UploadFile = File(...),
    username: str = Depends(require_auth),
):
    """Upload a project document, store in GCS, and import into user's datastore."""
    from tools.user_datastore import upload_and_import, ALLOWED_EXTENSIONS, MAX_FILE_SIZE_MB
    from pathlib import Path as _P

    suffix = _P(file.filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{suffix}'. Allowed: {', '.join(ALLOWED_EXTENSIONS)}",
        )

    content = await file.read()
    if len(content) > MAX_FILE_SIZE_MB * 1024 * 1024:
        raise HTTPException(status_code=400, detail=f"File exceeds {MAX_FILE_SIZE_MB} MB limit.")

    try:
        result = upload_and_import(username, content, file.filename)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")


@app.delete("/api/project-docs/{filename:path}")
async def delete_project_doc(filename: str, username: str = Depends(require_auth)):
    """Delete a user's project document from GCS and the Vertex datastore."""
    from tools.user_datastore import delete_user_document
    safe = Path(filename).name
    if not safe:
        raise HTTPException(status_code=400, detail="Invalid filename")
    deleted = delete_user_document(username, safe)
    return {"deleted": safe, "found": deleted}


@app.get("/api/project-docs/status")
async def project_docs_status(username: str = Depends(require_auth)):
    """Check whether the user's Vertex AI datastore is ready."""
    from tools.user_datastore import datastore_ready, datastore_id
    ready = datastore_ready(username)
    return {"ready": ready, "datastore_id": datastore_id(username)}


@app.get("/download/{filename:path}")
async def download_file(filename: str, username: str = Depends(require_auth)):
    """Serve a file from GCS (user-scoped) as a download."""
    safe_name = Path(filename).name
    if not safe_name or safe_name != filename.split("/")[-1]:
        raise HTTPException(status_code=400, detail="Invalid filename")

    # Try GCS first (production path)
    try:
        from tools.gcs_output import download_bytes, file_exists
        if file_exists(username, safe_name):
            data = download_bytes(username, safe_name)
            return StreamingResponse(
                iter([data]),
                media_type="application/octet-stream",
                headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
            )
    except Exception:
        pass

    # Fallback: local filesystem (local dev), scoped to this user's folder
    user_dir = user_output_dir(username).resolve()
    filepath = (user_dir / safe_name).resolve()
    if not str(filepath).startswith(str(user_dir)):
        raise HTTPException(status_code=400, detail="Invalid filename")
    if not filepath.exists() or not filepath.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(
        path=str(filepath),
        filename=safe_name,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
    )


# ── WebSocket ─────────────────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    # Authenticate before accepting
    username = get_user_from_ws_headers(websocket.headers)
    if not username:
        await websocket.close(code=1008)  # Policy Violation
        return

    await websocket.accept()

    loop = asyncio.get_event_loop()
    event_queue = queue.Queue()

    guest = is_guest(username)
    bind_log_context(user=username)
    log.info("websocket connected", extra={"event": "ws_connected", "is_guest": guest})
    orchestrator = EventOrchestrator(
        event_callback=lambda e: event_queue.put(e), username=username, is_guest=guest,
    )

    QUOTA_MESSAGES = {
        "session_cap": (
            f"You've used all {TRIAL_MAX_GENERATIONS} generations in this trial session. "
            "Your documents are still available to download from the sidebar."
        ),
        "daily_cap": (
            "Today's trial capacity is used up. Your email is on the list — "
            "we'll open it up for you as soon as capacity frees."
        ),
        "expired": "This trial session has expired. Start a new one from the trial page.",
    }

    async def drain_events():
        while True:
            try:
                event = await loop.run_in_executor(
                    None, event_queue.get, True, 0.15
                )
                await websocket.send_text(json.dumps(event))
                if event.get("type") in ("done", "error"):
                    break
            except queue.Empty:
                continue
            except Exception:
                break

    async def heartbeat(stop_event: asyncio.Event):
        """Send a ping every 20s to prevent Cloud Run idle timeout."""
        while not stop_event.is_set():
            await asyncio.sleep(20)
            if stop_event.is_set():
                break
            try:
                await websocket.send_text(json.dumps({"type": "heartbeat"}))
            except Exception:
                break

    try:
        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)
            user_input = msg.get("message", "").strip()
            if not user_input:
                continue

            while not event_queue.empty():
                try:
                    event_queue.get_nowait()
                except queue.Empty:
                    break

            if guest:
                from tools.trial_store import check_and_increment
                try:
                    verdict = await loop.run_in_executor(None, check_and_increment, username)
                except Exception as e:
                    # Fail closed: a broken quota store must not become an open endpoint.
                    log.exception("quota check failed — denying to fail closed",
                                  extra={"event": "quota_check_error", "guest": username})
                    verdict = {"ok": False, "reason": "daily_cap"}
                if not verdict["ok"]:
                    log.info("trial quota exhausted",
                             extra={"event": "quota_blocked", "guest": username,
                                    "reason": verdict["reason"]})
                    await websocket.send_text(json.dumps({
                        "type": "quota_exceeded",
                        "reason": verdict["reason"],
                        "message": QUOTA_MESSAGES.get(verdict["reason"], QUOTA_MESSAGES["session_cap"]),
                    }))
                    await websocket.send_text(json.dumps({"type": "done"}))
                    continue

            threading.Thread(
                target=orchestrator.run,
                args=(user_input,),
                daemon=True,
            ).start()

            stop_heartbeat = asyncio.Event()
            hb_task = asyncio.create_task(heartbeat(stop_heartbeat))
            try:
                await drain_events()
            finally:
                stop_heartbeat.set()
                hb_task.cancel()
                try:
                    await hb_task
                except asyncio.CancelledError:
                    pass

    except WebSocketDisconnect:
        log.info("websocket disconnected", extra={"event": "ws_disconnected"})
    except Exception as e:
        log.exception("websocket handler failed", extra={"event": "ws_error"})
        try:
            await websocket.send_text(json.dumps({"type": "error", "message": str(e)}))
        except Exception:
            log.debug("could not deliver error to a closed socket")


# ── Static files (last — so routes above take priority) ──────────────────────
app.mount("/static", StaticFiles(directory="static"), name="static")


if __name__ == "__main__":
    log.info("starting web interface at http://localhost:%s", PORT,
             extra={"event": "server_start", "port": PORT})
    uvicorn.run(app, host="0.0.0.0", port=PORT, reload=False)
