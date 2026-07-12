"""
Project Management Agent — Web Application

FastAPI server with authentication, WebSocket streaming, and file downloads.

Run with:  python web_app.py
Then open: http://localhost:8000
"""

import sys
import os
import json
import queue
import threading
import asyncio
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request, Depends, Form, UploadFile, File
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
import uvicorn

from config import OUTPUT_DIR, PORT
from auth import require_auth, get_user_from_ws_headers, verify_login, make_session_cookie, clear_session_cookie
from event_orchestrator import EventOrchestrator

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


@app.get("/api/files")
async def list_files(username: str = Depends(require_auth)):
    """Return all files belonging to this user from GCS, newest first."""
    try:
        from tools.gcs_output import list_files as _gcs_list
        return _gcs_list(username)
    except Exception:
        # Fallback to local OUTPUT_DIR during local dev or if GCS is unavailable
        files = []
        if OUTPUT_DIR.exists():
            for f in sorted(OUTPUT_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
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
        if OUTPUT_DIR.exists():
            for f in OUTPUT_DIR.iterdir():
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
        local = (OUTPUT_DIR / safe_name).resolve()
        if str(local).startswith(str(OUTPUT_DIR.resolve())) and local.exists():
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

    # Fallback: local filesystem (local dev)
    filepath = (OUTPUT_DIR / safe_name).resolve()
    if not str(filepath).startswith(str(OUTPUT_DIR.resolve())):
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

    orchestrator = EventOrchestrator(event_callback=lambda e: event_queue.put(e), username=username)

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
        pass
    except Exception as e:
        try:
            await websocket.send_text(json.dumps({"type": "error", "message": str(e)}))
        except Exception:
            pass


# ── Static files (last — so routes above take priority) ──────────────────────
app.mount("/static", StaticFiles(directory="static"), name="static")


if __name__ == "__main__":
    print("\n" + "=" * 55)
    print("  Project Management Agent — Web Interface")
    print(f"  Open your browser at: http://localhost:{PORT}")
    print("=" * 55 + "\n")
    uvicorn.run(app, host="0.0.0.0", port=PORT, reload=False)
